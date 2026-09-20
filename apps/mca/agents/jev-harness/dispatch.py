"""Bounded execution of the verb Jev chose.

This module is deliberately stupid. It executes exactly the verb it is handed
and reports what happened. It holds no opinion about whether the verb was a
good idea: there is no threshold here, no precedence, no "but there's lava",
no substitution of a safer action. Danger is surfaced to the model in state,
never acted on behind its back. If you find yourself adding a condition that
changes which verb runs, it belongs in a question, not here.

What it *is* responsible for is the clock and the keys.

`player.press_key` is a latch, not a pulse: `{"key": "forward", "action":
"press"}` holds the key down until something releases it, and a latch that
outlives the process leaves the player walking into the dark forever. That is
the worst failure in the design, so it is made structurally impossible rather
than carefully avoided:

  * every `execute()` opens by releasing every latchable key, whatever the
    verb, so a key leaked by a crash, a timeout or an earlier process is gone
    before the next action starts;
  * every press happens inside a context manager whose `finally` releases it;
  * `execute()` releases everything again in its own `finally`;
  * `atexit`, SIGINT and SIGTERM release everything for every live dispatcher.

Four independent paths, any one of which is enough.
"""
import atexit
import contextlib
import json
import math
import signal
import time
import weakref

from geometry import normalize_deg, relative_bearing

# Every key `player.press_key` can latch. The dispatcher only ever presses
# forward, back and jump, but it releases all of them: a key left down by
# anything else is exactly the failure this list exists to end.
LATCHING_KEYS = ("forward", "back", "left", "right", "jump",
                 "sneak", "sprint", "attack", "use")

# Container operations fired back-to-back are silently dropped: the screen
# and the server's menu sync need a beat between clicks. Every container
# click therefore pays this before the next verb can start.
CONTAINER_PACE_MS = 300

# The price of each verb, in milliseconds. This is the dispatcher's own
# config, and state.py ships it to Jev as `tick.verb_duration_ms`, so what the
# model is told an action costs cannot drift from what it actually costs.
VERB_DURATION_MS = {
    "advance": 300,
    "retreat": 300,
    "turn_left": 0,
    "turn_right": 0,
    "strafe_left": 300,
    "strafe_right": 300,
    "hotbar_next": 0,
    "hotbar_prev": 0,
    "look_up_slight": 0,
    "look_down_slight": 0,
    "look_up": 0,
    "look_down": 0,
    "look_left": 0,
    "look_right": 0,
    "look_up_left": 0,
    "look_up_right": 0,
    "look_down_left": 0,
    "look_down_right": 0,
    "look_up_smooth": 0,
    "look_down_smooth": 0,
    "look_left_smooth": 0,
    "look_right_smooth": 0,
    "look_center": 0,
    "cycle_item_left": 0,
    "cycle_item_right": 0,
    "inventory": 0,
    "drop_item": 0,
    "walk_forward": 300,
    "walk_backward": 300,
    "sneak": 300,
    "sprint": 400,
    "open_inventory": 0,
    **{f"slot_{n}": 0 for n in range(1, 10)},
    "jump": 150,
    "mine_front": 1000,
    "place_block": 0,
    "attack": 0,
    # A bare use_item is one instantaneous interaction. A `hold_ms` target
    # adds exactly the number the model itself supplied, so the figure the
    # model is shipped still describes everything the dispatcher spends on
    # its own account.
    "use_item": 0,
    # Vanilla food needs ~1.6s of held right-click; a single click eats nothing.
    # This exists as its own verb because the model chooses from enumerated
    # options and cannot supply a hold_ms of its own.
    "use_item_hold": 1700,
    "aim_higher": 0,
    "aim_lower": 0,
    "hold": 150,
    "done": 150,
    "equip": 0,
    # craft.item's own client-thread sleeps: 300ms to open a table, then
    # 250ms to place the recipe and 250ms to shift-click the result.
    "craft": 1000,
    # The GUI opens asynchronously after the server replies; this is how long
    # `open` will keep asking before calling it a failure.
    "open": 1000,
    "move_stack": CONTAINER_PACE_MS,
    "close": 0,
}

# One attackBlock tick does not break a block; mine_front keeps swinging at
# the same position until its bound runs out.
MINE_POLL_S = 0.05

# How often `open` asks whether the container screen has appeared yet.
CONTAINER_POLL_S = 0.1

