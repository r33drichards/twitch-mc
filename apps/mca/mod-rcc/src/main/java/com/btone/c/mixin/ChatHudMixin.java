package com.btone.c.mixin;

import com.btone.c.events.ChatBridge;
import com.btone.c.events.EventBus;
import com.btone.c.handlers.ChatHandlers;
import net.minecraft.client.gui.components.ChatComponent;
import net.minecraft.client.multiplayer.chat.GuiMessageSource;
import net.minecraft.client.multiplayer.chat.GuiMessageTag;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MessageSignature;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.HashMap;
import java.util.Map;

/**
 * Mixin on ChatComponent to intercept ALL chat messages — including any that
 * bypass ClientReceiveMessageEvents.GAME by calling ChatComponent.addMessage()
 * directly.
 *
 * We hook the funnel addMessage overload to catch everything exactly once.
 */
@Mixin(ChatComponent.class)
public class ChatHudMixin {

    // NOTE: We only hook the private funnel addMessage overload. All public
    // entry points (addClientSystemMessage / addServerSystemMessage /
    // addPlayerMessage) delegate to this 4-arg version, so hooking only here
    // catches everything exactly once.

    /**
     * Hook the 4-arg
     * addMessage(Component, MessageSignature, GuiMessageSource, GuiMessageTag)
     * — ALL addMessage calls funnel through this overload.
     */
    @Inject(
        method = "addMessage(Lnet/minecraft/network/chat/Component;Lnet/minecraft/network/chat/MessageSignature;Lnet/minecraft/client/multiplayer/chat/GuiMessageSource;Lnet/minecraft/client/multiplayer/chat/GuiMessageTag;)V",
        at = @At("HEAD")
    )
    private void onAddMessageFull(Component message, MessageSignature signature,
                                  GuiMessageSource source, GuiMessageTag tag, CallbackInfo ci) {
        handleMessage(message);
    }

    private static void handleMessage(Component message) {
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
