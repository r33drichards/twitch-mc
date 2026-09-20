"""Tick snapshots for the Jev harness: probe registry, rounding, descriptions.

Every tick ships one dict to the model, and that dict is the entire input
bill. Two rules shape it.

*Stable keys.* The same keys appear every tick, null rather than absent, so
question criteria can name a path like `in_frame[0].desc` and have it resolve
whether or not anything is standing there.

*Both representations.* Every spatial fact ships numeric and phrased. The
numbers are rounded to the precision the model and the dispatcher can act on
— a health of 18.92188262939453 spends eight tokens to say 18.9 — but neither
representation is dropped for the other.

Code senses and serves here. It does not judge.
"""
import glob
import json
import math
import os
import time

from geometry import describe_entity

PROBE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes")

# How many entities get described. entitiesJson sorts by distance, so this
# keeps the nearest.
MAX_DESCRIBED = 6

VANILLA = "minecraft:"

SELF_KEYS = ("x", "y", "z", "yaw", "pitch", "health", "food",
             "held", "slot", "dim", "name")
HAZARD_KEYS = ("block_under", "block_ahead", "block_ahead_under",
               "block_ahead_below2")


def compose_probes() -> str:
    """Concatenate probes/*.lua into one pcall-wrapped script."""
    parts = ["local S = {}",
             "local function probe(n, fn)",
             "  local ok, v = pcall(fn)",
             "  S[n] = ok and v or { error = tostring(v) }",
             "end"]
    for path in sorted(glob.glob(os.path.join(PROBE_DIR, "*.lua"))):
        name = os.path.basename(path)[:-4]
        with open(path) as fh:
            body = fh.read().rstrip()
        parts.append(f"probe({name!r}, function()\n{body}\nend)")
    parts.append("return S")
    return "\n".join(parts)


def _round(value, places):
    """Round, keeping None as None and returning an int at zero places."""
    if value is None or isinstance(value, bool):
        return value
    try:
        return int(round(float(value))) if places == 0 else round(float(value), places)
    except (TypeError, ValueError):
        return value


def _short(block):
    """Drop the vanilla namespace; keep any other so modded ids stay readable."""
    if isinstance(block, str) and block.startswith(VANILLA):
        return block[len(VANILLA):]
    return block


def _num(value):
    """A number as the shortest text that still reads right: 18.9, 20, -4."""
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _probe_error(field):
    """The error string a failed probe shipped, or None if it succeeded."""
    if isinstance(field, dict) and isinstance(field.get("error"), str):
        return field["error"]
    return None


def _build_self(me: dict) -> dict:
    out = {k: me.get(k) for k in SELF_KEYS}
    out["x"] = _round(out["x"], 2)
    out["y"] = _round(out["y"], 2)
    out["z"] = _round(out["z"], 2)
    out["yaw"] = _round(out["yaw"], 0)
    out["pitch"] = _round(out["pitch"], 0)
    out["health"] = _round(out["health"], 1)
    out["food"] = _round(out["food"], 0)
    out["held"] = _short(out["held"])
    out["dim"] = _short(out["dim"])
    if out["x"] is None or out["z"] is None:
        out["desc"] = None
        return out
    out["desc"] = (f"{out['name']} at "
                   f"({out['x']:.0f},{out['y'] or 0:.0f},{out['z']:.0f}), "
                   f"facing yaw {out['yaw']}, "
                   f"{_num(out['health'])}/20 health, "
                   f"{_num(out['food'])}/20 food, holding {out['held']}")
    return out


def _build_hazards(hz: dict) -> dict:
    out = {k: _short(hz.get(k)) for k in HAZARD_KEYS}
    if out["block_under"] is None and out["block_ahead"] is None:
        out["desc"] = None
        return out
    out["desc"] = (f"standing on {out['block_under']}, "
                   f"{out['block_ahead']} directly ahead, "
                   f"{out['block_ahead_under']} underfoot ahead")
    return out


def _parse_entities(raw):
    """entitiesJson hands back a JSON string; tolerate a parsed list too."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return raw if isinstance(raw, list) else []


def _describe(bridge, entities, me):
    """One dict per entity, split by what the player can actually see.

    Two fields from `describe_entity` are dropped as pure restatement:
    `bearing_word` is already a phrase inside `desc`, and a per-entity
    `in_frame` flag is already the list the entity landed in. The rest stay
    because something reads them — the dispatcher turns `x`/`y`/`z` into an
    absolute yaw and `rel_yaw` into a relative one, `attack` needs `id`, and
    `hostile` is the one fact `desc` never says.
    """
    in_frame, out_of_frame = [], []
    for e in entities[:MAX_DESCRIBED]:
        d = describe_entity(etype=e["type"], ex=e["x"], ey=e["y"], ez=e["z"],
                            px=me["x"], py=me["y"], pz=me["z"], pyaw=me["yaw"])
        visible = d["in_frame"]
        desc = d["desc"]
        if visible:
            # Cone says yes; ask the game whether a wall disagrees.
            visible = bool(bridge.eval(f"return api:canSee({e['id']})"))
            if not visible:
                desc += " (out of sight)"
        trimmed = {
            "id": e["id"],
            "type": d["type"],
            "x": _round(d["x"], 1),
            "y": _round(d["y"], 1),
            "z": _round(d["z"], 1),
            "dist": _round(d["dist"], 1),
            "rel_yaw": _round(d["rel_yaw"], 0),
            "dy": _round(d["dy"], 1),
            "hostile": bool(e.get("hostile", False)),
            "desc": desc,
        }
        (in_frame if visible else out_of_frame).append(trimmed)
    return in_frame, out_of_frame


def build_state(bridge, order=None) -> dict:
    """Assemble one tick snapshot from the probes plus the bridge's raycasts.

    `bridge` needs only `.eval(code)`. `order` is the standing order the
    harness holds, passed through untouched.
    """
    raw = bridge.eval(compose_probes())
    raw = raw if isinstance(raw, dict) else {}
    me_raw = raw.get("self")
    hz_raw = raw.get("hazard")
    ents_raw = raw.get("entities")

    me = _build_self(me_raw if isinstance(me_raw, dict) else {})
    hazards = _build_hazards(hz_raw if isinstance(hz_raw, dict) else {})

    in_frame, out_of_frame = [], []
    if None not in (me["x"], me["y"], me["z"], me["yaw"]):
        in_frame, out_of_frame = _describe(
            bridge, _parse_entities(ents_raw),
            {"x": me["x"], "y": me["y"], "z": me["z"], "yaw": me["yaw"]})

    return {
        "self": me,
        "hazards": hazards,
        "in_frame": in_frame,
        "out_of_frame": out_of_frame,
        "order": order,
        "errors": {"self": _probe_error(me_raw),
                   "hazards": _probe_error(hz_raw),
                   "entities": _probe_error(ents_raw)},
        "captured_at": round(time.time(), 1),
    }
