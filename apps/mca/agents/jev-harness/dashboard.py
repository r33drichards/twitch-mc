#!/usr/bin/env python3
"""Debug view for the harness: what Jev sees, what it answered, what it ran.

    python3 dashboard.py            # http://127.0.0.1:8770

Reads traces/live.json, which the harness rewrites every tick, and edits the
order file in place — the harness re-reads it on the next tick, so an order can
be changed mid-run without restarting anything.
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "traces", "live.json")
PAGE = os.path.join(HERE, "dashboard.html")
ORDER = os.path.join(HERE, "orders", "gold_farm.txt")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/api/live"):
            try:
                with open(LIVE) as fh:
                    return self._send(200, fh.read())
            except OSError:
                return self._send(200, json.dumps({"waiting": True}))
        if self.path.startswith("/api/order"):
            try:
                with open(self.server.order_path) as fh:
                    return self._send(200, json.dumps({"order": fh.read()}))
            except OSError as exc:
                return self._send(404, json.dumps({"error": str(exc)}))
        try:
            with open(PAGE, "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        except OSError as exc:
            return self._send(500, str(exc), "text/plain")

    def do_POST(self):
        if not self.path.startswith("/api/order"):
            return self._send(404, json.dumps({"error": "no such endpoint"}))
        length = int(self.headers.get("Content-Length") or 0)
        try:
            order = json.loads(self.rfile.read(length)).get("order", "")
        except ValueError:
            return self._send(400, json.dumps({"error": "bad json"}))
        with open(self.server.order_path, "w") as fh:
            fh.write(order)
        # The harness notices the changed mtime on its next tick.
        self._send(200, json.dumps({"saved": True, "bytes": len(order)}))

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--order-file", default=ORDER)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.order_path = args.order_file
    print(f"debug view: http://127.0.0.1:{args.port}   (order: {args.order_file})")
    server.serve_forever()


if __name__ == "__main__":
    main()