# The hotbar is the first nine slots of the inventory `api:inventoryJson()`
# reports, and the only ones `player.set_hotbar_slot` can select.
HOTBAR_SIZE = 9

RAYCAST_MAX = 5.0


class VerbError(Exception):
    """A verb could not be carried out. Reported as data, never raised outward."""


def _as_int(value, name: str) -> int:
    """A target field as a whole number, or a reportable failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise VerbError(f"{name} must be a whole number, got {value!r}") from None


def _json_list(raw, what: str) -> list:
    """The mod's JSON-string payloads; an already-parsed list is fine too."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise VerbError(f"{what} was not JSON: {raw!r:.80}") from None
    if not isinstance(raw, list):
        raise VerbError(f"{what} was not a list: {type(raw).__name__}")
    return raw


def _same_item(stack_id, wanted: str) -> bool:
    """Match an item id, forgiving the vanilla namespace.

    state.py strips `minecraft:` before the model ever sees an id — `held`
    arrives as `snowball` — so an answer of `snowball` has to find
    `minecraft:snowball`. This is spelling, not a judgement about the world.
    """
    if not isinstance(stack_id, str):
        return False
    if stack_id == wanted:
        return True
    return ":" not in wanted and stack_id == "minecraft:" + wanted


# --------------------------------------------------------------------------
# the safety net
# --------------------------------------------------------------------------

_LIVE = weakref.WeakSet()
_PREV_HANDLERS = {}
_SAFETY_INSTALLED = False


def _release_everything() -> None:
    """Release every latchable key on every dispatcher still alive."""
    for dispatcher in list(_LIVE):
        try:
            dispatcher.release_all()
        except Exception:
            pass


def _on_signal(signum, frame):
    _release_everything()
    prev = _PREV_HANDLERS.get(signum)
    if callable(prev):
        prev(signum, frame)          # e.g. default_int_handler -> KeyboardInterrupt
        return
    # SIG_DFL / SIG_IGN: put the original disposition back and re-raise, so
    # the process dies exactly as it would have without us in the way.
    try:
        signal.signal(signum, prev if prev is not None else signal.SIG_DFL)
        signal.raise_signal(signum)
    except Exception:
        pass


def _install_safety_net() -> None:
    global _SAFETY_INSTALLED
    if _SAFETY_INSTALLED:
        return
    _SAFETY_INSTALLED = True
    atexit.register(_release_everything)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            _PREV_HANDLERS[sig] = signal.getsignal(sig)
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # Not the main thread, or no such signal. The atexit path stands.
            _PREV_HANDLERS.pop(sig, None)


# --------------------------------------------------------------------------
# the dispatcher
# --------------------------------------------------------------------------

# The player's eyes sit this far above their feet, and a target is aimed at
# this far above its own feet — the middle of a mob rather than the ground it
# stands on.
EYE_HEIGHT = 1.62
AIM_HEIGHT = 1.0
# How far one aim_higher / aim_lower moves the head.
AIM_STEP_DEG = 10.0
# How far one turn_left / turn_right swings the view.
TURN_STEP_DEG = 15.0
# Full Keyboard Gameplay's own camera steps: the - and + keys move 15 degrees,
# the numpad keys 45, and the arrow keys move smoothly.
LOOK_SLIGHT_DEG = 15.0
LOOK_STEP_DEG = 45.0
LOOK_SMOOTH_DEG = 10.0
# How long a bare left-click holds the attack key.
ATTACK_CLICK_MS = 120.0


