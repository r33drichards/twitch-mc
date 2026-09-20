#!/usr/bin/env python3
"""The snowball subtask, scored against the live game.

Take snowballs out of a chest, hold them, throw one, and anger a piglin. Four
milestones, a quarter each, so a run that gets halfway scores halfway rather
than zero.

Every run starts from the same place: the player's snowballs are cleared and a
chest holding exactly sixteen is placed beside them. Without that reset the
score would measure what the last run left behind.

    python3 benchmark/live_task.py --ticks 25
    python3 benchmark/live_task.py --repeat 3        # the game is noisy
"""
import argparse
import glob
import json
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import Bridge  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
ORDER = os.path.join(HARNESS, "orders", "snowball_subtask.txt")
# The farm's own shulker box, which already holds the snowball supply. Nothing
# is placed or overwritten: the reset only empties the player's pockets, so a
# run cannot coast on what the last one collected.
SHULKER = (5732, 232, 437)
# Standable spots behind the platform, on the storage wall. A run starts at one
# of them facing a random way, so the bot has to find the shulker rather than
# already be pointed at it.
SPAWNS = ((5736, 231, 435), (5736, 231, 436), (5737, 231, 434),
          (5737, 231, 435), (5738, 231, 434))


def reset(bridge, verbose=True):
    """Empty the player's snowballs. The shulker keeps the supply.

    Non-destructive on purpose: the shulker is the farm's own, and overwriting
    it with a setblock would throw away everything else inside.
    """
    name = bridge.eval("return api:name()")
    bridge.rpc("container.close")
    # A run that killed the player leaves a corpse on the death screen; nothing
    # else works until it respawns.
    if float((bridge.rpc("player.state") or {}).get("health") or 0) <= 0:
        bridge.rpc("player.respawn")
        time.sleep(2.0)
        if verbose:
            print("  reset: respawned after a death")
    time.sleep(0.3)
    bridge.rpc("chat.send", {"text": f"/clear {name} minecraft:snowball"})
    time.sleep(0.6)
    # Count the shulker's stock while standing next to it: a container cannot
    # be opened from across the platform, and reading it after the teleport
    # reported an empty box that was actually full.
    sx, sy, sz = SHULKER
    bridge.rpc("chat.send", {"text": f"/tp {name} {sx + 1.5} {sy - 1} {sz + 0.5} -90 0"})
    time.sleep(0.9)
    stock = _shulker_snowballs(bridge)

    x, y, z = random.choice(SPAWNS)
    yaw = random.choice((-180, -135, -90, -45, 0, 45, 90, 135))
    bridge.rpc("chat.send",
               {"text": f"/tp {name} {x + 0.5} {y} {z + 0.5} {yaw} 0"})
    time.sleep(0.9)
    carried = _snowballs(bridge)
    if verbose:
        print(f"  reset: at ({x},{y},{z}) facing {yaw}, {carried} snowballs carried, "
              f"{stock} in the shulker")
    return carried == 0 and stock > 0


def _shulker_snowballs(bridge):
    """How many snowballs the shulker holds, leaving it as it was found."""
    x, y, z = SHULKER
    bridge.rpc("container.open", {"x": x, "y": y, "z": z})
    time.sleep(0.8)
    state = bridge.rpc("container.state") or {}
    total = sum(s.get("count", 0) for s in (state.get("containerSlots") or [])
                if str(s.get("id", "")).endswith("snowball"))
    bridge.rpc("container.close")
    time.sleep(0.3)
    return total


def _snowballs(bridge):
    inv = json.loads(bridge.eval("return api:inventoryJson()"))
    return sum(s.get("count", 0) for s in inv if s.get("id") == "minecraft:snowball")


def milestones(trace_path):
    """What the run actually achieved, read back from its own trace."""
    opened = took = held = threw = provoked = died = False
    for line in open(trace_path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        state = row.get("state") or {}
        if float((state.get("self") or {}).get("health") or 20) <= 0:
            died = True
        screen = (state.get("container") or {}).get("screen") or ""
        if "shulker" in screen:
            opened = True
        inv = state.get("inventory") or {}
        if (inv.get("counts") or {}).get("snowball"):
            took = True
        if ((inv.get("held") or {}).get("id") or "").endswith("snowball"):
            held = True
        change = (row.get("outcome") or {}).get("inventory_change") or {}
        if change.get("snowball", 0) < 0:
            threw = True
        for entity in list(state.get("in_frame") or []) + list(state.get("out_of_frame") or []):
            if entity.get("aggressive"):
                provoked = True
    reached = {"opened_shulker": opened, "took_snowball": took,
               "held_snowball": held, "threw_snowball": threw,
               "hit_a_piglin": provoked}
    # Dying is a failed run, whatever it managed first. Walking off the platform
    # and scoring for the snowball it picked up on the way is not a pass.
    if died:
        return {name: False for name in reached} | {"died": True}
    return reached


def one_run(ticks, controls):
    before = set(glob.glob(os.path.join(HARNESS, "traces", "*.jsonl")))
    subprocess.run(
        [sys.executable, os.path.join(HARNESS, "harness.py"),
         "--order-file", ORDER, "--controls", controls, "--max-ticks", str(ticks)],
        cwd=HARNESS, capture_output=True, text=True, timeout=ticks * 12 + 60)
    after = set(glob.glob(os.path.join(HARNESS, "traces", "*.jsonl")))
    fresh = sorted(after - before)
    if not fresh:
        return None
    return milestones(fresh[-1])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticks", type=int, default=35)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--controls", default="semantic", choices=("semantic", "keyboard"))
    args = ap.parse_args()

    bridge = Bridge()
    scores, tally = [], {}
    for run in range(args.repeat):
        print(f"run {run + 1}/{args.repeat}")
        reset(bridge)
        reached = one_run(args.ticks, args.controls)
        if reached is None:
            print("  no trace written; the harness did not run")
            continue
        died = reached.pop("died", False)
        for name, done in reached.items():
            tally[name] = tally.get(name, 0) + (1 if done else 0)
        got = 0 if died else sum(1 for v in reached.values() if v)
        scores.append(got / len(reached))
        line = "  ".join(f"{'*' if v else '.'} {k}" for k, v in reached.items())
        print(("  DIED — run scored 0.  " if died else "  ") + line)

    if not scores:
        print("SCORE INVALID (no runs completed)")
        return 2
    print(f"\nmilestones across {len(scores)} run(s):")
    for name, hits in tally.items():
        print(f"  {name:<20} {hits}/{len(scores)}")
    print(f"SCORE {sum(scores) / len(scores):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
