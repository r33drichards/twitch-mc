"""The push sensor: the mod's `/events` stream, held open on a background thread.

Pull state tells the harness where things are. This tells it what just
happened — a chat line arriving, a sound the player could hear but not see.
The `subtitle` feed is the game's own sound bus, and it reports the one thing
position reads cannot: being attacked from behind.

Nothing here decides anything. It receives, timestamps and keeps.

The thread is sealed: no failure inside it — a dead bridge, a truncated
record, a payload that is not JSON — escapes into the caller. A broken stream
becomes `connected == False` and `last_error`, which are facts the model can
be shown, not exceptions that stop the loop.
"""
import json
import threading
import time
import urllib.request
from collections import deque

# The mod writes ": keepalive" every 5 seconds when idle, so silence much
# longer than that means the stream is dead even though the socket is open.
READ_TIMEOUT_S = 20.0

# Reconnect delay doubles from the base and stops at the cap.
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 10.0

MAXLEN = 200

# Chat keeps a window of its own. Measured against the live bridge, 40 seconds
# in the Nether produced 126 subtitle events and nothing else: a single shared
# window would evict the chat line carrying the standing order inside a minute.
CHAT_MAXLEN = 32

# Events the mod emits. Kept for reference; unknown types are stored anyway,
# because a sensor that drops what it does not recognise is a sensor that lies.
KNOWN_TYPES = ("chat", "subtitle", "joined", "disconnected")


class SseParser:
    """Incremental `text/event-stream` parser.

    Feed it any slice of the stream — a line, a chunk, half a field — and it
    returns the records that completed. Comment lines (`: keepalive`) are
    counted and dropped, per the wire format.
    """

    def __init__(self):
        self._buf = ""
        self._event = None
        self._id = None
        self._data = []
        self.comment_count = 0

    def feed(self, text: str) -> list[dict]:
        if not text:
            return []
        self._buf += text
        # Normalise the three legal line terminators, then split on the last
        # complete line; whatever follows is a partial line, kept for next time.
        self._buf = self._buf.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._buf = self._buf.split("\n")
        records = []
        for line in lines:
            record = self._line(line)
            if record is not None:
                records.append(record)
        return records

    def _line(self, line: str) -> dict | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            self.comment_count += 1
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):           # exactly one space, per the spec
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        elif field == "id":
            self._id = value
        # "retry" and anything unrecognised: ignored, as the spec requires.
        return None

    def _dispatch(self) -> dict | None:
        if not self._data and self._event is None:
            return None                      # a stray blank line frames nothing
        record = {"event": self._event or "message",
                  "data": "\n".join(self._data),
                  "id": self._id}
        self._event = None
        self._data = []
        return record


