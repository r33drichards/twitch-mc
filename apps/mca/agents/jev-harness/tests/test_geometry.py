"""Bearing math against Minecraft's yaw convention.

MC yaw: 0 = +Z (south), 90 = -X (west), 180 = -Z (north), -90 = +X (east).
Increasing yaw turns the player right, so a positive relative bearing means
the target is to the RIGHT and negative means LEFT.

These tests exist because a wrong convention produces descriptions that read
perfectly and lie completely.
"""
import unittest

from geometry import (
    normalize_deg,
    relative_bearing,
    bearing_word,
    vertical_word,
    in_frame,
    describe_entity,
)


class TestNormalizeDeg(unittest.TestCase):
    def test_wraps_above_180_to_negative(self):
        self.assertAlmostEqual(normalize_deg(190), -170)

    def test_wraps_below_minus_180(self):
        self.assertAlmostEqual(normalize_deg(-350), 10)

    def test_keeps_exactly_180_positive(self):
        self.assertAlmostEqual(normalize_deg(180), 180)


class TestRelativeBearing(unittest.TestCase):
    def test_target_straight_ahead_facing_south(self):
        # yaw 0 faces +Z; target 10 blocks along +Z
        self.assertAlmostEqual(relative_bearing(0, dx=0, dz=10), 0)

    def test_west_target_is_right_when_facing_south(self):
        # facing south (+Z), your right hand points west (-X)
        self.assertAlmostEqual(relative_bearing(0, dx=-10, dz=0), 90)

    def test_east_target_is_left_when_facing_south(self):
        self.assertAlmostEqual(relative_bearing(0, dx=10, dz=0), -90)

    def test_target_directly_behind(self):
        self.assertAlmostEqual(abs(relative_bearing(0, dx=0, dz=-10)), 180)

    def test_accounts_for_player_yaw(self):
        # yaw 90 faces -X (west); a target due west is straight ahead
        self.assertAlmostEqual(relative_bearing(90, dx=-10, dz=0), 0)

    def test_wraps_the_short_way_around(self):
        # facing yaw 170, target lies at absolute bearing -170: a 20 degree
        # turn right, not a 340 degree turn left
        self.assertAlmostEqual(relative_bearing(170, dx=0, dz=-10), 10)


class TestBearingWord(unittest.TestCase):
    def test_zero_is_ahead(self):
        self.assertEqual(bearing_word(0), "ahead")

    def test_boundary_22_is_still_ahead(self):
        self.assertEqual(bearing_word(22), "ahead")

    def test_boundary_23_is_ahead_right(self):
        self.assertEqual(bearing_word(23), "ahead-right")

    def test_negative_45_is_ahead_left(self):
        self.assertEqual(bearing_word(-45), "ahead-left")

    def test_90_is_right(self):
        self.assertEqual(bearing_word(90), "right")

    def test_130_is_behind_right(self):
        self.assertEqual(bearing_word(130), "behind-right")

    def test_178_is_behind(self):
        self.assertEqual(bearing_word(178), "behind")

    def test_minus_178_is_also_behind(self):
        self.assertEqual(bearing_word(-178), "behind")


class TestVerticalWord(unittest.TestCase):
    def test_level_within_a_block_and_a_half(self):
        self.assertEqual(vertical_word(1.0), "level")

    def test_reports_blocks_above(self):
        self.assertEqual(vertical_word(3.0), "3 blocks above")

    def test_reports_one_block_below_singular(self):
        self.assertEqual(vertical_word(-1.6), "1 block below")


class TestInFrame(unittest.TestCase):
    def test_dead_ahead_is_in_frame(self):
        self.assertTrue(in_frame(0))

    def test_inside_the_cone_edge(self):
        self.assertTrue(in_frame(-49))

    def test_outside_the_cone(self):
        self.assertFalse(in_frame(60))

    def test_behind_is_never_in_frame(self):
        self.assertFalse(in_frame(178))


class TestDescribeEntity(unittest.TestCase):
    def test_creeper_behind_and_below(self):
        d = describe_entity(
            etype="minecraft:creeper", ex=118.0, ey=64.0, ez=-44.0,
            px=118.0, py=65.6, pz=-37.8, pyaw=0.0,
        )
        self.assertEqual(d["type"], "creeper")
        # 3D distance, matching the `dist` entitiesJson already reports
        self.assertAlmostEqual(d["dist"], 6.4, places=1)
        self.assertAlmostEqual(abs(d["rel_yaw"]), 180, places=0)
        self.assertEqual(d["bearing_word"], "behind")
        self.assertFalse(d["in_frame"])
        self.assertIn("creeper at (118,64,-44)", d["desc"])
        self.assertIn("6.4m away", d["desc"])
        self.assertIn("1 block below", d["desc"])

    def test_zombie_ahead_right_says_degrees_right(self):
        d = describe_entity(
            etype="minecraft:zombie", ex=100.0, ey=64.0, ez=110.0,
            px=100.0, py=64.0, pz=100.0, pyaw=-45.0,
        )
        self.assertEqual(d["bearing_word"], "ahead-right")
        self.assertIn("45° right", d["desc"])
        self.assertTrue(d["in_frame"])

    def test_dead_ahead_reads_directly_ahead(self):
        d = describe_entity(
            etype="minecraft:cow", ex=100.0, ey=64.0, ez=105.0,
            px=100.0, py=64.0, pz=100.0, pyaw=0.0,
        )
        self.assertIn("directly ahead", d["desc"])


if __name__ == "__main__":
    unittest.main()
