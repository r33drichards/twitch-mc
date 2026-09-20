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
import net.minecraft.client.gui.screens.recipebook.RecipeCollection;
import net.minecraft.world.item.crafting.display.RecipeDisplayEntry;
import net.minecraft.world.item.crafting.display.SlotDisplayContext;
import net.minecraft.world.entity.player.StackedItemContents;
import net.minecraft.util.context.ContextMap;
import com.fasterxml.jackson.databind.node.ArrayNode;
import java.util.ArrayList;
import java.util.List;

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

        // Craft any unlocked recipe by its RESULT item id. Generic: the recipe
        // comes from the player's recipe book and the server fills the grid, so
        // no recipe is named anywhere in this file.
        // Params: item (required), count (default 1), use_max (default false),
        //         x/y/z (optional crafting table to open first)
        // Returns: {crafted, recipe, before, after}
        r.register("craft.item", params -> ClientThread.call(9_000, () -> {
            String item = params.get("item").asText();
            int count = Math.max(1, Math.min(params.path("count").asInt(1), 16));
            boolean useMax = params.path("use_max").asBoolean(false);
            Integer x = params.has("x") ? params.get("x").asInt() : null;
            Integer y = params.has("y") ? params.get("y").asInt() : null;
            Integer z = params.has("z") ? params.get("z").asInt() : null;
            return craftItem(item, count, useMax, x, y, z);
        }));

        // What the recipe book can currently make. Params: craftable_only (default true),
        // item (optional substring filter). Returns: {recipes: [{result, count, craftable}]}
        r.register("craft.recipes", params -> ClientThread.call(3_000, () -> {
            boolean craftableOnly = params.path("craftable_only").asBoolean(true);
            String filter = params.path("item").asText("");
            var mc = Minecraft.getInstance();
            var p = mc.player;
            if (p == null || mc.level == null) throw new IllegalStateException("no_player");

            ContextMap ctx = SlotDisplayContext.fromLevel(mc.level);
            StackedItemContents contents = new StackedItemContents();
            p.getInventory().fillStackedContents(contents);

            ObjectNode n = M.createObjectNode();
            ArrayNode arr = n.putArray("recipes");
            for (RecipeCollection c : p.getRecipeBook().getCollections()) {
                for (RecipeDisplayEntry e : c.getRecipes()) {
                    List<ItemStack> results = e.resultItems(ctx);
                    if (results.isEmpty()) continue;
                    ItemStack out = results.get(0);
                    String id = BuiltInRegistries.ITEM.getKey(out.getItem()).toString();
                    if (!filter.isEmpty() && !id.contains(filter)) continue;
                    boolean craftable = e.canCraft(contents);
                    if (craftableOnly && !craftable) continue;
                    ObjectNode o = arr.addObject();
                    o.put("result", id);
                    o.put("count", out.getCount());
                    o.put("craftable", craftable);
                }
            }
            return n;
        }));

        // Kept for callers that still say "bread"; no recipe-specific code behind it.
        r.register("craft.bread", params -> ClientThread.call(9_000, () ->
                craftItem("minecraft:bread", Math.max(1, params.path("count").asInt(1)),
                        false, null, null, null)));

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

    /** How many of {@code itemId} the player is carrying. */
    private static int countItem(net.minecraft.client.player.LocalPlayer p, String itemId) {
        int n = 0;
        for (ItemStack st : p.getInventory()) {
            if (st.isEmpty()) continue;
            if (BuiltInRegistries.ITEM.getKey(st.getItem()).toString().equals(itemId)) n += st.getCount();
        }
        return n;
    }

    /**
     * Craft by result item, using the recipe book rather than hand-placed ingredients.
     *
     * <p>The client asks the server to place a known recipe into the open crafting
     * menu ({@code handlePlaceRecipe}), then shift-clicks the result slot. The server
     * owns ingredient selection, so this works for every unlocked recipe — 2x2 in the
     * player's own inventory, 3x3 at a table — and names none of them.
     *
     * <p>{@code useMax} fills the grid as full as the inventory allows, so one call can
     * yield a whole stack. Prefer that over a large {@code count}: each iteration sleeps
     * on the client thread to respect the server's container-op pacing, and those sleeps
     * stutter rendering.
     *
     * @return {crafted, recipe, before, after}; {@code crafted} is measured from the
     *         inventory, not assumed from the number of attempts
     */
    static ObjectNode craftItem(String itemId, int count, boolean useMax,
                                Integer tx, Integer ty, Integer tz) {
        var mc = Minecraft.getInstance();
        var p = mc.player;
        if (p == null || mc.gameMode == null || mc.level == null) {
            throw new IllegalStateException("no_player");
        }
        try {
            if (tx != null && ty != null && tz != null) {
                BlockPos pos = new BlockPos(tx, ty, tz);
                BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false);
                mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit);
                Thread.sleep(300);
            }

            ContextMap ctx = SlotDisplayContext.fromLevel(mc.level);
            StackedItemContents contents = new StackedItemContents();
            p.getInventory().fillStackedContents(contents);

            // Prefer a recipe the inventory can actually satisfy; fall back to any
            // recipe with the right result so the caller gets a useful error.
            RecipeDisplayEntry chosen = null;
            List<RecipeDisplayEntry> sameResult = new ArrayList<>();
            outer:
            for (RecipeCollection c : p.getRecipeBook().getCollections()) {
                for (RecipeDisplayEntry e : c.getRecipes()) {
                    List<ItemStack> results = e.resultItems(ctx);
                    if (results.isEmpty()) continue;
                    if (!BuiltInRegistries.ITEM.getKey(results.get(0).getItem()).toString().equals(itemId)) continue;
                    sameResult.add(e);
                    if (e.canCraft(contents)) { chosen = e; break outer; }
                }
            }
            if (chosen == null) {
                throw new IllegalStateException(sameResult.isEmpty()
                        ? "no_unlocked_recipe_for:" + itemId
                        : "missing_ingredients_for:" + itemId);
            }

            int before = countItem(p, itemId);
            int syncId = p.containerMenu.containerId;
            for (int i = 0; i < count; i++) {
                mc.gameMode.handlePlaceRecipe(syncId, chosen.id(), useMax);
                Thread.sleep(250);
                // Slot 0 is the result slot in both the 2x2 inventory menu and the
                // 3x3 crafting menu.
                mc.gameMode.handleContainerInput(syncId, 0, 0, ContainerInput.QUICK_MOVE, p);
                Thread.sleep(250);
            }
            int after = countItem(p, itemId);

            ObjectNode n = M.createObjectNode();
            n.put("crafted", after - before);
            n.put("recipe", String.valueOf(chosen.id()));
            n.put("before", before);
            n.put("after", after);
            return n;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted", e);
        }
    }
}
