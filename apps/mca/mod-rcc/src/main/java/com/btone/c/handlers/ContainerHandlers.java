package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.world.item.ItemStack;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.Vec3;

public final class ContainerHandlers {
    private static final ObjectMapper M = new ObjectMapper();

    private ContainerHandlers() {}

    public static void registerAll(RpcRouter r) {
        // Open the player's own inventory screen — no chest/block needed.
        // Required for SWAP-mode container.click against player main inventory
        // when no nearby container exists (e.g. mid-nether sword-into-hotbar fix).
        // After this returns, container.state shows the player inventory layout:
        //   slot 0: crafting output, 1-4: 2x2 craft grid, 5-8: armor slots,
        //   slot 45: offhand, slots 9-35: main inv, slots 36-44: hotbar.
        // SWAP a main-inv stack into hotbar slot K with: click slot=N, button=K, mode=SWAP.
        r.register("container.open_inventory", params -> ClientThread.call(1_000, () -> {
            var mc = Minecraft.getInstance();
            if (mc.player == null) {
                throw new IllegalStateException("no_player");
            }
            mc.setScreenAndShow(new InventoryScreen(mc.player));
            ObjectNode n = M.createObjectNode();
            n.put("opened", true);
            return n;
        }));
        r.register("container.open", params -> ClientThread.call(3_000, () -> {
            int x = params.get("x").asInt();
            int y = params.get("y").asInt();
            int z = params.get("z").asInt();
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.gameMode == null) {
                throw new IllegalStateException("no_player");
            }
            BlockPos pos = new BlockPos(x, y, z);
            BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false);
            mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit);
            // The screen opens asynchronously after the server reply; the caller
            // should poll container.state to confirm.
            ObjectNode n = M.createObjectNode();
            n.put("requested", true);
            return n;
        }));
        r.register("container.state", params -> ClientThread.call(2_000, () -> {
            var mc = Minecraft.getInstance();
            ObjectNode n = M.createObjectNode();
            if (!(mc.gui.screen() instanceof AbstractContainerScreen<?> hs)) {
                n.put("open", false);
                return n;
            }
            n.put("open", true);
            n.put("screen", hs.getClass().getSimpleName());
            var handler = hs.getMenu();
            n.put("syncId", handler.containerId);

            // Determine split point between container and player inventory
            // For most containers, first 27/54 slots are container, rest are player inventory
            int playerInvStart = handler.slots.size() - 36; // Last 36 slots are usually player inv

            var containerSlots = n.putArray("containerSlots");
            var playerSlots = n.putArray("playerSlots");

            for (int i = 0; i < handler.slots.size(); i++) {
                ItemStack s = handler.slots.get(i).getItem();
                if (s.isEmpty()) continue;

                ObjectNode o = M.createObjectNode();
                o.put("slot", i);
                o.put("id", BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
                o.put("count", s.getCount());

                // Add to appropriate array
                if (i < playerInvStart) {
                    containerSlots.add(o);
                } else {
                    playerSlots.add(o);
                }
            }
            return n;
        }));
        r.register("container.click", params -> ClientThread.call(2_000, () -> {
            int slot = params.get("slot").asInt();
            int button = params.path("button").asInt(0);
            String modeStr = params.path("mode").asText("PICKUP");
            var mc = Minecraft.getInstance();
            if (!(mc.gui.screen() instanceof AbstractContainerScreen<?> hs) || mc.player == null
                    || mc.gameMode == null) {
                throw new IllegalStateException("no_container");
            }
            ContainerInput mode;
            try { mode = ContainerInput.valueOf(modeStr); }
            catch (IllegalArgumentException iae) {
                throw new IllegalArgumentException("bad_mode:" + modeStr);
            }
            mc.gameMode.handleContainerInput(hs.getMenu().containerId, slot, button, mode, mc.player);
            ObjectNode n = M.createObjectNode();
            n.put("clicked", true);
            return n;
        }));
        r.register("container.close", params -> ClientThread.call(1_000, () -> {
            var mc = Minecraft.getInstance();
            if (mc.gui.screen() instanceof AbstractContainerScreen<?> && mc.player != null) {
                mc.player.closeContainer();
            }
            ObjectNode n = M.createObjectNode();
            n.put("closed", true);
            return n;
        }));
        r.register("container.craft", params -> ClientThread.call(5_000, () -> {
            String itemId = params.get("item").asText();
            int count = params.path("count").asInt(1);
            int tableX = params.path("tableX").asInt();
            int tableY = params.path("tableY").asInt();
            int tableZ = params.path("tableZ").asInt();
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.gameMode == null) {
                throw new IllegalStateException("no_player");
            }
            try {
                // Open crafting table if coordinates provided
                if (tableX != 0 || tableY != 0 || tableZ != 0) {
                    BlockPos pos = new BlockPos(tableX, tableY, tableZ);
                    BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false);
                    mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit);
                    Thread.sleep(300);
                }
                if (!(mc.gui.screen() instanceof AbstractContainerScreen<?> hs)) {
                    throw new IllegalStateException("no_container");
                }
                var handler = hs.getMenu();
                // For crafting table: slot 0 = output, slots 1-9 = 3x3 grid
                // Bread recipe: 3 wheat in a row (use slots 1,2,3)
                // Main inventory starts at slot 10 for crafting table
                int crafted = 0;
                if (itemId.equals("minecraft:bread")) {
                    for (int i = 0; i < count; i++) {
                        // Find wheat anywhere in the container (search all slots)
                        Integer wheatSlot = null;
                        for (int slot = 0; slot < handler.slots.size(); slot++) {
                            // Skip craft grid slots (0-9)
                            if (slot >= 1 && slot <= 9) continue;
                            ItemStack stack = handler.slots.get(slot).getItem();
                            if (!stack.isEmpty() && BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().equals("minecraft:wheat")) {
                                if (stack.getCount() >= 3) {
                                    wheatSlot = slot;
                                    break;
                                }
                            }
                        }
                        if (wheatSlot == null) break;

                        // Left-click wheat to pick up entire stack
                        mc.gameMode.handleContainerInput(handler.containerId, wheatSlot, 0, ContainerInput.PICKUP, p);
                        Thread.sleep(50);
                        // Right-click slots 1, 2, 3 to place 1 wheat each (top row)
                        mc.gameMode.handleContainerInput(handler.containerId, 1, 1, ContainerInput.PICKUP, p);
                        Thread.sleep(50);
                        mc.gameMode.handleContainerInput(handler.containerId, 2, 1, ContainerInput.PICKUP, p);
                        Thread.sleep(50);
                        mc.gameMode.handleContainerInput(handler.containerId, 3, 1, ContainerInput.PICKUP, p);
                        Thread.sleep(50);
                        // Left-click wheat slot again to put remaining wheat back
                        mc.gameMode.handleContainerInput(handler.containerId, wheatSlot, 0, ContainerInput.PICKUP, p);
                        Thread.sleep(50);
                        // Shift-click output slot to craft bread into inventory
                        mc.gameMode.handleContainerInput(handler.containerId, 0, 0, ContainerInput.QUICK_MOVE, p);
                        Thread.sleep(100);
                        crafted++;
                    }
                }
                ObjectNode n = M.createObjectNode();
                n.put("crafted", crafted);
                n.put("item", itemId);
                return n;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("interrupted", e);
            }
        }));
    }
}
