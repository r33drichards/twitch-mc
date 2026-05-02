package com.btone.c.meteor;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import meteordevelopment.meteorclient.systems.hud.HudElement;
import meteordevelopment.meteorclient.systems.hud.HudElementInfo;
import meteordevelopment.meteorclient.systems.hud.HudGroup;
import meteordevelopment.meteorclient.systems.hud.HudRenderer;
import meteordevelopment.meteorclient.utils.render.color.Color;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * HUD element showing active agent cron jobs with high-contrast design.
 * Follows WCAG AAA contrast guidelines (7:1 ratio) for optimal legibility.
 */
public class AgentStatusHud extends HudElement {
    private static final HudGroup GROUP = new HudGroup("BtoneC");

    public static final HudElementInfo<AgentStatusHud> INFO = new HudElementInfo<>(
        GROUP,
        "agent-status",
        "Shows active agent cron jobs (high contrast).",
        AgentStatusHud::new
    );

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String STATUS_FILE = "/tmp/sleet1213-agent-status.json";

    // High-contrast colors (WCAG AAA compliant: 7:1+ ratio)
    private static final Color TITLE_COLOR = new Color(140, 220, 255);  // Bright cyan
    private static final Color TEXT_COLOR = new Color(255, 255, 255);   // Pure white
    private static final Color ERROR_COLOR = new Color(255, 120, 120);  // Bright red
    private static final Color SUCCESS_COLOR = new Color(120, 255, 120); // Bright green
    private static final Color WARNING_COLOR = new Color(255, 220, 100); // Bright yellow
    private static final Color IDLE_COLOR = new Color(180, 180, 180);   // Light gray
    private static final Color BG_COLOR = new Color(0, 0, 0, 220);      // Near-black, high opacity

    private JsonNode cachedStatus = null;
    private long lastReadTime = 0;
    private static final long CACHE_MS = 2000;

    public AgentStatusHud() {
        super(INFO);
        setSize(230, 50);
    }

    @Override
    public void tick(HudRenderer renderer) {
        long now = System.currentTimeMillis();
        if (now - lastReadTime > CACHE_MS) {
            lastReadTime = now;
            try {
                File statusFile = new File(STATUS_FILE);
                if (statusFile.exists()) {
                    String content = Files.readString(Paths.get(STATUS_FILE));
                    cachedStatus = MAPPER.readTree(content);
                } else {
                    cachedStatus = null;
                }
            } catch (IOException e) {
                cachedStatus = null;
            }
        }

        // Increased line height for better spacing
        double lineHeight = 14;
        double width = 230;
        double padding = 6;
        double height = padding * 2 + lineHeight * 2; // Title + header

        if (cachedStatus != null && cachedStatus.has("crons")) {
            JsonNode crons = cachedStatus.get("crons");
            height += lineHeight * Math.min(crons.size(), 3); // Max 3 cron lines
        } else {
            height += lineHeight;
        }

        setSize(width, height);
    }

    @Override
    public void render(HudRenderer renderer) {
        double lineHeight = 14;
        double padding = 6;

        // High-opacity background for strong contrast
        renderer.quad(this.x, this.y, getWidth(), getHeight(), BG_COLOR);

        double yOffset = this.y + padding;

        // Title - larger and brighter
        renderer.text("Agent Status", this.x + padding, yOffset, TITLE_COLOR, true); // shadow=true
        yOffset += lineHeight;

        if (cachedStatus == null) {
            renderer.text("⭘ Offline", this.x + padding, yOffset, ERROR_COLOR, true);
            return;
        }

        if (cachedStatus.has("crons")) {
            JsonNode crons = cachedStatus.get("crons");

            // Header with count
            String header = "Crons: " + crons.size();
            renderer.text(header, this.x + padding, yOffset, TEXT_COLOR, true);
            yOffset += lineHeight;

            int count = 0;
            for (JsonNode cron : crons) {
                if (count >= 3) break; // Compact: max 3 lines

                String schedule = cron.path("schedule").asText("?").replace("Every ", "");
                String desc = cron.path("description").asText("?");
                String state = cron.path("state").asText("idle");
                String nextRun = cron.path("nextRun").asText("");

                // Aggressive truncation for compactness
                if (desc.length() > 18) {
                    desc = desc.substring(0, 15) + "...";
                }

                // State indicator
                String indicator;
                Color stateColor;
                switch (state.toLowerCase()) {
                    case "running":
                        indicator = "▶";
                        stateColor = SUCCESS_COLOR;
                        break;
                    case "failed":
                    case "error":
                        indicator = "✖";
                        stateColor = ERROR_COLOR;
                        break;
                    case "waiting":
                    case "scheduled":
                        indicator = "⏱";
                        stateColor = WARNING_COLOR;
                        break;
                    default:
                        indicator = "⏸";
                        stateColor = IDLE_COLOR;
                        break;
                }

                // Compact format: indicator + schedule + nextRun
                String line = indicator + " " + schedule + " " + desc;
                if (!nextRun.isEmpty()) {
                    line += " @" + nextRun;
                }
                renderer.text(line, this.x + padding, yOffset, stateColor, true); // shadow=true
                yOffset += lineHeight;
                count++;
            }
        } else {
            renderer.text("No data", this.x + padding, yOffset, IDLE_COLOR, true);
        }
    }
}
