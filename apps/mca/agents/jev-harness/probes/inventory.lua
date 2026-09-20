-- inventoryJson returns a JSON *string* of the 36 main slots (0-8 hotbar),
-- non-empty stacks only; the harness parses it. Equipped armour is NOT in
-- here — getNonEquipmentItems() excludes the armour and offhand slots.
return api:inventoryJson()
