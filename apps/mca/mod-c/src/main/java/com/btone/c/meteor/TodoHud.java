package com.btone.c.meteor;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import meteordevelopment.meteorclient.systems.hud.HudElement;
import meteordevelopment.meteorclient.systems.hud.HudElementInfo;
import meteordevelopment.meteorclient.systems.hud.HudGroup;
import meteordevelopment.meteorclient.systems.hud.HudRenderer;
import meteordevelopment.meteorclient.utils.render.color.Color;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * HUD element showing the agent's current todo list.
 * Reads /tmp/sleet1213-todos.json (written by the PostToolUse hook on TodoWrite).
 * Renders above the CronHud in the bottom-left column.
 *
 * Stacking order (bottom to top): Inventory → Crons → Todo
 * Uses absolute positioning to avoid Meteor auto-anchor issues.
 */
public class TodoHud extends HudElement {

    /** Exposed so CronHud can stack below this element. */
    public static int lastRenderedBottom = 0;
    private static final HudGroup GROUP = new HudGroup("BtoneC");

    public static final HudElementInfo<TodoHud> INFO = new HudElementInfo<>(
        GROUP,
        "todo-list",
        "Shows the agent's current task list.",
        TodoHud::new
    );

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String TODO_FILE = "/tmp/sleet1213-todos.json";

    // Colors — high contrast for stream legibility
    private static final Color TITLE_COLOR    = new Color(255, 200, 60);   // Gold
    private static final Color PROGRESS_COLOR = new Color(100, 200, 255);  // Bright blue
    private static final Color PENDING_COLOR  = new Color(180, 180, 180);  // Light gray
    private static final Color DONE_COLOR     = new Color(100, 220, 100);  // Bright green
    private static final Color BG_COLOR       = new Color(0, 0, 0, 200);   // Near-black

    private static final double LINE_HEIGHT = 12;
    private static final double PADDING = 5;
    private static final double WIDTH = 200;

    private JsonNode cachedTodos = null;
    private long lastReadTime = 0;
    private static final long CACHE_MS = 2000;

    public TodoHud() {
        super(INFO);
        setSize(WIDTH, PADDING * 2 + LINE_HEIGHT); // minimum: title only
    }

    @Override
    public void tick(HudRenderer renderer) {
        long now = System.currentTimeMillis();
        if (now - lastReadTime > CACHE_MS) {
            lastReadTime = now;
            try {
                File f = new File(TODO_FILE);
                if (f.exists()) {
                    String content = Files.readString(Paths.get(TODO_FILE));
                    cachedTodos = MAPPER.readTree(content);
                } else {
                    cachedTodos = null;
                }
            } catch (Exception e) {
                cachedTodos = null;
            }
        }

        // Calculate height based on content
        double height = PADDING * 2 + LINE_HEIGHT; // title row
        if (cachedTodos != null && cachedTodos.has("todos")) {
            JsonNode todos = cachedTodos.get("todos");
            int visibleCount = 0;
            for (JsonNode todo : todos) {
                String status = todo.path("status").asText("");
                // Show in_progress and pending items; skip completed
                if ("in_progress".equals(status) || "pending".equals(status)) {
                    visibleCount++;
                }
            }
            if (visibleCount > 0) {
                height += LINE_HEIGHT * Math.min(visibleCount, 8); // cap at 8 lines
            }
        }

        setSize(WIDTH, height);
    }

    @Override
    public void render(HudRenderer renderer) {
        // Use CronHud's rendered bottom as our anchor — we render ABOVE it.
        // Stacking order (bottom to top): Inventory → Crons → Todo
        int renderY = CronHud.lastRenderedTop - (int) getHeight() - 2;
        if (renderY < 0) renderY = this.y; // fallback if cron hasn't rendered yet
        int renderX = this.x;

        // Background
        renderer.quad(renderX, renderY, getWidth(), getHeight(), BG_COLOR);

        // Update static so other elements can reference our position
        lastRenderedBottom = renderY + (int) getHeight();

        double yOff = renderY + PADDING;

        if (cachedTodos == null || !cachedTodos.has("todos")) {
            renderer.text("Todo: idle", renderX + PADDING, yOff, PENDING_COLOR, true);
            return;
        }

        JsonNode todos = cachedTodos.get("todos");

        // Count by status
        int inProgress = 0, pending = 0, completed = 0;
        for (JsonNode todo : todos) {
            String status = todo.path("status").asText("");
            switch (status) {
                case "in_progress": inProgress++; break;
                case "pending":     pending++;    break;
                case "completed":   completed++;  break;
            }
        }
        int total = inProgress + pending + completed;

        // Title line with progress
        String title = "Todo " + completed + "/" + total;
        renderer.text(title, renderX + PADDING, yOff, TITLE_COLOR, true);
        yOff += LINE_HEIGHT;

        // List in_progress items first, then pending
        int lines = 0;
        for (JsonNode todo : todos) {
            if (lines >= 8) break;
            String status = todo.path("status").asText("");
            if (!"in_progress".equals(status)) continue;

            String text = todo.path("activeForm").asText(
                todo.path("content").asText("?"));
            String indicator = "▶ "; // ▶
            String line = indicator + truncate(text, 26);
            renderer.text(line, renderX + PADDING, yOff, PROGRESS_COLOR, true);
            yOff += LINE_HEIGHT;
            lines++;
        }

        for (JsonNode todo : todos) {
            if (lines >= 8) break;
            String status = todo.path("status").asText("");
            if (!"pending".equals(status)) continue;

            String text = todo.path("content").asText("?");
            String indicator = "○ "; // ○
            String line = indicator + truncate(text, 26);
            renderer.text(line, renderX + PADDING, yOff, PENDING_COLOR, true);
            yOff += LINE_HEIGHT;
            lines++;
        }
    }

    private static String truncate(String s, int max) {
        if (s.length() <= max) return s;
        return s.substring(0, max - 3) + "...";
    }
}
