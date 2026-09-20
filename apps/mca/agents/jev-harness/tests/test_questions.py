"""The question set: the only place the model is asked anything."""
import unittest

from dispatch import Dispatcher
from questions import ACT_CRITERIA, build_questions, build_candidates, ORDER_QUESTIONS


STATE = {
    "self": {"desc": "lmoik at (5733,231,440), 18.9/20 health"},
    "in_frame": [{"id": 11, "desc": "zombified_piglin — ahead, 4° left, 4.4m away, level"}],
    "out_of_frame": [{"id": 62, "desc": "ghast — behind, 40m away (last seen 3.0s ago)"}],
    "order": "kill the piglins",
}


class TestActCriteria(unittest.TestCase):
    def test_every_verb_the_dispatcher_can_run_has_a_criterion(self):
        # A verb with no criterion is a verb the model can never choose, which
        # silently removes an escape route.
        missing = set(Dispatcher.VERBS) - set(ACT_CRITERIA)
        self.assertEqual(missing, set(), f"verbs with no criterion: {missing}")

    def test_every_criterion_says_something(self):
        # Keyboard keys describe themselves in a few words ("W: walk forward."),
        # so the bar is that each one is present and meaningful, not that it is
        # long. The semantic verbs still carry a situation.
        for verb, text in ACT_CRITERIA.items():
            self.assertTrue(text.strip(), f"{verb} has an empty criterion")
            self.assertGreater(len(text), 10, f"{verb}'s criterion says nothing")


class TestBuildCandidates(unittest.TestCase):
    def test_lists_visible_and_remembered_entities(self):
        c = build_candidates(STATE)
        self.assertIn("11", c)
        self.assertIn("62", c)

    def test_always_offers_a_no_target_option(self):
        self.assertIn("none", build_candidates(STATE))

    def test_candidate_text_distinguishes_the_options(self):
        c = build_candidates(STATE)
        self.assertNotEqual(c["11"], c["62"])
        self.assertIn("piglin", c["11"])


class TestBuildQuestions(unittest.TestCase):
    def test_asks_act_and_its_targets_in_one_batch(self):
        q = build_questions(STATE)
        self.assertEqual(q["act"]["type"], "choice")
        # Targets are split by kind; see TestSpeculativeTargetQuestions.
        self.assertEqual(q["target_entity"]["type"], "choice")

    def test_act_offers_only_verbs_the_dispatcher_can_run(self):
        # The set is now situational — see tests/test_affordances.py — so the
        # invariant is that everything offered is runnable, not that everything
        # runnable is offered.
        q = build_questions(STATE)
        self.assertTrue(set(q["act"]["criteria"]).issubset(set(Dispatcher.VERBS)))

    def test_supporting_nouls_ride_along(self):
        q = build_questions(STATE)
        for name in ("arrived", "in_danger", "stuck"):
            self.assertEqual(q[name]["type"], "noul")

    def test_danger_criterion_states_a_concrete_threshold(self):
        # "immediate danger" with no number produced a 0.44 non-answer in testing.
        text = str(build_questions(STATE)["in_danger"]["criteria"])
        self.assertRegex(text, r"\d")

    def test_act_instructions_tell_the_model_what_a_verb_costs_in_time(self):
        q = build_questions(STATE)
        self.assertIn("verb_duration_ms", q["act"]["instructions"])


class TestOrderIntake(unittest.TestCase):
    def test_order_intake_is_a_separate_question_set(self):
        self.assertIn("order_kind", ORDER_QUESTIONS)
        self.assertEqual(ORDER_QUESTIONS["order_kind"]["type"], "choice")


if __name__ == "__main__":
    unittest.main()


