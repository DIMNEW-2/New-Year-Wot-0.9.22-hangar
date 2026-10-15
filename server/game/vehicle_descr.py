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
    devices = b""
    for index, device_id in enumerate(optional_devices or ()):
        if index >= OPTIONAL_DEVICE_SLOTS or not device_id:
            continue
        flags |= 1 << index
        devices += struct.pack("<H", int(device_id))
    payload += struct.pack("<B", flags) + devices
    return payload


def stock_compact_descr(vehicle: VehicleType) -> bytes:
    """The descriptor of a freshly researched vehicle (nothing installed)."""
    configuration = vehicle.stock_configuration()
    if not configuration:
        raise VehicleDescrError("vehicle %s has no stock configuration" % vehicle.name)
    return make_compact_descr(vehicle, configuration)


def parse_compact_descr(data: bytes) -> Dict[str, object]:
    """The inverse of :func:`make_compact_descr` (turrets/flags included)."""
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
    count = max(0, (len(data) - 10) // 4)
    turrets = []
    for index in range(count):
        turrets.append(struct.unpack("<2H", data[10 + index * 4:12 + index * 4]))
    result["turrets"] = turrets
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
