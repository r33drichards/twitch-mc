package com.btone.c.events;

import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;

import java.util.HashMap;
import java.util.Map;

/** Wires Fabric game events into the {@link EventBus}. */
public final class GameEvents {
    private GameEvents() {}

    public static void register(EventBus bus) {
        // NOTE: The ChatHudMixin intercepts ALL non-overlay messages at the
        // ChatHud level, so we no longer need a ClientReceiveMessageEvents.GAME
        // listener for them (it would double-emit server chat that goes through
        // both paths).
        // Kept as comment for reference:
        //
        // ClientReceiveMessageEvents.GAME.register((msg, overlay) -> {
        //     String text = msg.getString();
        //     ChatHandlers.record(text);
        //     bus.emit("chat", Map.of("text", text, "overlay", overlay));
        // });
        //
        // Overlay messages (action bar) still need to be captured separately
        // since they don't go through ChatHud.addMessage:
        ClientReceiveMessageEvents.GAME.register((msg, overlay) -> {
            if (overlay) {
                String text = msg.getString();
                Map<String, Object> p = new HashMap<>();
                p.put("text", text);
                p.put("overlay", true);
                bus.emit("chat", p);
            }
            // Non-overlay messages are handled by ChatHudMixin
        });
        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> bus.emit("joined"));
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> bus.emit("disconnected"));
    }
}
