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
    "turn_left": "Swing the view a notch to the left, without moving.",
    "turn_right": "Swing the view a notch to the right, without moving.",
    "strafe_left": "Step sideways to the left, still facing the same way.",
    "strafe_right": "Step sideways to the right, still facing the same way.",
    "hotbar_next": "Scroll to the next hotbar slot.",
    "hotbar_prev": "Scroll to the previous hotbar slot.",
    "sneak": "Crouch for a moment, which also stops you walking off an edge.",
    "sprint": "Run forward rather than walk.",
    "open_inventory": "Open your own inventory, as the E key does.",
    **{f"slot_{n}": f"Hold whatever is in hotbar slot {n}, as pressing {n} does."
       for n in range(1, 10)},
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
    "move_stack": "Take a whole stack out of the open container, or put one in. Whatever "
                  "the order needs from what is inside comes out this way.",
    "close": "Close the open container screen. Right only once nothing inside is worth "
             "taking.",
    "aim": "Move the view. Pick this when what you want to act on is not where you "
           "are pointing; which way is asked next.",
    "aim_higher": "Tilt the head up a notch without turning, so the next thing thrown "
                  "or used goes higher than where you are looking now.",
    "aim_lower": "Tilt the head down a notch without turning.",
    "hold": "Do nothing this tick; waiting is what the situation calls for.",
    "done": "The order is fully satisfied and should be cleared.",

    # --- Full Keyboard Gameplay: the game's own accessibility key map ---
    "walk_forward": "W: walk forward.",
    "walk_backward": "S: walk backward.",
    "strafe_left": "A: step sideways to the left, still facing the same way.",
    "strafe_right": "D: step sideways to the right, still facing the same way.",
    "look_up_slight": "Move the camera 15 degrees up.",
    "look_down_slight": "Move the camera 15 degrees down.",
    "look_up": "Move the camera 45 degrees up.",
    "look_down": "Move the camera 45 degrees down.",
    "look_left": "Move the camera 45 degrees left.",
    "look_right": "Move the camera 45 degrees right.",
    "look_up_left": "Move the camera 45 degrees up and left.",
    "look_up_right": "Move the camera 45 degrees up and right.",
    "look_down_left": "Move the camera 45 degrees down and left.",
    "look_down_right": "Move the camera 45 degrees down and right.",
    "look_up_smooth": "Move the camera up a small amount.",
    "look_down_smooth": "Move the camera down a small amount.",
    "look_left_smooth": "Move the camera left a small amount.",
    "look_right_smooth": "Move the camera right a small amount.",
    "look_center": "Level the camera back to the middle.",
    "cycle_item_left": "Select the hotbar slot to the left.",
    "cycle_item_right": "Select the hotbar slot to the right.",
    "inventory": "Open or close your own inventory.",
    "drop_item": "Drop what you are holding.",
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


# How long a container stays "just looked in". Long enough to break a loop,
# short enough that a farm's chest can be checked again later.
SEARCHED_RECENTLY_S = 45.0


def _recently_searched(state):
    """Positions of containers looked in moments ago, however malformed the memory."""
    out = set()
    for entry in state.get("containers_seen") or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("age_s") or 0) < SEARCHED_RECENTLY_S:
            out.add(f"{entry.get('x')},{entry.get('y')},{entry.get('z')}")
    return out


def place_candidates(state):
    """Block positions worth going to or opening, plus creatures to approach.

    Containers searched moments ago are left out: nothing has changed inside
    since, so offering them again only invites the same open-and-close loop.
    """
    searched = _recently_searched(state)
    # One of each kind before a second of any kind. Taking the nearest eight
    # offered five chests, two hoppers and a furnace, and dropped the shulker
    # box the order was naming — a candidate that is never listed cannot be
    # chosen, however clearly the order asks for it.
    stations = [s for s in _stations(state) if isinstance(s, dict)]
    ranked, ordered = {}, []
    for st in sorted(stations, key=lambda s: s.get("dist", 0)):
        key = f"{st['x']},{st['y']},{st['z']}" if "x" in st else st.get("id")
        if not key or str(key) in searched:
            continue
        rank = ranked.get(st.get("id"), 0)
        ranked[st["id"]] = rank + 1
        ordered.append((rank, st.get("dist", 0), str(key), st.get("desc") or str(key)))
    candidates = {}
    for _, _, key, desc in sorted(ordered)[:8]:
        candidates[key] = desc
    for e in list(state.get("in_frame") or [])[:4]:
        candidates[str(e["id"])] = e.get("desc") or e.get("type") or "entity"
    candidates["none"] = "Nothing listed is worth heading toward or opening."
    return candidates


def item_candidates(state):
    """Items carried, and results the recipe book says can be made.

    What is already in hand is left out: equipping it changes nothing, and
    seven ticks in a row once went to re-equipping a held sword.
    """
    candidates = {}
    inventory = state.get("inventory") or {}
    held = ((inventory.get("held") or {}).get("id") or "").split(":")[-1]
    for item, count in (inventory.get("counts") or {}).items():
        if item.split(":")[-1] == held:
            continue
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


