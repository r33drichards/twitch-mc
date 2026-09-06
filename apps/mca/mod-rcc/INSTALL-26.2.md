# mca-rcc on Minecraft 26.2 — install & configure

`mca-rcc` is a **pure-Java Fabric _client_ mod** that exposes the local
Minecraft client over a loopback JSON-RPC + SSE HTTP bridge, so an external
agent can read the world and drive the player. No Baritone, no Meteor — only
server-legal input-synthesis primitives and world reads.

This document covers the **26.2** target. The port from the old 1.20.1 build is
non-trivial because Minecraft 26.x changed the entire mappings/toolchain story.

---

## What changed for 26.x (why the old build won't work)

Yarn + Intermediary mappings were discontinued after 1.21.11. From 26.x the
game ships **unobfuscated** (Mojang official names + parameter names baked in),
so the mod builds against **Mojang official mappings** using the
**non-remapping** Loom plugin:

| Thing            | 1.20.1 (old)                          | 26.2 (this build)                         |
|------------------|---------------------------------------|-------------------------------------------|
| Loom plugin      | `fabric-loom` 1.7.4                    | `net.fabricmc.fabric-loom` **1.17.16**    |
| Mappings         | `mappings("net.fabricmc:yarn:…:v2")`  | none — Mojang names are already in the jar |
| Loader/API deps  | `modImplementation(...)`              | plain `implementation(...)`               |
| Packaged artifact| `remapJar`                            | `jar` (include/JiJ still attaches to it)  |
| Minecraft        | `1.20.1`                              | `26.2`                                    |
| Fabric API       | `0.92.9+1.20.1`                       | `0.155.2+26.2`                            |
| Fabric loader    | `0.19.2`                              | `0.19.3`                                  |
| JDK              | 17                                    | **25**                                    |
| Gradle           | 8.10.2                                | **9.6.1**                                 |

Two Gradle-9 gotchas already handled in `build.gradle.kts`:
- Gradle 9 no longer bundles the JUnit Platform launcher → we declare
  `testRuntimeOnly("org.junit.platform:junit-platform-launcher")`.
- `libraries.minecraft.net` is added as a repo (Mojang-hosted MC libraries).

---

## Prerequisites

- **JDK 25** (Temurin 25). With Nix: `nix shell nixpkgs#temurin-bin-25`.
- A Minecraft **26.2** client with **Fabric Loader ≥ 0.16.0** installed.
- **Fabric API `0.155.2+26.2`** dropped in the same `mods/` folder.
- Internet access on first build (Loom downloads the 26.2 client + libraries).

---

## Build

From `apps/mca/mod-rcc`:

```bash
nix shell nixpkgs#temurin-bin-25 --command bash -c '
  export JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"
  ./gradlew --no-daemon assemble -x test
'
```

(The Gradle toolchain is pinned to Java 25; `JAVA_HOME` just guarantees the
wrapper launches on a 25 JVM. First run takes a few minutes while Loom fetches
the game; subsequent runs are ~1–2 min.)

Output: `build/libs/mca-rcc-0.1.0.jar`.

---

## Install

Copy the jar (plus Fabric API) into a Fabric 26.2 profile's `mods/`:

```bash
PROFILE="$HOME/Library/Application Support/minecraft/mca-rcc26"   # your fabric profile
cp build/libs/mca-rcc-0.1.0.jar "$PROFILE/mods/"
# and, once:
cp /path/to/fabric-api-0.155.2+26.2.jar "$PROFILE/mods/"
```

The mod is a **client mod** — it loads on the client only. Changes take effect
**on the next client launch**; hot-swapping the jar while the game runs does
nothing.

---

## Configure & run

There is almost nothing to configure — the bridge is deliberately opinionated:

- **Bind:** `127.0.0.1:25591`, loopback only. Not reachable off-box.
- **Auth:** disabled by default (loopback is the security boundary). The server
  supports `Authorization: Bearer <token>` if a token is ever set, but the
  shipped build starts with none.
- **Bootstrap file:** on client start the mod writes
  `<fabric config dir>/btone-bridge.json` →
  `{"port":25591,"token":null,"version":"0.1.0"}` for a runner to discover.
- **Behavior tweaks** applied automatically on `CLIENT_STARTED`:
  `pauseOnLostFocus=false` (so the bot keeps running when unfocused) and
  render/simulation distance forced to **6** (keeps the render thread from
  saturating during bot mining, which otherwise gets the client kicked).

Endpoints:

| Path       | Method | Purpose                             |
|------------|--------|-------------------------------------|
| `/health`  | GET    | `{"ok":true}` liveness              |
| `/rpc`     | POST   | JSON-RPC: `{"method","params"}`     |
| `/events`  | GET    | Server-Sent Events stream           |

### Verify

```bash
curl -s localhost:25591/health
# {"ok":true}

curl -s localhost:25591/rpc -d '{"method":"player.state","params":{}}'
```

---

## RPC surface (the parts an agent actually uses)

- **State:** `player.state`, `world.block_at{x,y,z}`, `world.blocks_around{radius}`
  (returns the nearest ~3071 non-air blocks, ~r16 bubble).
- **Movement:** `player.set_velocity{vx,vy,vz}` (the only reliable mover;
  `vy≈0.45` to hop), `player.set_rotation{yaw,pitch}`, `player.set_hotbar_slot{slot}`.
  Yaw: `0=+Z (S)`, `90=-X (W)`, `180=-Z (N)`, `270=+X (E)`.
- **Blocks:** `world.mine_block{x,y,z}` (reach ~4.7), `world.place_block{x,y,z,hand,side}`,
  `world.use_item`.
- **Containers:** `container.open/state/click` (`QUICK_MOVE` = shift-transfer).
- **Chat:** `chat.send`.
- **Eval:** `debug.eval{code}` — runs Lua in-JVM with an `api` global.

### Entity detection + targeted combat (new in this build)

Exposed through `debug.eval`'s `api` object (see `ScriptApi.java`):

- `api:entitiesJson(radius)` → JSON array of nearby loaded entities,
  nearest-first: `[{"id","type","hostile","living","x","y","z","dist","health"}]`.
  `hostile` = implements the `Enemy` marker (monsters).
- `api:attackEntity(id)` → melee-attack the entity with that network id; returns
  `true` if found. Uses the server-side entity id, so it hits regardless of
  crosshair. Pair with `set_rotation` aimed at the entity for animation fidelity.

Together these are enough to build a **targeted KillAura** entirely agent-side
(enumerate hostiles in range → face → attack) without any client-side aimbot —
which is what the mangrove harvester's survival layer does.

---

## Troubleshooting

- **`Could not resolve com.mojang:minecraft:26.2`** — you're offline on a cold
  cache, or missing the `libraries.minecraft.net` repo. Build once with network.
- **JUnit "no TestEngine" / launcher errors** — ensure
  `testRuntimeOnly("org.junit.platform:junit-platform-launcher")` is present
  (Gradle 9 stopped bundling it). `assemble -x test` sidesteps it entirely.
- **Toolchain error demanding Java 25** — install Temurin 25;
  `nix shell nixpkgs#temurin-bin-25` is the supported path.
- **Mod loads but `/health` refuses** — the bridge binds only after the client
  reaches the main menu / world; confirm the client is actually running this jar
  (check `logs/latest.log` for `btone-mod-c` lines) and that it's a **26.2**
  profile, not 1.20.1.
- **Edited the jar but nothing changed** — you must **relaunch the client**; the
  bridge is initialized once at startup.
