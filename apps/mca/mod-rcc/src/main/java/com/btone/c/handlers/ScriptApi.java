package com.btone.c.handlers;

import com.fasterxml.jackson.databind.ObjectMapper;
import it.unimi.dsi.fastutil.objects.Object2IntMap;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.enchantment.Enchantment;
import net.minecraft.world.item.enchantment.ItemEnchantments;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.Vec3;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Curated, remap-safe helper API bound into every {@code debug.eval} script as
 * the Lua global {@code api}.
 *
 * <p><b>Why this class exists — the name problem.</b> When a script calls a
 * method on a raw Minecraft object via Luaj's reflective luajava bridge (e.g.
 * {@code player:getHealth()}), Luaj looks the method up <em>by the literal
 * name</em> at runtime. In a production (non-dev) Fabric client, Minecraft is
 * running under <b>intermediary</b> names: {@code LocalPlayer.getHealth()}
 * only exists as {@code method_6032}. So {@code player:getHealth()} resolves in
 * the Loom dev workspace but throws {@code method_6032 is not a member} against
 * the deployed jar. See the module doc on {@link EvalHandlers}.
 *
 * <p>The fix: every method here is a real Java method <i>compiled into this
 * mod's own jar</i>. Loom remaps the mojmap calls inside these bodies
 * ({@code getHealth()} → {@code method_6032}) at build time, so the reference is
 * baked in. The method names on <i>this</i> class ({@code health}, {@code pos},
 * {@code blockAt}, …) are our own identifiers and are <b>never remapped</b> —
 * they are the same string at compile time and at runtime. Therefore
 * {@code api:health()} from a script works identically in dev and in production.
 *
 * <p>All methods touch the client and MUST be called on the client thread;
 * {@link EvalHandlers} guarantees that by running the whole script there.
 */
public final class ScriptApi {

    private final Minecraft mc;

    ScriptApi(Minecraft mc) {
        this.mc = mc;
    }

    private net.minecraft.client.player.LocalPlayer p() {
        if (mc.player == null) throw new IllegalStateException("no_player");
        return mc.player;
    }

    // ---- reads ----

    public boolean inWorld() {
        return mc.player != null && mc.level != null;
    }

    public double health() {
        return p().getHealth();
    }

    public int food() {
        return p().getFoodData().getFoodLevel();
    }

    public double x() {
        return p().getX();
    }

    public double y() {
        return p().getY();
    }

    public double z() {
        return p().getZ();
    }

    public int blockX() {
        return p().getBlockX();
    }

    public int blockY() {
        return p().getBlockY();
    }

    public int blockZ() {
        return p().getBlockZ();
    }

    public float yaw() {
        return p().getYRot();
    }

    public float pitch() {
        return p().getXRot();
    }

    public String dim() {
        return mc.level == null ? null : mc.level.dimension().identifier().toString();
    }

    public String name() {
        return p().getName().getString();
    }

    public int hotbarSlot() {
        return p().getInventory().getSelectedSlot();
    }

    public String heldItem() {
        return BuiltInRegistries.ITEM.getKey(p().getMainHandItem().getItem()).toString();
    }

    /** Block id at world coords (e.g. "minecraft:stone"), or null if unloaded. */
    public String blockAt(int bx, int by, int bz) {
        if (mc.level == null) return null;
        BlockState s = mc.level.getBlockState(new BlockPos(bx, by, bz));
        return BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString();
    }

    // ---- writes / actions ----

    public void setYaw(double v) {
        var pl = p();
        pl.setYRot((float) v);
        pl.setYHeadRot((float) v);
        pl.setYBodyRot((float) v);
    }

    public void setPitch(double v) {
        p().setXRot((float) v);
    }

    public void selectSlot(int i) {
        if (i < 0 || i > 8) throw new IllegalArgumentException("slot must be 0-8, got: " + i);
        p().getInventory().setSelectedSlot(i);
    }

