package com.btone.c;

import com.btone.c.events.EventBus;
import com.btone.c.events.GameEvents;
import com.btone.c.events.SseEndpoint;
import com.btone.c.events.SubtitleEvents;
import com.btone.c.handlers.AgentHandlers;
import com.btone.c.handlers.AgentStatusHandlers;
import com.btone.c.handlers.BaritoneHandlers;
import com.btone.c.handlers.ChatHandlers;
import com.btone.c.handlers.ContainerHandlers;
import com.btone.c.handlers.CraftingHandlers;
import com.btone.c.handlers.MeteorHandlers;
import com.btone.c.handlers.PlayerHandlers;
import com.btone.c.handlers.VisionHandlers;
import com.btone.c.handlers.WorldReadHandlers;
import com.btone.c.handlers.WorldWriteHandlers;
import com.btone.c.http.BtoneHttpServer;
import com.btone.c.rpc.RpcRouter;
import com.btone.c.schema.Schema;
import com.btone.c.util.ConnectionConfig;
import com.btone.c.util.Token;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Consumer;

public final class BtoneC implements ClientModInitializer {
    public static final Logger LOG = LoggerFactory.getLogger("btone-c");
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Override
    public void onInitializeClient() {
        try {
            // No auth needed — server binds to 127.0.0.1 only.
            String token = null;

            EventBus eventBus = new EventBus();
            SseEndpoint sse = new SseEndpoint(eventBus);

            // Give AgentNotifier access to the EventBus so Meteor modules
            // can emit chat events directly (bypasses broken chat signing).
            com.btone.c.meteor.AgentNotifier.init(eventBus);

            // Give the ChatHud mixin access to the EventBus (via ChatBridge)
            // so ALL chat messages (including Baritone/Meteor which bypass
            // Fabric's ClientReceiveMessageEvents.GAME) get emitted as SSE events.
            com.btone.c.events.ChatBridge.init(eventBus);

            RpcRouter router = new RpcRouter();
            router.register("debug.echo", params -> params);
            router.register("debug.methods", params -> {
                ObjectNode n = MAPPER.createObjectNode();
                var arr = n.putArray("methods");
                router.all().keySet().forEach(arr::add);
                return n;
            });

            // OpenRPC self-introspection. Returns the full schema (param/result
            // types for every method) so clients can self-discover. Prefers the
            // bundled spec resource (committed via gradle generateOpenRpc), and
            // falls back to building from Schema.java in-memory.
            router.register("rpc.discover", params -> {
                ObjectNode bundled = Schema.loadBundledSpec();
                return bundled != null ? bundled : Schema.buildOpenRpc();
            });

            PlayerHandlers.registerAll(router);
            WorldReadHandlers.registerAll(router);
            ChatHandlers.registerAll(router);
            WorldWriteHandlers.registerAll(router);
            ContainerHandlers.registerAll(router);
            CraftingHandlers.registerAll(router);
            BaritoneHandlers.registerAll(router);
            VisionHandlers.registerAll(router);
            AgentHandlers.registerAll(router);
            AgentStatusHandlers.registerAll(router);

            // Optional Meteor surface -- reflection-loaded if Meteor is present.
            // Per the lessons from mod-b: probe with Class.forName at registration
            // time so we know whether to install the routes; the facade itself
            // re-resolves on every call (Meteor may not finish initializing
            // until well after our onInitializeClient runs).
            boolean meteorOk = false;
            try {
                Class.forName("meteordevelopment.meteorclient.systems.modules.Modules");
                MeteorHandlers.registerAll(router);
                LOG.info("meteor integration enabled");
                meteorOk = true;
            } catch (ClassNotFoundException cnfe) {
                LOG.info("meteor not present; meteor.* handlers disabled");
            }
            final boolean meteorPresent = meteorOk;
            // Custom-module registration is deferred to CLIENT_STARTED below,
            // because Modules.get() returns null at onInitializeClient time
            // (Meteor's own ClientModInitializer hasn't run yet).

            Map<String, Consumer<HttpExchange>> routes = new LinkedHashMap<>();
            routes.put("/health", ex -> BtoneHttpServer.write(ex, 200, "{\"ok\":true}"));
            routes.put("/rpc", ex -> {
                if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
                    BtoneHttpServer.write(ex, 405,
                            "{\"ok\":false,\"error\":{\"code\":\"method_not_allowed\"}}");
                    return;
                }
                try {
                    JsonNode req = MAPPER.readTree(ex.getRequestBody());
                    ObjectNode resp = router.dispatch(req);
                    BtoneHttpServer.write(ex, 200, resp.toString());
                } catch (Exception e) {
                    BtoneHttpServer.write(ex, 400,
                            "{\"ok\":false,\"error\":{\"code\":\"bad_request\",\"message\":\""
                                    + safe(e.getMessage()) + "\"}}");
                }
            });
            routes.put("/events", sse::handle);

            BtoneHttpServer server = new BtoneHttpServer(25591, token, routes);
            server.start();

            GameEvents.register(eventBus);

            // Disable vanilla auto-pause when the MC window loses focus.
            // The Bot is meant to run in the background while the agent drives it
            // via RPC; the auto-pause Game Menu was popping up after every alt-tab
            // and after some agent-driven screen interactions.
            ClientLifecycleEvents.CLIENT_STARTED.register(c -> {
                try {
                    c.options.pauseOnLostFocus = false;
                    LOG.info("btone-mod-c: pauseOnLostFocus disabled");
                } catch (Throwable t) {
                    LOG.warn("btone-mod-c: could not disable pauseOnLostFocus", t);
                }

                // Lower view distance to keep render-thread load manageable.
                // Bot mining + chunk-loading at default view distance has been
                // saturating the render thread (UBO resize warnings, slow
                // World save → server kicks the slow client). 6 chunks is
                // playable for the bot's needs and keeps frame budget free.
                try {
                    c.options.getViewDistance().setValue(6);
                    c.options.getSimulationDistance().setValue(6);
                    LOG.info("btone-mod-c: view/simulation distance set to 6");
                } catch (Throwable t) {
                    LOG.warn("btone-mod-c: could not lower view distance", t);
                }

                // Now that the client is running, Meteor's Modules registry is
                // populated. Register our custom modules.
                if (meteorPresent) {
                    try {
                        Class<?> modulesCls = Class.forName(
                                "meteordevelopment.meteorclient.systems.modules.Modules");
                        Object modulesInstance = modulesCls.getMethod("get").invoke(null);
                        if (modulesInstance == null) {
                            LOG.warn("meteor Modules.get() still null at CLIENT_STARTED; skipping");
                        } else {
                            var addMethod = modulesCls.getMethod("add", Class.forName(
                                    "meteordevelopment.meteorclient.systems.modules.Module"));
                            // RunAwayFromDanger - auto-enable on startup
                            com.btone.c.meteor.RunAwayFromDanger runAway = new com.btone.c.meteor.RunAwayFromDanger();
                            addMethod.invoke(modulesInstance, runAway);
                            runAway.toggle();  // Enable by default
                            LOG.info("registered meteor module: run-away-from-danger (auto-enabled)");

                            // IdlenessDetector - registered but NOT auto-enabled
                            addMethod.invoke(modulesInstance,
                                    new com.btone.c.meteor.IdlenessDetector());
                            LOG.info("registered meteor module: idleness-detector");

                            addMethod.invoke(modulesInstance,
                                    new com.btone.c.meteor.PanicBoxUp());
                            LOG.info("registered meteor module: panic-box-up");
                            addMethod.invoke(modulesInstance,
                                    new com.btone.c.meteor.EnsurePickInHotbar());
                            LOG.info("registered meteor module: ensure-pick-in-hotbar");
                            com.btone.c.meteor.EnsureFoodInHotbar ensureFood = new com.btone.c.meteor.EnsureFoodInHotbar();
                            addMethod.invoke(modulesInstance, ensureFood);
                            ensureFood.toggle();  // Enable by default
                            LOG.info("registered meteor module: ensure-food-in-hotbar (auto-enabled)");

                            // Surface - auto-enable on startup
                            com.btone.c.meteor.Surface surface = new com.btone.c.meteor.Surface();
                            addMethod.invoke(modulesInstance, surface);
                            surface.toggle();  // Enable by default
                            LOG.info("registered meteor module: surface (auto-enabled)");

                            addMethod.invoke(modulesInstance,
                                    new com.btone.c.meteor.AutoCraftBread());
                            LOG.info("registered meteor module: auto-craft-bread");
                            addMethod.invoke(modulesInstance,
                                    new com.btone.c.meteor.CraftIronArmor());
                            LOG.info("registered meteor module: craft-iron-armor");
                        }

                        // Register HUD elements with Meteor's HUD system.
                        try {
                            Class<?> hudCls = Class.forName(
                                    "meteordevelopment.meteorclient.systems.hud.Hud");
                            Object hudInstance = hudCls.getMethod("get").invoke(null);
                            if (hudInstance != null) {
                                Class<?> hudElementInfoCls = Class.forName(
                                    "meteordevelopment.meteorclient.systems.hud.HudElementInfo");
                                var registerMethod = hudCls.getMethod("register", hudElementInfoCls);
                                registerMethod.invoke(hudInstance,
                                    com.btone.c.meteor.InventoryOverlayHud.INFO);
                                LOG.info("registered hud element: inventory-overlay");
                                registerMethod.invoke(hudInstance,
                                    com.btone.c.meteor.ArmorOverlayHud.INFO);
                                LOG.info("registered hud element: armor-overlay");
                                registerMethod.invoke(hudInstance,
                                    com.btone.c.meteor.CronHud.INFO);
                                LOG.info("registered hud element: cron-status");
                                registerMethod.invoke(hudInstance,
                                    com.btone.c.meteor.TodoHud.INFO);
                                LOG.info("registered hud element: todo-list");

                                // Ensure the HUD system itself is active.
                                var activeField = hudCls.getField("active");
                                boolean wasActive = activeField.getBoolean(hudInstance);
                                activeField.setBoolean(hudInstance, true);
                                LOG.info("hud system active: was={}, now=true", wasActive);

                                // Check if our element is already in the elements list
                                // (from a saved config). If not, auto-add it.
                                var elementsField = hudCls.getDeclaredField("elements");
                                elementsField.setAccessible(true);
                                @SuppressWarnings("unchecked")
                                var elements = (java.util.List<Object>) elementsField.get(hudInstance);
                                boolean alreadyPresent = false;
                                for (Object el : elements) {
                                    var infoField = el.getClass().getSuperclass().getDeclaredField("info");
                                    if (!infoField.canAccess(el)) infoField.setAccessible(true);
                                    Object elInfo = infoField.get(el);
                                    if (elInfo == com.btone.c.meteor.InventoryOverlayHud.INFO) {
                                        alreadyPresent = true;
                                        break;
                                    }
                                }

                                if (!alreadyPresent) {
                                    // Auto-add to bottom-left.
                                    Class<?> xAnchorCls = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.XAnchor");
                                    Class<?> yAnchorCls = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.YAnchor");
                                    var addMethod2 = hudCls.getMethod("add",
                                        hudElementInfoCls, int.class, int.class,
                                        xAnchorCls, yAnchorCls);
                                    addMethod2.invoke(hudInstance,
                                        com.btone.c.meteor.InventoryOverlayHud.INFO,
                                        4, -4,
                                        Enum.valueOf((Class<Enum>) xAnchorCls, "Left"),
                                        Enum.valueOf((Class<Enum>) yAnchorCls, "Bottom"));
                                    LOG.info("auto-added inventory-overlay hud to bottom-left");
                                } else {
                                    LOG.info("inventory-overlay hud already present (from saved config)");
                                }

                                // Auto-add armor overlay next to inventory overlay
                                boolean armorAlreadyPresent = false;
                                for (Object el : elements) {
                                    var infoField = el.getClass().getSuperclass().getDeclaredField("info");
                                    if (!infoField.canAccess(el)) infoField.setAccessible(true);
                                    Object elInfo = infoField.get(el);
                                    if (elInfo == com.btone.c.meteor.ArmorOverlayHud.INFO) {
                                        armorAlreadyPresent = true;
                                        break;
                                    }
                                }

                                if (!armorAlreadyPresent) {
                                    // Auto-add to bottom-left, positioned to the right of inventory
                                    // Inventory is ~166px wide at scale 1.0, so place armor at x=174
                                    Class<?> xAnchorCls = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.XAnchor");
                                    Class<?> yAnchorCls = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.YAnchor");
                                    var addMethod2 = hudCls.getMethod("add",
                                        hudElementInfoCls, int.class, int.class,
                                        xAnchorCls, yAnchorCls);
                                    addMethod2.invoke(hudInstance,
                                        com.btone.c.meteor.ArmorOverlayHud.INFO,
                                        174, -4,
                                        Enum.valueOf((Class<Enum>) xAnchorCls, "Left"),
                                        Enum.valueOf((Class<Enum>) yAnchorCls, "Bottom"));
                                    LOG.info("auto-added armor-overlay hud next to inventory");
                                } else {
                                    LOG.info("armor-overlay hud already present (from saved config)");
                                }

                                // Auto-add todo-list HUD above the inventory overlay
                                boolean todoHudPresent = false;
                                for (Object el : elements) {
                                    var infoField = el.getClass().getSuperclass().getDeclaredField("info");
                                    if (!infoField.canAccess(el)) infoField.setAccessible(true);
                                    Object elInfo = infoField.get(el);
                                    if (elInfo == com.btone.c.meteor.TodoHud.INFO) {
                                        todoHudPresent = true;
                                        break;
                                    }
                                }

                                if (!todoHudPresent) {
                                    // Place above cron-status. Cron is ~70px tall at max.
                                    // Use y=-140 so it sits above the cron HUD.
                                    Class<?> xAnchorCls4 = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.XAnchor");
                                    Class<?> yAnchorCls4 = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.YAnchor");
                                    var addMethod4 = hudCls.getMethod("add",
                                        hudElementInfoCls, int.class, int.class,
                                        xAnchorCls4, yAnchorCls4);
                                    addMethod4.invoke(hudInstance,
                                        com.btone.c.meteor.TodoHud.INFO,
                                        4, -140,
                                        Enum.valueOf((Class<Enum>) xAnchorCls4, "Left"),
                                        Enum.valueOf((Class<Enum>) yAnchorCls4, "Bottom"));
                                    LOG.info("auto-added todo-list hud above cron-status");
                                } else {
                                    LOG.info("todo-list hud already present (from saved config)");
                                }

                                // Auto-add cron-status HUD above todo-list
                                boolean cronHudPresent = false;
                                for (Object el : elements) {
                                    var infoField = el.getClass().getSuperclass().getDeclaredField("info");
                                    if (!infoField.canAccess(el)) infoField.setAccessible(true);
                                    Object elInfo = infoField.get(el);
                                    if (elInfo == com.btone.c.meteor.CronHud.INFO) {
                                        cronHudPresent = true;
                                        break;
                                    }
                                }

                                if (!cronHudPresent) {
                                    // Place above inventory overlay (y=-92), below todo-list.
                                    Class<?> xAnchorCls5 = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.XAnchor");
                                    Class<?> yAnchorCls5 = Class.forName(
                                        "meteordevelopment.meteorclient.systems.hud.YAnchor");
                                    var addMethod5 = hudCls.getMethod("add",
                                        hudElementInfoCls, int.class, int.class,
                                        xAnchorCls5, yAnchorCls5);
                                    addMethod5.invoke(hudInstance,
                                        com.btone.c.meteor.CronHud.INFO,
                                        4, -92,
                                        Enum.valueOf((Class<Enum>) xAnchorCls5, "Left"),
                                        Enum.valueOf((Class<Enum>) yAnchorCls5, "Bottom"));
                                    LOG.info("auto-added cron-status hud above inventory");
                                } else {
                                    LOG.info("cron-status hud already present (from saved config)");
                                }
                            }
                        } catch (Throwable hudErr) {
                            LOG.warn("failed to register inventory-overlay hud: {}",
                                hudErr.toString());
                        }
                    } catch (Throwable modErr) {
                        LOG.warn("failed to register custom meteor module(s): {}", modErr.toString());
                    }
                }

                // Register subtitle (sound event) listener on the SoundManager.
                // Must happen after CLIENT_STARTED so SoundManager is initialised.
                SubtitleEvents.register(eventBus);
            });

            ClientLifecycleEvents.CLIENT_STOPPING.register(c -> {
                LOG.info("btone-mod-c stopping, closing http server");
                server.stop();
            });

            ConnectionConfig cfg = new ConnectionConfig(server.actualPort(), token, "0.1.0");
            var cfgPath = FabricLoader.getInstance().getConfigDir().resolve("btone-bridge.json");
            cfg.writeTo(cfgPath);

            LOG.info("btone-mod-c listening on 127.0.0.1:{}; config at {}",
                    server.actualPort(), cfgPath);
        } catch (Exception e) {
            LOG.error("failed to start btone-mod-c http server", e);
        }
    }

    private static String safe(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