# Which verbs make sense with a screen up, and which only make sense without
# one. Offering `attack` through an open chest is offering a fiction: the swing
# cannot land and the model has no way to know that. This is the candidate rule
# again — the model can only pick what it is shown.
# The semantic set: the verbs that mean something rather than naming a key.
# Kept apart from FULL_KEYBOARD_VERBS so neither mode can offer the other's.
SEMANTIC_VERBS = (
    "advance", "retreat", "strafe_left", "strafe_right",
    "turn_left", "turn_right", "aim_higher", "aim_lower",
    "jump", "mine_front", "place_block", "attack",
    "use_item", "use_item_hold", "equip", "open", "move_stack", "close",
    "craft", "aim", "hold", "done",
)

VERBS_WITH_SCREEN_OPEN = ("move_stack", "close", "hold", "done")
VERBS_NEEDING_CRAFTING_TABLE = ("craft",)
VERBS_NEEDING_A_SCREEN = ("move_stack", "close", "craft")


# The controls a person actually has at a keyboard: move, look, click, scroll.
# Every one is parameterless, so no target question is ever needed and nothing
# has to be enumerated for the model to point at.
# Minecraft's Full Keyboard Gameplay map, and nothing outside it. Every entry is
# a key a player presses; there is no verb here that means "craft this" or "open
# that", because there is no such key.
# The look keys are not offered individually. Choosing a direction to move the
# view is a different question from choosing what to do, and mixed into a
# thirty-eight way choice it lost every time: look_right sat at 0.02 while
# use_item took 0.54. `aim` is one verb here; which way is asked after.
AIM_KEYS = (
    "look_left", "look_right", "look_up", "look_down",
    "look_up_left", "look_up_right", "look_down_left", "look_down_right",
    "look_left_smooth", "look_right_smooth", "look_up_slight", "look_down_slight",
    "look_center",
)

FULL_KEYBOARD_VERBS = (
    "walk_forward", "walk_backward", "strafe_left", "strafe_right",
    "jump", "sneak", "sprint",
    "aim",
    *(f"slot_{n}" for n in range(1, 10)),
    "cycle_item_left", "cycle_item_right",
    "attack", "use_item", "use_item_hold", "inventory", "drop_item",
)
KEYBOARD_VERBS = FULL_KEYBOARD_VERBS


def available_verbs(state, controls="semantic"):
    """The verbs this situation actually allows, in criteria order."""
    if controls == "keyboard":
        # A screen being open changes nothing here: the keys still work, and
        # closing one is `close`, which this set deliberately does not include
        # because opening one is not in it either.
        #
        # The key for the slot already selected is left out: pressing it cannot
        # change anything, and a verb that does nothing never fails, so nothing
        # would ever catch the loop pressing it.
        inventory = state.get("inventory") or {}
        held = (inventory.get("held") or {}).get("id")
        held_slot = (inventory.get("held") or {}).get("slot")
        selected_key = f"slot_{int(held_slot) + 1}" if held_slot is not None else None
        # Slots holding nothing are no better than the one already selected.
        # With no hotbar detail, hide nothing: never remove a key on ignorance.
        hotbar = inventory.get("hotbar")
        filled = ({f"slot_{int(e['slot']) + 1}" for e in hotbar if e.get("slot") is not None}
                  if hotbar else None)
        # Using an empty hand does nothing at all, so those keys are not
        # offered while the hand is empty. Punching still works, so `attack`
        # stays.
        empty_handed = "held" in inventory and not held
        use_keys = {"use_item", "use_item_hold", "drop_item"}
        return [v for v in FULL_KEYBOARD_VERBS
                if v in ACT_CRITERIA and v != selected_key
                and (filled is None or not v.startswith("slot_") or v in filled)
                and not (empty_handed and v in use_keys)]
    container = state.get("container") or {}
    if container:
        allowed = set(VERBS_WITH_SCREEN_OPEN)
        # A crafting grid is the only screen where crafting is possible.
        if "craft" in str(container.get("screen") or ""):
            allowed.update(VERBS_NEEDING_CRAFTING_TABLE)
        return [v for v in SEMANTIC_VERBS if v in allowed]
    return [v for v in SEMANTIC_VERBS if v not in VERBS_NEEDING_A_SCREEN]


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


