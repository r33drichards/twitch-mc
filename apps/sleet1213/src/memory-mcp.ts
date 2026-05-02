/**
 * In-process MCP server exposing memory CRUD and MCP server management.
 * Passed to the Agent SDK via `mcpServers` so the agent can manage
 * memories and its own tool servers without custom built-in tools.
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
  deleteScheduleHeap,
  type MemoryTier,
} from './db.js';

const TEMPORAL_ADDRESS = process.env.TEMPORAL_ADDRESS ?? '127.0.0.1:7233';
const TEMPORAL_NAMESPACE = process.env.TEMPORAL_NAMESPACE ?? 'default';
const TASK_QUEUE = process.env.TASK_QUEUE ?? 'chat';
/** All scheduled prompts fire into this session — hardcoded to prevent
 *  agents from accidentally targeting a session the IRC bridge doesn't
 *  subscribe to. */
const SCHEDULE_SESSION = 'irc-sleet1213';

/**
 * Lazily-created Temporal ScheduleClient singleton. The connection is
 * established on first use and reused across subsequent calls.
 */
let _scheduleClient: ScheduleClient | null = null;
async function getScheduleClient(): Promise<ScheduleClient> {
  if (!_scheduleClient) {
    const connection = await Connection.connect({ address: TEMPORAL_ADDRESS });
    _scheduleClient = new ScheduleClient({ connection, namespace: TEMPORAL_NAMESPACE });
  }
  return _scheduleClient;
}

const tierEnum = z.enum(['working', 'short_term', 'long_term']);

const PLUGIN_MCP_JSON = '/app/ted-plugin/.mcp.json';

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

export interface TedMcpServerOptions {
  /** Include mcp_add/mcp_list/mcp_remove tools. Default: true (admin). */
  includeMcpManagement?: boolean;
}

