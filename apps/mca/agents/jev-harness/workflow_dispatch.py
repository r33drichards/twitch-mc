"""Run a workflow verb through the deterministic actions.

Same contract as the keyboard dispatcher — execute(verb, target) returns what
happened — but every verb here ends in a named state rather than pressing a key
for 300ms and hoping.
"""
import time

from actions import Actions, ActionError


class WorkflowDispatcher:
    VERBS = ("go_to", "open", "take", "close", "equip",
             "throw_at", "attack", "eat", "wait", "done")

    def __init__(self, bridge, sleep=time.sleep):
        self.actions = Actions(bridge, sleep=sleep)
        self.bridge = bridge

    def release_all(self):
        """Movement is bounded inside the actions; this is the panic path."""
        for key in ("forward", "back", "left", "right", "jump", "use", "attack"):
            try:
                self.bridge.rpc("player.press_key", {"key": key, "action": "release"})
            except Exception:  # noqa: BLE001 - releasing must never raise
                pass

    def execute(self, verb, target=None):
        started = time.monotonic()
        try:
            result = self._run(verb, target)
            result.setdefault("verb", verb)
            result["duration_ms"] = int((time.monotonic() - started) * 1000)
            result.setdefault("error", None)
            return result
        except ActionError as exc:
            return {"verb": verb, "ok": False, "changed": False,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - a failure is data, not a crash
            return {"verb": verb, "ok": False, "changed": False,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "error": f"{type(exc).__name__}: {exc}"}

    def _run(self, verb, target):
        actions, target = self.actions, target or {}
        if verb == "go_to":
            return actions.approach(self._place(target))
        if verb == "open":
            return actions.open_container(self._place(target))
        if verb == "take":
            return actions.take(self._item(target))
        if verb == "close":
            return actions.close_container()
        if verb == "equip":
            return actions.equip(self._item(target))
        if verb == "eat":
            return actions.eat(self._item(target))
        if verb == "throw_at":
            return actions.throw_at(self._entity(target))
        if verb == "attack":
            return actions.attack(self._entity(target))
        if verb in ("wait", "done"):
            return actions.wait()
        raise ActionError(f"no such action: {verb}")

    @staticmethod
    def _place(target):
        if "x" not in target:
            raise ActionError("this action needs a position, and none was chosen")
        return target

    @staticmethod
    def _item(target):
        item = target.get("item")
        if not item:
            raise ActionError("this action needs an item, and none was chosen")
        return item if ":" in item else f"minecraft:{item}"

    @staticmethod
    def _entity(target):
        if target.get("id") is None:
            raise ActionError("this action needs a creature, and none was chosen")
        return int(target["id"])
