"""Two-phase decision: choose the verb, then choose that verb's target.

Asked in one batch, `act` and the target questions cannot see each other. Live,
`act` chose move_stack twelve times at 0.7+ confidence while `target_slot`
answered `none`, so every tick failed with "needs a slot" and nothing could
break the tie — code is not allowed to. Asking the target AFTER the verb is
known removes the disagreement: the second question is told what was chosen.
"""
import unittest

from questions import build_act_questions, build_target_question, target_state


STATE = {
    "self": {"desc": "lmoik, 20/20 health"},
    "order": "take the snowballs",
    "in_frame": [{"id": 11, "desc": "piglin — ahead, 4.4m"}],
    "out_of_frame": [],
    "inventory": {"counts": {"snowball": 16}, "desc": "holding sword"},
    "craftable": [{"result": "gold_ingot", "count": 1, "desc": "craft gold_ingot"}],
    "stations": {"near": [{"id": "furnace", "x": 1, "y": 2, "z": 3, "desc": "furnace 3.9m"}],
                 "more": {}, "desc": "stations"},
    "container": {"screen": "chest", "desc": "chest open",
                  "slots": [{"slot": 2, "id": "minecraft:snowball", "count": 16}]},
    "tick": {"verb_duration_ms": {"attack": 0}},
}


class TestPhaseOne(unittest.TestCase):
    def test_asks_the_verb_and_the_nouls_only(self):
        q = build_act_questions(STATE)
        self.assertIn("act", q)
        for name in ("arrived", "in_danger", "stuck"):
            self.assertIn(name, q)

    def test_does_not_ask_any_target(self):
        # Targets belong to phase two, where the verb is known.
        self.assertFalse([k for k in build_act_questions(STATE) if k.startswith("target")])


class TestPhaseTwo(unittest.TestCase):
    def test_verbs_that_need_nothing_ask_nothing(self):
        for verb in ("hold", "done", "jump", "close"):
            self.assertIsNone(build_target_question(verb, STATE))

    def test_attack_asks_which_creature(self):
        name, q = build_target_question("attack", STATE)
        self.assertEqual(name, "target_entity")
        self.assertIn("11", q["criteria"])

    def test_move_stack_asks_which_slot(self):
        name, q = build_target_question("move_stack", STATE)
        self.assertEqual(name, "target_slot")
        self.assertIn("2", q["criteria"])

    def test_the_question_says_the_verb_was_already_chosen(self):
        # This is the whole point: the model is told the decision is made.
        _, q = build_target_question("move_stack", STATE)
        self.assertIn("move_stack", q["instructions"])
        self.assertIn("decided", q["instructions"].lower())

    def test_craft_asks_which_item(self):
        name, q = build_target_question("craft", STATE)
        self.assertEqual(name, "target_item")
        self.assertIn("gold_ingot", q["criteria"])


class TestPhaseTwoState(unittest.TestCase):
    def test_second_call_sends_only_what_the_question_needs(self):
        slim = target_state("move_stack", STATE)
        self.assertIn("container", slim)
        self.assertIn("order", slim)
        # The entity list has nothing to do with choosing a slot.
        self.assertNotIn("in_frame", slim)

    def test_entity_target_keeps_the_entities(self):
        slim = target_state("attack", STATE)
        self.assertIn("in_frame", slim)
        self.assertNotIn("container", slim)


if __name__ == "__main__":
    unittest.main()


class TestSlotCandidateCoverage(unittest.TestCase):
    """The model cannot choose a slot that was never offered.

    A chest ships 23 used slots; the candidate list capped at 12. The gold sat
    in slot 22 and the coal in slot 18, so phase two answered `none` at 0.79 —
    correctly, because nothing it needed was on the menu. Twelve ticks failed
    on what looked like a model problem and was a coverage problem.
    """

    def test_every_shipped_slot_is_offered(self):
        from questions import slot_candidates
        slots = [{"slot": i, "id": f"minecraft:item_{i}", "count": 1} for i in range(23)]
        state = {"container": {"screen": "chest", "slots": slots, "desc": "chest"}}
        offered = slot_candidates(state)
        for i in range(23):
            self.assertIn(str(i), offered, f"slot {i} was never offered")

    def test_candidate_text_names_the_item_in_the_slot(self):
        from questions import slot_candidates
        state = {"container": {"screen": "chest", "desc": "chest",
                               "slots": [{"slot": 22, "id": "minecraft:gold_nugget",
                                          "count": 2}]}}
        self.assertIn("gold_nugget", slot_candidates(state)["22"])
