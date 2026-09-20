"""Bounded execution against a recording fake bridge.

Nothing here talks to Minecraft. The fake records every call, so the tests
assert on the call sequence itself: that release-all precedes every verb,
that a bounded press is press-sleep-release, and that no path leaves a key
latched down.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import textwrap
import unittest

import dispatch
from dispatch import (AIM_STEP_DEG, LATCHING_KEYS, TURN_STEP_DEG,
                      VERB_DURATION_MS, Dispatcher)

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeBridge:
    """Records calls; answers with canned results."""

    def __init__(self, rpc_results=None, eval_result=True):
        self.calls = []
        self.rpc_results = rpc_results or {}
        self.eval_result = eval_result

    def rpc(self, method, params=None, timeout=10.0):
        self.calls.append(("rpc", method, dict(params or {})))
        result = self.rpc_results.get(method, {"ok": True})
        if isinstance(result, Exception):
            raise result
        return result

    def eval(self, code, timeout_ms=500):
        self.calls.append(("eval", code, timeout_ms))
        if isinstance(self.eval_result, Exception):
            raise self.eval_result
        return self.eval_result

    # -- views over the recording ------------------------------------------
    def methods(self):
        return [c[1] for c in self.calls if c[0] == "rpc"]

    def key_calls(self):
        return [(c[2]["key"], c[2].get("action", "press"))
                for c in self.calls if c[0] == "rpc" and c[1] == "player.press_key"]

    def held_keys(self):
        """Keys still latched down, replaying the recorded press/release calls."""
        state = {}
        for key, action in self.key_calls():
            state[key] = (action == "press")
        return sorted(k for k, down in state.items() if down)

    def verb_calls(self):
        """The verb's own calls: what sits between the two release-all fences.

        execute() brackets every verb with a full release-all, so the calls
        that belong to the verb itself are the ones in between.
        """
        n = len(LATCHING_KEYS)
        return self.calls[n:-n] if len(self.calls) >= 2 * n else []

    def verb_key_calls(self):
        return [(c[2]["key"], c[2].get("action", "press"))
                for c in self.verb_calls()
                if c[0] == "rpc" and c[1] == "player.press_key"]


class FakeClock:
    """Time only moves when something sleeps."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


PLAYER_AT_ORIGIN = {"player.state": {"inWorld": True,
                                     "pos": {"x": 0.0, "y": 64.0, "z": 0.0},
                                     "blockPos": {"x": 0, "y": 64, "z": 0},
                                     "rot": {"yaw": 0.0, "pitch": 0.0}}}
BLOCK_HIT = {"world.raycast": {"type": "BLOCK", "x": 10, "y": 64, "z": -3,
                               "side": "north", "id": "minecraft:netherrack"}}
MISS = {"world.raycast": {"type": "MISS"}}


def make(bridge=None, **kw):
    clock = FakeClock()
    bridge = bridge or FakeBridge()
    d = Dispatcher(bridge, sleep=clock.sleep, clock=clock.monotonic, **kw)
    return d, bridge, clock


ALL_VERBS = ["advance", "retreat", "jump", "mine_front", "use_item_hold",
             "aim_higher", "aim_lower", "turn_left", "turn_right",
             "strafe_left", "strafe_right", "hotbar_next", "hotbar_prev",
             "place_block", "attack", "use_item", "hold", "done",
             "equip", "craft", "open", "move_stack", "close"]


# --------------------------------------------------------------------------
# the thing that must never fail: no key survives a call
# --------------------------------------------------------------------------

class TestKeysAreNeverLeftHeld(unittest.TestCase):
    def test_every_verb_starts_by_releasing_every_latching_key(self):
        for verb in ALL_VERBS:
            with self.subTest(verb=verb):
                d, bridge, _ = make(FakeBridge({**PLAYER_AT_ORIGIN, **BLOCK_HIT}))
                d.execute(verb, {"id": 41, "x": 10, "y": 64, "z": -3})
                opening = bridge.calls[:len(LATCHING_KEYS)]
                self.assertEqual(
                    [(c[1], c[2]["key"], c[2]["action"]) for c in opening],
                    [("player.press_key", k, "release") for k in LATCHING_KEYS])

    def test_no_verb_leaves_a_key_held(self):
        for verb in ALL_VERBS:
            with self.subTest(verb=verb):
                d, bridge, _ = make(FakeBridge({**PLAYER_AT_ORIGIN, **BLOCK_HIT}))
                d.execute(verb, {"id": 41, "x": 10, "y": 64, "z": -3})
                self.assertEqual(bridge.held_keys(), [])

    def test_an_unknown_verb_still_releases(self):
        d, bridge, _ = make()
        out = d.execute("moonwalk")
        self.assertFalse(out["ok"])
        self.assertIn("moonwalk", out["error"])
        self.assertEqual(bridge.held_keys(), [])

    def test_a_crash_mid_press_still_releases(self):
        class Boom(Exception):
            pass

        clock = FakeClock()

        def exploding_sleep(_seconds):
            raise Boom("the loop died holding forward")

        bridge = FakeBridge()
        d = Dispatcher(bridge, sleep=exploding_sleep, clock=clock.monotonic)
        out = d.execute("advance")
        self.assertFalse(out["ok"])
        self.assertIn("Boom", out["error"])
        self.assertIn(("forward", "press"), bridge.key_calls())
        self.assertEqual(bridge.held_keys(), [])

    def test_a_bridge_error_is_data_not_an_exception(self):
        bridge = FakeBridge({"world.raycast": ConnectionError("bridge down")})
        d, _, _ = make(bridge)
        out = d.execute("mine_front")
        self.assertFalse(out["ok"])
        self.assertIn("bridge down", out["error"])
        self.assertEqual(out["verb"], "mine_front")

    def test_release_all_touches_every_latchable_key(self):
        d, bridge, _ = make()
        d.release_all()
        self.assertEqual(bridge.key_calls(), [(k, "release") for k in LATCHING_KEYS])

    def test_release_all_survives_a_dead_bridge(self):
        bridge = FakeBridge({"player.press_key": ConnectionError("gone")})
        d = Dispatcher(bridge, sleep=lambda s: None)
        d.release_all()                                  # must not raise
        self.assertEqual(len(bridge.key_calls()), len(LATCHING_KEYS))

    def test_module_panic_release_covers_every_live_dispatcher(self):
        d1, b1, _ = make()
        d2, b2, _ = make()
        b1.calls.clear()
        b2.calls.clear()
        dispatch._release_everything()
        for b in (b1, b2):
            self.assertEqual(b.key_calls(), [(k, "release") for k in LATCHING_KEYS])

    def test_signal_handler_releases_then_chains(self):
        import signal as signal_mod
        d, bridge, _ = make()
        bridge.calls.clear()
        chained = []
        dispatch._PREV_HANDLERS[signal_mod.SIGINT] = lambda *a: chained.append(a)
        try:
            dispatch._on_signal(signal_mod.SIGINT, None)
        finally:
            dispatch._PREV_HANDLERS.pop(signal_mod.SIGINT, None)
        self.assertEqual(bridge.key_calls(), [(k, "release") for k in LATCHING_KEYS])
        self.assertEqual(len(chained), 1)


