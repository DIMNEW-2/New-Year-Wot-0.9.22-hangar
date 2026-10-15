"""Vehicle compact descriptors.

The account inventory stores every vehicle as the *vehicle compact descriptor*
of ``common/items/vehicles.py``::

    chr(ITEM_TYPE_VEHICLE + nationID << 4) + chr(vehicleTypeID)
        + struct.pack('<4H', chassis, engine, fuelTank, radio)
        + struct.pack('<2H', turret, gun) * (one pair per turret position)
        + chr(flags) + optional devices [+ emblems / inscriptions / camouflages]

The client splits exactly this layout in ``_splitVehicleCompactDescr`` and
builds it back in ``VehicleDescr.makeCompactDescr``, so the server can ship a
stock descriptor (chassis/engine/fuelTank/radio/turrets, no optional devices,
no emblems) and let the client own the rest of the customisation.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Sequence, Tuple

from . import items as items_module
from .catalog import Component, VehicleType

#: Bit of the descriptor's flags byte that marks a customisation field.
FLAG_CAMOUFLAGES = 128
FLAG_EMBLEMS = 32
FLAG_INSCRIPTIONS = 64

#: Number of optional-device slots in the descriptor (bits 0..2 of ``flags``).
OPTIONAL_DEVICE_SLOTS = 3


class VehicleDescrError(Exception):
    """Raised when a configuration cannot be packed into a descriptor."""


def make_compact_descr(
    vehicle: VehicleType,
    configuration: Dict[str, int],
    turrets: Optional[Sequence[Sequence[int]]] = None,
    optional_devices: Optional[Sequence[int]] = None,
) -> bytes:
    """Build the compact descriptor of ``vehicle`` for ``configuration``.

    ``configuration`` maps ``chassis``/``engine``/``fuelTank``/``radio`` to the
    nation-local component ids the client data uses; ``turrets`` defaults to the
    stock ``(turret, gun)`` pairs of the vehicle and may be given explicitly.
    """
    chassis = _component_id(configuration, "chassis", vehicle)
    engine = _component_id(configuration, "engine", vehicle)
    fuel_tank = int(configuration.get("fuelTank", 0) or 0)
    radio = _component_id(configuration, "radio", vehicle)

    if turrets is None:
        turrets = configuration.get("turrets")
    if turrets is None:
        turrets = [(group.turret.id, group.guns[0].id if group.guns else 0)
                   for _position, groups in sorted(vehicle.turrets_by_position().items())
                   for group in groups[:1]]
    turrets = [tuple(int(value) for value in pair) for pair in turrets]

    positions = vehicle.turrets_by_position()
    if len(turrets) != len(positions):
        raise VehicleDescrError(
            "vehicle %s has %d turret positions but %d were given"
            % (vehicle.name, len(positions), len(turrets))
        )

    header = items_module.ITEM_TYPES["vehicle"] + (vehicle.nation_id << 4)
    payload = struct.pack("<2B", header, vehicle.innation_id)
    payload += struct.pack("<4H", chassis, engine, fuel_tank, radio)
    for turret_id, gun_id in turrets:
        payload += struct.pack("<2H", turret_id, gun_id)

    flags = 0
    devices: List[bytes] = []
    for index, device_id in enumerate(optional_devices or ()):
        # An empty slot is ``None`` (or a plain ``0`` for callers that build the
        # list from XML ids): the *flags* byte is what marks a mounted device,
        # and device id ``0`` is a real device (the toolbox), so the two must
        # not be confused.
        if index >= OPTIONAL_DEVICE_SLOTS or device_id is None or device_id == "":
            continue
        # ``VehicleDescr.makeCompactDescr`` of the client walks the three slots
        # from the *first* one while shifting the mask left, so slot 0 ends up in
        # the highest bit and the devices of the payload are packed from the last
        # slot backwards (``devices = pack(device) + devices``).
        flags |= 1 << (OPTIONAL_DEVICE_SLOTS - 1 - index)
        devices.insert(0, struct.pack("<H", int(device_id)))
    payload += struct.pack("<B", flags) + b"".join(devices)
    return payload


def stock_compact_descr(vehicle: VehicleType) -> bytes:
    """The descriptor of a freshly researched vehicle (nothing installed)."""
    configuration = vehicle.stock_configuration()
    if not configuration:
        raise VehicleDescrError("vehicle %s has no stock configuration" % vehicle.name)
    return make_compact_descr(vehicle, configuration)


def parse_compact_descr(data: bytes, turret_positions: int = None) -> Dict[str, object]:
    """The inverse of :func:`make_compact_descr` (turrets, flags, devices).

    The optional-device part follows the client's own
    ``_splitVehicleCompactDescr``/``VehicleDescr.__init__`` pair: the flags byte
    holds the slots from the last one upwards and the device ids follow, packed
    from the last occupied slot backwards.

    ``turret_positions`` is the number of turret positions of the vehicle type
    (the client takes it from its own catalogue: ``idx = 10 + len(type.turrets)
    * 4``).  Without it a descriptor that carries optional devices cannot be
    split unambiguously, so the devices are then expected to be absent.
    """
    if len(data) < 11:
        raise VehicleDescrError("descriptor is too short: %d bytes" % len(data))
    header, vehicle_type_id = struct.unpack("<2B", data[:2])
    nation_id = header >> 4 & 15
    item_type_id = header & 15
    if item_type_id != items_module.ITEM_TYPES["vehicle"]:
        raise VehicleDescrError("descriptor is not a vehicle (type %d)" % item_type_id)
    chassis, engine, fuel_tank, radio = struct.unpack("<4H", data[2:10])
    result = {
        "nationID": nation_id,
        "vehicleTypeID": vehicle_type_id,
        "chassis": chassis,
        "engine": engine,
        "fuelTank": fuel_tank,
        "radio": radio,
    }
    if turret_positions is None:
        count = max(0, (len(data) - 11) // 4)
    else:
        count = int(turret_positions)
        if 10 + count * 4 >= len(data) + 1:
            raise VehicleDescrError("descriptor ends inside its turret positions")
    turrets = []
    for index in range(count):
        turrets.append(struct.unpack("<2H", data[10 + index * 4:14 + index * 4]))
    result["turrets"] = turrets

    index = 10 + len(turrets) * 4
    flags = 0
    devices: List[Optional[int]] = [None] * OPTIONAL_DEVICE_SLOTS
    if index < len(data):
        flags = data[index]
        index += 1
        occupied = [slot for slot in range(OPTIONAL_DEVICE_SLOTS)
                    if flags & 1 << (OPTIONAL_DEVICE_SLOTS - 1 - slot)]
        for slot in reversed(occupied):
            if index + 2 > len(data):
                raise VehicleDescrError("descriptor ends inside the optional devices")
            devices[slot] = struct.unpack("<H", data[index:index + 2])[0]
            index += 2
    result["flags"] = flags
    result["optionalDevices"] = devices
    return result


def component_by_id(vehicle: VehicleType, group_name: str, component_id: int) -> Optional[Component]:
    for component in vehicle.group(group_name):
        if component.id == component_id:
            return component
    return None


def _component_id(configuration: Dict[str, int], key: str, vehicle: VehicleType) -> int:
    value = configuration.get(key)
    if value:
        return int(value)
    stock = vehicle.stock_configuration()
    if key in stock:
        return int(stock[key])
    raise VehicleDescrError("vehicle %s has no %s" % (vehicle.name, key))


def turret_pairs(configuration: Dict[str, int]) -> List[Tuple[int, int]]:
    return [tuple(int(value) for value in pair) for pair in configuration.get("turrets", ())]


__all__ = [
    "FLAG_CAMOUFLAGES",
    "FLAG_EMBLEMS",
    "FLAG_INSCRIPTIONS",
    "OPTIONAL_DEVICE_SLOTS",
    "VehicleDescrError",
    "component_by_id",
    "make_compact_descr",
    "parse_compact_descr",
    "stock_compact_descr",
    "turret_pairs",
]
