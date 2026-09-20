"""Finding a way across the platform instead of walking into it.

approach() faced the target and held forward, so a chest, a wall or a corner
ended the walk with "the way is not clear". The platform is small and fully
visible in one block scan, so a breadth-first search over standable blocks is
enough and always finds the shortest route.
"""
import unittest

from pathfinding import find_path, is_passable, is_standable


def world(solid, air_above=True):
    """A block lookup: everything in `solid` is stone, everything else air."""
    def look(x, y, z):
        if (x, y, z) in solid:
            return "minecraft:stone"
        return "minecraft:air"
    return look


FLOOR = {(x, 63, z) for x in range(-4, 6) for z in range(-4, 6)}


class TestStandable(unittest.TestCase):
    def test_a_block_of_air_over_solid_ground_is_standable(self):
        self.assertTrue(is_standable(world(FLOOR), 0, 64, 0))

    def test_air_over_air_is_not(self):
        self.assertFalse(is_standable(world(set()), 0, 64, 0))

    def test_solid_at_head_height_is_not(self):
        solid = set(FLOOR) | {(0, 65, 0)}
        self.assertFalse(is_standable(world(solid), 0, 64, 0))

    def test_solid_where_the_feet_go_is_not(self):
        solid = set(FLOOR) | {(0, 64, 0)}
        self.assertFalse(is_standable(world(solid), 0, 64, 0))


class TestFindPath(unittest.TestCase):
    def test_a_straight_walk(self):
        path = find_path(world(FLOOR), (0, 64, 0), (3, 64, 0))
        self.assertIsNotNone(path)
        self.assertEqual(path[-1], (3, 64, 0))
        self.assertEqual(len(path), 3)

    def test_standing_on_the_target_needs_no_steps(self):
        self.assertEqual(find_path(world(FLOOR), (0, 64, 0), (0, 64, 0)), [])

    def test_it_goes_around_a_wall(self):
        # Two blocks tall, so it cannot simply be stepped onto.
        wall = set(FLOOR) | {(1, y, z) for y in (64, 65) for z in range(-1, 3)}
        path = find_path(world(wall), (0, 64, 0), (2, 64, 0))
        self.assertIsNotNone(path)
        for step in path:
            self.assertNotIn(step, wall)
        self.assertEqual(path[-1], (2, 64, 0))
        self.assertGreater(len(path), 2, "going around must be longer than through")

    def test_it_can_step_up_one_block(self):
        raised = set(FLOOR) | {(2, 64, 0)}
        path = find_path(world(raised), (0, 64, 0), (2, 65, 0))
        self.assertIsNotNone(path)
        self.assertEqual(path[-1], (2, 65, 0))

    def test_it_gives_up_on_an_unreachable_target(self):
        island = set(FLOOR) | {(1, 64, z) for z in range(-4, 6)} | {(1, 65, z) for z in range(-4, 6)}
        self.assertIsNone(find_path(world(island), (0, 64, 0), (3, 64, 0)))

    def test_the_search_is_bounded(self):
        # An open plain must not be walked forever.
        def everything_solid_below(x, y, z):
            return "minecraft:stone" if y == 63 else "minecraft:air"
        self.assertIsNone(find_path(everything_solid_below, (0, 64, 0), (500, 64, 0),
                                    max_nodes=500))


if __name__ == "__main__":
    unittest.main()


class TestPassableIsNotSubstringMatching(unittest.TestCase):
    """`bamboo` must not make `bamboo_planks` walkable.

    Matching block names by substring turned the farm's whole bamboo floor into
    air as far as the search was concerned, so every route came back None and
    the walk fell back to blundering forwards.
    """

    def test_floors_are_solid(self):
        for solid in ("minecraft:bamboo_planks", "minecraft:bamboo_slab",
                      "minecraft:bamboo_mosaic", "minecraft:oak_planks",
                      "minecraft:chest", "minecraft:bamboo_trapdoor",
                      "minecraft:stone", "minecraft:bamboo_block"):
            self.assertFalse(is_passable(solid), solid)

    def test_things_you_can_walk_through_are_passable(self):
        for open_block in ("minecraft:air", "minecraft:cave_air", "minecraft:torch",
                           "minecraft:wall_torch", "minecraft:white_carpet",
                           "minecraft:bamboo", "minecraft:oak_button",
                           "minecraft:rail", "minecraft:water", "minecraft:vine"):
            self.assertTrue(is_passable(open_block), open_block)

    def test_a_bamboo_floor_is_standable_ground(self):
        floor = {(0, 63, 0): "minecraft:bamboo_planks"}
        look = lambda x, y, z: floor.get((x, y, z), "minecraft:air")
        self.assertTrue(is_standable(look, 0, 64, 0))