class TestKeysAreReleasedWhenTheProcessDies(unittest.TestCase):
    """The failure the design calls the worst: a key outliving the process."""

    SCRIPT = textwrap.dedent("""
        import sys, os, time, signal
        sys.path.insert(0, %(harness)r)
        import dispatch

        class Recorder:
            def rpc(self, method, params=None, timeout=10.0):
                with open(%(log)r, "a") as fh:
                    fh.write("%%s %%s\\n" %% (method, (params or {}).get("action", "")))
                return {"ok": True}
            def eval(self, code, timeout_ms=500):
                return True

        d = dispatch.Dispatcher(Recorder(), sleep=lambda s: None)
        d.bridge.rpc("player.press_key", {"key": "forward", "action": "press"})
        %(tail)s
    """)

    @staticmethod
    def _read(path):
        try:
            with open(path) as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def _run(self, tail, signal_to_send=None):
        log = os.path.join(tempfile.mkdtemp(), "keys.log")
        src = self.SCRIPT % {"harness": HARNESS_DIR, "log": log, "tail": tail}
        # The SIGINT child dies of KeyboardInterrupt by design; its traceback
        # is not this test suite's output.
        proc = subprocess.Popen([sys.executable, "-c", src], stderr=subprocess.DEVNULL)
        if signal_to_send is not None:
            deadline = time.time() + 5.0
            while "press" not in self._read(log):
                if time.time() > deadline:
                    proc.kill()
                    self.fail("child never pressed the key")
                time.sleep(0.02)
            proc.send_signal(signal_to_send)
        proc.wait(timeout=10)
        return self._read(log).splitlines()

    def test_normal_exit_releases_via_atexit(self):
        lines = self._run("")
        self.assertIn("player.press_key press", lines)
        self.assertEqual(lines.count("player.press_key release"), len(LATCHING_KEYS))
        self.assertEqual(lines[-1], "player.press_key release")

    def test_sigterm_releases(self):
        import signal as signal_mod
        lines = self._run("time.sleep(30)", signal_mod.SIGTERM)
        self.assertGreaterEqual(lines.count("player.press_key release"), len(LATCHING_KEYS))
        self.assertEqual(lines[-1], "player.press_key release")

    def test_sigint_releases(self):
        import signal as signal_mod
        lines = self._run("time.sleep(30)", signal_mod.SIGINT)
        self.assertGreaterEqual(lines.count("player.press_key release"), len(LATCHING_KEYS))
        self.assertEqual(lines[-1], "player.press_key release")


# --------------------------------------------------------------------------
# the verb table
# --------------------------------------------------------------------------

class TestVerbTable(unittest.TestCase):
    """Handlers and durations stay in lockstep.

    The table is partly generated now (slot_1..slot_9), so pinning a literal
    dict tested the literal rather than the property. What matters is that every
    verb can run, every verb has an honest duration, and the durations the
    design names have not drifted.
    """

    def test_every_verb_has_a_handler_and_a_duration(self):
        self.assertEqual(sorted(Dispatcher.VERBS), sorted(VERB_DURATION_MS))

    def test_durations_are_real_milliseconds(self):
        for verb, ms in VERB_DURATION_MS.items():
            self.assertIsInstance(ms, int, verb)
            self.assertGreaterEqual(ms, 0, verb)

    def test_the_durations_the_design_names_have_not_drifted(self):
        for verb, ms in (("advance", 300), ("retreat", 300), ("jump", 150),
                         ("mine_front", 1000), ("use_item_hold", 1700),
                         ("move_stack", 300), ("hold", 150), ("done", 150)):
            self.assertEqual(VERB_DURATION_MS[verb], ms, verb)

    def test_the_whole_keyboard_is_runnable(self):
        from questions import KEYBOARD_VERBS
        for verb in KEYBOARD_VERBS:
            self.assertIn(verb, Dispatcher.VERBS, verb)

