package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.CraftingScreen;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Hand;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;

/**
 * Simple, composable crafting commands (Unix philosophy).
 * Each command does one thing well and can be chained.
 */
public final class CraftingHandlers {
    private static final ObjectMapper M = new ObjectMapper();

    private CraftingHandlers() {}

    public static void registerAll(RpcRouter r) {
        // Find nearby crafting table within radius
        // Returns: {found: true, pos: {x,y,z}} or {found: false}
        r.register("craft.find_table", params -> ClientThread.call(2_000, () -> {
            int radius = params.path("radius").asInt(5);
            var mc = MinecraftClient.getInstance();
            var p = mc.player;
            if (p == null || mc.world == null) {
                throw new IllegalStateException("no_player");
            }

            Vec3d playerPos = p.getPos();
            for (int x = -radius; x <= radius; x++) {
                for (int y = -radius; y <= radius; y++) {
                    for (int z = -radius; z <= radius; z++) {
                        BlockPos pos = new BlockPos(
                            (int) playerPos.x + x,
                            (int) playerPos.y + y,
                            (int) playerPos.z + z
                        );
                        Identifier blockId = Registries.BLOCK.getId(mc.world.getBlockState(pos).getBlock());
                        if (blockId != null && blockId.toString().equals("minecraft:crafting_table")) {
                            ObjectNode n = M.createObjectNode();
                            n.put("found", true);
                            ObjectNode posNode = n.putObject("pos");
                            posNode.put("x", pos.getX());
                            posNode.put("y", pos.getY());
                            posNode.put("z", pos.getZ());
                            return n;
                        }
                    }
                }
            }

            ObjectNode n = M.createObjectNode();
            n.put("found", false);
            return n;
        }));


        // Open crafting table at specific coordinates
        r.register("craft.open_table", params -> ClientThread.call(2_000, () -> {
            int x = params.get("x").asInt();
            int y = params.get("y").asInt();
            int z = params.get("z").asInt();

            var mc = MinecraftClient.getInstance();
            var p = mc.player;
            if (p == null || mc.interactionManager == null) {
                throw new IllegalStateException("no_player");
            }

            try {
                BlockPos pos = new BlockPos(x, y, z);
                BlockHitResult hit = new BlockHitResult(Vec3d.ofCenter(pos), Direction.UP, pos, false);
                mc.interactionManager.interactBlock(p, Hand.MAIN_HAND, hit);
                Thread.sleep(200);

                ObjectNode n = M.createObjectNode();
                n.put("opened", mc.currentScreen instanceof CraftingScreen);
                return n;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("interrupted", e);
            }
        }));

        // Craft bread (assumes crafting screen is already open)
        // Params: count (default 1)
        // Returns: {crafted: N}
        r.register("craft.bread", params -> ClientThread.call(5_000, () -> {
            int count = params.path("count").asInt(1);

            var mc = MinecraftClient.getInstance();
            var p = mc.player;
            if (p == null || mc.interactionManager == null) {
                throw new IllegalStateException("no_player");
            }

            if (!(mc.currentScreen instanceof CraftingScreen screen)) {
                throw new IllegalStateException("crafting_screen_not_open");
            }

            try {
                int syncId = screen.getScreenHandler().syncId;
                int crafted = 0;

                for (int i = 0; i < count; i++) {
                    // Find wheat in inventory (skip craft grid slots 0-9)
                    Integer wheatSlot = null;
                    for (int slot = 10; slot < screen.getScreenHandler().slots.size(); slot++) {
                        ItemStack stack = screen.getScreenHandler().slots.get(slot).getStack();
                        if (!stack.isEmpty()) {
                            Identifier id = Registries.ITEM.getId(stack.getItem());
                            if (id != null && id.toString().equals("minecraft:wheat") && stack.getCount() >= 3) {
                                wheatSlot = slot;
                                break;
                            }
                        }
                    }

                    if (wheatSlot == null) break;

                    // Left-click wheat to pick up stack
                    mc.interactionManager.clickSlot(syncId, wheatSlot, 0, SlotActionType.PICKUP, p);
                    Thread.sleep(50);

                    // Right-click slots 1, 2, 3 (top row) to place 1 wheat each
                    mc.interactionManager.clickSlot(syncId, 1, 1, SlotActionType.PICKUP, p);
                    Thread.sleep(50);
                    mc.interactionManager.clickSlot(syncId, 2, 1, SlotActionType.PICKUP, p);
                    Thread.sleep(50);
                    mc.interactionManager.clickSlot(syncId, 3, 1, SlotActionType.PICKUP, p);
                    Thread.sleep(50);

                    // Left-click wheat slot to put remaining back
                    mc.interactionManager.clickSlot(syncId, wheatSlot, 0, SlotActionType.PICKUP, p);
                    Thread.sleep(50);

                    // Shift-click output slot (slot 0) to collect bread
                    mc.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, p);
                    Thread.sleep(100);

                    crafted++;
                }

                ObjectNode n = M.createObjectNode();
                n.put("crafted", crafted);
                return n;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("interrupted", e);
            }
        }));

        // Close crafting screen (ensures cursor is empty first)
        r.register("craft.close", params -> ClientThread.call(1_000, () -> {
            var mc = MinecraftClient.getInstance();
            var p = mc.player;
            if (p == null) {
                throw new IllegalStateException("no_player");
            }

            try {
                if (mc.currentScreen instanceof CraftingScreen screen) {
                    // Clear cursor by clicking empty slot
                    int syncId = screen.getScreenHandler().syncId;
                    for (int slot = 10; slot < screen.getScreenHandler().slots.size(); slot++) {
                        if (screen.getScreenHandler().slots.get(slot).getStack().isEmpty()) {
                            mc.interactionManager.clickSlot(syncId, slot, 0, SlotActionType.PICKUP, p);
                            Thread.sleep(50);
                            break;
                        }
                    }
                }

                p.closeHandledScreen();

                ObjectNode n = M.createObjectNode();
                n.put("closed", true);
                return n;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("interrupted", e);
            }
        }));
    }
}
