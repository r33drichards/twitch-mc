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
from memory import ContainerMemory, DecisionLog, EntityMemory, TickClock
from questions import (act_state, build_act_questions, build_target_question,
                       target_state, ORDER_QUESTIONS)
from sse import EventStream
from state import build_state
import jev

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE_DIR = os.path.join(HERE, "traces")
LIVE_PATH = os.path.join(TRACE_DIR, "live.json")
STALE_AFTER_S = 1.5
# How many times a tick may re-choose after landing on an impossible verb.
MAX_VERB_RETRIES = 3


# Which speculative answer each verb consumes. Every verb reads exactly one,
# so there is never a tie to break: the model answered all of them, and code
# takes the one belonging to the verb the model chose.
VERB_TARGET_QUESTION = {
    "attack": "target_entity",
    "open": "target_place",
    "equip": "target_item",
    "craft": "target_item",
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


def _known_items(state):
    """Item ids the state showed the model, as it showed them."""
    names = set((state.get("inventory") or {}).get("counts") or {})
    for recipe in state.get("craftable") or []:
        if recipe.get("result"):
            names.add(recipe["result"])
    return names


def _as_item(state, choice):
    """The full item id for a choice, or None if the choice is not an item.

    state.py drops the `minecraft:` namespace from vanilla ids to save tokens,
    so the model answers `netherite_sword` while the mod's inventory reports
    `minecraft:netherite_sword`. The namespace goes back on here.
    """
    if ":" in choice:
        return choice
    if choice in _known_items(state):
        return f"minecraft:{choice}"
    return None


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
    item = _as_item(state, str(choice))
    if item:
        return {"item": item}
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


def _counts(inventory_json):
    """Item counts keyed the way state keys them, for comparing two ticks."""
    counts = {}
    for slot in inventory_json or []:
        item = str(slot.get("id") or "")
        if not item or item == "minecraft:air":
            continue
        counts[item.split(":")[-1]] = counts.get(item.split(":")[-1], 0) + int(slot.get("count") or 0)
    return counts


def _entity_health(state, entity_id):
    for e in list((state or {}).get("in_frame") or []) + list((state or {}).get("out_of_frame") or []):
        if e.get("id") == entity_id:
            return e.get("health")
    return None


def measure_outcome(before, after, target=None):
    """What measurably followed the action. Facts only, no judgement."""
    if not before or not after:
        return {}
    a, b = (after.get("self") or {}), (before.get("self") or {})
    if not a or not b:
        return {}
    moved = math.dist(
        [b.get("x", 0.0), b.get("y", 0.0), b.get("z", 0.0)],
        [a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0)])
    out = {
        "moved_m": round(moved, 2),
        "health_delta": round(a.get("health", 0.0) - b.get("health", 0.0), 2),
    }
    # What the action cost or gained. A verb that changes nothing at all is the
    # signal that it is not working, and without this it looks identical to one
    # that worked.
    was = ((before.get("inventory") or {}).get("counts") or {})
    now = ((after.get("inventory") or {}).get("counts") or {})
    if was or now:
        changed = {item: now.get(item, 0) - was.get(item, 0)
                   for item in set(was) | set(now)
                   if now.get(item, 0) != was.get(item, 0)}
        out["inventory_change"] = changed

    # Whether the thing acted upon actually changed. A swing from out of reach
    # reports ok and does nothing, so without this there is no way to tell the
    # difference between hitting a mob and missing it.
    if target and target.get("id") is not None:
        was = _entity_health(before, target["id"])
        now = _entity_health(after, target["id"])
        if was is not None and now is not None:
            out["target_health_delta"] = round(now - was, 2)
    return out


def load_order(path):
    if not path:
        return None
    with open(path) as fh:
        return fh.read().strip()


