"""Only offer verbs the situation actually allows.

Offering `attack` while a chest screen is open is offering a fiction: the swing
cannot land, and the model has no way to know that. Equally, `move_stack` with
nothing open can only fail. This is the same rule as enumerating candidates —
the model can only pick what it is shown, so show it what is真 possible.
"""
import unittest

from dispatch import Dispatcher
from questions import ACT_CRITERIA, available_verbs, build_act_questions


def state(container=None, **kw):
    base = {"self": {"desc": "x"}, "order": "do the thing",
            "in_frame": [], "out_of_frame": [],
            "inventory": {"counts": {"snowball": 4}},
            "craftable": [{"result": "gold_ingot", "count": 1, "desc": "craft gold_ingot"}],
            "stations": {"near": [], "more": {}, "desc": ""},
            "container": container}
    base.update(kw)
    return base


class TestNothingOpen(unittest.TestCase):
    def test_container_verbs_are_hidden(self):
        verbs = available_verbs(state())
        for verb in ("move_stack", "close"):
            self.assertNotIn(verb, verbs)

    def test_craft_is_hidden_without_a_crafting_table_open(self):
        self.assertNotIn("craft", available_verbs(state()))

    def test_the_world_verbs_are_offered(self):
        verbs = available_verbs(state())
        for verb in ("advance", "attack", "open", "use_item", "aim_higher"):
            self.assertIn(verb, verbs)


class TestChestOpen(unittest.TestCase):
    CHEST = {"screen": "chest", "desc": "chest open",
             "slots": [{"slot": 2, "id": "minecraft:snowball", "count": 16}]}

    def test_the_container_verbs_appear(self):
        verbs = available_verbs(state(container=self.CHEST))
        self.assertIn("move_stack", verbs)
        self.assertIn("close", verbs)

    def test_world_verbs_vanish_while_a_screen_is_up(self):
        verbs = available_verbs(state(container=self.CHEST))
        for verb in ("advance", "attack", "mine_front", "use_item", "open", "aim_higher"):
            self.assertNotIn(verb, verbs)

    def test_craft_is_still_hidden_at_a_chest(self):
        self.assertNotIn("craft", available_verbs(state(container=self.CHEST)))

    def test_waiting_and_finishing_stay_possible(self):
        verbs = available_verbs(state(container=self.CHEST))
        self.assertIn("hold", verbs)
        self.assertIn("done", verbs)


class TestCraftingTableOpen(unittest.TestCase):
    TABLE = {"screen": "crafting", "desc": "crafting open", "slots": []}

    def test_craft_becomes_available(self):
        self.assertIn("craft", available_verbs(state(container=self.TABLE)))

    def test_the_container_verbs_are_still_there(self):
        verbs = available_verbs(state(container=self.TABLE))
        self.assertIn("close", verbs)
        self.assertIn("move_stack", verbs)


class TestTheQuestionFollowsTheAffordances(unittest.TestCase):
    def test_act_offers_exactly_what_is_available(self):
        st = state(container={"screen": "chest", "desc": "chest open", "slots": []})
        criteria = build_act_questions(st)["act"]["criteria"]
        self.assertEqual(set(criteria), set(available_verbs(st)))

    def test_every_offered_verb_can_actually_be_run(self):
        for st in (state(), state(container={"screen": "crafting", "slots": []})):
            for verb in available_verbs(st):
                self.assertIn(verb, Dispatcher.VERBS)

    def test_nothing_offered_is_missing_its_criterion(self):
        self.assertFalse(set(available_verbs(state())) - set(ACT_CRITERIA))


if __name__ == "__main__":
    unittest.main()