class TestCandidatesCoverVerbParameters(unittest.TestCase):
    """A verb is useless if its parameter cannot be named.

    The model picks one option from an enumerated list, so equipping snowballs,
    crafting ingots and opening the furnace are only possible if those things
    appear as candidates. Code enumerates what exists; the model picks.
    """

    STATE_WITH_STUFF = {
        "self": {"desc": "x"},
        "in_frame": [{"id": 11, "desc": "piglin — ahead, 4.4m"}],
        "out_of_frame": [],
        "inventory": {"counts": {"minecraft:snowball": 16, "minecraft:rotten_flesh": 24},
                      "desc": "holding sword; 16 snowball, 24 rotten_flesh"},
        "craftable": [{"result": "minecraft:gold_ingot", "desc": "gold_ingot from 9 nuggets"}],
        # state.py ships stations as {near, more, desc}, not a bare list.
        "stations": {"near": [{"id": "furnace", "x": 5733, "y": 232, "z": 436,
                               "desc": "furnace at (5733,232,436) — ahead, 4.0m, in reach"}],
                     "more": {"chest": 17},
                     "desc": "32 interactable blocks within 5 blocks"},
    }

    def test_carried_items_are_offered(self):
        c = build_candidates(self.STATE_WITH_STUFF)
        self.assertIn("minecraft:snowball", c)

    def test_craftable_results_are_offered(self):
        c = build_candidates(self.STATE_WITH_STUFF)
        self.assertIn("minecraft:gold_ingot", c)

    def test_station_positions_are_offered_as_coordinates(self):
        c = build_candidates(self.STATE_WITH_STUFF)
        self.assertIn("5733,232,436", c)

    def test_entities_still_offered_alongside(self):
        self.assertIn("11", build_candidates(self.STATE_WITH_STUFF))


class TestSpeculativeTargetQuestions(unittest.TestCase):
    """One target question cannot serve every verb.

    Questions cannot see each other's answers, so a single `target` asked
    against 30 mixed candidates has no idea whether the verb needs a creature,
    a position, an item or a slot — and answers `none`. Several narrow
    questions are asked instead, each stating its premise, and code consumes
    the one belonging to the chosen verb.
    """

    STATE = TestCandidatesCoverVerbParameters.STATE_WITH_STUFF

    def test_asks_a_separate_question_per_target_kind(self):
        q = build_questions(self.STATE)
        for name in ("target_entity", "target_place", "target_item"):
            self.assertIn(name, q)
            self.assertEqual(q[name]["type"], "choice")

    def test_each_target_question_stands_on_its_own(self):
        # Phrased as a conditional ("if the action is open, which place?") every
        # one of these answered `none` with high confidence against the live
        # world: the premise names a verb the question cannot see. Each must be
        # answerable on its own terms, and say that it is read selectively.
        q = build_questions(self.STATE)
        self.assertIn("creature", q["target_entity"]["instructions"])
        self.assertIn("open", q["target_place"]["instructions"])
        self.assertIn("craft", q["target_item"]["instructions"])
        with_container = dict(self.STATE)
        with_container["container"] = {"screen": "chest", "desc": "chest open",
                                       "slots": [{"slot": 2, "id": "minecraft:snowball",
                                                  "count": 16}]}
        full = build_questions(with_container)
        for name in ("target_entity", "target_place", "target_item", "target_slot"):
            text = full[name]["instructions"]
            self.assertNotIn("If the action is", text)
            self.assertIn("order", text)

    def test_entity_question_offers_creatures_not_items(self):
        c = build_questions(self.STATE)["target_entity"]["criteria"]
        self.assertIn("11", c)
        self.assertNotIn("minecraft:snowball", c)

    def test_item_question_offers_carried_and_craftable_not_creatures(self):
        c = build_questions(self.STATE)["target_item"]["criteria"]
        self.assertIn("minecraft:snowball", c)
        self.assertIn("minecraft:gold_ingot", c)
        self.assertNotIn("11", c)

    def test_place_question_offers_positions(self):
        c = build_questions(self.STATE)["target_place"]["criteria"]
        self.assertIn("5733,232,436", c)

    def test_slot_question_only_appears_when_a_container_is_open(self):
        self.assertNotIn("target_slot", build_questions(self.STATE))
        with_container = dict(self.STATE)
        with_container["container"] = {
            "screen": "furnace",
            "slots": [{"slot": 0, "id": "minecraft:golden_helmet", "count": 1}],
            "desc": "furnace open",
        }
        self.assertIn("target_slot", build_questions(with_container))


class TestCriteriaStayGeneric(unittest.TestCase):
    """Verb criteria must not name this task's items.

    The gold farm lives in the order text. A criterion that says "throw the
    snowball" makes that verb magnetic whenever the order mentions snowballs,
    which is how `use_item` beat `move_stack` while a chest stood open.
    """

    TASK_WORDS = ("snowball", "piglin", "gold", "nugget", "ingot", "rotten flesh")

    def test_no_criterion_names_a_task_item(self):
        for verb, text in ACT_CRITERIA.items():
            for word in self.TASK_WORDS:
                self.assertNotIn(word, text.lower(), f"{verb} names {word!r}")