    /**
     * Send a chat line (or /command if it starts with '/'). Fire-and-forget on
     * the client thread — offline-mode chat-signing can block, so we do not wait.
     */
    public void chat(String text) {
        mc.execute(() -> {
            var nh = mc.getConnection();
            if (nh == null) return;
            if (text.startsWith("/")) nh.sendCommand(text.substring(1));
            else nh.sendChat(text);
        });
    }

    // ---- action verbs (interaction manager; same plumbing as world.* handlers) ----

    /**
     * Right-click / use the held main-hand item. Casts or reels a fishing rod,
     * eats food, throws a pearl, etc. Returns true if the interaction was
     * accepted by the client.
     */
    public boolean useItem() {
        InteractionResult r = mc.gameMode.useItem(p(), InteractionHand.MAIN_HAND);
        return r != null && r.consumesAction();
    }

    /** Swing the main hand (one attack/visual tick). */
    public void swing() {
        p().swing(InteractionHand.MAIN_HAND);
    }

    /**
     * Single attack tick against a block, auto-choosing the face toward the
     * player. Call repeatedly (across ticks) to break it; one call just chips.
     */
    public boolean attackBlock(int bx, int by, int bz) {
        BlockPos pos = new BlockPos(bx, by, bz);
        boolean ok = mc.gameMode.startDestroyBlock(pos, faceToward(pos));
        p().swing(InteractionHand.MAIN_HAND);
        return ok;
    }

