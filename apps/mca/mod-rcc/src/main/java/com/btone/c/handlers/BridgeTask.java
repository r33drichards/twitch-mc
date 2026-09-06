package com.btone.c.handlers;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionHand;
import net.minecraft.resources.Identifier;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.Vec3;

import java.util.Queue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Horizontal bridge task — places blocks in a straight line in the given
 * direction (+x, -x, +z, -z), walking the bot across as it goes.
 *
 * Why this exists: {@link MovementTasks} BRIDGE_FLAT mode relies on
 * useKey.setDown(true) to place blocks each tick. The vanilla use
 * handler runs a raytrace from the bot's eye, and when the bot is
 * at the edge of its floor block looking east with pitch=70, the ray
 * EXITS above the east face (the floor block is 1m tall, the eye is
 * 1.62m above; at pitch=70 the ray reaches the east face at y=feet+0.25
 * which is ABOVE the top of the floor block). No face is hit → nothing
 * places → forward pushes the bot off the edge into water → death.
 *
 * This task bypasses the raycast entirely: it constructs a synthetic
 * {@link BlockHitResult} on the east face of the current floor block
 * and hands it directly to {@link net.minecraft.client.multiplayer.MultiPlayerGameMode#useItemOn},
 * which places the block east-adjacent regardless of camera angle.
 *
 * Stepping is driven by vanilla physics: the task holds
 * {@code forwardKey + sneakKey} for the duration. Sneak prevents
 * falling off the front edge when placement fails for a tick; forward
 * moves the bot onto each newly placed tile. We DON'T teleport via
 * {@code refreshPositionAndAngles} — this server's anticheat snaps the
 * bot back, and teleport-stepping turned into "place one block, bot
 * snaps back, place same block, repeat" until maxTicks expired.
 *
 * Termination conditions (first match wins):
 *   1. {@code blocksPlaced >= distance} → success
 *   2. {@code ticksRun >= maxTicks} → timeout
 *   3. Block id not present in hotbar → no_block_in_hotbar
 *   4. Next target position already has a solid non-basalt block → reached_land
 *   5. Bot HP hits 0 → died
 *   6. Bot falls below startY-2 (in water) → fell_off
 */
public final class BridgeTask {
    private static final ObjectMapper M = new ObjectMapper();
    private static final Queue<BridgeTask> PENDING = new ConcurrentLinkedQueue<>();
    private static volatile boolean tickHandlerInstalled = false;

    private final String blockId;
    private final String direction;      // "+x", "-x", "+z", "-z"
    private final int distance;
    private final int maxTicks;
    private final CompletableFuture<ObjectNode> future;
    private int startX = Integer.MIN_VALUE, startY = Integer.MIN_VALUE, startZ = Integer.MIN_VALUE;
    private int ticksRun = 0;
    private int blocksPlaced = 0;
    private String terminationReason = null;

    private BridgeTask(String blockId, String direction, int distance, int maxTicks,
                       CompletableFuture<ObjectNode> future) {
        this.blockId = blockId;
        this.direction = direction;
        this.distance = distance;
        this.maxTicks = maxTicks;
        this.future = future;
    }

    public static CompletableFuture<ObjectNode> submit(String blockId, String direction,
                                                        int distance, int maxTicks) {
        ensureTickHandler();
        CompletableFuture<ObjectNode> f = new CompletableFuture<>();
        PENDING.add(new BridgeTask(blockId, direction, distance, maxTicks, f));
        return f;
    }

    private static synchronized void ensureTickHandler() {
        if (tickHandlerInstalled) return;
        tickHandlerInstalled = true;
        ClientTickEvents.END_CLIENT_TICK.register(BridgeTask::onTick);
    }

    private static void onTick(Minecraft mc) {
        BridgeTask task = PENDING.peek();
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

    private boolean runOneTick(Minecraft mc) {
        LocalPlayer p = mc.player;
        if (p == null || mc.level == null || mc.gameMode == null) {
            complete(false, "no_world");
            releaseKeys(mc);
            return true;
        }
        if (startX == Integer.MIN_VALUE) {
            startX = p.getBlockX();
            startY = p.getBlockY();
            startZ = p.getBlockZ();
        }
        ticksRun++;

        // Safety: if bot died, bail.
        if (p.getHealth() <= 0.0f) {
            complete(false, "died");
            releaseKeys(mc);
            return true;
        }
        // Safety: if bot dropped significantly, it fell off the bridge — bail
        // so the agent can recover. A 2-block drop is the tripwire — normal
        // stepping shouldn't lose more than fractional y, and water entry
        // pulls the bot ~1.5 blocks below starting Y before the agent can
        // react.
        if (p.getBlockY() < startY - 2) {
            complete(false, "fell_off");
            releaseKeys(mc);
            return true;
        }

        if (ticksRun >= maxTicks) {
            complete(false, "timeout");
            releaseKeys(mc);
            return true;
        }
        if (blocksPlaced >= distance) {
            complete(true, null);
            releaseKeys(mc);
            return true;
        }

        // Hold sneak (fall-prevention) + forward (physics moves the bot onto
        // each placed tile). Vanilla edge-sneak stops the bot hard at the
        // east edge when nothing's there yet, and releases as soon as a
        // solid tile appears in front of its foot. This is the same
        // mechanic real players use to bridge — we just bypass the
        // raycast-target-miss by placing via a synthetic BlockHitResult
        // below.
        mc.options.keyShift.setDown(true);
        mc.options.keyUp.setDown(true);
        mc.options.keyUse.setDown(false);
        mc.options.keyJump.setDown(false);

        // Block in hotbar? Select it.
        int hotbarSlot = findBlockInHotbar(p, blockId);
        if (hotbarSlot < 0) {
            complete(false, "no_block_in_hotbar");
            releaseKeys(mc);
            return true;
        }
        if (p.getInventory().getSelectedSlot() != hotbarSlot) {
            p.getInventory().setSelectedSlot(hotbarSlot);
        }

        // Pin yaw/pitch looking in the travel direction, slightly down so
        // third-person renders look natural. Placement does NOT depend on
        // this — we use a synthetic BlockHitResult — but aim-logging tools
        // read the yaw, and future agent vision/debug looks cleaner when
        // the bot faces the direction it's bridging.
        float yaw = yawForDirection(direction);
        p.setYRot(yaw);
        p.setXRot(30.0f);
        p.setYHeadRot(yaw);
        p.setYBodyRot(yaw);
        p.yRotO = yaw;
        p.yHeadRotO = yaw;
        p.yBodyRotO = yaw;
        p.yHeadRot = yaw;
        p.yBodyRot = yaw;
        p.xRotO = 30.0f;

        // Current floor block is at (bx, by-1, bz). Target placement is one
        // step in the travel direction at the same y-1 (so it's level with
        // the floor, a flat bridge).
        int bx = p.getBlockX(), by = p.getBlockY(), bz = p.getBlockZ();
        int dx = 0, dz = 0;
        Direction face;
        switch (direction) {
            case "+x": dx = 1;  face = Direction.EAST;  break;
            case "-x": dx = -1; face = Direction.WEST;  break;
            case "+z": dz = 1;  face = Direction.SOUTH; break;
            case "-z": dz = -1; face = Direction.NORTH; break;
            default:
                complete(false, "bad_direction:" + direction);
                releaseKeys(mc);
                return true;
        }

        BlockPos floorPos = new BlockPos(bx, by - 1, bz);
        BlockPos targetPos = new BlockPos(bx + dx, by - 1, bz + dz);

        // If the target is already solid (basalt from a prior tick OR natural
        // land), skip placement and step forward. reachedLand check: the
        // target block is solid AND it isn't the block we just placed.
        BlockState targetState = mc.level.getBlockState(targetPos);
        boolean targetIsSolid = !targetState.isAir()
                && targetState.getBlock() != Blocks.WATER
                && targetState.getBlock() != Blocks.LAVA;
        if (targetIsSolid) {
            // Land reached OR we already placed here earlier. If it's
            // natural land AND we've already placed at least one block,
            // we've made it — report success. Otherwise keep the keys
            // pressed and let physics step the bot forward. The agent
            // calls world.bridge again if they want to continue past
            // natural land.
            Identifier tid = BuiltInRegistries.BLOCK.getKey(targetState.getBlock());
            Identifier placedId = Identifier.tryParse(blockId);
            boolean isNaturalLand = placedId == null || !tid.equals(placedId);
            if (isNaturalLand && blocksPlaced > 0) {
                complete(true, "reached_land");
                releaseKeys(mc);
                return true;
            }
            // Already-placed (or a natural-land tile we haven't placed
            // beyond yet on tick 0). Physics will walk the bot over it.
            return false;
        }

        // Floor block must be solid (something to place AGAINST).
        BlockState floorState = mc.level.getBlockState(floorPos);
        if (floorState.isAir()) {
            complete(false, "no_floor_to_place_against");
            releaseKeys(mc);
            return true;
        }

        // Synthetic hit on the east (or other-direction) face of the floor
        // block. Vec3 is a point ON that face for the server's validator.
        Vec3 hitVec = Vec3.atCenterOf(floorPos).add(
                face.getStepX() * 0.5,
                face.getStepY() * 0.5,
                face.getStepZ() * 0.5);
        BlockHitResult hit = new BlockHitResult(hitVec, face, floorPos, false);

        mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit);

        // Verify placement landed (server may reject on anticheat or if the
        // slot went empty between tick-start and the interact call).
        BlockState afterState = mc.level.getBlockState(targetPos);
        if (afterState.isAir()
                || afterState.getBlock() == Blocks.WATER
                || afterState.getBlock() == Blocks.LAVA) {
            // Placement didn't land this tick. Leave task running — next
            // tick it will retry. If this keeps failing, maxTicks will
            // eventually fire. Most commonly this is a transient "slot
            // being swapped by auto-replenish" issue that clears in 1-2 ticks.
            return false;
        }

        blocksPlaced++;
        // No teleport — forward+sneak keys move the bot over the new tile
        // across the next few ticks. Return false to keep the task running;
        // the next tick's targetIsSolid branch will be entered once the bot
        // has crossed onto the placed block (because bot's blockX/blockZ
        // updated) and fresh target will be one tile further east.
        return false;
    }

    private void complete(boolean ok, String reason) {
        ObjectNode n = M.createObjectNode();
        n.put("ok", ok);
        if (reason != null) n.put("reason", reason);
        n.put("direction", direction);
        n.put("blocksPlaced", blocksPlaced);
        n.put("distance_requested", distance);
        n.put("ticks", ticksRun);
        n.put("blockId", blockId);
        n.put("startX", startX);
        n.put("startY", startY);
        n.put("startZ", startZ);
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

    private static float yawForDirection(String dir) {
        switch (dir) {
            case "+x": return -90f;   // east
            case "-x": return 90f;    // west
            case "+z": return 0f;     // south
            case "-z": return 180f;   // north
            default:   return 0f;
        }
    }

    private static void releaseKeys(Minecraft mc) {
        try {
            mc.options.keyShift.setDown(false);
            mc.options.keyUp.setDown(false);
            mc.options.keyUse.setDown(false);
            mc.options.keyJump.setDown(false);
        } catch (Throwable ignored) {}
    }
}
