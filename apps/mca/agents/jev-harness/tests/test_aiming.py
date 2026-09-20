"""Aiming as its own judgment.

Asked as one flat choice among thirty-eight keys, `look_right` sat at 0.02
while `use_item` took 0.54 — so it threw into a chest sixteen times. Choosing a
direction to move the view is a different kind of question from choosing what
to do, and it is the kind a System One model answers well: given the target is
137 degrees to your left, which way does the view go?

So `aim` is one verb in the act choice, and which way is asked after, knowing
that aiming is what was chosen.
"""
import unittest

from dispatch import Dispatcher
from questions import (AIM_KEYS, available_verbs, build_target_question,
                       target_state)

STATE = {
    "self": {"desc": "facing yaw -138, pitch 20", "yaw": -138.0, "pitch": 20.0},
    "order": "throw a snowball at a piglin",
    "hazards": {"desc": "on bamboo_planks"},
    "looking_at": {"id": "chest", "desc": "looking at chest, 5.0m away"},
    "in_frame": [{"id": 7, "desc": "zombified_piglin — behind-left, 137° left, 17m away"}],
    "out_of_frame": [],
    "inventory": {"counts": {"snowball": 33}, "held": {"slot": 0, "id": "snowball"},
                  "hotbar": [{"slot": 0, "id": "snowball"}]},
    "craftable": [], "stations": {"near": []}, "container": None,
}


class TestTheActChoiceStaysSmall(unittest.TestCase):
    def test_aim_is_offered_as_one_verb(self):
        self.assertIn("aim", available_verbs(STATE, controls="keyboard"))

    def test_the_individual_look_keys_are_not_in_the_act_choice(self):
        offered = set(available_verbs(STATE, controls="keyboard"))
        for key in AIM_KEYS:
            self.assertNotIn(key, offered, f"{key} should be behind `aim`")

    def test_aim_is_offered_in_semantic_mode_too(self):
        self.assertIn("aim", available_verbs(STATE))


class TestTheAimQuestion(unittest.TestCase):
    def test_choosing_aim_asks_which_way(self):
        name, question = build_target_question("aim", STATE)
        self.assertEqual(name, "target_look")
        for key in ("look_left", "look_right", "look_up", "look_down"):
            self.assertIn(key, question["criteria"])

    def test_the_question_says_aiming_is_already_decided(self):
        _, question = build_target_question("aim", STATE)
        self.assertIn("aim", question["instructions"].lower())

    def test_each_direction_says_how_far_it_moves(self):
        _, question = build_target_question("aim", STATE)
        self.assertIn("45", question["criteria"]["look_left"])
        self.assertIn("10", question["criteria"]["look_left_smooth"])

    def test_it_is_given_the_bearings_and_the_crosshair(self):
        slim = target_state("aim", STATE)
        self.assertIn("in_frame", slim)
        self.assertIn("looking_at", slim)
        self.assertIn("self", slim)


class TestExecutingAim(unittest.TestCase):
    def test_the_chosen_direction_is_what_moves_the_view(self):
        from tests.test_dispatch import FakeBridge, PLAYER_AT_ORIGIN
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("aim", {"look": "look_left"})
        rot = [c[2] for c in bridge.calls
               if c[0] == "rpc" and c[1] == "player.set_rotation"][-1]
        self.assertLess(rot["yaw"], 0.0)   # from yaw 0, left is negative

    def test_aim_without_a_direction_is_a_reported_failure(self):
        from tests.test_dispatch import FakeBridge, PLAYER_AT_ORIGIN
        out = Dispatcher(FakeBridge(rpc_results=PLAYER_AT_ORIGIN),
                         sleep=lambda s: None).execute("aim", None)
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