def build_questions(state, controls="semantic"):
    """The one batch asked every tick."""
    questions = {
        "act": {
            "type": "choice",
            "instructions": ACT_INSTRUCTIONS,
            "criteria": {v: ACT_CRITERIA[v] for v in available_verbs(state, controls)},
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

AIM_DESCRIPTIONS = {
    "look_left": "45 degrees left.",
    "look_right": "45 degrees right.",
    "look_up": "45 degrees up.",
    "look_down": "45 degrees down.",
    "look_up_left": "45 degrees up and left.",
    "look_up_right": "45 degrees up and right.",
    "look_down_left": "45 degrees down and left.",
    "look_down_right": "45 degrees down and right.",
    "look_left_smooth": "10 degrees left, a small correction.",
    "look_right_smooth": "10 degrees right, a small correction.",
    "look_up_slight": "15 degrees up, a small correction.",
    "look_down_slight": "15 degrees down, a small correction.",
    "look_center": "Level the view back to the horizon.",
}


def look_candidates(state):
    """The directions the view can move."""
    return dict(AIM_DESCRIPTIONS)



TARGET_QUESTION_FOR_VERB = {
    "attack": "target_entity",
    "open": "target_place",
    "place_block": "target_place",
    "mine_front": "target_place",
    "equip": "target_item",
    "craft": "target_item",
    # use_item and use_item_hold take no target: they are the right-click, and
    # what they use is whatever equip already put in the hand.
    "move_stack": "target_slot",
    "aim": "target_look",
}

_TARGET_BUILDERS = {
    "target_entity": entity_candidates,
    "target_place": place_candidates,
    "target_item": item_candidates,
    "target_slot": slot_candidates,
    "target_look": look_candidates,
}

_TARGET_SUBJECT = {
    "target_entity": "creature",
    "target_place": "block position or creature",
    "target_item": "item",
    "target_slot": "slot of the open container",
    "target_look": "direction",
}

# Only the parts of state the second question can actually use.
_TARGET_STATE_KEYS = {
    "target_entity": ("self", "order", "in_frame", "out_of_frame"),
    # containers_seen belongs here: choosing which container to open without it
    # means choosing blind, and a live run opened the same chest eighteen times.
    "target_place": ("self", "order", "hazards", "stations", "in_frame",
                     "containers_seen"),
    "target_item": ("self", "order", "inventory", "craftable"),
    "target_slot": ("self", "order", "inventory", "container"),
    "target_look": ("self", "order", "looking_at", "in_frame", "out_of_frame"),
}


# What choosing a verb actually needs. Measured on a live tick: the full
# twenty-key state cost 4541 tokens and answered at 0.17 confidence with the
# probability spread flat, while these six cost 2006 and answered the right verb
# at 0.34. Phase two gets its own slice, so nothing is lost — only the noise.
ACT_STATE_KEYS = ("order", "self", "inventory", "hazards", "in_frame",
                  "looking_at", "recent_decisions_desc")


def act_state(state):
    """The slice phase one is given."""
    slim = {k: state.get(k) for k in ACT_STATE_KEYS if k in state}
    container = state.get("container")
    if container:
        # The prose line plus what is actually inside, as counts. Deciding
        # whether to take anything means comparing the contents against what
        # the order needs, and a sentence is harder to compare than a tally.
        slim["container"] = {
            "desc": container.get("desc") or "a container is open",
            "holds": container.get("counts") or {},
        }
    # A few phrases for what is worth walking to or opening. Without these the
    # act question cannot see that any container exists — a live run held still
    # for twenty-four ticks with an unsearched shulker four metres away, because
    # nothing in its state said so. Only unsearched ones, and only the phrases.
    searched = _recently_searched(state)
    # One of each kind first. Sorted by distance alone the list is four chests
    # and the shulker box that matters never appears.
    by_kind, worth_going_to = {}, []
    stations = [s for s in _stations(state) if isinstance(s, dict)]
    for station in sorted(stations, key=lambda s: s.get("dist", 0)):
        key = f"{station.get('x')},{station.get('y')},{station.get('z')}"
        if key in searched:
            continue
        rank = by_kind.get(station.get("id"), 0)
        by_kind[station["id"]] = rank + 1
        worth_going_to.append((rank, station.get("dist", 0), station.get("desc") or key))
    worth_going_to = [text for _, _, text in sorted(worth_going_to)[:5]]
    if worth_going_to:
        slim["nearby"] = worth_going_to
    # The nearest few creatures, in view or not. Standing at a container the
    # bot faces a wall, so everything alive is out of frame and the act
    # question could see none of it — no reason to ever turn around.
    creatures = [e for e in (list(state.get("in_frame") or [])
                             + list(state.get("out_of_frame") or []))
                 if isinstance(e, dict)]
    creatures.sort(key=lambda e: e.get("dist", 999))
    if creatures:
        slim["creatures"] = [e.get("desc") or e.get("type", "?") for e in creatures[:3]]
    return slim


def build_act_questions(state, without=(), controls="semantic"):
    """Phase one: the verb, and the readings that ride along.

    `without` drops verbs that have turned out to be impossible here — one
    whose own target answer came back `none` has no parameter and cannot run,
    so re-offering it would only repeat the same failure. The model still
    chooses; it just chooses from what is left.
    """
    questions = build_questions(state, controls=controls)
    questions = {k: v for k, v in questions.items() if not k.startswith("target")}
    if without:
        criteria = {v: text for v, text in questions["act"]["criteria"].items()
                    if v not in set(without)}
        # Waiting is always possible, and must stay reachable.
        if not criteria:
            criteria = {v: ACT_CRITERIA[v] for v in ("hold", "done")}
        questions["act"] = dict(questions["act"], criteria=criteria)
    return questions


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
