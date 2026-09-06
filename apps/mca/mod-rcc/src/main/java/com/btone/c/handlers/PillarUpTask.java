package com.btone.c.handlers;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;

import java.util.Queue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Pillar-up task — drives the bot to place blocks under itself while jumping
 * until it reaches a target Y. Runs as a tick-driven state machine on the
 * client thread, NOT a blocking RPC, because pillaring takes many ticks
 * (each placement = jump + use across 3-5 ticks).
 *
 * Implementation strategy: hold {@code mc.options.jumpKey} and
 * {@code mc.options.useKey} pressed for the duration of the task, with the
 * player pitch pinned to 90 (looking straight down). Vanilla MC's auto-place
 * mechanic does the rest: while jumping with a placeable block in hand and
 * looking at the floor, MC places the block at the spot the player is leaving.
 *
 * Termination conditions (in priority order):
 *   1. {@code player.blockY >= targetY} → success
 *   2. {@code ticksRun >= maxTicks} → timeout
 *   3. block id not present in hotbar → no_block_in_hotbar
 *   4. world/player goes null mid-task → no_world
 *
 * On any termination, jump+use keys are released so the bot doesn't get
 * stuck in a held-key state.
 */
public final class PillarUpTask {
    private static final ObjectMapper M = new ObjectMapper();
    private static final Queue<PillarUpTask> PENDING = new ConcurrentLinkedQueue<>();
    private static volatile boolean tickHandlerInstalled = false;

    private final String blockId;
    private final int targetY;
    private final int maxTicks;
    private final CompletableFuture<ObjectNode> future;
    private int startY = Integer.MIN_VALUE;
    private int ticksRun = 0;

    private PillarUpTask(String blockId, int targetY, int maxTicks,
                         CompletableFuture<ObjectNode> future) {
        this.blockId = blockId;
        this.targetY = targetY;
        this.maxTicks = maxTicks;
        this.future = future;
    }

    public static CompletableFuture<ObjectNode> submit(String blockId, int targetY, int maxTicks) {
        ensureTickHandler();
        CompletableFuture<ObjectNode> f = new CompletableFuture<>();
        PENDING.add(new PillarUpTask(blockId, targetY, maxTicks, f));
        return f;
    }

    private static synchronized void ensureTickHandler() {
        if (tickHandlerInstalled) return;
        tickHandlerInstalled = true;
        ClientTickEvents.END_CLIENT_TICK.register(PillarUpTask::onTick);
    }

    private static void onTick(Minecraft mc) {
        // Only run one task at a time — there's only one player to pillar.
        PillarUpTask task = PENDING.peek();
        if (task == null) return;
        boolean done;
        try {
            done = task.runOneTick(mc);
        } catch (Throwable t) {
            done = true;
            ObjectNode n = M.createObjectNode();
            n.put("ok", false);
            n.put("reason", "exception");
            n.put("message", t.toString());
            task.future.complete(n);
            releaseKeys(mc);
        }
        if (done) PENDING.poll();
    }

    /** Returns true when this task should be removed from the queue. */
    private boolean runOneTick(Minecraft mc) {
        Player p = mc.player;
        if (p == null || mc.level == null) {
            complete(false, "no_world", -1);
            releaseKeys(mc);
            return true;
        }
        if (startY == Integer.MIN_VALUE) startY = p.getBlockY();
        ticksRun++;

        int currentY = p.getBlockY();
        if (currentY >= targetY) {
            complete(true, null, currentY);
            releaseKeys(mc);
            return true;
        }
        if (ticksRun >= maxTicks) {
            complete(false, "timeout", currentY);
            releaseKeys(mc);
            return true;
        }

        int hotbarSlot = findBlockInHotbar(p, blockId);
        if (hotbarSlot < 0) {
            complete(false, "no_block_in_hotbar", currentY);
            releaseKeys(mc);
            return true;
        }
        // Select that hotbar slot so the place uses the right item.
        if (p.getInventory().getSelectedSlot() != hotbarSlot) {
            p.getInventory().setSelectedSlot(hotbarSlot);
        }

        // Pin pitch to straight down. Vision rotation taught us we also have
        // to stomp lastPitch — the renderer interpolates, and a tween between
        // the saved pitch and 90 means the place targets the wrong block face.
        p.setXRot(90.0f);
        p.xRotO = 90.0f;

        // Hold jump + use keys. Vanilla auto-place does the actual work:
        // each jump apex, MC fires a useItem on the block under the feet.
        mc.options.keyJump.setDown(true);
        mc.options.keyUse.setDown(true);

        return false;
    }

    private void complete(boolean ok, String reason, int reachedY) {
        ObjectNode n = M.createObjectNode();
        n.put("ok", ok);
        if (reason != null) n.put("reason", reason);
        n.put("startY", startY);
        n.put("reachedY", reachedY);
        n.put("targetY", targetY);
        n.put("ticks", ticksRun);
        n.put("blockId", blockId);
        future.complete(n);
    }

    private static int findBlockInHotbar(Player p, String blockId) {
        Identifier targetId = Identifier.tryParse(blockId);
        if (targetId == null) return -1;
        Inventory inv = p.getInventory();
        for (int i = 0; i < 9; i++) {
            ItemStack s = inv.getItem(i);
            if (s.isEmpty()) continue;
            Identifier id = BuiltInRegistries.ITEM.getKey(s.getItem());
            if (id.equals(targetId)) return i;
        }
        return -1;
    }

    private static void releaseKeys(Minecraft mc) {
        try {
            mc.options.keyJump.setDown(false);
            mc.options.keyUse.setDown(false);
        } catch (Throwable ignored) {}
    }
}
