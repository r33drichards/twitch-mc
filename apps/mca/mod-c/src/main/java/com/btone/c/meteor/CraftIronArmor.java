package com.btone.c.meteor;

import baritone.api.BaritoneAPI;
import baritone.api.pathing.goals.GoalNear;
import meteordevelopment.meteorclient.settings.*;
import meteordevelopment.meteorclient.systems.modules.Categories;
import meteordevelopment.meteorclient.systems.modules.Module;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.CraftingScreen;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;

public class CraftIronArmor extends Module {
    private final SettingGroup sgGeneral = settings.getDefaultGroup();

    private final Setting<Integer> searchRadius = sgGeneral.add(new IntSetting.Builder()
        .name("search-radius")
        .description("How far to search for a crafting table.")
        .defaultValue(30)
        .range(5, 100)
        .sliderRange(5, 100)
        .build());

    private boolean tickRegistered = false;
    private State state = State.IDLE;
    private BlockPos targetTable = null;
    private int ticksInState = 0;
    private int pieceCrafted = 0; // 0=helmet, 1=chestplate, 2=leggings, 3=boots

    private enum State {
        IDLE,
        SEARCHING_TABLE,
        NAVIGATING_TO_TABLE,
        OPENING_TABLE,
        CRAFTING_ARMOR,
        DONE
    }

    public CraftIronArmor() {
        super(Categories.Player, "craft-iron-armor",
            "Crafts a full iron armor kit (helmet, chestplate, leggings, boots) from iron_ingots. " +
            "Requires 24 iron_ingots in inventory. Auto-equips with Meteor's auto-armor.");
    }

    @Override
    public void onActivate() {
        state = State.SEARCHING_TABLE;
        targetTable = null;
        ticksInState = 0;
        pieceCrafted = 0;
        ensureTickRegistered();
        info("Starting iron armor crafting...");
    }

    @Override
    public void onDeactivate() {
        BaritoneAPI.getProvider().getPrimaryBaritone().getPathingBehavior().cancelEverything();
        MinecraftClient mc = MinecraftClient.getInstance();
        if (mc.player != null && mc.currentScreen instanceof CraftingScreen) {
            mc.player.closeHandledScreen();
        }
    }

    private void ensureTickRegistered() {
        if (tickRegistered) return;
        ClientTickEvents.END_CLIENT_TICK.register(this::tick);
        tickRegistered = true;
    }

    private void tick(MinecraftClient client) {
        if (!isActive()) return;
        ticksInState++;

        switch (state) {
            case SEARCHING_TABLE -> searchForTable();
            case NAVIGATING_TO_TABLE -> navigateToTable();
            case OPENING_TABLE -> openTable();
            case CRAFTING_ARMOR -> craftArmor();
            case DONE -> finishUp();
        }
    }

