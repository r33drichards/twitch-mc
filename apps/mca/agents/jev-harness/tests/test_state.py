"""Tests for state.py — the tick snapshot handed to Jev.

The snapshot is the harness's whole input budget, so these tests pin two
things at once: that every field the model and dispatcher read is present on
every tick, and that no field ships more precision than it can use.

A fake bridge stands in for Minecraft. Nothing here needs a running game.
"""
import json
import math
import unittest

import state


RAW = {
    "self": {
        "x": 5734.047213269778, "y": 231, "z": 440.26879906527984,
        "yaw": 90.95561981201172, "pitch": -4.499242305755615,
        "health": 18.92188262939453, "food": 16,
        "held": "minecraft:air", "slot": 4,
        "dim": "minecraft:the_nether", "name": "lmoik",
    },
    "hazard": {
        "block_under": "minecraft:bamboo_planks",
        "block_ahead": "minecraft:bamboo_button",
        "block_ahead_under": "minecraft:bamboo_planks",
        "block_ahead_below2": "minecraft:air",
    },
    # entitiesJson hands back a JSON *string*, as the probe comment says.
    "entities": json.dumps([
        {"id": 12, "type": "minecraft:zombified_piglin", "hostile": True,
         "x": 5729.699951171875, "y": 231.0625, "z": 440.419921875},
        {"id": 287, "type": "minecraft:zombified_piglin", "hostile": True,
         "x": 5729.699951171875, "y": 231.0625, "z": 440.460205078125},
    ]),
}

# `bearing_word` and a per-entity `in_frame` flag are gone: the first is a
# phrase already inside `desc`, the second is the list the entity landed in.
# Everything else has a reader — the dispatcher turns x/y/z and rel_yaw into
# rotations, `attack` needs `id`, and `hostile` is unsayable in the phrase.
ENTITY_KEYS = {"id", "type", "x", "y", "z", "dist", "rel_yaw", "dy",
               "hostile", "desc"}


class FakeBridge:
    """Answers the probe script and canSee() without a client."""

    def __init__(self, raw=None, can_see=True):
        self.raw = RAW if raw is None else raw
        self.can_see = can_see
        self.calls = []

    def eval(self, code, timeout_ms=500):
        self.calls.append(code)
        if "canSee" in code:
            eid = int(code.split("(")[1].split(")")[0])
            return self.can_see(eid) if callable(self.can_see) else self.can_see
        return self.raw


