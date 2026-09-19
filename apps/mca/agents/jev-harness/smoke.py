#!/usr/bin/env python3
"""Dry run: assemble one state snapshot, ask Jev once, execute nothing.

    python3 smoke.py              # probes + Jev
    python3 smoke.py --no-jev     # probes only, no API call
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

from bridge import Bridge, BridgeDown
from geometry import describe_entity, bearing_phrase, vertical_word

PROBE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes")
MAX_DESCRIBED = 6


def compose_probes() -> str:
    """Concatenate probes/*.lua into one pcall-wrapped script."""
    parts = ["local S = {}",
             "local function probe(n, fn)",
             "  local ok, v = pcall(fn)",
             "  S[n] = ok and v or { error = tostring(v) }",
             "end"]
    for path in sorted(glob.glob(os.path.join(PROBE_DIR, "*.lua"))):
        name = os.path.basename(path)[:-4]
        body = open(path).read().rstrip()
        parts.append(f"probe({name!r}, function()\n{body}\nend)")
    parts.append("return S")
    return "\n".join(parts)


def build_state(bridge: Bridge) -> dict:
    raw = bridge.eval(compose_probes())
    me = raw.get("self", {})
    entities = raw.get("entities")
    if isinstance(entities, str):
        entities = json.loads(entities)
    entities = entities or []

    described = []
    for e in entities[:MAX_DESCRIBED]:
        d = describe_entity(etype=e["type"], ex=e["x"], ey=e["y"], ez=e["z"],
                            px=me["x"], py=me["y"], pz=me["z"], pyaw=me["yaw"])
        d["id"] = e["id"]
        d["hostile"] = e.get("hostile", False)
        if d["in_frame"]:
            # Cone says yes; ask the game whether a wall disagrees.
            d["in_frame"] = bool(bridge.eval(f"return api:canSee({e['id']})"))
            d["desc"] += "" if d["in_frame"] else " (out of sight)"
        described.append(d)

    hz = raw.get("hazard", {})
    return {
        "self": {**me, "desc": (f"{me.get('name')} at "
                                f"({me.get('x',0):.0f},{me.get('y',0):.0f},{me.get('z',0):.0f}), "
                                f"facing yaw {me.get('yaw',0):.0f}, "
                                f"{me.get('health')}/20 health, {me.get('food')}/20 food, "
                                f"holding {me.get('held')}")},
        "hazards": {**hz, "desc": (f"standing on {hz.get('block_under')}, "
                                   f"{hz.get('block_ahead')} directly ahead, "
                                   f"{hz.get('block_ahead_under')} underfoot ahead")},
        "in_frame": [d for d in described if d["in_frame"]],
        "out_of_frame": [d for d in described if not d["in_frame"]],
        "order": None,
        "captured_at": time.time(),
    }


def ask_jev(state: dict) -> dict:
    key = subprocess.check_output(
        ["security", "find-generic-password", "-s", "typesafe-api-key", "-w"]).decode().strip()
    candidates = {str(d["id"]): d["desc"] for d in state["in_frame"] + state["out_of_frame"]}
    candidates["none"] = "No entity is the right target right now."
    body = {"model": "jev-latest", "state": state, "questions": {
        "act": {"type": "choice",
                "instructions": "Pick the single next physical action for the bot, given `order` and the world state. With no order, the bot is idle.",
                "criteria": {
                    "advance": "Move forward toward the target.",
                    "retreat": "Back away from danger.",
                    "turn_toward": "Rotate to face the target before moving.",
                    "jump": "Jump, to clear an obstacle or unstick.",
                    "mine_front": "Break the block directly ahead.",
                    "place_block": "Place a block from the hotbar.",
                    "attack": "Attack the chosen target.",
                    "use_item": "Use the held item.",
                    "hold": "Do nothing this tick.",
                    "done": "The order is satisfied; clear it."}},
        "target": {"type": "choice",
                   "instructions": "Which entity the action applies to, if any.",
                   "criteria": candidates},
        "in_danger": {"type": "noul",
                      "instructions": "The bot is in immediate physical danger.",
                      "criteria": {"true": "A hostile is within 4 blocks, or health fell in the last 2 seconds.",
                                   "false": "No hostile within 4 blocks and health is steady."}},
    }}
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"},
                                 method="POST")
    t = time.time()
    answer = json.loads(urllib.request.urlopen(req, timeout=30).read())
    answer["_latency_s"] = round(time.time() - t, 3)
    return answer


def main() -> int:
    try:
        bridge = Bridge()
    except FileNotFoundError:
        print("no bridge config; has the 26.2 client ever run?")
        return 2
    print(f"bridge: 127.0.0.1:{bridge.port}")
    try:
        t = time.time()
        state = build_state(bridge)
    except BridgeDown as exc:
        print(f"BRIDGE DOWN — {exc}")
        print("Launch the 'mca-rcc (26.2)' profile and load a world, then re-run.")
        return 1
    print(f"state assembled in {time.time()-t:.3f}s\n")
    print(json.dumps(state, indent=2)[:2500])
    if "--no-jev" in sys.argv:
        return 0
    ans = ask_jev(state)
    print(f"\njev {ans['model']} in {ans['_latency_s']}s, "
          f"{ans['usage']['input_tokens']} input tokens")
    print(json.dumps(ans["answers"], indent=2))
    print("\n(dry run — nothing was executed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
