# Jev-driven Minecraft harness — design

**Date:** 2026-09-19
**Branch:** `mca-26.2`
**Status:** design agreed, not yet implemented

An external harness drives a real Minecraft 26.3 client through the `mca-rcc` bridge.
TypeSafe's Jev makes every in-world decision; the harness senses, serves state,
and executes. The bot takes its orders from Minecraft chat and reacts
continuously underneath them.

---

## 1. Decisions

| Question | Decision |
|---|---|
| What the bot does | Follows chat instructions as standing orders, reacting continuously |
| Primitive granularity | Mid-level verbs; Jev picks the verb and the target, code resolves coordinates |
| Target world | Minecraft 26.3, `mca-rcc (26.3)` profile, `mca-rcc26` gamedir |
| Who decides | **Jev decides everything.** Code never arbitrates, gates, or overrides |
| State extensibility | Dynamic Lua probes, hot-reloaded, no rebuild |
| Location | `apps/mca/agents/jev-harness/`, committed beside the mod |

The harness lives in the repository because the previous one did not. Its Python
half (`harvester.py`, `killaura.py`, `drive_forever.py`) was never committed and
is gone.

---

## 2. What the bridge already provides

`mca-rcc` on 26.3 exposes a loopback JSON-RPC surface plus an SSE stream.

**Pull:** `player.state`, `player.inventory`, `player.equipped`, `world.block_at`,
`world.blocks_around{radius}`, `world.raycast{max}`, `container.state`, `chat.recent`.

**Pull via `debug.eval`:** arbitrary Lua on the client thread with the remap-safe
`api` global — `entitiesJson(radius)` (id, type, hostile, living, x/y/z, dist,
health), `inventoryJson()`, `containerJson()`, and every scalar read, composed in
one round-trip. The handler converts the returned Lua value to JSON (nested
tables, arrays inferred from keys, depth 8) and reports `ok`, `error`, `stdout`.
Default timeout 3s, maximum 15s.

**Push (SSE `/events`):** `chat`, `subtitle`, `joined`, `disconnected`, 5s
keepalive. The `subtitle` stream carries the game's own sound events and reveals
what position reads cannot, such as being attacked from behind.

**Act:** `press_key` (a latch: `{key, action}`), `set_rotation`, `set_velocity`,
`teleport` (1 block; longer jumps snap back), `pillar_up`, `stairs_up`,
`mine_block`, `mine_down`, `place_block`, `use_item`, `interact_entity`,
`chat.send`, container open/click/close, and the Lua verbs `useItem`, `swing`,
`attackBlock`, `placeBlock`, `attackEntity`, `selectSlot`, `setYaw`, `setPitch`.

**Vision:** `world.screenshot`, `world.screenshot_panorama`. Unused — Jev reads
text, not pixels — but useful for verifying traces.

---

## 3. What Jev is

`POST https://api.typesafe.ai/v1/systemone`, `model: "jev-latest"`. One `state`
blob and a map of named questions answered in parallel and independently.
Primitives: `choice` (one of a set, with probabilities and confidence), `noul`
(probability of yes), `score` (ordered levels).

Measured here on 2026-09-19 with a Minecraft-shaped state and three questions:
**HTTP 200 in 0.326s, `jev-1.13.0`, 713 input tokens.** Input costs $0.042/MTok;
output is free.

The Doom demo took structured text state at about 10Hz for roughly $7/hour. Its
stated point was reactivity to a state representation and instruction-following,
not play strength.

Batching is the economy: 13 questions in one call ran 10x faster and 12.2x
cheaper than 13 calls, because the state travels once. Questions cannot see each
other's answers.

The API key lives in the macOS login Keychain under service `typesafe-api-key`.
The harness reads it with `security find-generic-password -w`, so no file holds it.

---

## 4. Control flow

One process, two threads.

**SSE ingest** holds `/events` open and pushes timestamped `chat`, `subtitle`,
`joined` and `disconnected` events into a bounded deque.

