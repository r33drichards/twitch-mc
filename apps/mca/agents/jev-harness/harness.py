#!/usr/bin/env python3
"""The loop.

Assemble state, ask Jev once, execute what it chose, remember what happened.
Code never arbitrates: there are no thresholds and no overrides here. The only
thing this loop refuses to do is act on a snapshot that has gone stale, and in
that case it substitutes no decision of its own — it re-reads and asks again.

    python3 harness.py --order-file orders/gold_farm.txt --max-ticks 20
    python3 harness.py --dry-run          # decide and log, execute nothing
"""
import argparse
import json
import math
import os
import sys
import time

from bridge import Bridge, BridgeDown
from dispatch import Dispatcher, VERB_DURATION_MS
from memory import DecisionLog, EntityMemory, TickClock
from questions import build_questions, ORDER_QUESTIONS
from sse import EventStream
from state import build_state
import jev

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE_DIR = os.path.join(HERE, "traces")
STALE_AFTER_S = 1.5


# Which speculative answer each verb consumes. Every verb reads exactly one,
# so there is never a tie to break: the model answered all of them, and code
# takes the one belonging to the verb the model chose.
VERB_TARGET_QUESTION = {
    "attack": "target_entity",
    "advance": "target_place",
    "turn_toward": "target_place",
    "open": "target_place",
    "equip": "target_item",
    "craft": "target_item",
    "use_item": "target_item",
    "use_item_hold": "target_item",
    "move_stack": "target_slot",
    "place_block": "target_place",
    "mine_front": "target_place",
}


def target_for(verb, answers, state):
    """The target for this verb, read from its own speculative answer."""
    question = VERB_TARGET_QUESTION.get(verb)
    if not question:
        return None
    choice = (answers.get(question) or {}).get("choice")
    if not choice or choice == "none":
        return None
    if question == "target_slot":
        # A slot is a bare number and means nothing to entity or position lookup.
        try:
            return {"slot": int(choice)}
        except (TypeError, ValueError):
            return None
    return resolve_target(state, choice)


def resolve_target(state, choice):
    """Turn the model's chosen candidate id back into something executable.

    Returns None for `none`, and also for a choice that no longer exists — the
    entity may have died between the snapshot and the answer. Guessing a
    different target would be this code deciding.
    """
    if not choice or choice == "none":
        return None
    for e in list(state.get("in_frame") or []) + list(state.get("out_of_frame") or []):
        if str(e.get("id")) == str(choice):
            return dict(e)
    if ":" in str(choice):
        # An item id names a thing to equip, use or craft rather than a place.
        return {"item": str(choice)}
    if "," in str(choice):
        try:
            x, y, z = (int(float(p)) for p in str(choice).split(","))
            return {"x": x, "y": y, "z": z}
        except ValueError:
            return None
    stations = state.get("stations") or {}
    near = stations.get("near") if isinstance(stations, dict) else stations
    for st in near or []:
        if str(st.get("id")) == str(choice):
            return dict(st)
    return None


def measure_outcome(before, after):
    """What measurably followed the action. Facts only, no judgement."""
    if not before or not after:
        return {}
    a, b = (after.get("self") or {}), (before.get("self") or {})
    if not a or not b:
        return {}
    moved = math.dist(
        [b.get("x", 0.0), b.get("y", 0.0), b.get("z", 0.0)],
        [a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0)])
    return {
        "moved_m": round(moved, 2),
        "health_delta": round(a.get("health", 0.0) - b.get("health", 0.0), 2),
    }


def load_order(path):
    if not path:
        return None
    with open(path) as fh:
        return fh.read().strip()


