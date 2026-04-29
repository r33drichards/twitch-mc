#!/usr/bin/env node
/**
 * btone-mcp-sse — long-lived HTTP+SSE MCP server wrapping the btone HTTP RPC.
 *
 * Why this exists: mcp-v8 in `--http-port` mode deadlocks when also given a
 * stdio sub-server (--mcp-server btone=stdio:...): the rmcp client opens
 * the child's stdio but never sends `initialize`, so the HTTP transport
 * never starts. Running btone as a separate SSE MCP server sidesteps that
 * — mcp-v8 connects via `--mcp-server btone=sse:http://127.0.0.1:.../sse`,
 * which the rmcp client handles correctly.
 *
 * Tool surface mirrors btone-mcp-bridge.mjs (stdio bridge) — same OpenRPC
 * discovery, same baritone.goto safety check.
 */

import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { SSEServerTransport } from '@modelcontextprotocol/sdk/server/sse.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

// ---------------------------------------------------------------------------
// btone RPC client
// ---------------------------------------------------------------------------

const BRIDGE_CONFIG_PATH =
  process.env.BTONE_BRIDGE_CONFIG ??
  join(homedir(), 'btone-mc-work', 'config', 'btone-bridge.json');

let rpcUrl = '';
let rpcToken = '';

function loadBridgeConfig() {
  const cfg = JSON.parse(readFileSync(BRIDGE_CONFIG_PATH, 'utf8'));
  rpcUrl = `http://127.0.0.1:${cfg.port}/rpc`;
  rpcToken = cfg.token ?? '';
}

async function rpcCall(method, params) {
  const body = { method };
  if (params !== undefined && params !== null) body.params = params;
  const headers = { 'Content-Type': 'application/json' };
  if (rpcToken) headers['Authorization'] = `Bearer ${rpcToken}`;
  const resp = await fetch(rpcUrl, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  const env = await resp.json();
  if (!env.ok) {
    const err = env.error ?? {};
    throw new Error(`${err.code ?? 'rpc_error'}: ${err.message ?? JSON.stringify(env)}`);
  }
  return env.result;
}

// ---------------------------------------------------------------------------
// Blocks safe to stand on / walk through (used by baritone.goto safety check)
// ---------------------------------------------------------------------------
const SAFE_BLOCK_IDS = new Set([
  'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air',
  'minecraft:grass', 'minecraft:tall_grass', 'minecraft:fern', 'minecraft:large_fern',
  'minecraft:dead_bush', 'minecraft:dandelion', 'minecraft:poppy',
  'minecraft:blue_orchid', 'minecraft:allium', 'minecraft:azure_bluet',
  'minecraft:red_tulip', 'minecraft:orange_tulip', 'minecraft:white_tulip',
  'minecraft:pink_tulip', 'minecraft:oxeye_daisy', 'minecraft:cornflower',
  'minecraft:lily_of_the_valley', 'minecraft:sunflower', 'minecraft:lilac',
  'minecraft:rose_bush', 'minecraft:peony',
  'minecraft:snow', 'minecraft:torch', 'minecraft:wall_torch',
  'minecraft:soul_torch', 'minecraft:soul_wall_torch',
  'minecraft:redstone_torch', 'minecraft:redstone_wall_torch',
  'minecraft:water', 'minecraft:lava',
]);

// ---------------------------------------------------------------------------
// OpenRPC → MCP tool conversion
// ---------------------------------------------------------------------------
let openRpcSpec = null;
let componentSchemas = {};

function resolveSchema(schema) {
  if (!schema) return { type: 'object' };
  if (schema.$ref) {
    const refName = schema.$ref.replace('#/components/schemas/', '');
    return componentSchemas[refName] ?? { type: 'object' };
  }
  if (schema.properties) {
    const resolved = { ...schema, properties: { ...schema.properties } };
    for (const [k, v] of Object.entries(resolved.properties)) {
      resolved.properties[k] = resolveSchema(v);
    }
    return resolved;
  }
  if (schema.items) {
    return { ...schema, items: resolveSchema(schema.items) };
  }
  return schema;
}

function methodToTool(m) {
  const properties = {};
  const required = [];
  for (const p of m.params ?? []) {
    properties[p.name] = resolveSchema(p.schema);
    if (p.required) required.push(p.name);
  }
  if (m.name === 'baritone.goto') {
    properties.force = {
      type: 'boolean',
      description:
        'Skip the safety check that prevents navigating into non-air blocks ' +
        '(chests, crafting tables, etc.). Default false — the call is refused ' +
        'if the destination block is solid. Set true only when you intentionally ' +
        'want to pathfind into a solid block.',
    };
  }
  let description = m.summary ?? m.name;
  if (m.name === 'baritone.goto') {
    description +=
      ' SAFETY: When all 3 coords are given, the destination block is checked ' +
      "— if it's solid (chest, crafting table, etc.) the call is REFUSED to " +
      'prevent the bot from breaking it. Pass force:true to override. ' +
      'Tip: offset by 1 block (e.g. x+1) to stand next to a container instead of on it.';
  }
  return {
    name: m.name.replace(/\./g, '_'),
    description,
    inputSchema: {
      type: 'object',
      properties,
      ...(required.length > 0 ? { required } : {}),
    },
    _rpcMethod: m.name,
  };
}

let toolDefs = [];
let methodMap = new Map();

async function discoverTools() {
  openRpcSpec = await rpcCall('rpc.discover');
  componentSchemas = {};
  if (openRpcSpec.components?.schemas) {
    for (const [name, schema] of Object.entries(openRpcSpec.components.schemas)) {
      componentSchemas[name] = schema;
    }
  }
  const tools = [];
  const map = new Map();
  for (const m of openRpcSpec.methods ?? []) {
    const tool = methodToTool(m);
    tools.push(tool);
    map.set(tool.name, tool._rpcMethod);
  }
  toolDefs = tools;
  methodMap = map;
}

async function ensureToolsLoaded() {
  if (toolDefs.length > 0) return;
  try {
    loadBridgeConfig();
    await discoverTools();
  } catch (err) {
    process.stderr.write(`[btone-mcp-sse] tool discovery failed: ${err.message}\n`);
  }
}

// ---------------------------------------------------------------------------
// MCP Server factory — one Server per SSE session.
// ---------------------------------------------------------------------------
function createBtoneServer() {
  const server = new Server(
    { name: 'btone-mcp-bridge', version: '0.2.0' },
    { capabilities: { tools: { listChanged: true } } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    process.stderr.write('[btone-mcp-sse] handler: tools/list\n');
    await ensureToolsLoaded();
    return {
      tools: toolDefs.map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema,
      })),
    };
  });

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    process.stderr.write(`[btone-mcp-sse] handler: tools/call ${req.params?.name}\n`);
    await ensureToolsLoaded();
    const toolName = req.params?.name;
    const toolArgs = req.params?.arguments ?? {};
    const rpcMethod = methodMap.get(toolName);
    if (!rpcMethod) {
      return {
        content: [{ type: 'text', text: `Unknown tool: ${toolName}` }],
        isError: true,
      };
    }
    if (
      rpcMethod === 'baritone.goto' &&
      toolArgs.x != null && toolArgs.y != null && toolArgs.z != null &&
      !toolArgs.force
    ) {
      try {
        const block = await rpcCall('world.block_at', {
          x: Math.floor(toolArgs.x),
          y: Math.floor(toolArgs.y),
          z: Math.floor(toolArgs.z),
        });
        const blockId = block?.id ?? 'minecraft:air';
        const isAir = block?.air === true;
        if (!isAir && !SAFE_BLOCK_IDS.has(blockId)) {
          const sx = toolArgs.x, sy = toolArgs.y, sz = toolArgs.z;
          return {
            content: [{
              type: 'text',
              text:
                `REFUSED — destination (${sx}, ${sy}, ${sz}) contains "${blockId}" which is a solid block. ` +
                `Navigating there would break it.\n\n` +
                `Safe alternatives (offset by 1 block):\n` +
                `  (${sx + 1}, ${sy}, ${sz})\n` +
                `  (${sx - 1}, ${sy}, ${sz})\n` +
                `  (${sx}, ${sy}, ${sz + 1})\n` +
                `  (${sx}, ${sy}, ${sz - 1})\n\n` +
                `To override this safety check, pass force: true.`,
            }],
            isError: true,
          };
        }
      } catch (err) {
        process.stderr.write(`[btone-mcp-sse] safety check failed: ${err.message}, proceeding\n`);
      }
    }
    try {
      const fwdArgs = { ...toolArgs };
      delete fwdArgs.force;
      const hasArgs = Object.keys(fwdArgs).length > 0;
      const result = await rpcCall(rpcMethod, hasArgs ? fwdArgs : undefined);
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    } catch (err) {
      return {
        content: [{ type: 'text', text: `RPC error: ${err.message}` }],
        isError: true,
      };
    }
  });

  return server;
}

