"""A short walk across the platform, around whatever is in the way.

Breadth-first over standable blocks. The farm is a few dozen blocks across and
one scan sees all of it, so there is nothing to gain from anything cleverer:
BFS returns the shortest route in steps and cannot loop.

A block lookup is any callable `(x, y, z) -> block id`, which lets the search be
tested against a dictionary and run against the game unchanged.
"""
from collections import deque

# Blocks you can stand inside. Matched exactly or by suffix, never by
# substring: "bamboo" as a substring makes bamboo_planks — the farm's floor —
# look like empty air, and every route search then fails.
PASSABLE_NAMES = frozenset((
    "air", "cave_air", "void_air", "water", "bubble_column", "light",
    "grass", "short_grass", "tall_grass", "fern", "large_fern", "seagrass",
    "torch", "wall_torch", "soul_torch", "soul_wall_torch", "redstone_torch",
    "lever", "vine", "ladder", "cobweb", "tripwire", "redstone_wire",
    "bamboo", "bamboo_sapling", "sugar_cane", "kelp", "kelp_plant",
    "snow", "rail", "powered_rail", "detector_rail", "activator_rail",
    "poppy", "dandelion", "cornflower", "torchflower", "crimson_roots",
    "warped_roots", "nether_sprouts", "fire", "soul_fire",
))
PASSABLE_SUFFIXES = (
    "_carpet", "_button", "_pressure_plate", "_sign", "_wall_sign",
    "_hanging_sign", "_banner", "_sapling", "_rail", "_torch", "_tulip",
    "_orchid", "_bluet", "_daisy", "_flower", "_fungus", "_roots",
)
# Never step here even though you could.
DEADLY = ("lava", "fire", "magma_block", "campfire", "cactus", "sweet_berry")

MAX_NODES = 4000
MAX_STEP_UP = 1
MAX_DROP = 3


def _name(block_id):
    return str(block_id or "").split(":")[-1]


def is_passable(block_id):
    name = _name(block_id)
    if name in PASSABLE_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in PASSABLE_SUFFIXES)


def is_deadly(block_id):
    name = _name(block_id)
    return any(d in name for d in DEADLY)


def is_standable(look, x, y, z):
    """Feet and head clear, something solid underfoot, nothing lethal."""
    feet, head, below = look(x, y, z), look(x, y + 1, z), look(x, y - 1, z)
    if is_deadly(feet) or is_deadly(below):
        return False
    if not is_passable(feet) or not is_passable(head):
        return False
    return not is_passable(below)


def _neighbours(look, node):
    x, y, z = node
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, nz = x + dx, z + dz
        # Level, then a step up, then a drop — nearest height first.
        for ny in [y] + [y + up for up in range(1, MAX_STEP_UP + 1)] + \
                  [y - down for down in range(1, MAX_DROP + 1)]:
            if is_standable(look, nx, ny, nz):
                if ny > y and not is_passable(look(x, y + 2, z)):
                    break          # no room over your own head to climb
                yield (nx, ny, nz)
                break


def find_path(look, start, goal, max_nodes=MAX_NODES):
    """The shortest standable route from `start` to `goal`, or None.

    The returned list is the steps after `start`, ending on `goal`.
    """
    if start == goal:
        return []
    seen = {start: None}
    queue = deque([start])
    examined = 0
    while queue:
        node = queue.popleft()
        examined += 1
        if examined > max_nodes:
            return None
        for neighbour in _neighbours(look, node):
            if neighbour in seen:
                continue
            seen[neighbour] = node
            if neighbour == goal:
                path, step = [], neighbour
                while step != start:
                    path.append(step)
                    step = seen[step]
                return list(reversed(path))
            queue.append(neighbour)
    return None


def nearest_standable(look, goal, radius=3):
    """A block next to `goal` you can actually stand on, for solid targets."""
    gx, gy, gz = goal
    best = None
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            for dy in (0, 1, -1, 2, -2):
                x, y, z = gx + dx, gy + dy, gz + dz
                if not is_standable(look, x, y, z):
                    continue
                distance = abs(dx) + abs(dz) + abs(dy)
                if best is None or distance < best[0]:
                    best = (distance, (x, y, z))
    return best[1] if best else None
