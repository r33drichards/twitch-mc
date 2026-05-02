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
 * HUD element showing active cron jobs (scheduled tasks).
 * Reads /tmp/sleet1213-crons.json (written by the PostToolUse hook on CronCreate/CronDelete).
 * Renders below TodoHud and above inventory overlay in the bottom-left column.
 *
 * Stacking order (bottom to top): Inventory → Crons → Todo
 */
public class CronHud extends HudElement {

    /** Exposed so TodoHud can position itself above this element. */
    public static int lastRenderedTop = 0;
    private static final HudGroup GROUP = new HudGroup("BtoneC");

    public static final HudElementInfo<CronHud> INFO = new HudElementInfo<>(
        GROUP,
        "cron-status",
        "Shows active cron/scheduled jobs.",
        CronHud::new
    );

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String CRON_FILE = "/tmp/sleet1213-crons.json";

    // Colors — high contrast for stream
    private static final Color TITLE_COLOR    = new Color(140, 220, 255);  // Bright cyan
    private static final Color ACTIVE_COLOR   = new Color(120, 255, 120);  // Bright green
    private static final Color SCHEDULE_COLOR = new Color(255, 220, 100);  // Bright yellow
    private static final Color DURABLE_COLOR  = new Color(200, 160, 255);  // Light purple
    private static final Color DIM_COLOR      = new Color(160, 160, 160);  // Gray
    private static final Color BG_COLOR       = new Color(0, 0, 0, 200);   // Near-black

    private static final double LINE_HEIGHT = 18;
    private static final double PADDING = 10;
    private static final double WIDTH = 400;
    private static final double TEXT_SCALE = 1.5;

    private JsonNode cachedCrons = null;
    private long lastReadTime = 0;
    private static final long CACHE_MS = 2000;

    public CronHud() {
        super(INFO);
        setSize(WIDTH, PADDING * 2 + LINE_HEIGHT);
    }

    @Override
    public void tick(HudRenderer renderer) {
        long now = System.currentTimeMillis();
        if (now - lastReadTime > CACHE_MS) {
            lastReadTime = now;
            try {
                File f = new File(CRON_FILE);
                if (f.exists()) {
                    String content = Files.readString(Paths.get(CRON_FILE));
                    cachedCrons = MAPPER.readTree(content);
                } else {
                    cachedCrons = null;
                }
            } catch (Exception e) {
                cachedCrons = null;
            }
        }

        // Calculate height
        double height = PADDING * 2 + LINE_HEIGHT; // title row
        if (cachedCrons != null && cachedCrons.has("crons")) {
            int count = cachedCrons.get("crons").size();
            if (count > 0) {
                height += LINE_HEIGHT * Math.min(count, 10); // cap at 10 lines
            }
        }

        setSize(WIDTH, height);
    }

    @Override
    public void render(HudRenderer renderer) {
        // Update static position so TodoHud can stack above us
        lastRenderedTop = this.y;

        renderer.quad(this.x, this.y, getWidth(), getHeight(), BG_COLOR);

        double yOff = this.y + PADDING;

        if (cachedCrons == null || !cachedCrons.has("crons")) {
            renderer.text("Crons: none", this.x + PADDING, yOff, DIM_COLOR, true, TEXT_SCALE);
            return;
        }

        JsonNode crons = cachedCrons.get("crons");
        int count = crons.size();

        // Title
        String title = "Crons: " + count;
        renderer.text(title, this.x + PADDING, yOff, TITLE_COLOR, true, TEXT_SCALE);
        yOff += LINE_HEIGHT;

        // List jobs
        int lines = 0;
        for (JsonNode cron : crons) {
            if (lines >= 10) break;

            String cronExpr = cron.path("cron").asText("?");
            String prompt = cron.path("prompt").asText("?");
            boolean durable = cron.path("durable").asBoolean(false);

            // Truncate prompt for display
            if (prompt.length() > 35) {
                prompt = prompt.substring(0, 32) + "...";
            }

            // Format: schedule indicator + cron expression + short description
            String durableTag = durable ? " [D]" : "";
            String line = cronExpr + " " + prompt + durableTag;

            // Truncate the whole line
            if (line.length() > 50) {
                line = line.substring(0, 47) + "...";
            }

            Color lineColor = durable ? DURABLE_COLOR : ACTIVE_COLOR;
            renderer.text(line, this.x + PADDING, yOff, lineColor, true, TEXT_SCALE);
            yOff += LINE_HEIGHT;
            lines++;
        }
    }
}
