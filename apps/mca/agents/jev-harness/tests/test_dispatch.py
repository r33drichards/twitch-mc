"""Bounded execution against a recording fake bridge.

Nothing here talks to Minecraft. The fake records every call, so the tests
assert on the call sequence itself: that release-all precedes every verb,
that a bounded press is press-sleep-release, and that no path leaves a key
latched down.
"""
import os
import subprocess
import sys
import tempfile
import time
import textwrap
import unittest

import dispatch
from dispatch import LATCHING_KEYS, VERB_DURATION_MS, Dispatcher

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


ALL_VERBS = ["advance", "retreat", "turn_toward", "jump", "mine_front",
             "place_block", "attack", "use_item", "hold", "done"]


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
    def test_durations_match_the_design(self):
        self.assertEqual(VERB_DURATION_MS, {
            "advance": 300, "retreat": 300, "turn_toward": 0, "jump": 150,
            "mine_front": 1000, "place_block": 0, "attack": 0, "use_item": 0,
            "hold": 150, "done": 150})

    def test_every_verb_has_a_handler_and_a_duration(self):
        self.assertEqual(sorted(Dispatcher.VERBS), sorted(VERB_DURATION_MS))
        self.assertEqual(sorted(Dispatcher.VERBS), sorted(ALL_VERBS))

    def test_result_shape(self):
        d, _, _ = make()
        out = d.execute("hold")
        self.assertEqual(sorted(out), ["duration_ms", "error", "ok", "verb"])
        self.assertTrue(out["ok"])
        self.assertIsNone(out["error"])
        self.assertEqual(out["verb"], "hold")


class TestAdvance(unittest.TestCase):
    def test_turns_then_presses_forward_then_releases(self):
        d, bridge, clock = make(FakeBridge(PLAYER_AT_ORIGIN))
        out = d.execute("advance", {"yaw": 90.0})
        self.assertTrue(out["ok"])
        self.assertEqual([c[1] for c in bridge.verb_calls()],
                         ["player.set_rotation", "player.press_key", "player.press_key"])
        self.assertEqual(bridge.verb_key_calls(), [("forward", "press"), ("forward", "release")])
        self.assertEqual(clock.sleeps, [0.3])
        self.assertEqual(out["duration_ms"], 300)

    def test_target_coordinates_become_an_absolute_yaw(self):
        # facing +Z is yaw 0; a target due west (-X) is yaw 90
        d, bridge, _ = make(FakeBridge(PLAYER_AT_ORIGIN))
        d.execute("advance", {"x": -10.0, "y": 64.0, "z": 0.0})
        rot = [c for c in bridge.calls if c[1] == "player.set_rotation"][0]
        self.assertYaw(rot[2]["yaw"], 90.0)

    def test_target_straight_ahead_is_yaw_zero(self):
        d, bridge, _ = make(FakeBridge(PLAYER_AT_ORIGIN))
        d.execute("advance", {"x": 0.0, "y": 64.0, "z": 10.0})
        rot = [c for c in bridge.calls if c[1] == "player.set_rotation"][0]
        self.assertYaw(rot[2]["yaw"], 0.0)

    def test_relative_bearing_is_added_to_the_current_yaw(self):
        state = {"player.state": {"inWorld": True,
                                  "pos": {"x": 0.0, "y": 64.0, "z": 0.0},
                                  "rot": {"yaw": 170.0, "pitch": 0.0}}}
        d, bridge, _ = make(FakeBridge(state))
        d.execute("turn_toward", {"rel_yaw": 30.0})
        rot = [c for c in bridge.calls if c[1] == "player.set_rotation"][0]
        self.assertYaw(rot[2]["yaw"], -160.0)       # wrapped the short way

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

    def test_turn_toward_is_instant(self):
        d, bridge, clock = make(FakeBridge(PLAYER_AT_ORIGIN))
        out = d.execute("turn_toward", {"yaw": -45.0, "pitch": 10.0})
        self.assertEqual([c[1] for c in bridge.verb_calls()], ["player.set_rotation"])
        self.assertEqual(bridge.verb_calls()[-1][2], {"yaw": -45.0, "pitch": 10.0})
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(out["duration_ms"], 0)

    def test_turn_toward_without_a_bearing_is_a_reported_failure(self):
        d, bridge, _ = make()
        out = d.execute("turn_toward", None)
        self.assertFalse(out["ok"])
        self.assertIn("bearing", out["error"])
        self.assertNotIn("player.set_rotation", bridge.methods())

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

    def test_attack_without_an_entity_id_is_reported(self):
        d, bridge, _ = make()
        out = d.execute("attack", {"x": 1, "y": 2, "z": 3})
        self.assertFalse(out["ok"])
        self.assertIn("id", out["error"])
        self.assertEqual([c for c in bridge.calls if c[0] == "eval"], [])

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


if __name__ == "__main__":
    unittest.main()
