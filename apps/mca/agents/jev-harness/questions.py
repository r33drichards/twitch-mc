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
    "use_item": "Right-click once with whatever is in hand. It uses the held item and "
                "nothing else, so equip the item you want first.",
    "use_item_hold": "Hold the right-click down on whatever is in hand, which eating, "
                     "drinking, drawing a bow or raising a shield all require. A single "
                     "use does not eat. Equip the item you want first.",
    "equip": "Change what the hand holds to a different hotbar slot or item.",
    "craft": "Craft the chosen result item, at the crafting table if one is in reach.",
    "open": "Open the container at the chosen position, such as a chest or a furnace.",
    "move_stack": "Take a whole stack out of the open container, or put one in. This is "
                  "how items are collected from a chest and how a furnace is loaded.",
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

# One target question cannot serve every verb: questions cannot see each other's
# answers, so a single list of mixed candidates leaves the model guessing what
# kind of thing is even wanted. Each question below states its premise, all are
# asked together, and code reads only the one belonging to the chosen verb.
TARGET_ENTITY_INSTRUCTIONS = (
    "Which creature should the bot go for next, given `order` and the state? Judge it "
    "on its own: this is asked every tick and read only when the action turns out to "
    "involve a creature. Pick `none` only when no creature is a sensible one."
)
TARGET_PLACE_INSTRUCTIONS = (
    "Which block position or creature should the bot head toward, face, or open next, "
    "given `order` and the state? Judge it on its own: this is asked every tick and read "
    "only when the action turns out to involve going somewhere or opening something. "
    "Pick `none` only when nothing listed serves the order."
)
TARGET_ITEM_INSTRUCTIONS = (
    "Which item should the bot hold, use, or craft next, given `order` and the state? "
    "Judge it on its own: this is asked every tick and read only when the action turns "
    "out to involve an item. Pick `none` only when no item listed serves the order."
)
TARGET_SLOT_INSTRUCTIONS = (
    "Which slot of the open container should be moved next, given `order` and the "
    "state? Moving a slot takes that whole stack out of the container and into the "
    "inventory, or puts it in. Judge it on its own: this is asked whenever a "
    "container is open and read only when the action turns out to move a stack. "
    "Pick `none` only when nothing in the container serves the order."
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


def entity_candidates(state):
    """Creatures, visible or lately remembered."""
    candidates = {}
    for e in list(state.get("in_frame") or []) + list(state.get("out_of_frame") or []):
        candidates[str(e["id"])] = e.get("desc") or e.get("type") or "entity"
    candidates["none"] = "No creature listed is worth going for."
    return candidates


def place_candidates(state):
    """Block positions worth going to or opening, plus creatures to approach."""
    candidates = {}
    for st in _stations(state)[:8]:
        key = f"{st['x']},{st['y']},{st['z']}" if "x" in st else st.get("id")
        if key:
            candidates[str(key)] = st.get("desc") or str(key)
    for e in list(state.get("in_frame") or [])[:4]:
        candidates[str(e["id"])] = e.get("desc") or e.get("type") or "entity"
    candidates["none"] = "Nothing listed is worth heading toward or opening."
    return candidates


def item_candidates(state):
    """Items carried, and results the recipe book says can be made."""
    candidates = {}
    inventory = state.get("inventory") or {}
    for item, count in (inventory.get("counts") or {}).items():
        candidates[item] = f"{item.split(':')[-1]} carried, {count} of them"
    for recipe in (state.get("craftable") or [])[:8]:
        result = recipe.get("result")
        if result:
            candidates[result] = recipe.get("desc") or f"craft {result.split(':')[-1]}"
    candidates["none"] = "No item listed serves the order right now."
    return candidates


def slot_candidates(state):
    """Slots of the open container, if one is open.

    Every slot the state ships is offered. Capping this list once hid the gold
    in slot 22 and the coal in slot 18 behind a twelve-slot limit, and the model
    answered `none` because what the order needed was never on the menu. The
    second-phase call sends a slim state, so the room is there.
    """
    container = state.get("container") or {}
    candidates = {}
    for slot in (container.get("slots") or []):
        sid = slot.get("id", "?")
        candidates[str(slot.get("slot"))] = (
            f"slot {slot.get('slot')}: {slot.get('count', 1)} {str(sid).split(':')[-1]}")
    candidates["none"] = "Nothing in the container serves the order right now."
    return candidates


def build_candidates(state):
    """Every target option in one map, for callers that want the whole set."""
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
    questions = {
        "act": {
            "type": "choice",
            "instructions": ACT_INSTRUCTIONS,
            "criteria": dict(ACT_CRITERIA),
        },
        "target_entity": {
            "type": "choice",
            "instructions": TARGET_ENTITY_INSTRUCTIONS,
            "criteria": entity_candidates(state),
        },
        "target_place": {
            "type": "choice",
            "instructions": TARGET_PLACE_INSTRUCTIONS,
            "criteria": place_candidates(state),
        },
        "target_item": {
            "type": "choice",
            "instructions": TARGET_ITEM_INSTRUCTIONS,
            "criteria": item_candidates(state),
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
    # Only ask about slots when a container is actually open; otherwise the
    # question has nothing to offer but `none`.
    if state.get("container"):
        questions["target_slot"] = {
            "type": "choice",
            "instructions": TARGET_SLOT_INSTRUCTIONS,
            "criteria": slot_candidates(state),
        }
    return questions


def verbs_without_criteria():
    """Verbs the dispatcher can run that the model is never offered."""
    return set(Dispatcher.VERBS) - set(ACT_CRITERIA)


# ---- two-phase decision ----
#
# Asked together, `act` and the target questions cannot see each other, and they
# disagreed: `act` chose move_stack while `target_slot` answered `none`, so the
# tick failed and no amount of restating helped. The docs are explicit that a
# second request is warranted when an earlier answer determines the next
# options, so the verb is chosen first and its target second. Code still decides
# nothing — it only asks the question belonging to the verb the model picked.

TARGET_QUESTION_FOR_VERB = {
    "attack": "target_entity",
    "advance": "target_place",
    "turn_toward": "target_place",
    "open": "target_place",
    "place_block": "target_place",
    "mine_front": "target_place",
    "equip": "target_item",
    "craft": "target_item",
    # use_item and use_item_hold take no target: they are the right-click, and
    # what they use is whatever equip already put in the hand.
    "move_stack": "target_slot",
}

_TARGET_BUILDERS = {
    "target_entity": entity_candidates,
    "target_place": place_candidates,
    "target_item": item_candidates,
    "target_slot": slot_candidates,
}

_TARGET_SUBJECT = {
    "target_entity": "creature",
    "target_place": "block position or creature",
    "target_item": "item",
    "target_slot": "slot of the open container",
}

# Only the parts of state the second question can actually use.
_TARGET_STATE_KEYS = {
    "target_entity": ("self", "order", "in_frame", "out_of_frame"),
    "target_place": ("self", "order", "hazards", "stations", "in_frame"),
    "target_item": ("self", "order", "inventory", "craftable"),
    "target_slot": ("self", "order", "inventory", "container"),
}


def build_act_questions(state):
    """Phase one: the verb, and the readings that ride along."""
    questions = build_questions(state)
    return {k: v for k, v in questions.items() if not k.startswith("target")}


def build_target_question(verb, state):
    """Phase two: the one target question belonging to an already-chosen verb."""
    name = TARGET_QUESTION_FOR_VERB.get(verb)
    if not name:
        return None
    return name, {
        "type": "choice",
        "instructions": (
            f"The bot has already decided to {verb}. Which {_TARGET_SUBJECT[name]} does "
            f"it apply to, given `order` and the state? The decision itself is made and "
            f"is not in question here. Pick `none` only if nothing listed fits, which "
            f"means the {verb} cannot happen."),
        "criteria": _TARGET_BUILDERS[name](state),
    }


def target_state(verb, state):
    """The slice of state the second question needs, and nothing else."""
    name = TARGET_QUESTION_FOR_VERB.get(verb)
    if not name:
        return {}
    return {k: state.get(k) for k in _TARGET_STATE_KEYS[name] if k in state}
