"""Client for the mca-rcc loopback bridge."""
import json
import os
import urllib.error
import urllib.request

CONFIG = os.path.expanduser(
    "~/Library/Application Support/minecraft/mca-rcc26/config/btone-bridge.json")


class BridgeDown(Exception):
    """The mod's HTTP bridge did not answer; Minecraft is probably not running."""


class Bridge:
    def __init__(self, config_path: str = CONFIG):
        with open(config_path) as fh:
            cfg = json.load(fh)
        self.port = cfg["port"]
        self.token = cfg.get("token")
        self.base = f"http://127.0.0.1:{self.port}"

    def rpc(self, method: str, params: dict | None = None, timeout: float = 10.0):
        body = json.dumps({"method": method, "params": params or {}}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base}/rpc", data=body,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read()).get("result")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise BridgeDown(f"{self.base} unreachable: {exc}") from exc

    def eval(self, code: str, timeout_ms: int = 500):
        """Run Lua on the client thread; returns the script's value as JSON."""
        result = self.rpc("debug.eval", {"code": code, "timeout_ms": timeout_ms})
        if not result.get("ok"):
            raise RuntimeError(f"eval failed: {result.get('error')}")
        return result.get("result")
