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

Restricted to MCP tools only — can use `mcp-js` to edit and create skills, schedules, memory, and interact with Minecraft:

- **Built-in tools:** None (only `mcp__*` pattern)
- **MCP tools:** `mcp-js` (JavaScript runtime for creating skills, managing schedules, memory CRUD, and Minecraft RPC interaction via btone bridge)
- **Minecraft RPC:** Inside mcp-js, `mcp.listTools('btone')` discovers all bot RPC methods; `mcp.callTool('btone', 'player_state', {})` calls them. The btone-mcp-bridge (`/home/ubuntu/mca-src/bin/btone-mcp-bridge.mjs`) auto-generates MCP tools from the OpenRPC spec at runtime.
- **Plugins:** Disabled
- **Session:** `irc-sleet1213-public`

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

You are running on the host machine that the services deploy to. Infrastructure is managed by [numtide/system-manager](https://github.com/numtide/system-manager) via Nix. The flake at the repo root defines all systemd units (system and user level), config files under `/etc/`, and policies.

### Activating changes

After editing config files, service units, or Nix modules, run:

```bash
nix run github:numtide/system-manager -- switch --flake .#default --sudo
```

(`nix` is at `/nix/var/nix/profiles/default/bin/nix` if not on `$PATH`.)

This rebuilds the Nix closure and activates it — updating `/etc/` files, reloading systemd, and restarting changed services. There are also Makefile shortcuts:

| Target | Description |
|--------|-------------|
| `make activate` | Run system-manager switch locally |
| `make build` | Build the closure without activating (sanity check) |
| `make diff` | Dry-run: show what `activate` would change |
| `make deploy` | SSH to the host, `git pull`, and activate remotely |

**Note:** system-manager activates config files and restarts services whose unit files changed, but it does **not** automatically restart services that merely read a config file at startup. If you change a config file like `nick-groups.json`, you still need to manually restart the service that consumes it:

```bash
systemctl --user restart sleet1213-irc
```

### Managed services

**User-level** (`systemctl --user`):

| Service | Description |
|---------|-------------|
| `sleet1213-webhook` | HTTP API + SSE streaming |
| `sleet1213-irc` | IRC bridge (Twitch chat in/out) — depends on webhook |
| `sleet1213-worker` | Temporal worker (Claude Agent SDK) |
| `sleet1213-temporal` | Temporal dev server |
| `sleet1213-mc-bridge` | MC chat bridge (btone SSE -> webhook) |
| `sleet1213-hud-poller` | Schedule HUD poller (Temporal -> CronHud JSON) |
| `opencode-web` | OpenCode web UI |

**System-level** (`systemctl`):

| Service | Description |
|---------|-------------|
| `btone-bot` | Minecraft headless client |
| `btone-stream` | OBS/streaming pipeline |
| `btone-audio` | PulseAudio game audio |
| `xorg-headless` | Headless X server for rendering |

### Managed config files

| Path | Source |
|------|--------|
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
```
