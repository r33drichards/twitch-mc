"""Inventory state: what is carried, what is worn, and what just arrived.

The whole state for this profile. No world, no entities, no stations — the job
is deciding what to do with items as they turn up.
"""
import unittest

from inventory_state import InventoryWatcher, build_state


def stack(slot, item, count=1, damage=None, max_damage=None):
    row = {"slot": slot, "id": f"minecraft:{item}", "count": count}
    if max_damage:
        row.update({"damage": damage or 0, "max_damage": max_damage})
    return row


class FakeBridge:
    def __init__(self, stacks, food=20, health=20.0, held_slot=0):
        self.stacks = stacks
        self.food, self.health, self.held_slot = food, health, held_slot

    def eval(self, code, timeout_ms=500):
        import json
        if "inventoryJson" in code:
            return json.dumps(self.stacks)
        return {"food": self.food, "health": self.health, "slot": self.held_slot,
                "held": next((s["id"] for s in self.stacks
                              if s["slot"] == self.held_slot), "minecraft:air")}


class TestNewestItem(unittest.TestCase):
    def test_nothing_is_new_on_the_first_look(self):
        watcher = InventoryWatcher()
        watcher.observe([stack(0, "dirt", 3)])
        self.assertEqual(watcher.newest(), [])

    def test_a_fresh_item_is_reported(self):
        watcher = InventoryWatcher()
        watcher.observe([stack(0, "dirt", 3)])
        watcher.observe([stack(0, "dirt", 3), stack(1, "iron_sword")])
        self.assertEqual([n["id"] for n in watcher.newest()], ["minecraft:iron_sword"])

    def test_a_bigger_stack_of_something_carried_counts_as_new(self):
        watcher = InventoryWatcher()
        watcher.observe([stack(0, "arrow", 4)])
        watcher.observe([stack(0, "arrow", 12)])
        newest = watcher.newest()
        self.assertEqual(newest[0]["id"], "minecraft:arrow")
        self.assertEqual(newest[0]["gained"], 8)

    def test_losing_something_is_not_new(self):
        watcher = InventoryWatcher()
        watcher.observe([stack(0, "arrow", 12)])
        watcher.observe([stack(0, "arrow", 4)])
        self.assertEqual(watcher.newest(), [])

    def test_it_remembers_only_the_latest_arrival(self):
        watcher = InventoryWatcher()
        watcher.observe([])
        watcher.observe([stack(0, "dirt")])
        watcher.observe([stack(0, "dirt"), stack(1, "bread", 2)])
        self.assertEqual([n["id"] for n in watcher.newest()], ["minecraft:bread"])


class TestBuildState(unittest.TestCase):
    def test_it_carries_the_essentials_and_nothing_else(self):
        bridge = FakeBridge([stack(0, "iron_sword"), stack(3, "bread", 4)], food=14)
        state = build_state(bridge, InventoryWatcher())
        self.assertEqual(set(state), {"held", "hotbar", "armor", "pack", "food",
                                      "health", "free_slots", "newest", "desc"})

    def test_hunger_is_visible(self):
        bridge = FakeBridge([stack(0, "bread", 2)], food=11)
        self.assertEqual(build_state(bridge, InventoryWatcher())["food"], 11)

    def test_worn_armour_is_separated_from_the_pack(self):
        bridge = FakeBridge([stack(36, "iron_boots", 1, 0, 195),
                             stack(39, "iron_helmet", 1, 0, 165),
                             stack(0, "dirt", 5)])
        state = build_state(bridge, InventoryWatcher())
        worn = {piece["id"] for piece in state["armor"]}
        self.assertEqual(worn, {"minecraft:iron_boots", "minecraft:iron_helmet"})
        self.assertNotIn("minecraft:iron_boots", [p["id"] for p in state["pack"]])

    def test_the_first_nine_slots_are_the_hotbar(self):
        bridge = FakeBridge([stack(2, "stone_sword"), stack(20, "cobblestone", 64)])
        state = build_state(bridge, InventoryWatcher())
        self.assertEqual([h["slot"] for h in state["hotbar"]], [2])
        self.assertEqual([p["slot"] for p in state["pack"]], [20])


if __name__ == "__main__":
    unittest.main()
