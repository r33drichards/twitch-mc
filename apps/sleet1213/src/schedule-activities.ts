/**
 * schedule-activities.ts
 *
 * Lightweight activities used by the scheduledPrompt workflow.
 * Kept in a separate file so the main activities module doesn't grow.
 */

export interface FireScheduledPromptReq {
  sessionId: string;
  userId: string;
  prompt: string;
}

export interface FireScheduledRunJsReq {
  sessionId: string;
  userId: string;
  code: string;
  /** Named V8 heap for persistent state across executions. */
  heap?: string;
}

/**
 * Posts a message to the local webhook /message endpoint — the same path
 * the IRC bridge uses. This lets a Temporal Schedule inject a prompt into
 * an existing chatSession without duplicating any auth/session logic.
 *
 * Before firing, it echoes the prompt to the IRC channel so viewers can
 * see what triggered the bot (scheduled prompts don't originate from chat
 * so the IRC bridge wouldn't normally display them).
 */
export async function fireScheduledPrompt(req: FireScheduledPromptReq): Promise<void> {
  // Echo the scheduled prompt to IRC so it's visible in Twitch chat.
  // The IRC bridge runs a tiny HTTP echo server on IRC_ECHO_PORT (default 8790).
  const echoPort = process.env.IRC_ECHO_PORT ?? '8790';
  try {
    await fetch(`http://127.0.0.1:${echoPort}/echo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: `[scheduled] ${req.prompt}` }),
    });
  } catch { /* best-effort — IRC bridge may be down */ }

  const webhookUrl = process.env.WEBHOOK_URL ?? 'http://127.0.0.1:8787';

  // Prepend the scheduled prompt as a visible "user" message so the
  // session transcript (and Twitch chat via the IRC bridge's SSE
  // listener) shows the trigger. The message format mimics what a human
  // would type: "scheduler: <prompt>".
  const visibleMsg = `scheduler: ${req.prompt}`;

  const resp = await fetch(`${webhookUrl}/message`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': req.userId,
    },
    body: JSON.stringify({
      sessionId: req.sessionId,
      msg: visibleMsg,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`webhook /message returned ${resp.status}: ${text}`);
  }
}

// ---------------------------------------------------------------------------
// fireScheduledRunJs — execute JavaScript directly via the mcp-v8 HTTP API,
// then post the output into the agent session so the result is visible in
// chat and the agent can react to it.
//
// Requires a long-lived mcp-v8 sidecar on MCP_V8_URL (default
// http://127.0.0.1:25700). The sidecar exposes:
//   POST /api/exec          → { execution_id }
//   GET  /api/executions/ID → { status, result, error, heap }
//   GET  /api/executions/ID/output → { data }
//
// Heap persistence: mcp-v8 heaps are content-addressed. Each execution
// returns a new heap hash. We store the latest hash in a small JSON map
// at HEAP_MAP_PATH so the next fire of the same schedule resumes from
// where it left off.
// ---------------------------------------------------------------------------

import { readFileSync, writeFileSync, mkdirSync as mkdirSyncFs } from 'node:fs';
import { dirname } from 'node:path';

const MCP_V8_URL = process.env.MCP_V8_URL ?? 'http://127.0.0.1:25700';
const RUN_JS_POLL_MS = 500;
const RUN_JS_TIMEOUT_MS = Number(process.env.RUN_JS_TIMEOUT_MS ?? 120_000);
const HEAP_MAP_PATH = process.env.HEAP_MAP_PATH ?? '/home/ubuntu/.local/share/sleet1213/mcp-v8-heap-map.json';

/** Load the heap-name → content-hash map from disk. */
function loadHeapMap(): Record<string, string> {
  try {
    return JSON.parse(readFileSync(HEAP_MAP_PATH, 'utf-8'));
  } catch {
    return {};
  }
}

/** Persist the heap-name → content-hash map to disk (atomic via rename). */
function saveHeapMap(map: Record<string, string>): void {
  try {
    mkdirSyncFs(dirname(HEAP_MAP_PATH), { recursive: true });
    const tmp = `${HEAP_MAP_PATH}.tmp`;
    writeFileSync(tmp, JSON.stringify(map, null, 2));
    const { renameSync } = require('node:fs');
    renameSync(tmp, HEAP_MAP_PATH);
  } catch { /* best-effort */ }
}

export async function fireScheduledRunJs(req: FireScheduledRunJsReq): Promise<void> {
  const echoPort = process.env.IRC_ECHO_PORT ?? '8790';

  // Echo start to IRC
  try {
    await fetch(`http://127.0.0.1:${echoPort}/echo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: `[scheduled-js] executing…` }),
    });
  } catch { /* best-effort */ }

  // 1. Resolve the heap: look up the latest content-hash for this heap name
  const heapName = req.heap;
  let heapHash: string | undefined;
  if (heapName) {
    const map = loadHeapMap();
    heapHash = map[heapName];
  }

  // 2. Submit the JS code to mcp-v8
  const execBody: Record<string, any> = { code: req.code, session: heapName };
  if (heapHash) execBody.heap = heapHash;
  const execResp = await fetch(`${MCP_V8_URL}/api/exec`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(execBody),
  });
  if (!execResp.ok) {
    const text = await execResp.text().catch(() => '');
    throw new Error(`mcp-v8 /api/exec returned ${execResp.status}: ${text}`);
  }
  const { execution_id } = (await execResp.json()) as { execution_id: string };

  // 3. Poll until completion or timeout
  const deadline = Date.now() + RUN_JS_TIMEOUT_MS;
  let status = '';
  let result: string | null = null;
  let error: string | null = null;
  let outputHeapHash: string | null = null;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, RUN_JS_POLL_MS));
    const pollResp = await fetch(`${MCP_V8_URL}/api/executions/${execution_id}`);
    if (!pollResp.ok) {
      throw new Error(`mcp-v8 poll returned ${pollResp.status}`);
    }
    const info = (await pollResp.json()) as {
      status: string;
      result?: string | null;
      error?: string | null;
      heap?: string | null;
    };
    status = info.status;
    if (status === 'completed' || status === 'failed' || status === 'cancelled') {
      result = info.result ?? null;
      error = info.error ?? null;
      outputHeapHash = info.heap ?? null;
      break;
    }
  }

  if (status !== 'completed' && status !== 'failed' && status !== 'cancelled') {
    // Timed out — attempt to cancel
    try {
      await fetch(`${MCP_V8_URL}/api/executions/${execution_id}/cancel`, { method: 'POST' });
    } catch { /* best-effort */ }
    throw new Error(`mcp-v8 execution ${execution_id} timed out after ${RUN_JS_TIMEOUT_MS}ms`);
  }

  // 4. Persist the new heap hash so the next fire resumes from this state
  if (heapName && outputHeapHash) {
    const map = loadHeapMap();
    map[heapName] = outputHeapHash;
    saveHeapMap(map);
  }

  // 5. Fetch full output (console.log, return value, etc.)
  let output = '';
  try {
    const outResp = await fetch(`${MCP_V8_URL}/api/executions/${execution_id}/output`);
    if (outResp.ok) {
      const page = (await outResp.json()) as { data?: string };
      output = page.data ?? '';
    }
  } catch { /* best-effort */ }

  // Build a summary for the agent session
  const summary = error
    ? `[scheduled-js error] ${error}`
    : `[scheduled-js] ${(output || result || '(no output)').slice(0, 800)}`;

  // 6. Echo result to IRC
  try {
    await fetch(`http://127.0.0.1:${echoPort}/echo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: summary.slice(0, 400) }),
    });
  } catch { /* best-effort */ }

  // 7. Post the result into the agent session so the agent sees it
  const webhookUrl = process.env.WEBHOOK_URL ?? 'http://127.0.0.1:8787';
  const visibleMsg = `scheduler: ${summary}`;

  const resp = await fetch(`${webhookUrl}/message`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': req.userId,
    },
    body: JSON.stringify({
      sessionId: req.sessionId,
      msg: visibleMsg,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`webhook /message returned ${resp.status}: ${text}`);
  }
}