class Dispatcher:
    """Runs one verb, bounded, and says what happened.

        Dispatcher(bridge).execute("advance", {"x": 118, "y": 64, "z": -44})
        -> {"verb": "advance", "ok": True, "duration_ms": 301, "error": None}

    `target` is whatever Jev picked, already described by state.py. A bearing
    is taken from `yaw` if present, else computed from `x`/`z` against a fresh
    `player.state`, else from `rel_yaw` added to the current yaw. `attack`
    uses `id`. Resolving a coordinate into a bearing is arithmetic over a
    choice already made, not a choice.

    Outcome measurement (moved_m, health_delta) is not done here.
    """

    VERBS = {}          # filled in below, after the methods exist

    def __init__(self, bridge, sleep=time.sleep, clock=time.monotonic):
        self.bridge = bridge
        self._sleep = sleep
        self._clock = clock
        self.last_release_error = None
        _LIVE.add(self)
        _install_safety_net()

    # -- keys --------------------------------------------------------------

    def release_all(self) -> None:
        """Unlatch every key. Never raises: this runs on the way out of a crash."""
        for key in LATCHING_KEYS:
            try:
                self.bridge.rpc("player.press_key", {"key": key, "action": "release"})
            except Exception as exc:
                self.last_release_error = f"{type(exc).__name__}: {exc}"

    @contextlib.contextmanager
    def _held(self, key: str):
        self.bridge.rpc("player.press_key", {"key": key, "action": "press"})
        try:
            yield
        finally:
            try:
                self.bridge.rpc("player.press_key", {"key": key, "action": "release"})
            except Exception as exc:
                self.last_release_error = f"{type(exc).__name__}: {exc}"

    def _press_for(self, key: str, ms: int) -> None:
        with self._held(key):
            self._sleep(ms / 1000.0)

    # -- entry point -------------------------------------------------------

    def execute(self, verb: str, target: dict | None = None) -> dict:
        started = self._clock()
        ok, error = False, None
        try:
            # Unconditional, before anything else, for every verb including
            # the ones that never press a key.
            self.release_all()
            handler = self.VERBS.get(verb)
            if handler is None:
                raise VerbError(f"unknown verb: {verb!r}")
            deadline = started + VERB_DURATION_MS[verb] / 1000.0
            handler(self, target, deadline)
            ok = True
        except Exception as exc:                          # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self.release_all()
        return {"verb": verb, "ok": ok,
                "duration_ms": int(round((self._clock() - started) * 1000)),
                "error": error}

    # -- resolving a target into numbers -----------------------------------

    def _player_state(self) -> dict:
        state = self.bridge.rpc("player.state") or {}
        if not state.get("inWorld"):
            raise VerbError("player.state says not in world")
        return state

    def _raycast(self) -> dict:
        hit = self.bridge.rpc("world.raycast", {"max": RAYCAST_MAX}) or {}
        if hit.get("type") != "BLOCK":
            raise VerbError(f"raycast hit no block (type={hit.get('type')})")
        return hit

    @staticmethod
    def _block_pos(target: dict) -> dict:
        return {"x": math.floor(float(target["x"])),
                "y": math.floor(float(target["y"])),
                "z": math.floor(float(target["z"]))}

    # -- the verbs ---------------------------------------------------------

    def _advance(self, target, deadline):
        """Walk forward for the bound. Steering is turn_left / turn_right."""
        self._press_for("forward", VERB_DURATION_MS["advance"])

    def _retreat(self, target, deadline):
        """Straight back, without turning: the point is to keep facing the threat."""
        self._press_for("back", VERB_DURATION_MS["retreat"])

    def _aim(self, delta_deg):
        """Tilt the head by `delta_deg`, keeping the current yaw.

        Negative pitch is upward. Thrown items fall on the way to a target, so
        hitting something at range means aiming above it; how far above is not
        something this code knows.
        """
        state = self._player_state()
        rot = state.get("rot") or {}
        yaw = float(rot.get("yaw", 0.0))
        pitch = float(rot.get("pitch", 0.0)) + delta_deg
        pitch = max(-90.0, min(90.0, pitch))
        self.bridge.rpc("player.set_rotation", {"yaw": yaw, "pitch": round(pitch, 1)})

    def _aim_higher(self, target, deadline):
        self._aim(-AIM_STEP_DEG)

    def _aim_lower(self, target, deadline):
        self._aim(AIM_STEP_DEG)

    def _turn(self, delta_deg):
        """Swing the view left or right, keeping the current pitch.

        This is the mouse. Snapping the head onto a chosen entity would be the
        aiming solved here rather than by whoever is playing.
        """
        rot = self._player_state().get("rot") or {}
        yaw = normalize_deg(float(rot.get("yaw", 0.0)) + delta_deg)
        self.bridge.rpc("player.set_rotation",
                        {"yaw": round(yaw, 1), "pitch": round(float(rot.get("pitch", 0.0)), 1)})

    def _turn_left(self, target, deadline):
        self._turn(-TURN_STEP_DEG)

    def _turn_right(self, target, deadline):
        self._turn(TURN_STEP_DEG)

    def _strafe_left(self, target, deadline):
        self._press_for("left", VERB_DURATION_MS["strafe_left"])

    def _strafe_right(self, target, deadline):
        self._press_for("right", VERB_DURATION_MS["strafe_right"])

    def _look(self, d_yaw, d_pitch, center=False):
        """Move the camera, as one of the look keys does."""
        rot = self._player_state().get("rot") or {}
        yaw = normalize_deg(float(rot.get("yaw", 0.0)) + d_yaw)
        pitch = 0.0 if center else float(rot.get("pitch", 0.0)) + d_pitch
        pitch = max(-90.0, min(90.0, pitch))
        self.bridge.rpc("player.set_rotation",
                        {"yaw": round(yaw, 1), "pitch": round(pitch, 1)})

    def _walk_forward(self, target, deadline):
        self._press_for("forward", VERB_DURATION_MS["walk_forward"])

    def _walk_backward(self, target, deadline):
        self._press_for("back", VERB_DURATION_MS["walk_backward"])

    def _drop_item(self, target, deadline):
        raise VerbError("drop_item has no key on the bridge yet")

    def _sneak(self, target, deadline):
        self._press_for("sneak", VERB_DURATION_MS["sneak"])

    def _sprint(self, target, deadline):
        """Ctrl plus forward: sprinting alone does nothing standing still."""
        self.bridge.rpc("player.press_key", {"key": "sprint", "action": "press"})
        try:
            self._press_for("forward", VERB_DURATION_MS["sprint"])
        finally:
            self.bridge.rpc("player.press_key", {"key": "sprint", "action": "release"})

    def _open_inventory(self, target, deadline):
        self.bridge.rpc("container.open_inventory")

    def _select_slot(self, index):
        self.bridge.rpc("player.set_hotbar_slot", {"slot": index})

    def _selected_slot(self) -> int:
        """The hotbar slot in hand.

        player.state does not report it, so ask the script api, which does.
        Defaulting to 0 made every cycle_item_left land on slot 8.
        """
        try:
            return int(self.bridge.eval("return api:hotbarSlot()") or 0)
        except (TypeError, ValueError):
            return 0

    def _hotbar_next(self, target, deadline):
        self.bridge.rpc("player.set_hotbar_slot",
                        {"slot": (self._selected_slot() + 1) % HOTBAR_SIZE})

    def _hotbar_prev(self, target, deadline):
        self.bridge.rpc("player.set_hotbar_slot",
                        {"slot": (self._selected_slot() - 1) % HOTBAR_SIZE})

    def _jump(self, target, deadline):
        self._press_for("jump", VERB_DURATION_MS["jump"])

    def _mine_front(self, target, deadline):
        """Swing at the block the player is looking at until the bound expires."""
        pos = self._raycast()
        at = {"x": pos["x"], "y": pos["y"], "z": pos["z"]}
        while True:
            self.bridge.rpc("world.mine_block", at)
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(MINE_POLL_S, remaining))

    def _place_block(self, target, deadline):
        """Place against the chosen position, or against whatever is in front."""
        if target and target.get("x") is not None and target.get("z") is not None \
                and target.get("y") is not None:
            at = self._block_pos(target)
        else:
            hit = self._raycast()
            at = {"x": hit["x"], "y": hit["y"], "z": hit["z"]}
        self.bridge.rpc("world.place_block", at)

    def _attack(self, target, deadline):
        raw = (target or {}).get("id")
        if raw is None:
            # The mouse button, with nothing named: swing at whatever is ahead.
            self._press_for("attack", ATTACK_CLICK_MS)
            return
        try:
            entity_id = int(raw)
        except (TypeError, ValueError):
            raise VerbError(f"attack needs a numeric entity id, got {raw!r}") from None
        self.bridge.eval(f"return api:attackEntity({entity_id})")

    def _use_item_hold(self, target, deadline):
        """Right-click held down: eating, drinking, drawing a bow, raising a shield.

        The hold defaults to the table's own figure so `tick.verb_duration_ms`
        stays true; a target may still override it.
        """
        hold = dict(target or {})
        hold.setdefault("hold_ms", VERB_DURATION_MS["use_item_hold"])
        return self._use_item(hold, deadline)

    def _use_item(self, target, deadline):
        """One interaction, or the right-click held down for `hold_ms`.

        Eating is about 1.6 seconds of held right-click and a single click
        achieves nothing, so the held form exists. It goes through the same
        `_held` context manager as every other key, which means a crash
        inside the sleep cannot leave `use` latched: the context manager
        releases it, and `execute()`'s finally releases it again.
        """
        raw = (target or {}).get("hold_ms")
        if raw is None:
            self.bridge.eval("return api:useItem()")
            return
        try:
            hold_ms = float(raw)
        except (TypeError, ValueError):
            raise VerbError(f"use_item hold_ms must be milliseconds, "
                            f"got {raw!r}") from None
        if hold_ms < 0:
            raise VerbError(f"use_item hold_ms must not be negative, got {hold_ms:g}")
        if hold_ms == 0:
            self.bridge.eval("return api:useItem()")
            return
        self._press_for("use", hold_ms)

    # -- inventory, crafting and containers ---------------------------------

    def _hotbar_slot_of(self, item: str) -> int:
        """The hotbar slot holding `item`, or a reportable failure.

        Nothing is substituted when the item is elsewhere or absent. A slot
        the model can select is either there or it is not, and "not" is an
        answer the model gets to see rather than a fallback taken for it.
        """
        stacks = _json_list(self.bridge.eval("return api:inventoryJson()"), "inventory")
        matches = sorted(
            (s for s in stacks
             if isinstance(s, dict) and _same_item(s.get("id"), item)),
            key=lambda s: s.get("slot", HOTBAR_SIZE))
        for stack in matches:
            slot = stack.get("slot")
            if isinstance(slot, int) and 0 <= slot < HOTBAR_SIZE:
                return slot
        if matches:
            elsewhere = ", ".join(str(s.get("slot")) for s in matches)
            raise VerbError(f"{item} is in the inventory (slot {elsewhere}) "
                            f"but not the hotbar")
        raise VerbError(f"{item} is not in the inventory")

    def _equip(self, target, deadline):
        """Select a hotbar slot, by number or by what is sitting in it."""
        target = target or {}
        if target.get("slot") is not None:
            slot = _as_int(target["slot"], "equip slot")
            if not 0 <= slot < HOTBAR_SIZE:
                raise VerbError(f"equip slot must be 0-{HOTBAR_SIZE - 1}, got {slot}")
        elif target.get("item") is not None:
            slot = self._hotbar_slot_of(str(target["item"]))
        else:
            raise VerbError(f"equip needs a slot (0-{HOTBAR_SIZE - 1}) or an item id")
        self.bridge.rpc("player.set_hotbar_slot", {"slot": slot})

    def _craft(self, target, deadline):
        """Craft by result item id.

        The recipe comes from the player's own recipe book on the Java side,
        so no recipe is named here. `use_max` fills the grid as full as the
        inventory allows; the table coordinates are passed through only when
        all three are present, because `craft.item` needs the whole position
        to open a table and ignores a partial one.
        """
        target = target or {}
        item = target.get("item")
        if isinstance(item, str):
            item = item.strip()
        if not item:
            raise VerbError("craft needs an item id")
        params = {"item": str(item)}
        if target.get("use_max") is not None:
            params["use_max"] = bool(target["use_max"])
        if all(target.get(k) is not None for k in ("x", "y", "z")):
            params.update(self._block_pos(target))
        self.bridge.rpc("craft.item", params)

    def _open(self, target, deadline):
        """Ask for a container, then wait for the screen to actually exist.

        The screen opens asynchronously after the server replies, and a
        container op fired before it does is silently dropped. So the verb is
        not finished until `container.state` says open.
        """
        target = target or {}
        if any(target.get(k) is None for k in ("x", "y", "z")):
            raise VerbError("open needs x, y and z")
        at = self._block_pos(target)
        self.bridge.rpc("container.open", at)
        while True:
            if (self.bridge.rpc("container.state") or {}).get("open"):
                return
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(CONTAINER_POLL_S, remaining))
        raise VerbError(f"container at ({at['x']},{at['y']},{at['z']}) did not "
                        f"report open within {VERB_DURATION_MS['open']}ms")

    def _move_stack(self, target, deadline):
        """Shift-click one slot: a whole stack across the open container.

        Loading a furnace, taking its output and withdrawing from a chest are
        all this one call with a different slot number. Which slot is the
        model's business; the pacing afterwards is the dispatcher's.
        """
        target = target or {}
        if target.get("slot") is None:
            raise VerbError("move_stack needs a slot")
        slot = _as_int(target["slot"], "move_stack slot")
        button = _as_int(target.get("button", 0), "move_stack button")
        self.bridge.rpc("container.click",
                        {"slot": slot, "button": button, "mode": "QUICK_MOVE"})
        self._sleep(CONTAINER_PACE_MS / 1000.0)

    def _close(self, target, deadline):
        self.bridge.rpc("container.close")

    def _hold(self, target, deadline):
        self._sleep(VERB_DURATION_MS["hold"] / 1000.0)

    def _done(self, target, deadline):
        """Sleep only. Clearing the standing order belongs to the loop that owns it."""
        self._sleep(VERB_DURATION_MS["done"] / 1000.0)


