package com.btone.c.events;

import com.btone.c.BtoneC;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.sound.SoundInstance;
import net.minecraft.client.sound.SoundInstanceListener;
import net.minecraft.client.sound.WeightedSoundSet;
import net.minecraft.sound.SoundCategory;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Captures Minecraft sound events via the vanilla {@link SoundInstanceListener}
 * API and emits filtered {@code "subtitle"} events on the shared {@link EventBus}.
 *
 * <p>Only sounds with subtitle text and from relevant categories are emitted.
 * Rapid-fire duplicates (same sound ID within 1 second) are suppressed to
 * avoid flooding the SSE stream.</p>
 *
 * <p>Registration is deferred to {@code CLIENT_STARTED} because the
 * {@link net.minecraft.client.sound.SoundManager} is not ready at
 * {@code onInitializeClient} time.</p>
 */
public final class SubtitleEvents implements SoundInstanceListener {

    /** Categories worth reporting to the agent. */
    private static final Set<SoundCategory> KEEP = Set.of(
            SoundCategory.HOSTILE,
            SoundCategory.PLAYERS,
            SoundCategory.BLOCKS,
            SoundCategory.WEATHER,
            SoundCategory.NEUTRAL
    );

    /** Minimum interval (ms) between emitting the same sound ID. */
    private static final long DEDUP_INTERVAL_MS = 1_000;

    private final EventBus bus;

    /** Last-emit timestamp per sound ID for deduplication. */
    private final ConcurrentHashMap<Identifier, Long> lastEmit = new ConcurrentHashMap<>();

    private SubtitleEvents(EventBus bus) {
        this.bus = bus;
    }

    /**
     * Register the subtitle listener. Must be called after the client has
     * started (i.e. inside a {@code CLIENT_STARTED} callback) so that the
     * SoundManager is initialised.
     */
    public static void register(EventBus bus) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client.getSoundManager() == null) {
            BtoneC.LOG.warn("subtitle-events: SoundManager null at registration time; skipping");
            return;
        }
        SubtitleEvents listener = new SubtitleEvents(bus);
        client.getSoundManager().registerListener(listener);
        BtoneC.LOG.info("subtitle-events: registered SoundInstanceListener");
    }

    @Override
    public void onSoundPlayed(SoundInstance sound, WeightedSoundSet soundSet) {
        // --- category filter ---
        if (!KEEP.contains(sound.getCategory())) return;

        // --- subtitle text (same null-check as SubtitlesHud) ---
        Text subtitle = soundSet.getSubtitle();
        if (subtitle == null) return;

        // --- dedup: skip if same soundId fired within DEDUP_INTERVAL_MS ---
        Identifier id = sound.getId();
        long now = System.currentTimeMillis();
        Long prev = lastEmit.get(id);
        if (prev != null && (now - prev) < DEDUP_INTERVAL_MS) return;
        lastEmit.put(id, now);

        // --- compute distance from player ---
        MinecraftClient mc = MinecraftClient.getInstance();
        double distance = -1;
        if (mc.player != null && !sound.isRelative()) {
            double dx = sound.getX() - mc.player.getX();
            double dy = sound.getY() - mc.player.getY();
            double dz = sound.getZ() - mc.player.getZ();
            distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
        }

        // --- emit on EventBus ---
        Map<String, Object> payload = new HashMap<>();
        payload.put("soundId", id.toString());
        payload.put("category", sound.getCategory().getName());
        payload.put("subtitle", subtitle.getString());
        payload.put("x", sound.getX());
        payload.put("y", sound.getY());
        payload.put("z", sound.getZ());
        payload.put("distance", Math.round(distance * 10.0) / 10.0);
        bus.emit("subtitle", payload);

        // --- periodic cleanup of stale dedup entries ---
        if (lastEmit.size() > 200) {
            lastEmit.entrySet().removeIf(e -> (now - e.getValue()) > 10_000);
        }
    }
}
