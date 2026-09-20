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
    # inventoryJson hands back a JSON *string* too, and ships `enchants` the
    # harness does not serve.
    "inventory": json.dumps([
        {"slot": 0, "id": "minecraft:netherite_sword", "count": 1,
         "durability": 2030, "maxDurability": 2031,
         "enchants": {"minecraft:sharpness": 5, "minecraft:looting": 3}},
        {"slot": 2, "id": "minecraft:snowball", "count": 16},
        {"slot": 9, "id": "minecraft:gold_nugget", "count": 34},
        {"slot": 10, "id": "minecraft:rotten_flesh", "count": 24},
        {"slot": 11, "id": "minecraft:gold_nugget", "count": 30},
        {"slot": 12, "id": "minecraft:golden_chestplate", "count": 1,
         "durability": 40, "maxDurability": 112},
    ]),
    # entitiesJson hands back a JSON *string*, as the probe comment says.
    "entities": json.dumps([
        {"id": 12, "type": "minecraft:zombified_piglin", "hostile": True,
         "aggressive": True, "facing_me": True,
         "x": 5729.699951171875, "y": 231.0625, "z": 440.419921875},
        {"id": 287, "type": "minecraft:zombified_piglin", "hostile": True,
         "aggressive": False, "facing_me": False,
         "x": 5729.699951171875, "y": 231.0625, "z": 440.460205078125},
    ]),
}

# `bearing_word` and a per-entity `in_frame` flag are gone: the first is a
# phrase already inside `desc`, the second is the list the entity landed in.
# Everything else has a reader — the dispatcher turns x/y/z and rel_yaw into
# rotations, `attack` needs `id`, and `hostile` is unsayable in the phrase.
ENTITY_KEYS = {"id", "type", "x", "y", "z", "dist", "rel_yaw", "dy",
               "hostile", "aggressive", "facing_me", "desc"}

# The live farm, as `world.blocks_around` reported it: one crafting table, one
# furnace, one anvil, one shulker box, and a wall of chests and hoppers.
BLOCKS = (
    [{"x": 5734, "y": 231, "z": 441, "id": "minecraft:crafting_table"},
     {"x": 5735, "y": 231, "z": 439, "id": "minecraft:chest"},
     {"x": 5734, "y": 232, "z": 442, "id": "minecraft:chipped_anvil"},
     {"x": 5733, "y": 232, "z": 436, "id": "minecraft:furnace"},
     {"x": 5731, "y": 231, "z": 441, "id": "minecraft:shulker_box"},
     {"x": 5739, "y": 231, "z": 444, "id": "minecraft:blast_furnace"},
     {"x": 5730, "y": 231, "z": 440, "id": "minecraft:bamboo_planks"},
     {"x": 5732, "y": 230, "z": 440, "id": "minecraft:bamboo_button"}]
    + [{"x": 5736, "y": 231, "z": 435 + i, "id": "minecraft:chest"}
       for i in range(18)]
    + [{"x": 5735, "y": 230, "z": 435 + i, "id": "minecraft:hopper"}
       for i in range(9)])

CONTAINER = {
    "open": True, "screen": "FurnaceScreen", "syncId": 3,
    "containerSlots": [{"slot": 0, "id": "minecraft:golden_helmet", "count": 1},
                       {"slot": 1, "id": "minecraft:coal", "count": 12}],
    "playerSlots": [{"slot": 30, "id": "minecraft:snowball", "count": 16}],
}


class FakeBridge:
    """Answers the probe script and canSee() without a client."""

    recipes = []

    def __init__(self, raw=None, can_see=True, blocks=None, container=None,
                 rpc_fails=None):
        self.raw = RAW if raw is None else raw
        self.can_see = can_see
        self.blocks = BLOCKS if blocks is None else blocks
        self.container = {"open": False} if container is None else container
        self.rpc_fails = rpc_fails or {}
        self.calls = []
        self.rpc_calls = []

    def eval(self, code, timeout_ms=500):
        self.calls.append(code)
        if "canSee" in code:
            eid = int(code.split("(")[1].split(")")[0])
            return self.can_see(eid) if callable(self.can_see) else self.can_see
        return self.raw

    def rpc(self, method, params=None, timeout=10.0):
        self.rpc_calls.append((method, dict(params or {})))
        if method in self.rpc_fails:
            raise RuntimeError(self.rpc_fails[method])
        if method == "world.blocks_around":
            return {"blocks": self.blocks}
        if method == "container.state":
            return self.container
        if method == "craft.recipes":
            return {"recipes": self.recipes}
        raise AssertionError(f"unexpected rpc {method}")


