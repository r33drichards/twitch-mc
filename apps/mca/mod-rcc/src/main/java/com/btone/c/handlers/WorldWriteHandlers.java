package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcHandler;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.client.Minecraft;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.Vec3;

public final class WorldWriteHandlers {
    private static final ObjectMapper M = new ObjectMapper();
    private static final long TIMEOUT_MS = 2_000;

    private WorldWriteHandlers() {}

    public static void registerAll(RpcRouter r) {
        r.register("world.mine_block", mineBlock());
        r.register("world.place_block", placeBlock());
        r.register("world.use_item", useItem());
        r.register("world.interact_entity", interactEntity());
        r.register("world.mine_down", mineDown());
        r.register("world.bridge", bridge());
    }

    /**
     * Build a flat horizontal bridge in the given direction. Unlike
     * {@code player.bridge} (which holds forward+use+sneak and relies on
     * the client's raycast to pick a place target), this synthesizes a
     * {@link net.minecraft.util.hit.BlockHitResult} on the chosen face of
     * the current floor block. Placement always targets the correct face
     * regardless of camera pitch, so it works from the edge of a platform
     * where the raycast-based approach silently misses.
     *
     * See {@link BridgeTask} for the tick-loop implementation and the
     * termination conditions.
     *
     * params: { block?: string (default "minecraft:basalt"),
     *           direction: "+x"|"-x"|"+z"|"-z",
     *           distance: int,
     *           max_ticks?: int (default 1200 = 1 min) }
     */
    private static RpcHandler bridge() {
        return params -> {
            String block = params.has("block") ? params.get("block").asText() : "minecraft:basalt";
            String direction = params.get("direction").asText();
            int distance = params.get("distance").asInt();
            int maxTicks = params.has("max_ticks") ? params.get("max_ticks").asInt() : 1200;
            try {
                return BridgeTask.submit(block, direction, distance, maxTicks)
                        .get(120_000, java.util.concurrent.TimeUnit.MILLISECONDS);
            } catch (java.util.concurrent.TimeoutException te) {
                ObjectNode n = M.createObjectNode();
                n.put("ok", false);
                n.put("reason", "rpc_timeout_120s");
                return n;
            }
        };
    }

    /**
     * Break {@code count} blocks straight down from the bot's current feet
     * position. Drives {@link net.minecraft.client.network.ClientPlayerInteractionManager#updateBlockBreakingProgress}
     * from the client-tick callback — the only reliable way to run continuous
     * mining from an external agent (see {@link MineDownTask} for why
     * attackBlock/press_key paths don't work).
     *
     * params: { count: int, max_ticks?: int (default 6000 = ~5 min) }
     * blocks the HTTP thread for up to 5 min waiting for the task to finish.
     */
    private static RpcHandler mineDown() {
        return params -> {
            int count = params.get("count").asInt();
            int maxTicks = params.has("max_ticks") ? params.get("max_ticks").asInt() : 6000;
            try {
                return MineDownTask.submit(count, maxTicks)
                        .get(300_000, java.util.concurrent.TimeUnit.MILLISECONDS);
            } catch (java.util.concurrent.TimeoutException te) {
                ObjectNode n = M.createObjectNode();
                n.put("ok", false);
                n.put("reason", "rpc_timeout_300s");
                return n;
            }
        };
    }

    private static String describeAction(InteractionResult ar) {
        // InteractionResult is a sealed interface in 1.21+, not an enum. Use the
        // simple class name (Success / Fail / Pass / TryEmptyHandInteraction).
        return ar == null ? "Null" : ar.getClass().getSimpleName();
    }

