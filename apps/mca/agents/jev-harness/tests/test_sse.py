"""The push sensor: SSE wire parsing and the bounded event window.

The parser is fed raw text, never a socket. The stream is driven by a fake
opener that yields byte lines, so the reader loop, its reconnect backoff and
its refusal to raise are all testable without Minecraft.
"""
import json
import threading
import unittest

from sse import EventStream, SseParser


class FakeBridge:
    """Just enough of Bridge for the stream to build a URL."""

    def __init__(self, token=None):
        self.port = 25591
        self.token = token
        self.base = "http://127.0.0.1:25591"


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def chat(text, ts=1, overlay=False):
    return ("event: chat\n"
            "data: " + json.dumps({"type": "chat", "ts": ts,
                                   "payload": {"text": text, "overlay": overlay}})
            + "\n\n")


def subtitle(name, ts=1, distance=3.0):
    return ("event: subtitle\n"
            "data: " + json.dumps({"type": "subtitle", "ts": ts,
                                   "payload": {"subtitle": name, "distance": distance,
                                               "soundId": "minecraft:entity.zombie.ambient"}})
            + "\n\n")


# --------------------------------------------------------------------------
# wire format
# --------------------------------------------------------------------------

class TestSseParser(unittest.TestCase):
    def setUp(self):
        self.p = SseParser()

    def test_parses_one_record(self):
        recs = self.p.feed('event: chat\ndata: {"a": 1}\n\n')
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["event"], "chat")
        self.assertEqual(recs[0]["data"], '{"a": 1}')

    def test_keepalive_comment_yields_no_record(self):
        self.assertEqual(self.p.feed(": keepalive\n\n"), [])
        self.assertEqual(self.p.comment_count, 1)

    def test_keepalive_between_events_does_not_break_framing(self):
        recs = self.p.feed(chat("one") + ": keepalive\n\n" + chat("two"))
        self.assertEqual(len(recs), 2)
        self.assertEqual(self.p.comment_count, 1)

    def test_two_events_in_one_chunk(self):
        recs = self.p.feed(chat("one") + chat("two"))
        self.assertEqual([json.loads(r["data"])["payload"]["text"] for r in recs],
                         ["one", "two"])

    def test_record_split_across_chunks(self):
        self.assertEqual(self.p.feed("event: ch"), [])
        self.assertEqual(self.p.feed('at\ndata: {"a"'), [])
        recs = self.p.feed(': 1}\n\n')
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["event"], "chat")
        self.assertEqual(recs[0]["data"], '{"a": 1}')

    def test_multiple_data_lines_join_with_newline(self):
        recs = self.p.feed("event: chat\ndata: one\ndata: two\n\n")
        self.assertEqual(recs[0]["data"], "one\ntwo")

    def test_strips_exactly_one_leading_space(self):
        recs = self.p.feed("event: chat\ndata:  padded\n\n")
        self.assertEqual(recs[0]["data"], " padded")

    def test_field_without_space_after_colon(self):
        recs = self.p.feed("event:chat\ndata:{}\n\n")
        self.assertEqual(recs[0]["event"], "chat")
        self.assertEqual(recs[0]["data"], "{}")

    def test_crlf_line_endings(self):
        recs = self.p.feed("event: joined\r\ndata: {}\r\n\r\n")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["event"], "joined")

    def test_missing_event_line_defaults_to_message(self):
        recs = self.p.feed("data: {}\n\n")
        self.assertEqual(recs[0]["event"], "message")

    def test_blank_line_with_no_fields_yields_nothing(self):
        self.assertEqual(self.p.feed("\n\n\n"), [])

    def test_unknown_field_is_ignored(self):
        recs = self.p.feed("id: 7\nretry: 100\nevent: chat\ndata: x\n\n")
        self.assertEqual(recs[0]["event"], "chat")
        self.assertEqual(recs[0]["id"], "7")

    def test_state_resets_between_records(self):
        self.p.feed("event: chat\ndata: one\n\n")
        recs = self.p.feed("data: two\n\n")
        self.assertEqual(recs[0]["event"], "message")
        self.assertEqual(recs[0]["data"], "two")


# --------------------------------------------------------------------------
# the event window
# --------------------------------------------------------------------------