Dispatcher.VERBS = {
    "advance": Dispatcher._advance,
    "retreat": Dispatcher._retreat,
    "turn_left": Dispatcher._turn_left,
    "turn_right": Dispatcher._turn_right,
    "strafe_left": Dispatcher._strafe_left,
    "strafe_right": Dispatcher._strafe_right,
    "hotbar_next": Dispatcher._hotbar_next,
    "hotbar_prev": Dispatcher._hotbar_prev,
    "look_up_slight": (lambda d: lambda self, target, deadline: self._look(*d))((0.0, -LOOK_SLIGHT_DEG)),
    "look_down_slight": (lambda d: lambda self, target, deadline: self._look(*d))((0.0, LOOK_SLIGHT_DEG)),
    "look_up": (lambda d: lambda self, target, deadline: self._look(*d))((0.0, -LOOK_STEP_DEG)),
    "look_down": (lambda d: lambda self, target, deadline: self._look(*d))((0.0, LOOK_STEP_DEG)),
    "look_left": (lambda d: lambda self, target, deadline: self._look(*d))((-LOOK_STEP_DEG, 0.0)),
    "look_right": (lambda d: lambda self, target, deadline: self._look(*d))((LOOK_STEP_DEG, 0.0)),
    "look_up_left": (lambda d: lambda self, target, deadline: self._look(*d))((-LOOK_STEP_DEG, -LOOK_STEP_DEG)),
    "look_up_right": (lambda d: lambda self, target, deadline: self._look(*d))((LOOK_STEP_DEG, -LOOK_STEP_DEG)),
    "look_down_left": (lambda d: lambda self, target, deadline: self._look(*d))((-LOOK_STEP_DEG, LOOK_STEP_DEG)),
    "look_down_right": (lambda d: lambda self, target, deadline: self._look(*d))((LOOK_STEP_DEG, LOOK_STEP_DEG)),
    "look_up_smooth": (lambda d: lambda self, target, deadline: self._look(*d))((0.0, -LOOK_SMOOTH_DEG)),
    "look_down_smooth": (lambda d: lambda self, target, deadline: self._look(*d))((0.0, LOOK_SMOOTH_DEG)),
    "look_left_smooth": (lambda d: lambda self, target, deadline: self._look(*d))((-LOOK_SMOOTH_DEG, 0.0)),
    "look_right_smooth": (lambda d: lambda self, target, deadline: self._look(*d))((LOOK_SMOOTH_DEG, 0.0)),
    "look_center": lambda self, target, deadline: self._look(0.0, 0.0, center=True),
    "cycle_item_left": Dispatcher._hotbar_prev,
    "cycle_item_right": Dispatcher._hotbar_next,
    "inventory": Dispatcher._open_inventory,
    "drop_item": Dispatcher._drop_item,
    "walk_forward": Dispatcher._walk_forward,
    "walk_backward": Dispatcher._walk_backward,
    "sneak": Dispatcher._sneak,
    "sprint": Dispatcher._sprint,
    "open_inventory": Dispatcher._open_inventory,
    **{f"slot_{n}": (lambda n: lambda self, target, deadline: self._select_slot(n - 1))(n)
       for n in range(1, 10)},
    "jump": Dispatcher._jump,
    "mine_front": Dispatcher._mine_front,
    "place_block": Dispatcher._place_block,
    "attack": Dispatcher._attack,
    "use_item": Dispatcher._use_item,
    "use_item_hold": Dispatcher._use_item_hold,
    "aim_higher": Dispatcher._aim_higher,
    "aim_lower": Dispatcher._aim_lower,
    "hold": Dispatcher._hold,
    "done": Dispatcher._done,
    "equip": Dispatcher._equip,
    "craft": Dispatcher._craft,
    "open": Dispatcher._open,
    "move_stack": Dispatcher._move_stack,
    "close": Dispatcher._close,
}