class EventStream:
    """A bounded, timestamped window on the mod's event stream.

        stream = EventStream(bridge).start()
        stream.recent_chat(3)          # the last three chat lines
        stream.recent_sounds(2.0)      # what the player heard in the last 2s
        stream.connected               # is the sensor alive
        stream.stop()
    """

    def __init__(self, bridge, maxlen: int = MAXLEN, clock=time.time, opener=None,
                 read_timeout: float = READ_TIMEOUT_S,
                 backoff_base: float = BACKOFF_BASE_S,
                 backoff_cap: float = BACKOFF_CAP_S,
                 max_reconnects: int | None = None):
        self.bridge = bridge
        self.events = deque(maxlen=maxlen)
        self.chat = deque(maxlen=CHAT_MAXLEN)
        self.read_timeout = read_timeout
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        # None means "reconnect forever"; the tests bound it so the loop ends.
        self.max_reconnects = max_reconnects

        self.last_error = None
        self.last_message_at = None
        self.in_world = None          # set by `joined` / `disconnected`
        self.reconnects = 0

        self._clock = clock
        self._opener = opener or self._default_opener
        self._parser = SseParser()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._resp = None
        self._connected = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "EventStream":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sse-ingest", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        # Closing the response is what unblocks a reader parked on readline.
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._connected = False

    @property
    def connected(self) -> bool:
        """True while the `/events` stream is open and being read."""
        return self._connected

    # -- the window --------------------------------------------------------

    def feed(self, text: str) -> None:
        """Push raw stream text through the parser and record what it frames."""
        now = self._clock()
        if text:
            self.last_message_at = now
        for record in self._parser.feed(text):
            self._record(record, now)

    def _record(self, record: dict, now: float) -> None:
        event = {"type": record["event"], "ts": None, "payload": {},
                 "received_at": now}
        raw = record["data"]
        try:
            body = json.loads(raw) if raw else {}
        except ValueError as exc:
            # A payload we cannot read is still an event that happened. Ship
            # the breakage rather than pretending the event never arrived.
            event["raw"] = raw
            event["error"] = f"bad json: {exc}"
        else:
            if isinstance(body, dict):
                event["type"] = body.get("type") or record["event"]
                event["ts"] = body.get("ts")
                payload = body.get("payload")
                event["payload"] = payload if isinstance(payload, dict) else {}
            else:
                event["raw"] = raw
        if event["type"] == "joined":
            self.in_world = True
        elif event["type"] == "disconnected":
            self.in_world = False
        with self._lock:
            self.events.append(event)
            if event["type"] == "chat":
                self.chat.append(event)

    def recent(self, n: int = 10) -> list[dict]:
        """The last `n` events of any type, oldest first."""
        return self._window(lambda e: True, n)

    def recent_chat(self, n: int = 3) -> list[dict]:
        """The last `n` chat lines, oldest first. Never crowded out by sound."""
        return self._window(lambda e: True, n, source=self.chat)

    def recent_sounds(self, seconds: float = 2.0) -> list[dict]:
        """Subtitle events heard within the last `seconds`, oldest first.

        This is the game's own sound feed: what the player could hear,
        including things behind them and out of the frame.
        """
        cutoff = self._clock() - seconds
        return self._window(
            lambda e: e["type"] == "subtitle" and e["received_at"] >= cutoff, None)

    def _window(self, keep, n, source=None) -> list[dict]:
        now = self._clock()
        with self._lock:
            picked = [e for e in (self.events if source is None else source) if keep(e)]
        if n is not None:
            picked = picked[-n:]
        return [{**e, "age_s": round(now - e["received_at"], 2)} for e in picked]

    # -- the reader --------------------------------------------------------

    def _request(self) -> urllib.request.Request:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        token = getattr(self.bridge, "token", None)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return urllib.request.Request(f"{self.bridge.base}/events", headers=headers)

    def _default_opener(self):
        return urllib.request.urlopen(self._request(), timeout=self.read_timeout)

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_base * (2 ** attempt), self.backoff_cap)

    def _exhausted(self, attempts: int) -> bool:
        return self.max_reconnects is not None and attempts >= self.max_reconnects

    def _run(self) -> None:
        attempts = 0
        failures = 0
        while not self._stop.is_set():
            if self._exhausted(attempts):
                break
            attempts += 1
            if attempts > 1:
                self.reconnects += 1
            try:
                resp = self._opener()
            except Exception as exc:                      # noqa: BLE001 — sealed thread
                self.last_error = f"{type(exc).__name__}: {exc}"
                failures += 1
                if self._exhausted(attempts):
                    break
                self._stop.wait(self._backoff(failures - 1))
                continue
            self._resp = resp
            self._connected = True
            failures = 0
            try:
                for line in resp:
                    if self._stop.is_set():
                        break
                    self.feed(line.decode("utf-8", "replace"))
            except Exception as exc:                      # noqa: BLE001 — sealed thread
                self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._connected = False
                self._resp = None
                try:
                    resp.close()
                except Exception:
                    pass
            if self._stop.is_set() or self._exhausted(attempts):
                break
            # A clean EOF is still a dropped stream: wait, then reconnect.
            self._stop.wait(self._backoff(0))
        self._connected = False
