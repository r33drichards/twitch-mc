/**
 * schedule-activities.ts
 *
 * Lightweight activities used by the scheduledPrompt and scheduledJs workflows.
 * Kept in a separate file so the main activities module doesn't grow.
 */

import { DEFAULT_PUBLIC_CONFIG } from './nick-groups.js';
import { getScheduleHeap, setScheduleHeap } from './db.js';

export interface FireScheduledPromptReq {
  sessionId: string;
  userId: string;
  prompt: string;
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

  // Always use the public (least-privilege) agent config for scheduled
  // prompts. This prevents privilege escalation — a public user who creates
  // a schedule must not gain admin tools when the cron fires.
  const resp = await fetch(`${webhookUrl}/message`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-ID': req.userId,
    },
    body: JSON.stringify({
      sessionId: req.sessionId,
      msg: visibleMsg,
      agentConfig: DEFAULT_PUBLIC_CONFIG,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`webhook /message returned ${resp.status}: ${text}`);
  }
}

/* ------------------------------------------------------------------ */
/*  Scheduled JS execution via mcp-v8 HTTP sidecar                    */
/* ------------------------------------------------------------------ */

const MCP_V8_URL = process.env.MCP_V8_URL ?? 'http://127.0.0.1:25700';
const JS_POLL_INTERVAL_MS = 1000;
const JS_POLL_MAX_ATTEMPTS = 150; // 150s max (execution_timeout is 120s)

export interface FireScheduledJsReq {
  scheduleId: string;
  code: string;
}

interface ExecSubmitResp {
  execution_id: string;
}

interface ExecStatusResp {
  execution_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'timed_out';
  result?: string;
  heap?: string;
  error?: string;
}

interface ExecOutputResp {
  data: string;
  total_lines: number;
}

/**
 * Execute JavaScript on the mcp-v8 HTTP sidecar with per-schedule heap
 * persistence. Each schedule gets its own heap chain — variables set in
 * one execution are available in the next.
 *
 * Flow:
 * 1. Look up the schedule's last heap hash from Postgres
 * 2. POST /api/exec with the code + heap (if any)
 * 3. Poll /api/executions/{id} until terminal
 * 4. If completed with a new heap hash, upsert it in Postgres
 * 5. Echo output to IRC for visibility
 */
export async function fireScheduledJs(req: FireScheduledJsReq): Promise<void> {
  const echoPort = process.env.IRC_ECHO_PORT ?? '8790';

  // Echo to IRC
  try {
    await fetch(`http://127.0.0.1:${echoPort}/echo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: `[scheduled-js:${req.scheduleId}] running...` }),
    });
  } catch { /* best-effort */ }

  // 1. Load previous heap for this schedule
  const previousHeap = await getScheduleHeap(req.scheduleId);

  // 2. Submit execution to mcp-v8
  const execBody: Record<string, unknown> = {
    code: req.code,
    session: `schedule:${req.scheduleId}`,
    execution_timeout_secs: 120,
  };
  if (previousHeap) {
    execBody.heap = previousHeap;
  }

  const submitResp = await fetch(`${MCP_V8_URL}/api/exec`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(execBody),
  });
  if (!submitResp.ok) {
    const text = await submitResp.text().catch(() => '');
    throw new Error(`mcp-v8 /api/exec returned ${submitResp.status}: ${text}`);
  }
  const { execution_id } = (await submitResp.json()) as ExecSubmitResp;

  // 3. Poll until terminal
  let status: ExecStatusResp | null = null;
  for (let i = 0; i < JS_POLL_MAX_ATTEMPTS; i++) {
    await new Promise((r) => setTimeout(r, JS_POLL_INTERVAL_MS));
    const pollResp = await fetch(`${MCP_V8_URL}/api/executions/${execution_id}`);
    if (!pollResp.ok) continue;
    status = (await pollResp.json()) as ExecStatusResp;
    if (status.status !== 'running') break;
  }

  if (!status || status.status === 'running') {
    // Try to cancel the runaway execution
    try {
      await fetch(`${MCP_V8_URL}/api/executions/${execution_id}/cancel`, { method: 'POST' });
    } catch { /* best-effort */ }
    throw new Error(`mcp-v8 execution ${execution_id} timed out waiting for completion`);
  }

  // 4. Persist new heap if execution succeeded
  if (status.status === 'completed' && status.heap) {
    await setScheduleHeap(req.scheduleId, status.heap);
  }

  // 5. Get console output and echo to IRC
  let output = '';
  try {
    const outResp = await fetch(
      `${MCP_V8_URL}/api/executions/${execution_id}/output?line_limit=50`,
    );
    if (outResp.ok) {
      const outData = (await outResp.json()) as ExecOutputResp;
      output = outData.data;
    }
  } catch { /* best-effort */ }

  // Build a summary message for IRC
  const truncatedOutput = output.length > 400 ? output.slice(0, 400) + '...' : output;
  const statusEmoji = status.status === 'completed' ? 'done' : status.status;
  let echoMsg = `[scheduled-js:${req.scheduleId}] ${statusEmoji}`;
  if (truncatedOutput.trim()) {
    echoMsg += ` | ${truncatedOutput.trim()}`;
  }
  if (status.error) {
    echoMsg += ` | error: ${status.error}`;
  }

  try {
    await fetch(`http://127.0.0.1:${echoPort}/echo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: echoMsg }),
    });
  } catch { /* best-effort */ }

  // Throw on failure so Temporal marks the activity as failed
  if (status.status !== 'completed') {
    throw new Error(
      `Scheduled JS execution ${execution_id} ${status.status}: ${status.error ?? 'unknown error'}`,
    );
  }
}
