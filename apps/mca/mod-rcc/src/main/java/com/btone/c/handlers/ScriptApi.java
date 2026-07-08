package com.btone.c.handlers;

import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.registry.Registries;
import net.minecraft.util.ActionResult;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;

/**
 * Curated, remap-safe helper API bound into every {@code debug.eval} script as
 * the Lua global {@code api}.
 *
 * <p><b>Why this class exists — the name problem.</b> When a script calls a
 * method on a raw Minecraft object via Luaj's reflective luajava bridge (e.g.
 * {@code player:getHealth()}), Luaj looks the method up <em>by the literal
 * name</em> at runtime. In a production (non-dev) Fabric client, Minecraft is
 * running under <b>intermediary</b> names: {@code ClientPlayerEntity.getHealth()}
 * only exists as {@code method_6032}. So {@code player:getHealth()} resolves in
 * the Loom dev workspace but throws {@code method_6032 is not a member} against
 * the deployed jar. See the module doc on {@link EvalHandlers}.
 *
 * <p>The fix: every method here is a real Java method <i>compiled into this
 * mod's own jar</i>. Loom remaps the yarn calls inside these bodies
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

    private final MinecraftClient mc;

    ScriptApi(MinecraftClient mc) {
        this.mc = mc;
    }

    private net.minecraft.client.network.ClientPlayerEntity p() {
        if (mc.player == null) throw new IllegalStateException("no_player");
        return mc.player;
    }

    // ---- reads ----

    public boolean inWorld() {
        return mc.player != null && mc.world != null;
    }

    public double health() {
        return p().getHealth();
    }

    public int food() {
        return p().getHungerManager().getFoodLevel();
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
        return p().getYaw();
    }

    public float pitch() {
        return p().getPitch();
    }

    public String dim() {
        return mc.world == null ? null : mc.world.getRegistryKey().getValue().toString();
    }

    public String name() {
        return p().getName().getString();
    }

    public int hotbarSlot() {
        return p().getInventory().selectedSlot;
    }

    public String heldItem() {
        return Registries.ITEM.getId(p().getMainHandStack().getItem()).toString();
    }

    /** Block id at world coords (e.g. "minecraft:stone"), or null if unloaded. */
    public String blockAt(int bx, int by, int bz) {
        if (mc.world == null) return null;
        BlockState s = mc.world.getBlockState(new BlockPos(bx, by, bz));
        return Registries.BLOCK.getId(s.getBlock()).toString();
    }

    // ---- writes / actions ----

    public void setYaw(double v) {
        var pl = p();
        pl.setYaw((float) v);
        pl.setHeadYaw((float) v);
        pl.setBodyYaw((float) v);
    }

    public void setPitch(double v) {
        p().setPitch((float) v);
    }

    public void selectSlot(int i) {
        if (i < 0 || i > 8) throw new IllegalArgumentException("slot must be 0-8, got: " + i);
        p().getInventory().selectedSlot = i;
    }

    /**
     * Send a chat line (or /command if it starts with '/'). Fire-and-forget on
     * the client thread — offline-mode chat-signing can block, so we do not wait.
     */
    public void chat(String text) {
        mc.execute(() -> {
            var nh = mc.getNetworkHandler();
            if (nh == null) return;
            if (text.startsWith("/")) nh.sendChatCommand(text.substring(1));
            else nh.sendChatMessage(text);
        });
    }

    // ---- action verbs (interaction manager; same plumbing as world.* handlers) ----

    /**
     * Right-click / use the held main-hand item. Casts or reels a fishing rod,
     * eats food, throws a pearl, etc. Returns true if the interaction was
     * accepted by the client.
     */
    public boolean useItem() {
        ActionResult r = mc.interactionManager.interactItem(p(), Hand.MAIN_HAND);
        return r != null && r.isAccepted();
    }

    /** Swing the main hand (one attack/visual tick). */
    public void swing() {
        p().swingHand(Hand.MAIN_HAND);
    }

    /**
     * Single attack tick against a block, auto-choosing the face toward the
     * player. Call repeatedly (across ticks) to break it; one call just chips.
     */
    public boolean attackBlock(int bx, int by, int bz) {
        BlockPos pos = new BlockPos(bx, by, bz);
        boolean ok = mc.interactionManager.attackBlock(pos, faceToward(pos));
        p().swingHand(Hand.MAIN_HAND);
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
        BlockHitResult hit = new BlockHitResult(Vec3d.ofCenter(pos), dir, pos, false);
        ActionResult r = mc.interactionManager.interactBlock(p(), Hand.MAIN_HAND, hit);
        return r != null && r.isAccepted();
    }

    /** True while a fishing bobber is deployed (the line is cast). */
    public boolean bobberOut() {
        return p().fishHook != null;
    }

    private Direction faceToward(BlockPos pos) {
        Vec3d eye = p().getEyePos();
        Vec3d c = Vec3d.ofCenter(pos);
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
