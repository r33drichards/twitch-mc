package com.btone.c.handlers;

import com.btone.c.ClientThread;
import com.btone.c.rpc.RpcRouter;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * RPC handlers for querying agent status (cron jobs, tasks, etc).
 * Polls status files written by the agent or calls agent webhook.
 */
public final class AgentStatusHandlers {
    private static final ObjectMapper M = new ObjectMapper();
    private static final String AGENT_STATUS_FILE = "/tmp/sleet1213-agent-status.json";

    private AgentStatusHandlers() {}

    public static void registerAll(RpcRouter r) {
        // Get agent cron jobs and active tasks
        // Returns: {crons: [{id, schedule, description, nextRun}], tasks: []}
        r.register("agent.status", params -> ClientThread.call(1_000, () -> {
            ObjectNode result = M.createObjectNode();

            // Try to read status file written by agent
            try {
                File statusFile = new File(AGENT_STATUS_FILE);
                if (statusFile.exists()) {
                    String content = Files.readString(Paths.get(AGENT_STATUS_FILE));
                    JsonNode status = M.readTree(content);
                    return (ObjectNode) status;
                }
            } catch (Exception ignored) {
                // If file doesn't exist or is invalid, return empty status
            }

            // Return empty status if file not available
            result.putArray("crons");
            result.putArray("tasks");
            result.put("available", false);
            result.put("message", "Agent status file not found at " + AGENT_STATUS_FILE);
            return result;
        }));
    }
}