    private static RpcHandler mineBlock() {
        // Fully break a single block. Calls attackBlock then repeatedly calls
        // updateBlockBreakingProgress until the block is gone or a timeout.
        return params -> {
            int x = params.get("x").asInt();
            int y = params.get("y").asInt();
            int z = params.get("z").asInt();
            int timeoutTicks = params.path("timeout").asInt(200); // ~10s max

            BlockPos pos = new BlockPos(x, y, z);

            // Start the break on the client thread.
            Direction[] sideHolder = new Direction[1];
            ClientThread.call(TIMEOUT_MS, () -> {
                var mc = Minecraft.getInstance();
                if (mc.gameMode == null || mc.player == null)
                    throw new IllegalStateException("no_player");
                sideHolder[0] = chooseSide(pos);
                aimAt(Vec3.atCenterOf(pos));
                mc.gameMode.startDestroyBlock(pos, sideHolder[0]);
                return null;
            });

            // Now pump updateBlockBreakingProgress on the client thread each tick
            // until the block is broken (turns to air) or we time out.
            int ticks = 0;
            while (ticks < timeoutTicks) {
                try { Thread.sleep(50); } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt(); break;
                }
                ticks++;
                Boolean done = ClientThread.call(TIMEOUT_MS, () -> {
                    var mc = Minecraft.getInstance();
                    if (mc.gameMode == null) return true;
                    // Check if block is already broken
                    if (mc.level != null && mc.level.getBlockState(pos).isAir()) return true;
                    // Continue mining
                    aimAt(Vec3.atCenterOf(pos));
                    mc.gameMode.continueDestroyBlock(pos, sideHolder[0]);
                    return false;
                });
                if (Boolean.TRUE.equals(done)) break;
            }

            // Check final state
            final int finalTicks = ticks;
            return ClientThread.call(TIMEOUT_MS, () -> {
                var mc = Minecraft.getInstance();
                boolean broken = mc.level != null && mc.level.getBlockState(pos).isAir();
                ObjectNode n = M.createObjectNode();
                n.put("broken", broken);
                n.put("ticks", finalTicks);
                return n;
            });
        };
    }

    private static RpcHandler placeBlock() {
        return params -> ClientThread.call(TIMEOUT_MS, () -> {
            int x = params.get("x").asInt();
            int y = params.get("y").asInt();
            int z = params.get("z").asInt();
            String hand = params.path("hand").asText("main");
            // Optional `side` overrides chooseSide() — useful when caller
            // knows which face to click (e.g. UP face of a basalt floor to
            // place a crafting_table on top). Values: up/down/north/south/east/west.
            String sideStr = params.path("side").asText("");
            var mc = Minecraft.getInstance();
            if (mc.gameMode == null || mc.player == null) {
                throw new IllegalStateException("no_player");
            }
            BlockPos pos = new BlockPos(x, y, z);
            Direction side;
            if (!sideStr.isEmpty()) {
                Direction parsed = null;
                for (Direction d : Direction.values()) {
                    if (d.getName().equalsIgnoreCase(sideStr)) { parsed = d; break; }
                }
                if (parsed == null) throw new IllegalArgumentException("bad_side:" + sideStr);
                side = parsed;
            } else {
                side = chooseSide(pos);
            }
            aimAt(Vec3.atCenterOf(pos));
            BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), side, pos, false);
            InteractionResult result = mc.gameMode.useItemOn(
                    mc.player,
                    "off".equalsIgnoreCase(hand) ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND,
                    hit);
            ObjectNode n = M.createObjectNode();
            n.put("result", describeAction(result));
            n.put("side", side.getName());
            return n;
        });
    }

    private static RpcHandler useItem() {
        return params -> ClientThread.call(TIMEOUT_MS, () -> {
            String hand = params == null || params.isNull() ? "main" : params.path("hand").asText("main");
            var mc = Minecraft.getInstance();
            if (mc.gameMode == null || mc.player == null) {
                throw new IllegalStateException("no_player");
            }
            InteractionResult result = mc.gameMode.useItem(
                    mc.player,
                    "off".equalsIgnoreCase(hand) ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND);
            ObjectNode n = M.createObjectNode();
            n.put("result", describeAction(result));
            return n;
        });
    }

    private static RpcHandler interactEntity() {
        return params -> ClientThread.call(TIMEOUT_MS, () -> {
            int id = params.get("entityId").asInt();
            String hand = params.path("hand").asText("main");
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.level == null || mc.gameMode == null) {
                throw new IllegalStateException("no_player");
            }
            var e = mc.level.getEntity(id);
            if (e == null) throw new IllegalArgumentException("no_entity:" + id);
            InteractionResult result = mc.gameMode.interact(
                    p,
                    e,
                    new EntityHitResult(e),
                    "off".equalsIgnoreCase(hand) ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND);
            ObjectNode n = M.createObjectNode();
            n.put("result", describeAction(result));
            return n;
        });
    }

    private static Direction chooseSide(BlockPos pos) {
        var p = Minecraft.getInstance().player;
        if (p == null) return Direction.UP;
        Vec3 eye = p.getEyePosition(1.0f);
        Vec3 center = Vec3.atCenterOf(pos);
        Vec3 dir = center.subtract(eye);
        // Pick the dominant axis of the approach vector and return the inverse face.
        double ax = Math.abs(dir.x), ay = Math.abs(dir.y), az = Math.abs(dir.z);
        if (ax > ay && ax > az) return dir.x > 0 ? Direction.WEST : Direction.EAST;
        if (ay > az) return dir.y > 0 ? Direction.DOWN : Direction.UP;
        return dir.z > 0 ? Direction.NORTH : Direction.SOUTH;
    }

    private static void aimAt(Vec3 target) {
        var p = Minecraft.getInstance().player;
        if (p == null) return;
        Vec3 eye = p.getEyePosition(1.0f);
        double dx = target.x - eye.x;
        double dy = target.y - eye.y;
        double dz = target.z - eye.z;
        double horiz = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        float pitch = (float) -Math.toDegrees(Math.atan2(dy, horiz));
        p.setYRot(yaw);
        p.setXRot(pitch);
    }
}
