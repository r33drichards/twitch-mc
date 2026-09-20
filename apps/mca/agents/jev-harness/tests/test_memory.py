"""Memory layers and the self-clock: everything the loop remembers between ticks."""
import unittest

from memory import ContainerMemory, EntityMemory, DecisionLog, TickClock


def ent(eid, etype="zombie", desc=None, **kw):
    d = {"id": eid, "type": etype, "desc": desc or f"{etype} somewhere", "dist": 5.0}
    d.update(kw)
    return d


class TestEntityMemory(unittest.TestCase):
    def test_entity_in_frame_is_not_listed_as_remembered(self):
        m = EntityMemory(clock=lambda: 100.0)
        m.observe(in_frame=[ent(1)], out_of_frame=[])
        self.assertEqual(m.seen_recently(), [])

    def test_entity_that_leaves_view_is_remembered_with_age(self):
        t = [100.0]
        m = EntityMemory(clock=lambda: t[0])
        m.observe(in_frame=[ent(1, "creeper")], out_of_frame=[])
        t[0] = 104.2
        m.observe(in_frame=[], out_of_frame=[])
        remembered = m.seen_recently()
        self.assertEqual(len(remembered), 1)
        self.assertEqual(remembered[0]["type"], "creeper")
        self.assertAlmostEqual(remembered[0]["age_s"], 4.2, places=1)

    def test_memory_expires_after_ttl(self):
        t = [100.0]
        m = EntityMemory(clock=lambda: t[0], ttl_s=30.0)
        m.observe(in_frame=[ent(1)], out_of_frame=[])
        t[0] = 131.0
        m.observe(in_frame=[], out_of_frame=[])
        self.assertEqual(m.seen_recently(), [])

    def test_disconfirmation_drops_it_immediately(self):
        # Back in view, and it is not there: the memory is wrong NOW, not in 30s.
        t = [100.0]
        m = EntityMemory(clock=lambda: t[0], ttl_s=30.0)
        m.observe(in_frame=[ent(7)], out_of_frame=[])
        t[0] = 101.0
        m.observe(in_frame=[], out_of_frame=[])
        self.assertEqual(len(m.seen_recently()), 1)
        t[0] = 102.0
        m.observe(in_frame=[], out_of_frame=[], visible_ids={7})
        self.assertEqual(m.seen_recently(), [])

    def test_recycled_entity_id_does_not_inherit_the_old_type(self):
        t = [100.0]
        m = EntityMemory(clock=lambda: t[0])
        m.observe(in_frame=[ent(3, "cow")], out_of_frame=[])
        t[0] = 101.0
        m.observe(in_frame=[ent(3, "ghast")], out_of_frame=[])
        t[0] = 102.0
        m.observe(in_frame=[], out_of_frame=[])
        self.assertEqual(m.seen_recently()[0]["type"], "ghast")

    def test_remembered_entities_carry_a_phrase(self):
        t = [100.0]
        m = EntityMemory(clock=lambda: t[0])
        m.observe(in_frame=[ent(1, "piglin", desc="piglin at (1,2,3) — ahead, 4m away, level")],
                  out_of_frame=[])
        t[0] = 103.0
        m.observe(in_frame=[], out_of_frame=[])
        self.assertIn("last seen 3.0s ago", m.seen_recently()[0]["desc"])


class TestDecisionLog(unittest.TestCase):
    def test_keeps_only_the_last_five(self):
        log = DecisionLog(clock=lambda: 100.0)
        for i in range(8):
            log.record(verb="advance", target=None, gap_ms=300, outcome={"moved_m": 0.0})
        self.assertEqual(len(log.recent()), 5)

    def test_entries_older_than_the_window_fall_out(self):
        t = [100.0]
        log = DecisionLog(clock=lambda: t[0], window_s=10.0)
        log.record(verb="jump", target=None, gap_ms=150, outcome={})
        t[0] = 111.0
        self.assertEqual(log.recent(), [])

    def test_entry_reports_age_and_outcome(self):
        t = [100.0]
        log = DecisionLog(clock=lambda: t[0])
        log.record(verb="advance", target={"id": 4}, gap_ms=380, outcome={"moved_m": 1.2})
        t[0] = 100.5
        e = log.recent()[0]
        self.assertEqual(e["verb"], "advance")
        self.assertEqual(e["gap_ms"], 380)
        self.assertAlmostEqual(e["age_s"], 0.5, places=1)
        self.assertEqual(e["outcome"]["moved_m"], 1.2)

    def test_stall_is_visible_as_data_not_as_a_verdict(self):
        log = DecisionLog(clock=lambda: 100.0)
        for _ in range(4):
            log.record(verb="advance", target=None, gap_ms=300, outcome={"moved_m": 0.0})
        phrase = log.desc()
        self.assertIn("advance", phrase)
        # The lack of progress is stated as measurement, not as a verdict.
        self.assertIn("nothing changed", phrase)
        for forbidden in ("stuck", "stalled", "danger", "should", "try"):
            self.assertNotIn(forbidden, phrase.lower())


class TestTickClock(unittest.TestCase):
    def test_reports_the_last_gap(self):
        c = TickClock()
        c.record(gap_ms=412, model_latency_ms=326, action_ms=300)
        self.assertEqual(c.snapshot()["last_gap_ms"], 412)

    def test_percentiles_over_recorded_gaps(self):
        c = TickClock()
        for g in (300, 320, 340, 360, 900):
            c.record(gap_ms=g, model_latency_ms=200, action_ms=100)
        snap = c.snapshot()
        self.assertEqual(snap["p50_gap_ms"], 340)
        self.assertEqual(snap["p90_gap_ms"], 900)

    def test_snapshot_ships_the_verb_price_list(self):
        c = TickClock(verb_duration_ms={"advance": 300, "hold": 150})
        self.assertEqual(c.snapshot()["verb_duration_ms"]["advance"], 300)

    def test_next_decision_estimate_uses_the_chosen_verb(self):
        c = TickClock(verb_duration_ms={"mine_front": 1000})
        c.record(gap_ms=400, model_latency_ms=300, action_ms=100)
        self.assertEqual(c.next_decision_in_ms("mine_front"), 1000)

    def test_clock_phrase_states_the_rate_plainly(self):
        c = TickClock()
        c.record(gap_ms=380, model_latency_ms=300, action_ms=80)
        self.assertIn("380", c.desc())


