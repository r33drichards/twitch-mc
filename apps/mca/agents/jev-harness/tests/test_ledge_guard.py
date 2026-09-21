"""Deciding when to crouch so a step does not become a fall.

Minecraft refuses to walk you off a block while you are sneaking, so the guard
is only ever a question of when to hold shift. It holds it when the player is
near the edge of what they are standing on and the ground beside them is gone.
"""
import unittest

from ledge_guard import should_sneak


def reading(x=0.5, z=0.5, on_ground=True, north=True, south=True, east=True, west=True):
    """Fractions within the block, and whether there is ground on each side."""
    return {"fx": x, "fz": z, "on_ground": on_ground,
            "ground": {"north": north, "south": south, "east": east, "west": west}}


class TestShouldSneak(unittest.TestCase):
    def test_the_middle_of_solid_ground_is_safe(self):
        self.assertFalse(should_sneak(reading()))

    def test_ground_missing_on_every_side_but_standing_central(self):
        # A one-block pillar: safe while centred, and this is Pillars of Fortune.
        self.assertFalse(should_sneak(reading(north=False, south=False,
                                              east=False, west=False)))

    def test_near_an_edge_with_nothing_beyond_it(self):
        # Drifted north (low z) with no ground to the north.
        self.assertTrue(should_sneak(reading(z=0.15, north=False)))

    def test_near_an_edge_that_has_ground_beyond_it(self):
        self.assertFalse(should_sneak(reading(z=0.15, north=True)))

    def test_the_far_edge_does_not_matter(self):
        # Drifted north, but the missing ground is south.
        self.assertFalse(should_sneak(reading(z=0.15, south=False)))

    def test_a_corner_counts_if_either_side_is_open(self):
        self.assertTrue(should_sneak(reading(x=0.12, z=0.12, west=False)))

    def test_it_does_not_crouch_in_mid_air(self):
        # Already falling or jumping: crouching achieves nothing.
        self.assertFalse(should_sneak(reading(z=0.05, north=False, on_ground=False)))

    def test_east_and_west_are_the_x_axis(self):
        self.assertTrue(should_sneak(reading(x=0.9, east=False)))
        self.assertTrue(should_sneak(reading(x=0.1, west=False)))


if __name__ == "__main__":
    unittest.main()


from ledge_guard import clutch_move, DANGEROUS_FALL, WATER_AT, BLOCK_AT


def falling(drop=10.0, to_ground=10.0, water=True, blocks=True, on_ground=False,
            vy=-0.8):
    return {"on_ground": on_ground, "fallen": drop, "to_ground": to_ground,
            "vy": vy, "has_water": water, "has_blocks": blocks}


class TestClutch(unittest.TestCase):
    def test_standing_still_clutches_nothing(self):
        self.assertIsNone(clutch_move(falling(on_ground=True, vy=0.0)))

    def test_a_short_hop_is_not_worth_saving(self):
        self.assertIsNone(clutch_move(falling(drop=1.5, to_ground=0.5)))

    def test_early_in_a_long_fall_it_waits(self):
        # Placing water twenty blocks up saves nothing.
        self.assertIsNone(clutch_move(falling(drop=4.0, to_ground=20.0)))

    def test_water_goes_in_just_before_landing(self):
        self.assertEqual(clutch_move(falling(to_ground=WATER_AT - 0.2)), "water")

    def test_blocks_are_the_fallback_when_there_is_no_water(self):
        self.assertEqual(clutch_move(falling(to_ground=BLOCK_AT - 0.2, water=False)),
                         "block")

    def test_with_neither_there_is_nothing_to_do(self):
        self.assertIsNone(clutch_move(falling(to_ground=1.0, water=False, blocks=False)))

    def test_rising_is_not_falling(self):
        self.assertIsNone(clutch_move(falling(vy=0.4, to_ground=1.0)))

    def test_the_threshold_for_bothering_is_a_dangerous_drop(self):
        self.assertIsNone(clutch_move(falling(drop=DANGEROUS_FALL - 0.5, to_ground=1.0)))
        self.assertIsNotNone(clutch_move(falling(drop=DANGEROUS_FALL + 0.5,
                                                 to_ground=WATER_AT - 0.2)))
