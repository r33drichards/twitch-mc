"""Full Keyboard Gameplay: the action set is Minecraft's own key map, nothing more.

Every verb here is a key that exists in the game's accessibility mapping, and
nothing outside it is offered — no semantic "craft this" or "open that", just
the keys a player presses.
"""
import unittest

from dispatch import Dispatcher, LOOK_SLIGHT_DEG, LOOK_STEP_DEG
from questions import ACT_CRITERIA, FULL_KEYBOARD_VERBS, available_verbs
from tests.test_dispatch import FakeBridge, PLAYER_AT_ORIGIN


def rot_after(verb, yaw=90.0, pitch=0.0):
    state = {"player.state": {"inWorld": True,
                              "pos": {"x": 0.0, "y": 64.0, "z": 0.0},
                              "blockPos": {"x": 0, "y": 64, "z": 0},
                              "rot": {"yaw": yaw, "pitch": pitch}}}
    bridge = FakeBridge(rpc_results=state)
    Dispatcher(bridge, sleep=lambda s: None).execute(verb)
    calls = [c[2] for c in bridge.calls
             if c[0] == "rpc" and c[1] == "player.set_rotation"]
    return calls[-1] if calls else None


class TestTheSetItself(unittest.TestCase):
    def test_only_real_keys_are_offered(self):
        # A subset, not equality: affordances drop keys that cannot do anything
        # in the moment — the selected slot's own key, empty slots, a single
        # right-click into an empty view. What matters is that nothing outside
        # the game's key map ever appears.
        offered = available_verbs({"container": None}, controls="keyboard")
        self.assertTrue(set(offered).issubset(set(FULL_KEYBOARD_VERBS)))
        self.assertTrue(set(offered), "some keys must always be offered")

    def test_no_semantic_verbs_survive_in_this_mode(self):
        offered = set(available_verbs({"container": None}, controls="keyboard"))
        for gone in ("craft", "open", "close", "move_stack", "equip",
                     "mine_front", "place_block", "advance", "turn_toward"):
            self.assertNotIn(gone, offered)

    def test_every_key_can_run_and_is_described(self):
        for verb in FULL_KEYBOARD_VERBS:
            self.assertIn(verb, Dispatcher.VERBS, verb)
            self.assertIn(verb, ACT_CRITERIA, verb)


class TestCamera(unittest.TestCase):
    def test_slight_keys_move_fifteen_degrees(self):
        self.assertAlmostEqual(rot_after("look_up_slight")["pitch"], -LOOK_SLIGHT_DEG, places=1)
        self.assertAlmostEqual(rot_after("look_down_slight")["pitch"], LOOK_SLIGHT_DEG, places=1)

    def test_numpad_keys_move_forty_five_degrees(self):
        self.assertAlmostEqual(rot_after("look_up")["pitch"], -LOOK_STEP_DEG, places=1)
        self.assertAlmostEqual(rot_after("look_down")["pitch"], LOOK_STEP_DEG, places=1)
        self.assertAlmostEqual(rot_after("look_left")["yaw"], 90.0 - LOOK_STEP_DEG, places=1)
        self.assertAlmostEqual(rot_after("look_right")["yaw"], 90.0 + LOOK_STEP_DEG, places=1)

    def test_diagonals_move_both_axes(self):
        r = rot_after("look_up_left")
        self.assertAlmostEqual(r["pitch"], -LOOK_STEP_DEG, places=1)
        self.assertAlmostEqual(r["yaw"], 90.0 - LOOK_STEP_DEG, places=1)
        r = rot_after("look_down_right")
        self.assertAlmostEqual(r["pitch"], LOOK_STEP_DEG, places=1)
        self.assertAlmostEqual(r["yaw"], 90.0 + LOOK_STEP_DEG, places=1)

    def test_look_center_levels_the_pitch(self):
        self.assertAlmostEqual(rot_after("look_center", pitch=40.0)["pitch"], 0.0, places=1)

    def test_pitch_cannot_pass_straight_down(self):
        self.assertLessEqual(rot_after("look_down", pitch=80.0)["pitch"], 90.0)

    def test_smooth_keys_move_a_smaller_amount(self):
        smooth = abs(rot_after("look_up_smooth")["pitch"])
        self.assertLess(smooth, LOOK_STEP_DEG)
        self.assertGreater(smooth, 0.0)


class TestItemsAndActions(unittest.TestCase):
    def test_cycle_keys_change_the_selected_slot(self):
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("cycle_item_right")
        self.assertTrue(any(c[0] == "rpc" and c[1] == "player.set_hotbar_slot"
                            for c in bridge.calls))

    def test_attack_is_the_q_key(self):
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("attack")
        keys = [(c[2].get("key"), c[2].get("action")) for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.press_key"]
        self.assertIn(("attack", "press"), keys)

    def test_inventory_is_the_c_key(self):
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("inventory")
        self.assertTrue(any(c[0] == "rpc" and c[1] == "container.open_inventory"
                            for c in bridge.calls))


if __name__ == "__main__":
    unittest.main()
