"""Deterministic, idempotent actions. Jev drives the transitions between them.

Every action here ends in a state you can name — the container is open, the
item is in hand, the creature has been thrown at — and says whether it got
there. Asking for something already true is a success that changed nothing, so
repeating an action is safe and a loop costs a tick rather than breaking the
run.

This is the opposite of the keyboard verbs, which asked the model to hold a
multi-step intention across independent per-tick judgements it had no way to
remember. Here the steps are the harness's job and the order of them is the
model's.
"""
import json
import math
import time

from ballistics import launch_pitch
from geometry import relative_bearing

EYE_HEIGHT = 1.62
AIM_HEIGHT = 1.0          # aim at a mob's middle, not the ground under it
REACH = 4.0               # container reach, kept under the server's 4.5
MELEE_REACH = 3.0         # the server refuses a swing from further than this
# Where the farm is fought from: on the platform, behind the barrier, with a
# sightline to the piglins. The spots nearer the mobs have no line of sight —
# swinging there hits the barrier.
SAFE_SPOT = {"x": 5731, "y": 231, "z": 439}
MAX_SWINGS = 12
WALK_TICK_MS = 250
MAX_WALK_STEPS = 12
CONTAINER_PACE_S = 0.35   # the server drops container ops fired faster than this


# What counts as a weapon, best first. Melee before ranged: the farm is killed
# with a sword.
WEAPON_KINDS = ("_sword", "_axe", "trident", "crossbow", "bow")


def is_weapon(item_id):
    return any(kind in (item_id or "") for kind in WEAPON_KINDS)


def weapon_rank(item_id):
    for rank, kind in enumerate(WEAPON_KINDS):
        if kind in (item_id or ""):
            return rank
    return len(WEAPON_KINDS)


class ActionError(Exception):
    """The action could not reach its end state, with the reason why."""


def _ok(what, changed=True, **extra):
    return {"ok": True, "did": what, "changed": changed, **extra}


