/**
 * Minecraft subtitle → sleet1213 webhook bridge (batched).
 *
 * Subscribes to btone-mod-c's SSE event stream (`/events`) and collects
 * `subtitle` events (sound events the mod captures via SoundInstanceListener).
 * Every SUBTITLE_BATCH_INTERVAL_MS (default 30 000 ms) it flushes a single
 * summary message to the chatSession webhook so the agent perceives ambient
 * game sounds — hostile mob noises, block breaks, weather, etc.
 *
 * Shares the same session as mc-bridge (irc-sleet1213 by default) so the
 * agent sees chat and sound context together.
 *
 * Design notes:
 *   * The mod filters at the Java level (category allowlist + 1s dedup per
 *     soundId). This bridge applies a second pass: a blocklist for known
 *     spam sounds and per-batch dedup-by-count.
 *   * If no interesting sounds occur during a batch window the bridge stays
 *     silent — no empty messages are posted.
 *   * Reconnect-on-error follows the same pattern as mc-bridge.ts.
 */
import { readFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { loadNickGroups, resolveNickGroup, type NickGroup } from './nick-groups.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Config = {
  bridgeUrl: string;
  bridgeToken: string | null;
  webhookUrl: string;
  group: NickGroup;
  msgPrefix: string;
  batchIntervalMs: number;
  blocklist: Set<string>;
};

type SubtitleEntry = {
  subtitle: string;
  category: string;
  count: number;
  minDist: number;
};

type SubtitlePayload = {
  type: string;
  payload: {
    soundId?: string;
    category?: string;
    subtitle?: string;
    x?: number;
    y?: number;
    z?: number;
    distance?: number;
  };
};

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

/** Default sound ID prefixes to drop — high-frequency spam. */
const DEFAULT_BLOCKLIST = [
  'minecraft:entity.player.swim',
  'minecraft:entity.player.splash',
  'minecraft:entity.player.breath',
  'minecraft:entity.player.hurt_drown',
  'minecraft:block.water.ambient',
  'minecraft:ambient.',
  'minecraft:music.',
  'minecraft:entity.generic.footstep',
  'minecraft:block.stone.step',
  'minecraft:block.grass.step',
  'minecraft:block.gravel.step',
  'minecraft:block.sand.step',
  'minecraft:block.wood.step',
  'minecraft:block.snow.step',
  'minecraft:entity.player.attack.nodamage',
  'minecraft:entity.experience_orb.pickup',
  'minecraft:entity.item.pickup',
];

function loadBridgeAuth(): { url: string; token: string | null } {
  const cfgPath =
    process.env.BTONE_BRIDGE_CONFIG ?? '/var/lib/btone/config/btone-bridge.json';
  const raw = readFileSync(cfgPath, 'utf-8');
  const parsed = JSON.parse(raw) as { port: number; token: string | null };
  if (!parsed.port) {
    throw new Error(`btone bridge config at ${cfgPath} missing port`);
  }
  return {
    url: `http://127.0.0.1:${parsed.port}`,
    token: parsed.token || null,
  };
}

function loadConfig(): Config {
  const auth = loadBridgeAuth();
  const groups = loadNickGroups();
  if (!groups) {
    throw new Error(
      'NICK_GROUPS_CONFIG not loadable — subtitle-bridge needs a nick group ' +
        'to derive userId/sessionId/agentConfig from',
    );
  }
  // Reuse the same group resolution as mc-bridge so they share the session.
  const groupName = process.env.MC_NICK_GROUP ?? 'mc';
  const fallbackNick = (process.env.BOT_USERNAME ?? 'sleet1213').toLowerCase();
  const group =
    groups.groups.find((g) => g.name === groupName) ??
    resolveNickGroup(fallbackNick, groups);
  if (!group) {
    throw new Error(
      `subtitle-bridge: no nick group named "${groupName}" and no group ` +
        `claims fallback nick "${fallbackNick}"`,
    );
  }

  // Blocklist: merge defaults with env-provided extras.
  const envBlocklist = process.env.SUBTITLE_BLOCKLIST ?? '';
  const extraEntries = envBlocklist
    .split(',')
    .map((s: string) => s.trim())
    .filter(Boolean);
  const blocklist = new Set([...DEFAULT_BLOCKLIST, ...extraEntries]);

  return {
    bridgeUrl: auth.url,
    bridgeToken: auth.token,
    webhookUrl: process.env.WEBHOOK_URL ?? 'http://localhost:8787',
    group,
    msgPrefix: process.env.SUBTITLE_MSG_PREFIX ?? '[mc-sounds] ',
    batchIntervalMs: parseInt(process.env.SUBTITLE_BATCH_INTERVAL_MS ?? '30000', 10),
    blocklist,
  };
}

