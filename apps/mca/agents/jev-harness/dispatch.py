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

# The price of each verb, in milliseconds. This is the dispatcher's own
# config, and state.py ships it to Jev as `tick.verb_duration_ms`, so what the
# model is told an action costs cannot drift from what it actually costs.
VERB_DURATION_MS = {
    "advance": 300,
    "retreat": 300,
    "turn_toward": 0,
    "jump": 150,
    "mine_front": 1000,
    "place_block": 0,
    "attack": 0,
    "use_item": 0,
    "hold": 150,
    "done": 150,
}

# One attackBlock tick does not break a block; mine_front keeps swinging at
# the same position until its bound runs out.
MINE_POLL_S = 0.05

RAYCAST_MAX = 5.0


class VerbError(Exception):
    """A verb could not be carried out. Reported as data, never raised outward."""


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

    def _resolve_yaw(self, target: dict | None) -> float | None:
        """The absolute yaw that faces `target`, or None if it names no bearing."""
        if not target:
            return None
        if target.get("yaw") is not None:
            return float(target["yaw"])
        if target.get("x") is not None and target.get("z") is not None:
            state = self._player_state()
            pos = state.get("pos") or {}
            dx = float(target["x"]) - float(pos.get("x", 0.0))
            dz = float(target["z"]) - float(pos.get("z", 0.0))
            if dx == 0.0 and dz == 0.0:
                return None                # standing on it; no bearing exists
            # A bearing relative to yaw 0 is the absolute yaw.
            return relative_bearing(0.0, dx, dz)
        if target.get("rel_yaw") is not None:
            state = self._player_state()
            yaw = float((state.get("rot") or {}).get("yaw", 0.0))
            return normalize_deg(yaw + float(target["rel_yaw"]))
        return None

    def _face(self, target: dict | None) -> bool:
        yaw = self._resolve_yaw(target)
        if yaw is None:
            return False
        params = {"yaw": yaw}
        if target and target.get("pitch") is not None:
            params["pitch"] = float(target["pitch"])
        self.bridge.rpc("player.set_rotation", params)
        return True

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
        """Face the target if one was given, then walk forward for the bound."""
        self._face(target)
        self._press_for("forward", VERB_DURATION_MS["advance"])

    def _retreat(self, target, deadline):
        """Straight back, without turning: the point is to keep facing the threat."""
        self._press_for("back", VERB_DURATION_MS["retreat"])

    def _turn_toward(self, target, deadline):
        if not self._face(target):
            raise VerbError("turn_toward needs a bearing: target has no "
                            "yaw, rel_yaw or x/z")

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
            raise VerbError("attack needs a target entity id")
        try:
            entity_id = int(raw)
        except (TypeError, ValueError):
            raise VerbError(f"attack needs a numeric entity id, got {raw!r}") from None
        self.bridge.eval(f"return api:attackEntity({entity_id})")

    def _use_item(self, target, deadline):
        self.bridge.eval("return api:useItem()")

    def _hold(self, target, deadline):
        self._sleep(VERB_DURATION_MS["hold"] / 1000.0)

    def _done(self, target, deadline):
        """Sleep only. Clearing the standing order belongs to the loop that owns it."""
        self._sleep(VERB_DURATION_MS["done"] / 1000.0)


Dispatcher.VERBS = {
    "advance": Dispatcher._advance,
    "retreat": Dispatcher._retreat,
    "turn_toward": Dispatcher._turn_toward,
    "jump": Dispatcher._jump,
    "mine_front": Dispatcher._mine_front,
    "place_block": Dispatcher._place_block,
    "attack": Dispatcher._attack,
    "use_item": Dispatcher._use_item,
    "hold": Dispatcher._hold,
    "done": Dispatcher._done,
}
