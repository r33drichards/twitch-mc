#!/usr/bin/env python3
"""Watch a game and record what happens to items. Touches nothing.

Run it while you play. It polls the inventory, writes one line per change, and
notes what you did with each thing that arrived: worn, moved to the hotbar,
held, eaten, dropped, or simply carried to the end. That record is the material
for writing the policy — what counts as useful here, and what is litter.

    python3 observe.py --label pillars-1
    python3 observe.py --label pillars-2 --seconds 900
"""
import argparse
import json
import os
import sys
import time

from bridge import Bridge, BridgeDown

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "observations")
ARMOR_SLOTS = range(36, 40)


def snapshot(bridge):
    stacks = json.loads(bridge.eval("return api:inventoryJson()"))
    me = bridge.eval("return {food=api:food(),health=api:health(),"
                     "slot=api:hotbarSlot(),held=api:heldItem(),"
                     "x=api:x(),y=api:y(),z=api:z()}")
    carried = {}
    where = {}
    for stack in stacks:
        item = stack.get("id")
        if not item or item == "minecraft:air":
            continue
        carried[item] = carried.get(item, 0) + stack.get("count", 1)
        where.setdefault(item, []).append(stack.get("slot"))
    return {"at": round(time.time(), 1), "carried": carried, "where": where,
            "held": me.get("held"), "slot": me.get("slot"),
            "food": me.get("food"), "health": round(float(me.get("health") or 0), 1),
            "pos": [round(me.get("x", 0), 1), round(me.get("y", 0), 1),
                    round(me.get("z", 0), 1)]}


def changes(before, after):
    """What moved between two looks."""
    events = []
    for item, count in (after["carried"]).items():
        gained = count - before["carried"].get(item, 0)
        if gained > 0:
            events.append({"event": "gained", "item": item, "count": gained})
    for item, count in (before["carried"]).items():
        lost = count - after["carried"].get(item, 0)
        if lost > 0:
            events.append({"event": "lost", "item": item, "count": lost})
    for item, slots in (after["where"]).items():
        was = set(before["where"].get(item, []))
        now = set(slots)
        if now - was:
            moved_to_armor = [s for s in now - was if s in ARMOR_SLOTS]
            moved_to_hotbar = [s for s in now - was if 0 <= s <= 8]
            if moved_to_armor:
                events.append({"event": "worn", "item": item, "slot": moved_to_armor[0]})
            elif moved_to_hotbar and was:
                events.append({"event": "to_hotbar", "item": item,
                               "slot": moved_to_hotbar[0]})
    if before["held"] != after["held"]:
        events.append({"event": "held", "item": after["held"]})
    if after["food"] < before["food"]:
        events.append({"event": "hunger_fell", "to": after["food"]})
    elif after["food"] > before["food"]:
        events.append({"event": "ate", "to": after["food"]})
    if after["health"] < before["health"] - 0.5:
        events.append({"event": "hurt", "to": after["health"]})
    return events


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="game")
    ap.add_argument("--seconds", type=int, default=1800)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{args.label}-{int(time.time())}.jsonl")
    bridge = Bridge()
    print(f"watching; writing {os.path.relpath(path, HERE)}   (ctrl-c to stop)")
    print("nothing is sent to the game — this only reads.\n")

    previous, started, seen = None, time.time(), 0
    with open(path, "a") as log:
        try:
            while time.time() - started < args.seconds:
                try:
                    now = snapshot(bridge)
                except BridgeDown:
                    time.sleep(2.0)
                    continue
                if previous is None:
                    log.write(json.dumps({"event": "start", **now}) + "\n")
                else:
                    for event in changes(previous, now):
                        seen += 1
                        row = {**event, "at": now["at"], "food": now["food"],
                               "health": now["health"], "held": now["held"]}
                        log.write(json.dumps(row) + "\n")
                        log.flush()
                        detail = event.get("item") or event.get("to")
                        print(f"  {event['event']:<12} {str(detail or '')[:34]:<34} "
                              f"food {now['food']:>2} hp {now['health']:>4}")
                previous = now
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    print(f"\n{seen} events written to {os.path.relpath(path, HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
