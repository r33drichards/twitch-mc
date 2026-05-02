# sleet1213

Durable Claude chat agent for the `sleet1213` Twitch channel — fork of [r33drichards/ted](https://github.com/r33drichards/ted). Powered by the Claude Agent SDK, Temporal workflows, and an IRC bridge that connects directly to Twitch chat. **No web frontend** — all interaction is through IRC/Twitch chat only.

The agent is the brain behind a live Minecraft bot stream — it talks to the operator (`lokvolt`) and public viewers in Twitch chat and drives `btone-bot` (a headless Minecraft client) over a local RPC bridge.

This app lives in the **twitch-mc monorepo** at `apps/sleet1213/`. All services run from this directory — there is no separate standalone checkout.

## Architecture

- `src/activities.ts` — Temporal activities: `streamClaude` (Agent SDK query), `persistTurn`, `generateTitle`
- `src/memory-mcp.ts` — In-process MCP server for memory CRUD (working/short_term/long_term)
- `src/workflows.ts` — Temporal chatSession workflow
- `src/webhook.ts` — Hono HTTP API (message ingestion, SSE streaming)
- `src/irc-bridge.ts` — IRC bridge (Twitch chat via IRC)
- `src/nick-groups.ts` — Nick-to-group resolver, per-group agent config (includes `DEFAULT_PUBLIC_CONFIG` fallback)
- `src/db.ts` — Postgres schema + CRUD (messages, sessions, mcp_servers, memories)
- `src/publish.ts` — Redis Streams for SSE deltas (delta, thinking, tool_call, turn_end)
- `.claude/skills/` — Agent skills (auto-discovered, self-editable)

## Nick Groups & Tool Restrictions

Access control is enforced via nick groups defined in `/etc/sleet1213/nick-groups.json` (managed by system-manager from `etc/sleet1213/nick-groups.json` in the repo root). Each group maps a set of IRC nicks to a specific agent configuration with restricted tools.

Tool restrictions are enforced via the SDK `tools` option (controls which built-in tools exist) and `allowedTools` (auto-approves those tools without permission prompts). Both are set from `agentConfig.allowedTools` in the nick group config.

**Important:** When changing tool restrictions, you must update BOTH:
1. `/etc/sleet1213/nick-groups.json` — the deployed config read by the IRC bridge
2. `src/nick-groups.ts` `DEFAULT_PUBLIC_CONFIG` — the hardcoded fallback used by scheduled prompts

The `systemPromptSuffix` in nick-groups.json also tells the agent what tools it has. If you add a tool to `allowedTools` but the system prompt says "you don't have X", the agent will refuse to use it.

### Admin (lokvolt)

Full system access — can drive the EC2 host, systemd services, build code, and self-edit skills:

- **Built-in tools:** Read, Write, Edit, Glob, Grep, Bash, WebSearch, WebFetch, Skill, Agent, TodoWrite, NotebookEdit
- **MCP tools:** `mcp__*` (memory MCP, user-configured MCP servers)
- **Plugins:** REPO + LOCAL skill directories
- **Session:** `irc-sleet1213`

### Public (everyone else)

Restricted to Agent + Skill + MCP tools:

- **Built-in tools:** Agent, Skill, `mcp__*`
- **MCP tools:**
  - `mcp-js` — JavaScript runtime with **Rego-policy-sandboxed filesystem access** (read-write LOCAL skills, read-only REPO skills)
  - `btone` — direct Minecraft bot control (50+ RPC methods)
  - `minecraft-data` — game data lookup (recipes, items, blocks, food, entities, biomes, enchantments)
  - `ted` — memory CRUD + schedule management (no MCP server management)
- **Minecraft RPC:** Inside mcp-js, `mcp.listTools('btone')` discovers all bot RPC methods; `mcp.callTool('btone', 'player_state', {})` calls them.
- **Plugins:** REPO + LOCAL skill directories
- **Skill editing:** Via mcp-js `fs` module. Filesystem access is enforced by Rego policy.
  - **LOCAL (read-write):** `/home/ubuntu/.local/share/sleet1213/plugin/skills/`
  - **REPO (read-only):** `/home/ubuntu/twitch-mc/apps/sleet1213/ted-plugin/skills/`
  - Policy: `/etc/sleet1213/policies/skill-filesystem.rego`
- **Session:** `irc-sleet1213`

## Scheduled Prompts & Privilege Isolation

Temporal Schedules fire prompts on a cron or one-shot basis. Two safety rails:

1. **Session hardcoded** — `schedule_create` in `memory-mcp.ts` always uses `SCHEDULE_SESSION = 'irc-sleet1213'`.
2. **Least-privilege execution** — All scheduled prompts execute with `DEFAULT_PUBLIC_CONFIG` regardless of who created them.

## Chat Ingress Filters

The IRC bridge routes messages through nick groups:

- Admin nicks (e.g. `lokvolt`) get full agent capabilities
- All other nicks (`*` wildcard) get the public restricted agent
- `requireMention: true` means messages must mention `@sleet1213` to trigger an agent turn

## E2E Testing

```
node e2e/irc-e2e.mjs [--message "text"] [--timeout 90]
```

## Deploy

You are running on the host machine that the services deploy to. Infrastructure is managed by [numtide/system-manager](https://github.com/numtide/system-manager) via Nix. The flake at the repo root (`/home/ubuntu/twitch-mc/flake.nix`) defines all systemd units, config files under `/etc/`, and policies.

### Activating changes

After editing config files, service units, or Nix modules, deploy from the repo root:

```bash
cd /home/ubuntu/twitch-mc
nix run github:numtide/system-manager -- switch --flake .#default --sudo
```

(`nix` is at `/nix/var/nix/profiles/default/bin/nix` if not on `$PATH`.)

This rebuilds the Nix closure and activates it — updating `/etc/` files, reloading systemd daemons, and restarting services whose unit files changed. There are also Makefile shortcuts:

| Target | Description |
|--------|-------------|
| `make activate` | Run system-manager switch locally |
| `make build` | Build the closure without activating (sanity check) |
| `make diff` | Dry-run: show what `activate` would change |

### What system-manager does NOT do

- It does **not** restart services that merely read a config file at startup (like `nick-groups.json`). You must manually restart the consuming service:

```bash
systemctl --user restart sleet1213-irc      # picks up nick-groups.json changes
systemctl --user restart sleet1213-worker   # picks up source code changes
```

- It does **not** clear Temporal workflow state. If the workflow has a cached `sdkSessionId` from an old tool config, you may need to terminate it:

```bash
/home/ubuntu/.temporalio/bin/temporal workflow terminate \
  --address 127.0.0.1:7233 \
  --workflow-id "chat:irc-sleet1213" \
  --reason "reset for new config"
```

### Managed services

**User-level** (`systemctl --user`):

| Service | Description |
|---------|-------------|
| `sleet1213-temporal` | Temporal dev server (port 7233, UI 8233) |
| `sleet1213-webhook` | HTTP API + SSE streaming (port 8787) |
| `sleet1213-worker` | Temporal worker (Claude Agent SDK) |
| `sleet1213-irc` | IRC bridge (Twitch chat in/out) |
| `sleet1213-hud-poller` | Schedule HUD poller (Temporal -> CronHud JSON) |
| `sleet1213-mc-bridge` | MC chat bridge (btone SSE -> webhook) |
| `opencode-web` | OpenCode web UI |

**System-level** (`systemctl`):

| Service | Description |
|---------|-------------|
| `btone-bot` | Minecraft headless client |
| `btone-stream` | OBS/streaming pipeline |
| `btone-audio` | PulseAudio game audio |
| `xorg-headless` | Headless X server for rendering |

### Managed config files

| Deployed path | Repo source |
|---------------|-------------|
| `/etc/sleet1213/nick-groups.json` | `etc/sleet1213/nick-groups.json` |
| `/etc/sleet1213/policies/skill-filesystem.rego` | `etc/sleet1213/policies/skill-filesystem.rego` |
| `/etc/sleet1213/policies/skills-fs.json` | `etc/sleet1213/policies/skills-fs.json` |
| `/etc/X11/xorg-headless.conf` | `etc/X11/xorg-headless.conf` |

### Useful commands

```bash
systemctl --user status sleet1213-irc          # check service status
systemctl --user restart sleet1213-irc         # restart a service
systemctl --user list-units 'sleet1213-*'      # list all sleet1213 services
journalctl --user -u sleet1213-irc -f          # tail logs
journalctl --user -u sleet1213-worker -f       # tail worker/SDK logs
```

### Typical deploy workflow

1. Edit files in `/home/ubuntu/twitch-mc/`
2. `nix run github:numtide/system-manager -- switch --flake .#default --sudo` (from repo root)
3. `systemctl --user restart sleet1213-worker sleet1213-irc` (or whichever services need the changes)
4. If tool config changed, terminate the Temporal workflow to clear cached SDK sessions