class Actions:
    """One method per end state. Each is safe to repeat."""

    def __init__(self, bridge, sleep=time.sleep):
        self.bridge = bridge
        self._sleep = sleep

    # -- reading the world -------------------------------------------------

    def _self(self):
        return self.bridge.eval(
            "return {x=api:x(),y=api:y(),z=api:z(),yaw=api:yaw(),pitch=api:pitch(),"
            "held=api:heldItem(),slot=api:hotbarSlot(),food=api:food()}")

    def _inventory(self):
        return json.loads(self.bridge.eval("return api:inventoryJson()"))

    def _count(self, item):
        return sum(s.get("count", 0) for s in self._inventory()
                   if s.get("id") == item)

    def _entity(self, entity_id):
        found = json.loads(self.bridge.eval("return api:entitiesJson(48)"))
        for e in found:
            if e.get("id") == entity_id:
                return e
        return None

    def _container(self):
        return self.bridge.rpc("container.state") or {}

    # -- the actions -------------------------------------------------------

    def face(self, target, arc_for_throw=False):
        """End looking at `target`, allowing for a thrown item's drop."""
        me = self._self()
        tx, ty, tz = self._target_point(target)
        dx, dz = tx - me["x"], tz - me["z"]
        horizontal = math.hypot(dx, dz)
        dy = ty - (me["y"] + EYE_HEIGHT)
        if horizontal < 0.1:
            # Directly below or above — a block underfoot is a normal thing to
            # open. Keep the heading and look straight down or up.
            yaw = float(me["yaw"])
            pitch = 90.0 if dy < 0 else -90.0
        else:
            yaw = relative_bearing(0.0, dx, dz)
            pitch = (launch_pitch(horizontal, dy) if arc_for_throw
                     else -math.degrees(math.atan2(dy, horizontal)))
        self.bridge.rpc("player.set_rotation",
                        {"yaw": round(yaw, 1), "pitch": round(max(-90, min(90, pitch)), 1)})
        return _ok("faced", yaw=round(yaw, 1), pitch=round(pitch, 1))

    def approach(self, target, reach=REACH):
        """End within `reach` of `target`, or say why not."""
        for step in range(MAX_WALK_STEPS):
            distance = self._distance_to(target)
            if distance <= reach:
                return _ok("in reach", changed=step > 0, distance=round(distance, 1))
            self.face(target)
            before = self._self()
            self.bridge.rpc("player.press_key", {"key": "forward", "action": "press"})
            self._sleep(WALK_TICK_MS / 1000.0)
            self.bridge.rpc("player.press_key", {"key": "forward", "action": "release"})
            self._sleep(0.15)
            after = self._self()
            moved = math.dist([before["x"], before["z"]], [after["x"], after["z"]])
            if moved < 0.05:
                raise ActionError(
                    f"blocked {round(self._distance_to(target), 1)}m away; the way is not clear")
        raise ActionError(f"still {round(self._distance_to(target), 1)}m away after walking")

    def open_container(self, position):
        """End with the container at `position` open."""
        state = self._container()
        if state.get("open") and self._same_block(state, position):
            return _ok("already open", changed=False)
        self.approach(position)
        self.face(position)
        x, y, z = (int(position[k]) for k in ("x", "y", "z"))
        self.bridge.rpc("container.open", {"x": x, "y": y, "z": z})
        for _ in range(8):
            self._sleep(CONTAINER_PACE_S)
            if (self._container()).get("open"):
                return _ok("opened")
        raise ActionError("the screen never opened")

    def take(self, item):
        """End with at least one more `item` than before, from the open screen."""
        state = self._container()
        if not state.get("open"):
            raise ActionError("nothing is open to take from")
        before = self._count(item)
        for slot in state.get("containerSlots") or []:
            if slot.get("id") != item:
                continue
            self.bridge.rpc("container.click",
                            {"slot": slot["slot"], "button": 0, "mode": "QUICK_MOVE"})
            self._sleep(CONTAINER_PACE_S)
            after = self._count(item)
            if after > before:
                return _ok("took", gained=after - before, carrying=after)
        raise ActionError(f"no {item.split(':')[-1]} in the open container")

    def close_container(self):
        if not (self._container()).get("open"):
            return _ok("nothing was open", changed=False)
        self.bridge.rpc("container.close")
        self._sleep(CONTAINER_PACE_S)
        return _ok("closed")

    def equip(self, item):
        """End holding `item`."""
        me = self._self()
        if me.get("held") == item:
            return _ok("already held", changed=False)
        for stack in self._inventory():
            if stack.get("id") == item and 0 <= (stack.get("slot", -1)) <= 8:
                self.bridge.rpc("player.set_hotbar_slot", {"slot": stack["slot"]})
                self._sleep(0.2)
                if self._self().get("held") == item:
                    return _ok("equipped", slot=stack["slot"])
        raise ActionError(f"{item.split(':')[-1]} is not in the hotbar")

    def throw_at(self, entity_id):
        """Face a creature allowing for the drop, and throw what is held."""
        entity = self._entity(entity_id)
        if not entity:
            raise ActionError(f"entity {entity_id} is gone")
        held = self._self().get("held")
        if not held or held == "minecraft:air":
            raise ActionError("nothing in hand to throw")
        # A thrown item travels in a straight line and stops at the first thing
        # it meets. Without this the bot emptied sixteen snowballs into the
        # barrier in front of a piglin it could not see.
        if not self.bridge.eval(f"return api:canSee({int(entity_id)})"):
            raise ActionError(
                f"no line of sight to the {str(entity.get('type','')).split(':')[-1]} "
                f"{round(entity.get('dist', 0), 1)}m away; something is in the way")
        before = self._count(held)
        self.face(entity, arc_for_throw=True)
        self._sleep(0.15)
        self.bridge.eval("return api:useItem()")
        self._sleep(0.35)
        after = self._count(held)
        return _ok("threw", spent=before - after, at=entity.get("type"),
                   distance=round(entity.get("dist", 0), 1))

    def attack(self, entity_id):
        """End having swung at a creature from inside reach."""
        entity = self._entity(entity_id)
        if not entity:
            raise ActionError(f"entity {entity_id} is gone")
        self.approach(entity, reach=3.0)
        self.face(entity)
        self._sleep(0.1)
        hit = self.bridge.eval(f"return api:attackEntity({int(entity_id)})")
        return _ok("attacked", landed=bool(hit))

    def eat(self, item):
        """End with food restored, or say it did not rise."""
        self.equip(item)
        before = self._self().get("food", 20)
        self.bridge.rpc("player.press_key", {"key": "use", "action": "press"})
        self._sleep(1.8)
        self.bridge.rpc("player.press_key", {"key": "use", "action": "release"})
        self._sleep(0.3)
        after = self._self().get("food", 20)
        return _ok("ate", gained=after - before)

    def acquire_weapon(self, search_radius=8, max_containers=8):
        """End holding a weapon, wherever one has to be found.

        Idempotent: already armed is a success that changed nothing. Otherwise
        it tries the hand, the hotbar, the rest of the inventory, and finally
        the containers around it, nearest first.
        """
        held = (self._self().get("held") or "")
        if is_weapon(held):
            return _ok("already armed", changed=False, weapon=held)

        carried = sorted((s for s in self._inventory() if is_weapon(s.get("id"))),
                         key=lambda s: weapon_rank(s.get("id")))
        for stack in carried:
            slot = stack.get("slot", -1)
            if 0 <= slot <= 8:
                self.equip(stack["id"])
                return _ok("armed from the hotbar", weapon=stack["id"])
            # Carried but out of the hotbar: shift it down, then hold it.
            self.bridge.rpc("container.open_inventory")
            self._sleep(CONTAINER_PACE_S)
            self.bridge.rpc("container.click",
                            {"slot": slot, "button": 0, "mode": "QUICK_MOVE"})
            self._sleep(CONTAINER_PACE_S)
            self.bridge.rpc("container.close")
            self._sleep(CONTAINER_PACE_S)
            try:
                self.equip(stack["id"])
                return _ok("armed from the pack", weapon=stack["id"])
            except ActionError:
                pass

        tried, last_error = 0, "nothing searched"
        for position in self._containers_nearby(search_radius):
            if tried >= max_containers:
                break
            tried += 1
            try:
                self.open_container(position)
            except ActionError as exc:
                last_error = str(exc)
                continue
            inside = (self._container().get("containerSlots") or [])
            weapons = sorted((s for s in inside if is_weapon(s.get("id"))),
                             key=lambda s: weapon_rank(s.get("id")))
            if not weapons:
                self.close_container()
                continue
            item = weapons[0]["id"]
            self.take(item)
            self.close_container()
            self.equip(item)
            return _ok("armed from a container", weapon=item,
                       found_in=f"{position['x']},{position['y']},{position['z']}",
                       searched=tried)
        raise ActionError(
            f"no weapon in {tried} containers within {search_radius} blocks ({last_error})")

    def _containers_nearby(self, radius):
        """Container positions around the player, nearest first."""
        me = self._self()
        blocks = (self.bridge.rpc("world.blocks_around", {"radius": radius}) or {}).get("blocks") or []
        found = [b for b in blocks
                 if any(kind in b.get("id", "")
                        for kind in ("chest", "shulker_box", "barrel"))]
        found.sort(key=lambda b: math.dist([me["x"], me["y"], me["z"]],
                                           [b["x"] + 0.5, b["y"] + 0.5, b["z"] + 0.5]))
        return [{"x": b["x"], "y": b["y"], "z": b["z"]} for b in found]

    def attack_piglins(self, spot=None, max_swings=MAX_SWINGS):
        """Stand where the farm is fought from, and swing at what comes.

        Ends either with a piglin killed or with nothing in reach to hit, and
        says which. Repeating it is safe: the walk is a no-op once you are
        already standing there.
        """
        spot = spot or dict(SAFE_SPOT)
        moved = self.approach(spot, reach=1.2)
        killed, swings, last = 0, 0, None
        while swings < max_swings:
            target = self._nearest_reachable_hostile()
            if not target:
                break
            last = target.get("type")
            self.face(target)
            self._sleep(0.1)
            before = target.get("health")
            self.bridge.eval(f"return api:attackEntity({int(target['id'])})")
            swings += 1
            self._sleep(0.45)
            after = self._entity(target["id"])
            if after is None:
                killed += 1
            elif before is not None and (after.get("health") or 0) <= 0:
                killed += 1
        return _ok("fought" if swings else "nothing in reach",
                   changed=bool(swings or moved.get("changed")),
                   swings=swings, killed=killed, at=last,
                   standing_at=f"{spot['x']},{spot['y']},{spot['z']}")

    def _nearest_reachable_hostile(self):
        """The closest hostile inside melee reach that is actually visible."""
        me = self._self()
        found = json.loads(self.bridge.eval("return api:entitiesJson(12)"))
        candidates = []
        for e in found:
            if not e.get("hostile") or not e.get("living"):
                continue
            distance = math.dist([me["x"], me["y"], me["z"]],
                                 [e["x"], e["y"], e["z"]])
            if distance > MELEE_REACH:
                continue
            if not self.bridge.eval(f"return api:canSee({int(e['id'])})"):
                continue
            candidates.append((distance, e))
        candidates.sort(key=lambda pair: pair[0])
        return candidates[0][1] if candidates else None

    def wait(self):
        self._sleep(0.2)
        return _ok("waited", changed=False)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _same_block(state, position):
        block = state.get("blockPos") or {}
        return all(int(block.get(k, -9999)) == int(position[k]) for k in ("x", "y", "z"))

    def _target_point(self, target):
        if "id" in target and "x" in target and target.get("living") is not None:
            return float(target["x"]), float(target["y"]) + AIM_HEIGHT, float(target["z"])
        if "x" in target:
            return float(target["x"]) + 0.5, float(target["y"]) + 0.5, float(target["z"]) + 0.5
        raise ActionError("target has no position")

    def _distance_to(self, target):
        me = self._self()
        tx, ty, tz = self._target_point(target)
        return math.dist([me["x"], me["y"], me["z"]], [tx, ty, tz])
