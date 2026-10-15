"""The garage: modules, ammunition, equipment, devices, crew and repair.

Every command here is sent by the client's own helpers, so the argument order is
theirs (``client/account_helpers/Inventory.py`` / ``Shop.py``):

=================================  ===============================================
``CMD_EQUIP`` (101)                ``Int3 (vehInvID, itemCompDescr, gunCompDescr)``
                                   - a turret travels with the gun that goes with
                                   it, every other module with a trailing ``0``
``CMD_EQUIP_OPTDEV`` (102)         ``IntArr [shopRev, vehInvID, deviceCompDescr,
                                   slotIdx, isPaidRemoval]`` - a ``0`` descriptor
                                   takes the device off
``CMD_EQUIP_SHELLS`` (103)         ``IntArr [vehInvID, shell…]``
``CMD_EQUIP_EQS`` (104)            ``IntArr [vehInvID, eq1, eq2, eq3, booster]``
``CMD_EQUIP_TMAN`` (105)           ``Int3 (vehInvID, slot, tmanInvID)`` - ``-1``
                                   empties the seat
``CMD_REPAIR`` (106)               ``Int3 (vehInvID, 0, 0)``
``CMD_VEH_SETTINGS`` (107)         ``Int3 (vehInvID, setting, isOn)``
``CMD_SET_AND_FILL_LAYOUTS`` (108) ``IntArr [shopRev, vehInvID, len(shells),
                                   *shellsPairs, equipmentType, len(eqs),
                                   *eqsPairs]``
``CMD_BUY_AND_EQUIP_ITEM`` (308)   ``IntArr [shopRev, compDescr, vehInvID,
                                   slotIdx, isPaidRemoval, gunCompDescr]``
``CMD_BUY_AND_EQUIP_TMAN`` (309)   ``Int4 (shopRev, vehInvID, slot,
                                   tmanCostTypeIdx)``
=================================  ===============================================

The pairs of the layout commands are ``(compactDescr, count, isBoughtForCredits)``
(``LayoutIterator`` of ``common/account_shared.py``): a negative compact
descriptor means the item is paid for in credits even when its shop price is in
gold.

Everything the client mounts stays out of the depot and everything it takes off
goes back into it (the client's ``Inventory`` counts the *spare* modules, shells,
equipment and devices by asking for ``inventory[itemType][compactDescr]``), so
the depot the client displays and the depot the server charges for never
disagree.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..game import items as items_module
from ..game.catalog import Component, VehicleType, layout_key
from .purchases import PurchaseError, Shopkeeper
from .state import AccountState, TankmanEntry, VehicleEntry

# --------------------------------------------------------------------------
# Item types (``common/items/__init__.py``)
# --------------------------------------------------------------------------
ITEM = items_module.ITEM_TYPES
VEHICLE_TYPE = ITEM["vehicle"]
CHASSIS_TYPE = ITEM["vehicleChassis"]
TURRET_TYPE = ITEM["vehicleTurret"]
GUN_TYPE = ITEM["vehicleGun"]
ENGINE_TYPE = ITEM["vehicleEngine"]
FUEL_TANK_TYPE = ITEM["vehicleFuelTank"]
RADIO_TYPE = ITEM["vehicleRadio"]
TANKMAN_TYPE = ITEM["tankman"]
OPTIONAL_DEVICE_TYPE = ITEM["optionalDevice"]
SHELL_TYPE = ITEM["shell"]
EQUIPMENT_TYPE = ITEM["equipment"]

#: The module types ``CMD_EQUIP`` accepts, mapped to the key their component
#: lives under in :attr:`server.game.catalog.VehicleType.groups`.
COMPONENT_KEYS = {
    CHASSIS_TYPE: "chassis",
    ENGINE_TYPE: "engine",
    FUEL_TANK_TYPE: "fuelTank",
    RADIO_TYPE: "radio",
}
MODULE_TYPES = tuple(COMPONENT_KEYS) + (TURRET_TYPE, GUN_TYPE)

#: ``items.EQUIPMENT_TYPES`` - which half of a layout a command talks about.
EQUIPMENT_TYPE_REGULAR = 0
EQUIPMENT_TYPE_BATTLE_BOOSTERS = 1

#: ``vehicles.NUM_EQUIPMENT_SLOTS_BY_TYPE``: three consumables plus one battle
#: booster.  The client always sends the whole layout
#: (``EquipmentLayoutHelper.getRawLayout``).
REGULAR_EQUIPMENT_SLOTS = 3
EQUIPMENT_SLOTS = 4

#: Number of optional-device slots of a vehicle descriptor
#: (``vehicle_descr.OPTIONAL_DEVICE_SLOTS``).
DEVICE_SLOTS = 3

#: ``AccountCommands.VEHICLE_SETTINGS_FLAG``.
VEHICLE_SETTINGS_FLAG = {
    "NONE": 0,
    "XP_TO_TMAN": 1,
    "AUTO_REPAIR": 2,
    "AUTO_LOAD": 4,
    "AUTO_EQUIP": 8,
    "GROUP_0": 16,
    "ORIGINAL_CREW": 32,
    "NO_BATTLE": 64,
    "AUTO_EQUIP_BOOSTER": 128,
    "AUTO_RENT_CUSTOMIZATION": 256,
}

# --------------------------------------------------------------------------
# Error strings
# --------------------------------------------------------------------------
#: The client turns an error string into ``#system_messages:<prefix>/<error>``
#: (``makeI18nError('%s/%s' % (prefix, errStr))``) and can only render the keys
#: its own ``gui/scaleform/locale/system_messages.py`` carries.  The constants
#: below are the suffixes that exist under *every* prefix the operations here
#: report with (``module_apply``, ``artefact_apply``, ``equip_tankman``,
#: ``layout_apply``, ``module_buy``, ``artefact_buy``, ``recruit_window``), so a
#: refusal always has a message to show.
ERROR_SERVER = "server_error"
ERROR_INVALID_VEHICLE = "invalid_vehicle"
ERROR_VEHICLE_LOCKED = "vehicle_locked"
ERROR_VEHICLE_NEED_REPAIR = "vehicle_need_repair"
#: ``module_buy``/``artefact_buy``/``vehicle_repair``/``recruit_window``
ERROR_NOT_ENOUGH_CREDITS = "not_enough_credits"
ERROR_NOT_ENOUGH_GOLD = "not_enough_gold"
ERROR_INVALID_MODULE = "invalid_module"
#: ``module_apply``/``module_buy``
ERROR_WRONG_ITEM_TYPE = "error_wrong_item_type"
ERROR_NOT_FOR_VEHICLE = "error_not_for_this_vehicle_type"
ERROR_NEED_GUN = "error_need_gun"
#: ``layout_apply``
ERROR_SHELLS_NO_CREDITS = "SHELLS_NO_CREDITS"
ERROR_SHELLS_NO_GOLD = "SHELLS_NO_GOLD"
ERROR_EQS_NO_CREDITS = "EQS_NO_CREDITS"
ERROR_EQS_NO_GOLD = "EQS_NO_GOLD"
ERROR_WRONG_ARG_VALUE = "WRONG_ARG_VALUE"
ERROR_NOT_RESEARCHED = "NOT_RESEARCHED_ITEM"
#: ``equip_tankman``/``reequip_tankman``/``recruit_window``
ERROR_FORBIDDEN = "FORBIDDEN"


class GarageError(Exception):
    """A refused garage operation; ``error`` is what the client displays."""

    def __init__(self, code: int, error: str):
        Exception.__init__(self, error)
        self.code = int(code)
        self.error = error


def parse_shop_revision(args: Sequence) -> Tuple[int, List[int]]:
    """``[shopRev, …]`` -> ``(shopRev, rest)``; the revision itself is not checked."""
    values = list(args)
    if len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = list(values[0])
    if not values:
        raise GarageError(-2, ERROR_SERVER)
    return int(values[0]), values[1:]


# --------------------------------------------------------------------------
# Layout helpers
# --------------------------------------------------------------------------
def iter_layout(values: Sequence[int]) -> List[Tuple[int, int, bool]]:
    """``[cd, count, cd, count, …]`` -> ``[(cd, count, isBoughtForCredits), …]``.

    The client writes the third field of a pair into the *sign* of the compact
    descriptor (``LayoutIterator`` of ``common/account_shared.py``), so a
    negative descriptor means "paid for in credits".
    """
    numbers = [int(value) for value in values]
    pairs: List[Tuple[int, int, bool]] = []
    for index in range(0, len(numbers) - len(numbers) % 2, 2):
        compact = numbers[index]
        pairs.append((abs(compact), numbers[index + 1], compact < 0))
    return pairs


def flat_layout(pairs: Iterable[Tuple[int, int, bool]]) -> List[int]:
    """The inverse of :func:`iter_layout`, in the order the pairs arrive."""
    result: List[int] = []
    for compact, count, bought_for_credits in pairs:
        compact = int(compact)
        result.append(-compact if bought_for_credits else compact)
        result.append(int(count))
    return result


def layout_int_cds(values: Sequence[int], slots: int = EQUIPMENT_SLOTS) -> List[int]:
    """A layout of ``slots`` equipment compact descriptors (``0`` = empty)."""
    numbers = [int(value) for value in values]
    if len(numbers) % 2:
        numbers.append(0)
    result = [abs(compact) for compact in numbers[0::2]]
    return (result + [0] * slots)[:slots]


def equipment_slots(equipment_type: int) -> List[int]:
    """The inventory positions of one ``items.EQUIPMENT_TYPES`` half of a layout.

    A vehicle carries ``vehicles.NUM_EQUIPMENT_SLOTS_BY_TYPE`` regular
    consumables and battle boosters, so a regular layout (what the garage's
    layout dialog sends) writes the first three entries of ``eqs``/``eqsLayout``
    and leaves the booster alone.
    """
    if int(equipment_type) == EQUIPMENT_TYPE_BATTLE_BOOSTERS:
        return list(range(REGULAR_EQUIPMENT_SLOTS, EQUIPMENT_SLOTS))
    return list(range(REGULAR_EQUIPMENT_SLOTS))


def parse_set_and_fill(args: Sequence) -> Tuple[int, int, List[int], int, List[int]]:
    """``[shopRev, vehInvID, len(shells), *shells, type, len(eqs), *eqs]``.

    ``Inventory.__setAndFillLayouts_onShopSynced`` writes the *flat* length of
    each layout (not the number of pairs) in front of it, and ``0`` when the
    caller passed no layout at all.
    """
    values = list(args)
    if len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = list(values[0])
    if len(values) < 3:
        raise GarageError(-2, ERROR_WRONG_ARG_VALUE)
    shop_rev = int(values[0])
    veh_inv_id = int(values[1])
    shells_len = int(values[2])
    if shells_len < 0 or len(values) < 3 + shells_len:
        raise GarageError(-2, ERROR_WRONG_ARG_VALUE)
    shells = [int(value) for value in values[3:3 + shells_len]]
    index = 3 + shells_len
    equipment_type = int(values[index]) if index < len(values) else EQUIPMENT_TYPE_REGULAR
    eqs_len = int(values[index + 1]) if index + 1 < len(values) else 0
    if eqs_len < 0:
        raise GarageError(-2, ERROR_WRONG_ARG_VALUE)
    eqs = [int(value) for value in values[index + 2:index + 2 + eqs_len]]
    return shop_rev, veh_inv_id, shells, equipment_type, eqs


def looks_like_pairs(numbers: Sequence[int]) -> bool:
    """``True`` for ``[cd, count, …]``: a count never looks like a descriptor."""
    numbers = [int(value) for value in numbers]
    if not numbers or len(numbers) % 2:
        return False
    for index in range(1, len(numbers), 2):
        value = numbers[index]
        if value < 0 or value > 0xFFFF:
            return False
    return True


class Garage(object):
    """Mounting, filling, repairing and crewing the vehicles of one account."""

    def __init__(self, state: AccountState):
        self.state = state
        self.shop = Shopkeeper(state)
        self.catalog = state.catalog
        #: What the installation's ``optional_devices.xml`` says about every
        #: device (tags, removability, vehicle filter); empty when the file is
        #: not part of the installation, in which case nothing is refused.
        self.devices: Dict[int, dict] = getattr(self.catalog, "devices", {}) or {}

    # -- helpers --------------------------------------------------------
    def vehicle(self, inv_id: int) -> VehicleEntry:
        entry = self.state.vehicles.get(int(inv_id))
        if entry is None:
            raise GarageError(-1, ERROR_INVALID_VEHICLE)
        return entry

    def vehicle_type(self, entry: VehicleEntry) -> VehicleType:
        vehicle = self.catalog.vehicles.get(entry.compact_descr)
        if vehicle is None:
            raise GarageError(-1, ERROR_INVALID_VEHICLE)
        return vehicle

    def module(self, vehicle: VehicleType, compact_descr: int) -> Component:
        """Resolve a module of a vehicle, refusing anything else."""
        item_type = items_module.get_type_of_compact_descr(compact_descr)
        _type, _nation, item_id = items_module.parse_int_compact_descr(compact_descr)
        if item_type in COMPONENT_KEYS:
            component = vehicle.find_component(COMPONENT_KEYS[item_type], item_id)
        elif item_type == GUN_TYPE:
            component = vehicle.find_gun(item_id)
        elif item_type == TURRET_TYPE:
            group = vehicle.find_turret_group(item_id)
            component = group.turret if group is not None else None
        else:
            raise GarageError(-1, ERROR_WRONG_ITEM_TYPE)
        if component is None:
            raise GarageError(-1, ERROR_NOT_FOR_VEHICLE)
        return component

    def spare(self, item_type: int, compact_descr: int, count: int = 1,
              error: str = ERROR_SERVER) -> None:
        """Refuse when the depot does not hold ``count`` spare items."""
        if not compact_descr:
            return
        if self.state.item_count(item_type, compact_descr) < int(count):
            raise GarageError(-1, error)

    # -- turret/gun pairs of a descriptor -------------------------------
    def turret_pairs(self, entry: VehicleEntry, vehicle: VehicleType) -> Dict[int, Tuple[int, int]]:
        """``position -> (turret id, gun id)`` of a vehicle's descriptor."""
        pairs: Dict[int, Tuple[int, int]] = {}
        for pair in entry.turrets or ():
            turret_id, gun_id = int(pair[0]), int(pair[1])
            position = vehicle.turret_position(turret_id)
            if position is not None:
                pairs[position] = (turret_id, gun_id)
        for position, groups in vehicle.turrets_by_position().items():
            if position in pairs or not groups:
                continue
            group = groups[0]
            pairs[position] = (group.turret.id, group.guns[0].id if group.guns else 0)
        return pairs

    def store_pairs(self, entry: VehicleEntry, pairs: Dict[int, Tuple[int, int]]) -> None:
        entry.turrets = [[int(pairs[position][0]), int(pairs[position][1])]
                         for position in sorted(pairs)]

    def current_turret(self, entry: VehicleEntry, vehicle: VehicleType) -> Optional[Component]:
        pairs = self.turret_pairs(entry, vehicle)
        if not pairs:
            return None
        group = vehicle.find_turret_group(pairs[sorted(pairs)[0]][0])
        return group.turret if group is not None else None

    def current_gun(self, entry: VehicleEntry, vehicle: VehicleType) -> Optional[Component]:
        pairs = self.turret_pairs(entry, vehicle)
        if not pairs:
            return None
        return vehicle.find_gun(pairs[sorted(pairs)[0]][1])

    # -- modules (``CMD_EQUIP``) ----------------------------------------
    def current_layout_key(self, entry: VehicleEntry, vehicle: VehicleType) -> Optional[Tuple[int, int]]:
        """``(turretCompactDescr, gunCompactDescr)`` - ``Vehicle.shellsLayoutIdx``."""
        turret = self.current_turret(entry, vehicle)
        gun = self.current_gun(entry, vehicle)
        if turret is None or gun is None:
            return None
        return layout_key(turret.compact_descr, gun.compact_descr)

    def equip(self, veh_inv_id: int, compact_descr: int, gun_descr: int = 0) -> dict:
        """``CMD_EQUIP`` - mount a module; a turret carries its gun with it."""
        entry = self.vehicle(veh_inv_id)
        vehicle = self.vehicle_type(entry)
        compact_descr = int(compact_descr)
        item_type = items_module.get_type_of_compact_descr(compact_descr)
        self.module(vehicle, compact_descr)  # refuses a module of another vehicle

        pairs = self.turret_pairs(entry, vehicle)
        old_key = self.current_layout_key(entry, vehicle)
        taken: List[Tuple[int, int]] = [(item_type, compact_descr)]
        returned: List[Tuple[int, int]] = []
        ext: Dict = {"incompatibleEqs": []}

        if item_type == TURRET_TYPE:
            _type, _nation, turret_id = items_module.parse_int_compact_descr(compact_descr)
            group = vehicle.find_turret_group(turret_id)
            position = group.position
            # ``0`` is a real component id (the first gun of ``guns.xml``), so an
            # unset position is ``None`` and never ``0``.
            old_turret, old_gun = pairs.get(position) or (None, None)
            if old_turret == turret_id and not int(gun_descr or 0):
                return self._result(entry, ext, item_types=[item_type])
            gun_id = None
            if int(gun_descr or 0):
                _gun_type, _gun_nation, gun_id = items_module.parse_int_compact_descr(int(gun_descr))
                if not vehicle.gun_fits_turret(gun_id, turret_id):
                    raise GarageError(-1, ERROR_NEED_GUN)
            elif old_gun is not None and vehicle.gun_fits_turret(old_gun, turret_id):
                gun_id = old_gun
            elif group.guns:
                gun_id = group.guns[0].id
            if gun_id is None:
                raise GarageError(-1, ERROR_NEED_GUN)
            if old_gun is not None and gun_id != old_gun:
                returned.append((GUN_TYPE, _compact(GUN_TYPE, vehicle, old_gun)))
                taken.append((GUN_TYPE, _compact(GUN_TYPE, vehicle, gun_id)))
            if old_turret is not None and old_turret != turret_id:
                returned.append((TURRET_TYPE, _compact(TURRET_TYPE, vehicle, old_turret)))
            pairs[position] = (turret_id, gun_id)
        elif item_type == GUN_TYPE:
            _type, _nation, gun_id = items_module.parse_int_compact_descr(compact_descr)
            position = sorted(pairs)[0] if pairs else 0
            turret_id, old_gun = pairs.get(position) or (None, None)
            if old_gun == gun_id:
                return self._result(entry, ext, item_types=[item_type])
            if turret_id is not None and not vehicle.gun_fits_turret(gun_id, turret_id):
                raise GarageError(-1, ERROR_NOT_FOR_VEHICLE)
            if old_gun is not None:
                returned.append((GUN_TYPE, _compact(GUN_TYPE, vehicle, old_gun)))
            pairs[position] = (turret_id, gun_id)
        else:
            _type, _nation, component_id = items_module.parse_int_compact_descr(compact_descr)
            key = COMPONENT_KEYS[item_type]
            old_id = int(entry.components.get(key, 0) or 0)
            if old_id == component_id:
                return self._result(entry, ext, item_types=[item_type])
            if old_id:
                returned.append((item_type, _compact(item_type, vehicle, old_id)))
            entry.components[key] = component_id

        for taken_type, taken_compact in taken:
            self.spare(taken_type, taken_compact, 1,
                       ERROR_INVALID_MODULE if taken_type == item_type else ERROR_SERVER)
        self.store_pairs(entry, pairs)
        for returned_type, old_compact in returned:
            self.state.add_item(returned_type, old_compact, 1)
        for taken_type, new_compact in taken:
            self.state.remove_item(taken_type, new_compact, 1)
        entry.descriptor = None
        item_types = [item_type] + [kind for kind, _cd in taken[1:]]
        if self._switch_ammunition(entry, vehicle, old_key):
            item_types.append(SHELL_TYPE)
        return self._result(entry, ext, item_types=item_types)

    def _switch_ammunition(self, entry: VehicleEntry, vehicle: VehicleType,
                           old_key: Optional[Tuple[int, int]]) -> bool:
        """Move the loaded shells and follow the new ``(turret, gun)`` layout.

        The client keeps one layout per ``(turret, gun)`` and reads the one of
        the mounted pair, so a fitting command that changes either half has to
        answer with the layout of the *new* pair: the shells of the old one go
        back into the depot and the new one starts with whatever it held the last
        time it was mounted (nothing at all after a fresh gun).
        """
        new_key = self.current_layout_key(entry, vehicle)
        if new_key == old_key:
            return False
        loaded = False
        for compact, count in zip(list(entry.shells[0::2]), list(entry.shells[1::2])):
            compact, count = int(compact), int(count)
            if compact and count:
                self.state.add_item(SHELL_TYPE, compact, count)
                loaded = True
        entry.shells = list(entry.shells_layout.get(new_key) or ())
        if new_key is not None:
            entry.shells_layout[new_key] = list(entry.shells)
        return loaded

    # -- optional devices (``CMD_EQUIP_OPTDEV``) ------------------------
    def equip_opt_device(self, veh_inv_id: int, compact_descr: int, slot_idx: int,
                         is_paid_removal: bool = False) -> dict:
        """``CMD_EQUIP_OPTDEV`` - mount or take off an optional device.

        A device the client data marks ``<removable>false</removable>`` is
        destroyed when it is taken off for free; taking it off for gold
        (``isPaidRemoval``, the ``paidRemovalCost`` of the shop) returns it to
        the depot, exactly like the client's own confirmations describe.
        """
        entry = self.vehicle(veh_inv_id)
        vehicle = self.vehicle_type(entry)
        slot_idx = int(slot_idx)
        if not 0 <= slot_idx < DEVICE_SLOTS:
            raise GarageError(-2, ERROR_SERVER)
        devices = list(entry.devices or [])
        devices = (devices + [None] * DEVICE_SLOTS)[:DEVICE_SLOTS]
        old_id = devices[slot_idx]
        old_compact = 0 if old_id is None else _device_compact(int(old_id))
        ext: Dict = {"incompatibleEqs": []}

        if not int(compact_descr or 0):
            # Taking the device off (``OptDeviceInstaller`` sends 0).
            if old_id is None:
                raise GarageError(-1, ERROR_SERVER)
            if self._is_removable(vehicle, int(old_id)):
                self.state.add_item(OPTIONAL_DEVICE_TYPE, old_compact, 1)
            elif is_paid_removal:
                self._charge_removal()
                self.state.add_item(OPTIONAL_DEVICE_TYPE, old_compact, 1)
            devices[slot_idx] = None
        else:
            compact_descr = int(compact_descr)
            item_type, _nation, item_id = items_module.parse_int_compact_descr(compact_descr)
            if item_type != OPTIONAL_DEVICE_TYPE:
                raise GarageError(-1, ERROR_WRONG_ITEM_TYPE)
            self._check_device(vehicle, compact_descr)
            if old_id is not None and int(old_id) == item_id:
                return self._result(entry, ext, item_types=[OPTIONAL_DEVICE_TYPE])
            self.spare(OPTIONAL_DEVICE_TYPE, compact_descr, 1, ERROR_INVALID_MODULE)
            if old_id is not None:
                if self._is_removable(vehicle, int(old_id)):
                    self.state.add_item(OPTIONAL_DEVICE_TYPE, old_compact, 1)
                elif is_paid_removal:
                    self._charge_removal()
                    self.state.add_item(OPTIONAL_DEVICE_TYPE, old_compact, 1)
            self.state.remove_item(OPTIONAL_DEVICE_TYPE, compact_descr, 1)
            for conflict_slot, conflict_id in self._conflicting_devices(entry, device_id=item_id):
                if conflict_slot == slot_idx:
                    continue
                devices[conflict_slot] = None
                self.state.add_item(OPTIONAL_DEVICE_TYPE, _device_compact(conflict_id), 1)
                ext["incompatibleEqs"].append(_device_compact(conflict_id))
            devices[slot_idx] = item_id
        entry.devices = devices
        entry.descriptor = None
        return self._result(entry, ext, item_types=[OPTIONAL_DEVICE_TYPE])

    def _is_removable(self, vehicle: VehicleType, device_id: int) -> bool:
        device = self.devices.get(_device_compact(device_id))
        if device is None:
            return True
        return bool(device.get("removable", True))

    def _charge_removal(self) -> None:
        cost = dict(self.state.shop_payload().get("paidRemovalCost") or {})
        gold = int(cost.get("gold", 0) or 0)
        if self.state.gold < gold:
            raise GarageError(-1, ERROR_NOT_ENOUGH_GOLD)
        self.state.gold -= gold

    def _check_device(self, vehicle: VehicleType, compact_descr: int) -> None:
        """The vehicle filter of an optional device (``<vehicleFilter>``)."""
        device = self.devices.get(int(compact_descr))
        if not device:
            return
        tags = set(vehicle.tags or ())
        included = device.get("includeTags")
        excluded = device.get("excludeTags")
        if included and not tags & set(included):
            raise GarageError(-1, ERROR_NOT_FOR_VEHICLE)
        if excluded and tags & set(excluded):
            raise GarageError(-1, ERROR_NOT_FOR_VEHICLE)

    def _conflicting_devices(self, entry: VehicleEntry,
                             device_id: int) -> List[Tuple[int, int]]:
        """The mounted devices that carry a tag the new one cannot live with."""
        device = self.devices.get(_device_compact(device_id)) or {}
        conflicts = set(device.get("incompatibleTags") or ())
        if not conflicts:
            return []
        result = []
        for slot, value in enumerate(list(entry.devices or ())):
            if value is None:
                continue
            other = self.devices.get(_device_compact(int(value))) or {}
            if conflicts & set(other.get("tags") or ()):
                result.append((slot, int(value)))
        return result

    # -- ammunition (``CMD_EQUIP_SHELLS``) ------------------------------
    def equip_shells(self, veh_inv_id: int, values: Sequence[int]) -> dict:
        """``CMD_EQUIP_SHELLS`` - load the ammunition of one vehicle.

        This build only mentions the command in its ``Inventory`` helper (the
        ammunition panel goes through ``CMD_SET_AND_FILL_LAYOUTS``), so both
        shapes are accepted: ``(compactDescr, count)`` pairs and a plain list of
        shells, which keeps the counts the vehicle already carries.
        """
        entry = self.vehicle(veh_inv_id)
        vehicle = self.vehicle_type(entry)
        numbers = [int(value) for value in values]
        if looks_like_pairs(numbers):
            pairs = [(compact, count) for compact, count, _alt in iter_layout(numbers)]
        else:
            current = dict(zip(entry.shells[0::2], entry.shells[1::2]))
            pairs = [(abs(compact), int(current.get(abs(compact), 0))) for compact in numbers]
        self.load_shells(entry, vehicle, pairs)
        return self._result(entry, {}, item_types=[SHELL_TYPE])

    def load_shells(self, entry: VehicleEntry, vehicle: VehicleType,
                    pairs: Sequence[Tuple[int, int]]) -> List[int]:
        """Replace a vehicle's ammunition, moving shells to/from the depot."""
        loaded: Dict[int, int] = {}
        for compact, count in zip(entry.shells[0::2], entry.shells[1::2]):
            loaded[int(compact)] = int(count)
        wanted: Dict[int, int] = {}
        order: List[int] = []
        for compact, count in pairs:
            compact = abs(int(compact))
            if items_module.get_type_of_compact_descr(compact) != SHELL_TYPE:
                continue
            if compact not in wanted:
                order.append(compact)
            wanted[compact] = wanted.get(compact, 0) + max(0, int(count))
        for compact in set(wanted) | set(loaded):
            old_count = int(loaded.get(compact, 0))
            new_count = int(wanted.get(compact, 0))
            if new_count == old_count:
                continue
            if new_count > old_count:
                self.spare(SHELL_TYPE, compact, new_count - old_count, ERROR_SERVER)
                self.state.remove_item(SHELL_TYPE, compact, new_count - old_count)
            else:
                self.state.add_item(SHELL_TYPE, compact, old_count - new_count)
        entry.shells = flat_layout([(compact, wanted[compact], False) for compact in order])
        turret = self.current_turret(entry, vehicle)
        gun = self.current_gun(entry, vehicle)
        if turret is not None and gun is not None:
            entry.shells_layout[layout_key(turret.compact_descr, gun.compact_descr)] = list(entry.shells)
        return list(entry.shells)

    # -- equipment (``CMD_EQUIP_EQS``) ----------------------------------
    def equip_equipments(self, veh_inv_id: int, values: Sequence[int]) -> dict:
        """``CMD_EQUIP_EQS`` - mount the consumables of one vehicle."""
        entry = self.vehicle(veh_inv_id)
        numbers = [int(value) for value in values]
        if looks_like_pairs(numbers) and len(numbers) >= 2 * EQUIPMENT_SLOTS:
            layout = layout_int_cds(numbers)
        else:
            layout = (numbers + [0] * EQUIPMENT_SLOTS)[:EQUIPMENT_SLOTS]
        self.load_equipment(entry, layout)
        return self._result(entry, {}, item_types=[EQUIPMENT_TYPE])

    def load_equipment(self, entry: VehicleEntry, layout: Sequence[int],
                       slots: Sequence[int] = None) -> None:
        """Install ``layout`` into the equipment slots of a vehicle.

        ``slots`` are the inventory positions the layout talks about - the first
        three for a regular layout (:data:`EQUIPMENT_TYPE_REGULAR`) and the last
        one for the battle boosters of ``EQUIPMENT_TYPE_BATTLE_BOOSTERS``
        (``vehicles.NUM_EQUIPMENT_SLOTS_BY_TYPE``).  The positions the command
        does not mention keep what they had.
        """
        current = list(entry.equipment or [])
        current = (current + [0] * EQUIPMENT_SLOTS)[:EQUIPMENT_SLOTS]
        positions = list(slots) if slots else list(range(EQUIPMENT_SLOTS))
        for index, compact in zip(positions, layout):
            was, now = int(current[index] or 0), int(compact or 0)
            if was == now:
                continue
            if now:
                self.spare(EQUIPMENT_TYPE, now, 1, ERROR_NOT_RESEARCHED)
                self.state.remove_item(EQUIPMENT_TYPE, now, 1)
            if was:
                self.state.add_item(EQUIPMENT_TYPE, was, 1)
            current[index] = now
        entry.equipment = current

    # -- crew (``CMD_EQUIP_TMAN``) --------------------------------------
    def equip_tankman(self, veh_inv_id: int, slot: int, tman_inv_id: int) -> dict:
        """``CMD_EQUIP_TMAN`` - put a crew member in a seat (``-1`` empties it)."""
        entry = self.vehicle(veh_inv_id)
        roles = self.state.crew_roles_of(entry)
        slot = int(slot)
        if slot < 0 or slot >= len(roles):
            raise GarageError(-2, ERROR_FORBIDDEN)
        # An empty seat is ``None``: the client's ``Vehicle._buildCrew`` walks
        # the list and only builds a tankman for a non-``None`` entry, so ``-1``
        # would send it looking for a crew member that does not exist.
        crew = [None if not value else int(value)
                for value in (list(entry.crew) + [None] * len(roles))[:len(roles)]]
        tman_inv_id = int(tman_inv_id)
        touched: List[int] = []
        if crew[slot] and crew[slot] != tman_inv_id:
            self._release_tankman(crew[slot])
            touched.append(crew[slot])
        if tman_inv_id > 0:
            tankman = self.state.tankmen.get(tman_inv_id)
            if tankman is None:
                raise GarageError(-1, ERROR_FORBIDDEN)
            for other_slot, value in enumerate(crew):
                if value == tman_inv_id and other_slot != slot:
                    crew[other_slot] = None
            self._assign_tankman(tankman, entry, roles[slot])
            crew[slot] = tman_inv_id
            touched.append(tman_inv_id)
        else:
            crew[slot] = None
        entry.crew = crew
        return self._result(entry, {}, tankman_inv_ids=touched)

    def _assign_tankman(self, tankman: TankmanEntry, entry: VehicleEntry, role: str) -> None:
        vehicle = self.vehicle_type(entry)
        tankman.vehicle_inv_id = entry.inv_id
        if tankman.role == role and tankman.passport is None:
            return
        tankman.role = role
        if tankman.passport is None or self.state.tankman_factory is None:
            return
        try:
            tankman.compact_descr = self.state.tankman_factory.make_tankman_descr(
                tankman.passport, vehicle.innation_id, role, role_level=tankman.role_level
            )
        except Exception:  # pragma: no cover - a broken passport keeps the old descriptor
            pass

    def _release_tankman(self, tman_inv_id: int) -> None:
        tankman = self.state.tankmen.get(int(tman_inv_id))
        if tankman is not None:
            tankman.vehicle_inv_id = -1

    # -- repair (``CMD_REPAIR``) and settings (``CMD_VEH_SETTINGS``) ----
    def repair(self, veh_inv_id: int) -> dict:
        """``CMD_REPAIR`` - pay the outstanding repair bill of one vehicle."""
        entry = self.vehicle(veh_inv_id)
        cost = int(entry.repair[0] or 0)
        if cost > self.state.credits:
            raise GarageError(-1, ERROR_NOT_ENOUGH_CREDITS)
        self.state.credits -= cost
        entry.repair = (0, int(entry.repair[1] or 0))
        return self._result(entry, {})

    def change_setting(self, veh_inv_id: int, setting: int, is_on: bool) -> dict:
        """``CMD_VEH_SETTINGS`` - one bit of ``VehicleEntry.settings``."""
        entry = self.vehicle(veh_inv_id)
        flag = int(setting)
        if flag <= 0 or flag & (flag - 1):
            raise GarageError(-1, ERROR_INVALID_VEHICLE)
        settings = int(entry.settings or 0)
        entry.settings = (settings | flag) if is_on else (settings & ~flag)
        return self._result(entry, {})

    # -- layouts (``CMD_SET_AND_FILL_LAYOUTS``) -------------------------
    def set_and_fill_layouts(self, veh_inv_id: int, shells_layout: Sequence[int],
                             equipment_type: int = EQUIPMENT_TYPE_REGULAR,
                             eqs_layout: Sequence[int] = ()) -> dict:
        """``CMD_SET_AND_FILL_LAYOUTS`` - save a layout and buy what it is short of.

        The client prices the missing items itself
        (``VehicleLayoutProcessor.getShellsLayoutPrice``: ``count - inventoryCount
        - vehCount``) and only sends the layout, so the server charges exactly
        the shortfall.  The answer carries what was bought - the client shows one
        message per entry of ``ext['shells']``/``ext['eqs']``
        (:meth:`_successHandler` of ``VehicleLayoutProcessor``).
        """
        entry = self.vehicle(veh_inv_id)
        vehicle = self.vehicle_type(entry)
        shells = iter_layout(shells_layout or ())
        eqs = iter_layout(eqs_layout or ())
        bought_shells: List[list] = []
        bought_eqs: List[list] = []
        credits = 0
        gold = 0

        loaded: Dict[int, int] = {}
        for compact, count in zip(entry.shells[0::2], entry.shells[1::2]):
            loaded[int(compact)] = int(count)
        for compact, count, bought_for_credits in shells:
            if not compact:
                continue
            if items_module.get_type_of_compact_descr(compact) != SHELL_TYPE:
                raise GarageError(-2, ERROR_WRONG_ARG_VALUE)
            count = max(0, int(count))
            if not count:
                continue
            missing = count - self.state.item_count(SHELL_TYPE, compact) - int(loaded.get(compact, 0))
            if missing <= 0:
                continue
            price = self._maybe_for_credits(
                self._price_for(compact, ERROR_NOT_RESEARCHED), SHELL_TYPE, bought_for_credits
            )
            if price[1] and self.state.gold < price[1] * missing:
                raise GarageError(-1, ERROR_SHELLS_NO_GOLD)
            if price[0] and self.state.credits < price[0] * missing:
                raise GarageError(-1, ERROR_SHELLS_NO_CREDITS)
            credits += price[0] * missing
            gold += price[1] * missing
            bought_shells.append([compact, [price[0], price[1]], int(missing)])
            # The purchased shells go straight from the shop into the vehicle.
            self.state.add_item(SHELL_TYPE, compact, missing)

        slots = equipment_slots(equipment_type)
        mounted = set(int(value or 0) for value in (entry.equipment or ()))
        seen = set()
        for index, (compact, count, bought_for_credits) in enumerate(eqs):
            if not compact:
                continue
            if items_module.get_type_of_compact_descr(compact) != EQUIPMENT_TYPE:
                raise GarageError(-2, ERROR_WRONG_ARG_VALUE)
            if index >= len(slots) or not count or compact in seen:
                continue
            seen.add(compact)
            if compact in mounted or self.state.item_count(EQUIPMENT_TYPE, compact):
                continue
            price = self._maybe_for_credits(
                self._price_for(compact, ERROR_NOT_RESEARCHED), EQUIPMENT_TYPE, bought_for_credits
            )
            if price[1] and self.state.gold < price[1]:
                raise GarageError(-1, ERROR_EQS_NO_GOLD)
            if price[0] and self.state.credits < price[0]:
                raise GarageError(-1, ERROR_EQS_NO_CREDITS)
            credits += price[0]
            gold += price[1]
            bought_eqs.append([compact, [price[0], price[1]], 1])
            self.state.add_item(EQUIPMENT_TYPE, compact, 1)

        if credits or gold:
            self._charge(credits, gold, shells=bool(bought_shells))
        self.load_shells(entry, vehicle, [(compact, count) for compact, count, _alt in shells])
        self.load_equipment(entry, layout_int_cds(eqs_layout or ())[:len(slots)], slots)
        ext = {"shells": bought_shells, "eqs": bought_eqs}
        item_types = [SHELL_TYPE]
        if eqs:
            item_types.append(EQUIPMENT_TYPE)
        return self._result(entry, ext, item_types=item_types)

    def _price_for(self, compact_descr: int, error: str = ERROR_SERVER) -> Tuple[int, int]:
        price = self.state.price_of(int(compact_descr))
        if price is None:
            raise GarageError(-1, error)
        return (max(0, int(price[0])), max(0, int(price[1])))

    def _maybe_for_credits(self, price: Tuple[int, int], item_type: int,
                           bought_for_credits: bool) -> Tuple[int, int]:
        """A gold-priced item the client wants to pay for in credits."""
        if not bought_for_credits or not price[1]:
            return price
        payload = self.state.shop_payload()
        if item_type in (SHELL_TYPE, EQUIPMENT_TYPE) and not payload.get(
                "isEnabledBuyingGoldShellsForCredits", True):
            raise GarageError(-1, ERROR_SHELLS_NO_CREDITS)
        rate = payload.get("exchangeRateForShellsAndEqs") if item_type in (SHELL_TYPE, EQUIPMENT_TYPE) \
            else payload.get("exchangeRate")
        if not rate:
            return price
        return (price[1] * int(rate), 0)

    def _charge(self, credits: int, gold: int, shells: bool = True) -> None:
        if credits and self.state.credits < credits:
            raise GarageError(-1, ERROR_SHELLS_NO_CREDITS if shells else ERROR_EQS_NO_CREDITS)
        if gold and self.state.gold < gold:
            raise GarageError(-1, ERROR_SHELLS_NO_GOLD if shells else ERROR_EQS_NO_GOLD)
        self.state.credits -= int(credits)
        self.state.gold -= int(gold)

    # -- buying and mounting in one step -------------------------------
    def buy_and_equip_item(self, compact_descr: int, veh_inv_id: int, slot_idx: int = 0,
                           is_paid_removal: bool = False, gun_descr: int = 0) -> dict:
        """``CMD_BUY_AND_EQUIP_ITEM`` - buy a module/device and mount it at once."""
        compact_descr = int(compact_descr)
        item_type = items_module.get_type_of_compact_descr(compact_descr)
        entry = self.vehicle(veh_inv_id)
        vehicle = self.vehicle_type(entry)
        if item_type != OPTIONAL_DEVICE_TYPE:
            self.module(vehicle, compact_descr)
        else:
            self._check_device(vehicle, compact_descr)
        price = self._price_for(compact_descr, ERROR_INVALID_MODULE)
        if price[1]:
            if self.state.gold < price[1]:
                raise GarageError(-1, ERROR_NOT_ENOUGH_GOLD)
            self.state.gold -= price[1]
        elif price[0]:
            if self.state.credits < price[0]:
                raise GarageError(-1, ERROR_NOT_ENOUGH_CREDITS)
            self.state.credits -= price[0]
        self.state.add_item(item_type, compact_descr, 1)
        if item_type == OPTIONAL_DEVICE_TYPE:
            return self.equip_opt_device(entry.inv_id, compact_descr, slot_idx, is_paid_removal)
        return self.equip(entry.inv_id, compact_descr, gun_descr)

    def buy_and_equip_tankman(self, veh_inv_id: int, slot: int,
                              tman_cost_type_index: int = 0) -> dict:
        """``CMD_BUY_AND_EQUIP_TMAN`` - recruit one crew member into a seat."""
        entry = self.vehicle(veh_inv_id)
        vehicle = self.vehicle_type(entry)
        roles = self.state.crew_roles_of(entry)
        slot = int(slot)
        if slot < 0 or slot >= len(roles):
            raise GarageError(-2, ERROR_FORBIDDEN)
        credits, gold = self.shop.crew_cost(tman_cost_type_index)
        if gold > self.state.gold:
            raise GarageError(-1, ERROR_NOT_ENOUGH_GOLD)
        if credits > self.state.credits:
            raise GarageError(-1, ERROR_NOT_ENOUGH_CREDITS)
        if gold:
            self.state.gold -= gold
        elif credits:
            self.state.credits -= credits
        tankman = self.state.recruit_tankman(
            vehicle.nation_id, vehicle.innation_id, roles[slot],
            role_level=self.shop.crew_role_level(tman_cost_type_index),
        )
        if tankman is None:
            raise GarageError(-1, ERROR_SERVER)
        response = self.equip_tankman(entry.inv_id, slot, tankman.inv_id)
        # ``Shop.buyAndEquipTankman`` hands ``ext`` to the processor, which
        # passes it on as the system message context.
        response["ext"] = {"tmanInvID": tankman.inv_id, "tmanCompDescr": tankman.compact_descr}
        return response

    # -- diffs ----------------------------------------------------------
    def _result(self, entry: VehicleEntry, ext: Dict, item_types: Sequence[int] = (),
                tankman_inv_ids: Sequence[int] = ()) -> dict:
        """The diff a garage operation publishes (vehicle + depot + wallet)."""
        item_types = sorted(set(int(value) for value in item_types))
        diff = self.shop.purchase_diff(
            vehicle_inv_ids=[entry.inv_id],
            tankman_inv_ids=list(tankman_inv_ids),
            item_types=item_types,
        )
        return {"code": 0, "ext": ext, "diff": diff}


