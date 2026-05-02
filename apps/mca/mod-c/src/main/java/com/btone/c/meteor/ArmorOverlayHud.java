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
import net.minecraft.entity.EquipmentSlot;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;

/**
 * Armor overlay HUD element — shows the 4 equipped armor pieces
 * (helmet, chestplate, leggings, boots) in a vertical column.
 * Designed to appear next to the inventory overlay so viewers can see
 * the bot's armor at a glance.
 */
public class ArmorOverlayHud extends HudElement {
    private static final HudGroup GROUP = new HudGroup("BtoneC");

    public static final HudElementInfo<ArmorOverlayHud> INFO =
        new HudElementInfo<>(GROUP, "armor-overlay",
            "Shows equipped armor pieces in a vertical display.",
            ArmorOverlayHud::new);

    private static final int SLOT_SIZE = 18;  // 16px item + 2px padding
    private static final int PAD = 4;         // outer padding

    private final SettingGroup sgGeneral = settings.getDefaultGroup();

    private final Setting<Double> scale = sgGeneral.add(new DoubleSetting.Builder()
        .name("scale")
        .description("Scale of the armor overlay.")
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
        .description("Background color of each armor slot.")
        .defaultValue(new SettingColor(50, 50, 80, 160))
        .build());

    private final Setting<Boolean> showDurability = sgGeneral.add(new BoolSetting.Builder()
        .name("show-durability")
        .description("Show durability bar below each armor piece.")
        .defaultValue(true)
        .build());

    private final Setting<SettingColor> durabilityGoodColor = sgGeneral.add(new ColorSetting.Builder()
        .name("durability-good")
        .description("Durability bar color when armor is in good condition.")
        .defaultValue(new SettingColor(0, 255, 0, 200))
        .build());

    private final Setting<SettingColor> durabilityMediumColor = sgGeneral.add(new ColorSetting.Builder()
        .name("durability-medium")
        .description("Durability bar color when armor is half-damaged.")
        .defaultValue(new SettingColor(255, 255, 0, 200))
        .build());

    private final Setting<SettingColor> durabilityLowColor = sgGeneral.add(new ColorSetting.Builder()
        .name("durability-low")
        .description("Durability bar color when armor is nearly broken.")
        .defaultValue(new SettingColor(255, 0, 0, 200))
        .build());

    public ArmorOverlayHud() {
        super(INFO);
        calculateSize();
    }

    private void calculateSize() {
        double s = scale.get();
        // 1 column, 4 rows (helmet, chestplate, leggings, boots)
        double w = (PAD * 2 + SLOT_SIZE) * s;
        double h = (PAD * 2 + 4 * SLOT_SIZE) * s;
        setSize(w, h);
    }

    @Override
    public void render(HudRenderer renderer) {
        double s = scale.get();
        double x = this.x;
        double y = this.y;

        double totalW = (PAD * 2 + SLOT_SIZE) * s;
        double totalH = (PAD * 2 + 4 * SLOT_SIZE) * s;

        // Draw background
        renderer.quad(x, y, totalW, totalH, bgColor.get());

        MinecraftClient mc = MinecraftClient.getInstance();
        ClientPlayerEntity player = mc == null ? null : mc.player;

        // Armor slots in order: HEAD, CHEST, LEGS, FEET
        EquipmentSlot[] armorSlots = {
            EquipmentSlot.HEAD,
            EquipmentSlot.CHEST,
            EquipmentSlot.LEGS,
            EquipmentSlot.FEET
        };

        for (int i = 0; i < 4; i++) {
            double slotX = x + PAD * s;
            double slotY = y + (PAD + i * SLOT_SIZE) * s;
            double slotW = (SLOT_SIZE - 1) * s;
            double slotH = (SLOT_SIZE - 1) * s;

            // Slot background
            renderer.quad(slotX, slotY, slotW, slotH, slotColor.get());

            // Draw armor item if player is in world
            if (player != null) {
                ItemStack stack = player.getEquippedStack(armorSlots[i]);
                if (stack != null && !stack.isEmpty()) {
                    // Render item
                    renderer.item(stack,
                        (int)(slotX + 1 * s),
                        (int)(slotY + 1 * s),
                        (float) s, true);

                    // Draw durability bar if enabled and item has durability
                    if (showDurability.get() && stack.isDamageable()) {
                        int maxDamage = stack.getMaxDamage();
                        int damage = stack.getDamage();
                        double durabilityPercent = 1.0 - ((double) damage / maxDamage);

                        // Choose color based on durability
                        Color durColor;
                        if (durabilityPercent > 0.66) {
                            durColor = durabilityGoodColor.get();
                        } else if (durabilityPercent > 0.33) {
                            durColor = durabilityMediumColor.get();
                        } else {
                            durColor = durabilityLowColor.get();
                        }

                        // Draw durability bar below item
                        double barX = slotX + 2 * s;
                        double barY = slotY + slotH - 3 * s;
                        double barMaxWidth = (SLOT_SIZE - 5) * s;
                        double barWidth = barMaxWidth * durabilityPercent;
                        double barHeight = 2 * s;

                        // Black background for bar
                        renderer.quad(barX, barY, barMaxWidth, barHeight, new Color(0, 0, 0, 200));
                        // Colored durability fill
                        if (barWidth > 0) {
                            renderer.quad(barX, barY, barWidth, barHeight, durColor);
                        }
                    }
                }
            }
        }
    }
}
