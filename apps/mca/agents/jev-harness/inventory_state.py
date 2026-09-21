"""The whole state for inventory management: what you carry, wear, and just got.

Nothing about the world. This profile's job is deciding what to do with items
as they arrive, so the state is the pack, the hotbar, the armour, hunger, and
the newest arrival — which is the thing most decisions are actually about.
"""
import json

HOTBAR_SLOTS = range(0, 9)
ARMOR_SLOTS = range(36, 40)     # boots, leggings, chestplate, helmet
OFFHAND_SLOT = 40


def _name(item_id):
    return str(item_id or "").split(":")[-1]


def _row(stack):
    row = {"slot": stack.get("slot"), "id": stack.get("id"),
           "count": stack.get("count", 1)}
    if stack.get("max_damage"):
        used, total = stack.get("damage", 0), stack["max_damage"]
        row["durability"] = total - used
        row["max_durability"] = total
    for extra in ("enchants", "custom_name"):
        if stack.get(extra):
            row[extra] = stack[extra]
    return row


class InventoryWatcher:
    """Remembers the last look so the newest arrival can be named.

    Only gains count. Losing something is not news: it was spent, dropped or
    eaten, and the decision about it has already been made.
    """

    def __init__(self):
        self._previous = None
        self._newest = []

    def observe(self, stacks):
        counts = {}
        for stack in stacks or []:
            item = stack.get("id")
            if item and item != "minecraft:air":
                counts[item] = counts.get(item, 0) + stack.get("count", 1)
        if self._previous is None:
            self._previous, self._newest = counts, []
            return self._newest
        arrivals = []
        for item, total in counts.items():
            gained = total - self._previous.get(item, 0)
            if gained > 0:
                arrivals.append({"id": item, "name": _name(item),
                                 "gained": gained, "carrying": total})
        self._previous = counts
        if arrivals:
            self._newest = arrivals
        elif arrivals == [] and self._newest:
            # Keep reporting the last arrival until something else turns up,
            # so a decision about it survives a tick that changed nothing.
            pass
        return arrivals

    def newest(self):
        return list(self._newest)

    def clear(self):
        self._newest = []


def build_state(bridge, watcher):
    """One tick of inventory state."""
    stacks = json.loads(bridge.eval("return api:inventoryJson()"))
    me = bridge.eval(
        "return {food=api:food(),health=api:health(),slot=api:hotbarSlot(),"
        "held=api:heldItem()}")
    watcher.observe(stacks)

    carried = [s for s in stacks if s.get("id") and s["id"] != "minecraft:air"]
    hotbar = [_row(s) for s in carried if s.get("slot") in HOTBAR_SLOTS]
    armor = [_row(s) for s in carried if s.get("slot") in ARMOR_SLOTS]
    pack = [_row(s) for s in carried
            if s.get("slot") not in HOTBAR_SLOTS and s.get("slot") not in ARMOR_SLOTS
            and s.get("slot") != OFFHAND_SLOT]

    newest = watcher.newest()
    worn = ", ".join(_name(p["id"]) for p in armor) or "nothing"
    arrival = (", ".join(f"{n['gained']} {n['name']}" for n in newest)
               if newest else "nothing new")
    return {
        "held": me.get("held"),
        "hotbar": hotbar,
        "armor": armor,
        "pack": pack,
        "food": me.get("food"),
        "health": round(float(me.get("health") or 0), 1),
        "free_slots": 36 - len([s for s in carried if s.get("slot", 99) < 36]),
        "newest": newest,
        "desc": (f"holding {_name(me.get('held'))}, wearing {worn}, "
                 f"food {me.get('food')}/20, health {round(float(me.get('health') or 0))}/20; "
                 f"just picked up {arrival}"),
    }