    private void searchForTable() {
        MinecraftClient mc = MinecraftClient.getInstance();
        var p = mc.player;
        if (p == null || mc.world == null) return;

        // Check if player has enough iron ingots
        int ingotCount = countItem("minecraft:iron_ingot");
        if (ingotCount < 24) {
            error("Not enough iron ingots! Need 24, have %d", ingotCount);
            toggle();
            return;
        }

        Vec3d playerPos = p.getPos();
        int radius = searchRadius.get();
        BlockPos closest = null;
        double closestDist = Double.MAX_VALUE;

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
                        double dist = pos.getSquaredDistance(playerPos);
                        if (dist < closestDist) {
                            closestDist = dist;
                            closest = pos;
                        }
                    }
                }
            }
        }

        if (closest == null) {
            error("No crafting table found within %d blocks!", radius);
            toggle();
            return;
        }

        targetTable = closest;
        info("Found crafting table at (%d, %d, %d)", targetTable.getX(), targetTable.getY(), targetTable.getZ());
        state = State.NAVIGATING_TO_TABLE;
        ticksInState = 0;
    }

    private void navigateToTable() {
        MinecraftClient mc = MinecraftClient.getInstance();
        var p = mc.player;
        if (p == null || targetTable == null) return;

        // Check if we're close enough (within 4 blocks)
        if (p.getPos().squaredDistanceTo(Vec3d.ofCenter(targetTable)) <= 16) {
            info("Reached crafting table");
            BaritoneAPI.getProvider().getPrimaryBaritone().getPathingBehavior().cancelEverything();
            state = State.OPENING_TABLE;
            ticksInState = 0;
            return;
        }

        // Start baritone navigation if not already running
        if (ticksInState == 1) {
            BaritoneAPI.getProvider().getPrimaryBaritone()
                .getCustomGoalProcess()
                .setGoalAndPath(new GoalNear(targetTable, 2));
        }

        // Timeout after 10 seconds
        if (ticksInState > 200) {
            error("Navigation timeout!");
            toggle();
        }
    }

    private void openTable() {
        MinecraftClient mc = MinecraftClient.getInstance();
        var p = mc.player;
        if (p == null || mc.interactionManager == null || targetTable == null) return;

        // Check if table is already open
        if (mc.currentScreen instanceof CraftingScreen) {
            info("Crafting table opened");
            state = State.CRAFTING_ARMOR;
            ticksInState = 0;
            return;
        }

        // Try opening the table
        if (ticksInState % 10 == 5) {
            mc.interactionManager.interactBlock(
                p,
                net.minecraft.util.Hand.MAIN_HAND,
                new net.minecraft.util.hit.BlockHitResult(
                    Vec3d.ofCenter(targetTable),
                    net.minecraft.util.math.Direction.UP,
                    targetTable,
                    false
                )
            );
        }

        // Timeout after 5 seconds
        if (ticksInState > 100) {
            error("Failed to open crafting table!");
            toggle();
        }
    }

    private void craftArmor() {
        MinecraftClient mc = MinecraftClient.getInstance();
        var p = mc.player;
        if (p == null || mc.interactionManager == null) return;

        if (!(mc.currentScreen instanceof CraftingScreen screen)) {
            error("Crafting table closed unexpectedly!");
            toggle();
            return;
        }

        // Wait a few ticks between crafts
        if (ticksInState < 10) return;

        try {
            int syncId = screen.getScreenHandler().syncId;

            switch (pieceCrafted) {
                case 0 -> {
                    info("Crafting helmet (5 ingots)...");
                    craftHelmet(syncId, p);
                    pieceCrafted++;
                    ticksInState = 0;
                }
                case 1 -> {
                    info("Crafting chestplate (8 ingots)...");
                    craftChestplate(syncId, p);
                    pieceCrafted++;
                    ticksInState = 0;
                }
                case 2 -> {
                    info("Crafting leggings (7 ingots)...");
                    craftLeggings(syncId, p);
                    pieceCrafted++;
                    ticksInState = 0;
                }
                case 3 -> {
                    info("Crafting boots (4 ingots)...");
                    craftBoots(syncId, p);
                    pieceCrafted++;
                    ticksInState = 0;
                }
                default -> {
                    info("All armor pieces crafted!");
                    p.closeHandledScreen();
                    state = State.DONE;
                    ticksInState = 0;
                }
            }
        } catch (Exception e) {
            error("Error crafting: %s", e.getMessage());
            toggle();
        }
    }

    private void craftHelmet(int syncId, net.minecraft.entity.player.PlayerEntity p) throws InterruptedException {
        MinecraftClient mc = MinecraftClient.getInstance();
        // Helmet pattern: slots 1,2,3 (top row), slots 4,6 (middle row sides)
        // Find iron ingots in inventory (slots 10+)
        Integer ingotSlot = findItemSlot("minecraft:iron_ingot", 10);
        if (ingotSlot == null) {
            throw new IllegalStateException("No iron ingots found!");
        }

        // Pick up ingot stack
        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);

        // Place in pattern
        for (int slot : new int[]{1, 2, 3, 4, 6}) {
            mc.interactionManager.clickSlot(syncId, slot, 1, SlotActionType.PICKUP, p);
            Thread.sleep(50);
        }

        // Put remaining back
        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);

        // Shift-click output
        mc.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, p);
        Thread.sleep(100);
    }

    private void craftChestplate(int syncId, net.minecraft.entity.player.PlayerEntity p) throws InterruptedException {
        MinecraftClient mc = MinecraftClient.getInstance();
        // Chestplate: slots 1,3 (top row sides), 4,5,6 (middle row), 7,8,9 (bottom row)
        Integer ingotSlot = findItemSlot("minecraft:iron_ingot", 10);
        if (ingotSlot == null) throw new IllegalStateException("No iron ingots!");

        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);

        for (int slot : new int[]{1, 3, 4, 5, 6, 7, 8, 9}) {
            mc.interactionManager.clickSlot(syncId, slot, 1, SlotActionType.PICKUP, p);
            Thread.sleep(50);
        }

        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);
        mc.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, p);
        Thread.sleep(100);
    }

    private void craftLeggings(int syncId, net.minecraft.entity.player.PlayerEntity p) throws InterruptedException {
        MinecraftClient mc = MinecraftClient.getInstance();
        // Leggings: slots 1,2,3 (top row), 4,6 (middle row sides), 7,9 (bottom row sides)
        Integer ingotSlot = findItemSlot("minecraft:iron_ingot", 10);
        if (ingotSlot == null) throw new IllegalStateException("No iron ingots!");

        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);

        for (int slot : new int[]{1, 2, 3, 4, 6, 7, 9}) {
            mc.interactionManager.clickSlot(syncId, slot, 1, SlotActionType.PICKUP, p);
            Thread.sleep(50);
        }

        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);
        mc.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, p);
        Thread.sleep(100);
    }

    private void craftBoots(int syncId, net.minecraft.entity.player.PlayerEntity p) throws InterruptedException {
        MinecraftClient mc = MinecraftClient.getInstance();
        // Boots: slots 4,6 (middle row sides), 7,9 (bottom row sides)
        Integer ingotSlot = findItemSlot("minecraft:iron_ingot", 10);
        if (ingotSlot == null) throw new IllegalStateException("No iron ingots!");

        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);

        for (int slot : new int[]{4, 6, 7, 9}) {
            mc.interactionManager.clickSlot(syncId, slot, 1, SlotActionType.PICKUP, p);
            Thread.sleep(50);
        }

        mc.interactionManager.clickSlot(syncId, ingotSlot, 0, SlotActionType.PICKUP, p);
        Thread.sleep(50);
        mc.interactionManager.clickSlot(syncId, 0, 0, SlotActionType.QUICK_MOVE, p);
        Thread.sleep(100);
    }

    private void finishUp() {
        info("Iron armor crafting complete! Enable auto-armor to equip.");
        toggle();
    }

    private int countItem(String itemId) {
        MinecraftClient mc = MinecraftClient.getInstance();
        if (mc.player == null) return 0;

        PlayerInventory inv = mc.player.getInventory();
        int count = 0;
        for (int i = 0; i < inv.size(); i++) {
            ItemStack stack = inv.getStack(i);
            if (!stack.isEmpty()) {
                Identifier id = Registries.ITEM.getId(stack.getItem());
                if (id != null && id.toString().equals(itemId)) {
                    count += stack.getCount();
                }
            }
        }
        return count;
    }

    private Integer findItemSlot(String itemId, int startSlot) {
        MinecraftClient mc = MinecraftClient.getInstance();
        if (mc.player == null || !(mc.currentScreen instanceof CraftingScreen screen)) return null;

        var handler = screen.getScreenHandler();
        for (int i = startSlot; i < handler.slots.size(); i++) {
            ItemStack stack = handler.slots.get(i).getStack();
            if (!stack.isEmpty()) {
                Identifier id = Registries.ITEM.getId(stack.getItem());
                if (id != null && id.toString().equals(itemId)) {
                    return i;
                }
            }
        }
        return null;
    }
}