class Harness:
    def __init__(self, bridge, order=None, dry_run=False, trace_path=None,
                 order_path=None, controls="semantic"):
        self.bridge = bridge
        self.order = order
        # Re-read on every tick so the order can be edited while it runs.
        self.order_path = order_path
        self.controls = controls
        self._order_mtime = self._mtime(order_path)
        self.dry_run = dry_run
        self.events = EventStream(bridge)
        self.dispatcher = Dispatcher(bridge)
        self.entities = EntityMemory()
        self.containers = ContainerMemory()
        self.decisions = DecisionLog()
        self.clock = TickClock(verb_duration_ms=VERB_DURATION_MS)
        self.trace_path = trace_path
        self.tick_id = 0
        self._last_open_target = None
        self.last_assessment = []
        self._last_gap = None
        self._seen_chat = set()

    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path) if path else None
        except OSError:
            return None

    def reload_order(self):
        """Pick up an edited order file without restarting."""
        mtime = self._mtime(self.order_path)
        if mtime is None or mtime == self._order_mtime:
            return
        self._order_mtime = mtime
        self.order = load_order(self.order_path)
        print(f"[order] reloaded from {os.path.basename(self.order_path)}")

    # ---- state ----

    def snapshot(self):
        state = build_state(self.bridge, order=self.order)
        visible = {e["id"] for e in (state.get("in_frame") or [])}
        self.entities.observe(state.get("in_frame") or [],
                              state.get("out_of_frame") or [],
                              visible_ids=visible)
        state["seen_recently"] = self.entities.seen_recently()
        self.containers.observe(self._last_open_target, state.get("container"))
        state["containers_seen"] = self.containers.recent()
        state["recent_decisions"] = self.decisions.recent()
        state["recent_decisions_desc"] = self.decisions.desc()
        # The model's own prior reading, kept apart from anything observed.
        state["self_assessment"] = self.last_assessment
        state["tick"] = self.clock.snapshot()
        state["recent_chat"] = [e.get("payload", {}) for e in self.events.recent_chat(3)]
        # The game's own sound feed. A thrown item landing, a mob grunting or
        # being hurt all show up here, and nothing else tells the bot what its
        # last action actually did.
        sounds = self.events.recent_sounds(3.0)
        state["recent_sounds"] = [
            {"sound": (e.get("payload") or {}).get("soundId")
                      or (e.get("payload") or {}).get("text"),
             "age_s": round(e.get("age_s", 0.0), 1)}
            for e in sounds[-8:]]
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

        # Phase one: which verb. Phase two: that verb's target, asked knowing
        # the verb, so the two answers cannot contradict each other.
        # Phase one picks the verb; phase two picks that verb's target knowing
        # it. A verb whose target comes back `none` cannot run, so it is dropped
        # and the verb chosen again from what remains — the model decides both
        # times, and the loop cannot wedge on an impossible option.
        answer, answers, verb, target = None, {}, None, None
        impossible = []
        for _ in range(MAX_VERB_RETRIES):
            answer = jev.ask(act_state(state),
                             build_act_questions(state, without=impossible,
                                                 controls=self.controls))
            answers = answer["answers"]
            verb = answers["act"]["choice"]
            asked = build_target_question(verb, state)
            if not asked:
                break
            name, question = asked
            target_answer = jev.ask(target_state(verb, state), {name: question})
            answers[name] = target_answer["answers"][name]
            answer["latency_ms"] = (answer.get("latency_ms", 0)
                                    + target_answer.get("latency_ms", 0))
            for field in ("input_tokens", "output_tokens"):
                answer.setdefault("usage", {})[field] = (
                    answer.get("usage", {}).get(field, 0)
                    + target_answer.get("usage", {}).get(field, 0))
            target = target_for(verb, answers, state)
            if target is not None:
                break
            impossible.append(verb)
            print(f"[tick {self.tick_id}] {verb}: no target available, choosing again")

        age = time.time() - float(state.get("captured_at") or time.time())
        if age > STALE_AFTER_S:
            print(f"[tick {self.tick_id}] discarded: state {age:.1f}s old")
            self.write_trace(state, answer, verb, None, {"discarded": True})
            return

        if verb == "open" and target:
            self._last_open_target = dict(target)

        if self.dry_run:
            result = {"verb": verb, "ok": None, "duration_ms": 0, "dry_run": True}
        else:
            result = self.dispatcher.execute(verb, target)

        after = None
        try:
            after = {"self": self.bridge.eval(
                "return {x=api:x(),y=api:y(),z=api:z(),health=api:health()}")}
            after["inventory"] = {"counts": _counts(json.loads(
                self.bridge.eval("return api:inventoryJson()")))}
            if target and target.get("id") is not None:
                seen = json.loads(self.bridge.eval(
                    f"return api:entitiesJson(32)")) or []
                after["in_frame"] = seen
                after["out_of_frame"] = []
        except Exception:  # noqa: BLE001 - a failed read is not a decision
            pass
        outcome = measure_outcome(state, after, target=target)

        gap_ms = int((time.monotonic() - started) * 1000)
        self.clock.record(gap_ms=gap_ms,
                          model_latency_ms=answer.get("latency_ms", 0),
                          action_ms=result.get("duration_ms", 0))
        self.decisions.record(verb=verb, target=target, gap_ms=gap_ms, outcome=outcome,
                              result=result)
        self.last_assessment = [{
            "age_s": 0.0,
            "arrived": answers.get("arrived", {}).get("noul"),
            "in_danger": answers.get("in_danger", {}).get("noul"),
            "stuck": answers.get("stuck", {}).get("noul"),
        }]

        conf = answers["act"].get("confidence")
        failed = "" if result.get("ok") in (True, None) else f"  FAILED: {result.get('error')}"
        print(f"[tick {self.tick_id}] {verb}"
              f"{'' if target is None else ' -> ' + str(target.get('type', target))}"
              f"  conf {conf}  {gap_ms}ms  {outcome or ''}{failed}")
        self.write_trace(state, answer, verb, target, outcome, result)

    def write_trace(self, state, answer, verb, target, outcome, result=None):
        if not self.trace_path:
            return
        row = {"tick_id": self.tick_id, "at": time.time(), "state": state,
               "answers": answer.get("answers"), "usage": answer.get("usage"),
               "latency_ms": answer.get("latency_ms"), "verb": verb,
               "target": target, "result": result, "outcome": outcome}
        with open(self.trace_path, "a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.write_live(row)

    def write_live(self, row):
        """Overwrite the single file the debug view polls."""
        row = dict(row, order=self.order)
        tmp = LIVE_PATH + ".tmp"
        try:
            os.makedirs(TRACE_DIR, exist_ok=True)
            with open(tmp, "w") as fh:
                json.dump(row, fh, ensure_ascii=False)
            os.replace(tmp, LIVE_PATH)
        except OSError:
            pass

    def run(self, max_ticks=None):
        self.events.start()
        try:
            while max_ticks is None or self.tick_id < max_ticks:
                try:
                    self.reload_order()
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
    ap.add_argument("--controls", choices=("semantic", "keyboard"), default="semantic",
                    help="keyboard = only the controls a person has at a keyboard, "
                         "every verb parameterless and no target questions")
    args = ap.parse_args()

    order = args.order or load_order(args.order_file)
    trace_path = None
    if not args.no_trace:
        os.makedirs(TRACE_DIR, exist_ok=True)
        trace_path = os.path.join(TRACE_DIR, f"{int(time.time())}.jsonl")

    harness = Harness(Bridge(), order=order, dry_run=args.dry_run,
                      trace_path=trace_path, order_path=args.order_file,
                      controls=args.controls)
    print(f"order: {order!r}\ntrace: {trace_path}")
    harness.run(max_ticks=args.max_ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
