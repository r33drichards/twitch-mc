import { readFileSync } from 'node:fs';
import type { AgentConfig } from './types.js';

/**
 * A nick group maps a set of IRC nicks to a specific agent configuration,
 * userId, and sessionId. This lets different users (admin vs public) get
 * different agent capabilities.
 */
export type NickGroup = {
  /** Human-readable group name (e.g. "admin", "public") */
  name: string;
  /**
   * Nicks belonging to this group (lowercased). Use ["*"] as a wildcard
   * catch-all for any nick not matched by an earlier group.
   */
  nicks: string[];
  /** The userId this group's messages are posted under */
  userId: string;
  /** The sessionId this group's messages land in */
  sessionId: string;
  /** Whether messages must mention the bot nick */
  requireMention: boolean;
  /** Agent configuration for this group */
  agentConfig: AgentConfig;
};

export type NickGroupsConfig = {
  groups: NickGroup[];
};

/**
 * Default admin config — matches the current hardcoded behavior in
 * activities.ts. Used when no nick-groups config is provided.
 */
export const DEFAULT_ADMIN_CONFIG: AgentConfig = {
  allowedTools: [
    'Read', 'Write', 'Edit',
    'Glob', 'Grep',
    'Bash',
    'WebSearch', 'WebFetch',
    'Skill', 'Agent',
    'TodoWrite',
    'NotebookEdit',
    'mcp__*',
  ],
  includePlugins: true,
  includeUserMcpServers: true,
};

/**
 * Least-privilege fallback used when an inbound request doesn't supply
 * an agentConfig (e.g. scheduled prompts, internal calls). Mirrors the
 * "public" group from /etc/sleet1213/nick-groups.json.
 */
export const DEFAULT_PUBLIC_CONFIG: AgentConfig = {
  allowedTools: ['Agent', 'Skill', 'mcp__*'],
  includePlugins: true,
  includeUserMcpServers: false,
  extraMcpServers: {
    'mcp-js': {
      command: '/usr/local/bin/mcp-v8',
      args: [
        '--stateless',
        '--policies-json',
        '/etc/sleet1213/policies/skills-fs.json',
        '--mcp-server',
        'btone=stdio:node:/home/ubuntu/mca-src/bin/btone-mcp-bridge.mjs',
      ],
    },
    btone: {
      command: 'node',
      args: ['/home/ubuntu/mca-src/bin/btone-mcp-stub.mjs'],
    },
    'minecraft-data': {
      command: 'node',
      args: ['/home/ubuntu/minecraft-data/minecraft-data-mcp.mjs'],
    },
  },
  systemPromptSuffix:
    'You are a helpful assistant for Twitch viewers. You have access to: 1) mcp-js — a JavaScript runtime with sandboxed filesystem access for reading and writing skill files and coords.md. btone (Minecraft bot control with 50+ RPC methods) is available as a sub-server inside mcp-js via mcp.callTool(\'btone\', \'method\', {params}). Use mcp.listTools(\'btone\') to discover available methods. 2) btone — tool discovery only. Shows all available bot RPC methods and their parameters, but calls are redirected to mcp-js. 3) minecraft-data — Minecraft game data lookup (recipes, items, blocks, food, entities, biomes, enchantments). Use mcp__minecraft-data__* tools to look up game data.\n\n## Bot Control via mcp-js\nAll Minecraft bot interaction goes through mcp__mcp-js__run_js:\n  const state = await mcp.callTool(\'btone\', \'player_state\', {});\n  const tools = await mcp.listTools(\'btone\');\n  await mcp.callTool(\'btone\', \'baritone_goto\', { x: 100, y: 64, z: 200 });\nDo NOT call mcp__btone__* tools directly — they will redirect you to mcp-js.\n\n## Skill Editing\nYou can create and edit skills using the mcp-js fs module. Filesystem access is sandboxed by Rego policy to skill directories only:\n  - LOCAL (read-write): /home/ubuntu/.local/share/sleet1213/plugin/skills/\n  - REPO (read-only): /home/ubuntu/twitch-mc/apps/sleet1213/ted-plugin/skills/\nUse mcp-js to run JavaScript that calls fs.readFile(), fs.writeFile(), fs.readdir(), fs.mkdir(), etc.\nSkills need a SKILL.md with YAML front matter (name + description) and a markdown body. Reloaded every turn.\n\n## Coordinates Reference\nRead and write the shared coordinates file via mcp-js:\n  const coords = await fs.readFile("/home/ubuntu/mca-src/coords.md", "utf-8");\n  await fs.writeFile("/home/ubuntu/mca-src/coords.md", updatedCoords);\nThis file contains all known Minecraft world coordinates (spawn, warehouses, chests, farms, hazards). Always read it before navigating or interacting with the world. Update it when you discover new locations.\n\nYou do NOT have Bash, Grep, Glob, or other system tools. You cannot add/remove MCP servers. You DO have the Agent tool — use it to spawn sub-agents for complex multi-step tasks.\n\n## Scheduled JS (mcp__ted__schedule_create_js)\nYou can schedule JavaScript code to run on a cron or one-shot basis using schedule_create_js. Unlike schedule_create (which sends a prompt to the agent), schedule_create_js runs code directly on the mcp-v8 runtime — no agent involved. Each schedule gets its own persistent V8 heap, so variables set with globalThis.x = ... survive across executions. The code has access to the same mcp.callTool(\'btone\', ...) bridge as run_js. Use this for lightweight recurring tasks like polling, data collection, or periodic bot actions that don\'t need the full agent.',
};

let _config: NickGroupsConfig | null = null;

/**
 * Load nick groups from the config file. Returns null if the file
 * doesn't exist (falls back to legacy IRC_ALLOWED_NICKS behavior).
 */
export function loadNickGroups(path?: string): NickGroupsConfig | null {
  const configPath = path ?? process.env.NICK_GROUPS_CONFIG ?? '/etc/sleet1213/nick-groups.json';
  try {
    const raw = readFileSync(configPath, 'utf-8');
    const parsed = JSON.parse(raw) as NickGroupsConfig;
    // Normalise nicks to lowercase
    for (const g of parsed.groups) {
      g.nicks = g.nicks.map((n) => n.toLowerCase());
    }
    _config = parsed;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Resolve an IRC nick to its group. Returns the first group whose nicks
 * list contains the nick (case-insensitive), or the first group with a
 * "*" wildcard. Returns null if no group matches.
 */
export function resolveNickGroup(nick: string, config?: NickGroupsConfig): NickGroup | null {
  const cfg = config ?? _config;
  if (!cfg) return null;
  const lc = nick.toLowerCase();
  // First pass: exact match
  for (const g of cfg.groups) {
    if (g.nicks.includes(lc)) return g;
  }
  // Second pass: wildcard
  for (const g of cfg.groups) {
    if (g.nicks.includes('*')) return g;
  }
  return null;
}

/**
 * Check if a nick is allowed by any group in the config.
 */
export function isNickAllowed(nick: string, config?: NickGroupsConfig): boolean {
  return resolveNickGroup(nick, config) !== null;
}