def _compact(item_type: int, vehicle: VehicleType, component_id: int) -> int:
    """``(item type, component id)`` -> the component's compact descriptor."""
    name = {
        CHASSIS_TYPE: "vehicleChassis",
        TURRET_TYPE: "vehicleTurret",
        GUN_TYPE: "vehicleGun",
        ENGINE_TYPE: "vehicleEngine",
        FUEL_TANK_TYPE: "vehicleFuelTank",
        RADIO_TYPE: "vehicleRadio",
        OPTIONAL_DEVICE_TYPE: "optionalDevice",
        SHELL_TYPE: "shell",
        EQUIPMENT_TYPE: "equipment",
    }[int(item_type)]
    nation_id = 15 if item_type in (OPTIONAL_DEVICE_TYPE, EQUIPMENT_TYPE) else vehicle.nation_id
    try:
        return items_module.make_int_compact_descr_by_id(name, nation_id, int(component_id))
    except ValueError:
        return 0


def _device_compact(device_id: int) -> int:
    """Optional devices live in ``nations.NONE_INDEX`` (15), like the client."""
    try:
        return items_module.make_int_compact_descr_by_id("optionalDevice", 15, int(device_id))
    except ValueError:
        return 0


__all__ = [
    "COMPONENT_KEYS",
    "DEVICE_SLOTS",
    "EQUIPMENT_SLOTS",
    "EQUIPMENT_TYPE_BATTLE_BOOSTERS",
    "EQUIPMENT_TYPE_REGULAR",
    "ERROR_NOT_ENOUGH_CREDITS",
    "ERROR_NOT_ENOUGH_GOLD",
    "ERROR_SERVER",
    "Garage",
    "GarageError",
    "MODULE_TYPES",
    "REGULAR_EQUIPMENT_SLOTS",
    "VEHICLE_SETTINGS_FLAG",
    "equipment_slots",
    "flat_layout",
    "iter_layout",
    "layout_int_cds",
    "looks_like_pairs",
    "parse_set_and_fill",
    "parse_shop_revision",
]