def walk_floats(obj, path="$"):
    """Every float in the snapshot, with the path that reached it."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_floats(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_floats(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        yield path, obj


def decimals(value):
    """How many digits the float actually needs when printed."""
    text = repr(float(value))
    if "e" in text or "E" in text:
        return 99
    return len(text.split(".")[1].rstrip("0").rstrip()) if "." in text else 0


class TestShape(unittest.TestCase):
    """Keys are stable tick to tick; question criteria index into them."""

    def test_top_level_keys_are_fixed(self):
        s = state.build_state(FakeBridge())
        self.assertEqual(set(s), {"self", "hazards", "in_frame", "out_of_frame",
                                  "order", "errors", "captured_at"})

    def test_self_keys_are_fixed(self):
        s = state.build_state(FakeBridge())
        self.assertEqual(set(s["self"]),
                         {"x", "y", "z", "yaw", "pitch", "health", "food",
                          "held", "slot", "dim", "name", "desc"})

    def test_hazard_keys_are_fixed(self):
        s = state.build_state(FakeBridge())
        self.assertEqual(set(s["hazards"]),
                         {"block_under", "block_ahead", "block_ahead_under",
                          "block_ahead_below2", "desc"})

    def test_entity_keys_are_exactly_the_useful_ones(self):
        s = state.build_state(FakeBridge())
        for e in s["in_frame"] + s["out_of_frame"]:
            self.assertEqual(set(e), ENTITY_KEYS)

    def test_missing_probe_fields_are_null_not_absent(self):
        raw = {"self": {"error": "boom"}, "hazard": {"error": "boom"},
               "entities": None}
        s = state.build_state(FakeBridge(raw))
        self.assertEqual(set(s["self"]),
                         {"x", "y", "z", "yaw", "pitch", "health", "food",
                          "held", "slot", "dim", "name", "desc"})
        self.assertIsNone(s["self"]["x"])
        self.assertIsNone(s["self"]["desc"])
        self.assertIsNone(s["hazards"]["block_under"])

    def test_probe_errors_reach_the_model(self):
        raw = {"self": {"error": "attempt to index a nil value"},
               "hazard": {"error": "nope"}, "entities": None}
        s = state.build_state(FakeBridge(raw))
        self.assertIn("nil value", s["errors"]["self"])
        self.assertEqual(s["errors"]["hazards"], "nope")

    def test_errors_are_null_when_probes_are_healthy(self):
        s = state.build_state(FakeBridge())
        self.assertEqual(s["errors"], {"self": None, "hazards": None,
                                       "entities": None})


class TestPrecision(unittest.TestCase):
    """The 15-decimal floats were half the bill."""

    def test_no_float_carries_more_than_two_decimals(self):
        s = state.build_state(FakeBridge())
        for path, value in walk_floats(s):
            self.assertLessEqual(decimals(value), 2,
                                 f"{path} = {value!r} is over-precise")

    def test_self_coordinates_round_to_two_decimals(self):
        s = state.build_state(FakeBridge())["self"]
        self.assertEqual(s["x"], 5734.05)
        self.assertEqual(s["z"], 440.27)

    def test_health_rounds_to_one_decimal(self):
        self.assertEqual(state.build_state(FakeBridge())["self"]["health"], 18.9)

    def test_yaw_and_pitch_are_whole_degrees(self):
        s = state.build_state(FakeBridge())["self"]
        self.assertEqual(s["yaw"], 91)
        self.assertEqual(s["pitch"], -4)

    def test_entity_coordinates_round_to_one_decimal(self):
        """The dispatcher turns these into a yaw; it never needs 15 digits."""
        e = state.build_state(FakeBridge())["in_frame"][0]
        self.assertEqual(e["x"], 5729.7)
        self.assertEqual(e["y"], 231.1)
        self.assertEqual(e["z"], 440.4)

    def test_derived_restatements_are_gone(self):
        e = state.build_state(FakeBridge())["in_frame"][0]
        self.assertNotIn("bearing_word", e)
        self.assertNotIn("in_frame", e)

    def test_distance_rounds_to_one_decimal(self):
        e = state.build_state(FakeBridge())["in_frame"][0]
        self.assertEqual(decimals(e["dist"]), 1)

    def test_bearing_is_a_whole_degree_int(self):
        e = state.build_state(FakeBridge())["in_frame"][0]
        self.assertIsInstance(e["rel_yaw"], int)
        self.assertEqual(e["rel_yaw"], -3)

    def test_captured_at_is_not_a_seventeen_digit_float(self):
        s = state.build_state(FakeBridge())
        self.assertLessEqual(decimals(s["captured_at"]), 2)


class TestDescriptions(unittest.TestCase):
    """Both representations survive — numeric and phrased."""

    def test_self_desc_exists_and_is_rounded(self):
        d = state.build_state(FakeBridge())["self"]["desc"]
        self.assertIn("18.9/20 health", d)
        self.assertNotIn("18.92188", d)

    def test_self_desc_names_the_player_and_position(self):
        d = state.build_state(FakeBridge())["self"]["desc"]
        self.assertIn("lmoik", d)
        self.assertIn("(5734,231,440)", d)

    def test_hazard_desc_survives(self):
        d = state.build_state(FakeBridge())["hazards"]["desc"]
        self.assertIn("bamboo_planks", d)
        self.assertIn("bamboo_button", d)

    def test_entity_desc_survives_with_bearing_and_distance(self):
        e = state.build_state(FakeBridge())["in_frame"][0]
        self.assertIn("zombified_piglin", e["desc"])
        self.assertIn("left", e["desc"])
        self.assertIn("m away", e["desc"])

    def test_minecraft_namespace_is_stripped_everywhere(self):
        s = state.build_state(FakeBridge())
        self.assertNotIn("minecraft:", json.dumps(s))

    def test_non_vanilla_namespace_is_kept(self):
        raw = json.loads(json.dumps(RAW))
        raw["hazard"]["block_under"] = "create:andesite_casing"
        s = state.build_state(FakeBridge(raw))
        self.assertEqual(s["hazards"]["block_under"], "create:andesite_casing")


class TestVisibility(unittest.TestCase):
    """The cone proposes; canSee disposes."""

    def test_visible_entity_lands_in_frame(self):
        s = state.build_state(FakeBridge(can_see=True))
        self.assertEqual(len(s["in_frame"]), 2)
        self.assertEqual(s["out_of_frame"], [])

    def test_walled_off_entity_moves_out_of_frame(self):
        s = state.build_state(FakeBridge(can_see=False))
        self.assertEqual(s["in_frame"], [])
        self.assertEqual(len(s["out_of_frame"]), 2)

    def test_out_of_sight_is_said_in_the_desc(self):
        s = state.build_state(FakeBridge(can_see=False))
        self.assertIn("(out of sight)", s["out_of_frame"][0]["desc"])

    def test_entity_behind_the_player_skips_the_raycast(self):
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.dumps([
            {"id": 9, "type": "minecraft:creeper", "hostile": True,
             "x": 5744.0, "y": 231.0, "z": 440.0}])  # opposite the yaw-91 facing
        bridge = FakeBridge(raw)
        s = state.build_state(bridge)
        self.assertEqual(len(s["out_of_frame"]), 1)
        self.assertFalse(any("canSee" in c for c in bridge.calls))

    def test_entity_id_survives_for_the_dispatcher(self):
        s = state.build_state(FakeBridge())
        self.assertEqual([e["id"] for e in s["in_frame"]], [12, 287])


class TestPlumbing(unittest.TestCase):
    def test_order_defaults_to_null(self):
        self.assertIsNone(state.build_state(FakeBridge())["order"])

    def test_order_is_passed_through(self):
        order = {"kind": "goto", "desc": "walk to the warehouse"}
        self.assertEqual(state.build_state(FakeBridge(), order=order)["order"],
                         order)

    def test_entities_may_arrive_already_parsed(self):
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.loads(raw["entities"])
        self.assertEqual(len(state.build_state(FakeBridge(raw))["in_frame"]), 2)

    def test_described_entities_are_capped(self):
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.dumps([
            {"id": i, "type": "minecraft:zombie", "hostile": True,
             "x": 5730.0, "y": 231.0, "z": 440.0} for i in range(20)])
        s = state.build_state(FakeBridge(raw))
        self.assertEqual(len(s["in_frame"]) + len(s["out_of_frame"]),
                         state.MAX_DESCRIBED)

    def test_compose_probes_wraps_every_probe(self):
        script = state.compose_probes()
        for name in ("self", "hazard", "entities"):
            self.assertIn(f"probe('{name}'", script)
        self.assertIn("pcall", script)
        self.assertTrue(script.rstrip().endswith("return S"))


class TestBudget(unittest.TestCase):
    """A regression fence on the thing this module exists to fix.

    The same six-entity fixture cost 2292 bytes before the trim. The fence
    sits above the current figure, not on it, so ordinary description
    rewording does not fail the suite — it catches a field coming back at
    fifteen decimals.
    """

    def test_six_entity_snapshot_stays_small(self):
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.dumps([
            {"id": 240 + i, "type": "minecraft:zombified_piglin",
             "hostile": True, "x": 5729.699951171875 + i * 0.03125,
             "y": 231.0625, "z": 440.419921875 - i * 0.0625} for i in range(6)])
        # Measured the way it ships: compact UTF-8, not \uXXXX escapes.
        blob = json.dumps(state.build_state(FakeBridge(raw)), ensure_ascii=False,
                          separators=(",", ":")).encode()
        self.assertLess(len(blob), 1850, f"snapshot grew to {len(blob)} bytes")


if __name__ == "__main__":
    unittest.main()
