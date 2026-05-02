package com.btone.c.meteor;

import com.btone.c.events.EventBus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.Map;

/**
 * Utility for Meteor modules to send notifications to the agent.
 *
 * Emits directly to the {@link EventBus} as {@code event: chat} so that
 * the mc-bridge SSE subscriber picks them up and forwards to the agent
 * session.  Previous approach of bouncing through server chat failed
 * because 1.21+ chat signing means sendChatMessage() is fire-and-forget
 * — the server never echoes the message back to
 * ClientReceiveMessageEvents.GAME.
 */
public final class AgentNotifier {
    private static final Logger LOG = LoggerFactory.getLogger("btone-c");
    private static volatile EventBus bus;

    private AgentNotifier() {}

    /** Called once from BtoneC during init. */
    public static void init(EventBus eventBus) {
        bus = eventBus;
    }

    /**
     * Send a notification to the agent.
     * @param message The notification message
     * @param priority "high", "normal", or "low"
     */
    public static void notify(String message, String priority) {
        if (message == null || message.trim().isEmpty()) {
            LOG.warn("AgentNotifier.notify: empty message, skipping");
            return;
        }
        if (bus == null) {
            LOG.warn("AgentNotifier.notify: bus is NULL, cannot emit. Message: {}", message);
            return;
        }

        String priorityPrefix = priority.equals("high") ? "⚠️ " :
                              priority.equals("low") ? "ℹ️ " : "📋 ";

        String text = "[btone] " + priorityPrefix + message;

        LOG.info("AgentNotifier.notify: emitting chat event: {}", text);
        Map<String, Object> payload = new HashMap<>();
        payload.put("text", text);
        payload.put("overlay", false);
        bus.emit("chat", payload);
        LOG.info("AgentNotifier.notify: emitted successfully, bus subscribers={}", bus.subscriberCount());
    }

    /**
     * Send a normal priority notification.
     */
    public static void notify(String message) {
        notify(message, "normal");
    }
}
