"""Which of two pieces of gear is better. A table, not a judgement.

Asked as a question, a model will sometimes answer that a wooden axe improves
on a netherite sword. Asked as a comparison, code always gets it right, so the
model chooses *what kind of thing* to do and this decides whether it is an
upgrade.
"""

# Damage order, worst first. Gold swings fast and hits soft; it sits below stone.
MATERIALS = ("wooden", "golden", "leather", "stone", "chainmail",
             "iron", "diamond", "netherite")
WEAPON_KINDS = {"sword": 3, "axe": 2, "trident": 3}
ARMOR_PLACES = {"helmet": "head", "chestplate": "chest",
                "leggings": "legs", "boots": "feet"}


def _parts(item_id):
    name = str(item_id or "").split(":")[-1]
    material = next((m for m in MATERIALS if name.startswith(m + "_")), None)
    kind = name.split("_")[-1] if "_" in name else name
    return material, kind, name


def is_weapon(item_id):
    _, kind, name = _parts(item_id)
    return kind in WEAPON_KINDS or name == "trident"


def is_armor(item_id):
    _, kind, _ = _parts(item_id)
    return kind in ARMOR_PLACES


def armor_slot(item_id):
    _, kind, _ = _parts(item_id)
    return ARMOR_PLACES.get(kind)


def _score(item_id, kinds):
    material, kind, _ = _parts(item_id)
    tier = MATERIALS.index(material) if material in MATERIALS else 0
    return tier * 10 + kinds.get(kind, 0)


def better_weapon(candidate, current):
    """True when `candidate` is a weapon worth putting in the hand instead."""
    if not is_weapon(candidate):
        return False
    if current is None or str(current).endswith("air") or not is_weapon(current):
        return True
    return _score(candidate, WEAPON_KINDS) > _score(current, WEAPON_KINDS)


def better_armor(candidate, current):
    """True when `candidate` should replace `current` on the same body part."""
    if not is_armor(candidate):
        return False
    if current is None or str(current).endswith("air"):
        return True
    if not is_armor(current) or armor_slot(candidate) != armor_slot(current):
        return False
    return _score(candidate, {}) > _score(current, {})