class EvalOnlyBridge(FakeBridge):
    """A bridge with no `rpc` — the two RPC-fed fields must degrade, not crash."""

    rpc = None


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
        self.assertEqual(set(s), {"self", "hazards", "in_frame", "out_of_frame", "craftable",
                                  "inventory", "stations", "container",
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
        self.assertEqual(s["errors"], {"self": None, "hazards": None, "craftable": None,
                                       "entities": None, "inventory": None,
                                       "stations": None, "container": None})

    def test_inventory_keys_are_fixed(self):
        s = state.build_state(FakeBridge())
        self.assertEqual(set(s["inventory"]),
                         {"counts", "kinds", "free_slots", "hotbar", "held",
                          "tools", "desc"})

    def test_stations_keys_are_fixed(self):
        s = state.build_state(FakeBridge())
        self.assertEqual(set(s["stations"]), {"near", "more", "desc"})

    def test_station_keys_are_fixed(self):
        s = state.build_state(FakeBridge())
        for st in s["stations"]["near"]:
            self.assertEqual(set(st), {"id", "x", "y", "z", "dist", "rel_yaw",
                                       "in_reach", "desc"})

    def test_container_keys_are_fixed_when_open(self):
        s = state.build_state(FakeBridge(container=CONTAINER))
        self.assertEqual(set(s["container"]),
                         {"screen", "slots", "counts", "more_slots",
                          "player_slots", "first_player_slot", "desc"})


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

    The same six-entity fixture cost 2292 bytes before the trim. The fences
    sit above the current figures, not on them, so ordinary description
    rewording does not fail the suite — they catch a field coming back at
    fifteen decimals, or a wall of chests shipping one entry at a time.

    They are split per source because the three new ones are not free: the
    station scan alone is comparable to the entire original snapshot, and a
    single combined number would hide which one grew.
    """

    def snapshot(self, **kw):
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.dumps([
            {"id": 240 + i, "type": "minecraft:zombified_piglin",
             "hostile": True, "aggressive": i % 2 == 0, "facing_me": i % 3 == 0,
             "x": 5729.699951171875 + i * 0.03125,
             "y": 231.0625, "z": 440.419921875 - i * 0.0625} for i in range(6)])
        return state.build_state(FakeBridge(raw, **kw))

    def size(self, obj):
        """Measured the way it ships: compact UTF-8, not escaped."""
        return len(json.dumps(obj, ensure_ascii=False,
                              separators=(",", ":")).encode())

    def test_the_original_snapshot_did_not_grow(self):
        """self, hazards and six entities, with the two hostility bits added."""
        s = self.snapshot()
        old = {k: v for k, v in s.items()
               if k not in ("inventory", "stations", "container")}
        self.assertLess(self.size(old), 2300,
                        f"core snapshot grew to {self.size(old)} bytes")

    def test_inventory_stays_a_few_hundred_bytes(self):
        inv = self.snapshot()["inventory"]
        self.assertLess(self.size(inv), 700,
                        f"inventory grew to {self.size(inv)} bytes")

    def test_a_wall_of_chests_does_not_ship_one_entry_each(self):
        st = self.snapshot()["stations"]
        self.assertLess(self.size(st), 1650,
                        f"stations grew to {self.size(st)} bytes")

    def test_a_full_double_chest_stays_bounded(self):
        container = {"open": True, "screen": "ContainerScreen",
                     "containerSlots": [{"slot": i, "id": "minecraft:gold_ingot",
                                         "count": 64} for i in range(54)],
                     "playerSlots": [{"slot": 54 + i, "id": "minecraft:snowball",
                                      "count": 16} for i in range(36)]}
        c = self.snapshot(container=container)["container"]
        # The worst case there is: 54 gold-ingot slots and a full player
        # inventory. Both sides cap at 27 entries and the rest rolls into
        # `counts`, so this is the ceiling, not a typical furnace tick.
        self.assertLess(self.size(c), 2500,
                        f"container grew to {self.size(c)} bytes")

    def test_the_whole_tick_stays_under_five_kilobytes(self):
        s = self.snapshot(container=CONTAINER)
        self.assertLess(self.size(s), 5000,
                        f"snapshot grew to {self.size(s)} bytes")


class TestInventory(unittest.TestCase):
    """What the bot is carrying, both as counts and as a sentence."""

    def inv(self, **kw):
        return state.build_state(FakeBridge(**kw))["inventory"]

    def test_counts_are_summed_across_slots(self):
        """gold_nugget sits in two slots; the model wants the total."""
        self.assertEqual(self.inv()["counts"]["gold_nugget"], 64)

    def test_counts_are_namespace_stripped(self):
        self.assertNotIn("minecraft:gold_nugget", self.inv()["counts"])

    def test_kinds_counts_distinct_items(self):
        self.assertEqual(self.inv()["kinds"], 5)

    def test_free_slots_is_thirty_six_minus_occupied(self):
        self.assertEqual(self.inv()["free_slots"], 30)

    def test_hotbar_reports_slot_and_item(self):
        hot = self.inv()["hotbar"]
        self.assertEqual([h["slot"] for h in hot], [0, 2])
        self.assertEqual(hot[1]["id"], "snowball")
        self.assertEqual(hot[1]["count"], 16)

    def test_hotbar_excludes_the_main_inventory(self):
        self.assertTrue(all(h["slot"] < 9 for h in self.inv()["hotbar"]))

    def test_hotbar_durability_is_inline(self):
        sword = self.inv()["hotbar"][0]
        self.assertEqual(sword["durability"], 2030)
        self.assertEqual(sword["max_durability"], 2031)

    def test_undamageable_items_carry_no_durability(self):
        self.assertNotIn("durability", self.inv()["hotbar"][1])

    def test_tools_are_damageable_items_outside_the_hotbar(self):
        tools = self.inv()["tools"]
        self.assertEqual([t["id"] for t in tools], ["golden_chestplate"])
        self.assertEqual(tools[0]["durability"], 40)
        self.assertEqual(tools[0]["max_durability"], 112)

    def test_held_is_the_stack_in_the_selected_slot(self):
        raw = json.loads(json.dumps(RAW))
        raw["self"]["slot"] = 2
        self.assertEqual(self.inv(raw=raw)["held"]["id"], "snowball")

    def test_held_is_null_when_the_selected_slot_is_empty(self):
        self.assertIsNone(self.inv()["held"])  # RAW selects slot 4

    def test_enchantments_are_not_shipped(self):
        s = state.build_state(FakeBridge())
        self.assertNotIn("sharpness", json.dumps(s))
        self.assertNotIn("enchants", json.dumps(s))

    def test_desc_names_the_held_item_counts_and_free_slots(self):
        raw = json.loads(json.dumps(RAW))
        raw["self"]["slot"] = 0
        d = self.inv(raw=raw)["desc"]
        self.assertIn("holding netherite_sword", d)
        self.assertIn("64 gold_nugget", d)
        self.assertIn("24 rotten_flesh", d)
        self.assertIn("30 free slots", d)

    def test_desc_says_empty_hands_when_nothing_is_selected(self):
        self.assertIn("holding nothing", self.inv()["desc"])

    def test_desc_places_the_hotbar(self):
        d = self.inv()["desc"]
        self.assertIn("hotbar 0 netherite_sword, 2 snowball", d)

    def test_an_empty_inventory_is_empty_not_unknown(self):
        raw = json.loads(json.dumps(RAW))
        raw["inventory"] = "[]"
        inv = self.inv(raw=raw)
        self.assertEqual(inv["counts"], {})
        self.assertEqual(inv["free_slots"], 36)
        self.assertIn("carrying nothing", inv["desc"])

    def test_a_failed_probe_leaves_nulls_and_reports(self):
        raw = json.loads(json.dumps(RAW))
        raw["inventory"] = {"error": "attempt to call a nil value"}
        s = state.build_state(FakeBridge(raw=raw))
        self.assertIsNone(s["inventory"]["counts"])
        self.assertIsNone(s["inventory"]["free_slots"])
        self.assertIsNone(s["inventory"]["desc"])
        self.assertIn("nil value", s["errors"]["inventory"])

    def test_item_kinds_are_capped_and_the_total_still_shows(self):
        raw = json.loads(json.dumps(RAW))
        raw["inventory"] = json.dumps(
            [{"slot": i, "id": f"minecraft:item_{i}", "count": 36 - i}
             for i in range(30)])
        inv = self.inv(raw=raw)
        self.assertEqual(len(inv["counts"]), state.MAX_ITEM_KINDS)
        self.assertEqual(inv["kinds"], 30)

    def test_the_largest_stacks_survive_the_cap(self):
        raw = json.loads(json.dumps(RAW))
        raw["inventory"] = json.dumps(
            [{"slot": i, "id": f"minecraft:item_{i}", "count": 36 - i}
             for i in range(30)])
        self.assertIn("item_0", self.inv(raw=raw)["counts"])
        self.assertNotIn("item_29", self.inv(raw=raw)["counts"])


class TestStations(unittest.TestCase):
    """Interactable blocks: where they are, how far, and whether they reach."""

    def st(self, **kw):
        return state.build_state(FakeBridge(**kw))["stations"]

    def test_the_scan_asks_for_the_configured_radius(self):
        bridge = FakeBridge()
        state.build_state(bridge)
        self.assertIn(("world.blocks_around", {"radius": state.STATION_RADIUS}),
                      bridge.rpc_calls)

    def test_plain_building_blocks_are_not_stations(self):
        ids = {s["id"] for s in self.st()["near"]}
        self.assertNotIn("bamboo_planks", ids)
        self.assertNotIn("bamboo_button", ids)

    def test_stations_are_sorted_nearest_first(self):
        d = [s["dist"] for s in self.st()["near"]]
        self.assertEqual(d, sorted(d))

    def test_the_nearest_station_is_the_crafting_table(self):
        near = self.st()["near"][0]
        self.assertEqual(near["id"], "crafting_table")
        self.assertEqual((near["x"], near["y"], near["z"]), (5734, 231, 441))

    def test_reach_is_a_distance_and_a_flag_not_a_verdict(self):
        near = self.st()["near"][0]
        self.assertIsInstance(near["dist"], float)
        self.assertTrue(near["in_reach"])
        self.assertLess(near["dist"], state.REACH_BLOCKS)

    def test_a_far_station_is_out_of_reach(self):
        far = [s for s in self.st()["near"] if s["id"] == "blast_furnace"][0]
        self.assertGreater(far["dist"], state.REACH_BLOCKS)
        self.assertFalse(far["in_reach"])

    def test_a_station_inside_the_interaction_range_is_in_reach(self):
        """3.9m is reachable; only the flag, never a verdict about using it."""
        furnace = [s for s in self.st()["near"] if s["id"] == "furnace"][0]
        self.assertLess(furnace["dist"], state.REACH_BLOCKS)
        self.assertTrue(furnace["in_reach"])

    def test_desc_carries_position_bearing_distance_and_reach(self):
        far = [s for s in self.st()["near"] if s["id"] == "blast_furnace"][0]
        self.assertIn("blast_furnace at (5739,231,444)", far["desc"])
        self.assertIn("m,", far["desc"])
        self.assertIn("out of reach", far["desc"])
        self.assertTrue(any(w in far["desc"]
                            for w in ("ahead", "behind", "left", "right")))

    def test_a_reachable_station_says_in_reach(self):
        self.assertIn("in reach", self.st()["near"][0]["desc"])

    def test_nineteen_chests_collapse_to_a_count(self):
        s = self.st()
        shown = [x for x in s["near"] if x["id"] == "chest"]
        self.assertEqual(len(shown), state.MAX_PER_KIND)
        self.assertEqual(s["more"]["chest"], 19 - state.MAX_PER_KIND)

    def test_the_collapsed_count_is_said_in_the_roll_up(self):
        self.assertIn("more chests", self.st()["desc"])

    def test_the_list_is_capped(self):
        self.assertLessEqual(len(self.st()["near"]), state.MAX_STATIONS)

    def test_one_of_a_kind_stations_are_never_collapsed(self):
        ids = {s["id"] for s in self.st()["near"]}
        for one in ("crafting_table", "furnace", "chipped_anvil",
                    "shulker_box", "blast_furnace"):
            self.assertIn(one, ids)

    def test_coloured_shulker_boxes_count_as_stations(self):
        blocks = [{"x": 5734, "y": 231, "z": 441,
                   "id": "minecraft:red_shulker_box"}]
        self.assertEqual(self.st(blocks=blocks)["near"][0]["id"],
                         "red_shulker_box")

    def test_no_stations_is_said_plainly(self):
        s = self.st(blocks=[{"x": 5734, "y": 231, "z": 441,
                             "id": "minecraft:netherrack"}])
        self.assertEqual(s["near"], [])
        self.assertEqual(s["more"], {})
        self.assertIn("no ", s["desc"])

    def test_a_failed_scan_degrades_and_reports(self):
        bridge = FakeBridge(rpc_fails={"world.blocks_around": "no_player"})
        s = state.build_state(bridge)
        self.assertIsNone(s["stations"]["near"])
        self.assertIsNone(s["stations"]["desc"])
        self.assertIn("no_player", s["errors"]["stations"])

    def test_a_bridge_without_rpc_degrades_rather_than_crashing(self):
        s = state.build_state(EvalOnlyBridge())
        self.assertIsNone(s["stations"]["near"])
        self.assertIsNone(s["container"])
        self.assertIsNotNone(s["errors"]["stations"])


class TestContainer(unittest.TestCase):
    """Null until a screen is open; then the slots, split by side."""

    def test_nothing_open_is_null(self):
        self.assertIsNone(state.build_state(FakeBridge())["container"])

    def test_the_key_is_present_even_when_null(self):
        self.assertIn("container", state.build_state(FakeBridge()))

    def test_an_open_screen_is_named_readably(self):
        s = state.build_state(FakeBridge(container=CONTAINER))["container"]
        self.assertEqual(s["screen"], "furnace")

    def test_container_slots_are_separate_from_player_slots(self):
        s = state.build_state(FakeBridge(container=CONTAINER))["container"]
        self.assertEqual([x["slot"] for x in s["slots"]], [0, 1])
        self.assertEqual([x["slot"] for x in s["player_slots"]], [30])

    def test_slot_ids_are_namespace_stripped(self):
        s = state.build_state(FakeBridge(container=CONTAINER))["container"]
        self.assertEqual(s["slots"][0]["id"], "golden_helmet")

    def test_first_player_slot_marks_the_boundary(self):
        s = state.build_state(FakeBridge(container=CONTAINER))["container"]
        self.assertEqual(s["first_player_slot"], 30)

    def test_counts_roll_up_the_container_side_only(self):
        s = state.build_state(FakeBridge(container=CONTAINER))["container"]
        self.assertEqual(s["counts"], {"golden_helmet": 1, "coal": 12})

    def test_desc_says_what_is_inside_and_where_the_player_side_starts(self):
        d = state.build_state(FakeBridge(container=CONTAINER))["container"]["desc"]
        self.assertIn("furnace open", d)
        self.assertIn("12 coal", d)
        self.assertIn("menu slot 30", d)

    def test_an_open_but_empty_container_says_so(self):
        c = {"open": True, "screen": "ContainerScreen",
             "containerSlots": [], "playerSlots": []}
        s = state.build_state(FakeBridge(container=c))["container"]
        self.assertEqual(s["slots"], [])
        self.assertIn("empty", s["desc"])

    def test_a_full_double_chest_is_capped_but_counted(self):
        c = {"open": True, "screen": "ContainerScreen",
             "containerSlots": [{"slot": i, "id": "minecraft:gold_ingot",
                                 "count": 64} for i in range(54)],
             "playerSlots": []}
        s = state.build_state(FakeBridge(container=c))["container"]
        self.assertEqual(len(s["slots"]), state.MAX_CONTAINER_SLOTS)
        self.assertEqual(s["more_slots"], 54 - state.MAX_CONTAINER_SLOTS)
        self.assertEqual(s["counts"]["gold_ingot"], 54 * 64)

    def test_a_failed_read_degrades_and_reports(self):
        bridge = FakeBridge(rpc_fails={"container.state": "timeout"})
        s = state.build_state(bridge)
        self.assertIsNone(s["container"])
        self.assertIn("timeout", s["errors"]["container"])


class TestHostilityBits(unittest.TestCase):
    """`hostile`, `aggressive` and `facing_me` are three facts, not one."""

    def ents(self, raw=None):
        s = state.build_state(FakeBridge(raw=raw) if raw else FakeBridge())
        return s["in_frame"] + s["out_of_frame"]

    def test_all_three_bits_survive(self):
        e = self.ents()[0]
        self.assertTrue(e["hostile"])
        self.assertTrue(e["aggressive"])
        self.assertTrue(e["facing_me"])

    def test_a_calm_piglin_is_hostile_but_not_aggressive(self):
        e = self.ents()[1]
        self.assertTrue(e["hostile"])
        self.assertFalse(e["aggressive"])
        self.assertFalse(e["facing_me"])

    def test_desc_says_aggressive_when_it_is(self):
        self.assertIn("aggressive", self.ents()[0]["desc"])
        self.assertIn("facing you", self.ents()[0]["desc"])

    def test_desc_says_not_aggressive_when_it_is_not(self):
        """The order turns on this: aggravate the ones that are not already."""
        d = self.ents()[1]["desc"]
        self.assertIn("not aggressive", d)
        self.assertIn("facing away", d)

    def test_an_old_jar_reports_unknown_not_false(self):
        """Before the rebuilt jar loads, neither bit is on the wire."""
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.dumps([
            {"id": 12, "type": "minecraft:zombified_piglin", "hostile": True,
             "x": 5729.7, "y": 231.06, "z": 440.42}])
        e = self.ents(raw)[0]
        self.assertIsNone(e["aggressive"])
        self.assertIsNone(e["facing_me"])
        self.assertNotIn("aggressive", e["desc"])

    def test_hostility_class_is_never_conflated_with_aggression(self):
        raw = json.loads(json.dumps(RAW))
        raw["entities"] = json.dumps([
            {"id": 5, "type": "minecraft:villager", "hostile": False,
             "aggressive": False, "facing_me": True,
             "x": 5729.7, "y": 231.06, "z": 440.42}])
        e = self.ents(raw)[0]
        self.assertFalse(e["hostile"])
        self.assertFalse(e["aggressive"])
        self.assertTrue(e["facing_me"])


class TestNewProbes(unittest.TestCase):
    def test_inventory_is_a_probe_not_python(self):
        self.assertIn("probe('inventory'", state.compose_probes())


class TestCraftable(unittest.TestCase):
    """What the recipe book says can be made right now.

    Without this, `craft` has nothing to name: target_item can only offer items
    already carried, so "craft nuggets into ingots" is unreachable.
    """

    class RecipeBridge(FakeBridge):
        def __init__(self, recipes=None, fail=False):
            super().__init__()
            self._recipes = recipes if recipes is not None else [
                {"result": "minecraft:gold_ingot", "count": 1, "craftable": True},
                {"result": "minecraft:gold_ingot", "count": 9, "craftable": True},
                {"result": "minecraft:gold_block", "count": 1, "craftable": True},
            ]
            self._fail = fail

        def rpc(self, method, params=None, timeout=10.0):
            if method == "craft.recipes":
                if self._fail:
                    raise RuntimeError("no such method")
                return {"recipes": list(self._recipes)}
            return super().rpc(method, params, timeout)

    def test_craftable_results_reach_the_state(self):
        st = state.build_state(self.RecipeBridge())
        results = [r["result"] for r in st["craftable"]]
        self.assertIn("minecraft:gold_block", results)

    def test_each_result_appears_once(self):
        st = state.build_state(self.RecipeBridge())
        results = [r["result"] for r in st["craftable"]]
        self.assertEqual(len(results), len(set(results)))

    def test_each_entry_carries_a_phrase(self):
        st = state.build_state(self.RecipeBridge())
        self.assertTrue(all(r.get("desc") for r in st["craftable"]))

    def test_a_bridge_without_the_method_degrades_to_empty(self):
        # An older jar has no craft.recipes; the tick must still happen.
        st = state.build_state(self.RecipeBridge(fail=True))
        self.assertEqual(st["craftable"], [])
        self.assertIsNotNone(st["errors"]["craftable"])


class TestEntityHealthIsVisible(unittest.TestCase):
    """The phrase should say how hurt a creature is.

    Thirty attacks landed on nothing and the state never showed the target's
    health, so there was no way to see that it was not working — or that one
    piglin was already at 0.06 hp.
    """

    def test_phrase_carries_health(self):
        raw = {"self": {"x": 0.0, "y": 64.0, "z": 0.0, "yaw": 0.0},
               "entities": json.dumps([{"id": 1, "type": "minecraft:zombified_piglin",
                                        "x": 0.0, "y": 64.0, "z": 5.0, "dist": 5.0,
                                        "hostile": True, "living": True, "health": 12.5}])}
        st = state.build_state(FakeBridge(raw=raw))
        desc = (st["in_frame"] + st["out_of_frame"])[0]["desc"]
        self.assertIn("12.5", desc)
