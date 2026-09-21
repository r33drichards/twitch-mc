#!/usr/bin/env python3
"""Reflexes that run while you play: don't step off the edge, clutch the fall.

One probe a tick, and two decisions that have to happen inside a tenth of a
second — which is why they are code, not a model call. Nothing here asks Jev
anything; inventory management is a separate, slower loop.

    python3 assist.py                 # both reflexes
    python3 assist.py --dry-run       # say what it would do, touch nothing
    python3 assist.py --no-clutch     # edge guard only
"""
import argparse
import json
import sys
import time

from bridge import Bridge, BridgeDown
from ledge_guard import clutch_move, should_sneak

PROBE = """
local bx,by,bz = api:blockX(),api:blockY(),api:blockZ()
local function solid(px,py,pz)
  local b = api:blockAt(px,py,pz)
  return b ~= "minecraft:air" and b ~= "minecraft:cave_air"
     and b ~= "minecraft:void_air" and b ~= "minecraft:water"
end
local drop = 0
for i = 1, 24 do
  if solid(bx, by - i, bz) then break end
  drop = i
end
return {x=api:x(), y=api:y(), z=api:z(), bx=bx, by=by, bz=bz, drop=drop,
        below=solid(bx,by-1,bz),
        north=solid(bx,by-1,bz-1), south=solid(bx,by-1,bz+1),
        west=solid(bx-1,by-1,bz),  east=solid(bx+1,by-1,bz)}
"""

WATER = "minecraft:water_bucket"
PLACEABLE_HINTS = ("planks", "cobblestone", "stone", "dirt", "wool", "concrete",
                   "terracotta", "sandstone", "bricks", "log", "deepslate",
                   "netherrack", "blackstone", "obsidian", "glass", "block")


def carried(bridge):
    """Water and blocks in the hotbar, where a clutch can reach them."""
    stacks = json.loads(bridge.eval("return api:inventoryJson()"))
    water_slot = blocks_slot = None
    for stack in stacks:
        slot, item = stack.get("slot"), stack.get("id") or ""
        if slot is None or not 0 <= slot <= 8:
            continue
        if item == WATER and water_slot is None:
            water_slot = slot
        elif any(hint in item for hint in PLACEABLE_HINTS) and blocks_slot is None:
            blocks_slot = slot
    return water_slot, blocks_slot


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sneak", action="store_true")
    ap.add_argument("--no-clutch", action="store_true")
    args = ap.parse_args()

    bridge = Bridge()
    period = 1.0 / max(2.0, args.hz)
    sneaking = False
    last_y, last_at = None, None
    peak_y = None
    water_slot = blocks_slot = None
    checked_inventory = 0.0
    clutched_at = 0.0

    print(f"assist running at {args.hz:g}Hz"
          f"{' (dry run)' if args.dry_run else ''}   ctrl-c to stop")
    try:
        while True:
            loop_started = time.monotonic()
            try:
                probe = bridge.eval(PROBE, timeout_ms=300)
                if time.monotonic() - checked_inventory > 2.0:
                    water_slot, blocks_slot = carried(bridge)
                    checked_inventory = time.monotonic()
            except (BridgeDown, RuntimeError):
                time.sleep(1.0)
                last_y = None
                continue

            now = time.monotonic()
            y = float(probe.get("y", 0))
            vy = 0.0 if last_y is None or not last_at else (y - last_y) / max(1e-3, now - last_at)
            last_y, last_at = y, now

            on_ground = bool(probe.get("below")) and abs(vy) < 0.4
            peak_y = y if (on_ground or peak_y is None or y > peak_y) else peak_y
            fallen = max(0.0, (peak_y or y) - y)

            # -- edge guard --
            if not args.no_sneak:
                wants = should_sneak({
                    "fx": probe.get("x", 0) % 1, "fz": probe.get("z", 0) % 1,
                    "on_ground": on_ground,
                    "ground": {side: bool(probe.get(side))
                               for side in ("north", "south", "east", "west")},
                })
                if wants != sneaking:
                    sneaking = wants
                    if args.dry_run:
                        print(f"  {'sneak' if wants else 'release'} "
                              f"(edge at {probe.get('bx')},{probe.get('bz')})")
                    else:
                        bridge.rpc("player.press_key",
                                   {"key": "sneak",
                                    "action": "press" if wants else "release"})

            # -- clutch --
            if not args.no_clutch and now - clutched_at > 1.5:
                move = clutch_move({
                    "on_ground": on_ground, "vy": vy, "fallen": fallen,
                    "to_ground": float(probe.get("drop", 99)),
                    "has_water": water_slot is not None,
                    "has_blocks": blocks_slot is not None,
                })
                if move:
                    slot = water_slot if move == "water" else blocks_slot
                    print(f"  CLUTCH {move} — fallen {fallen:.0f}m, "
                          f"{probe.get('drop')}m to go")
                    if not args.dry_run:
                        bridge.rpc("player.set_hotbar_slot", {"slot": slot})
                        bridge.rpc("player.set_rotation", {"pitch": 90.0})
                        bridge.eval("return api:useItem()", timeout_ms=300)
                    clutched_at = now
                    checked_inventory = 0.0     # the bucket has emptied

            time.sleep(max(0.0, period - (time.monotonic() - loop_started)))
    except KeyboardInterrupt:
        pass
    finally:
        if not args.dry_run:
            try:
                bridge.rpc("player.press_key", {"key": "sneak", "action": "release"})
            except Exception:  # noqa: BLE001 - releasing must never raise
                pass
        print("\nassist stopped; sneak released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
