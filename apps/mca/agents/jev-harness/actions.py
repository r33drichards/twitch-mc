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
REACH = 4.0               # container and melee reach, kept under the server's 4.5
WALK_TICK_MS = 250
MAX_WALK_STEPS = 12
CONTAINER_PACE_S = 0.35   # the server drops container ops fired faster than this


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