**Main loop** assembles state, sends one batched Jev call, executes one bounded
action, repeats.

The tick is self-paced rather than fixed-rate. Each tick reads fresh state after
the previous action completes, so the loop settles near 2-4Hz and no decision can
be applied to a world that has already moved on. Each snapshot carries `tick_id`
and `captured_at`; if a round-trip exceeds 1.5s the harness discards the tick and
re-reads. It substitutes no decision of its own.

---

## 5. State

### 5.1 Dynamic Lua probes

Each file in `probes/` is a Lua chunk whose returned value becomes one top-level
state field named after the file. The builder concatenates them into a single
script per tick, wrapping each in `pcall`:

```lua
local S = {}
local function probe(n, fn)
  local ok, v = pcall(fn)
  S[n] = ok and v or { error = tostring(v) }
end
probe("self", function()
  return { x=api:x(), y=api:y(), z=api:z(), yaw=api:yaw(),
           health=api:health(), food=api:food(), held=api:heldItem() }
end)
probe("entities", function() return api:entitiesJson(32) end)
return S
```

Adding `probes/lava_check.lua` adds `state.lava_check` on the next tick, with no
rebuild and no Python change. The builder re-reads the directory when mtimes
change; `.lua.off` disables a probe. A failing probe degrades one field and ships
its error to Jev rather than faking a value.

Probes run on the client thread, so a slow probe stutters the game. Total budget
`timeout_ms: 500`. Probes sense; they never act.

### 5.2 Derived memory

Sensing stays in Lua. Derivation and memory stay in Python, which keeps the
history the client does not.

| Field | Built from | Decay |
|---|---|---|
| `in_frame[]` | bearing against `yaw`, inside the ~100° horizontal cone | live |
| `seen_recently[]` | entities that left the frame or the client's tracking | `age_s` per entry; dropped at 30s, or immediately on disconfirmation |
| `recent_decisions[]` | last 5 ticks: verb, target, `gap_ms`, measured outcome | dropped past 10s or 5 entries |
| `self_assessment[]` | Jev's own prior `in_danger` / `arrived` / `stuck` answers | `age_s` per entry; tagged as inferred, never as observed |

`entitiesJson` iterates `entitiesForRendering()`, so it sees only what the client
tracks. That limit is why `seen_recently` exists.

Field-of-view alone cannot distinguish visible from behind-a-wall, so
`api:canSee(entityId)` raycasts from the player's eyes to a given entity id.
Each call raycasts, so probes ask about the few entities that matter rather
than everything in range.

**What a client cannot know about intent.** A first live run reported four
peacefully idling zombified piglins as `hostile: true`, because `hostile` is
`instanceof Enemy` — class membership, not intent. The obvious fixes are
unavailable: `Mob.getTarget()` and `NeutralMob.isAngry()` read plain fields
written only by server-side AI, so on a client they are permanently null and
false. A `targeting_me` built on them would report "no threat" with total
confidence about a piglin mid-charge, which is the same lie inverted.

`entitiesJson` therefore reports three separate things and conflates none:

| Field | Means | Source |
|---|---|---|
| `hostile` | belongs to a monster class | `instanceof Enemy` |
| `aggressive` | this mob's attack goal is running | `Mob.isAggressive()`, bit `0x04` of the synced `DATA_MOB_FLAGS_ID` |
| `facing_me` | its head is aimed within 30° of the player | synced head yaw |

`aggressive` is the only server-authoritative hostility bit a client can see;
`facing_me` is the qualifier that turns it into "aggressive *at me*".

### 5.3 Natural-language descriptions

Every spatial fact ships twice, numeric and phrased:

```json
{"id": 41, "type": "creeper", "x": 118, "y": 64, "z": -44,
 "dist": 6.2, "rel_yaw": -178, "dy": -1,
 "desc": "creeper at (118,64,-44) — behind you, 178° left, 6.2m away, 1 block below"}
```

