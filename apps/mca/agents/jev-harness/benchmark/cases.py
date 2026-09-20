#!/usr/bin/env python3
"""Turn recorded ticks into labelled decision cases.

Every case is a real state the harness captured from a live game, paired with
the set of actions that would be reasonable in it. The labels are judgement,
not ground truth handed down by the game: they encode what this session
established by watching the bot fail — that you select an item before using
it, that you aim before throwing, that a container holding nothing you need
should be closed. Scoring against them measures agreement with that reading.

    python3 cases.py --summary
"""
import argparse
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES = os.path.join(os.path.dirname(HERE), "traces")

# What the gold farm actually wants out of a container.
WANTED = ("snowball", "gold", "golden_", "coal", "blaze")
SELECT_VERBS = {"equip", "cycle_item_left", "cycle_item_right",
                *(f"slot_{n}" for n in range(1, 10))}
AIM_VERBS = {"aim", "aim_higher", "aim_lower", "turn_left", "turn_right",
             *(f"look_{d}" for d in ("left", "right", "up", "down"))}


def _held(state):
    inv = state.get("inventory") or {}
    return ((inv.get("held") or {}).get("id") or "").split(":")[-1]


def _counts(state):
    return (state.get("inventory") or {}).get("counts") or {}


def _hostiles(state, anywhere=False):
    """Hostiles in view, or anywhere the bot knows about.

    The aiming case needs the second sense. Requiring a hostile *in frame*
    before saying "you should aim" was a contradiction: the reason to aim is
    that the target is not where you are looking.
    """
    seen = list(state.get("in_frame") or [])
    if anywhere:
        seen += list(state.get("out_of_frame") or []) + list(state.get("seen_recently") or [])
    return [e for e in seen if e.get("hostile")]


def classify(state):
    """The case this state is an example of, or None if it teaches nothing.

    Returns (name, acceptable_verbs, why, controls).
    """
    container = state.get("container") or {}
    counts = _counts(state)
    held = _held(state)
    looking = (state.get("looking_at") or {}).get("id")
    food = (state.get("self") or {}).get("food")

    if container:
        # Container work has no keyboard equivalent — there is no key that
        # takes a specific slot — so these cases are only answerable with the
        # semantic verbs, and are scored there.
        inside = " ".join(str(s.get("id", "")) for s in (container.get("slots") or []))
        if any(w in inside for w in WANTED):
            return ("container_holds_something_wanted", {"move_stack"},
                    "the open container holds snowballs or gold, so take them",
                    "semantic")
        return ("container_holds_nothing_wanted", {"close"},
                "nothing in the open container serves the order, so close it",
                "semantic")

    if isinstance(food, (int, float)) and food < 18 and counts.get("rotten_flesh"):
        return ("hungry_with_food_carried", SELECT_VERBS | {"use_item_hold"},
                "food is low and rotten flesh is carried, so hold it and eat",
                "keyboard")

    if counts.get("snowball") and held != "snowball" and _hostiles(state):
        return ("snowballs_carried_but_not_held", SELECT_VERBS,
                "throwing needs the snowball in hand first", "keyboard")

    if held == "snowball" and _hostiles(state, anywhere=True):
        if looking:
            return ("aimed_at_a_block_with_snowball_held", AIM_VERBS,
                    "the crosshair is on a block, so the throw would hit it",
                    "keyboard")
        return ("snowball_held_and_nothing_in_the_way", {"use_item"},
                "holding a snowball with the view clear: throw it", "keyboard")

    return None


def build(limit_per_case=8):
    seen, cases = {}, []
    # Newest traces first. Filling each quota from the oldest files meant a
    # freshly recorded situation could never enter the benchmark, which is how
    # the aiming case stayed at zero examples while it was being recorded.
    for path in sorted(glob.glob(os.path.join(TRACES, "*.jsonl")), reverse=True):
        for line in open(path):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            state = row.get("state") or {}
            if not state.get("self"):
                continue
            verdict = classify(state)
            if not verdict:
                continue
            name, acceptable, why, controls = verdict
            if seen.get(name, 0) >= limit_per_case:
                continue
            seen[name] = seen.get(name, 0) + 1
            cases.append({
                "id": f"{name}-{seen[name]}",
                "case": name,
                "why": why,
                "controls": controls,
                "acceptable": sorted(acceptable),
                "chose_at_the_time": row.get("verb"),
                "state": state,
            })
    return cases


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(HERE, "cases.json"))
    ap.add_argument("--per-case", type=int, default=8)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    cases = build(args.per_case)
    with open(args.out, "w") as fh:
        json.dump(cases, fh)
    if args.summary:
        by = {}
        for c in cases:
            by.setdefault(c["case"], []).append(c)
        print(f"{len(cases)} cases written to {os.path.relpath(args.out, HERE)}\n")
        for name, group in sorted(by.items()):
            agreed = sum(1 for c in group
                         if c["chose_at_the_time"] in c["acceptable"])
            print(f"  {name:<40} {len(group):>2} cases  "
                  f"(the live run got {agreed}/{len(group)} right)")
            print(f"      {group[0]['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