class TestAdvance(unittest.TestCase):
    def test_walks_forward_and_releases_without_steering(self):
        # Steering belongs to turn_left / turn_right, as at a keyboard.
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("advance")
        keys = [(c[2].get("key"), c[2].get("action")) for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.press_key"]
        self.assertIn(("forward", "press"), keys)
        self.assertIn(("forward", "release"), keys)
        self.assertEqual([c for c in bridge.calls
                          if c[0] == "rpc" and c[1] == "player.set_rotation"], [])

    def test_without_a_target_it_walks_where_it_already_faces(self):
        d, bridge, clock = make()
        d.execute("advance", None)
        self.assertNotIn("player.set_rotation", bridge.methods())
        self.assertEqual(bridge.verb_key_calls(), [("forward", "press"), ("forward", "release")])
        self.assertEqual(clock.sleeps, [0.3])

    def assertYaw(self, got, want):
        self.assertAlmostEqual(float(got), want, places=3)


class TestSimpleVerbs(unittest.TestCase):
    def test_retreat_presses_back_and_never_rotates(self):
        d, bridge, clock = make()
        d.execute("retreat", {"x": 5.0, "y": 64.0, "z": 5.0})
        self.assertNotIn("player.set_rotation", bridge.methods())
        self.assertEqual(bridge.verb_key_calls(), [("back", "press"), ("back", "release")])
        self.assertEqual(clock.sleeps, [0.3])

    def test_jump_is_bounded_at_150ms(self):
        d, bridge, clock = make()
        out = d.execute("jump")
        self.assertEqual(bridge.verb_key_calls(), [("jump", "press"), ("jump", "release")])
        self.assertEqual(clock.sleeps, [0.15])
        self.assertEqual(out["duration_ms"], 150)

    def test_hold_sleeps_and_touches_nothing_else(self):
        d, bridge, clock = make()
        d.execute("hold")
        self.assertEqual(bridge.verb_calls(), [])
        self.assertEqual(clock.sleeps, [0.15])

    def test_done_sleeps_and_touches_nothing_else(self):
        d, bridge, clock = make()
        d.execute("done")
        self.assertEqual(bridge.verb_calls(), [])
        self.assertEqual(clock.sleeps, [0.15])


class TestMineFront(unittest.TestCase):
    def test_mines_the_raycast_hit(self):
        d, bridge, clock = make(FakeBridge({**BLOCK_HIT}))
        out = d.execute("mine_front")
        self.assertTrue(out["ok"])
        methods = [c[1] for c in bridge.verb_calls()]
        self.assertEqual(methods[0], "world.raycast")
        self.assertTrue(all(m == "world.mine_block" for m in methods[1:]))
        for call in bridge.calls:
            if call[1] == "world.mine_block":
                self.assertEqual(call[2], {"x": 10, "y": 64, "z": -3})

    def test_never_runs_longer_than_one_second(self):
        d, bridge, clock = make(FakeBridge({**BLOCK_HIT}))
        out = d.execute("mine_front")
        self.assertLessEqual(sum(clock.sleeps), 1.0)
        self.assertLessEqual(out["duration_ms"], VERB_DURATION_MS["mine_front"])

    def test_keeps_swinging_rather_than_one_tick(self):
        d, bridge, _ = make(FakeBridge({**BLOCK_HIT}))
        d.execute("mine_front")
        swings = [c for c in bridge.calls if c[1] == "world.mine_block"]
        self.assertGreater(len(swings), 1)

    def test_a_miss_is_reported_and_nothing_is_mined(self):
        d, bridge, _ = make(FakeBridge({**MISS}))
        out = d.execute("mine_front")
        self.assertFalse(out["ok"])
        self.assertIn("MISS", out["error"])
        self.assertNotIn("world.mine_block", bridge.methods())


class TestPlaceBlock(unittest.TestCase):
    def test_places_at_the_target_coordinates(self):
        d, bridge, _ = make(FakeBridge({**BLOCK_HIT}))
        out = d.execute("place_block", {"x": 12.4, "y": 64.0, "z": -2.9})
        self.assertTrue(out["ok"])
        self.assertEqual([c for c in bridge.calls if c[1] == "world.place_block"][0][2],
                         {"x": 12, "y": 64, "z": -3})

    def test_falls_back_to_the_block_being_looked_at(self):
        d, bridge, _ = make(FakeBridge({**BLOCK_HIT}))
        d.execute("place_block", None)
        self.assertEqual([c[1] for c in bridge.verb_calls()],
                         ["world.raycast", "world.place_block"])
        self.assertEqual(bridge.verb_calls()[-1][2], {"x": 10, "y": 64, "z": -3})

    def test_nothing_to_place_against_is_reported(self):
        d, bridge, _ = make(FakeBridge({**MISS}))
        out = d.execute("place_block", None)
        self.assertFalse(out["ok"])
        self.assertNotIn("world.place_block", bridge.methods())

    def test_is_instant(self):
        d, _, clock = make(FakeBridge({**BLOCK_HIT}))
        out = d.execute("place_block", {"x": 1, "y": 2, "z": 3})
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(out["duration_ms"], 0)


class TestAttackAndUse(unittest.TestCase):
    def test_attack_evals_attack_entity_with_the_chosen_id(self):
        d, bridge, _ = make()
        out = d.execute("attack", {"id": 41, "type": "zombie"})
        self.assertTrue(out["ok"])
        evals = [c for c in bridge.calls if c[0] == "eval"]
        self.assertEqual(len(evals), 1)
        self.assertIn("api:attackEntity(41)", evals[0][1])

    def test_attack_rejects_a_non_numeric_id(self):
        d, bridge, _ = make()
        out = d.execute("attack", {"id": "41); api:chat('pwned'"})
        self.assertFalse(out["ok"])
        self.assertEqual([c for c in bridge.calls if c[0] == "eval"], [])

    def test_use_item_evals_use_item(self):
        d, bridge, _ = make()
        out = d.execute("use_item")
        self.assertTrue(out["ok"])
        self.assertIn("api:useItem()", [c[1] for c in bridge.calls if c[0] == "eval"][0])


# --------------------------------------------------------------------------
# held use: eating takes ~1.6s of held right-click, a click does nothing
# --------------------------------------------------------------------------

class TestUseItemHold(unittest.TestCase):
    def test_a_hold_presses_use_sleeps_and_releases(self):
        d, bridge, clock = make()
        out = d.execute("use_item", {"hold_ms": 1700})
        self.assertTrue(out["ok"])
        self.assertEqual(bridge.verb_key_calls(), [("use", "press"), ("use", "release")])
        self.assertEqual(clock.sleeps, [1.7])
        self.assertEqual(out["duration_ms"], 1700)

    def test_a_hold_does_not_also_eval_use_item(self):
        d, bridge, _ = make()
        d.execute("use_item", {"hold_ms": 1700})
        self.assertEqual([c for c in bridge.calls if c[0] == "eval"], [])

    def test_no_hold_keeps_the_instantaneous_behaviour(self):
        for target in (None, {}, {"hold_ms": None}, {"hold_ms": 0}):
            with self.subTest(target=target):
                d, bridge, clock = make()
                out = d.execute("use_item", target)
                self.assertTrue(out["ok"])
                self.assertIn("api:useItem()",
                              [c[1] for c in bridge.calls if c[0] == "eval"][0])
                self.assertEqual(bridge.verb_key_calls(), [])
                self.assertEqual(clock.sleeps, [])
                self.assertEqual(out["duration_ms"], 0)

    def test_a_non_numeric_hold_is_reported_and_presses_nothing(self):
        d, bridge, _ = make()
        out = d.execute("use_item", {"hold_ms": "a while"})
        self.assertFalse(out["ok"])
        self.assertIn("hold_ms", out["error"])
        self.assertEqual(bridge.verb_key_calls(), [])
        self.assertEqual([c for c in bridge.calls if c[0] == "eval"], [])

    def test_a_negative_hold_is_reported(self):
        d, bridge, _ = make()
        out = d.execute("use_item", {"hold_ms": -5})
        self.assertFalse(out["ok"])
        self.assertIn("hold_ms", out["error"])
        self.assertEqual(bridge.verb_key_calls(), [])

    def test_a_crash_mid_hold_still_releases_use(self):
        """The eating equivalent of the advance crash test.

        A 1.6s hold is the longest the player ever latches a key. If the sleep
        dies in the middle of it, `use` must not survive the call.
        """
        class Boom(Exception):
            pass

        def exploding_sleep(_seconds):
            raise Boom("the loop died holding use")

        clock = FakeClock()
        bridge = FakeBridge()
        d = Dispatcher(bridge, sleep=exploding_sleep, clock=clock.monotonic)
        out = d.execute("use_item", {"hold_ms": 1700})
        self.assertFalse(out["ok"])
        self.assertIn("Boom", out["error"])
        self.assertIn(("use", "press"), bridge.key_calls())
        self.assertEqual(bridge.held_keys(), [])
        # released by the context manager, then again by execute()'s finally
        self.assertGreaterEqual(
            len([k for k in bridge.key_calls() if k == ("use", "release")]), 2)


# --------------------------------------------------------------------------
# equip
# --------------------------------------------------------------------------

def inventory(*stacks):
    """An inventoryJson payload: (slot, id) pairs as the mod serializes them."""
    return json.dumps([{"slot": s, "id": i, "count": 1} for s, i in stacks])


class TestEquip(unittest.TestCase):
    def test_a_slot_is_selected_directly(self):
        d, bridge, clock = make()
        out = d.execute("equip", {"slot": 3})
        self.assertTrue(out["ok"])
        self.assertEqual([c[1] for c in bridge.verb_calls()], ["player.set_hotbar_slot"])
        self.assertEqual(bridge.verb_calls()[0][2], {"slot": 3})
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(out["duration_ms"], 0)

    def test_a_slot_outside_the_hotbar_is_reported(self):
        for slot in (-1, 9, 36):
            with self.subTest(slot=slot):
                d, bridge, _ = make()
                out = d.execute("equip", {"slot": slot})
                self.assertFalse(out["ok"])
                self.assertIn("slot", out["error"])
                self.assertNotIn("player.set_hotbar_slot", bridge.methods())

    def test_a_non_numeric_slot_is_reported(self):
        d, bridge, _ = make()
        out = d.execute("equip", {"slot": "left hand"})
        self.assertFalse(out["ok"])
        self.assertNotIn("player.set_hotbar_slot", bridge.methods())

    def test_an_item_is_resolved_to_the_hotbar_slot_holding_it(self):
        bridge = FakeBridge()
        bridge.eval_result = inventory((0, "minecraft:golden_sword"),
                                       (4, "minecraft:snowball"),
                                       (20, "minecraft:rotten_flesh"))
        d, _, _ = make(bridge)
        out = d.execute("equip", {"item": "minecraft:snowball"})
        self.assertTrue(out["ok"])
        self.assertIn("api:inventoryJson()",
                      [c[1] for c in bridge.calls if c[0] == "eval"][0])
        self.assertEqual([c for c in bridge.calls
                          if c[1] == "player.set_hotbar_slot"][0][2], {"slot": 4})

    def test_an_item_only_outside_the_hotbar_is_a_reported_failure(self):
        bridge = FakeBridge()
        bridge.eval_result = inventory((20, "minecraft:snowball"))
        d, _, _ = make(bridge)
        out = d.execute("equip", {"item": "minecraft:snowball"})
        self.assertFalse(out["ok"])
        self.assertIn("hotbar", out["error"])
        self.assertIn("snowball", out["error"])
        self.assertNotIn("player.set_hotbar_slot", bridge.methods())

    def test_an_item_the_player_does_not_carry_is_a_reported_failure(self):
        bridge = FakeBridge()
        bridge.eval_result = inventory((0, "minecraft:golden_sword"))
        d, _, _ = make(bridge)
        out = d.execute("equip", {"item": "minecraft:snowball"})
        self.assertFalse(out["ok"])
        self.assertIn("snowball", out["error"])
        self.assertNotIn("player.set_hotbar_slot", bridge.methods())

    def test_a_short_id_matches_the_vanilla_namespace(self):
        """State ships `held: "snowball"`, so the model may well answer that."""
        bridge = FakeBridge()
        bridge.eval_result = inventory((6, "minecraft:snowball"))
        d, _, _ = make(bridge)
        out = d.execute("equip", {"item": "snowball"})
        self.assertTrue(out["ok"])
        self.assertEqual([c for c in bridge.calls
                          if c[1] == "player.set_hotbar_slot"][0][2], {"slot": 6})

    def test_an_already_parsed_inventory_is_tolerated(self):
        bridge = FakeBridge()
        bridge.eval_result = [{"slot": 2, "id": "minecraft:snowball", "count": 16}]
        d, _, _ = make(bridge)
        out = d.execute("equip", {"item": "minecraft:snowball"})
        self.assertTrue(out["ok"])
        self.assertEqual([c for c in bridge.calls
                          if c[1] == "player.set_hotbar_slot"][0][2], {"slot": 2})

    def test_the_lowest_hotbar_slot_wins_when_an_item_is_held_twice(self):
        bridge = FakeBridge()
        bridge.eval_result = inventory((7, "minecraft:snowball"),
                                       (2, "minecraft:snowball"))
        d, _, _ = make(bridge)
        d.execute("equip", {"item": "minecraft:snowball"})
        self.assertEqual([c for c in bridge.calls
                          if c[1] == "player.set_hotbar_slot"][0][2], {"slot": 2})

    def test_a_slot_wins_over_an_item_and_skips_the_inventory_read(self):
        bridge = FakeBridge()
        bridge.eval_result = inventory((4, "minecraft:snowball"))
        d, _, _ = make(bridge)
        out = d.execute("equip", {"slot": 1, "item": "minecraft:snowball"})
        self.assertTrue(out["ok"])
        self.assertEqual([c for c in bridge.calls if c[0] == "eval"], [])
        self.assertEqual([c for c in bridge.calls
                          if c[1] == "player.set_hotbar_slot"][0][2], {"slot": 1})

    def test_neither_a_slot_nor_an_item_is_reported(self):
        d, bridge, _ = make()
        out = d.execute("equip", None)
        self.assertFalse(out["ok"])
        self.assertIn("slot", out["error"])
        self.assertIn("item", out["error"])
        self.assertEqual(bridge.verb_calls(), [])


# --------------------------------------------------------------------------
# craft
# --------------------------------------------------------------------------

class TestCraft(unittest.TestCase):
    def test_passes_the_item_through_and_nothing_else(self):
        d, bridge, clock = make()
        out = d.execute("craft", {"item": "minecraft:gold_ingot"})
        self.assertTrue(out["ok"])
        self.assertEqual([c[1] for c in bridge.verb_calls()], ["craft.item"])
        self.assertEqual(bridge.verb_calls()[0][2], {"item": "minecraft:gold_ingot"})
        self.assertEqual(clock.sleeps, [])

    def test_use_max_is_passed_through(self):
        d, bridge, _ = make()
        d.execute("craft", {"item": "minecraft:gold_block", "use_max": True})
        self.assertEqual(bridge.verb_calls()[0][2],
                         {"item": "minecraft:gold_block", "use_max": True})

    def test_count_is_not_forwarded(self):
        """One craft.item pass per verb keeps the duration table honest.

        craft.item sleeps 500ms per iteration on the client thread, so a
        count of N would cost N times what `tick.verb_duration_ms` promises.
        `use_max` already yields a whole stack in a single pass.
        """
        d, bridge, _ = make()
        d.execute("craft", {"item": "minecraft:gold_ingot", "count": 16})
        self.assertEqual(bridge.verb_calls()[0][2], {"item": "minecraft:gold_ingot"})

    def test_table_coordinates_are_passed_through_as_ints(self):
        d, bridge, _ = make()
        d.execute("craft", {"item": "minecraft:gold_ingot", "use_max": True,
                            "x": 118.7, "y": 64.0, "z": -44.2})
        self.assertEqual(bridge.verb_calls()[0][2],
                         {"item": "minecraft:gold_ingot", "use_max": True,
                          "x": 118, "y": 64, "z": -45})

    def test_partial_coordinates_are_not_sent(self):
        """craft.item only opens a table when it has all three."""
        d, bridge, _ = make()
        d.execute("craft", {"item": "minecraft:gold_ingot", "x": 118, "z": -44})
        self.assertEqual(bridge.verb_calls()[0][2], {"item": "minecraft:gold_ingot"})

    def test_without_an_item_it_is_a_reported_failure(self):
        d, bridge, _ = make()
        out = d.execute("craft", {"use_max": True})
        self.assertFalse(out["ok"])
        self.assertIn("item", out["error"])
        self.assertNotIn("craft.item", bridge.methods())

    def test_a_bridge_failure_is_data(self):
        bridge = FakeBridge({"craft.item": RuntimeError("missing_ingredients_for:x")})
        d, _, _ = make(bridge)
        out = d.execute("craft", {"item": "minecraft:gold_ingot"})
        self.assertFalse(out["ok"])
        self.assertIn("missing_ingredients", out["error"])


# --------------------------------------------------------------------------
# containers: open, move_stack, close
# --------------------------------------------------------------------------

class OpensAfter(FakeBridge):
    """container.state reports closed until the Nth poll."""

    def __init__(self, polls):
        super().__init__()
        self.polls = polls
        self.seen = 0

    def rpc(self, method, params=None, timeout=10.0):
        if method == "container.state":
            self.seen += 1
            self.calls.append(("rpc", method, dict(params or {})))
            return {"open": self.seen >= self.polls}
        return super().rpc(method, params, timeout)


class TestOpen(unittest.TestCase):
    def test_requests_then_polls_until_the_gui_reports_open(self):
        bridge = OpensAfter(1)
        d, _, clock = make(bridge)
        out = d.execute("open", {"x": 118, "y": 64, "z": -44})
        self.assertTrue(out["ok"])
        self.assertEqual([c[1] for c in bridge.verb_calls()],
                         ["container.open", "container.state"])
        self.assertEqual(bridge.verb_calls()[0][2], {"x": 118, "y": 64, "z": -44})
        self.assertEqual(clock.sleeps, [])

    def test_keeps_polling_while_the_gui_is_still_closed(self):
        bridge = OpensAfter(4)
        d, _, clock = make(bridge)
        out = d.execute("open", {"x": 1, "y": 2, "z": 3})
        self.assertTrue(out["ok"])
        self.assertEqual(len([c for c in bridge.calls if c[1] == "container.state"]), 4)
        self.assertEqual(len([c for c in bridge.calls if c[1] == "container.open"]), 1)
        self.assertGreater(sum(clock.sleeps), 0.0)

    def test_a_gui_that_never_opens_is_reported_within_the_bound(self):
        bridge = OpensAfter(10_000)
        d, _, clock = make(bridge)
        out = d.execute("open", {"x": 1, "y": 2, "z": 3})
        self.assertFalse(out["ok"])
        self.assertIn("open", out["error"])
        # It spends the whole bound and not a poll more.
        self.assertAlmostEqual(sum(clock.sleeps),
                               VERB_DURATION_MS["open"] / 1000.0, places=6)
        self.assertLessEqual(out["duration_ms"], VERB_DURATION_MS["open"])
        self.assertGreater(len([c for c in bridge.calls if c[1] == "container.state"]), 1)

    def test_coordinates_are_floored_to_a_block(self):
        bridge = OpensAfter(1)
        d, _, _ = make(bridge)
        d.execute("open", {"x": 118.9, "y": 64.2, "z": -44.1})
        self.assertEqual([c for c in bridge.calls if c[1] == "container.open"][0][2],
                         {"x": 118, "y": 64, "z": -45})

    def test_without_coordinates_it_is_a_reported_failure(self):
        d, bridge, _ = make()
        out = d.execute("open", {"x": 1, "z": 3})
        self.assertFalse(out["ok"])
        self.assertNotIn("container.open", bridge.methods())


class TestMoveStack(unittest.TestCase):
    def test_shift_clicks_the_slot_and_paces_itself(self):
        d, bridge, clock = make()
        out = d.execute("move_stack", {"slot": 5})
        self.assertTrue(out["ok"])
        self.assertEqual([c[1] for c in bridge.verb_calls()], ["container.click"])
        self.assertEqual(bridge.verb_calls()[0][2],
                         {"slot": 5, "button": 0, "mode": "QUICK_MOVE"})
        self.assertEqual(clock.sleeps, [0.3])
        self.assertEqual(out["duration_ms"], 300)

    def test_the_button_is_passed_through(self):
        d, bridge, _ = make()
        d.execute("move_stack", {"slot": 5, "button": 1})
        self.assertEqual(bridge.verb_calls()[0][2],
                         {"slot": 5, "button": 1, "mode": "QUICK_MOVE"})

    def test_slot_zero_is_a_real_slot(self):
        """The furnace output and the 2x2 result both live at slot 0."""
        d, bridge, _ = make()
        out = d.execute("move_stack", {"slot": 0})
        self.assertTrue(out["ok"])
        self.assertEqual(bridge.verb_calls()[0][2],
                         {"slot": 0, "button": 0, "mode": "QUICK_MOVE"})

    def test_without_a_slot_it_is_a_reported_failure(self):
        d, bridge, _ = make()
        out = d.execute("move_stack", None)
        self.assertFalse(out["ok"])
        self.assertIn("slot", out["error"])
        self.assertNotIn("container.click", bridge.methods())

    def test_a_non_numeric_slot_is_reported(self):
        d, bridge, _ = make()
        out = d.execute("move_stack", {"slot": "the coal one"})
        self.assertFalse(out["ok"])
        self.assertNotIn("container.click", bridge.methods())

    def test_a_closed_container_is_data_not_an_exception(self):
        bridge = FakeBridge({"container.click": RuntimeError("no_container")})
        d, _, _ = make(bridge)
        out = d.execute("move_stack", {"slot": 5})
        self.assertFalse(out["ok"])
        self.assertIn("no_container", out["error"])


class TestClose(unittest.TestCase):
    def test_closes_and_is_instant(self):
        d, bridge, clock = make()
        out = d.execute("close")
        self.assertTrue(out["ok"])
        self.assertEqual([c[1] for c in bridge.verb_calls()], ["container.close"])
        self.assertEqual(bridge.verb_calls()[0][2], {})
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(out["duration_ms"], 0)

    def test_a_bridge_failure_is_data(self):
        bridge = FakeBridge({"container.close": ConnectionError("bridge down")})
        d, _, _ = make(bridge)
        out = d.execute("close")
        self.assertFalse(out["ok"])
        self.assertIn("bridge down", out["error"])


# --------------------------------------------------------------------------
# the rule the whole module exists to keep
# --------------------------------------------------------------------------

class TestNoVerbDecidesAnything(unittest.TestCase):
    def test_craft_runs_whatever_it_is_handed(self):
        """No "not enough nuggets, skip it": the RPC is always sent."""
        bridge = FakeBridge()
        bridge.eval_result = inventory()          # empty inventory
        d, _, _ = make(bridge)
        d.execute("craft", {"item": "minecraft:gold_block", "use_max": True})
        self.assertIn("craft.item", bridge.methods())

    def test_move_stack_does_not_check_what_is_in_the_slot(self):
        d, bridge, _ = make()
        d.execute("move_stack", {"slot": 31})
        self.assertEqual([c[1] for c in bridge.verb_calls()], ["container.click"])

    def test_use_item_holds_even_with_a_full_food_bar(self):
        d, bridge, clock = make(FakeBridge(PLAYER_AT_ORIGIN))
        out = d.execute("use_item", {"hold_ms": 1600})
        self.assertTrue(out["ok"])
        self.assertNotIn("player.state", bridge.methods())
        self.assertEqual(clock.sleeps, [1.6])


if __name__ == "__main__":
    unittest.main()


class TestUseItemHoldVerb(unittest.TestCase):
    """Eating needs the button held, and the model can only name a verb.

    `use_item` accepts a hold_ms, but the model chooses from enumerated
    candidates and has no way to say "hold it for 1700ms". Without its own verb,
    eating is unreachable no matter what the order says.
    """

    def test_use_item_hold_is_a_verb(self):
        self.assertIn("use_item_hold", Dispatcher.VERBS)

    def test_it_presses_and_releases_the_use_key(self):
        bridge = FakeBridge()
        Dispatcher(bridge, sleep=lambda s: None).execute("use_item_hold")
        calls = [(c[2].get("key"), c[2].get("action"))
                 for c in bridge.calls if c[0] == "rpc" and c[1] == "player.press_key"]
        self.assertIn(("use", "press"), calls)
        self.assertIn(("use", "release"), calls)
        self.assertEqual(bridge.held_keys(), [])

    def test_its_duration_is_long_enough_to_eat(self):
        # Vanilla food takes ~1.6s of held right-click.
        self.assertGreaterEqual(VERB_DURATION_MS["use_item_hold"], 1600)


class TestUseItemUsesWhatIsHeld(unittest.TestCase):
    """`use_item` is the right-click, nothing more.

    Jev cannot generate text, only pick from enumerated options, so it has no
    way to pass arguments. The two real controls are "choose a hotbar slot" and
    "right-click", and they map onto equip and use_item one for one. Having
    use_item quietly equip something would invent an argument the model never
    gave; it must choose equip first, like anyone holding a controller.
    """

    def _bridge(self):
        bridge = FakeBridge()
        bridge.inventory_json = json.dumps([
            {"slot": 1, "id": "minecraft:netherite_sword", "count": 1},
            {"slot": 8, "id": "minecraft:snowball", "count": 16},
        ])
        return bridge

    def test_it_never_changes_the_hotbar_slot(self):
        bridge = self._bridge()
        Dispatcher(bridge, sleep=lambda s: None).execute("use_item")
        slot_calls = [c for c in bridge.calls
                      if c[0] == "rpc" and c[1] == "player.set_hotbar_slot"]
        self.assertEqual(slot_calls, [])

    def test_it_uses_the_held_item(self):
        bridge = self._bridge()
        result = Dispatcher(bridge, sleep=lambda s: None).execute("use_item")
        self.assertTrue(result["ok"])


class TestAimAdjustmentVerbs(unittest.TestCase):
    """Controls for aiming above or below what you are facing.

    turn_toward points straight at a thing. Thrown items fall on the way, so
    hitting something at range needs aiming higher than it — a fact about the
    world the model has to discover for itself. These verbs are the means to
    try it, nothing more.
    """

    def _rot_after(self, verb, pitch_before=0.0):
        state = {"player.state": {"inWorld": True,
                                  "pos": {"x": 0.0, "y": 64.0, "z": 0.0},
                                  "blockPos": {"x": 0, "y": 64, "z": 0},
                                  "rot": {"yaw": 90.0, "pitch": pitch_before}}}
        bridge = FakeBridge(rpc_results=state)
        Dispatcher(bridge, sleep=lambda s: None).execute(verb)
        return [c[2] for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.set_rotation"][-1]

    def test_aim_higher_raises_the_head(self):
        # Negative pitch is upward in Minecraft.
        self.assertLess(self._rot_after("aim_higher")["pitch"], 0.0)

    def test_aim_lower_drops_the_head(self):
        self.assertGreater(self._rot_after("aim_lower")["pitch"], 0.0)

    def test_aiming_keeps_the_current_yaw(self):
        self.assertEqual(self._rot_after("aim_higher")["yaw"], 90.0)

    def test_aim_is_relative_to_where_the_head_already_points(self):
        self.assertAlmostEqual(self._rot_after("aim_higher", pitch_before=20.0)["pitch"],
                               20.0 - AIM_STEP_DEG, places=1)

    def test_the_head_cannot_spin_past_straight_up(self):
        self.assertGreaterEqual(self._rot_after("aim_higher", pitch_before=-88.0)["pitch"], -90.0)


class TestTurningIsIncremental(unittest.TestCase):
    """Turning is the mouse, not a targeting computer.

    `turn_toward` snapped the head onto a chosen entity, which is the aiming
    solved in code rather than by the model. The real controls are left, right,
    up and down; with the bearing to everything already in state, steering is
    the model's job.
    """

    def _rot(self, verb, yaw=90.0, pitch=5.0):
        state = {"player.state": {"inWorld": True,
                                  "pos": {"x": 0.0, "y": 64.0, "z": 0.0},
                                  "blockPos": {"x": 0, "y": 64, "z": 0},
                                  "rot": {"yaw": yaw, "pitch": pitch}}}
        bridge = FakeBridge(rpc_results=state)
        Dispatcher(bridge, sleep=lambda s: None).execute(verb)
        return [c[2] for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.set_rotation"][-1]

    def test_turn_right_increases_yaw(self):
        self.assertAlmostEqual(self._rot("turn_right")["yaw"], 90.0 + TURN_STEP_DEG, places=1)

    def test_turn_left_decreases_yaw(self):
        self.assertAlmostEqual(self._rot("turn_left")["yaw"], 90.0 - TURN_STEP_DEG, places=1)

    def test_turning_leaves_the_pitch_alone(self):
        self.assertAlmostEqual(self._rot("turn_right")["pitch"], 5.0, places=1)

    def test_yaw_wraps_rather_than_running_away(self):
        self.assertLessEqual(abs(self._rot("turn_right", yaw=179.0)["yaw"]), 180.0)

    def test_turn_toward_is_gone(self):
        self.assertNotIn("turn_toward", Dispatcher.VERBS)

    def test_advance_walks_without_steering(self):
        # Facing a target for it was the same auto-aim in disguise.
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("advance", {"x": 10.0, "y": 64.0, "z": 0.0})
        rotations = [c for c in bridge.calls
                     if c[0] == "rpc" and c[1] == "player.set_rotation"]
        self.assertEqual(rotations, [])


class TestAttackWithoutATarget(unittest.TestCase):
    """A left-click with no target is still a swing.

    In keyboard mode nothing names an entity, so attack has to mean what the
    mouse button means: swing at whatever is in front of you.
    """

    def test_it_swings_when_no_entity_is_named(self):
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        out = Dispatcher(bridge, sleep=lambda s: None).execute("attack")
        self.assertTrue(out["ok"])
        keys = [(c[2].get("key"), c[2].get("action")) for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.press_key"]
        self.assertIn(("attack", "press"), keys)
        self.assertIn(("attack", "release"), keys)

    def test_a_named_entity_still_gets_the_targeted_swing(self):
        bridge = FakeBridge(rpc_results=PLAYER_AT_ORIGIN)
        Dispatcher(bridge, sleep=lambda s: None).execute("attack", {"id": 41})
        self.assertTrue(any(c[0] == "eval" and "attackEntity(41)" in c[1]
                            for c in bridge.calls))


class TestKeyboardLayoutVerbs(unittest.TestCase):
    """The verbs match the keys a player actually has.

    W A S D, Space, Shift, Ctrl, 1-9, E. Minecraft has no native binding for
    looking around with keys, so turn_* and aim_* stand in for the mouse — the
    same rebinds an accessibility setup would need.
    """

    def _bridge(self):
        return FakeBridge(rpc_results=PLAYER_AT_ORIGIN)

    def test_sneak_presses_shift(self):
        bridge = self._bridge()
        Dispatcher(bridge, sleep=lambda s: None).execute("sneak")
        keys = [(c[2].get("key"), c[2].get("action")) for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.press_key"]
        self.assertIn(("sneak", "press"), keys)
        self.assertIn(("sneak", "release"), keys)

    def test_sprint_presses_ctrl_while_moving_forward(self):
        bridge = self._bridge()
        Dispatcher(bridge, sleep=lambda s: None).execute("sprint")
        keys = [(c[2].get("key"), c[2].get("action")) for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.press_key"]
        self.assertIn(("sprint", "press"), keys)
        self.assertIn(("forward", "press"), keys)

    def test_number_keys_select_their_slot(self):
        for n in range(1, 10):
            bridge = self._bridge()
            Dispatcher(bridge, sleep=lambda s: None).execute(f"slot_{n}")
            slots = [c[2].get("slot") for c in bridge.calls
                     if c[0] == "rpc" and c[1] == "player.set_hotbar_slot"]
            self.assertEqual(slots, [n - 1], f"slot_{n} should select hotbar index {n-1}")

    def test_open_inventory_is_the_e_key(self):
        bridge = self._bridge()
        Dispatcher(bridge, sleep=lambda s: None).execute("open_inventory")
        self.assertTrue(any(c[0] == "rpc" and c[1] == "container.open_inventory"
                            for c in bridge.calls))


class TestCycleKeysReadTheRealSlot(unittest.TestCase):
    """Cycling must start from the slot actually selected.

    player.state carries no hotbarSlot, so the cycle keys defaulted to 0 and
    every cycle_item_left landed on slot 8 — empty — leaving the hand holding
    air. Twenty-two ticks of a live run went to that oscillation.
    """

    class SlotBridge(FakeBridge):
        def __init__(self, slot):
            super().__init__(rpc_results=PLAYER_AT_ORIGIN)
            self.slot = slot

        def eval(self, code, timeout_ms=500):
            self.calls.append(("eval", code, timeout_ms))
            if "hotbarSlot" in code:
                return self.slot
            return True

    def _selected_after(self, verb, from_slot):
        bridge = self.SlotBridge(from_slot)
        Dispatcher(bridge, sleep=lambda s: None).execute(verb)
        return [c[2].get("slot") for c in bridge.calls
                if c[0] == "rpc" and c[1] == "player.set_hotbar_slot"][-1]

    def test_next_moves_one_along_from_where_it_is(self):
        self.assertEqual(self._selected_after("cycle_item_right", from_slot=3), 4)

    def test_previous_moves_one_back_from_where_it_is(self):
        self.assertEqual(self._selected_after("cycle_item_left", from_slot=3), 2)

    def test_it_wraps_at_the_ends(self):
        self.assertEqual(self._selected_after("cycle_item_left", from_slot=0), 8)
        self.assertEqual(self._selected_after("cycle_item_right", from_slot=8), 0)
