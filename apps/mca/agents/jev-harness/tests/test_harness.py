"""The loop's own logic: resolving a choice into a target, and measuring outcomes."""
import unittest

from harness import resolve_target, measure_outcome, target_for


STATE = {
    "self": {"x": 100.0, "y": 64.0, "z": 100.0, "health": 20.0},
    "in_frame": [{"id": 11, "type": "zombified_piglin", "x": 104.0, "y": 64.0, "z": 100.0}],
    "out_of_frame": [{"id": 62, "type": "ghast", "x": 140.0, "y": 70.0, "z": 100.0}],
    "stations": {"near": [{"id": "furnace", "x": 5733, "y": 232, "z": 436,
                           "desc": "furnace at (5733,232,436) — ahead, 4.0m, in reach"}],
                 "more": {}, "desc": "stations nearby"},
}


class TestResolveTarget(unittest.TestCase):
    def test_entity_id_resolves_to_that_entity(self):
        t = resolve_target(STATE, "11")
        self.assertEqual(t["id"], 11)
        self.assertEqual(t["type"], "zombified_piglin")

    def test_remembered_entity_resolves_too(self):
        self.assertEqual(resolve_target(STATE, "62")["type"], "ghast")

    def test_none_resolves_to_nothing(self):
        self.assertIsNone(resolve_target(STATE, "none"))

    def test_coordinate_key_resolves_to_a_position(self):
        t = resolve_target(STATE, "5733,232,436")
        self.assertEqual((t["x"], t["y"], t["z"]), (5733, 232, 436))

    def test_unknown_choice_resolves_to_nothing_rather_than_guessing(self):
        # The entity may have died between the snapshot and the answer.
        self.assertIsNone(resolve_target(STATE, "999"))


class TestMeasureOutcome(unittest.TestCase):
    def test_reports_distance_moved(self):
        after = {"self": {"x": 103.0, "y": 64.0, "z": 104.0, "health": 20.0}}
        self.assertAlmostEqual(measure_outcome(STATE, after)["moved_m"], 5.0, places=1)

    def test_reports_health_change(self):
        after = {"self": {"x": 100.0, "y": 64.0, "z": 100.0, "health": 14.5}}
        self.assertAlmostEqual(measure_outcome(STATE, after)["health_delta"], -5.5, places=1)

    def test_survives_a_missing_snapshot(self):
        # A discarded or failed tick must not crash the loop.
        self.assertEqual(measure_outcome(STATE, None), {})


if __name__ == "__main__":
    unittest.main()


class TestResolveItemTargets(unittest.TestCase):
    def test_item_id_resolves_to_an_item_target(self):
        t = resolve_target(STATE, "minecraft:snowball")
        self.assertEqual(t, {"item": "minecraft:snowball"})

    def test_item_target_is_what_equip_and_craft_both_take(self):
        # dispatch's equip takes {"item": ...} and craft takes {"item": ...}.
        self.assertIn("item", resolve_target(STATE, "minecraft:gold_ingot"))


class TestTargetForVerb(unittest.TestCase):
    """Each verb consumes exactly one speculative answer; no tie-breaks."""

    ANSWERS = {
        "target_entity": {"choice": "11"},
        "target_place": {"choice": "5733,232,436"},
        "target_item": {"choice": "minecraft:snowball"},
        "target_slot": {"choice": "0"},
    }

    def test_attack_uses_the_entity_answer(self):
        self.assertEqual(target_for("attack", self.ANSWERS, STATE)["id"], 11)

    def test_open_uses_the_place_answer(self):
        t = target_for("open", self.ANSWERS, STATE)
        self.assertEqual((t["x"], t["y"], t["z"]), (5733, 232, 436))

    def test_equip_uses_the_item_answer(self):
        self.assertEqual(target_for("equip", self.ANSWERS, STATE)["item"], "minecraft:snowball")

    def test_craft_uses_the_item_answer(self):
        self.assertEqual(target_for("craft", self.ANSWERS, STATE)["item"], "minecraft:snowball")

    def test_move_stack_uses_the_slot_answer(self):
        self.assertEqual(target_for("move_stack", self.ANSWERS, STATE)["slot"], 0)

    def test_verbs_that_need_nothing_get_nothing(self):
        for verb in ("hold", "done", "jump", "close"):
            self.assertIsNone(target_for(verb, self.ANSWERS, STATE))

    def test_unanswered_target_is_not_invented(self):
        answers = {"target_place": {"choice": "none"}}
        self.assertIsNone(target_for("open", answers, STATE))


