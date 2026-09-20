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
import re
import time

from geometry import bearing_phrase, describe_entity, relative_bearing, vertical_word

PROBE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes")

# How many entities get described. entitiesJson sorts by distance, so this
# keeps the nearest.
MAX_DESCRIBED = 6

VANILLA = "minecraft:"

# The player's main inventory: 36 slots, of which 0-8 are the hotbar. Equipped
# armour lives outside it and `inventoryJson` cannot see it.
INVENTORY_SLOTS = 36
HOTBAR_SLOTS = 9

# How many distinct item ids `counts` names before it stops. `kinds` always
# reports the true total, so a truncated list is visible rather than silent.
MAX_ITEM_KINDS = 20

# Blocks the player can interact with. Stripped of the vanilla namespace, as
# everything else here is. Dyed shulker boxes are matched by suffix.
STATION_IDS = frozenset((
    "crafting_table", "furnace", "blast_furnace", "smoker",
    "chest", "trapped_chest", "barrel", "ender_chest",
    "anvil", "chipped_anvil", "damaged_anvil",
    "hopper", "shulker_box", "brewing_stand",
))

# `world.blocks_around` scans a cube of this radius (its own ceiling is 8).
STATION_RADIUS = 5

# The vanilla block interaction range, measured eye to block. This is an engine
# constant, not advice: `in_reach` says the game would accept a click, and says
# nothing about whether clicking is a good idea.
REACH_BLOCKS = 4.5
EYE_HEIGHT = 1.62

# A wall of 19 chests is one fact repeated 19 times. Keep the nearest few of
# each kind and count the rest; both numbers ship.
MAX_STATIONS = 10
# Craftable results offered as targets; the recipe book lists hundreds.
MAX_CRAFTABLE = 8
MAX_PER_KIND = 2

# A double chest is 54 slots. Ship the first few and roll the rest into
# `counts`, so nothing becomes invisible.
MAX_CONTAINER_SLOTS = 27

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


