"""Item type identifiers and compact descriptors (mirror of ``common/items``).

Only the parts the local server needs are mirrored: the item type table, the
compact-descriptor arithmetic and the ``GUI_ITEM_TYPE`` indexes the account sync
data is keyed by.  The formulas are copied from the supported client so that a
descriptor produced here is accepted there and vice versa.
"""

from __future__ import annotations

ITEM_TYPE_NAMES = (
    "_reserved",
    "vehicle",
    "vehicleChassis",
    "vehicleTurret",
    "vehicleGun",
    "vehicleEngine",
    "vehicleFuelTank",
    "vehicleRadio",
    "tankman",
    "optionalDevice",
    "shell",
    "equipment",
    "customizationItem",
)

ITEM_TYPES = {name: index for index, name in enumerate(ITEM_TYPE_NAMES) if not name.startswith("_")}
ITEM_TYPE_INDICES = ITEM_TYPES

SIMPLE_ITEM_TYPE_NAMES = (
    "vehicleChassis",
    "vehicleTurret",
    "vehicleGun",
    "vehicleEngine",
    "vehicleFuelTank",
    "vehicleRadio",
    "optionalDevice",
    "shell",
    "equipment",
)
SIMPLE_ITEM_TYPE_INDICES = tuple(ITEM_TYPE_INDICES[name] for name in SIMPLE_ITEM_TYPE_NAMES)
VEHICLE_COMPONENT_TYPE_NAMES = (
    "vehicleChassis",
    "vehicleTurret",
    "vehicleGun",
    "vehicleEngine",
    "vehicleFuelTank",
    "vehicleRadio",
)
VEHICLE_COMPONENT_TYPE_INDICES = tuple(ITEM_TYPE_INDICES[name] for name in VEHICLE_COMPONENT_TYPE_NAMES)

# ``gui.shared.gui_items.GUI_ITEM_TYPE`` uses the same numbering for the item
# types that reach the inventory plus four extra pseudo types at the end.
class GUI_ITEM_TYPE:
    VEHICLE = 1
    CHASSIS = 2
    TURRET = 3
    GUN = 4
    ENGINE = 5
    FUEL_TANK = 6
    RADIO = 7
    TANKMAN = 8
    OPTIONAL_DEVICE = 9
    SHELL = 10
    EQUIPMENT = 11
    CUSTOMIZATION = 12
    OUTFIT = 13
    VEHICLE_DOSSIER = 14
    ACCOUNT_DOSSIER = 15
    BADGE = 16

    VEHICLE_MODULES = (CHASSIS, TURRET, GUN, ENGINE, FUEL_TANK, RADIO)
    MODULES = VEHICLE_MODULES
    ARTEFACTS = (OPTIONAL_DEVICE, SHELL, EQUIPMENT)


def make_int_compact_descr_by_id(item_type_name: str, nation_id: int, item_id: int) -> int:
    if not 0 <= item_id <= 65535:
        raise ValueError("item id out of range: %r" % (item_id,))
    if not 0 <= nation_id <= 15:
        raise ValueError("nation id out of range: %r" % (nation_id,))
    item_type_id = ITEM_TYPES[item_type_name]
    if item_type_id <= 15:
        header = item_type_id + (nation_id << 4)
        return (item_id << 8) + header
    if item_type_id <= 255:
        header = nation_id << 4
        return (item_type_id << 24) + (item_id << 8) + header
    raise AssertionError(item_type_id)


def make_vehicle_compact_descr(nation_id: int, vehicle_type_id: int) -> int:
    return make_int_compact_descr_by_id("vehicle", nation_id, vehicle_type_id)


def parse_int_compact_descr(compact_descr: int):
    item_type_id = compact_descr & 15
    if item_type_id == 0:
        item_type_id = (compact_descr >> 24) & 255
    return item_type_id, (compact_descr >> 4) & 15, (compact_descr >> 8) & 65535


def get_type_of_compact_descr(compact_descr) -> int:
    if isinstance(compact_descr, int):
        item_type_id = compact_descr & 15
        if item_type_id == 0:
            item_type_id = (compact_descr >> 24) & 255
    else:
        item_type_id = ord(compact_descr[0:1]) & 15
        if item_type_id == 0:
            item_type_id = ord(compact_descr[1:2])
    if item_type_id >= len(ITEM_TYPE_NAMES):
        raise ValueError("value is not a compact descriptor: %r" % (compact_descr,))
    return item_type_id