export function createTedMcpServer(userId: string, opts?: TedMcpServerOptions) {
  const includeMcpMgmt = opts?.includeMcpManagement ?? true;

  // ---- Memory tools (always included) ----
  const allTools: any[] = [
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
  ];

  // ---- MCP server management tools (admin only) ----
  if (includeMcpMgmt) {
    allTools.push(
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
    );
  }

  // ---- Schedule management tools (always included) ----
  // These let users (including the public agent) create, list, delete,
  // and trigger Temporal schedules. Scheduled prompts always execute
  // with the public (least-privilege) agent config — see
  // schedule-activities.ts.
  allTools.push(
    tool(
      'schedule_create',
      'Create a recurring or one-shot scheduled prompt. Use --cron for recurring (standard cron expression) or --at for a one-time fire (ISO 8601 timestamp). The scheduled prompt always runs with public (restricted) permissions.',
      {
        id: z.string().describe('Unique schedule identifier (e.g. "my-farm-loop")'),
        cron: z.string().optional().describe('Cron expression for recurring schedule (e.g. "*/90 * * * *")'),
        at: z.string().optional().describe('ISO 8601 timestamp for one-shot schedule (e.g. "2026-04-28T15:00:00Z")'),
        prompt: z.string().describe('The prompt text that will be sent when the schedule fires'),
      },
      async (input) => {
        if (!input.cron && !input.at) {
          return { content: [{ type: 'text', text: 'Must provide either cron (recurring) or at (one-shot).' }] };
        }
        try {
          const client = await getScheduleClient();
          const spec: any = {};
          if (input.cron) {
            spec.cronExpressions = [input.cron];
          } else if (input.at) {
            const d = new Date(input.at);
            if (isNaN(d.getTime())) {
              return { content: [{ type: 'text', text: `Invalid timestamp: "${input.at}"` }] };
            }
            const cronOnce = `${d.getUTCMinutes()} ${d.getUTCHours()} ${d.getUTCDate()} ${d.getUTCMonth() + 1} * ${d.getUTCFullYear()}`;
            spec.cronExpressions = [cronOnce];
          }
          const handle = await client.create({
            scheduleId: input.id,
            spec,
            action: {
              type: 'startWorkflow' as const,
              workflowType: 'scheduledPrompt',
              taskQueue: TASK_QUEUE,
              args: [SCHEDULE_SESSION, userId, input.prompt],
            },
            policies: {
              overlap: ScheduleOverlapPolicy.SKIP,
            },
            state: input.at ? { remainingActions: 1 } : undefined,
          });
          const kind = input.cron ? `recurring (${input.cron})` : `one-shot (${input.at})`;
          return {
            content: [{
              type: 'text',
              text: `Created schedule "${handle.scheduleId}" — ${kind}\n` +
                    `  session: ${SCHEDULE_SESSION}, user: ${userId}\n` +
                    `  prompt: ${input.prompt}`,
            }],
          };
        } catch (err) {
          return { content: [{ type: 'text', text: `Failed to create schedule: ${(err as Error).message}` }] };
        }
      },
    ),
    tool(
      'schedule_list',
      'List all Temporal schedules with their status and next fire times.',
      {},
      async () => {
        try {
          const client = await getScheduleClient();
          const lines: string[] = [];
          for await (const schedule of client.list()) {
            const paused = schedule.state.paused ? ' [PAUSED]' : '';
            const nextTimes = schedule.info.nextActionTimes
              .slice(0, 3)
              .map((d: Date) => d.toISOString())
              .join(', ');
            let line = `${schedule.scheduleId}${paused}`;
            if (nextTimes) line += `\n  next: ${nextTimes}`;
            const recentActions = schedule.info.recentActions ?? [];
            if (recentActions.length > 0) {
              const last = recentActions[recentActions.length - 1];
              line += `\n  last: ${(last as any).scheduledAt?.toISOString() ?? 'unknown'}`;
            }
            lines.push(line);
          }
          if (lines.length === 0) {
            return { content: [{ type: 'text', text: 'No schedules found.' }] };
          }
          return { content: [{ type: 'text', text: lines.join('\n\n') }] };
        } catch (err) {
          return { content: [{ type: 'text', text: `Failed to list schedules: ${(err as Error).message}` }] };
        }
      },
    ),
    tool(
      'schedule_delete',
      'Delete a Temporal schedule by ID. Also cleans up any associated JS heap state.',
      { id: z.string().describe('The schedule ID to delete') },
      async (input) => {
        try {
          const client = await getScheduleClient();
          const handle = client.getHandle(input.id);
          await handle.delete();
          // Clean up heap tracking for JS schedules (no-op for prompt schedules)
          await deleteScheduleHeap(input.id).catch(() => {});
          return { content: [{ type: 'text', text: `Deleted schedule "${input.id}".` }] };
        } catch (err) {
          return { content: [{ type: 'text', text: `Failed to delete schedule: ${(err as Error).message}` }] };
        }
      },
    ),
    tool(
      'schedule_trigger',
      'Manually trigger a Temporal schedule to fire immediately.',
      { id: z.string().describe('The schedule ID to trigger') },
      async (input) => {
        try {
          const client = await getScheduleClient();
          const handle = client.getHandle(input.id);
          await handle.trigger();
          return { content: [{ type: 'text', text: `Triggered schedule "${input.id}" — fires now.` }] };
        } catch (err) {
          return { content: [{ type: 'text', text: `Failed to trigger schedule: ${(err as Error).message}` }] };
        }
      },
    ),
    // ---- Scheduled JS tool ----
    // Like schedule_create but runs JavaScript code directly on the mcp-v8
    // HTTP sidecar instead of sending a prompt to the agent. Each schedule
    // gets its own persistent V8 heap so variables survive across executions.
    tool(
      'schedule_create_js',
      'Create a recurring or one-shot scheduled JavaScript execution. The code runs directly on the mcp-v8 runtime (same as run_js) — NOT through the agent. Each schedule gets its own persistent V8 heap, so variables set with globalThis.x = ... survive across executions. Use this for lightweight recurring tasks that don\'t need the full agent (e.g. polling, data collection, periodic bot actions via mcp.callTool).',
      {
        id: z.string().describe('Unique schedule identifier (e.g. "health-check")'),
        title: z.string().optional().describe('Human-readable title for HUD display (e.g. "Health Monitor"). Defaults to the schedule id.'),
        cron: z.string().optional().describe('Cron expression for recurring schedule (e.g. "*/5 * * * *")'),
        at: z.string().optional().describe('ISO 8601 timestamp for one-shot schedule (e.g. "2026-04-28T15:00:00Z")'),
        code: z.string().describe('JavaScript code to execute. Has access to globalThis for persistent state, console.log for output, fetch() for HTTP, and mcp.callTool(\'btone\', method, params) for Minecraft bot control.'),
      },
      async (input) => {
        if (!input.cron && !input.at) {
          return { content: [{ type: 'text', text: 'Must provide either cron (recurring) or at (one-shot).' }] };
        }
        try {
          const client = await getScheduleClient();
          const spec: any = {};
          if (input.cron) {
            spec.cronExpressions = [input.cron];
          } else if (input.at) {
            const d = new Date(input.at);
            if (isNaN(d.getTime())) {
              return { content: [{ type: 'text', text: `Invalid timestamp: "${input.at}"` }] };
            }
            const cronOnce = `${d.getUTCMinutes()} ${d.getUTCHours()} ${d.getUTCDate()} ${d.getUTCMonth() + 1} * ${d.getUTCFullYear()}`;
            spec.cronExpressions = [cronOnce];
          }
          const handle = await client.create({
            scheduleId: input.id,
            spec,
            action: {
              type: 'startWorkflow' as const,
              workflowType: 'scheduledJs',
              taskQueue: TASK_QUEUE,
              args: [input.id, input.code, input.title ?? input.id],
            },
            policies: {
              overlap: ScheduleOverlapPolicy.SKIP,
            },
            state: input.at ? { remainingActions: 1 } : undefined,
          });
          const kind = input.cron ? `recurring (${input.cron})` : `one-shot (${input.at})`;
          const displayTitle = input.title ?? input.id;
          const codePreview = input.code.length > 80 ? input.code.slice(0, 80) + '...' : input.code;
          return {
            content: [{
              type: 'text',
              text: `Created JS schedule "${handle.scheduleId}" — ${kind}\n` +
                    `  title: ${displayTitle}\n` +
                    `  heap: per-schedule persistent (schedule:${input.id})\n` +
                    `  code: ${codePreview}`,
            }],
          };
        } catch (err) {
          return { content: [{ type: 'text', text: `Failed to create JS schedule: ${(err as Error).message}` }] };
        }
      },
    ),
  );

  return createSdkMcpServer({
    name: 'ted',
    version: '1.0.0',
    tools: allTools,
  });
}
