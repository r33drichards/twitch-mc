"""The Jev call. One batched request, typed answers back."""
import json
import subprocess
import time
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEYCHAIN_SERVICE = "typesafe-api-key"

_key_cache = None


def api_key():
    """Read the key from the login Keychain, so no file ever holds it."""
    global _key_cache
    if _key_cache is None:
        _key_cache = subprocess.check_output(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"]
        ).decode().strip()
    return _key_cache


def ask(state, questions, timeout=30.0):
    """Send one state and a map of questions; return answers plus timing."""
    body = json.dumps({"model": MODEL, "state": state, "questions": questions},
                      ensure_ascii=False, separators=(",", ":")).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    payload["latency_ms"] = int((time.monotonic() - started) * 1000)
    return payload
