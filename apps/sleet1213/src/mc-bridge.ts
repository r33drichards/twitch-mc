/**
 * Minecraft chat → sleet1213 webhook bridge.
 *
 * Subscribes to btone-mod-c's SSE event stream (`/events`) and forwards
 * `chat` events into the chatSession workflow as user messages, so the
 * agent perceives in-game chat — including Meteor Notifications module
 * messages emitted when modules like "run away from danger" trigger.
 *
 * Design notes:
 *   * The mod's SSE endpoint emits `event: chat` with payload
 *     `{type: "chat", text: <rendered text>, overlay: bool}`.
 *     overlay=true is action-bar HUD spam (skipped); overlay=false is
 *     real chat (player messages, system messages, Meteor notifications).
 *   * Sender metadata is NOT structured — chat events carry only the
 *     fully-rendered text (e.g. "<player> hello" or "[KillAura] X").
 *     We rely on MC's rendering to reveal source rather than parsing.
 *   * No Last-Event-ID support upstream, so events emitted during a
 *     reconnect window are lost. Acceptable for a first pass.
 *   * agentConfig is looked up from nick-groups.json under the "mc"
 *     group; if that group doesn't exist we fall back to the first
 *     group containing the bridge's own configured nick.
 */
import { readFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { loadNickGroups, resolveNickGroup, type NickGroup } from './nick-groups.js';

type Config = {
  bridgeUrl: string;
  bridgeToken: string;
  webhookUrl: string;
  group: NickGroup;
  msgPrefix: string;
  skipOverlay: boolean;
};

function loadBridgeAuth(): { url: string; token: string } {
  const cfgPath =
    process.env.BTONE_BRIDGE_CONFIG ?? '/var/lib/btone/config/btone-bridge.json';
  const raw = readFileSync(cfgPath, 'utf-8');
  const parsed = JSON.parse(raw) as { port: number; token: string };
  if (!parsed.port || !parsed.token) {
    throw new Error(`btone bridge config at ${cfgPath} missing port/token`);
  }
  return {
    url: `http://127.0.0.1:${parsed.port}`,
    token: parsed.token,
  };
}

function loadConfig(): Config {
  const auth = loadBridgeAuth();
  const groups = loadNickGroups();
  if (!groups) {
    throw new Error(
      'NICK_GROUPS_CONFIG not loadable — mc-bridge needs a nick group ' +
        'to derive userId/sessionId/agentConfig from',
    );
  }
  const groupName = process.env.MC_NICK_GROUP ?? 'mc';
  // Resolve the dedicated "mc" group, or fall back to whichever group
  // claims the BOT_USERNAME nick (lets the operator reuse the admin group
  // without configuring a separate one).
  const fallbackNick = (process.env.BOT_USERNAME ?? 'sleet1213').toLowerCase();
  const group =
    groups.groups.find((g) => g.name === groupName) ??
    resolveNickGroup(fallbackNick, groups);
  if (!group) {
    throw new Error(
      `mc-bridge: no nick group named "${groupName}" and no group ` +
        `claims fallback nick "${fallbackNick}"`,
    );
  }
  return {
    bridgeUrl: auth.url,
    bridgeToken: auth.token,
    webhookUrl: process.env.WEBHOOK_URL ?? 'http://localhost:8787',
    group,
    msgPrefix: process.env.MC_MSG_PREFIX ?? '[mc] ',
    skipOverlay: (process.env.MC_SKIP_OVERLAY ?? 'true').toLowerCase() !== 'false',
  };
}

/** Minimal SSE parser over a fetch Response body. Yields {event, data}. */
async function* readSse(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
): AsyncGenerator<{ event: string; data: string }> {
  const res = await fetch(url, { headers, signal });
  if (!res.ok || !res.body) throw new Error(`sse ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  let evtType = '';
  let dataLines: string[] = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const raw = buf.slice(0, nl).replace(/\r$/, '');
      buf = buf.slice(nl + 1);
      if (raw === '') {
        if (dataLines.length) {
          yield { event: evtType || 'message', data: dataLines.join('\n') };
          dataLines = [];
          evtType = '';
        }
        continue;
      }
      if (raw.startsWith(':')) continue;
      if (raw.startsWith('event:')) evtType = raw.slice(6).replace(/^ /, '');
      else if (raw.startsWith('data:')) dataLines.push(raw.slice(5).replace(/^ /, ''));
    }
  }
}

async function postToWebhook(
  cfg: Config,
  text: string,
): Promise<void> {
  const msg = cfg.msgPrefix + text;
  const res = await fetch(`${cfg.webhookUrl}/message`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'X-User-ID': cfg.group.userId,
    },
    body: JSON.stringify({
      sessionId: cfg.group.sessionId,
      msg,
      agentConfig: cfg.group.agentConfig,
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`webhook ${res.status}: ${body}`);
  }
}

async function streamChat(cfg: Config, signal: AbortSignal): Promise<void> {
  const url = `${cfg.bridgeUrl}/events`;
  const headers = { Authorization: `Bearer ${cfg.bridgeToken}` };
  for await (const ev of readSse(url, headers, signal)) {
    if (ev.event !== 'chat') continue;
    let payload: { text?: string; overlay?: boolean };
    try {
      payload = JSON.parse(ev.data);
    } catch {
      continue;
    }
    const text = (payload.text ?? '').trim();
    if (!text) continue;
    if (cfg.skipOverlay && payload.overlay === true) continue;
    try {
      await postToWebhook(cfg, text);
    } catch (err) {
      console.error('[mc-bridge] webhook post failed:', (err as Error).message);
      // swallow — keep streaming the next event
    }
  }
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  console.log(
    `[mc-bridge] subscribing to ${cfg.bridgeUrl}/events → ` +
      `${cfg.webhookUrl}/message (session=${cfg.group.sessionId} ` +
      `userId=${cfg.group.userId} group=${cfg.group.name})`,
  );

  const abort = new AbortController();
  process.on('SIGINT', () => abort.abort());
  process.on('SIGTERM', () => abort.abort());

  while (!abort.signal.aborted) {
    try {
      await streamChat(cfg, abort.signal);
    } catch (err) {
      if (abort.signal.aborted) return;
      console.error('[mc-bridge] stream error:', (err as Error).message);
      await sleep(2000);
    }
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
