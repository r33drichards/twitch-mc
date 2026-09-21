"""Deterministic inventory actions: wear it, hotbar it, drop it, eat it, keep it.

Every one opens the player's own inventory screen, makes exactly one click, and
closes again. The slot numbers are the dangerous part — the screen numbers
slots differently from the inventory itself — so the conversion lives in one
place and is pinned by tests.
"""
import json
import time

from gear import better_armor, better_weapon, is_armor, is_weapon

# api:inventoryJson() numbering -> open-screen numbering.
ARMOR_TO_MENU = {39: 5, 38: 6, 37: 7, 36: 8}      # helmet, chest, legs, boots
OFFHAND_MENU = 45
PACE_S = 0.3


class InventoryError(Exception):
    """The item is not where the action needs it to be."""


def menu_slot(inventory_slot):
    """Where the open inventory screen puts a given inventory slot."""
    slot = int(inventory_slot)
    if 0 <= slot <= 8:
        return slot + 36
    if 9 <= slot <= 35:
        return slot
    if slot in ARMOR_TO_MENU:
        return ARMOR_TO_MENU[slot]
    if slot == 40:
        return OFFHAND_MENU
    raise ValueError(f"no screen slot for inventory slot {slot}")


def _same_body_part(a, b):
    from gear import armor_slot
    return armor_slot(a) is not None and armor_slot(a) == armor_slot(b)


def _ok(what, changed=True, **extra):
    return {"ok": True, "did": what, "changed": changed, **extra}


class InventoryActions:
    def __init__(self, bridge, sleep=time.sleep):
        self.bridge = bridge
        self._sleep = sleep

    def _stacks(self):
        return json.loads(self.bridge.eval("return api:inventoryJson()"))

    def _find(self, item):
        for stack in self._stacks():
            if stack.get("id") == item:
                return stack
        raise InventoryError(f"{item.split(':')[-1]} is not carried")

    def _click(self, inventory_slot, mode, button=0):
        self.bridge.rpc("container.open_inventory")
        self._sleep(PACE_S)
        self.bridge.rpc("container.click",
                        {"slot": menu_slot(inventory_slot), "button": button,
                         "mode": mode})
        self._sleep(PACE_S)
        self.bridge.rpc("container.close")

    def drop(self, item):
        """Throw the whole stack away."""
        stack = self._find(item)
        self._click(stack["slot"], "THROW", button=1)
        return _ok("dropped", item=item, count=stack.get("count", 1))

    def to_hotbar(self, item, slot):
        """Put it in a numbered hotbar slot, swapping out whatever is there.

        A weapon only displaces a weapon it actually beats. Jev once answered
        that a wooden axe belonged in the slot holding a netherite sword, and
        that comparison is a table rather than a judgement.
        """
        slot = int(slot)
        if not 0 <= slot <= 8:
            raise InventoryError(f"hotbar slots are 0-8, not {slot}")
        if is_weapon(item):
            occupant = next((s.get("id") for s in self._stacks()
                             if s.get("slot") == slot), None)
            if not better_weapon(item, occupant):
                return _ok("kept the better weapon", changed=False,
                           item=item, instead_of=occupant)
        stack = self._find(item)
        if stack["slot"] == slot:
            return _ok("already in that slot", changed=False, item=item, slot=slot)
        self._click(stack["slot"], "SWAP", button=slot)
        return _ok("moved to the hotbar", item=item, slot=slot)

    def wear(self, item):
        """Shift-click a piece of armour, which puts it on — if it is better.

        Wearing worse armour than you already have on is strictly a loss, and
        shift-clicking would do it without asking.
        """
        stack = self._find(item)
        if stack["slot"] in ARMOR_TO_MENU:
            return _ok("already worn", changed=False, item=item)
        if is_armor(item):
            worn = {s.get("id") for s in self._stacks()
                    if s.get("slot") in ARMOR_TO_MENU}
            same_place = next((w for w in worn
                               if w and _same_body_part(w, item)), None)
            if not better_armor(item, same_place):
                return _ok("worn armour is better", changed=False,
                           item=item, instead_of=same_place)
        self._click(stack["slot"], "QUICK_MOVE")
        return _ok("worn", item=item)

    def eat(self, item, hold_ms=1800):
        """Hold it and hold right-click until it goes down."""
        stack = self._find(item)
        if stack["slot"] > 8:
            self.to_hotbar(item, 3)
            stack = self._find(item)
        self.bridge.rpc("player.set_hotbar_slot", {"slot": stack["slot"]})
        self._sleep(0.2)
        before = (self.bridge.eval("return {food=api:food()}") or {}).get("food", 20)
        self.bridge.rpc("player.press_key", {"key": "use", "action": "press"})
        self._sleep(hold_ms / 1000.0)
        self.bridge.rpc("player.press_key", {"key": "use", "action": "release"})
        self._sleep(0.3)
        after = (self.bridge.eval("return {food=api:food()}") or {}).get("food", 20)
        return _ok("ate", item=item, gained=after - before)

    def keep(self, item):
        """Leave it alone. Building blocks and anything else worth carrying."""
        return _ok("kept", changed=False, item=item)
