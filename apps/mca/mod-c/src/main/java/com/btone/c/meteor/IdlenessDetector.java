package com.btone.c.meteor;

import meteordevelopment.meteorclient.settings.IntSetting;
import meteordevelopment.meteorclient.settings.Setting;
import meteordevelopment.meteorclient.settings.SettingGroup;
import meteordevelopment.meteorclient.systems.modules.Categories;
import meteordevelopment.meteorclient.systems.modules.Module;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.util.math.Vec3d;

/**
 * IdlenessDetector - Notifies agent when bot is stuck or not making progress.
 *
 * Tracks bot position over time and alerts if:
 * - Bot hasn't moved more than threshold distance in X seconds
 * - Useful for detecting pathfinding failures, obstacles, or being trapped
 */
public class IdlenessDetector extends Module {
    private final SettingGroup sgGeneral = settings.getDefaultGroup();

    private final Setting<Integer> checkIntervalSeconds = sgGeneral.add(new IntSetting.Builder()
        .name("check-interval")
        .description("Check for idleness every N seconds.")
        .defaultValue(30)
        .range(10, 300)
        .sliderRange(10, 120)
        .build());

    private final Setting<Integer> movementThreshold = sgGeneral.add(new IntSetting.Builder()
        .name("movement-threshold")
        .description("Consider idle if moved less than this many blocks in check interval.")
        .defaultValue(5)
        .range(1, 50)
        .sliderRange(1, 20)
        .build());

    private final Setting<Integer> notificationCooldown = sgGeneral.add(new IntSetting.Builder()
        .name("notification-cooldown")
        .description("Wait this many seconds before sending another idle notification.")
        .defaultValue(60)
        .range(30, 600)
        .sliderRange(30, 300)
        .build());

    private Vec3d lastCheckPos = null;
    private long lastCheckTime = 0L;
    private long lastNotificationTime = 0L;
    private boolean tickRegistered = false;

    public IdlenessDetector() {
        super(Categories.Misc, "idleness-detector",
            "Notifies agent when bot is stuck or not making progress.");
    }

    @Override
    public void onActivate() {
        PlayerEntity p = MinecraftClient.getInstance().player;
        if (p != null) {
            lastCheckPos = p.getPos();
            lastCheckTime = System.currentTimeMillis();
            lastNotificationTime = 0L;
        }
        ensureTickRegistered();
    }

    private void ensureTickRegistered() {
        if (tickRegistered) return;
        ClientTickEvents.END_CLIENT_TICK.register(this::tick);
        tickRegistered = true;
    }

    private void tick(MinecraftClient client) {
        if (!isActive()) return;
        runTick();
    }

    private void runTick() {
        MinecraftClient mc = MinecraftClient.getInstance();
        PlayerEntity player = mc.player;
        if (player == null || mc.world == null) return;

        long now = System.currentTimeMillis();
        long elapsedMs = now - lastCheckTime;
        int intervalMs = checkIntervalSeconds.get() * 1000;

        // Not time to check yet
        if (elapsedMs < intervalMs) return;

        Vec3d currentPos = player.getPos();

        // First check - just record position
        if (lastCheckPos == null) {
            lastCheckPos = currentPos;
            lastCheckTime = now;
            return;
        }

        // Calculate distance moved
        double distanceMoved = currentPos.distanceTo(lastCheckPos);

        // Update check time and position
        lastCheckPos = currentPos;
        lastCheckTime = now;

        // Check if bot is idle
        if (distanceMoved < movementThreshold.get()) {
            // Check notification cooldown
            long timeSinceLastNotification = now - lastNotificationTime;
            int cooldownMs = notificationCooldown.get() * 1000;

            if (timeSinceLastNotification >= cooldownMs) {
                lastNotificationTime = now;

                info("[idleness-detector] Bot idle - moved only %.1f blocks in %d seconds",
                    distanceMoved, checkIntervalSeconds.get());

                // Notify agent
                try {
                    String message = String.format(
                        "Bot appears stuck or idle. Moved only %.1f blocks in %d seconds at position (%d, %d, %d)",
                        distanceMoved,
                        checkIntervalSeconds.get(),
                        (int) currentPos.x,
                        (int) currentPos.y,
                        (int) currentPos.z
                    );
                    AgentNotifier.notify(message, "medium");
                } catch (Throwable t) {
                    warning("Failed to notify agent: %s", t.getClass().getSimpleName());
                }
            }
        }
    }
}
