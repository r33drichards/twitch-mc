package com.btone.c.events;

import baritone.api.BaritoneAPI;
import baritone.api.event.events.PathEvent;
import baritone.api.event.listener.AbstractGameEventListener;
import com.btone.c.handlers.ChatHandlers;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;

import java.util.HashMap;
import java.util.Map;

/** Wires Fabric / Baritone game events into the {@link EventBus}. */
public final class GameEvents {
    private GameEvents() {}

    public static void register(EventBus bus) {
        // NOTE: Chat messages from Baritone and Meteor bypass
        // ClientReceiveMessageEvents.GAME — they call ChatHud.addMessage()
        // directly. The ChatHudMixin now intercepts ALL messages at the
        // ChatHud level, so we no longer need this listener (it would
        // double-emit server chat that goes through both paths).
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
        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> {
            bus.emit("joined");
            // primaryBaritone is null until the player joins, so register after.
            registerBaritonePathListener(bus);
        });
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> bus.emit("disconnected"));
    }

    private static void registerBaritonePathListener(EventBus bus) {
        try {
            var b = BaritoneAPI.getProvider().getPrimaryBaritone();
            if (b == null) return;
            b.getGameEventHandler().registerEventListener(new AbstractGameEventListener() {
                @Override
                public void onPathEvent(PathEvent event) {
                    bus.emit("path", Map.of("event", event.name()));
                }
            });
        } catch (Throwable ignored) {
            // Baritone not present or API mismatch — not fatal.
        }
    }
}
