"""Inventory actions, and the slot mapping they depend on.

`api:inventoryJson()` numbers slots the way the player's inventory does:
0-8 hotbar, 9-35 pack, 36-39 armour, 40 offhand. The open inventory *screen*
numbers them differently: 5-8 armour, 9-35 pack, 36-44 hotbar, 45 offhand.
Clicking the wrong number throws away the wrong thing, so the conversion is
pinned here.
"""
import unittest

from inventory_actions import InventoryActions, menu_slot


class TestMenuSlot(unittest.TestCase):
    def test_hotbar_moves_to_the_bottom_row(self):
        self.assertEqual(menu_slot(0), 36)
        self.assertEqual(menu_slot(8), 44)

    def test_the_pack_keeps_its_numbers(self):
        self.assertEqual(menu_slot(9), 9)
        self.assertEqual(menu_slot(35), 35)

    def test_armour_is_helmet_first(self):
        self.assertEqual(menu_slot(39), 5)   # helmet
        self.assertEqual(menu_slot(38), 6)   # chestplate
        self.assertEqual(menu_slot(37), 7)   # leggings
        self.assertEqual(menu_slot(36), 8)   # boots

    def test_the_offhand_is_last(self):
        self.assertEqual(menu_slot(40), 45)

    def test_an_impossible_slot_is_refused(self):
        with self.assertRaises(ValueError):
            menu_slot(99)


class FakeBridge:
    def __init__(self, stacks):
        self.stacks, self.calls = stacks, []

    def rpc(self, method, params=None, timeout=10.0):
        self.calls.append((method, dict(params or {})))
        if method == "container.state":
            return {"open": True, "screen": "InventoryScreen"}
        return {}

    def eval(self, code, timeout_ms=500):
        import json
        if "inventoryJson" in code:
            return json.dumps(self.stacks)
        return {"food": 20, "health": 20.0, "slot": 0, "held": "minecraft:air"}


def sword_in_pack():
    return [{"slot": 14, "id": "minecraft:diamond_sword", "count": 1}]


class TestActions(unittest.TestCase):
    def test_dropping_throws_the_whole_stack_from_the_right_slot(self):
        bridge = FakeBridge(sword_in_pack())
        InventoryActions(bridge, sleep=lambda s: None).drop("minecraft:diamond_sword")
        clicks = [p for m, p in bridge.calls if m == "container.click"]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0]["slot"], 14)          # pack slots keep their number
        self.assertEqual(clicks[0]["mode"], "THROW")
        self.assertEqual(clicks[0]["button"], 1)         # 1 = the whole stack

    def test_moving_to_a_hotbar_slot_swaps(self):
        bridge = FakeBridge(sword_in_pack())
        InventoryActions(bridge, sleep=lambda s: None).to_hotbar(
            "minecraft:diamond_sword", 1)
        click = [p for m, p in bridge.calls if m == "container.click"][0]
        self.assertEqual(click["mode"], "SWAP")
        self.assertEqual(click["button"], 1)

    def test_wearing_armour_shift_clicks_it(self):
        bridge = FakeBridge([{"slot": 12, "id": "minecraft:iron_chestplate", "count": 1}])
        InventoryActions(bridge, sleep=lambda s: None).wear("minecraft:iron_chestplate")
        click = [p for m, p in bridge.calls if m == "container.click"][0]
        self.assertEqual(click["mode"], "QUICK_MOVE")
        self.assertEqual(click["slot"], 12)

    def test_every_action_closes_the_screen_it_opened(self):
        bridge = FakeBridge(sword_in_pack())
        InventoryActions(bridge, sleep=lambda s: None).drop("minecraft:diamond_sword")
        methods = [m for m, _ in bridge.calls]
        self.assertIn("container.open_inventory", methods)
        self.assertEqual(methods[-1], "container.close")

    def test_acting_on_something_not_carried_is_refused(self):
        bridge = FakeBridge([])
        with self.assertRaises(Exception):
            InventoryActions(bridge, sleep=lambda s: None).drop("minecraft:diamond_sword")

    def test_keeping_something_touches_nothing(self):
        bridge = FakeBridge(sword_in_pack())
        result = InventoryActions(bridge, sleep=lambda s: None).keep("minecraft:cobblestone")
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(bridge.calls, [])


if __name__ == "__main__":
    unittest.main()
