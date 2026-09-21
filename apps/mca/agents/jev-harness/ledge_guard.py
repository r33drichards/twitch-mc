"""Two reflexes that keep a player alive: don't step off, and clutch the fall.

Both are decisions about the next tenth of a second, which is why they live in
code rather than in a model call. A judgement that takes 300ms to come back is
not a reflex.
"""

# How close to the edge of a block counts as "at the edge".
EDGE = 0.25
# Falls shorter than this hurt too little to be worth interrupting.
DANGEROUS_FALL = 4.0
# How far above the ground to put the water, and to place a block.
WATER_AT = 3.0
BLOCK_AT = 1.8


def should_sneak(reading):
    """Whether to hold shift: near an edge with nothing on the other side.

    `fx`/`fz` are the player's position within their block, 0..1, and `ground`
    says whether there is something to stand on one block out in each compass
    direction. Sneaking in mid-air does nothing, so it is only for the grounded.
    """
    if not reading.get("on_ground", True):
        return False
    ground = reading.get("ground") or {}
    fx = float(reading.get("fx", 0.5))
    fz = float(reading.get("fz", 0.5))
    near = (
        ("north", fz < EDGE),
        ("south", fz > 1 - EDGE),
        ("west", fx < EDGE),
        ("east", fx > 1 - EDGE),
    )
    return any(close and not ground.get(side, True) for side, close in near)


def clutch_move(reading):
    """What to do about a fall in progress: "water", "block", or nothing yet.

    Waits until the ground is close, because water placed twenty blocks up
    lands nowhere near you.
    """
    if reading.get("on_ground", False):
        return None
    if float(reading.get("vy", 0.0)) >= 0:
        return None
    if float(reading.get("fallen", 0.0)) < DANGEROUS_FALL:
        return None
    to_ground = float(reading.get("to_ground", 999))
    if reading.get("has_water") and to_ground <= WATER_AT:
        return "water"
    if reading.get("has_blocks") and to_ground <= BLOCK_AT:
        return "block"
    return None
