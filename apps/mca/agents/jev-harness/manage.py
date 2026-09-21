#!/usr/bin/env python3
"""Inventory management: one decision per arriving item.

The state is the pack, the hotbar, what is worn, hunger, and what just turned
up. For each arrival Jev picks one of a handful of things to do with it, and
the action that follows is deterministic. Nothing here moves or fights.

    python3 manage.py --dry-run     # decide and print, touch nothing
    python3 manage.py               # live
"""
import argparse
import json
import os
import sys
import time

import jev
from bridge import Bridge, BridgeDown
from inventory_actions import InventoryActions, InventoryError
from inventory_state import InventoryWatcher, build_state

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY = os.path.join(HERE, "orders", "pillars_inventory.txt")
LOG = os.path.join(HERE, "observations", "decisions.jsonl")

CHOICES = {
    "hotbar_weapon": "It is a weapon and better than what is in slot 1: put it there.",
    "hotbar_blocks": "It is a block that stacks and places solidly, for building "
                     "and bridging: put it in slot 2.",
    "hotbar_food": "It is food: put it in slot 3.",
    "wear": "It is armour and better than what is worn: put it on.",
    "eat": "Food is needed now, not later: eat it.",
    "keep": "Worth carrying, but nothing needs doing with it right now.",
    "drop": "Litter: throw it away to keep space free.",
}
SLOT_FOR = {"hotbar_weapon": 0, "hotbar_blocks": 1, "hotbar_food": 2}


def decide(state, item, policy):
    """One typed judgement about one item."""
    question = {
        "what_to_do": {
            "type": "choice",
            "instructions": (
                f"A {item['name']} just arrived. What should be done with it, given "
                f"the policy in `order` and what is already carried?"),
            "criteria": dict(CHOICES),
        },
        "hungry": {
            "type": "noul",
            "instructions": "The player should eat something soon.",
            "criteria": {"true": "Food is 16 or below out of 20.",
                         "false": "Food is above 16."},
        },
    }
    answer = jev.ask({**state, "order": policy, "deciding_about": item}, question)
    act = answer["answers"]["what_to_do"]
    return (act["choice"], act.get("confidence"),
            answer["answers"]["hungry"]["noul"], answer["usage"]["input_tokens"])


def carry_out(actions, choice, item, dry_run):
    if dry_run:
        return {"ok": None, "did": f"would {choice}", "changed": False}
    if choice == "drop":
        return actions.drop(item["id"])
    if choice == "wear":
        return actions.wear(item["id"])
    if choice == "eat":
        return actions.eat(item["id"])
    if choice in SLOT_FOR:
        return actions.to_hotbar(item["id"], SLOT_FOR[choice])
    return actions.keep(item["id"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--policy", default=POLICY)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--eat-below", type=int, default=16)
    args = ap.parse_args()

    policy = open(args.policy).read()
    bridge = Bridge()
    actions = InventoryActions(bridge)
    watcher = InventoryWatcher()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    decided = 0

    print(f"managing inventory{' (dry run)' if args.dry_run else ''}   ctrl-c to stop")
    try:
        while True:
            started = time.monotonic()
            try:
                state = build_state(bridge, watcher)
            except (BridgeDown, RuntimeError):
                time.sleep(2.0)
                continue

            for item in state.get("newest") or []:
                choice, confidence, hungry, tokens = decide(state, item, policy)
                try:
                    result = carry_out(actions, choice, item, args.dry_run)
                    note = result.get("did")
                except InventoryError as exc:
                    result, note = {"ok": False, "error": str(exc)}, f"failed: {exc}"
                decided += 1
                print(f"  {item['name']:<26} -> {choice:<14} ({confidence}) {note}")
                with open(LOG, "a") as log:
                    log.write(json.dumps({
                        "at": time.time(), "item": item["id"], "choice": choice,
                        "confidence": confidence, "hungry": hungry,
                        "tokens": tokens, "result": result,
                        "dry_run": args.dry_run}) + "\n")
            watcher.clear()

            # Hunger is a standing condition rather than an arrival.
            if (state.get("food") or 20) <= args.eat_below and not args.dry_run:
                food = next((row["id"] for row in state["hotbar"] + state["pack"]
                             if "bread" in row["id"] or "cooked" in row["id"]
                             or "apple" in row["id"] or "carrot" in row["id"]), None)
                if food:
                    print(f"  hungry ({state['food']}/20) -> eating {food.split(':')[-1]}")
                    try:
                        actions.eat(food)
                    except InventoryError:
                        pass

            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    print(f"\n{decided} decisions made")
    return 0


if __name__ == "__main__":
    sys.exit(main())
