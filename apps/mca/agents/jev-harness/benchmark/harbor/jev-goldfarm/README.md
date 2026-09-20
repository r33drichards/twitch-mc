# jev-goldfarm-decisions

A Harbor task that replays recorded Minecraft states through the Jev harness
and scores the decisions.

## What is being evaluated

Not the model in isolation — the **design around it**: which verbs are offered
in a situation, how the criteria are worded, what state the act question is
given, and what the order says. Those are the things that moved the number
during development; the model and its prompt contract stay fixed.

## Cases

Built by `benchmark/cases.py` from `traces/*.jsonl`, which the harness writes
one line per tick. Four situations so far, eight examples each:

| case | acceptable | controls |
|---|---|---|
| `snowballs_carried_but_not_held` | any slot/cycle/equip that selects them | keyboard |
| `snowball_held_and_nothing_in_the_way` | `use_item` | keyboard |
| `aimed_at_a_block_with_snowball_held` | `aim` and the look keys | keyboard |
| `container_holds_something_wanted` | `move_stack` | semantic |
| `container_holds_nothing_wanted` | `close` | semantic |

Container cases are scored with the semantic verbs because no key on a keyboard
takes one slot out of a chest.

## Labels are judgement, not ground truth

The game does not say what the right move was. These labels encode what this
project established by watching the bot fail: select an item before using it,
aim before throwing, close a container holding nothing you need. The score
measures agreement with that reading — worth stating plainly, because a
benchmark whose labels are opinions can be gamed by changing the opinion.

## Running it

```bash
export TYPESAFE_API_KEY=...        # or macOS Keychain: service typesafe-api-key
python3 benchmark/cases.py --summary   # rebuild cases from traces
python3 benchmark/score.py             # prints SCORE <0..1> on its last line
```

Under Harbor, with Docker available:

```bash
harbor exec benchmark/harbor/jev-goldfarm
```

## Baseline

At the time of writing: **0.75**, with `container_holds_something_wanted` at
0/8 — it closes the chest instead of taking the gold. The live runs these cases
were recorded from scored 9/32.
