"""The questions put to Jev, and nothing else.

One batched call per tick. `act` alone drives execution: whatever comes back is
executed. There are no thresholds here and no precedence anywhere, so the verb
set must stay complete — every escape the bot might need has to be selectable,
or it cannot be taken.

The supporting nouls gate nothing. They return to the model next tick as its own
prior reading, tagged as inferred.
"""
from dispatch import Dispatcher, VERB_DURATION_MS

# One line per verb. These are situations, not rules for code to apply.
ACT_CRITERIA = {
    "advance": "Move forward toward the target or destination; it is not reached yet.",
    "retreat": "Back away from what is in front of the player.",
    "turn_toward": "Rotate to face the target; it is not in front of the player.",
    "jump": "Jump, to clear a block, a gap, or to shake free of an obstruction.",
    "mine_front": "Break the block directly ahead of the player.",
    "place_block": "Place a block from the hotbar against what is ahead.",
    "attack": "Swing at the chosen target entity, which is close enough to hit.",
    "use_item": "Use what is held once: throw the snowball, or activate the item in hand.",
    "use_item_hold": "Hold the use button down, which eating, drinking, drawing a bow "
                     "or raising a shield all require. A single use does not eat.",
    "equip": "Change what the hand holds to a different hotbar slot or item.",
    "craft": "Craft the chosen result item, at the crafting table if one is in reach.",
    "open": "Open the container at the chosen position, such as a chest or a furnace.",
    "move_stack": "Move one whole stack between the open container and the inventory.",
    "close": "Close the open container screen.",
    "hold": "Do nothing this tick; waiting is what the situation calls for.",
    "done": "The order is fully satisfied and should be cleared.",
}

ACT_INSTRUCTIONS = (
    "Choose the single next physical action for the bot, given `order` and the world "
    "state. The action you choose runs for the time listed in `tick.verb_duration_ms`, "
    "and you will not be asked again for about `tick.next_decision_in_ms`. Nothing else "
    "decides: whatever you choose is what happens."
)

TARGET_INSTRUCTIONS = (
    "Which entity, block position or item the chosen action applies to. Pick `none` "
    "when the action needs no target."
)

ORDER_QUESTIONS = {
    "order_kind": {
        "type": "choice",
        "instructions": "What kind of standing order the latest chat line gives the bot.",
        "criteria": {
            "goto": "Travel to a place or thing.",
            "follow": "Stay near a named player or entity.",
            "gather": "Collect, craft, process or store items, possibly over a long run.",
            "attack": "Fight something.",
            "stop": "Abandon the current order and do nothing further.",
            "unclear": "The line is not an instruction, or its meaning cannot be told.",
        },
    },
}


def _stations(state):
    """The nearby stations, however state ships them.

    state.py returns {near, more, desc}; only `near` holds individual blocks,
    and `more` is a count roll-up with no positions to target.
    """
    stations = state.get("stations") or {}
    if isinstance(stations, dict):
        return list(stations.get("near") or [])
    return list(stations)


def build_candidates(state):
    """The target options, enumerated from what the world actually contains.

    Code lists what exists; the model picks among them. Nothing is invented,
    because an entity that is not here cannot appear in the criteria.
    """
    candidates = {}
    for e in list(state.get("in_frame") or []) + list(state.get("out_of_frame") or []):
        candidates[str(e["id"])] = e.get("desc") or e.get("type") or "entity"
    for st in _stations(state)[:6]:
        key = f"{st['x']},{st['y']},{st['z']}" if "x" in st else st.get("id")
        if key:
            candidates[str(key)] = st.get("desc") or str(key)
    # Carried items, so `equip` and `use_item` have something to name.
    inventory = state.get("inventory") or {}
    for item, count in (inventory.get("counts") or {}).items():
        candidates[item] = f"{item.split(':')[-1]} carried, {count} of them"
    # Craftable results, so `craft` has something to name.
    for recipe in (state.get("craftable") or [])[:8]:
        result = recipe.get("result")
        if result:
            candidates[result] = recipe.get("desc") or f"craft {result.split(':')[-1]}"
    candidates["none"] = "No target: the action needs none."
    return candidates


def build_questions(state):
    """The one batch asked every tick."""
    return {
        "act": {
            "type": "choice",
            "instructions": ACT_INSTRUCTIONS,
            "criteria": dict(ACT_CRITERIA),
        },
        "target": {
            "type": "choice",
            "instructions": TARGET_INSTRUCTIONS,
            "criteria": build_candidates(state),
        },
        "arrived": {
            "type": "noul",
            "instructions": "The bot has reached what `order` describes.",
            "criteria": {
                "true": "The player is within 2 blocks of the described destination, "
                        "or the described goal has been achieved.",
                "false": "There is still travel or work left to do.",
            },
        },
        "in_danger": {
            "type": "noul",
            "instructions": "The bot is in immediate physical danger.",
            "criteria": {
                "true": "A hostile mob with `aggressive` true is within 4 blocks, or "
                        "health fell in the last 2 seconds, or lava or a drop is within "
                        "2 blocks ahead.",
                "false": "Nothing aggressive is within 4 blocks, health is steady, and "
                         "the ground ahead is solid.",
            },
        },
        "stuck": {
            "type": "noul",
            "instructions": "The bot is repeating itself without progress.",
            "criteria": {
                "true": "`recent_decisions` show the same verb 3 or more times with "
                        "moved_m under 0.5 and nothing else changing.",
                "false": "Recent decisions are varied, or something measurably changed.",
            },
        },
    }


def verbs_without_criteria():
    """Verbs the dispatcher can run that the model is never offered."""
    return set(Dispatcher.VERBS) - set(ACT_CRITERIA)
