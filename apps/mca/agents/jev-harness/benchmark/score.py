#!/usr/bin/env python3
"""Score the current question design against the recorded cases.

Replays real states through whatever questions.py, the order file and the
affordance rules currently say, asks Jev for the verb, and reports the fraction
that falls in the acceptable set. No Minecraft, no container: the game is
already in the recording, so the only variable is the design.

    python3 score.py                 # prints SCORE <0..1> last
    python3 score.py --verbose       # per case
"""
import argparse
import concurrent.futures
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jev  # noqa: E402
from questions import act_state, build_act_questions  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = os.path.join(os.path.dirname(HERE), "orders", "gold_farm.txt")


MAX_ATTEMPTS = 5


def ask_with_retry(state, questions):
    """The docs prescribe exponential backoff on 429 and 529; a long run hits both."""
    last = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return jev.ask(state, questions)
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            time.sleep((2 ** attempt) * 0.5 + random.random() * 0.3)
    raise last


def ask(case, controls, order):
    # Each case names the control set that can express its answer: there is no
    # key on a keyboard that takes one slot out of a chest.
    controls = case.get("controls") or controls
    state = dict(case["state"], order=order)
    questions = build_act_questions(state, controls=controls)
    try:
        answer = ask_with_retry(act_state(state), questions)
    except Exception as exc:  # noqa: BLE001 - reported, never scored
        # A call that never answered is not a wrong answer. Scoring it as one
        # turns a rate limit into a fake regression.
        return {**case, "chose": None, "error": str(exc), "ok": False,
                "failed": True, "offered": []}
    act = answer["answers"]["act"]
    chose = act["choice"]
    return {**case, "chose": chose, "confidence": act.get("confidence"),
            "tokens": answer["usage"]["input_tokens"],
            "offered": sorted(questions["act"]["criteria"]),
            "ok": chose in case["acceptable"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=os.path.join(HERE, "cases.json"))
    ap.add_argument("--controls", default="keyboard", choices=("keyboard", "semantic"))
    ap.add_argument("--order", default=ORDER)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=1,
                    help="average over N runs. Measured spread on identical code is "
                         "about +/-0.04, so a single run cannot resolve a small change.")
    args = ap.parse_args()

    with open(args.cases) as fh:
        cases = json.load(fh)
    order = open(args.order).read()

    def one_pass():
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            return list(pool.map(lambda c: ask(c, args.controls, order), cases))

    passes = [one_pass() for _ in range(max(1, args.repeat))]
    scores, failures = [], 0
    for p in passes:
        answered = [r for r in p if not r.get("failed")]
        failures += len(p) - len(answered)
        if answered:
            scores.append(sum(1 for r in answered if r["ok"]) / len(answered))
    results = passes[-1]

    by_case = {}
    for r in results:
        by_case.setdefault(r["case"], []).append(r)

    print(f"{len(results)} cases, controls={args.controls}\n")
    for name, group in sorted(by_case.items()):
        good = sum(1 for r in group if r["ok"])
        picks = {}
        for r in group:
            picks[r["chose"]] = picks.get(r["chose"], 0) + 1
        spread = ", ".join(f"{v}x {k}" for k, v in
                           sorted(picks.items(), key=lambda kv: -kv[1])[:3])
        mode = group[0].get("controls", args.controls)
        print(f"  {name:<42} {good}/{len(group)}  [{mode[:3]}]  {spread}")
        if args.verbose:
            print(f"      want one of: {', '.join(group[0]['acceptable'][:6])}")

    attempted = len(results) * len(passes)
    if failures:
        print(f"\n{failures}/{attempted} calls never answered (rate limits or errors); "
              f"they are excluded, not counted wrong.")
    if failures > attempted * 0.1 or not scores:
        # Refuse to report a number built on a broken run.
        print("SCORE INVALID (too many calls failed)")
        return 2
    tokens = sum(r.get("tokens", 0) for r in results)
    print(f"\ntokens: {tokens}  (~${tokens * 0.042 / 1e6:.4f} per run)")
    if len(scores) > 1:
        lo, hi = min(scores), max(scores)
        print(f"{len(scores)} runs: {', '.join(f'{s:.4f}' for s in scores)}  "
              f"(spread {hi - lo:.4f})")
    # autoresearch reads this line.
    print(f"SCORE {sum(scores) / len(scores):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
