# sleet1213

Durable Claude chat agent for the `sleet1213` Twitch channel — fork of [r33drichards/ted](https://github.com/r33drichards/ted). Powered by the Claude Agent SDK, Temporal workflows, and an IRC bridge that connects directly to Twitch chat. **No web frontend** — all interaction is through IRC/Twitch chat only.

The agent is the brain behind a live Minecraft bot stream — it talks to the operator (`lokvolt`) and public viewers in Twitch chat and drives `btone-bot` (a headless Minecraft client) over a local RPC bridge.

## Architecture

- `src/activities.ts` — Temporal activities: `streamClaude` (Agent SDK query), `persistTurn`, `generateTitle`
- `src/memory-mcp.ts` — In-process MCP server for memory CRUD (working/short_term/long_term)
- `src/workflows.ts` — Temporal chatSession workflow
- `src/webhook.ts` — Hono HTTP API (message ingestion, SSE streaming)
- `src/irc-bridge.ts` — IRC bridge (InspIRCd on Railway private network)
- `src/nick-groups.ts` — Nick-to-group resolver, per-group agent config
- `src/db.ts` — Postgres schema + CRUD (messages, sessions, mcp_servers, memories)
- `src/publish.ts` — Redis Streams for SSE deltas (delta, thinking, tool_call, turn_end)
- `.claude/skills/` — Agent skills (auto-discovered, self-editable)

## Nick Groups & Tool Restrictions

Access control is enforced via nick groups defined in `/etc/sleet1213/nick-groups.json`. Each group maps a set of IRC nicks to a specific agent configuration with restricted tools.

Tool restrictions are enforced via the SDK `tools` option (controls which built-in tools exist) and `allowedTools` (auto-approves those tools without permission prompts). Both are set from `agentConfig.allowedTools` in the nick group config.

### Admin (lokvolt)

Full system access — can drive the EC2 host, systemd services, build code, and self-edit skills:

- **Built-in tools:** Read, Write, Edit, Glob, Grep, Bash, WebSearch, WebFetch, Skill, Agent, TodoWrite, NotebookEdit
- **MCP tools:** `mcp__*` (memory MCP, user-configured MCP servers)
- **Plugins:** REPO + LOCAL skill directories
- **Session:** `irc-sleet1213`

### Public (everyone else)

Restricted to Skill + MCP tools — can use skills, `mcp-js` for sandboxed JS + filesystem, and `btone` for Minecraft:

- **Built-in tools:** Skill, `mcp__*`
- **MCP tools:**
  - `mcp-js` — JavaScript runtime with **Rego-policy-sandboxed filesystem access** (read-write LOCAL skills, read-only REPO skills)
  - `btone` — direct Minecraft bot control (50+ RPC methods)
- **Minecraft RPC:** Inside mcp-js, `mcp.listTools('btone')` discovers all bot RPC methods; `mcp.callTool('btone', 'player_state', {})` calls them. The btone-mcp-bridge (`/home/ubuntu/mca-src/bin/btone-mcp-bridge.mjs`) auto-generates MCP tools from the OpenRPC spec at runtime.
- **Plugins:** REPO + LOCAL skill directories (same as admin — can discover and invoke skills)
- **Skill editing:** Via mcp-js `fs` module (not Read/Write/Edit built-in tools). Filesystem access is enforced by a Rego policy at the Rust runtime level — cannot be bypassed by prompt injection.
  - **LOCAL (read-write):** `/home/ubuntu/.local/share/sleet1213/plugin/skills/`
  - **REPO (read-only):** `/home/ubuntu/twitch-mc/apps/sleet1213/ted-plugin/skills/`
  - Policy file: `/etc/sleet1213/policies/skill-filesystem.rego`
  - Config: `/etc/sleet1213/policies/skills-fs.json`
- **Session:** `irc-sleet1213`

#### Skill editing examples (mcp-js)

```javascript
// List existing skills
const skills = await fs.readdir("/home/ubuntu/.local/share/sleet1213/plugin/skills/");

// Read a skill
const content = await fs.readFile("/home/ubuntu/.local/share/sleet1213/plugin/skills/farm-loop/SKILL.md", "utf-8");

// Create a new skill
await fs.mkdir("/home/ubuntu/.local/share/sleet1213/plugin/skills/my-skill", { recursive: true });
await fs.writeFile("/home/ubuntu/.local/share/sleet1213/plugin/skills/my-skill/SKILL.md", `---
name: my-skill
description: Does something cool
---
# My Skill
Instructions here...
`);
```

## Scheduled Prompts & Privilege Isolation

Temporal Schedules fire prompts on a cron or one-shot basis. Two safety rails:

1. **Session hardcoded** — `schedule_create` in `memory-mcp.ts` always uses `SCHEDULE_SESSION = 'irc-sleet1213'`. The session parameter is not exposed to agents, preventing output from going to a session the IRC bridge doesn't subscribe to.
2. **Least-privilege execution** — All scheduled prompts execute with `DEFAULT_PUBLIC_CONFIG` regardless of who created them. This prevents privilege escalation.

- `schedule-activities.ts` explicitly attaches `DEFAULT_PUBLIC_CONFIG` to every scheduled webhook POST.
- `activities.ts` defaults to `DEFAULT_PUBLIC_CONFIG` when no `agentConfig` is provided (defense-in-depth).
- Schedule CRUD is exposed via `mcp__ted__schedule_*` tools (available to both admin and public agents).

## baritone_goto Safety Check

The `baritone_goto` MCP tool (via btone-mcp-bridge) has a built-in safety check that prevents the bot from navigating into solid blocks. When Baritone uses `GoalBlock(x, y, z)` it pathfinds the bot to stand AT the exact destination — if that block is a chest, crafting table, or other solid block, Baritone will break it to get there.

### How it works

When all three coordinates (x, y, z) are provided, the bridge calls `world.block_at` to inspect the destination before dispatching the goto. If the block is solid (not air/flowers/torches/etc.), the call is **refused** with an error that:
1. Names the block at the destination (e.g. `minecraft:chest`)
2. Suggests 4 safe offset positions (±1 on x and z axes)
3. Explains how to override with `force: true`

### Usage

```
# Safe — go next to a chest, not on it
baritone_goto { x: 1014, y: 69, z: 827 }   # refused if solid block there
baritone_goto { x: 1015, y: 69, z: 827 }   # offset by 1 — safe

# Override when you intentionally want to navigate into a solid block
baritone_goto { x: 1014, y: 69, z: 827, force: true }

# GoalXZ and GoalYLevel modes (missing coords) skip the safety check
baritone_goto { x: 1014, z: 827 }           # GoalXZ — no block check
baritone_goto { y: 69 }                      # GoalYLevel — no block check
```

### When to use `force: true`

Only when you intentionally need to path into a solid block — e.g. mining through terrain or replacing a block. For container interaction (chests, crafting tables, furnaces), always offset by 1 block and use `container.open` or `craft.open_table` instead.

## Chat Ingress Filters

The IRC bridge routes messages through nick groups (defined in `/etc/sleet1213/nick-groups.json`):

- Admin nicks (e.g. `lokvolt`) get full agent capabilities
- All other nicks (`*` wildcard) get the public restricted agent
- `requireMention: true` means messages must mention `@sleet1213` to trigger an agent turn

Legacy fallback: `IRC_ALLOWED_NICKS` / `IRC_REQUIRE_MENTION` env vars are used if no nick-groups config exists.

## E2E Testing

```
node e2e/irc-e2e.mjs [--message "text"] [--timeout 90]
```

## Deploy

Push to master. Railway auto-deploys `ted` and `ted-irc-bridge`.

After workflow-shape changes, terminate the old workflow:
```
railway ssh -s ted -- 'node -e "
const { Connection, Client } = require(\"@temporalio/client\");
(async () => {
  const conn = await Connection.connect({ address: process.env.TEMPORAL_ADDRESS });
  const client = new Client({ connection: conn });
  await client.workflow.getHandle(\"chat:irc-ted\").terminate(\"deploy reset\");
  process.exit(0);
})();
"'
```