class Harness:
    def __init__(self, bridge, order=None, dry_run=False, trace_path=None):
        self.bridge = bridge
        self.order = order
        self.dry_run = dry_run
        self.events = EventStream(bridge)
        self.dispatcher = Dispatcher(bridge)
        self.entities = EntityMemory()
        self.decisions = DecisionLog()
        self.clock = TickClock(verb_duration_ms=VERB_DURATION_MS)
        self.trace_path = trace_path
        self.tick_id = 0
        self.last_assessment = []
        self._last_gap = None
        self._seen_chat = set()

    # ---- state ----

    def snapshot(self):
        state = build_state(self.bridge, order=self.order)
        visible = {e["id"] for e in (state.get("in_frame") or [])}
        self.entities.observe(state.get("in_frame") or [],
                              state.get("out_of_frame") or [],
                              visible_ids=visible)
        state["seen_recently"] = self.entities.seen_recently()
        state["recent_decisions"] = self.decisions.recent()
        state["recent_decisions_desc"] = self.decisions.desc()
        # The model's own prior reading, kept apart from anything observed.
        state["self_assessment"] = self.last_assessment
        state["tick"] = self.clock.snapshot()
        state["recent_chat"] = [e.get("payload", {}) for e in self.events.recent_chat(3)]
        state["tick_id"] = self.tick_id
        return state

    # ---- orders ----

    def poll_orders(self):
        """A new chat line becomes the standing order. Its own call, not every tick."""
        for event in self.events.recent_chat(3):
            text = (event.get("payload") or {}).get("text", "")
            key = (text, event.get("ts"))
            if not text or key in self._seen_chat:
                continue
            self._seen_chat.add(key)
            try:
                answer = jev.ask({"chat_line": text, "current_order": self.order},
                                 ORDER_QUESTIONS)
                kind = answer["answers"]["order_kind"]["choice"]
            except Exception as exc:  # noqa: BLE001 - intake must never kill the loop
                print(f"[order] intake failed: {exc}", file=sys.stderr)
                continue
            if kind == "unclear":
                continue
            self.order = None if kind == "stop" else text
            print(f"[order] {kind}: {text!r}")

    # ---- the tick ----

    def tick(self):
        self.tick_id += 1
        started = time.monotonic()
        state = self.snapshot()

        answer = jev.ask(state, build_questions(state))
        answers = answer["answers"]
        verb = answers["act"]["choice"]
        target = target_for(verb, answers, state)

        age = time.time() - float(state.get("captured_at") or time.time())
        if age > STALE_AFTER_S:
            print(f"[tick {self.tick_id}] discarded: state {age:.1f}s old")
            self.write_trace(state, answer, verb, None, {"discarded": True})
            return

        if self.dry_run:
            result = {"verb": verb, "ok": None, "duration_ms": 0, "dry_run": True}
        else:
            result = self.dispatcher.execute(verb, target)

        after = None
        try:
            after = {"self": self.bridge.eval(
                "return {x=api:x(),y=api:y(),z=api:z(),health=api:health()}")}
        except Exception:  # noqa: BLE001 - a failed read is not a decision
            pass
        outcome = measure_outcome(state, after)

        gap_ms = int((time.monotonic() - started) * 1000)
        self.clock.record(gap_ms=gap_ms,
                          model_latency_ms=answer.get("latency_ms", 0),
                          action_ms=result.get("duration_ms", 0))
        self.decisions.record(verb=verb, target=target, gap_ms=gap_ms, outcome=outcome)
        self.last_assessment = [{
            "age_s": 0.0,
            "arrived": answers.get("arrived", {}).get("noul"),
            "in_danger": answers.get("in_danger", {}).get("noul"),
            "stuck": answers.get("stuck", {}).get("noul"),
        }]

        conf = answers["act"].get("confidence")
        print(f"[tick {self.tick_id}] {verb}"
              f"{'' if target is None else ' -> ' + str(target.get('type', target))}"
              f"  conf {conf}  {gap_ms}ms  {outcome or ''}")
        self.write_trace(state, answer, verb, target, outcome)

    def write_trace(self, state, answer, verb, target, outcome):
        if not self.trace_path:
            return
        row = {"tick_id": self.tick_id, "at": time.time(), "state": state,
               "answers": answer.get("answers"), "usage": answer.get("usage"),
               "latency_ms": answer.get("latency_ms"), "verb": verb,
               "target": target, "outcome": outcome}
        with open(self.trace_path, "a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def run(self, max_ticks=None):
        self.events.start()
        try:
            while max_ticks is None or self.tick_id < max_ticks:
                try:
                    self.poll_orders()
                    self.tick()
                except BridgeDown as exc:
                    print(f"[bridge] {exc}; retrying", file=sys.stderr)
                    time.sleep(2.0)
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            self.events.stop()
            self.dispatcher.release_all()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--order-file", help="text file holding the standing order")
    ap.add_argument("--order", help="the standing order, inline")
    ap.add_argument("--max-ticks", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="decide and log, execute nothing")
    ap.add_argument("--no-trace", action="store_true")
    args = ap.parse_args()

    order = args.order or load_order(args.order_file)
    trace_path = None
    if not args.no_trace:
        os.makedirs(TRACE_DIR, exist_ok=True)
        trace_path = os.path.join(TRACE_DIR, f"{int(time.time())}.jsonl")

    harness = Harness(Bridge(), order=order, dry_run=args.dry_run, trace_path=trace_path)
    print(f"order: {order!r}\ntrace: {trace_path}")
    harness.run(max_ticks=args.max_ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
