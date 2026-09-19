"""Bearing and description math for the Jev harness.

Minecraft yaw: 0 = +Z (south), 90 = -X (west), 180 = -Z (north), -90 = +X
(east). Increasing yaw turns the player right, so a positive relative bearing
means the target lies to the RIGHT and negative means LEFT.

Code does the trig; the model reads the words.
"""
import math

# Horizontal field of view, in degrees, used to decide whether something is
# on screen. Vanilla's default 70 degree vertical FOV at 16:9 works out near
# 100 degrees horizontally.
HFOV_DEG = 100.0

# Within this many blocks of vertical offset, entities read as "level".
LEVEL_BLOCKS = 1.5


def normalize_deg(deg: float) -> float:
    """Fold an angle into (-180, 180], keeping exactly-behind positive."""
    d = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def relative_bearing(player_yaw: float, dx: float, dz: float) -> float:
    """Degrees the player must turn to face (dx, dz). Positive turns right."""
    absolute = math.degrees(math.atan2(-dx, dz))
    return normalize_deg(absolute - player_yaw)


def bearing_word(rel_yaw: float) -> str:
    """The eight-point word for a relative bearing."""
    a = abs(rel_yaw)
    side = "right" if rel_yaw > 0 else "left"
    if a <= 22.0:
        return "ahead"
    if a <= 67.0:
        return f"ahead-{side}"
    if a <= 112.0:
        return side
    if a <= 157.0:
        return f"behind-{side}"
    return "behind"


def vertical_word(dy: float) -> str:
    """How the vertical offset reads: level, N above, or N below."""
    if abs(dy) < LEVEL_BLOCKS:
        return "level"
    n = int(math.floor(abs(dy)))
    noun = "block" if n == 1 else "blocks"
    return f"{n} {noun} {'above' if dy > 0 else 'below'}"


def in_frame(rel_yaw: float, hfov: float = HFOV_DEG) -> bool:
    """True when the bearing falls inside the horizontal view cone.

    This is the cone only. It cannot tell a visible entity from one behind a
    wall; `api:canSee(id)` supplies that.
    """
    return abs(rel_yaw) <= hfov / 2.0


def bearing_phrase(rel_yaw: float) -> str:
    """The bearing as prose: 'directly ahead', 'ahead-right, 45° right'."""
    word = bearing_word(rel_yaw)
    if word == "ahead" and abs(rel_yaw) < 1.0:
        return "directly ahead"
    if word == "behind" and abs(rel_yaw) > 179.0:
        return "directly behind"
    side = "right" if rel_yaw > 0 else "left"
    return f"{word}, {abs(rel_yaw):.0f}° {side}"


def describe_entity(etype: str, ex: float, ey: float, ez: float,
                    px: float, py: float, pz: float, pyaw: float) -> dict:
    """Both representations of one entity: numbers and a phrase."""
    name = etype.split(":")[-1]
    dx, dy, dz = ex - px, ey - py, ez - pz
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    rel = relative_bearing(pyaw, dx, dz)
    vertical = vertical_word(dy)
    desc = (f"{name} at ({ex:.0f},{ey:.0f},{ez:.0f}) — {bearing_phrase(rel)}, "
            f"{dist:.1f}m away, {vertical}")
    return {
        "type": name,
        "x": ex, "y": ey, "z": ez,
        "dist": round(dist, 2),
        "rel_yaw": round(rel, 1),
        "dy": round(dy, 2),
        "bearing_word": bearing_word(rel),
        "in_frame": in_frame(rel),
        "desc": desc,
    }
