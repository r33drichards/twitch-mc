package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcHandler;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.core.BlockPos;

public final class WorldReadHandlers {
    private static final ObjectMapper M = new ObjectMapper();
    private static final long TIMEOUT_MS = 2_000;

    private WorldReadHandlers() {}

    public static void registerAll(RpcRouter r) {
        r.register("world.block_at", blockAt());
        r.register("world.blocks_around", blocksAround());
        r.register("world.raycast", raycast());
    }

    private static ObjectNode describe(BlockState s) {
        ObjectNode n = M.createObjectNode();
        n.put("id", BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString());
        n.put("air", s.isAir());
        return n;
    }

    private static RpcHandler blockAt() {
        return params -> ClientThread.call(TIMEOUT_MS, () -> {
            int x = params.get("x").asInt();
            int y = params.get("y").asInt();
            int z = params.get("z").asInt();
            var w = Minecraft.getInstance().level;
            if (w == null) throw new IllegalStateException("no_world");
            return describe(w.getBlockState(new BlockPos(x, y, z)));
        });
    }

    private static RpcHandler blocksAround() {
        return params -> ClientThread.call(TIMEOUT_MS, () -> {
            int rRaw = params.path("radius").asInt(3);
            int r = Math.max(0, Math.min(rRaw, 8));
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.level == null) throw new IllegalStateException("no_player");
            BlockPos center = p.blockPosition();
            ObjectNode root = M.createObjectNode();
            var arr = root.putArray("blocks");
            for (int dx = -r; dx <= r; dx++) {
                for (int dy = -r; dy <= r; dy++) {
                    for (int dz = -r; dz <= r; dz++) {
                        BlockPos pos = center.offset(dx, dy, dz);
                        BlockState s = mc.level.getBlockState(pos);
                        if (s.isAir()) continue;
                        ObjectNode o = arr.addObject();
                        o.put("x", pos.getX()); o.put("y", pos.getY()); o.put("z", pos.getZ());
                        o.put("id", BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString());
                    }
                }
            }
            return root;
        });
    }

    private static RpcHandler raycast() {
        return params -> ClientThread.call(TIMEOUT_MS, () -> {
            double maxDist = params.path("max").asDouble(5.0);
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.level == null) throw new IllegalStateException("no_player");
            HitResult hit = p.pick(maxDist, 1.0f, false);
            ObjectNode n = M.createObjectNode();
            n.put("type", hit.getType().name());
            if (hit instanceof BlockHitResult bhr && bhr.getType() == HitResult.Type.BLOCK) {
                BlockPos bp = bhr.getBlockPos();
                n.put("x", bp.getX()); n.put("y", bp.getY()); n.put("z", bp.getZ());
                n.put("side", bhr.getDirection().getName());
                n.put("id", BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(bp).getBlock()).toString());
            } else if (hit instanceof EntityHitResult ehr) {
                var e = ehr.getEntity();
                n.put("entityId", e.getId());
                n.put("entityType", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
                n.put("entityName", e.getName().getString());
            }
            return n;
        });
    }
}