class TestItemNamespaces(unittest.TestCase):
    """state.py strips `minecraft:` from vanilla ids; the mod does not.

    The model answers with what it was shown ("netherite_sword"), while
    dispatch matches against api:inventoryJson(), which returns the full id.
    Resolving has to put the namespace back, or equip and craft always fail.
    """

    STATE = {
        "self": {"x": 0, "y": 0, "z": 0},
        "in_frame": [], "out_of_frame": [],
        "inventory": {"counts": {"netherite_sword": 1, "rotten_flesh": 3}},
        "craftable": [{"result": "gold_ingot", "count": 1}],
        "stations": {"near": [], "more": {}, "desc": ""},
    }

    def test_bare_vanilla_name_regains_its_namespace(self):
        self.assertEqual(resolve_target(self.STATE, "netherite_sword"),
                         {"item": "minecraft:netherite_sword"})

    def test_craftable_result_resolves_too(self):
        self.assertEqual(resolve_target(self.STATE, "gold_ingot"),
                         {"item": "minecraft:gold_ingot"})

    def test_explicit_namespace_is_left_alone(self):
        self.assertEqual(resolve_target(self.STATE, "modpack:widget"),
                         {"item": "modpack:widget"})

    def test_a_word_that_is_not_an_item_resolves_to_nothing(self):
        self.assertIsNone(resolve_target(self.STATE, "somewhere"))


class TestOutcomeWatchesTheTarget(unittest.TestCase):
    """An action against a creature should report what happened to it.

    Thirty ticks of `attack` on a piglin 8.7m away all reported ok, because the
    entity existed and the swing happened; the server simply ignored a swing
    from out of reach. Nothing in state ever said the target was unharmed, so
    there was nothing to learn from. Health is observation, not judgement.
    """

    BEFORE = {"self": {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20.0},
              "in_frame": [{"id": 7, "type": "zombified_piglin", "health": 20.0}],
              "out_of_frame": []}

    def test_target_health_change_is_reported(self):
        after = {"self": {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20.0},
                 "in_frame": [{"id": 7, "type": "zombified_piglin", "health": 12.5}],
                 "out_of_frame": []}
        out = measure_outcome(self.BEFORE, after, target={"id": 7})
        self.assertAlmostEqual(out["target_health_delta"], -7.5, places=1)

    def test_an_untouched_target_reports_zero_not_silence(self):
        after = {"self": {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20.0},
                 "in_frame": [{"id": 7, "type": "zombified_piglin", "health": 20.0}],
                 "out_of_frame": []}
        out = measure_outcome(self.BEFORE, after, target={"id": 7})
        self.assertEqual(out["target_health_delta"], 0.0)

    def test_a_target_that_vanished_is_not_reported_as_unharmed(self):
        after = {"self": {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20.0},
                 "in_frame": [], "out_of_frame": []}
        out = measure_outcome(self.BEFORE, after, target={"id": 7})
        self.assertNotIn("target_health_delta", out)

    def test_no_target_means_no_target_field(self):
        out = measure_outcome(self.BEFORE, self.BEFORE, target=None)
        self.assertNotIn("target_health_delta", out)


class TestOutcomeNoticesInventoryChange(unittest.TestCase):
    """Whether the action changed what you carry.

    Thirty right-clicks on a chestplate produced no movement, no damage and no
    inventory change, and nothing in state said so — so the same answer came
    back every tick. Throwing a snowball decrements it; using a chestplate
    moves it; doing nothing changes nothing, and that is the useful signal.
    """

    BEFORE = {"self": {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20.0},
              "inventory": {"counts": {"snowball": 65, "torch": 64}}}

    def test_an_item_that_was_spent_is_reported(self):
        after = {"self": self.BEFORE["self"],
                 "inventory": {"counts": {"snowball": 64, "torch": 64}}}
        out = measure_outcome(self.BEFORE, after)
        self.assertEqual(out["inventory_change"], {"snowball": -1})

    def test_no_change_is_reported_as_nothing_changed(self):
        out = measure_outcome(self.BEFORE, dict(self.BEFORE))
        self.assertEqual(out["inventory_change"], {})

    def test_a_new_item_shows_up(self):
        after = {"self": self.BEFORE["self"],
                 "inventory": {"counts": {"snowball": 65, "torch": 64, "gold_ingot": 1}}}
        self.assertEqual(measure_outcome(self.BEFORE, after)["inventory_change"],
                         {"gold_ingot": 1})
