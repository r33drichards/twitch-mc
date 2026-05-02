package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import net.minecraft.client.MinecraftClient;
import net.minecraft.text.Text;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * RPC handlers for sending notifications to the Claude agent.
 * Allows Meteor modules to push async alerts to the agent's inbox.
 *
 * Uses Minecraft chat to send notifications that get picked up by the IRC bridge.
 * Includes rate limiting and deduplication to prevent context flooding.
 */
public final class AgentHandlers {
    private static final ObjectMapper M = new ObjectMapper();

    // Rate limiting: max 5 notifications per minute
    private static final int MAX_NOTIFICATIONS_PER_MINUTE = 5;
    private static final long RATE_LIMIT_WINDOW_MS = 60_000;

    // Deduplication: don't send same message within 5 minutes
    private static final long DEDUP_WINDOW_MS = 300_000;

    // Track recent notifications for rate limiting and deduplication
    private static final java.util.concurrent.ConcurrentHashMap<String, Long> recentMessages =
        new java.util.concurrent.ConcurrentHashMap<>();
    private static final java.util.concurrent.ConcurrentLinkedQueue<Long> recentTimestamps =
        new java.util.concurrent.ConcurrentLinkedQueue<>();

    private AgentHandlers() {}

    public static void registerAll(RpcRouter r) {
        // Send a notification to the agent via Minecraft chat (picked up by IRC bridge)
        // Params: {message: string, priority?: "high"|"normal"|"low", dedupe?: boolean}
        // Returns: {sent: boolean, method: string, rateLimit?: object}
        r.register("agent.notify", params -> ClientThread.call(2_000, () -> {
            String message = params.get("message").asText();
            String priority = params.path("priority").asText("normal");
            boolean dedupe = params.path("dedupe").asBoolean(true);

            if (message == null || message.trim().isEmpty()) {
                throw new IllegalArgumentException("message is required");
            }

            long now = System.currentTimeMillis();

            // Clean up old timestamps (older than rate limit window)
            while (!recentTimestamps.isEmpty() &&
                   now - recentTimestamps.peek() > RATE_LIMIT_WINDOW_MS) {
                recentTimestamps.poll();
            }

            // Clean up old dedupe entries
            recentMessages.entrySet().removeIf(entry ->
                now - entry.getValue() > DEDUP_WINDOW_MS);

            // Check deduplication (skip if same message sent recently)
            if (dedupe) {
                Long lastSent = recentMessages.get(message);
                if (lastSent != null && now - lastSent < DEDUP_WINDOW_MS) {
                    ObjectNode result = M.createObjectNode();
                    result.put("sent", false);
                    result.put("reason", "deduplicated");
                    result.put("lastSentMs", now - lastSent);
                    return result;
                }
            }

            // Check rate limit (max notifications per minute)
            if (recentTimestamps.size() >= MAX_NOTIFICATIONS_PER_MINUTE) {
                ObjectNode result = M.createObjectNode();
                result.put("sent", false);
                result.put("reason", "rate_limited");
                ObjectNode rateLimitInfo = result.putObject("rateLimit");
                rateLimitInfo.put("max", MAX_NOTIFICATIONS_PER_MINUTE);
                rateLimitInfo.put("windowMs", RATE_LIMIT_WINDOW_MS);
                rateLimitInfo.put("current", recentTimestamps.size());
                return result;
            }

            var mc = MinecraftClient.getInstance();
            var player = mc.player;

            if (player == null || mc.getNetworkHandler() == null) {
                ObjectNode result = M.createObjectNode();
                result.put("sent", false);
                result.put("error", "player not in world");
                return result;
            }

            // Send notification via AgentNotifier (includes @mention for IRC bridge)
            try {
                com.btone.c.meteor.AgentNotifier.notify(message, priority);
                String chatMessage = "@sleet1213 " +
                    (priority.equals("high") ? "⚠️ " : priority.equals("low") ? "ℹ️ " : "📋 ") +
                    message;

                // Record notification for rate limiting and deduplication
                recentTimestamps.add(now);
                if (dedupe) {
                    recentMessages.put(message, now);
                }

                ObjectNode result = M.createObjectNode();
                result.put("sent", true);
                result.put("method", "chat-to-irc");
                result.put("message", chatMessage);
                ObjectNode rateLimitInfo = result.putObject("rateLimit");
                rateLimitInfo.put("remaining", MAX_NOTIFICATIONS_PER_MINUTE - recentTimestamps.size());
                rateLimitInfo.put("max", MAX_NOTIFICATIONS_PER_MINUTE);
                return result;
            } catch (Exception e) {
                ObjectNode result = M.createObjectNode();
                result.put("sent", false);
                result.put("error", e.getMessage());
                return result;
            }
        }));

        // Get current notification method info and rate limit status
        r.register("agent.notify_info", params -> ClientThread.call(1_000, () -> {
            long now = System.currentTimeMillis();

            // Clean up old timestamps
            while (!recentTimestamps.isEmpty() &&
                   now - recentTimestamps.peek() > RATE_LIMIT_WINDOW_MS) {
                recentTimestamps.poll();
            }

            ObjectNode result = M.createObjectNode();
            result.put("method", "chat-to-irc");
            result.put("description", "Notifications sent via Minecraft chat, forwarded by IRC bridge to agent");

            ObjectNode rateLimitInfo = result.putObject("rateLimit");
            rateLimitInfo.put("max", MAX_NOTIFICATIONS_PER_MINUTE);
            rateLimitInfo.put("windowMs", RATE_LIMIT_WINDOW_MS);
            rateLimitInfo.put("current", recentTimestamps.size());
            rateLimitInfo.put("remaining", MAX_NOTIFICATIONS_PER_MINUTE - recentTimestamps.size());

            ObjectNode dedupeInfo = result.putObject("deduplication");
            dedupeInfo.put("windowMs", DEDUP_WINDOW_MS);
            dedupeInfo.put("trackedMessages", recentMessages.size());

            return result;
        }));
    }
}