// ---------------------------------------------------------------------------
// HTTP server with SSE endpoint
// ---------------------------------------------------------------------------
const PORT = Number(process.env.BTONE_MCP_SSE_PORT ?? 4002);
const HOST = process.env.BTONE_MCP_SSE_HOST ?? '127.0.0.1';
const transports = new Map();

const httpServer = createServer(async (req, res) => {
  let url;
  try { url = new URL(req.url, 'http://localhost'); }
  catch { res.writeHead(400).end('bad url'); return; }

  process.stderr.write(`[btone-mcp-sse] ${req.method} ${url.pathname}${url.search}\n`);

  if (req.method === 'GET' && url.pathname === '/sse') {
    const transport = new SSEServerTransport('/messages', res);
    transports.set(transport.sessionId, transport);
    res.on('close', () => { transports.delete(transport.sessionId); });
    const server = createBtoneServer();
    try {
      await server.connect(transport);
    } catch (err) {
      process.stderr.write(`[btone-mcp-sse] connect failed: ${err.message}\n`);
      transports.delete(transport.sessionId);
    }
    return;
  }
  if (req.method === 'POST' && url.pathname === '/messages') {
    const sessionId = url.searchParams.get('sessionId');
    const transport = transports.get(sessionId);
    if (!transport) {
      res.writeHead(404, { 'content-type': 'text/plain' }).end('no session');
      return;
    }
    try {
      await transport.handlePostMessage(req, res);
    } catch (err) {
      process.stderr.write(`[btone-mcp-sse] post handle failed: ${err.message}\n`);
      if (!res.headersSent) res.writeHead(500).end('error');
    }
    return;
  }
  if (req.method === 'GET' && url.pathname === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' })
      .end(JSON.stringify({ ok: true, sessions: transports.size, tools: toolDefs.length }));
    return;
  }
  res.writeHead(404, { 'content-type': 'text/plain' }).end('not found');
});

httpServer.listen(PORT, HOST, () => {
  process.stderr.write(
    `[btone-mcp-sse] listening on http://${HOST}:${PORT}/sse (POST messages → /messages?sessionId=...)\n`,
  );
});

function shutdown() {
  httpServer.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 5000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
