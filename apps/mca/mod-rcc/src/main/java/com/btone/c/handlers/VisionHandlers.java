package com.btone.c.handlers;

import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.platform.NativeImage;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.Camera;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import org.joml.Matrix4f;
import org.joml.Quaternionf;
import org.joml.Vector4f;

import javax.imageio.ImageIO;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Vision handlers — {@code world.screenshot} and {@code world.screenshot_panorama}.
 *
 * <h2>Threading model</h2>
 *
 * <p>Two non-obvious constraints shape this code:
 *
 * <ol>
 *   <li><b>No render-thread blocking.</b>
 *       {@link Screenshot#takeScreenshot(RenderTarget, java.util.function.Consumer)}
 *       enqueues a GPU texture→buffer copy whose completion callback also runs
 *       on the render thread. If we block on its future from the same thread,
 *       the callback can never drain. So the HTTP thread (off render thread) is
 *       the only place we await futures.</li>
 *   <li><b>State changes don't take effect until the NEXT render.</b>
 *       Setting {@code player.setYRot(...)} or toggling the HUD just mutates
 *       state; the captured render target reflects the most recently rendered
 *       frame, which used the previous state. So we have to apply the change,
 *       let MC render one frame with it, THEN call takeScreenshot.</li>
 * </ol>
 *
 * <p>Pipeline (all enqueueing happens off the render thread; everything
 * else happens on it):
 *
 * <ol>
 *   <li>HTTP thread: create {@code respFuture}, submit prep runnable via
 *       {@link Minecraft#execute(Runnable)}, await respFuture.get(5s).</li>
 *   <li>Prep runnable (render thread, runs at frame N's task drain BEFORE
 *       the frame's render): snapshot saved state, apply yaw/pitch/HUD
 *       override, derive the override view-projection matrix, compute
 *       entity/block/crosshair annotations, enqueue a {@link PendingCapture}
 *       into {@code PENDING}.</li>
 *   <li>Frame N proceeds and renders with the overridden player rotation.
 *       The main render target now holds the override scene.</li>
 *   <li>Frame N+1 starts. END_CLIENT_TICK fires before the next render. The
 *       tick handler dequeues the pending capture, calls takeScreenshot
 *       (which enqueues a copy of the still-valid frame-N color texture),
 *       restores saved state immediately, and the GPU-readback callback
 *       encodes PNG/JPEG and completes respFuture.</li>
 *   <li>HTTP thread unblocks, returns the response.</li>
 * </ol>
 *
 * <p>Panorama chains N captures sequentially via
 * {@link CompletableFuture#thenCompose}; only the final aggregated future
 * is awaited on the HTTP thread.
 *
 * <h2>26.2 notes</h2>
 *
 * <p>The 26.x render pipeline replaced {@code Framebuffer} with
 * {@link RenderTarget} (reached via {@code gameRenderer.mainRenderTarget()},
 * exactly what vanilla F2 grabs) and made the screenshot readback
 * callback-based instead of returning a {@code NativeImage} synchronously.
 * {@code Camera.update(...)} no longer accepts an explicit rotation, so
 * instead of mutating the camera to snapshot matrices we reconstruct the
 * override view matrix from {@code Camera}'s own convention
 * ({@code rotationYXZ(PI - yaw, -pitch, 0)}) and recover the pure projection
 * matrix as {@code P = (P*V) * V⁻¹} from the camera's cached matrices.
 * {@code Options.hudHidden} moved to {@code Minecraft.gui.hud} as
 * {@code isHidden()} / {@code toggle()}.
 */
public final class VisionHandlers {
    private static final ObjectMapper M = new ObjectMapper();
    private static final long TIMEOUT_MS = 5_000;
    private static final long PANORAMA_TIMEOUT_MS = 30_000;
    private static final int MAX_ENTITIES = 64;
    private static final int MAX_BLOCKS = 128;
    private static final double ENTITY_RANGE = 64.0;
    private static final float DEG_TO_RAD = 0.017453292f;

    private VisionHandlers() {}

    /** Identifiers of blocks worth annotating in the structured side-channel. */
    private static final Set<Identifier> INTERACTIVE_BLOCKS;
    static {
        Set<Identifier> s = new java.util.HashSet<>();
        String[] explicit = {
                "minecraft:chest", "minecraft:trapped_chest", "minecraft:ender_chest",
                "minecraft:barrel", "minecraft:furnace", "minecraft:blast_furnace",
                "minecraft:smoker", "minecraft:crafting_table", "minecraft:brewing_stand",
                "minecraft:anvil", "minecraft:chipped_anvil", "minecraft:damaged_anvil",
                "minecraft:beacon", "minecraft:hopper", "minecraft:dropper",
                "minecraft:dispenser", "minecraft:lectern", "minecraft:cartography_table",
                "minecraft:smithing_table", "minecraft:stonecutter", "minecraft:grindstone",
                "minecraft:loom", "minecraft:fletching_table", "minecraft:composter",
                "minecraft:cauldron", "minecraft:water_cauldron", "minecraft:lava_cauldron",
                "minecraft:powder_snow_cauldron", "minecraft:jukebox", "minecraft:note_block",
                "minecraft:repeater", "minecraft:comparator", "minecraft:daylight_detector",
                "minecraft:lever",
        };
        for (String id : explicit) s.add(Identifier.parse(id));
        // Color- and wood-suffix families — iterate the registry once at class
        // load. BuiltInRegistries.BLOCK is populated long before any dispatch.
        String[] suffixes = {"_bed", "_door", "_trapdoor", "_button", "_pressure_plate",
                "_sign", "_hanging_sign", "_wall_sign", "_wall_hanging_sign"};
        for (Identifier id : BuiltInRegistries.BLOCK.keySet()) {
            String path = id.getPath();
            for (String suf : suffixes) {
                if (path.endsWith(suf)) { s.add(id); break; }
            }
        }
        INTERACTIVE_BLOCKS = java.util.Collections.unmodifiableSet(s);
    }

    /**
     * Pending captures awaiting the next post-state-change render frame.
     *
     * <p>Static because Fabric's {@link ClientTickEvents} has no
     * {@code unregister} — we install ONE shared listener at startup that
     * drains this queue forever. Each entry counts down ticks; when ready,
     * the listener calls {@link Screenshot#takeScreenshot} for it and
     * restores the saved state.
     */
    private static final java.util.concurrent.ConcurrentLinkedQueue<PendingCapture> PENDING =
            new java.util.concurrent.ConcurrentLinkedQueue<>();
    private static volatile boolean tickHandlerInstalled = false;

    public static void registerAll(RpcRouter r) {
        installTickHandler();
        r.register("world.screenshot", params -> {
            CaptureRequest req = CaptureRequest.of(params);
            CompletableFuture<ObjectNode> fut = scheduleCapture(req);
            return await0(fut, TIMEOUT_MS, "capture_timeout");
        });
        r.register("world.screenshot_panorama", params -> {
            CaptureRequest base = CaptureRequest.of(params);
            int angles = clamp(params.path("angles").asInt(4), 1, 16);
            float[][] offsets = panoramaOffsets(angles);

            // Resolve base yaw/pitch on the render thread so each frame's
            // override is computed from a consistent snapshot. We do this via
            // a small future (no GPU work, so no deadlock risk).
            CompletableFuture<float[]> seedFut = new CompletableFuture<>();
            Minecraft.getInstance().execute(() -> {
                Player p = Minecraft.getInstance().player;
                float by = (base.yaw != null) ? base.yaw : (p != null ? p.getYRot() : 0f);
                float bp = (base.pitch != null) ? base.pitch : (p != null ? p.getXRot() : 0f);
                seedFut.complete(new float[]{by, bp});
            });
            float[] seed = await0(seedFut, 1_000, "panorama_seed_timeout");

            // Chain N single-shot captures sequentially.
            CompletableFuture<ArrayNode> chain = CompletableFuture.completedFuture(M.createArrayNode());
            for (float[] off : offsets) {
                final float yawAbs = seed[0] + off[0];
                final float pitchAbs = Float.isNaN(off[1]) ? seed[1] : off[1];
                chain = chain.thenCompose(arr -> {
                    CaptureRequest each = base.withAbsolute(yawAbs, pitchAbs);
                    return scheduleCapture(each).thenApply(frame -> {
                        // Stamp yaw/pitch onto the frame for caller convenience.
                        frame.put("yaw", yawAbs);
                        frame.put("pitch", pitchAbs);
                        arr.add(frame);
                        return arr;
                    });
                });
            }
            ArrayNode frames = await0(chain, PANORAMA_TIMEOUT_MS, "panorama_timeout");
            ObjectNode root = M.createObjectNode();
            root.set("frames", frames);
            return root;
        });
    }

    // --- Request struct -----------------------------------------------------

    private static final class CaptureRequest {
        Integer width;
        Float yaw;
        Float pitch;
        boolean includeHud;
        int annotateRange;
        String format; // "png" | "jpeg"

        static CaptureRequest of(JsonNode p) {
            CaptureRequest r = new CaptureRequest();
            if (p == null || p.isMissingNode()) p = M.createObjectNode();
            r.width = p.has("width") ? p.get("width").asInt() : null;
            r.yaw = p.has("yaw") ? (float) p.get("yaw").asDouble() : null;
            r.pitch = p.has("pitch") ? (float) p.get("pitch").asDouble() : null;
            r.includeHud = p.path("includeHud").asBoolean(false);
            r.annotateRange = clamp(p.path("annotateRange").asInt(16), 1, 64);
            r.format = p.path("format").asText("png").toLowerCase();
            if (!r.format.equals("png") && !r.format.equals("jpeg")) r.format = "png";
            return r;
        }

        CaptureRequest withAbsolute(float yawAbs, float pitchAbs) {
            CaptureRequest c = new CaptureRequest();
            c.width = this.width;
            c.includeHud = this.includeHud;
            c.annotateRange = this.annotateRange;
            c.format = this.format;
            c.yaw = yawAbs;
            c.pitch = pitchAbs;
            return c;
        }
    }

    // --- Async capture pipeline --------------------------------------------

    /**
     * Pending capture record. Built on the render thread by the prep
     * runnable, consumed by the END_CLIENT_TICK handler one tick later
     * (after MC has rendered one frame with the override applied).
     */
    private static final class PendingCapture {
        final CaptureRequest req;
        final CompletableFuture<ObjectNode> respFuture;
        // Saved state (to restore after capture).
        final float savedYaw, savedPitch, savedHeadYaw, savedBodyYaw;
        final boolean savedHud;
        final boolean override;
        // Override values (also stamped into the response.camera node).
        final float useYaw, usePitch;
        // Camera snapshot taken AFTER override applied.
        final Vec3 camPosSnapshot;
        final ArrayNode entityAnns;
        final ArrayNode blockAnns;
        final ObjectNode crossAnn;
        // Ticks remaining before the GPU readback. Decremented on each
        // END_CLIENT_TICK; capture fires when this drops to 0.
        int framesUntilCapture;

        PendingCapture(CaptureRequest req, CompletableFuture<ObjectNode> respFuture,
                       float sy, float sp, float shy, float sby, boolean shud,
                       boolean override, float useYaw, float usePitch,
                       Vec3 camPos, ArrayNode entities, ArrayNode blocks,
                       ObjectNode crosshair, int framesUntilCapture) {
            this.req = req; this.respFuture = respFuture;
            this.savedYaw = sy; this.savedPitch = sp;
            this.savedHeadYaw = shy; this.savedBodyYaw = sby;
            this.savedHud = shud;
            this.override = override;
            this.useYaw = useYaw; this.usePitch = usePitch;
            this.camPosSnapshot = camPos;
            this.entityAnns = entities; this.blockAnns = blocks;
            this.crossAnn = crosshair;
            this.framesUntilCapture = framesUntilCapture;
        }
    }

    /**
     * Install the one-shot END_CLIENT_TICK listener that drains
     * {@link #PENDING}. Idempotent (Fabric's event API has no unregister,
     * so we must guarantee a single registration for the JVM lifetime).
     */
    private static synchronized void installTickHandler() {
        if (tickHandlerInstalled) return;
        tickHandlerInstalled = true;
        ClientTickEvents.END_CLIENT_TICK.register(VisionHandlers::onEndClientTick);
    }

    /**
     * Per-tick drain: each pending capture decrements its
     * {@code framesUntilCapture}. When it reaches 0, we know the previous
     * frame rendered with the override applied; the main render target holds
     * that scene. Take the screenshot, restore state immediately, and let the
     * GPU-readback callback complete the future.
     */
    private static void onEndClientTick(Minecraft mc) {
        if (PENDING.isEmpty()) return;
        // Drain at most one ready capture per tick. The render target can only
        // hold one scene at a time, so processing N captures back-to-back
        // would all read the SAME texture (incorrect). Sequencing is
        // enforced by panorama's CompletableFuture.thenCompose chain anyway.
        java.util.Iterator<PendingCapture> it = PENDING.iterator();
        while (it.hasNext()) {
            PendingCapture pc = it.next();
            pc.framesUntilCapture--;
            if (pc.framesUntilCapture > 0) continue;
            it.remove();
            try {
                runCapture(mc, pc);
            } catch (Throwable t) {
                pc.respFuture.completeExceptionally(t);
            }
            // Only one capture per tick (see above).
            return;
        }
    }

    /** Schedule the GPU readback for a ready PendingCapture, then restore state. */
    private static void runCapture(Minecraft mc, PendingCapture pc) {
        RenderTarget rt = (mc.gameRenderer != null) ? mc.gameRenderer.mainRenderTarget() : null;
        if (rt == null) {
            pc.respFuture.completeExceptionally(new IllegalStateException("no_framebuffer"));
            restoreState(mc, pc);
            return;
        }
        try {
            // 26.2's Screenshot.takeScreenshot is ASYNC: it enqueues a
            // texture→buffer copy on the command encoder and invokes the
            // consumer once the readback lands (still on the render thread).
            // The consumer OWNS the NativeImage — vanilla's own callback hands
            // it to the IO pool, which closes it — so encodeAndAssemble closes
            // it for us.
            Screenshot.takeScreenshot(rt, img -> {
                try {
                    if (img == null) {
                        pc.respFuture.completeExceptionally(
                                new IllegalStateException("null_native_image"));
                    } else {
                        ObjectNode out = encodeAndAssemble(
                                pc.req, img, pc.useYaw, pc.usePitch, pc.camPosSnapshot,
                                pc.entityAnns, pc.blockAnns, pc.crossAnn);
                        pc.respFuture.complete(out);
                    }
                } catch (Throwable t) {
                    pc.respFuture.completeExceptionally(t);
                }
            });
        } finally {
            // Restore IMMEDIATELY after the copy is enqueued. The GPU already
            // holds the frame-N pixels; mutating player state now affects only
            // the NEXT render frame (which is what the human player sees).
            restoreState(mc, pc);
        }
    }

    private static void restoreState(Minecraft mc, PendingCapture pc) {
        try {
            setHudHidden(mc, pc.savedHud);
            if (pc.override && mc.player != null) {
                mc.player.setYRot(pc.savedYaw);
                mc.player.setXRot(pc.savedPitch);
                mc.player.setYHeadRot(pc.savedHeadYaw);
                mc.player.setYBodyRot(pc.savedBodyYaw);
                // Mirror the *O (previous-tick) / head / body field stomp from
                // prep. Without this, the next-tick render lerp from the
                // override *O values back toward the restored yaw causes a
                // visible camera spin between successive panorama frames.
                mc.player.yRotO = pc.savedYaw;
                mc.player.xRotO = pc.savedPitch;
                mc.player.yHeadRotO = pc.savedHeadYaw;
                mc.player.yBodyRotO = pc.savedBodyYaw;
                mc.player.yHeadRot = pc.savedHeadYaw;
                mc.player.yBodyRot = pc.savedBodyYaw;
            }
        } catch (Throwable ignored) {
            // Swallow — restoration must never throw past the dispatcher.
        }
    }

    /**
     * 26.2 dropped {@code Options.hudHidden} in favour of {@code Hud}'s own
     * flag, which only exposes a toggle — so read-then-flip.
     */
    private static void setHudHidden(Minecraft mc, boolean hidden) {
        if (mc.gui == null || mc.gui.hud == null) return;
        if (mc.gui.hud.isHidden() != hidden) mc.gui.hud.toggle();
    }

    private static boolean isHudHidden(Minecraft mc) {
        return mc.gui != null && mc.gui.hud != null && mc.gui.hud.isHidden();
    }

    /**
     * Submits one capture. Returns a future that completes when the
     * GPU readback finishes and the response is encoded.
     *
     * <p>The prep runnable executes on the render thread BEFORE the next
     * frame's render. It applies the override + HUD change, then enqueues
     * a {@link PendingCapture} that the END_CLIENT_TICK handler will pick
     * up one tick later (i.e., AFTER MC has rendered one frame with the
     * override).
     */
    private static CompletableFuture<ObjectNode> scheduleCapture(CaptureRequest req) {
        CompletableFuture<ObjectNode> respFuture = new CompletableFuture<>();
        Minecraft mc = Minecraft.getInstance();
        mc.execute(() -> {
            try {
                if (mc.level == null || mc.player == null) {
                    respFuture.completeExceptionally(new IllegalStateException("no_world"));
                    return;
                }

                final float savedYaw = mc.player.getYRot();
                final float savedPitch = mc.player.getXRot();
                final float savedHeadYaw = mc.player.getYHeadRot();
                final float savedBodyYaw = mc.player.yBodyRot;
                final boolean savedHud = isHudHidden(mc);
                final boolean override = (req.yaw != null) || (req.pitch != null);
                final float useYaw = (req.yaw != null) ? req.yaw : savedYaw;
                final float usePitch = (req.pitch != null) ? req.pitch : savedPitch;

                if (override) {
                    mc.player.setYRot(useYaw);
                    mc.player.setXRot(usePitch);
                    mc.player.setYHeadRot(useYaw);
                    mc.player.setYBodyRot(useYaw);
                    // Camera rendering uses lerp(yRotO, yRot, partialTick) for
                    // every angle. If we only set yRot, MC interpolates against
                    // the OLD yRotO and the rendered frame is a tween between
                    // saved and override. Stomp the *O fields too so there's
                    // nothing to interpolate from — the override frame is the
                    // override yaw exactly.
                    mc.player.yRotO = useYaw;
                    mc.player.xRotO = usePitch;
                    mc.player.yHeadRotO = useYaw;
                    mc.player.yBodyRotO = useYaw;
                    mc.player.yHeadRot = useYaw;
                    mc.player.yBodyRot = useYaw;
                }
                setHudHidden(mc, !req.includeHud);

                // Snapshot the view-projection matrix for the OVERRIDE camera.
                // 26.2's Camera.update(DeltaTracker) can't be handed an explicit
                // rotation any more, so instead of mutating the camera we
                // rebuild the view rotation from Camera's own convention and
                // reuse the camera's current projection.
                final Camera cam = mc.gameRenderer.mainCamera();
                final Vec3 camPosSnapshot = cam.position();
                final Matrix4f vpSnapshot = overrideViewProjection(cam, useYaw, usePitch);

                final ArrayNode entityAnns =
                        entityAnnotations(mc, camPosSnapshot, vpSnapshot);
                final ArrayNode blockAnns =
                        blockAnnotations(mc, camPosSnapshot, vpSnapshot, req.annotateRange);
                final ObjectNode crossAnn = crosshairAnnotation(mc);

                // framesUntilCapture = 2 because of MC's render-loop ordering:
                //   This prep runnable runs during the client thread's task
                //   drain, END_CLIENT_TICK fires shortly after (still before
                //   the frame's render), and the actual render — the one that
                //   uses our override — happens later in the same frame.
                // So the SAME frame's END_CLIENT_TICK (post-prep) would read a
                // render target that still holds the PREVIOUS frame. With
                // counter=2, the first END_CLIENT_TICK decrements 2→1 (skip),
                // MC renders the override, and the NEXT END_CLIENT_TICK sees
                // 1→0 and captures the override-rendered target.
                PENDING.add(new PendingCapture(
                        req, respFuture,
                        savedYaw, savedPitch, savedHeadYaw, savedBodyYaw, savedHud,
                        override, useYaw, usePitch,
                        camPosSnapshot, entityAnns, blockAnns, crossAnn,
                        2));
            } catch (Throwable t) {
                respFuture.completeExceptionally(t);
            }
        });
        return respFuture;
    }

    /**
     * Build {@code P · V(yaw,pitch)} for an arbitrary rotation.
     *
     * <p>{@link Camera#getViewRotationProjectionMatrix} returns {@code P · V}
     * for the camera's CURRENT rotation and {@link Camera#getViewRotationMatrix}
     * returns that same {@code V}, so the pure projection is
     * {@code P = (P·V) · V⁻¹}. {@code V} is a pure rotation, so the inverse is
     * exact. The override {@code V} follows {@code Camera.setRotation}:
     * {@code rotationYXZ(π − yaw·deg, −pitch·deg, 0)}, then conjugated (view
     * matrices are the inverse of the camera orientation).
     */
    private static Matrix4f overrideViewProjection(Camera cam, float yaw, float pitch) {
        Matrix4f viewCurrent = cam.getViewRotationMatrix(new Matrix4f());
        Matrix4f proj = cam.getViewRotationProjectionMatrix(new Matrix4f())
                .mul(viewCurrent.invert());
        Quaternionf rot = new Quaternionf().rotationYXZ(
                (float) Math.PI - yaw * DEG_TO_RAD, -pitch * DEG_TO_RAD, 0.0f);
        Matrix4f viewOverride = new Matrix4f().rotation(rot.conjugate());
        return proj.mul(viewOverride);
    }

    /** Image conversion + base64 + envelope. Runs inside the GPU-readback callback. */
    private static ObjectNode encodeAndAssemble(CaptureRequest req,
                                                NativeImage img,
                                                float useYaw,
                                                float usePitch,
                                                Vec3 camPos,
                                                ArrayNode entityAnns,
                                                ArrayNode blockAnns,
                                                ObjectNode crossAnn) throws Exception {
        int srcW, srcH;
        BufferedImage buf;
        try (NativeImage ni = img) {
            srcW = ni.getWidth();
            srcH = ni.getHeight();
            buf = nativeToBuffered(ni);
        }

        int outW = (req.width != null && req.width > 0) ? Math.min(req.width, srcW) : srcW;
        int outH = (int) Math.round((double) outW * srcH / srcW);
        BufferedImage finalImg = (outW == srcW && outH == srcH) ? buf : downscale(buf, outW, outH);

        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        if (req.format.equals("jpeg")) {
            // JPEG has no alpha channel; ImageIO refuses TYPE_INT_ARGB input.
            finalImg = toOpaque(finalImg);
        }
        ImageIO.write(finalImg, req.format.equals("jpeg") ? "jpeg" : "png", baos);
        String base64 = java.util.Base64.getEncoder().encodeToString(baos.toByteArray());

        ObjectNode out = M.createObjectNode();
        out.put("image", base64);
        out.put("format", req.format);
        out.put("width", outW);
        out.put("height", outH);
        out.put("captured_at", System.currentTimeMillis());
        ObjectNode camNode = out.putObject("camera");
        camNode.put("yaw", useYaw);
        camNode.put("pitch", usePitch);
        ObjectNode camPosNode = camNode.putObject("pos");
        camPosNode.put("x", camPos.x);
        camPosNode.put("y", camPos.y);
        camPosNode.put("z", camPos.z);

        // Annotations were computed in normalized [0..1] coords (origin
        // top-left). Now that we know the final output image dims, scale to
        // pixel space so the agent gets concrete x/y/w/h matching .image.
        ObjectNode anns = out.putObject("annotations");
        anns.set("entities", scaleEntityAnns(entityAnns, outW, outH));
        anns.set("blocks", scaleBlockAnns(blockAnns, outW, outH));
        anns.set("lookingAt", crossAnn);
        return out;
    }

    private static ArrayNode scaleEntityAnns(ArrayNode src, int w, int h) {
        ArrayNode out = M.createArrayNode();
        for (JsonNode n : src) {
            ObjectNode o = (ObjectNode) n.deepCopy();
            ObjectNode s = (ObjectNode) o.get("screen");
            double x = s.get("x").asDouble() * w;
            double y = s.get("y").asDouble() * h;
            double sw = s.get("w").asDouble() * w;
            double sh = s.get("h").asDouble() * h;
            s.put("x", x); s.put("y", y); s.put("w", sw); s.put("h", sh);
            s.remove("normalized");
            out.add(o);
        }
        return out;
    }

    private static ArrayNode scaleBlockAnns(ArrayNode src, int w, int h) {
        ArrayNode out = M.createArrayNode();
        for (JsonNode n : src) {
            ObjectNode o = (ObjectNode) n.deepCopy();
            ObjectNode s = (ObjectNode) o.get("screen");
            double x = s.get("x").asDouble() * w;
            double y = s.get("y").asDouble() * h;
            s.put("x", x); s.put("y", y);
            s.remove("normalized");
            out.add(o);
        }
        return out;
    }

    // --- Image helpers ------------------------------------------------------

    private static BufferedImage nativeToBuffered(NativeImage ni) {
        int w = ni.getWidth(), h = ni.getHeight();
        BufferedImage out = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
        // getPixels() is row-major ARGB (bulk ABGR read + ARGB.fromABGR per
        // pixel) and requires an RGBA-format image — which is exactly what
        // Screenshot's readback allocates. It replaces the now-deprecated
        // makePixelArray(), which does the same thing one getPixel() at a time.
        int[] pixels = ni.getPixels();
        out.setRGB(0, 0, w, h, pixels, 0, w);
        return out;
    }

    private static BufferedImage downscale(BufferedImage src, int w, int h) {
        BufferedImage dst = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = dst.createGraphics();
        try {
            g.setRenderingHint(RenderingHints.KEY_INTERPOLATION,
                    RenderingHints.VALUE_INTERPOLATION_BILINEAR);
            g.setRenderingHint(RenderingHints.KEY_RENDERING,
                    RenderingHints.VALUE_RENDER_QUALITY);
            g.drawImage(src, 0, 0, w, h, null);
        } finally {
            g.dispose();
        }
        return dst;
    }

    private static BufferedImage toOpaque(BufferedImage src) {
        BufferedImage dst = new BufferedImage(src.getWidth(), src.getHeight(),
                BufferedImage.TYPE_INT_RGB);
        Graphics2D g = dst.createGraphics();
        try {
            g.drawImage(src, 0, 0, null);
        } finally {
            g.dispose();
        }
        return dst;
    }

    // --- Projection ---------------------------------------------------------

    /**
     * Project a world point into normalized [0..1] screen coordinates (origin
     * top-left). Returns {@code null} if behind camera or off-screen. The
     * caller multiplies by whatever pixel resolution it eventually emits.
     */
    private static float[] projectNorm(Vec3 worldPos, Vec3 camPos, Matrix4f viewProj) {
        Vector4f v = new Vector4f(
                (float) (worldPos.x - camPos.x),
                (float) (worldPos.y - camPos.y),
                (float) (worldPos.z - camPos.z),
                1.0f);
        viewProj.transform(v);
        if (v.w <= 0.0001f) return null;
        float ndcX = v.x / v.w;
        float ndcY = v.y / v.w;
        if (ndcX < -1f || ndcX > 1f || ndcY < -1f || ndcY > 1f) return null;
        float u = ndcX * 0.5f + 0.5f;
        float vY = 1f - (ndcY * 0.5f + 0.5f);
        return new float[]{u, vY};
    }

    // --- Annotation builders (project to NORMALIZED [0..1] coords) ---------

    private static ArrayNode entityAnnotations(Minecraft mc, Vec3 camPos, Matrix4f viewProj) {
        ArrayNode arr = M.createArrayNode();
        if (mc.level == null) return arr;
        record Hit(Entity e, float[] center, float minU, float minV, float maxU, float maxV, double dist) {}
        List<Hit> hits = new ArrayList<>();
        for (Entity e : mc.level.entitiesForRendering()) {
            if (e == null || e == mc.player || !e.isAlive() || e.isRemoved()) continue;
            double dist = e.position().distanceTo(camPos);
            if (dist > ENTITY_RANGE) continue;
            Vec3 center = e.position().add(0, e.getBoundingBox().getYsize() * 0.5, 0);
            float[] c = projectNorm(center, camPos, viewProj);
            if (c == null) continue;
            AABB bb = e.getBoundingBox();
            float minU = Float.POSITIVE_INFINITY, minV = Float.POSITIVE_INFINITY;
            float maxU = Float.NEGATIVE_INFINITY, maxV = Float.NEGATIVE_INFINITY;
            int seen = 0;
            for (int i = 0; i < 8; i++) {
                double cx = ((i & 1) == 0) ? bb.minX : bb.maxX;
                double cy = ((i & 2) == 0) ? bb.minY : bb.maxY;
                double cz = ((i & 4) == 0) ? bb.minZ : bb.maxZ;
                float[] p = projectNorm(new Vec3(cx, cy, cz), camPos, viewProj);
                if (p == null) continue;
                seen++;
                if (p[0] < minU) minU = p[0];
                if (p[1] < minV) minV = p[1];
                if (p[0] > maxU) maxU = p[0];
                if (p[1] > maxV) maxV = p[1];
            }
            if (seen == 0) {
                minU = c[0]; maxU = c[0]; minV = c[1]; maxV = c[1];
            }
            hits.add(new Hit(e, c, minU, minV, maxU, maxV, dist));
        }
        hits.sort(Comparator.comparingDouble(Hit::dist));
        int n = Math.min(hits.size(), MAX_ENTITIES);
        for (int i = 0; i < n; i++) {
            Hit hit = hits.get(i);
            ObjectNode o = arr.addObject();
            o.put("entityId", hit.e.getId());
            o.put("type", BuiltInRegistries.ENTITY_TYPE.getKey(hit.e.getType()).toString());
            o.put("name", hit.e.getName().getString());
            o.put("distance", hit.dist);
            ObjectNode screen = o.putObject("screen");
            // Normalized [0..1] coords. Caller multiplies by image width/height.
            screen.put("x", hit.minU);
            screen.put("y", hit.minV);
            screen.put("w", hit.maxU - hit.minU);
            screen.put("h", hit.maxV - hit.minV);
            screen.put("normalized", true);
            ObjectNode world = o.putObject("world");
            Vec3 ep = hit.e.position();
            world.put("x", ep.x); world.put("y", ep.y); world.put("z", ep.z);
        }
        return arr;
    }

    private static ArrayNode blockAnnotations(Minecraft mc, Vec3 camPos, Matrix4f viewProj,
                                              int range) {
        ArrayNode arr = M.createArrayNode();
        if (mc.level == null || mc.player == null) return arr;
        BlockPos origin = mc.player.blockPosition();
        record Hit(Identifier id, BlockPos pos, float[] screen, double dist) {}
        List<Hit> hits = new ArrayList<>();
        for (int dx = -range; dx <= range; dx++) {
            for (int dy = -range; dy <= range; dy++) {
                for (int dz = -range; dz <= range; dz++) {
                    BlockPos bp = origin.offset(dx, dy, dz);
                    var state = mc.level.getBlockState(bp);
                    if (state.isAir()) continue;
                    Identifier id = BuiltInRegistries.BLOCK.getKey(state.getBlock());
                    if (!INTERACTIVE_BLOCKS.contains(id)) continue;
                    Vec3 center = new Vec3(bp.getX() + 0.5, bp.getY() + 0.5, bp.getZ() + 0.5);
                    float[] s = projectNorm(center, camPos, viewProj);
                    if (s == null) continue;
                    double dist = center.distanceTo(camPos);
                    hits.add(new Hit(id, bp, s, dist));
                }
            }
        }
        hits.sort(Comparator.comparingDouble(Hit::dist));
        int n = Math.min(hits.size(), MAX_BLOCKS);
        for (int i = 0; i < n; i++) {
            Hit hit = hits.get(i);
            ObjectNode o = arr.addObject();
            o.put("id", hit.id.toString());
            o.put("distance", hit.dist);
            ObjectNode screen = o.putObject("screen");
            screen.put("x", hit.screen[0]);
            screen.put("y", hit.screen[1]);
            screen.put("normalized", true);
            ObjectNode world = o.putObject("world");
            world.put("x", hit.pos.getX());
            world.put("y", hit.pos.getY());
            world.put("z", hit.pos.getZ());
        }
        return arr;
    }

    private static ObjectNode crosshairAnnotation(Minecraft mc) {
        ObjectNode n = M.createObjectNode();
        HitResult hit = mc.hitResult;
        if (hit == null || hit.getType() == HitResult.Type.MISS) {
            n.put("kind", "miss");
            return n;
        }
        if (hit instanceof BlockHitResult bhr && bhr.getType() == HitResult.Type.BLOCK && mc.level != null) {
            n.put("kind", "block");
            BlockPos bp = bhr.getBlockPos();
            n.put("id", BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(bp).getBlock()).toString());
            n.put("side", bhr.getDirection().getSerializedName());
            ObjectNode w = n.putObject("world");
            w.put("x", bp.getX()); w.put("y", bp.getY()); w.put("z", bp.getZ());
            ObjectNode hp = n.putObject("hit");
            Vec3 hv = bhr.getLocation();
            hp.put("x", hv.x); hp.put("y", hv.y); hp.put("z", hv.z);
            return n;
        }
        if (hit instanceof EntityHitResult ehr) {
            n.put("kind", "entity");
            Entity e = ehr.getEntity();
            n.put("entityId", e.getId());
            n.put("type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
            ObjectNode w = n.putObject("world");
            Vec3 ep = e.position();
            w.put("x", ep.x); w.put("y", ep.y); w.put("z", ep.z);
            return n;
        }
        n.put("kind", "miss");
        return n;
    }

    // --- Panorama -----------------------------------------------------------

    private static float[][] panoramaOffsets(int angles) {
        float NAN = Float.NaN; // sentinel: keep base pitch
        return switch (angles) {
            case 4 -> new float[][]{
                    {0,   NAN}, {90,  NAN}, {180, NAN}, {270, NAN}
            };
            case 6 -> new float[][]{
                    {0,   NAN}, {90,  NAN}, {180, NAN}, {270, NAN},
                    {0,  -90f}, {0,    90f}
            };
            case 8 -> new float[][]{
                    {0,   NAN}, {45,  NAN}, {90,  NAN}, {135, NAN},
                    {180, NAN}, {225, NAN}, {270, NAN}, {315, NAN}
            };
            default -> {
                float step = 360f / angles;
                float[][] out = new float[angles][2];
                for (int i = 0; i < angles; i++) { out[i][0] = i * step; out[i][1] = NAN; }
                yield out;
            }
        };
    }

    // --- Misc ---------------------------------------------------------------

    private static <T> T await0(CompletableFuture<T> fut, long timeoutMs, String onTimeout) throws Exception {
        try {
            return fut.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException te) {
            throw new RuntimeException(onTimeout);
        } catch (ExecutionException ee) {
            Throwable c = ee.getCause();
            if (c instanceof Exception ex) throw ex;
            throw new RuntimeException(c != null ? c : ee);
        }
    }

    private static int clamp(int v, int lo, int hi) { return Math.max(lo, Math.min(hi, v)); }
}
