package com.btone.c.mixin;

import com.btone.c.events.ChatBridge;
import com.btone.c.events.EventBus;
import com.btone.c.handlers.ChatHandlers;
import net.minecraft.client.gui.hud.ChatHud;
import net.minecraft.client.gui.hud.MessageIndicator;
import net.minecraft.network.message.MessageSignatureData;
import net.minecraft.text.Text;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.HashMap;
import java.util.Map;

/**
 * Mixin on ChatHud to intercept ALL chat messages — including those from
 * Baritone and Meteor which bypass ClientReceiveMessageEvents.GAME and
 * call ChatHud.addMessage() directly.
 *
 * We hook both public addMessage overloads to catch everything.
 */
@Mixin(ChatHud.class)
public class ChatHudMixin {

    // NOTE: We only hook the 3-arg overload. The 1-arg addMessage(Text) internally
    // delegates to this 3-arg version, so hooking both causes every message to
    // appear twice. By hooking only here, we catch everything exactly once.

    /**
     * Hook the 3-arg addMessage(Text, MessageSignatureData, MessageIndicator)
     * — ALL addMessage calls funnel through this overload.
     */
    @Inject(
        method = "addMessage(Lnet/minecraft/text/Text;Lnet/minecraft/network/message/MessageSignatureData;Lnet/minecraft/client/gui/hud/MessageIndicator;)V",
        at = @At("HEAD")
    )
    private void onAddMessageFull(Text message, MessageSignatureData signature, MessageIndicator indicator, CallbackInfo ci) {
        handleMessage(message);
    }

    private static void handleMessage(Text message) {
        if (message == null) return;
        String text = message.getString();
        if (text == null || text.isEmpty()) return;

        // Record in the chat buffer so chat.recent picks it up
        ChatHandlers.record(text);

        // Emit to EventBus for SSE subscribers (mc-bridge)
        EventBus bus = ChatBridge.bus();
        if (bus != null) {
            Map<String, Object> payload = new HashMap<>();
            payload.put("text", text);
            payload.put("overlay", false);
            bus.emit("chat", payload);
        }
    }
}