def _parse_json_array(raw):
    """A probe's JSON-string array as a list, or None when it is not one.

    None and [] are different answers: an empty inventory is a fact, a probe
    that failed is not, and the two must not render the same.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, list) else None


def _parse_entities(raw):
    """entitiesJson hands back a JSON string; tolerate a parsed list too."""
    parsed = _parse_json_array(raw)
    return parsed if parsed is not None else []


def _plural(word):
    """`chest` -> `chests`, `shulker_box` -> `shulker_boxes`."""
    return word + ("es" if word.endswith(("s", "x", "ch", "sh")) else "s")


def _stack(entry):
    """One inventory or container slot, trimmed to what a click needs.

    `enchants` is dropped: a sword's seven enchantment ids cost more than
    every other item in the inventory put together, and no verb reads them.
    """
    out = {"slot": entry.get("slot"),
           "id": _short(entry.get("id")),
           "count": entry.get("count")}
    if entry.get("durability") is not None:
        out["durability"] = entry["durability"]
        out["max_durability"] = entry.get("maxDurability")
    return out


def _count_phrase(counts, limit=6):
    """`34 gold_nugget, 24 rotten_flesh` — the biggest stacks first."""
    items = list(counts.items())[:limit]
    tail = len(counts) - len(items)
    text = ", ".join(f"{n} {name}" for name, n in items)
    return text + (f", +{tail} more kinds" if tail > 0 else "")


def _build_inventory(raw, held_slot) -> dict:
    """What the bot carries: totals, hotbar layout, wear, and a sentence."""
    out = {"counts": None, "kinds": None, "free_slots": None,
           "hotbar": None, "held": None, "tools": None, "desc": None}
    items = _parse_json_array(raw)
    if items is None:
        return out

    stacks = [_stack(e) for e in items if isinstance(e, dict)]
    totals = {}
    for s in stacks:
        totals[s["id"]] = totals.get(s["id"], 0) + (s["count"] or 0)
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))

    out["kinds"] = len(totals)
    out["counts"] = dict(ranked[:MAX_ITEM_KINDS])
    out["free_slots"] = INVENTORY_SLOTS - len(stacks)
    out["hotbar"] = [s for s in stacks
                     if isinstance(s["slot"], int) and s["slot"] < HOTBAR_SLOTS]
    out["tools"] = [s for s in stacks
                    if "durability" in s
                    and isinstance(s["slot"], int) and s["slot"] >= HOTBAR_SLOTS]
    out["held"] = next((s for s in out["hotbar"] if s["slot"] == held_slot), None)

    held = out["held"]
    if held is None:
        holding = "holding nothing"
    elif "durability" in held:
        holding = (f"holding {held['id']} "
                   f"({held['durability']}/{held['max_durability']})")
    else:
        holding = f"holding {held['id']}"
    hotbar = ", ".join(f"{s['slot']} {s['id']}" for s in out["hotbar"])
    if not stacks:
        out["desc"] = (f"{holding}, carrying nothing, "
                       f"{INVENTORY_SLOTS} free slots")
        return out
    out["desc"] = (f"{holding}; {_count_phrase(out['counts'])}; "
                   f"hotbar {hotbar or 'empty'}; "
                   f"{out['free_slots']} free slots")
    return out


def _is_station(block_id):
    return isinstance(block_id, str) and (
        block_id in STATION_IDS or block_id.endswith("_shulker_box"))


def _build_stations(blocks, me) -> dict:
    """Interactable blocks around the player, nearest first.

    Distance is eye to block centre, which is what the game measures when it
    decides whether a click lands. `in_reach` is that comparison and nothing
    more — which station to walk to is not a question this module answers.
    """
    out = {"near": None, "more": None, "desc": None}
    if blocks is None or me["x"] is None or me["yaw"] is None:
        return out

    ex, ey, ez = me["x"], (me["y"] or 0) + EYE_HEIGHT, me["z"]
    found = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        sid = _short(b.get("id"))
        if not _is_station(sid):
            continue
        try:
            bx, by, bz = int(b["x"]), int(b["y"]), int(b["z"])
        except (KeyError, TypeError, ValueError):
            continue
        dx, dy, dz = bx + 0.5 - ex, by + 0.5 - ey, bz + 0.5 - ez
        dist = round(math.sqrt(dx * dx + dy * dy + dz * dz), 1)
        found.append((dist, sid, bx, by, bz,
                      relative_bearing(me["yaw"], dx, dz),
                      by + 0.5 - (me["y"] or 0)))
    found.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4]))

    near, more, shown = [], {}, {}
    for dist, sid, bx, by, bz, rel, dy in found:
        shown[sid] = shown.get(sid, 0) + 1
        if shown[sid] > MAX_PER_KIND or len(near) >= MAX_STATIONS:
            more[sid] = more.get(sid, 0) + 1
            continue
        reach = dist <= REACH_BLOCKS
        vertical = vertical_word(dy)
        desc = (f"{sid} at ({bx},{by},{bz}) — {bearing_phrase(rel)}, {dist}m"
                + ("" if vertical == "level" else f", {vertical}")
                + (", in reach" if reach else ", out of reach"))
        # No `dy`: `y` is right here and the phrase already says "1 block
        # above". Entities keep theirs only because the dispatcher aims a pitch
        # with it; nothing aims at a station that way.
        near.append({"id": sid, "x": bx, "y": by, "z": bz, "dist": dist,
                     "rel_yaw": _round(rel, 0), "in_reach": reach,
                     "desc": desc})

    out["near"], out["more"] = near, more
    if not near and not more:
        out["desc"] = f"no interactable blocks within {STATION_RADIUS} blocks"
        return out
    # The roll-up counts; it does not repeat. Every listed station already
    # carries its own phrase, so restating nine of them here would bill the
    # same sentence twice.
    total = len(near) + sum(more.values())
    reachable = sum(1 for s in near if s["in_reach"])
    parts = [f"{total} interactable block{'' if total == 1 else 's'} within "
             f"{STATION_RADIUS} blocks, nearest {len(near)} listed, "
             f"{reachable} in reach"]
    if more:
        parts.append(", ".join(f"{n} more {_plural(k)}"
                               for k, n in sorted(more.items())))
    out["desc"] = "; ".join(parts)
    return out


def _screen_name(raw):
    """`FurnaceScreen` -> `furnace`; the class name is the only label there is."""
    if not isinstance(raw, str) or not raw:
        return None
    name = raw[:-6] if raw.endswith("Screen") else raw
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() or "container"


def _build_looking_at(raw, me):
    """The block under the crosshair, which is where a thrown item goes.

    A player can see this; without it the bot cannot tell aiming at a mob from
    aiming at the floor two blocks in front of it.
    """
    if not isinstance(raw, dict) or raw.get("type") != "BLOCK":
        return {"id": None, "desc": "looking at nothing within reach"}
    name = str(raw.get("id", "")).split(":")[-1]
    dist = None
    if None not in (me.get("x"), me.get("y"), me.get("z")):
        dist = round(math.dist([me["x"], me["y"] + 1.62, me["z"]],
                               [raw.get("x", 0) + 0.5, raw.get("y", 0) + 0.5,
                                raw.get("z", 0) + 0.5]), 1)
    where = f", {dist}m away" if dist is not None else ""
    return {"id": name, "x": raw.get("x"), "y": raw.get("y"), "z": raw.get("z"),
            "dist": dist, "desc": f"looking at {name}{where}"}


def _build_container(raw):
    """The open container screen, or None when nothing is open.

    `container.state` lists only non-empty slots, so the boundary between the
    container and the player inventory is reported as the lowest *occupied*
    player slot rather than the menu's true split point.
    """
    if not isinstance(raw, dict) or not raw.get("open"):
        return None
    own = [_stack(s) for s in (raw.get("containerSlots") or [])
           if isinstance(s, dict)]
    player = [_stack(s) for s in (raw.get("playerSlots") or [])
              if isinstance(s, dict)]
    counts = {}
    for s in own:
        counts[s["id"]] = counts.get(s["id"], 0) + (s["count"] or 0)
    counts = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    first_player = min((s["slot"] for s in player
                        if isinstance(s["slot"], int)), default=None)

    screen = _screen_name(raw.get("screen")) or "container"
    inside = (f"{len(own)} slot{'' if len(own) == 1 else 's'} used: "
              f"{_count_phrase(counts)}") if own else "empty"
    where = (f"; player inventory from menu slot {first_player}"
             if first_player is not None else "")
    return {
        "screen": screen,
        "slots": own[:MAX_CONTAINER_SLOTS],
        "more_slots": max(0, len(own) - MAX_CONTAINER_SLOTS),
        "counts": counts,
        "player_slots": player[:MAX_CONTAINER_SLOTS],
        "first_player_slot": first_player,
        "desc": f"{screen} open — {inside}{where}",
    }


def _read_rpc(bridge, method, params=None):
    """One bridge RPC as (value, error). A failure degrades one field.

    The probe registry cannot reach these: `world.blocks_around` has no
    ScriptApi twin, and `containerJson()` returns `[]` for both an empty chest
    and no chest at all, so only `container.state` carries the open bit.
    """
    call = getattr(bridge, "rpc", None)
    if not callable(call):
        return None, f"bridge cannot call {method}"
    try:
        return call(method, params or {}), None
    except Exception as exc:  # a dead RPC is data, not a crashed tick
        return None, f"{type(exc).__name__}: {exc}"


def _in_view_cone(e, me):
    """Whether the bearing alone puts this entity on screen."""
    d = describe_entity(etype=e.get("type", "?"), ex=e.get("x", 0.0), ey=e.get("y", 0.0),
                        ez=e.get("z", 0.0), px=me["x"], py=me["y"], pz=me["z"],
                        pyaw=me["yaw"])
    return d["in_frame"]


def _batch_can_see(bridge, ids):
    """`api:canSee` for several entities in a single eval."""
    if not ids:
        return {}
    body = ", ".join(f"[{int(i)}]=api:canSee({int(i)})" for i in ids)
    try:
        got = bridge.eval("return {" + body + "}")
    except Exception:  # noqa: BLE001 - a failed read is not a decision
        return {}
    if not isinstance(got, dict):
        return {}
    return {int(k): bool(v) for k, v in got.items()}


def _describe(bridge, entities, me):
    """One dict per entity, split by what the player can actually see.

    Two fields from `describe_entity` are dropped as pure restatement:
    `bearing_word` is already a phrase inside `desc`, and a per-entity
    `in_frame` flag is already the list the entity landed in. The rest stay
    because something reads them — the dispatcher turns `x`/`y`/`z` into an
    absolute yaw and `rel_yaw` into a relative one, `attack` needs `id`, and
    `hostile` is the one fact `desc` never says.

    `hostile`, `aggressive` and `facing_me` are three different facts and are
    never merged: class membership, a running attack goal, and where the head
    points. The last two also reach `desc`, because a standing order can turn
    on "if it is not already aggressive" and a phrase-only reader would
    otherwise be blind to it. A client that has not been told either bit
    reports null, and null says nothing in the phrase rather than "no".
    """
    # One call for every visibility check, rather than one call each. Each eval
    # blocks Minecraft's client thread, so a tick that asked about six entities
    # stalled rendering six separate times.
    cone_ids = [e["id"] for e in entities
                if _in_view_cone(e, me)][:MAX_DESCRIBED]
    sight = _batch_can_see(bridge, cone_ids)
    in_frame, out_of_frame = [], []
    for e in entities[:MAX_DESCRIBED]:
        d = describe_entity(etype=e["type"], ex=e["x"], ey=e["y"], ez=e["z"],
                            px=me["x"], py=me["y"], pz=me["z"], pyaw=me["yaw"])
        visible = d["in_frame"]
        desc = d["desc"]
        if visible:
            # Cone says yes; the batched sight check says whether a wall disagrees.
            visible = bool(sight.get(e["id"]))
            if not visible:
                desc += " (out of sight)"
        aggressive = e.get("aggressive")
        facing = e.get("facing_me")
        if aggressive is not None:
            desc += ", aggressive" if aggressive else ", not aggressive"
        if facing is not None:
            desc += ", facing you" if facing else ", facing away"
        # How hurt it is, so an attack that changes nothing is visible as such.
        health = e.get("health")
        if health is not None:
            desc += f", {_round(health, 1)} hp"
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
            "aggressive": None if aggressive is None else bool(aggressive),
            "facing_me": None if facing is None else bool(facing),
            "desc": desc,
        }
        (in_frame if visible else out_of_frame).append(trimmed)
    return in_frame, out_of_frame


def _build_craftable(payload):
    """Results the recipe book says are makeable right now, one row per result.

    The book lists several recipes for the same item (an ingot from nuggets and
    an ingot from a block); the model picks a result, not a recipe, so they
    collapse to one entry carrying the largest yield.
    """
    rows = (payload or {}).get("recipes") if isinstance(payload, dict) else None
    if not rows:
        return []
    best = {}
    for row in rows:
        result = row.get("result")
        if not result:
            continue
        count = row.get("count") or 1
        if result not in best or count > best[result]:
            best[result] = count
    out = []
    for result, count in list(best.items())[:MAX_CRAFTABLE]:
        name = result.split(":")[-1]
        out.append({"result": result, "count": count,
                    "desc": f"craft {name}" + (f", {count} at a time" if count > 1 else "")})
    return out


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
    inv_raw = raw.get("inventory")

    me = _build_self(me_raw if isinstance(me_raw, dict) else {})
    hazards = _build_hazards(hz_raw if isinstance(hz_raw, dict) else {})
    inventory = _build_inventory(inv_raw, me["slot"])

    in_frame, out_of_frame = [], []
    if None not in (me["x"], me["y"], me["z"], me["yaw"]):
        in_frame, out_of_frame = _describe(
            bridge, _parse_entities(ents_raw),
            {"x": me["x"], "y": me["y"], "z": me["z"], "yaw": me["yaw"]})

    scan, scan_err = _read_rpc(bridge, "world.blocks_around",
                               {"radius": STATION_RADIUS})
    blocks = scan.get("blocks") if isinstance(scan, dict) else None
    stations = _build_stations(blocks, me)
    if blocks is None and scan_err is None:
        scan_err = "world.blocks_around returned no blocks"

    crosshair, _ = _read_rpc(bridge, "world.raycast", {"max": 6.0})

    screen, screen_err = _read_rpc(bridge, "container.state")

    recipes, recipes_err = _read_rpc(bridge, "craft.recipes", {"craftable_only": True})

    return {
        "self": me,
        "hazards": hazards,
        "in_frame": in_frame,
        "out_of_frame": out_of_frame,
        "inventory": inventory,
        "stations": stations,
        "container": _build_container(screen),
        "looking_at": _build_looking_at(crosshair, me),
        "craftable": _build_craftable(recipes),
        "order": order,
        "errors": {"self": _probe_error(me_raw),
                   "hazards": _probe_error(hz_raw),
                   "entities": _probe_error(ents_raw),
                   "inventory": _probe_error(inv_raw),
                   "stations": scan_err,
                   "container": screen_err,
                   "craftable": recipes_err},
        "captured_at": round(time.time(), 1),
    }