Bearing buckets from the signed relative yaw, so the sign carries the turn
direction: ahead (±0-22°), ahead-left/right (±23-67°), left/right (±68-112°),
behind-left/right (±113-157°), behind (±158-180°). Vertical reads as `3 blocks
above`, `level`, `1 block below`. Visibility reads as `in frame`, `out of frame`,
or `last seen 4.2s ago`.

The same phrasing covers `self`, `hazards`, `order`, `seen_recently`,
`recent_decisions` and `tick`. These strings also become the criteria of the
`target` question, so Jev selects between readable options rather than entity ids.

Minecraft yaw is 0 = +Z, 90 = −X, so bearing is `atan2(-dx, dz) - yaw`,
normalized to ±180. A wrong convention produces descriptions that read perfectly
and lie completely, so the bearing function gets unit tests against known cases
before anything else runs.

### 5.4 The self-clock

Measured numbers, no verdicts. A boolean such as `"slow": true` would be code
interpreting rather than serving.

```json
"tick": {
  "last_gap_ms": 412, "p50_gap_ms": 380, "p90_gap_ms": 640,
  "model_latency_ms": 326, "action_ms": 300, "next_decision_in_ms": 300,
  "verb_duration_ms": {"advance": 300, "retreat": 300, "jump": 150,
    "turn_toward": 0, "mine_front": 1000, "place_block": 0,
    "attack": 0, "use_item": 0, "hold": 150, "done": 150}
}
```

`verb_duration_ms` comes from the dispatcher config and therefore cannot drift
from reality. It tells Jev the price of each verb: `mine_front` commits a second
of blindness, `turn_toward` commits nothing.

### 5.5 Hazards

Because code holds no veto, `probes/hazard.lua` surfaces `lava_within`,
`drop_ahead`, `void_below`, `block_ahead` and `health_delta_2s` as top-level
named fields. Danger must be as visible to Jev as the order is.

---

## 6. Questions

One batched call per tick:

```python
questions = {
  "act":       choice(advance | retreat | turn_toward | jump | mine_front
                      | place_block | attack | use_item | hold | done),
  "target":    choice(candidates enumerated from in_frame, seen_recently, order),
  "arrived":   noul(...),
  "in_danger": noul(...),
  "stuck":     noul(...),
}
```

`act` alone drives execution. Whatever comes back runs. No thresholds, no
overrides, no precedence rules.

The verb set must therefore stay complete: every escape the harness might need
has to be selectable, including `retreat`, `jump`, `turn_toward`, `hold` and
`done`. A way out that is not a verb cannot be taken.

`target` is speculative. It is asked every tick and consumed only when `act` is
`advance`, `turn_toward` or `attack`. Code enumerates the candidates; Jev picks
among them. Uncertainty on unused branches is ignored.

`arrived`, `in_danger` and `stuck` gate nothing. They ride in the same batch and
return as `self_assessment` in the next tick's state, so Jev sees what it
believed 400ms ago.

Criteria carry concrete thresholds. A first smoke test returned `in_danger: 0.44`
for a zombie 18 blocks away, which is the model reporting that "immediate danger"
was undefined:

```
in_danger  true:  "A hostile is within 4 blocks, or health fell in the last 2 seconds."
           false: "No hostile within 4 blocks and health is steady."
arrived    true:  "Player is within 2 blocks of the destination in `order`."
stuck      true:  "`recent_decisions` show the same verb 3+ times with moved_m under 0.5."
```

The `act` instructions state the clock: the chosen action runs for the time in
`tick.verb_duration_ms`, and no further decision arrives for about
`tick.next_decision_in_ms`.

### Orders

A new chat line from the owner triggers a separate intake call — `order_kind`
(goto, follow, gather, attack, stop) and `order_target` over candidates — because
those options depend on parsing the line first. It fires on an SSE event, not
every tick, and costs nothing at rest. The resulting order is a Python dataclass
the harness holds until superseded.

---

## 7. Dispatcher

