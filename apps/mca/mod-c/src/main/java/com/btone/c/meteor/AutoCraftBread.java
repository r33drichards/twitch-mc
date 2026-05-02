package com.btone.c.meteor;

import meteordevelopment.meteorclient.settings.IntSetting;
import meteordevelopment.meteorclient.settings.Setting;
import meteordevelopment.meteorclient.settings.SettingGroup;
import meteordevelopment.meteorclient.systems.modules.Categories;
import meteordevelopment.meteorclient.systems.modules.Module;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.CraftingScreen;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.Direction;

/**
 * AutoCraftBread — automatically crafts bread from wheat when enabled.
 * Scans for nearby crafting tables, opens them, and performs the crafting.
 */
public class AutoCraftBread extends Module {
    private final SettingGroup sgGeneral = settings.getDefaultGroup();

    private final Setting<Integer> intervalTicks = sgGeneral.add(new IntSetting.Builder()
        .name("interval-ticks")
        .description("Run the crafting check every N ticks (20 ticks = 1 second).")
        .defaultValue(40)
        .range(1, 200)
        .sliderRange(1, 200)
        .build());

    private final Setting<Integer> maxCrafts = sgGeneral.add(new IntSetting.Builder()
        .name("max-crafts-per-run")
        .description("Maximum number of bread to craft in one cycle.")
        .defaultValue(10)
        .range(1, 64)
        .sliderRange(1, 64)
        .build());

    private boolean tickRegistered = false;
    private int tickCounter = 0;
    private boolean isCrafting = false;

    public AutoCraftBread() {
        super(Categories.Player, "auto-craft-bread",
            "Automatically crafts bread from wheat using nearby crafting tables.");
    }

    @Override
    public void onActivate() {
        tickCounter = 0;
        isCrafting = false;
        ensureTickRegistered();
    }

    private void ensureTickRegistered() {
        if (tickRegistered) return;
        ClientTickEvents.END_CLIENT_TICK.register(this::tick);
        tickRegistered = true;
    }

    private void tick(MinecraftClient client) {
        if (!isActive()) return;
        if (isCrafting) return; // Don't interrupt ongoing craft
        if (++tickCounter < intervalTicks.get()) return;
        tickCounter = 0;
        runCraftCheck();
    }

    private void runCraftCheck() {
        MinecraftClient mc = MinecraftClient.getInstance();
        ClientPlayerEntity p = mc.player;
        if (p == null || mc.world == null || mc.interactionManager == null) return;

        // If crafting screen is already open, try to craft
        if (mc.currentScreen instanceof CraftingScreen) {
            craftBread();
            return;
        }

        // Check if we have enough wheat (at least 3)
        int wheatCount = countWheat(p.getInventory());
        if (wheatCount < 3) {
            return;
        }

        // Find nearby crafting table
        BlockPos tablePos = findNearbyCraftingTable(mc, p);
        if (tablePos == null) {
            return;
        }

        // Check if we're within interaction range (4 blocks)
        Vec3d playerPos = p.getPos();
        double distance = Math.sqrt(
            Math.pow(playerPos.x - tablePos.getX() - 0.5, 2) +
            Math.pow(playerPos.y - tablePos.getY(), 2) +
            Math.pow(playerPos.z - tablePos.getZ() - 0.5, 2)
        );

        if (distance <= 4.5) {
            // Close enough, try to open it
            openCraftingTable(mc, p, tablePos);
        }
    }

    private int countWheat(PlayerInventory inv) {
        int count = 0;
        for (int i = 0; i < inv.size(); i++) {
            ItemStack stack = inv.getStack(i);
            if (!stack.isEmpty()) {
                Identifier id = Registries.ITEM.getId(stack.getItem());
                if (id != null && id.toString().equals("minecraft:wheat")) {
                    count += stack.getCount();
                }
            }
        }
        return count;
    }

    private BlockPos findNearbyCraftingTable(MinecraftClient mc, ClientPlayerEntity p) {
        Vec3d playerPos = p.getPos();
        int radius = 5;

        for (int x = -radius; x <= radius; x++) {
            for (int y = -radius; y <= radius; y++) {
                for (int z = -radius; z <= radius; z++) {
                    BlockPos pos = new BlockPos(
                        (int) playerPos.x + x,
                        (int) playerPos.y + y,
                        (int) playerPos.z + z
                    );

                    if (mc.world != null) {
                        Identifier blockId = Registries.BLOCK.getId(mc.world.getBlockState(pos).getBlock());
                        if (blockId != null && blockId.toString().equals("minecraft:crafting_table")) {
                            return pos;
                        }
                    }
                }
            }
        }
        return null;
    }

    private void openCraftingTable(MinecraftClient mc, ClientPlayerEntity p, BlockPos pos) {
        try {
            BlockHitResult hit = new BlockHitResult(
                Vec3d.ofCenter(pos),
                Direction.UP,
                pos,
                false
            );
            mc.interactionManager.interactBlock(p, Hand.MAIN_HAND, hit);
            info("Opened crafting table at %s", pos.toString());
        } catch (Throwable t) {
            warning("Failed to open crafting table: %s", t.getMessage());
        }
    }

    private void craftBread() {
        MinecraftClient mc = MinecraftClient.getInstance();
        ClientPlayerEntity p = mc.player;
        if (p == null || mc.interactionManager == null) return;
        if (!(mc.currentScreen instanceof CraftingScreen)) return;

        isCrafting = true;

        try {
            CraftingScreen screen = (CraftingScreen) mc.currentScreen;
            int syncId = screen.getScreenHandler().syncId;

            int maxCraft = maxCrafts.get();
            int crafted = 0;

            for (int i = 0; i < maxCraft; i++) {
                // Find wheat in inventory (skip craft grid slots 1-9)
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

                // Right-click slots 1, 2, 3 (top row of 3x3 grid) to place 1 wheat each
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

            if (crafted > 0) {
                info("Crafted %d bread", crafted);
            }

            // Ensure cursor is empty before closing by clicking empty slot
            // This prevents dropping items when the screen closes
            for (int slot = 10; slot < screen.getScreenHandler().slots.size(); slot++) {
                if (screen.getScreenHandler().slots.get(slot).getStack().isEmpty()) {
                    mc.interactionManager.clickSlot(syncId, slot, 0, SlotActionType.PICKUP, p);
                    break;
                }
            }

            Thread.sleep(100);
            // Close the crafting screen
            p.closeHandledScreen();

        } catch (Throwable t) {
            warning("Crafting failed: %s", t.getMessage());
        } finally {
            isCrafting = false;
        }
    }
}