// ---------------------------------------------------------------------------
// SSE parser (identical to mc-bridge — minimal, no deps)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Blocklist matcher (supports prefix matching)
// ---------------------------------------------------------------------------

function isBlocked(soundId: string, blocklist: Set<string>): boolean {
  if (blocklist.has(soundId)) return true;
  // Check prefix matches (entries ending with '.' act as prefix filters)
  for (const entry of blocklist) {
    if (entry.endsWith('.') && soundId.startsWith(entry)) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Buffer + flush
// ---------------------------------------------------------------------------

/** Accumulation buffer keyed by soundId. */
const buffer = new Map<string, SubtitleEntry>();

function addToBuffer(
  soundId: string,
  subtitle: string,
  category: string,
  distance: number,
): void {
  const existing = buffer.get(soundId);
  if (existing) {
    existing.count += 1;
    existing.minDist = Math.min(existing.minDist, distance);
  } else {
    buffer.set(soundId, { subtitle, category, count: 1, minDist: distance });
  }
}

/** Format buffer contents into a human-readable summary string. */
function formatBatch(prefix: string): string | null {
  if (buffer.size === 0) return null;

  // Sort: hostile first, then by count descending.
  const entries = [...buffer.values()].sort((a, b) => {
    if (a.category === 'hostile' && b.category !== 'hostile') return -1;
    if (b.category === 'hostile' && a.category !== 'hostile') return 1;
    return b.count - a.count;
  });

  const parts: string[] = [];
  for (const e of entries) {
    let part = e.subtitle;
    if (e.minDist >= 0) {
      part += ` (${Math.round(e.minDist)}m)`;
    }
    if (e.count > 1) {
      part += ` x${e.count}`;
    }
    parts.push(part);
  }

  buffer.clear();
  return prefix + parts.join(' | ');
}

// ---------------------------------------------------------------------------
// Webhook posting
// ---------------------------------------------------------------------------

async function postToWebhook(cfg: Config, text: string): Promise<void> {
  const res = await fetch(`${cfg.webhookUrl}/message`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'X-User-ID': cfg.group.userId,
    },
    body: JSON.stringify({
      sessionId: cfg.group.sessionId,
      msg: text,
      agentConfig: cfg.group.agentConfig,
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`webhook ${res.status}: ${body}`);
  }
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

async function streamSubtitles(cfg: Config, signal: AbortSignal): Promise<void> {
  const url = `${cfg.bridgeUrl}/events`;
  const headers: Record<string, string> = cfg.bridgeToken
    ? { Authorization: `Bearer ${cfg.bridgeToken}` }
    : {};

  // Flush timer — runs independently of the SSE stream.
  const flushInterval = setInterval(async () => {
    const msg = formatBatch(cfg.msgPrefix);
    if (!msg) return;
    try {
      await postToWebhook(cfg, msg);
    } catch (err) {
      console.error('[subtitle-bridge] flush post failed:', (err as Error).message);
    }
  }, cfg.batchIntervalMs);

  try {
    for await (const ev of readSse(url, headers, signal)) {
      if (ev.event !== 'subtitle') continue;

      let payload: SubtitlePayload;
      try {
        payload = JSON.parse(ev.data) as SubtitlePayload;
      } catch {
        continue;
      }

      const p = payload.payload ?? payload;
      const soundId = (p as Record<string, unknown>).soundId as string | undefined;
      const subtitle = (p as Record<string, unknown>).subtitle as string | undefined;
      const category = (p as Record<string, unknown>).category as string | undefined;
      const distance = (p as Record<string, unknown>).distance as number | undefined;

      if (!soundId || !subtitle) continue;
      if (isBlocked(soundId, cfg.blocklist)) continue;

      addToBuffer(soundId, subtitle, category ?? 'unknown', distance ?? -1);
    }
  } finally {
    clearInterval(flushInterval);
    // Flush remaining buffer on disconnect.
    const msg = formatBatch(cfg.msgPrefix);
    if (msg) {
      try {
        await postToWebhook(cfg, msg);
      } catch {
        // swallow — we're shutting down
      }
    }
  }
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  console.log(
    `[subtitle-bridge] subscribing to ${cfg.bridgeUrl}/events → ` +
      `${cfg.webhookUrl}/message (session=${cfg.group.sessionId} ` +
      `userId=${cfg.group.userId} group=${cfg.group.name} ` +
      `batch=${cfg.batchIntervalMs}ms)`,
  );

  const abort = new AbortController();
  process.on('SIGINT', () => abort.abort());
  process.on('SIGTERM', () => abort.abort());

  while (!abort.signal.aborted) {
    try {
      await streamSubtitles(cfg, abort.signal);
    } catch (err) {
      if (abort.signal.aborted) return;
      console.error('[subtitle-bridge] stream error:', (err as Error).message);
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
