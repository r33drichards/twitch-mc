/**
 * In-process MCP server exposing memory CRUD, MCP server management,
 * and Temporal schedule management.
 * Passed to the Agent SDK via `mcpServers` so the agent can manage
 * memories, tool servers, and crons without custom built-in tools.
 */
import { createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod/v4';
import { writeFileSync, mkdirSync } from 'fs';
import { Connection, ScheduleClient, ScheduleOverlapPolicy } from '@temporalio/client';
import {
  setMemory,
  getMemory,
  deleteMemory,
  listMemories,
  searchMemories,
  listMcpServers,
  createMcpServer,
  deleteMcpServer,
  McpNameTakenError,
  type MemoryTier,
} from './db.js';
import type { scheduledPrompt } from './workflows.js';

const tierEnum = z.enum(['working', 'short_term', 'long_term']);

const PLUGIN_MCP_JSON = '/app/ted-plugin/.mcp.json';
const TEMPORAL_ADDRESS = process.env.TEMPORAL_ADDRESS ?? '127.0.0.1:7233';
const TEMPORAL_NAMESPACE = process.env.TEMPORAL_NAMESPACE ?? 'default';
const TASK_QUEUE = process.env.TASK_QUEUE ?? 'chat';

// Lazy singleton — created on first schedule tool call.
let _scheduleClient: ScheduleClient | null = null;
async function getScheduleClient(): Promise<ScheduleClient> {
  if (!_scheduleClient) {
    const connection = await Connection.connect({ address: TEMPORAL_ADDRESS });
    _scheduleClient = new ScheduleClient({ connection, namespace: TEMPORAL_NAMESPACE });
  }
  return _scheduleClient;
}

/**
 * Sync the DB mcp_servers to the plugin's .mcp.json so the SDK
 * discovers them on next session. Also returns the config for
 * programmatic use.
 */
async function syncMcpJson(userId: string): Promise<Record<string, any>> {
  const servers = await listMcpServers(userId);
  const config: Record<string, any> = {};
  for (const s of servers) {
    if (s.transport === 'stdio' && s.command) {
      config[s.name] = { command: s.command, args: s.args ?? [] };
    } else if (s.url) {
      config[s.name] = { type: 'http', url: s.url };
    }
  }
  try {
    writeFileSync(PLUGIN_MCP_JSON, JSON.stringify({ mcpServers: config }, null, 2));
  } catch {
    // Volume might not be writable, ignore
  }
  return config;
}

export interface TedMcpOptions {
  /** Include mcp_add/mcp_list/mcp_remove tools (admin only). */
  includeMcpManagement?: boolean;
}

export function createTedMcpServer(userId: string, opts: TedMcpOptions = {}) {
  const { includeMcpManagement = false } = opts;

  // ---- MCP server management tools (admin only) ----
  const mcpTools = includeMcpManagement ? [
    tool(
      'mcp_add',
      'Add an MCP tool server. For HTTP servers provide url. For stdio servers (local commands) provide command and args. The server becomes available on the next turn.',
      {
        name: z.string().describe('Short identifier (e.g. "github", "runno")'),
        url: z.string().optional().describe('HTTP(S) URL for HTTP transport'),
        command: z.string().optional().describe('Command for stdio transport (e.g. "npx")'),
        args: z.array(z.string()).optional().describe('Args for stdio transport (e.g. ["@runno/mcp"])'),
      },
      async (input) => {
        if (!input.url && !input.command) {
          return { content: [{ type: 'text', text: 'Provide either url (HTTP) or command (stdio).' }] };
        }
        const transport = input.command ? 'stdio' : 'http';
        try {
          await createMcpServer(userId, {
            name: input.name,
            url: input.url,
            transport: transport as any,
            command: input.command,
            args: input.args,
          });
        } catch (err) {
          if (err instanceof McpNameTakenError) {
            return { content: [{ type: 'text', text: `Server "${input.name}" already exists.` }] };
          }
          throw err;
        }
        const config = await syncMcpJson(userId);
        const label = input.command ? `${input.command} ${(input.args ?? []).join(' ')}` : input.url!;
        return {
          content: [{
            type: 'text',
            text: `Added MCP server "${input.name}" (${transport}: ${label}). ` +
                  `It will be available on the next turn. ` +
                  `Plugin .mcp.json updated with ${Object.keys(config).length} server(s).`,
          }],
        };
      },
    ),
    tool(
      'mcp_list',
      'List all configured MCP tool servers.',
      {},
      async () => {
        const servers = await listMcpServers(userId);
        if (servers.length === 0) return { content: [{ type: 'text', text: 'No MCP servers configured.' }] };
        const lines = servers.map((s) => {
          const label = s.transport === 'stdio' && s.command
            ? `${s.command} ${(s.args ?? []).join(' ')}`
            : s.url ?? 'unknown';
          return `${s.name} — ${s.transport}: ${label} (${s.enabled ? 'enabled' : 'disabled'})`;
        });
        return { content: [{ type: 'text', text: lines.join('\n') }] };
      },
    ),
    tool(
      'mcp_remove',
      'Remove an MCP tool server by name.',
      { name: z.string() },
      async (input) => {
        const servers = await listMcpServers(userId);
        const target = servers.find((s) => s.name === input.name);
        if (!target) return { content: [{ type: 'text', text: `No server named "${input.name}".` }] };
        await deleteMcpServer(target.id, userId);
        await syncMcpJson(userId);
        return { content: [{ type: 'text', text: `Removed "${input.name}".` }] };
      },
    ),
  ] : [];

  // ---- Schedule (cron) management tools ----
  const scheduleTools = [
    tool(
      'schedule_create',
      'Create a recurring or one-shot Temporal schedule.\n\n' +
      'type "prompt" (default): sends a text prompt to the agent session, triggering a full LLM turn.\n' +
      'type "run_js": executes JavaScript directly via the mcp-v8 sidecar — zero LLM cost, deterministic.\n\n' +
      'run_js schedules get a persistent V8 heap named after the schedule ID (or a custom heap name). ' +
      'Variables, counters, and state set with globalThis persist across every fire of that schedule. ' +
      'Example: `globalThis.runCount = (globalThis.runCount || 0) + 1;` increments each fire.\n\n' +
      'The code runs at top level — use console.log() for output, top-level await is supported. ' +
      'The btone sub-server is available via `await mcp.callTool("btone", "method", {params})`. ' +
      'The fs module is available for reading/writing files (sandboxed by policy).\n\n' +
      'Results are posted into the agent session so they appear in chat.',
      {
        id: z.string().describe('Unique schedule ID (e.g. "farm-loop", "check-health")'),
        session_id: z.string().describe('Session ID to post results to (e.g. "irc-sleet1213")'),
        cron: z.string().optional().describe('Cron expression for recurring schedules (e.g. "*/30 * * * *" for every 30 min)'),
        at: z.string().optional().describe('ISO timestamp for one-shot schedules (e.g. "2026-05-02T15:00:00Z")'),
        prompt: z.string().describe('For type=prompt: the text prompt. For type=run_js: the JavaScript code.'),
        type: z.enum(['prompt', 'run_js']).default('prompt').describe('prompt = send to agent (LLM turn), run_js = execute JS directly via mcp-v8'),
        heap: z.string().optional().describe('Custom heap name for run_js schedules (defaults to the schedule ID). Variables stored on globalThis persist across fires within the same heap.'),
      },
      async (args) => {
        if (!args.cron && !args.at) {
          return { content: [{ type: 'text', text: 'Provide either cron (recurring) or at (one-shot ISO timestamp).' }] };
        }
        // Default heap to the schedule ID for run_js schedules
        const heap = args.type === 'run_js' ? (args.heap ?? args.id) : undefined;
        try {
          const client = await getScheduleClient();

          const spec: any = {};
          if (args.cron) {
            spec.cronExpressions = [args.cron];
          } else if (args.at) {
            const d = new Date(args.at);
            const cronOnce = `${d.getUTCMinutes()} ${d.getUTCHours()} ${d.getUTCDate()} ${d.getUTCMonth() + 1} * ${d.getUTCFullYear()}`;
            spec.cronExpressions = [cronOnce];
          }

          const handle = await client.create({
            scheduleId: args.id,
            spec,
            action: {
              type: 'startWorkflow' as const,
              workflowType: 'scheduledPrompt',
              taskQueue: TASK_QUEUE,
              args: [args.session_id, userId, args.prompt, args.type, heap] as Parameters<typeof scheduledPrompt>,
            },
            policies: {
              overlap: ScheduleOverlapPolicy.SKIP,
            },
            state: args.at ? { remainingActions: 1 } : undefined,
          });

          const kind = args.cron ? `recurring (${args.cron})` : `one-shot (${args.at})`;
          const typeLabel = args.type === 'run_js' ? ` [run_js, heap="${heap}"]` : '';
          return { content: [{ type: 'text', text: `Created schedule "${handle.scheduleId}" — ${kind}${typeLabel}` }] };
        } catch (err: any) {
          return { content: [{ type: 'text', text: `Failed to create schedule: ${err.message}` }] };
        }
      },
    ),
    tool(
      'schedule_list',
      'List all Temporal schedules (crons).',
      {},
      async () => {
        try {
          const client = await getScheduleClient();
          const entries: string[] = [];
          for await (const schedule of client.list()) {
            const paused = schedule.state.paused ? ' [PAUSED]' : '';
            const nextTimes = schedule.info.nextActionTimes
              .slice(0, 2)
              .map((d) => d.toISOString())
              .join(', ');
            let line = `${schedule.scheduleId}${paused}`;
            if (nextTimes) line += ` — next: ${nextTimes}`;
            entries.push(line);
          }
          if (entries.length === 0) return { content: [{ type: 'text', text: 'No schedules found.' }] };
          return { content: [{ type: 'text', text: entries.join('\n') }] };
        } catch (err: any) {
          return { content: [{ type: 'text', text: `Failed to list schedules: ${err.message}` }] };
        }
      },
    ),
    tool(
      'schedule_delete',
      'Delete a Temporal schedule by ID.',
      { id: z.string().describe('Schedule ID to delete') },
      async (args) => {
        try {
          const client = await getScheduleClient();
          const handle = client.getHandle(args.id);
          await handle.delete();
          return { content: [{ type: 'text', text: `Deleted schedule "${args.id}".` }] };
        } catch (err: any) {
          return { content: [{ type: 'text', text: `Failed to delete schedule: ${err.message}` }] };
        }
      },
    ),
    tool(
      'schedule_trigger',
      'Manually fire a Temporal schedule immediately (one-off trigger, does not affect the regular cadence).',
      { id: z.string().describe('Schedule ID to trigger') },
      async (args) => {
        try {
          const client = await getScheduleClient();
          const handle = client.getHandle(args.id);
          await handle.trigger();
          return { content: [{ type: 'text', text: `Triggered schedule "${args.id}" (fires now).` }] };
        } catch (err: any) {
          return { content: [{ type: 'text', text: `Failed to trigger schedule: ${err.message}` }] };
        }
      },
    ),
  ];

  return createSdkMcpServer({
    name: 'ted',
    version: '1.0.0',
    tools: [
      // ---- Memory tools ----
      tool(
        'memory_set',
        'Create or update a memory. working = always in context, short_term = index in context, long_term = searchable.',
        { tier: tierEnum, key: z.string(), content: z.string() },
        async (args) => {
          await setMemory(userId, args.tier as MemoryTier, args.key, args.content);
          return { content: [{ type: 'text', text: `Memory "${args.key}" saved to ${args.tier}.` }] };
        },
      ),
      tool(
        'memory_get',
        'Read the full content of a memory by key.',
        { key: z.string() },
        async (args) => {
          const mem = await getMemory(userId, args.key);
          if (!mem) return { content: [{ type: 'text', text: `No memory found with key "${args.key}".` }] };
          return { content: [{ type: 'text', text: `[${mem.tier}] ${mem.key}:\n${mem.content}` }] };
        },
      ),
      tool(
        'memory_delete',
        'Delete a memory by key.',
        { key: z.string() },
        async (args) => {
          const ok = await deleteMemory(userId, args.key);
          return {
            content: [{ type: 'text', text: ok ? `Deleted "${args.key}".` : `No memory "${args.key}".` }],
          };
        },
      ),
      tool(
        'memory_list',
        'List all memories, optionally filtered by tier.',
        { tier: tierEnum.optional() },
        async (args) => {
          const mems = await listMemories(userId, args.tier as MemoryTier | undefined);
          if (mems.length === 0) return { content: [{ type: 'text', text: 'No memories found.' }] };
          const lines = mems.map((m) => {
            const preview = m.content.length > 80 ? m.content.slice(0, 80) + '...' : m.content;
            return `[${m.tier}] ${m.key}: ${preview}`;
          });
          return { content: [{ type: 'text', text: lines.join('\n') }] };
        },
      ),
      tool(
        'memory_search',
        'Search memories by keyword across keys and content.',
        { query: z.string(), tier: tierEnum.optional() },
        async (args) => {
          const results = await searchMemories(userId, args.query, args.tier as MemoryTier | undefined);
          if (results.length === 0) return { content: [{ type: 'text', text: `No memories matching "${args.query}".` }] };
          const lines = results.map((m) => {
            const preview = m.content.length > 80 ? m.content.slice(0, 80) + '...' : m.content;
            return `[${m.tier}] ${m.key}: ${preview}`;
          });
          return { content: [{ type: 'text', text: lines.join('\n') }] };
        },
      ),

      // ---- MCP server management (admin only) + schedules (always) ----
      ...mcpTools,
      ...scheduleTools,
    ],
  });
}
