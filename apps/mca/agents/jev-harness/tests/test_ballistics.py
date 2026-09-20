"""Where to point so a thrown item arrives.

A snowball leaves the hand at 1.5 blocks a tick, loses 1% of its speed each
tick and falls 0.03 blocks a tick squared. Pointing straight at a piglin twenty
metres away lands the throw about two blocks short, which is why eighteen
snowballs in a row hit nothing.
"""
import unittest

from ballistics import launch_pitch, THROW_SPEED


class TestLaunchPitch(unittest.TestCase):
    def test_a_target_at_arms_length_needs_no_arc(self):
        self.assertAlmostEqual(launch_pitch(2.0, 0.0), 0.0, delta=3.0)

    def test_a_distant_target_is_aimed_above(self):
        # Negative pitch is upward in Minecraft.
        self.assertLess(launch_pitch(20.0, 0.0), -2.0)

    def test_further_means_higher(self):
        self.assertLess(launch_pitch(25.0, 0.0), launch_pitch(10.0, 0.0))

    def test_a_target_below_is_aimed_lower_than_one_level(self):
        self.assertGreater(launch_pitch(10.0, -3.0), launch_pitch(10.0, 0.0))

    def test_it_stays_inside_what_a_head_can_do(self):
        for dist in (1.0, 5.0, 20.0, 60.0):
            for dy in (-10.0, 0.0, 10.0):
                self.assertGreaterEqual(launch_pitch(dist, dy), -90.0)
                self.assertLessEqual(launch_pitch(dist, dy), 90.0)

    def test_the_speed_is_the_game_s_own(self):
        self.assertAlmostEqual(THROW_SPEED, 1.5, places=2)


class TestItActuallyLands(unittest.TestCase):
    """Simulate the flight and check it passes near the target."""

    def _miss_distance(self, dist, dy):
        import math
        from ballistics import DRAG, GRAVITY, THROW_SPEED
        pitch = math.radians(launch_pitch(dist, dy))
        vx, vy = THROW_SPEED * math.cos(pitch), THROW_SPEED * -math.sin(pitch)
        x = y = 0.0
        best = abs(dy)
        for _ in range(200):
            x += vx
            y += vy
            vy -= GRAVITY
            vx *= DRAG
            vy *= DRAG
            if x >= dist:
                return abs(y - dy)
            best = min(best, abs(y - dy)) if abs(x - dist) < 1.0 else best
        return best

    def test_it_lands_within_a_block_at_ten_metres(self):
        self.assertLess(self._miss_distance(10.0, 0.0), 1.0)

    def test_it_lands_within_a_block_at_twenty_metres(self):
        self.assertLess(self._miss_distance(20.0, 0.0), 1.0)

    def test_it_lands_within_a_block_when_the_target_is_lower(self):
        self.assertLess(self._miss_distance(18.0, -2.0), 1.0)


if __name__ == "__main__":
    unittest.main()
