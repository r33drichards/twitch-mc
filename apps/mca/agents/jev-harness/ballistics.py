"""Where to point so a thrown item arrives.

Minecraft throws a snowball at 1.5 blocks per tick. Each tick it loses one
percent of its speed to drag and 0.03 blocks per tick to gravity, so a throw
aimed straight at something twenty blocks away lands roughly two blocks short.
Pointing at the target is not aiming at it.

The pitch is found by simulating the flight rather than solving it: drag makes
the closed form ugly, and a search over half-degree steps is exact enough to
hit a mob and costs nothing.
"""
import math

THROW_SPEED = 1.5      # blocks per tick, the same for snowballs and eggs
GRAVITY = 0.03         # blocks per tick squared
DRAG = 0.99            # speed retained each tick
MAX_TICKS = 200


def _height_at(distance, pitch_deg):
    """How high the projectile is when it has travelled `distance` horizontally."""
    pitch = math.radians(pitch_deg)
    vx = THROW_SPEED * math.cos(pitch)
    vy = THROW_SPEED * -math.sin(pitch)   # negative pitch is upward
    x = y = 0.0
    for _ in range(MAX_TICKS):
        if vx <= 1e-6:
            return None
        previous_x, previous_y = x, y
        x += vx
        y += vy
        vy -= GRAVITY
        vx *= DRAG
        vy *= DRAG
        if x >= distance:
            # Linear interpolation across the tick it crosses the target plane.
            span = x - previous_x
            if span <= 0:
                return y
            t = (distance - previous_x) / span
            return previous_y + (y - previous_y) * t
    return None


def launch_pitch(distance, dy, step=0.5):
    """The head angle that puts a thrown item at (`distance`, `dy`).

    `dy` is how far above the thrower's eyes the target sits — negative when it
    is lower. Returns Minecraft pitch, where negative is upward.
    """
    distance = max(0.1, float(distance))
    best_pitch, best_miss = 0.0, float("inf")
    angle = -89.0
    while angle <= 89.0:
        height = _height_at(distance, angle)
        if height is not None:
            miss = abs(height - dy)
            if miss < best_miss:
                best_pitch, best_miss = angle, miss
        angle += step
    return round(best_pitch, 1)
