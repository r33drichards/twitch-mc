"""Never trade a netherite sword for a wooden axe.

Live, Jev answered hotbar_weapon for a wooden_axe while a netherite_sword sat
in slot 1, at 0.72 confidence. Which of two weapons is better is not a
judgement call — it is a table — so the action refuses a downgrade rather than
asking.
"""
import unittest

from gear import armor_slot, better_armor, better_weapon, is_armor, is_weapon


class TestWeapons(unittest.TestCase):
    def test_material_beats_material(self):
        self.assertTrue(better_weapon("minecraft:diamond_sword", "minecraft:iron_sword"))
        self.assertFalse(better_weapon("minecraft:wooden_axe", "minecraft:netherite_sword"))

    def test_a_sword_beats_an_axe_of_the_same_material(self):
        self.assertTrue(better_weapon("minecraft:iron_sword", "minecraft:iron_axe"))

    def test_gold_is_not_better_than_stone(self):
        self.assertFalse(better_weapon("minecraft:golden_sword", "minecraft:stone_sword"))

    def test_anything_beats_an_empty_hand(self):
        self.assertTrue(better_weapon("minecraft:wooden_sword", None))

    def test_the_same_weapon_is_not_an_upgrade(self):
        self.assertFalse(better_weapon("minecraft:iron_sword", "minecraft:iron_sword"))

    def test_what_counts_as_a_weapon(self):
        for weapon in ("minecraft:netherite_sword", "minecraft:stone_axe",
                       "minecraft:trident"):
            self.assertTrue(is_weapon(weapon), weapon)
        for other in ("minecraft:iron_pickaxe", "minecraft:oak_planks",
                      "minecraft:bread"):
            self.assertFalse(is_weapon(other), other)


class TestArmour(unittest.TestCase):
    def test_pieces_know_where_they_go(self):
        self.assertEqual(armor_slot("minecraft:iron_helmet"), "head")
        self.assertEqual(armor_slot("minecraft:diamond_boots"), "feet")
        self.assertIsNone(armor_slot("minecraft:oak_planks"))

    def test_better_material_wins_in_the_same_place(self):
        self.assertTrue(better_armor("minecraft:diamond_chestplate",
                                     "minecraft:leather_chestplate"))
        self.assertFalse(better_armor("minecraft:leather_helmet",
                                      "minecraft:netherite_helmet"))

    def test_a_helmet_never_replaces_boots(self):
        self.assertFalse(better_armor("minecraft:netherite_helmet",
                                      "minecraft:leather_boots"))

    def test_bare_skin_is_worse_than_anything(self):
        self.assertTrue(better_armor("minecraft:leather_boots", None))

    def test_what_counts_as_armour(self):
        self.assertTrue(is_armor("minecraft:chainmail_leggings"))
        self.assertFalse(is_armor("minecraft:iron_sword"))


if __name__ == "__main__":
    unittest.main()