if __name__ == "__main__":
    unittest.main()


class TestDecisionLogCarriesFailures(unittest.TestCase):
    """A verb that failed must be visible next tick.

    Independent questions can disagree: `act` chose move_stack eight times in a
    row while `target_slot` answered none, so every one failed with "needs a
    slot" and the model never learned. Code cannot override the choice, so the
    failure has to reach the model as data.
    """

    def test_entry_keeps_the_result(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="move_stack", target=None, gap_ms=1200, outcome={},
                   result={"ok": False, "error": "VerbError: move_stack needs a slot"})
        self.assertFalse(log.recent()[0]["result"]["ok"])

    def test_phrase_says_what_failed_and_why(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="move_stack", target=None, gap_ms=1200, outcome={},
                   result={"ok": False, "error": "VerbError: move_stack needs a slot"})
        phrase = log.desc()
        self.assertIn("move_stack", phrase)
        self.assertIn("failed", phrase.lower())
        self.assertIn("needs a slot", phrase)

    def test_successful_entries_stay_quiet_about_it(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="attack", target={"id": 1}, gap_ms=300, outcome={"moved_m": 0.0},
                   result={"ok": True, "error": None})
        self.assertNotIn("failed", log.desc().lower())

    def test_a_missing_result_is_not_reported_as_failure(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="hold", target=None, gap_ms=150, outcome={})
        self.assertNotIn("failed", log.desc().lower())


class TestContainerMemory(unittest.TestCase):
    """What was inside the containers already opened.

    The bot opened one chest, found nothing it wanted, closed it, and opened it
    again — thirty-five times. Nothing in state recorded that it had just
    looked, and a container's contents are invisible once the screen shuts.
    """

    CHEST = {"screen": "chest", "desc": "chest open",
             "slots": [{"slot": 0, "id": "minecraft:dirt", "count": 3},
                       {"slot": 1, "id": "minecraft:iron_ingot", "count": 64}]}

    def test_an_opened_container_is_remembered_with_its_contents(self):
        m = ContainerMemory(clock=lambda: 100.0)
        m.observe({"x": 5735, "y": 232, "z": 438}, self.CHEST)
        seen = m.recent()
        self.assertEqual(len(seen), 1)
        self.assertIn("iron_ingot", seen[0]["desc"])

    def test_the_entry_ages(self):
        t = [100.0]
        m = ContainerMemory(clock=lambda: t[0])
        m.observe({"x": 1, "y": 2, "z": 3}, self.CHEST)
        t[0] = 107.0
        self.assertIn("7.0s ago", m.recent()[0]["desc"])

    def test_reopening_refreshes_rather_than_duplicates(self):
        t = [100.0]
        m = ContainerMemory(clock=lambda: t[0])
        m.observe({"x": 1, "y": 2, "z": 3}, self.CHEST)
        t[0] = 110.0
        m.observe({"x": 1, "y": 2, "z": 3}, self.CHEST)
        self.assertEqual(len(m.recent()), 1)
        self.assertIn("0.0s ago", m.recent()[0]["desc"])

    def test_old_entries_fall_out(self):
        t = [100.0]
        m = ContainerMemory(clock=lambda: t[0], ttl_s=120.0)
        m.observe({"x": 1, "y": 2, "z": 3}, self.CHEST)
        t[0] = 300.0
        self.assertEqual(m.recent(), [])

    def test_nothing_is_recorded_without_a_position(self):
        m = ContainerMemory(clock=lambda: 100.0)
        m.observe(None, self.CHEST)
        self.assertEqual(m.recent(), [])


class TestNothingChangedIsSaid(unittest.TestCase):
    """An action with no effect should read as one.

    `slot_2 0.7s ago` looks identical whether it did something or not, and the
    model kept choosing it. The outcome already held the evidence; the phrase
    now says it.
    """

    NOTHING = {"moved_m": 0.0, "health_delta": 0.0, "inventory_change": {}}

    def test_a_no_op_says_nothing_changed(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="slot_2", target=None, gap_ms=700, outcome=self.NOTHING,
                   result={"ok": True, "error": None})
        self.assertIn("nothing changed", log.desc())

    def test_movement_is_not_called_nothing(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="advance", target=None, gap_ms=300,
                   outcome={"moved_m": 1.5, "health_delta": 0.0, "inventory_change": {}},
                   result={"ok": True, "error": None})
        self.assertNotIn("nothing changed", log.desc())

    def test_an_inventory_change_is_not_called_nothing(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="use_item", target=None, gap_ms=300,
                   outcome={"moved_m": 0.0, "health_delta": 0.0,
                            "inventory_change": {"snowball": -1}},
                   result={"ok": True, "error": None})
        self.assertNotIn("nothing changed", log.desc())

    def test_a_failure_still_reads_as_a_failure(self):
        log = DecisionLog(clock=lambda: 100.0)
        log.record(verb="move_stack", target=None, gap_ms=300, outcome=self.NOTHING,
                   result={"ok": False, "error": "needs a slot"})
        self.assertIn("FAILED", log.desc())
