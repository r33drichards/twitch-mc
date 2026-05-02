package com.btone.c.meteor;

import meteordevelopment.meteorclient.settings.*;
import meteordevelopment.meteorclient.systems.hud.HudElement;
import meteordevelopment.meteorclient.systems.hud.HudElementInfo;
import meteordevelopment.meteorclient.systems.hud.HudGroup;
import meteordevelopment.meteorclient.systems.hud.HudRenderer;
import meteordevelopment.meteorclient.utils.render.color.Color;
import meteordevelopment.meteorclient.utils.render.color.SettingColor;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;

/**
 * Compact inventory overlay HUD element — shows all 36 inventory slots
 * (27 main + 9 hotbar) in a grid in the bottom-left corner of the screen.
 * Designed for the stream so viewers can see the bot's inventory at a glance
 * without opening the inventory screen.
 */
public class InventoryOverlayHud extends HudElement {
    private static final HudGroup GROUP = new HudGroup("BtoneC");

    public static final HudElementInfo<InventoryOverlayHud> INFO =
        new HudElementInfo<>(GROUP, "inventory-overlay",
            "Shows the player's inventory as a compact grid overlay.",
            InventoryOverlayHud::new);

    // 9 columns, 4 rows (3 main inv + 1 hotbar)
    private static final int COLS = 9;
    private static final int ROWS = 4;
    private static final int SLOT_SIZE = 18;  // 16px item + 2px padding
    private static final int PAD = 4;         // outer padding
    private static final int GAP = 2;         // gap between main inv and hotbar

    private final SettingGroup sgGeneral = settings.getDefaultGroup();

    private final Setting<Double> scale = sgGeneral.add(new DoubleSetting.Builder()
        .name("scale")
        .description("Scale of the inventory overlay.")
        .defaultValue(1.0)
        .range(0.5, 3.0)
        .sliderRange(0.5, 3.0)
        .onChanged(v -> calculateSize())
        .build());

    private final Setting<SettingColor> bgColor = sgGeneral.add(new ColorSetting.Builder()
        .name("background-color")
        .description("Background color of the overlay.")
        .defaultValue(new SettingColor(30, 30, 30, 180))
        .build());

    private final Setting<SettingColor> slotColor = sgGeneral.add(new ColorSetting.Builder()
        .name("slot-color")
        .description("Background color of each item slot.")
        .defaultValue(new SettingColor(50, 50, 50, 160))
        .build());

    private final Setting<SettingColor> hotbarSlotColor = sgGeneral.add(new ColorSetting.Builder()
        .name("hotbar-slot-color")
        .description("Background color of hotbar slots.")
        .defaultValue(new SettingColor(70, 60, 40, 160))
        .build());

    private final Setting<Boolean> showHotbar = sgGeneral.add(new BoolSetting.Builder()
        .name("show-hotbar")
        .description("Also show the hotbar row below main inventory.")
        .defaultValue(true)
        .onChanged(v -> calculateSize())
        .build());

    public InventoryOverlayHud() {
        super(INFO);
        calculateSize();
    }

    private void calculateSize() {
        double s = scale.get();
        int rows = showHotbar.get() ? ROWS : (ROWS - 1);
        int extraGap = showHotbar.get() ? GAP : 0;
        double w = (PAD * 2 + COLS * SLOT_SIZE) * s;
        double h = (PAD * 2 + rows * SLOT_SIZE + extraGap) * s;
        setSize(w, h);
    }

    @Override
    public void render(HudRenderer renderer) {
        double s = scale.get();
        double x = this.x;
        double y = this.y;

        int rows = showHotbar.get() ? ROWS : (ROWS - 1);
        int extraGap = showHotbar.get() ? GAP : 0;
        double totalW = (PAD * 2 + COLS * SLOT_SIZE) * s;
        double totalH = (PAD * 2 + rows * SLOT_SIZE + extraGap) * s;

        // Draw background
        renderer.quad(x, y, totalW, totalH, bgColor.get());

        MinecraftClient mc = MinecraftClient.getInstance();
        ClientPlayerEntity player = mc == null ? null : mc.player;
        PlayerInventory inv = player == null ? null : player.getInventory();

        // Draw slot backgrounds and items
        for (int row = 0; row < rows; row++) {
            for (int col = 0; col < COLS; col++) {
                // Calculate slot index
                // Rows 0-2 = main inv (slots 9-35), Row 3 = hotbar (slots 0-8)
                int slotIndex;
                boolean isHotbar;
                if (row < 3) {
                    slotIndex = 9 + row * 9 + col; // main inventory
                    isHotbar = false;
                } else {
                    slotIndex = col; // hotbar
                    isHotbar = true;
                }

                double slotX = x + (PAD + col * SLOT_SIZE) * s;
                double slotY;
                if (isHotbar) {
                    slotY = y + (PAD + 3 * SLOT_SIZE + GAP) * s;
                } else {
                    slotY = y + (PAD + row * SLOT_SIZE) * s;
                }

                double slotW = (SLOT_SIZE - 1) * s;  // -1 for grid line effect
                double slotH = (SLOT_SIZE - 1) * s;

                // Slot background
                Color slotBg = isHotbar ? hotbarSlotColor.get() : slotColor.get();
                renderer.quad(slotX, slotY, slotW, slotH, slotBg);

                // Draw item if player is in world
                if (inv != null) {
                    ItemStack stack = inv.getStack(slotIndex);
                    if (stack != null && !stack.isEmpty()) {
                        renderer.item(stack,
                            (int)(slotX + 1 * s),
                            (int)(slotY + 1 * s),
                            (float) s, true);
                    }
                }
            }
        }
    }
}