class TestEventStreamWindow(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.stream = EventStream(FakeBridge(), clock=self.clock.time)

    def test_feed_records_type_and_payload(self):
        self.stream.feed(chat("hello bot"))
        events = self.stream.recent(10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "chat")
        self.assertEqual(events[0]["payload"]["text"], "hello bot")
        self.assertEqual(events[0]["received_at"], 1000.0)

    def test_recent_chat_returns_last_n_oldest_first(self):
        for i in range(5):
            self.stream.feed(chat(f"line {i}"))
        texts = [e["payload"]["text"] for e in self.stream.recent_chat(3)]
        self.assertEqual(texts, ["line 2", "line 3", "line 4"])

    def test_recent_chat_ignores_other_types(self):
        self.stream.feed(subtitle("Zombie groans"))
        self.stream.feed(chat("only me"))
        self.assertEqual([e["payload"]["text"] for e in self.stream.recent_chat()],
                         ["only me"])

    def test_recent_sounds_windows_by_receive_time(self):
        self.stream.feed(subtitle("old sound"))
        self.clock.advance(5.0)
        self.stream.feed(subtitle("fresh sound"))
        heard = self.stream.recent_sounds(seconds=2.0)
        self.assertEqual([e["payload"]["subtitle"] for e in heard], ["fresh sound"])

    def test_recent_sounds_ignores_chat(self):
        self.stream.feed(chat("not a sound"))
        self.assertEqual(self.stream.recent_sounds(), [])

    def test_events_carry_age_seconds(self):
        self.stream.feed(chat("aged"))
        self.clock.advance(3.5)
        self.assertAlmostEqual(self.stream.recent_chat()[0]["age_s"], 3.5)

    def test_deque_is_bounded(self):
        stream = EventStream(FakeBridge(), maxlen=4, clock=self.clock.time)
        for i in range(10):
            stream.feed(chat(f"line {i}"))
        kept = [e["payload"]["text"] for e in stream.recent(100)]
        self.assertEqual(kept, ["line 6", "line 7", "line 8", "line 9"])

    def test_chat_survives_a_flood_of_sounds(self):
        # Measured against the live bridge: 126 subtitle events in 40 seconds
        # standing in the Nether. A single shared window evicts the chat line
        # carrying the standing order inside a minute, so chat gets its own.
        self.stream.feed(chat("go to the portal"))
        for i in range(500):
            self.stream.feed(subtitle(f"Footsteps {i}"))
        self.assertEqual([e["payload"]["text"] for e in self.stream.recent_chat()],
                         ["go to the portal"])

    def test_malformed_json_becomes_data_not_an_exception(self):
        self.stream.feed("event: chat\ndata: {not json\n\n")
        ev = self.stream.recent(1)[0]
        self.assertEqual(ev["type"], "chat")
        self.assertEqual(ev["payload"], {})
        self.assertEqual(ev["raw"], "{not json")
        self.assertIn("error", ev)

    def test_event_type_falls_back_to_the_event_line(self):
        # payload JSON without its own "type" still lands under the SSE name
        self.stream.feed('event: joined\ndata: {"ts": 5}\n\n')
        self.assertEqual(self.stream.recent(1)[0]["type"], "joined")

    def test_joined_and_disconnected_track_in_world(self):
        self.assertIsNone(self.stream.in_world)
        self.stream.feed("event: joined\ndata: {}\n\n")
        self.assertTrue(self.stream.in_world)
        self.stream.feed("event: disconnected\ndata: {}\n\n")
        self.assertFalse(self.stream.in_world)

    def test_keepalive_updates_liveness_without_an_event(self):
        self.clock.advance(7.0)
        self.stream.feed(": keepalive\n\n")
        self.assertEqual(self.stream.recent(10), [])
        self.assertEqual(self.stream.last_message_at, 1007.0)


# --------------------------------------------------------------------------
# the reader loop
# --------------------------------------------------------------------------

class FakeResponse:
    """An iterable of byte lines, like urlopen's HTTPResponse."""

    def __init__(self, lines, then=None):
        self._lines = list(lines)
        self._then = then          # exception to raise after the lines run out
        self.closed = False

    def __iter__(self):
        for line in self._lines:
            yield line
        if self._then is not None:
            raise self._then

    def close(self):
        self.closed = True


class TestEventStreamLoop(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def _drain(self, stream, timeout=2.0):
        """Run the reader until it stops on its own, or fail."""
        stream.start()
        stream._thread.join(timeout)
        self.assertFalse(stream._thread.is_alive(), "reader thread never finished")

    def test_not_connected_before_start(self):
        stream = EventStream(FakeBridge(), clock=self.clock.time)
        self.assertFalse(stream.connected)

    def test_reads_a_stream_and_disconnects_at_eof(self):
        done = threading.Event()
        lines = [b"event: chat\n", b'data: {"type":"chat","payload":{"text":"hi"}}\n', b"\n"]

        def opener():
            if done.is_set():
                raise AssertionError("reopened after stop")
            return FakeResponse(lines)

        stream = EventStream(FakeBridge(), clock=self.clock.time, opener=opener,
                             max_reconnects=1)
        self._drain(stream)
        done.set()
        self.assertEqual([e["payload"]["text"] for e in stream.recent_chat()], ["hi"])
        self.assertFalse(stream.connected)

    def test_reconnects_after_the_stream_drops(self):
        opened = []

        def opener():
            opened.append(1)
            if len(opened) == 1:
                return FakeResponse([b"event: chat\n", b'data: {"payload":{"text":"a"}}\n',
                                     b"\n"], then=OSError("stream died"))
            return FakeResponse([b"event: chat\n", b'data: {"payload":{"text":"b"}}\n', b"\n"])

        stream = EventStream(FakeBridge(), clock=self.clock.time, opener=opener,
                             max_reconnects=2, backoff_base=0.0)
        self._drain(stream)
        self.assertEqual(len(opened), 2)
        self.assertEqual([e["payload"]["text"] for e in stream.recent_chat()], ["a", "b"])

    def test_a_failing_opener_never_raises_into_the_caller(self):
        def opener():
            raise ConnectionRefusedError("bridge down")

        stream = EventStream(FakeBridge(), clock=self.clock.time, opener=opener,
                             max_reconnects=3, backoff_base=0.0)
        self._drain(stream)                       # must not blow up
        self.assertFalse(stream.connected)
        self.assertIn("bridge down", stream.last_error)

    def test_a_poisoned_payload_does_not_kill_the_reader(self):
        opener = lambda: FakeResponse([b"event: chat\n", b"data: {broken\n", b"\n",
                                       b"event: chat\n",
                                       b'data: {"payload":{"text":"still here"}}\n', b"\n"])
        stream = EventStream(FakeBridge(), clock=self.clock.time, opener=opener,
                             max_reconnects=1)
        self._drain(stream)
        self.assertEqual(stream.recent_chat()[-1]["payload"]["text"], "still here")

    def test_stop_closes_the_response_and_ends_the_thread(self):
        resp = FakeResponse([b": keepalive\n", b"\n"])
        stream = EventStream(FakeBridge(), clock=self.clock.time,
                             opener=lambda: resp, max_reconnects=1)
        self._drain(stream)
        stream.stop()
        self.assertTrue(resp.closed)
        self.assertFalse(stream.connected)

    def test_backoff_grows_and_is_capped(self):
        stream = EventStream(FakeBridge(), clock=self.clock.time,
                             backoff_base=0.5, backoff_cap=10.0)
        delays = [stream._backoff(i) for i in range(8)]
        self.assertEqual(delays[:5], [0.5, 1.0, 2.0, 4.0, 8.0])
        self.assertTrue(all(d == 10.0 for d in delays[5:]))

    def test_start_returns_the_stream(self):
        stream = EventStream(FakeBridge(), clock=self.clock.time,
                             opener=lambda: FakeResponse([]), max_reconnects=1)
        self.assertIs(stream.start(), stream)
        stream.stop()

    def test_request_carries_the_bearer_token_when_the_bridge_has_one(self):
        stream = EventStream(FakeBridge(token="s3cret"), clock=self.clock.time)
        req = stream._request()
        self.assertEqual(req.full_url, "http://127.0.0.1:25591/events")
        self.assertEqual(req.get_header("Authorization"), "Bearer s3cret")

    def test_request_omits_authorization_without_a_token(self):
        stream = EventStream(FakeBridge(), clock=self.clock.time)
        self.assertIsNone(stream._request().get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
