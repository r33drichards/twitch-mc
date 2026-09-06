package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.CraftingScreen;
import net.minecraft.world.item.ItemStack;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.InteractionHand;
import net.minecraft.resources.Identifier;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.Vec3;

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
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.level == null) {
                throw new IllegalStateException("no_player");
            }

            Vec3 playerPos = p.position();
            for (int x = -radius; x <= radius; x++) {
                for (int y = -radius; y <= radius; y++) {
                    for (int z = -radius; z <= radius; z++) {
                        BlockPos pos = new BlockPos(
                            (int) playerPos.x + x,
                            (int) playerPos.y + y,
                            (int) playerPos.z + z
                        );
                        Identifier blockId = BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(pos).getBlock());
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

            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.gameMode == null) {
                throw new IllegalStateException("no_player");
            }

            try {
                BlockPos pos = new BlockPos(x, y, z);
                BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false);
                mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit);
                Thread.sleep(200);

                ObjectNode n = M.createObjectNode();
                n.put("opened", mc.gui.screen() instanceof CraftingScreen);
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

            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.gameMode == null) {
                throw new IllegalStateException("no_player");
            }

            if (!(mc.gui.screen() instanceof CraftingScreen screen)) {
                throw new IllegalStateException("crafting_screen_not_open");
            }

            try {
                int syncId = screen.getMenu().containerId;
                int crafted = 0;

                for (int i = 0; i < count; i++) {
                    // Find wheat in inventory (skip craft grid slots 0-9)
                    Integer wheatSlot = null;
                    for (int slot = 10; slot < screen.getMenu().slots.size(); slot++) {
                        ItemStack stack = screen.getMenu().slots.get(slot).getItem();
                        if (!stack.isEmpty()) {
                            Identifier id = BuiltInRegistries.ITEM.getKey(stack.getItem());
                            if (id != null && id.toString().equals("minecraft:wheat") && stack.getCount() >= 3) {
                                wheatSlot = slot;
                                break;
                            }
                        }
                    }

                    if (wheatSlot == null) break;

                    // Left-click wheat to pick up stack
                    mc.gameMode.handleContainerInput(syncId, wheatSlot, 0, ContainerInput.PICKUP, p);
                    Thread.sleep(50);

                    // Right-click slots 1, 2, 3 (top row) to place 1 wheat each
                    mc.gameMode.handleContainerInput(syncId, 1, 1, ContainerInput.PICKUP, p);
                    Thread.sleep(50);
                    mc.gameMode.handleContainerInput(syncId, 2, 1, ContainerInput.PICKUP, p);
                    Thread.sleep(50);
                    mc.gameMode.handleContainerInput(syncId, 3, 1, ContainerInput.PICKUP, p);
                    Thread.sleep(50);

                    // Left-click wheat slot to put remaining back
                    mc.gameMode.handleContainerInput(syncId, wheatSlot, 0, ContainerInput.PICKUP, p);
                    Thread.sleep(50);

                    // Shift-click output slot (slot 0) to collect bread
                    mc.gameMode.handleContainerInput(syncId, 0, 0, ContainerInput.QUICK_MOVE, p);
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
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null) {
                throw new IllegalStateException("no_player");
            }

            try {
                if (mc.gui.screen() instanceof CraftingScreen screen) {
                    // Clear cursor by clicking empty slot
                    int syncId = screen.getMenu().containerId;
                    for (int slot = 10; slot < screen.getMenu().slots.size(); slot++) {
                        if (screen.getMenu().slots.get(slot).getItem().isEmpty()) {
                            mc.gameMode.handleContainerInput(syncId, slot, 0, ContainerInput.PICKUP, p);
                            Thread.sleep(50);
                            break;
                        }
                    }
                }

                p.closeContainer();

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
