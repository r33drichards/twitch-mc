"""What the loop remembers between ticks.

Three layers, all of them decayed by age and all of them data rather than
verdicts. The model is told what was observed and when; it is never told what
that means. A field like "stuck": true would be this code deciding, so these
classes render numbers and phrases and stop there.
"""
import time

DEFAULT_TTL_S = 30.0
DEFAULT_WINDOW_S = 10.0
MAX_DECISIONS = 5
MAX_REMEMBERED = 5


class EntityMemory:
    """Entities the client can no longer see, kept briefly with their age.

    `entitiesJson` only reports what the client is currently tracking, so an
    entity that walks behind a wall simply vanishes from the world state. This
    holds it for a while, and drops it the moment the world disagrees.
    """

    def __init__(self, clock=time.time, ttl_s=DEFAULT_TTL_S, max_entries=MAX_REMEMBERED):
        self._clock = clock
        self._ttl = ttl_s
        self._max = max_entries
        self._seen = {}

    def observe(self, in_frame, out_of_frame, visible_ids=None):
        """Fold this tick's sighting into memory.

        `visible_ids` are ids the client can see right now. An id that is
        visible but absent from the live lists has been disproven, so it is
        dropped immediately rather than left to expire.
        """
        now = self._clock()
        live = {}
        for e in list(in_frame) + list(out_of_frame):
            live[e["id"]] = e
            # A recycled id must not inherit the previous entity's identity.
            self._seen[e["id"]] = {"entity": dict(e), "at": now}

        if visible_ids:
            for eid in list(self._seen):
                if eid in visible_ids and eid not in live:
                    del self._seen[eid]

        for eid, rec in list(self._seen.items()):
            if now - rec["at"] > self._ttl:
                del self._seen[eid]

        self._live_ids = set(live)

    def seen_recently(self):
        """Remembered entities not visible this tick, newest first."""
        now = self._clock()
        out = []
        for eid, rec in self._seen.items():
            if eid in getattr(self, "_live_ids", set()):
                continue
            age = round(now - rec["at"], 1)
            e = dict(rec["entity"])
            base = e.get("desc", e.get("type", "entity"))
            e["age_s"] = age
            e["desc"] = f"{base} (last seen {age}s ago)"
            out.append(e)
        out.sort(key=lambda e: e["age_s"])
        return out[: self._max]


class DecisionLog:
    """The last few decisions with what measurably followed them."""

    def __init__(self, clock=time.time, window_s=DEFAULT_WINDOW_S, max_entries=MAX_DECISIONS):
        self._clock = clock
        self._window = window_s
        self._max = max_entries
        self._entries = []

    def record(self, verb, target, gap_ms, outcome, result=None):
        self._entries.append({
            "verb": verb,
            "target": target,
            "gap_ms": gap_ms,
            "outcome": outcome or {},
            "result": result,
            "at": self._clock(),
        })
        self._entries = self._entries[-self._max:]

    def recent(self):
        now = self._clock()
        out = []
        for e in self._entries:
            age = now - e["at"]
            if age > self._window:
                continue
            row = {k: v for k, v in e.items() if k != "at"}
            row["age_s"] = round(age, 1)
            out.append(row)
        return out

    def desc(self):
        """A plain reading of the recent run: verbs, ages, and what moved."""
        rows = self.recent()
        if not rows:
            return "no decisions yet"
        parts = []
        for r in rows:
            moved = r["outcome"].get("moved_m")
            moved_s = f", moved {moved:.1f}m" if isinstance(moved, (int, float)) else ""
            result = r.get("result") or {}
            # A verb that failed says so, with the reason the dispatcher gave.
            if result.get("ok") is False:
                reason = result.get("error") or "no reason given"
                parts.append(f"{r['verb']} {r['age_s']}s ago FAILED: {reason}")
            else:
                parts.append(f"{r['verb']} {r['age_s']}s ago{moved_s}")
        return "; ".join(parts)


class TickClock:
    """How fast the loop is actually running, and what each verb costs in time."""

    def __init__(self, verb_duration_ms=None, keep=32):
        self._verb_ms = dict(verb_duration_ms or {})
        self._gaps = []
        self._keep = keep
        self._last = {}

    def record(self, gap_ms, model_latency_ms, action_ms):
        self._gaps.append(int(gap_ms))
        self._gaps = self._gaps[-self._keep:]
        self._last = {
            "last_gap_ms": int(gap_ms),
            "model_latency_ms": int(model_latency_ms),
            "action_ms": int(action_ms),
        }

    def _pct(self, p):
        if not self._gaps:
            return None
        ordered = sorted(self._gaps)
        idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
        return ordered[idx]

    def next_decision_in_ms(self, verb=None):
        return self._verb_ms.get(verb, self._last.get("action_ms", 0))

    def snapshot(self, next_verb=None):
        snap = dict(self._last)
        snap["p50_gap_ms"] = self._pct(50)
        snap["p90_gap_ms"] = self._pct(90)
        snap["verb_duration_ms"] = dict(self._verb_ms)
        snap["next_decision_in_ms"] = self.next_decision_in_ms(next_verb)
        snap["desc"] = self.desc()
        return snap

    def desc(self):
        if not self._last:
            return "first decision; no timing history yet"
        return (f"about {self._last['last_gap_ms']}ms between decisions "
                f"(model {self._last['model_latency_ms']}ms)")
