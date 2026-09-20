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


class TestTheTwoModesStaySeparate(unittest.TestCase):
    """Keyboard keys must not leak into the semantic set, or the reverse.

    Adding the Full Keyboard criteria to the shared dict put slot_1..slot_9 and
    the look keys into semantic mode, and a live run spent fifteen ticks
    pressing slot_2 with nothing to show for it.
    """

    def test_semantic_mode_offers_no_raw_keys(self):
        from questions import available_verbs
        offered = set(available_verbs(state()))
        for key in ("slot_1", "slot_5", "look_up", "look_down_left", "look_center",
                    "cycle_item_left", "inventory", "drop_item",
                    "walk_forward", "walk_backward", "sneak", "sprint"):
            self.assertNotIn(key, offered, f"{key} leaked into semantic mode")

    def test_semantic_mode_keeps_its_own_verbs(self):
        from questions import available_verbs
        offered = set(available_verbs(state()))
        for verb in ("advance", "retreat", "attack", "equip", "open",
                     "use_item", "use_item_hold", "hold", "done"):
            self.assertIn(verb, offered, verb)

    def test_keyboard_mode_offers_no_semantic_verbs(self):
        from questions import available_verbs
        offered = set(available_verbs(state(), controls="keyboard"))
        for verb in ("equip", "open", "craft", "move_stack", "close", "hold", "done"):
            self.assertNotIn(verb, offered, f"{verb} leaked into keyboard mode")


class TestNoOpVerbsAreNotOffered(unittest.TestCase):
    """Don't offer a key that cannot change anything.

    Twenty ticks went to `slot_2` — the key for the slot already selected — and
    seven to equipping a sword already in hand. A no-op never fails, so nothing
    catches it. The fix is the same rule as hiding `craft` with no table open:
    only offer what could actually do something.
    """

    HOLDING_SLOT_1 = {"self": {}, "order": "x", "in_frame": [], "out_of_frame": [],
                      "craftable": [], "stations": {"near": []}, "container": None,
                      "inventory": {"counts": {"netherite_sword": 1, "snowball": 65},
                                    "held": {"slot": 1, "id": "netherite_sword"}}}

    def test_the_selected_slot_key_is_not_offered(self):
        from questions import available_verbs
        offered = available_verbs(self.HOLDING_SLOT_1, controls="keyboard")
        self.assertNotIn("slot_2", offered)   # hotbar index 1 is the 2 key

    def test_the_other_slot_keys_still_are(self):
        from questions import available_verbs
        offered = available_verbs(self.HOLDING_SLOT_1, controls="keyboard")
        for key in ("slot_1", "slot_3", "slot_9"):
            self.assertIn(key, offered)

    def test_equipping_what_is_already_held_is_not_offered(self):
        from questions import item_candidates
        options = item_candidates(self.HOLDING_SLOT_1)
        self.assertNotIn("netherite_sword", options)
        self.assertIn("snowball", options)


class TestEmptySlotKeysAreNotOffered(unittest.TestCase):
    """A key that selects an empty slot cannot help either.

    With the held slot hidden, the loop simply moved to toggling between two
    empty slots. The hotbar is visible to a player at a glance; the offered
    keys should match what is actually in it.
    """

    HOTBAR = {"self": {}, "order": "x", "in_frame": [], "out_of_frame": [],
              "craftable": [], "stations": {"near": []}, "container": None,
              "inventory": {"counts": {"snowball": 49},
                            "held": {"slot": 0, "id": None},
                            "hotbar": [{"slot": 2, "id": "snowball", "count": 16},
                                       {"slot": 5, "id": "torch", "count": 64}]}}

    def test_only_slots_holding_something_are_offered(self):
        from questions import available_verbs
        offered = set(available_verbs(self.HOTBAR, controls="keyboard"))
        self.assertIn("slot_3", offered)    # hotbar index 2 holds snowballs
        self.assertIn("slot_6", offered)    # index 5 holds torches
        for empty in ("slot_1", "slot_2", "slot_4", "slot_7", "slot_8", "slot_9"):
            self.assertNotIn(empty, offered, f"{empty} selects an empty slot")

    def test_without_hotbar_detail_every_slot_stays_offered(self):
        # Never hide a key on missing information.
        from questions import available_verbs
        bare = dict(self.HOTBAR, inventory={"counts": {}, "held": {}})
        offered = set(available_verbs(bare, controls="keyboard"))
        self.assertIn("slot_4", offered)