    /**
     * Place / use the held item against a block face. {@code side} is
     * up/down/north/south/east/west, or null/empty to auto-pick the face toward
     * the player. Returns true if accepted.
     */
    public boolean placeBlock(int bx, int by, int bz, String side) {
        BlockPos pos = new BlockPos(bx, by, bz);
        Direction dir = parseSide(side, pos);
        BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), dir, pos, false);
        InteractionResult r = mc.gameMode.useItemOn(p(), InteractionHand.MAIN_HAND, hit);
        return r != null && r.consumesAction();
    }

    /** True while a fishing bobber is deployed (the line is cast). */
    public boolean bobberOut() {
        return p().fishing != null;
    }

    // ---- entity detection + combat (targeted KillAura support) ----

    /**
     * Nearby loaded entities within {@code radius} blocks, nearest-first, as a JSON array string:
     * [{"id":int,"type":"minecraft:zombie","hostile":bool,"living":bool,"x":d,"y":d,"z":d,"dist":d,"health":d}].
     * {@code hostile} = implements the Enemy marker (monsters). Use with attackEntity(id) for a targeted KillAura.
     */
    public String entitiesJson(double radius) {
        if (mc.level == null) return "[]";
        var self = p();
        Vec3 me = self.position();
        double r2 = radius * radius;
        List<Map<String, Object>> out = new ArrayList<>();
        for (net.minecraft.world.entity.Entity e : mc.level.entitiesForRendering()) {
            if (e == self) continue;
            double d2 = e.distanceToSqr(me);
            if (d2 > r2) continue;
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", e.getId());
            m.put("type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
            m.put("hostile", e instanceof net.minecraft.world.entity.monster.Enemy);
            boolean living = e instanceof net.minecraft.world.entity.LivingEntity;
            m.put("living", living);
            m.put("x", e.getX());
            m.put("y", e.getY());
            m.put("z", e.getZ());
            m.put("dist", Math.sqrt(d2));
            if (living) m.put("health", ((net.minecraft.world.entity.LivingEntity) e).getHealth());
            out.add(m);
        }
        out.sort((a, b) -> Double.compare((double) a.get("dist"), (double) b.get("dist")));
        try { return ITEM_JSON.writeValueAsString(out); } catch (Exception ex) { return "[]"; }
    }

    /**
     * Attack (melee) the entity with the given network id (from entitiesJson). Faces nothing —
     * pair with setYaw/setPitch aimed at the entity for reliable hits, or just call directly since
     * the client attack uses the server-side entity id. Returns true if the entity was found.
     */
    public boolean attackEntity(int entityId) {
        if (mc.level == null) return false;
        net.minecraft.world.entity.Entity e = mc.level.getEntity(entityId);
        if (e == null) return false;
        mc.gameMode.attack(p(), e);
        p().swing(InteractionHand.MAIN_HAND);
        return true;
    }

    // ---- rich item reads (enchants / durability / custom name) ----
    // Returned as JSON strings: the eval->JSON path stringifies raw Java objects,
    // so we serialize here and hand back a plain string the caller can parse.

    private static final ObjectMapper ITEM_JSON = new ObjectMapper();

    private Map<String, Object> stackMap(ItemStack st, int slot) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("slot", slot);
        m.put("id", BuiltInRegistries.ITEM.getKey(st.getItem()).toString());
        m.put("count", st.getCount());
        if (st.isDamageableItem()) {
            m.put("durability", st.getMaxDamage() - st.getDamageValue());
            m.put("maxDurability", st.getMaxDamage());
        }
        if (st.getCustomName() != null) m.put("customName", st.getHoverName().getString());
        // Enchantments are data-driven (1.21+): read the ItemEnchantments
        // component and resolve each enchant Holder's registry id. Enchantments
        // live in a dynamic registry, so the id comes from the Holder's
        // ResourceKey rather than a static BuiltInRegistries lookup.
        ItemEnchantments ench = st.getEnchantments();
        if (!ench.isEmpty()) {
            Map<String, Integer> e = new LinkedHashMap<>();
            for (Object2IntMap.Entry<Holder<Enchantment>> en : ench.entrySet()) {
                Holder<Enchantment> holder = en.getKey();
                String id = holder.unwrapKey()
                        .map(k -> k.identifier().toString())
                        .orElseGet(() -> holder.value().toString());
                e.put(id, en.getIntValue());
            }
            m.put("enchants", e);
        }
        return m;
    }

    /** Player main inventory (slots 0-35), non-empty stacks with full detail, as a JSON array string. */
    public String inventoryJson() {
        List<Map<String, Object>> out = new ArrayList<>();
        var main = p().getInventory().getNonEquipmentItems();
        for (int i = 0; i < main.size(); i++) {
            ItemStack st = main.get(i);
            if (!st.isEmpty()) out.add(stackMap(st, i));
        }
        try { return ITEM_JSON.writeValueAsString(out); } catch (Exception e) { return "[]"; }
    }

    /** Currently-open container's own slots (not player inventory), with full detail, as a JSON array string. */
    public String containerJson() {
        if (!(mc.gui.screen() instanceof AbstractContainerScreen<?> hs)) return "[]";
        var handler = hs.getMenu();
        int playerInvStart = handler.slots.size() - 36;
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < playerInvStart; i++) {
            ItemStack st = handler.slots.get(i).getItem();
            if (!st.isEmpty()) out.add(stackMap(st, i));
        }
        try { return ITEM_JSON.writeValueAsString(out); } catch (Exception e) { return "[]"; }
    }

    private Direction faceToward(BlockPos pos) {
        Vec3 eye = p().getEyePosition();
        Vec3 c = Vec3.atCenterOf(pos);
        double dx = c.x - eye.x, dy = c.y - eye.y, dz = c.z - eye.z;
        double ax = Math.abs(dx), ay = Math.abs(dy), az = Math.abs(dz);
        if (ax > ay && ax > az) return dx > 0 ? Direction.WEST : Direction.EAST;
        if (ay > az) return dy > 0 ? Direction.DOWN : Direction.UP;
        return dz > 0 ? Direction.NORTH : Direction.SOUTH;
    }

    private Direction parseSide(String side, BlockPos pos) {
        if (side != null && !side.isEmpty()) {
            for (Direction d : Direction.values()) {
                if (d.getName().equalsIgnoreCase(side)) return d;
            }
        }
        return faceToward(pos);
    }
}