| Verb | Call | Bound |
|---|---|---|
| `advance` | `set_rotation` to target bearing, `press_key forward`, release | 300ms |
| `retreat` | `press_key back`, release | 300ms |
| `turn_toward` | `player.set_rotation{yaw,pitch}` | instant |
| `jump` | `press_key jump`, release | 150ms |
| `mine_front` | `world.mine_block` at the raycast hit | ≤1s |
| `place_block` | `world.place_block` | instant |
| `attack` | `api:attackEntity(id)` on the chosen target | instant |
| `use_item` | `api:useItem()` | instant |
| `hold` | sleep | 150ms |
| `done` | sleep, clear the standing order | 150ms |

Bearing math and target coordinates are arithmetic over data Jev already chose,
not decisions.

Crafting, smelting and container work are not in this table yet. When they
arrive they stay equally generic: `craft.item` takes a result item id and lets
the server place the recipe from the player's own recipe book, so no recipe is
named in Java or in Python. A task like running a gold farm is then expressed
entirely as the order text, never as code.

`press_key` latches rather than pulses, so a bounded press is press, sleep,
release. **Every tick begins by releasing all movement keys**, and `atexit` plus
signal handlers do the same. A held key surviving the process is the harness's
worst failure: the player keeps walking.

---

## 8. Failure handling

Failures become data. The harness never substitutes judgment.

- A probe that raises ships `{"error": "..."}` in its field. Jev sees the broken sensor.
- A closed bridge or client parks the loop, which retries. No action, no invented decision.
- 429 and 529 back off exponentially.
- A snapshot older than 1.5s is discarded, re-read and re-asked.

---

## 9. Replay

Every tick appends one line to `traces/<session>.jsonl`: `tick_id`, full state,
question set, raw answers with probabilities, chosen verb, measured outcome.

`replay.py` re-runs recorded states against edited questions offline, with no
Minecraft and no client thread, and diffs the answers. That is how `in_danger:
0.44` gets fixed without standing in a cave, and it turns every run into a
dataset.

Metrics it yields: stall rate, deaths per hour, order-completion time, and
measured dollars per hour.

---

## 10. Cost

Measured on 2026-09-19 against a real 6-entity Nether tick, not estimated:

| | input tokens | $/hour @ 3Hz |
|---|---|---|
| API floor (empty state, one question) | 285 | — |
| First implementation | 2350 | $1.07 |
| After trimming | 1895 | $0.86 |

State alone, floor subtracted, fell 1435 → 1103 tokens.

**The earlier estimate in this document was wrong, and the way it was wrong is
worth keeping.** It budgeted "~950 tokens of state" and then costed the tick as
if state were the whole request. It is not: the fixed API floor and the question
block together spend about 905 tokens before a single entity is described, and
the largest single item is the `act` criteria — the very prose this design says
must stay concrete and complete. Halving the original 2350 would have left
roughly 270 tokens for the entire world.

Trimming came from rounding (`18.92188262939453` → `18.9`), dropping fields the
`desc` string already carries, and stripping the `minecraft:` prefix from vanilla
ids while keeping modded namespaces intact. Nothing was cut from `desc`, the
hazard fields, or entity ids, and entity coordinates were restored after they
turned out to be load-bearing for `advance` and `place_block`.

## 11. Layout

```
apps/mca/agents/jev-harness/
  harness.py    # the loop
  bridge.py     # RPC + SSE client
  state.py      # probe registry, memory layers, descriptions, self-clock
  questions.py  # question definitions and criteria
  dispatch.py   # verb table and bounded execution
  replay.py     # offline re-run against recorded traces
  probes/*.lua  # dynamic state, hot-reloaded
  traces/       # one JSONL per session
```

---

## 12. Open items

1. Decide the trace retention policy; `drive.log` reached 37MB on the last harness.
2. `mca-26.2` has no open pull request.

Closed on 2026-09-19: `api:canSee(entityId)` is in `ScriptApi` and built into the
deployed jar, and `geometry.py` ships with 27 passing tests pinning the yaw
convention.
