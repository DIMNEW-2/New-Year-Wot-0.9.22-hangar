"""Client-derived catalogs: the New Year event and the vehicle catalogue.

The catalogs are the contract between the client and this server.  Both sides
read the same ``item_defs`` XML of the player's installation, so the identifiers
(toy ids, box/chest ids, component ids, compact descriptors) always agree; the
server only decides *what happens*, never what an item *is*.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import achievements_reward as achievements_reward_module
from . import badges as badges_module
from . import items as items_module
from . import ny_constants as nyc
from . import tankmen as tankmen_module
from .customization import (CustomizationCatalog, LegacyCustomization,
                             load_legacy_customization)
from .defs import DefsError, DefsSource, parse_xml

NY_FILES = {
    "toys": "new_year/toys.xml",
    "slots": "new_year/slots.xml",
    "chests": "new_year/chests.xml",
    "boxes": "new_year/boxes.xml",
    "variadic_discounts": "new_year/variadic_discounts.xml",
    "variadic_tankmen": "new_year/variadic_tankmen.xml",
    "collection_rewards": "new_year/collection_rewards.xml",
}

NUMERIC_ID_RE = re.compile(r"^[A-Za-z]")


# --------------------------------------------------------------------------
# New Year catalog
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NyToy:
    id: int
    type: str
    setting: str
    rank: int
    setting_id: int = 0
    atmosphere: int = 0
    rating: int = 0
    fragments: int = 0
    icon: str = ""
    slot_icon: str = ""
    name: str = ""
    hanging_effect: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "setting": self.setting,
            "settingID": self.setting_id,
            "rank": self.rank,
            "atmosphere": self.atmosphere,
            "rating": self.rating,
            "fragments": self.fragments,
            "icon": self.icon,
            "slotIcon": self.slot_icon,
            "name": self.name,
            "hangingEffect": self.hanging_effect,
        }


@dataclass(frozen=True)
class NySlot:
    id: int
    type: str
    object: str = ""
    nodes: str = ""
    direction: str = ""


@dataclass(frozen=True)
class NyBox:
    id: str
    setting: Optional[str] = None
    present: bool = False

    @property
    def setting_id(self) -> Optional[int]:
        if self.setting is None:
            return None
        return nyc.NATIONAL_SETTINGS_IDS_BY_NAME.get(self.setting)


@dataclass(frozen=True)
class NyChest:
    id: str
    level: int


@dataclass(frozen=True)
class NyVariadicDiscount:
    id: str
    goodies_range: Tuple[int, int]


@dataclass(frozen=True)
class NyVariadicTankman:
    id: str
    level: int


@dataclass(frozen=True)
class NyCollectionReward:
    id: str
    setting: str
    setting_id: int


class CatalogError(Exception):
    pass


class NyCatalog:
    """Everything the event needs to know about toys, slots and containers."""

    def __init__(
        self,
        toys: Dict[int, NyToy],
        slots: Sequence[NySlot],
        boxes: Dict[str, NyBox],
        chests: Dict[str, NyChest],
        variadic_discounts: Dict[str, NyVariadicDiscount],
        variadic_tankmen: Dict[str, NyVariadicTankman],
        collection_rewards: Sequence[NyCollectionReward],
    ):
        self.toys = dict(toys)
        self.slots = list(slots)
        self.boxes = dict(boxes)
        self.chests = dict(chests)
        self.variadic_discounts = dict(variadic_discounts)
        self.variadic_tankmen = dict(variadic_tankmen)
        self.collection_rewards = list(collection_rewards)

        self.chest_id_by_level: Dict[int, str] = {}
        for chest in self.chests.values():
            self.chest_id_by_level[chest.level] = chest.id

        self.collection_reward_by_setting = {reward.setting: reward.id for reward in self.collection_rewards}
        self.collection_reward_by_id = {reward.id: reward for reward in self.collection_rewards}

        self.toy_groups: Dict[int, List[int]] = {}
        for toy in self.toys.values():
            group_id = (nyc.TOY_TYPES_IDS_BY_NAME[toy.type] << 16) | (toy.setting_id << 8) | toy.rank
            self.toy_groups.setdefault(group_id, []).append(toy.id)
        for ids in self.toy_groups.values():
            ids.sort()

        self.toys_by_setting_and_type: Dict[Tuple[str, str], List[NyToy]] = {}
        for toy in self.toys.values():
            self.toys_by_setting_and_type.setdefault((toy.setting, toy.type), []).append(toy)
        for toys in self.toys_by_setting_and_type.values():
            toys.sort(key=lambda toy: toy.id)

        self.boxes_by_setting: Dict[Optional[str], List[NyBox]] = {}
        for box in self.boxes.values():
            self.boxes_by_setting.setdefault(box.setting, []).append(box)
        for boxes in self.boxes_by_setting.values():
            boxes.sort(key=lambda box: box.id)

    # -- helpers ---------------------------------------------------------
    def toys_of_setting(self, setting: str, types: Sequence[str] = None, rank: int = None) -> List[NyToy]:
        result = []
        for toy in self.toys.values():
            if toy.setting != setting:
                continue
            if types is not None and toy.type not in types:
                continue
            if rank is not None and toy.rank != rank:
                continue
            result.append(toy)
        result.sort(key=lambda toy: toy.id)
        return result

    def toys_of_group(self, toy_type: str, setting: str, rank: int) -> List[NyToy]:
        type_id = nyc.TOY_TYPES_IDS_BY_NAME.get(toy_type)
        setting_id = nyc.NATIONAL_SETTINGS_IDS_BY_NAME.get(setting)
        if type_id is None or setting_id is None:
            return []
        group_id = (type_id << 16) | (setting_id << 8) | rank
        return [self.toys[toy_id] for toy_id in self.toy_groups.get(group_id, ())]

    def box(self, box_id: str) -> Optional[NyBox]:
        return self.boxes.get(box_id)

    def chest(self, chest_id: str) -> Optional[NyChest]:
        return self.chests.get(chest_id)

    def boxes_for_setting(self, setting: Optional[str], present: bool = False) -> List[NyBox]:
        return [box for box in self.boxes_by_setting.get(setting, ()) if box.present == present]

    def validate(self):
        """Reproduce the client's own consistency checks (fail fast, not later)."""
        slot_ids = sorted(slot.id for slot in self.slots)
        if slot_ids != list(range(len(self.slots))):
            raise CatalogError("slot ids must be 0..%d, got %s" % (len(self.slots) - 1, slot_ids))
        for slot in self.slots:
            if slot.type not in nyc.TOY_TYPES:
                raise CatalogError("slot %d has unknown type %r" % (slot.id, slot.type))
        for toy in self.toys.values():
            if not 0 <= toy.id <= nyc.MAX_TOY_ID:
                raise CatalogError("toy id out of the collection bit set: %d" % toy.id)
            if toy.type not in nyc.TOY_TYPES:
                raise CatalogError("toy %d has unknown type %r" % (toy.id, toy.type))
            if toy.setting not in nyc.NATIONAL_SETTINGS:
                raise CatalogError("toy %d has unknown setting %r" % (toy.id, toy.setting))
            if not 1 <= toy.rank <= nyc.MAX_TOY_RANK:
                raise CatalogError("toy %d has unknown rank %r" % (toy.id, toy.rank))
        missing = []
        for type_id, toy_type in enumerate(nyc.TOY_TYPES):
            for setting_id, setting in enumerate(nyc.NATIONAL_SETTINGS):
                for rank in range(1, nyc.MAX_TOY_RANK + 1):
                    group_id = (type_id << 16) | (setting_id << 8) | rank
                    if not self.toy_groups.get(group_id):
                        missing.append((toy_type, setting, rank))
        if missing:
            raise CatalogError("incomplete toy groups (crafting would be broken): %s" % (missing,))
        for chest in self.chests.values():
            if not 1 <= chest.level <= nyc.MAX_CHEST_LEVEL:
                raise CatalogError("chest %s has wrong level %d" % (chest.id, chest.level))
        for level in range(1, nyc.MAX_CHEST_LEVEL + 1):
            if level not in self.chest_id_by_level:
                raise CatalogError("no chest descriptor for level %d" % level)
        for box in self.boxes.values():
            if box.setting is not None and box.setting not in nyc.NATIONAL_SETTINGS:
                raise CatalogError("box %s has unknown setting %r" % (box.id, box.setting))
        for reward in self.collection_rewards:
            if reward.setting not in nyc.NATIONAL_SETTINGS:
                raise CatalogError("collection reward %s has unknown setting" % reward.id)

    # -- serialization ---------------------------------------------------
    def to_json(self) -> dict:
        return {
            "toys": [self.toys[toy_id].as_dict() for toy_id in sorted(self.toys)],
            "slots": [
                {"id": slot.id, "type": slot.type, "object": slot.object, "nodes": slot.nodes, "direction": slot.direction}
                for slot in self.slots
            ],
            "boxes": [{"id": box.id, "setting": box.setting, "present": box.present} for box in self.boxes.values()],
            "chests": [{"id": chest.id, "level": chest.level} for chest in self.chests.values()],
            "variadicDiscounts": [
                {"id": entry.id, "goodiesRange": list(entry.goodies_range)}
                for entry in self.variadic_discounts.values()
            ],
            "variadicTankmen": [
                {"id": entry.id, "level": entry.level} for entry in self.variadic_tankmen.values()
            ],
            "collectionRewards": [
                {"id": reward.id, "setting": reward.setting} for reward in self.collection_rewards
            ],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "NyCatalog":
        toys = {}
        for raw in payload["toys"]:
            setting = raw["setting"]
            rank = int(raw["rank"])
            toys[int(raw["id"])] = NyToy(
                id=int(raw["id"]),
                type=raw["type"],
                setting=setting,
                rank=rank,
                setting_id=nyc.NATIONAL_SETTINGS_IDS_BY_NAME[setting],
                atmosphere=nyc.TOY_ATMOSPHERE_BY_RANK[rank - 1],
                rating=nyc.TOY_RATING_BY_RANK[rank - 1],
                fragments=nyc.CRAFT.FRAGMENTS_BY_RANK[rank - 1],
                icon=raw.get("icon", ""),
                slot_icon=raw.get("slotIcon", ""),
                name=raw.get("name", ""),
                hanging_effect=raw.get("hangingEffect", ""),
            )
        slots = [
            NySlot(
                id=int(raw["id"]),
                type=raw["type"],
                object=raw.get("object", ""),
                nodes=raw.get("nodes", ""),
                direction=raw.get("direction", ""),
            )
            for raw in payload["slots"]
        ]
        boxes = {
            raw["id"]: NyBox(id=raw["id"], setting=raw.get("setting"), present=bool(raw.get("present", False)))
            for raw in payload["boxes"]
        }
        chests = {raw["id"]: NyChest(id=raw["id"], level=int(raw["level"])) for raw in payload["chests"]}
        variadic_discounts = {
            raw["id"]: NyVariadicDiscount(id=raw["id"], goodies_range=tuple(raw["goodiesRange"]))
            for raw in payload["variadicDiscounts"]
        }
        variadic_tankmen = {
            raw["id"]: NyVariadicTankman(id=raw["id"], level=int(raw["level"]))
            for raw in payload["variadicTankmen"]
        }
        collection_rewards = [
            NyCollectionReward(
                id=raw["id"],
                setting=raw["setting"],
                setting_id=nyc.NATIONAL_SETTINGS_IDS_BY_NAME[raw["setting"]],
            )
            for raw in payload["collectionRewards"]
        ]
        catalog = cls(toys, slots, boxes, chests, variadic_discounts, variadic_tankmen, collection_rewards)
        catalog.validate()
        return catalog


def load_ny_catalog(source: DefsSource, validate: bool = True) -> NyCatalog:
    """Read ``new_year/*.xml`` from the client (or a JSON export)."""
    exported = source.json_payload()
    if exported is not None:
        return NyCatalog.from_json(exported["newYear"])

    toys_section = source.read_section(NY_FILES["toys"])
    toys = {}
    for entry in toys_section.subsections():
        toy_id = entry.value_int("id", -1)
        toy_type = entry.value("type", "")
        setting = entry.value("setting", "")
        rank = entry.value_int("rank", 0)
        if toy_id < 0 or setting not in nyc.NATIONAL_SETTINGS or toy_type not in nyc.TOY_TYPES:
            raise CatalogError("broken toy entry in %s: %r" % (NY_FILES["toys"], entry.values))
        if not 1 <= rank <= nyc.MAX_TOY_RANK:
            raise CatalogError("toy %d has rank %d" % (toy_id, rank))
        if toy_id in toys:
            raise CatalogError("repeated toy id %d" % toy_id)
        toys[toy_id] = NyToy(
            id=toy_id,
            type=toy_type,
            setting=setting,
            rank=rank,
            setting_id=nyc.NATIONAL_SETTINGS_IDS_BY_NAME[setting],
            atmosphere=nyc.TOY_ATMOSPHERE_BY_RANK[rank - 1],
            rating=nyc.TOY_RATING_BY_RANK[rank - 1],
            fragments=nyc.CRAFT.FRAGMENTS_BY_RANK[rank - 1],
            icon=entry.value("icon", ""),
            slot_icon=entry.value("slotIcon", ""),
            name=entry.value("name", ""),
            hanging_effect=entry.value("hangingEffect", ""),
        )

    slots = [
        NySlot(
            id=entry.value_int("id", -1),
            type=entry.value("type", ""),
            object=entry.value("object", ""),
            nodes=entry.value("nodes", ""),
            direction=entry.value("direction", ""),
        )
        for entry in source.read_section(NY_FILES["slots"]).subsections()
    ]

    boxes = {}
    for entry in source.read_section(NY_FILES["boxes"]).subsections():
        setting = entry.value("setting", "") or None
        boxes[entry.value("id", "")] = NyBox(
            id=entry.value("id", ""), setting=setting, present=entry.value_bool("present", False)
        )

    chests = {}
    for entry in source.read_section(NY_FILES["chests"]).subsections():
        chests[entry.value("id", "")] = NyChest(id=entry.value("id", ""), level=entry.value_int("level", 0))

    variadic_discounts = {}
    for entry in source.read_section(NY_FILES["variadic_discounts"]).subsections():
        bounds = entry.value_tokens("goodiesRange")
        variadic_discounts[entry.value("id", "")] = NyVariadicDiscount(
            id=entry.value("id", ""),
            goodies_range=(int(bounds[0]), int(bounds[1])) if len(bounds) == 2 else (0, 0),
        )

    variadic_tankmen = {}
    for entry in source.read_section(NY_FILES["variadic_tankmen"]).subsections():
        variadic_tankmen[entry.value("id", "")] = NyVariadicTankman(
            id=entry.value("id", ""), level=entry.value_int("level", 0)
        )

    collection_rewards = []
    for entry in source.read_section(NY_FILES["collection_rewards"]).subsections():
        setting = entry.value("setting", "")
        collection_rewards.append(
            NyCollectionReward(
                id=entry.value("id", ""),
                setting=setting,
                setting_id=nyc.NATIONAL_SETTINGS_IDS_BY_NAME.get(setting, -1),
            )
        )

    catalog = NyCatalog(toys, slots, boxes, chests, variadic_discounts, variadic_tankmen, collection_rewards)
    if validate:
        catalog.validate()
    return catalog


# --------------------------------------------------------------------------
# Vehicle catalogue
# --------------------------------------------------------------------------
@dataclass
class Component:
    """An installable vehicle module (chassis, turret, gun, engine, ...)."""

    type_name: str
    name: str
    id: int
    nation_id: int
    price_credits: int = 0
    price_gold: int = 0
    level: int = 0
    unlocks: List[Tuple[str, str, int]] = field(default_factory=list)  # (group, name, cost)
    shared: bool = True
    #: Ammunition of a gun (``<maxAmmo>``, ``<clip><count>`` and ``<shots>`` of
    #: the nation's ``guns.xml``): the client fills a vehicle's ammunition from
    #: the gun's own shots, so the local server reads the same numbers.
    max_ammo: int = 0
    clip_size: int = 1
    shots: List[Tuple[str, float]] = field(default_factory=list)  # (shell name, defaultPortion)

    @property
    def compact_descr(self) -> int:
        return items_module.make_int_compact_descr_by_id(self.type_name, self.nation_id, self.id)

    def as_json(self) -> dict:
        payload = {
            "name": self.name,
            "id": self.id,
            "price": self.price_credits,
            "priceGold": self.price_gold,
            "level": self.level,
            "unlocks": [[group, name, cost] for group, name, cost in self.unlocks],
        }
        if self.max_ammo:
            payload["maxAmmo"] = self.max_ammo
            payload["clipSize"] = self.clip_size
            payload["shots"] = [[name, portion] for name, portion in self.shots]
        return payload

    @classmethod
    def from_json(cls, type_name: str, payload: dict) -> "Component":
        return cls(
            type_name=type_name,
            name=payload["name"],
            id=int(payload["id"]),
            nation_id=int(payload.get("nationID", 0)),
            price_credits=int(payload.get("price", 0)),
            price_gold=int(payload.get("priceGold", 0)),
            level=int(payload.get("level", 0)),
            unlocks=[(group, name, int(cost)) for group, name, cost in payload.get("unlocks", ())],
            max_ammo=int(payload.get("maxAmmo", 0)),
            clip_size=int(payload.get("clipSize", 1)) or 1,
            shots=[(str(name), float(portion)) for name, portion in payload.get("shots", ())],
        )


@dataclass
class TurretGroup:
    turret: Component
    guns: List[Component] = field(default_factory=list)
    #: Turret position of the hull (``turrets0`` -> 0, ``turrets1`` -> 1, ...);
    #: a vehicle descriptor carries one ``(turret, gun)`` pair per position.
    position: int = 0


@dataclass
class VehicleType:
    name: str
    nation: str
    nation_id: int
    innation_id: int
    level: int
    tags: Tuple[str, ...] = ()
    price_credits: int = 0
    price_gold: int = 0
    user_string: str = ""
    short_user_string: str = ""
    groups: Dict[str, List[Component]] = field(default_factory=dict)
    turrets: List[TurretGroup] = field(default_factory=list)
    not_in_shop: bool = False
    #: ``<camouflage><priceFactor>`` of the vehicle's own file.  The client's
    #: item data copies it per vehicle type
    #: (``vehicles.VehicleType.__init__`` -> ``vehicleCamouflagePriceFactors``),
    #: and every vehicle of this build has it (``1.0`` for a tank whose paint
    #: pattern needs no correction).
    camouflage_price_factor: float = 1.0
    #: One entry per crew slot of the hull, each listing the roles the slot
    #: covers (``<crew><commander>gunner</commander><loader/></crew>`` ->
    #: ``(('commander', 'gunner'), ('loader',))``).  This is exactly the client's
    #: ``VehicleType.crewRoles``: a vehicle descriptor needs one tankman per
    #: slot and the slot's *first* role is the one the member is recruited for
    #: (``common/items/utils.generateDefaultCrew``).
    crew_roles: Tuple[Tuple[str, ...], ...] = ()

    @property
    def compact_descr(self) -> int:
        return items_module.make_vehicle_compact_descr(self.nation_id, self.innation_id)

    @property
    def is_secret(self) -> bool:
        return "secret" in self.tags

    @property
    def is_premium(self) -> bool:
        """``True`` for a vehicle that is paid for in gold.

        The client marks those by price, not by tag
        (``vehicles._readVehicleType``: ``if 'gold' in price: item.tags |=
        frozenset(('premium',))``), and this build's ``list.xml`` writes the
        currency as a child of ``<price>``.
        """
        return self.price_gold > 0 or "premium" in self.tags

    def turrets_by_position(self) -> Dict[int, List[TurretGroup]]:
        """Turret alternatives per turret position (``turrets0``, ``turrets1``, ...).

        The client reads one ``turretsN`` section per turret position of the
        hull and a vehicle descriptor carries one ``(turret, gun)`` pair for
        every position, so the catalogue keeps the positions apart as well.
        """
        result: Dict[int, List[TurretGroup]] = {}
        for group in self.turrets:
            result.setdefault(group.position, []).append(group)
        for groups in result.values():
            groups.sort(key=lambda group: group.turret.id)
        return result

    def group(self, name: str) -> List[Component]:
        return self.groups.get(name, [])

    def stock_configuration(self) -> dict:
        """The configuration a freshly bought vehicle has (first of every group).

        Groups the client data leaves empty for a vehicle (the tiny shared
        fuel-tank table, for instance) are allowed to be missing: they simply do
        not appear in the resulting configuration until the caller asks for the
        default id.
        """
        chassis = self.group("chassis")
        engines = self.group("engines")
        fuel_tanks = self.group("fuelTanks")
        radios = self.group("radios")
        positions = self.turrets_by_position()
        if not (chassis and engines and radios and positions):
            return {}
        configuration = {
            "chassis": chassis[0].id,
            "engine": engines[0].id,
            "radio": radios[0].id,
            # One (turret, gun) pair per turret position of the hull; the first
            # alternative of a position is the stock turret of that position.
            "turrets": [
                (groups[0].turret.id, groups[0].guns[0].id if groups[0].guns else 0)
                for _position, groups in sorted(positions.items())
            ],
        }
        if fuel_tanks:
            configuration["fuelTank"] = fuel_tanks[0].id
        return configuration

    def top_configuration(self) -> dict:
        """The fully researched configuration.

        A component is "top" when it unlocks nothing else inside its own group
        (a leaf of the research graph the client ships in the vehicle XML); ties
        are broken by price so the result is deterministic.
        """
        configuration = self.stock_configuration()
        if not configuration:
            return {}
        for group_name, key in (
            ("chassis", "chassis"),
            ("engines", "engine"),
            ("fuelTanks", "fuelTank"),
            ("radios", "radio"),
        ):
            components = self.group(group_name)
            top = _top_component(components, group_name)
            if top is not None:
                configuration[key] = top.id
        turrets = []
        for _position, groups in sorted(self.turrets_by_position().items()):
            turret = _top_component([group.turret for group in groups], "vehicleTurret")
            if turret is None:
                continue
            guns = [gun for group in groups if group.turret is turret for gun in group.guns]
            gun = _top_component(guns, "vehicleGun")
            turrets.append((turret.id, gun.id if gun is not None else (guns[0].id if guns else 0)))
        if turrets:
            configuration["turrets"] = turrets
        return configuration

    def configuration_with_components(self, configuration: dict) -> dict:
        """Return ``configuration`` completed with the missing groups (stock)."""
        stock = self.stock_configuration()
        result = dict(stock)
        for key, value in (configuration or {}).items():
            if value not in (None, 0, [], ()):
                result[key] = value
        return result

    def find_component(self, group_name: str, component_id: int) -> Optional[Component]:
        for component in self.group(group_name):
            if component.id == component_id:
                return component
        return None

    def find_gun(self, gun_id: int) -> Optional[Component]:
        for group in self.turrets:
            for gun in group.guns:
                if gun.id == gun_id:
                    return gun
        return None

    def find_turret_group(self, turret_id: int) -> Optional[TurretGroup]:
        for group in self.turrets:
            if group.turret.id == turret_id:
                return group
        return None

    def turret_position(self, turret_id: int) -> Optional[int]:
        """The ``turretsN`` position of a turret of this vehicle."""
        group = self.find_turret_group(turret_id)
        return None if group is None else group.position

    def gun_fits_turret(self, gun_id: int, turret_id: int) -> bool:
        """``True`` when a gun is one of the turret's own alternatives.

        This is the check ``VehicleDescr.mayInstallComponent`` performs in the
        client before it sends ``CMD_EQUIP``: a gun only fits the turrets that
        list it in their ``<guns>`` section.
        """
        group = self.find_turret_group(turret_id)
        if group is None:
            return False
        return any(gun.id == gun_id for gun in group.guns)

    def as_json(self) -> dict:
        return {
            "name": self.name,
            "nation": self.nation,
            "nationID": self.nation_id,
            "innationID": self.innation_id,
            "level": self.level,
            "tags": list(self.tags),
            "price": self.price_credits,
            "priceGold": self.price_gold,
            "userString": self.user_string,
            "shortUserString": self.short_user_string,
            "notInShop": self.not_in_shop,
            "camouflagePriceFactor": self.camouflage_price_factor,
            "groups": {
                group_name: [component.as_json() for component in components]
                for group_name, components in self.groups.items()
            },
            "crewRoles": [list(slot) for slot in self.crew_roles],
            "turrets": [
                {
                    "turret": group.turret.as_json(),
                    "guns": [gun.as_json() for gun in group.guns],
                    "position": group.position,
                }
                for group in self.turrets
            ],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "VehicleType":
        groups = {}
        for group_name, components in payload.get("groups", {}).items():
            groups[group_name] = [Component.from_json(group_name, raw) for raw in components]
        turrets = [
            TurretGroup(
                turret=Component.from_json("vehicleTurret", raw["turret"]),
                guns=[Component.from_json("vehicleGun", raw) for raw in raw.get("guns", [])],
                position=int(raw.get("position", 0)),
            )
            for raw in payload.get("turrets", [])
        ]
        return cls(
            name=payload["name"],
            nation=payload["nation"],
            nation_id=int(payload["nationID"]),
            innation_id=int(payload["innationID"]),
            level=int(payload["level"]),
            tags=tuple(payload.get("tags", ())),
            price_credits=int(payload.get("price", 0)),
            price_gold=int(payload.get("priceGold", 0)),
            user_string=payload.get("userString", ""),
            short_user_string=payload.get("shortUserString", ""),
            groups=groups,
            turrets=turrets,
            not_in_shop=bool(payload.get("notInShop", False)),
            camouflage_price_factor=float(payload.get("camouflagePriceFactor", 1.0)),
            crew_roles=tuple(tuple(slot) for slot in payload.get("crewRoles", ())),
        )


#: ``shellsLayout`` is keyed by ``(turret compact descriptor, gun compact
#: descriptor)`` - the client's own ``Vehicle.shellsLayoutIdx``.
LayoutKey = Tuple[int, int]


def layout_key(turret_compact_descr: int, gun_compact_descr: int) -> LayoutKey:
    return (int(turret_compact_descr), int(gun_compact_descr))


def parse_layout_key(value) -> LayoutKey:
    """Read back a ``shellsLayout`` key, which JSON keeps as a string."""
    if isinstance(value, (tuple, list)):
        return (int(value[0]), int(value[1]))
    text = str(value).strip()
    for separator in (":", ",", "-"):
        if separator in text:
            left, _sep, right = text.partition(separator)
            return (int(left), int(right))
    return (int(text), 0)


def _top_component(components: Sequence[Component], group_name: str, fallback: Optional[Component] = None):
    """The last component of a research chain (a component unlocking nothing else).

    ``<unlocks>`` in the client data points from a module to the module it opens,
    so the ends of those chains are the fully researched modules.  Several ends
    may exist (branches); the most expensive one wins, ties are broken by id so
    the choice is reproducible.
    """
    if not components:
        return fallback
    known = {component.name for component in components}
    candidates = []
    for component in components:
        opens_something = any(
            group == group_name and name in known for group, name, _cost in component.unlocks
        )
        if not opens_something:
            candidates.append(component)
    if not candidates:
        candidates = list(components)
    return max(candidates, key=lambda component: (component.price_credits + component.price_gold * 1000, component.id))


class VehicleCatalog:
    def __init__(self, vehicles: Dict[int, VehicleType], extra_prices: Dict[int, Tuple[int, int]] = None,
                 shells: Dict[int, Dict[str, Component]] = None,
                 devices: Dict[int, dict] = None):
        self.vehicles = dict(vehicles)
        self.by_name: Dict[str, VehicleType] = {vehicle.name: vehicle for vehicle in self.vehicles.values()}
        #: ``compactDescr -> (credits, gold)`` of the items that do not belong to
        #: a vehicle: shells (one table per nation), equipment and optional
        #: devices (both ``nations.NONE_INDEX``).  The shop sends these prices
        #: with its cache, and the depot/buy dialogs read them from there.
        self.extra_prices: Dict[int, Tuple[int, int]] = dict(extra_prices or {})
        #: ``nationID -> {shell name: Component}`` - the table that turns a gun's
        #: ``<shots>`` into compact descriptors (see :meth:`default_ammo`).
        self.shells: Dict[int, Dict[str, Component]] = {
            int(nation_id): dict(table) for nation_id, table in (shells or {}).items()
        }
        #: ``compactDescr -> metadata`` of every optional device of
        #: ``optional_devices.xml`` (its tags, whether it can be taken off for
        #: free and the vehicle tags it is allowed/forbidden on).
        self.devices: Dict[int, dict] = {int(cd): dict(meta) for cd, meta in (devices or {}).items()}

    def get(self, compact_descr: int) -> Optional[VehicleType]:
        return self.vehicles.get(compact_descr)

    def get_by_name(self, name: str) -> Optional[VehicleType]:
        return self.by_name.get(name)

    def shell(self, nation_id: int, name: str) -> Optional[Component]:
        """The shell a gun's ``<shots>`` entry points at."""
        return (self.shells.get(int(nation_id)) or {}).get(name)

    def default_ammo(self, vehicle: VehicleType, gun_id: int) -> List[int]:
        """The default ammunition of one gun as flat ``[intCD, count, ...]``.

        Mirrors ``common/items/vehicles.getDefaultAmmoForGun``: every shot of
        the gun gets ``int(portion * maxAmmo / clipSize + 0.5) * clipSize``
        shells, the last one only what is left of ``maxAmmo``.
        """
        gun = vehicle.find_gun(gun_id)
        if gun is None or not gun.shots or gun.max_ammo <= 0:
            return []
        clip = max(1, int(gun.clip_size or 1))
        ammo: List[int] = []
        taken = 0
        for name, portion in gun.shots:
            shell = self.shell(vehicle.nation_id, name)
            if shell is None:
                continue
            count = int(portion * gun.max_ammo / clip + 0.5) * clip
            if taken + count > gun.max_ammo:
                count = gun.max_ammo - taken
            taken += count
            ammo.extend((shell.compact_descr, max(0, count)))
        return ammo

    def all(self) -> List[VehicleType]:
        return sorted(self.vehicles.values(), key=lambda vehicle: (vehicle.nation_id, vehicle.innation_id))

    def playable(self) -> List[VehicleType]:
        """Vehicles worth handing to a player (the client hides the rest anyway)."""
        result = []
        for vehicle in self.vehicles.values():
            if not vehicle.groups or not vehicle.turrets:
                continue
            if "observer" in vehicle.tags:
                continue
            name = vehicle.name
            if name.endswith("_bot") or name.endswith("_training") or name.endswith("_IGR"):
                continue
            if name.startswith("Env_") or name == "Observer":
                continue
            result.append(vehicle)
        return sorted(result, key=lambda vehicle: (vehicle.nation_id, vehicle.innation_id))

    def item_prices(self) -> Dict[int, Tuple[int, int]]:
        """``compactDescr -> (credits, gold)`` of every priced item."""
        prices: Dict[int, Tuple[int, int]] = {}
        from .items import make_int_compact_descr_by_id

        for vehicle in self.all():
            prices[vehicle.compact_descr] = (max(0, int(vehicle.price_credits)),
                                            max(0, int(vehicle.price_gold)))
            modules = list(vehicle.groups.get("chassis", ())) + list(vehicle.groups.get("engine", ()))
            modules += list(vehicle.groups.get("radio", ())) + list(vehicle.groups.get("fuelTank", ()))
            for turret_group in vehicle.turrets:
                modules.append(turret_group.turret)
                modules.extend(turret_group.guns)
            for component in modules:
                prices[component.compact_descr] = (max(0, int(component.price_credits)),
                                                  max(0, int(component.price_gold)))
        prices.update(self.extra_prices)
        return prices

    def to_json(self) -> dict:
        return {
            "vehicles": [vehicle.as_json() for vehicle in self.all()],
            "extraPrices": [[compact, price[0], price[1]]
                            for compact, price in sorted(self.extra_prices.items())],
            "shells": [
                [nation_id, [[name, shell.as_json()] for name, shell in sorted(table.items())]]
                for nation_id, table in sorted(self.shells.items())
            ],
            "devices": [[compact, meta] for compact, meta in sorted(self.devices.items())],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "VehicleCatalog":
        vehicles = {}
        for raw in payload["vehicles"]:
            vehicle = VehicleType.from_json(raw)
            vehicles[vehicle.compact_descr] = vehicle
        extra = {int(compact): (int(credits), int(gold))
                 for compact, credits, gold in payload.get("extraPrices", ())}
        shells = {
            int(nation_id): {name: Component.from_json("shell", raw) for name, raw in entries}
            for nation_id, entries in payload.get("shells", ())
        }
        devices = {int(compact): dict(meta) for compact, meta in payload.get("devices", ())}
        return cls(vehicles, extra, shells, devices)


# --------------------------------------------------------------------------
# Combined catalog
# --------------------------------------------------------------------------
class Catalog:
    def __init__(self, new_year: NyCatalog, vehicles: VehicleCatalog,
                 customization: "CustomizationCatalog" = None, meta: dict = None,
                 badges: Dict[int, badges_module.Badge] = None,
                 legacy_customization: "LegacyCustomization" = None,
                 achievements_reward: dict = None):
        self.new_year = new_year
        self.vehicles = vehicles
        #: The ``achievementsReward`` section of the shop cache - the crystal
        #: (bond) every medal pays (see :mod:`server.game.achievements_reward`).
        #: ``Catalog.load`` of a client tree cannot build it (the record ids
        #: live in the client's *scripts*, not in ``item_defs``), so
        #: :func:`server.loader.load_catalog` fills it in; a catalogue that
        #: carries no table publishes the disabled section.
        self.achievements_reward = (achievements_reward_module.from_json(achievements_reward)
                                    if achievements_reward is not None
                                    else achievements_reward_module.disabled())
        # The appearance items of the installation (paints, camouflages, decals,
        # modifications and styles); empty when the installation ships none.
        if customization is None:
            from .customization import CustomizationCatalog

            customization = CustomizationCatalog()
        self.customization = customization
        # The badges of ``item_defs/badges.xml`` (``{id: Badge}``); the client
        # reads the same file, so this is only what the profile commands
        # validate a badge id against.
        self.badges: Dict[int, badges_module.Badge] = dict(badges or {})
        # The legacy appearance tables of the shop cache (camouflage and
        # emblem price factors plus their hidden lists), read from the same
        # files the client's own item data reads.
        if legacy_customization is None:
            legacy_customization = LegacyCustomization()
        self.legacy_customization = legacy_customization
        self.meta = meta or {}

    @property
    def devices(self) -> Dict[int, dict]:
        """The optional devices the installation describes."""
        return self.vehicles.devices

    def validate(self):
        self.new_year.validate()

    @property
    def badge_ids(self) -> Dict[int, str]:
        """``{id: name}`` of the installation's badges."""
        return badges_module.badge_ids(self.badges)

    def vehicle_camouflage_price_factors(self) -> Dict[int, float]:
        """``vehicleCamouflagePriceFactors``: ``{typeCompDescr: factor}``."""
        return {vehicle.compact_descr: float(vehicle.camouflage_price_factor)
                for vehicle in self.vehicles.all()}

    def not_in_shop_items(self) -> set:
        """``notInShopItems``: everything the installation keeps out of the shop.

        The appearance half of the shop cache carries the items of the second
        customization system (``CustomizationCatalog.not_in_shop``); the vehicles
        themselves add their ``<notInShop>`` flag from ``list.xml``
        (``_readVehicleType``), and the legacy camouflages, inscriptions and
        emblems are *not* part of this set - they have hidden lists of their own.
        """
        items = set(self.customization.not_in_shop())
        items.update(vehicle.compact_descr for vehicle in self.vehicles.all()
                     if vehicle.not_in_shop)
        return items

    def to_json(self) -> dict:
        vehicles = self.vehicles.to_json()
        payload = {
            "schema": 2,
            "generatedBy": "New-Year-Wot-0.9.22-hangar",
            "clientBuild": self.meta.get("clientBuild", "0.9.22.0.1 #1513"),
            "newYear": self.new_year.to_json(),
            "vehicles": vehicles["vehicles"],
            "extraPrices": vehicles["extraPrices"],
            "shells": vehicles["shells"],
            "devices": vehicles["devices"],
            "customization": self.customization.to_json(),
            "legacyCustomization": self.legacy_customization.to_json(),
            "achievementsReward": achievements_reward_module.to_json(self.achievements_reward),
        }
        return payload

    @classmethod
    def from_json(cls, payload: dict) -> "Catalog":
        if int(payload.get("schema", 0)) not in (1, 2):
            raise CatalogError("unsupported catalog schema: %r" % (payload.get("schema"),))
        from .customization import CustomizationCatalog

        new_year = NyCatalog.from_json(payload["newYear"])
        vehicles = VehicleCatalog.from_json(
            {
                "vehicles": payload["vehicles"],
                "extraPrices": payload.get("extraPrices", ()),
                "shells": payload.get("shells", ()),
                "devices": payload.get("devices", ()),
            }
        )
        # An export of schema 1 has no appearance items; the catalogue then
        # simply knows none (the shop publishes no customization prices).
        customization = CustomizationCatalog.from_json(payload.get("customization") or {})
        legacy = LegacyCustomization.from_json(payload.get("legacyCustomization") or {})
        return cls(new_year, vehicles, customization, payload, legacy_customization=legacy,
                   achievements_reward=payload.get("achievementsReward"))

    @classmethod
    def load(cls, source: DefsSource) -> "Catalog":
        """Load both catalogs from a client tree, an archive or a JSON export."""
        exported = source.json_payload()
        if exported is not None:
            return cls.from_json(exported)
        from .customization import CustomizationCatalog

        new_year = load_ny_catalog(source)
        vehicles = load_vehicle_catalog(source)
        return cls(new_year, vehicles, CustomizationCatalog.load(source),
                   badges=badges_module.load_badges(source),
                   legacy_customization=load_legacy_customization(source, nyc.NATIONS))


# --------------------------------------------------------------------------
# Vehicle parsing
# --------------------------------------------------------------------------
COMPONENT_GROUP_FILES = {
    "chassis": "vehicles/%s/components/chassis.xml",
    "turret": "vehicles/%s/components/turrets.xml",
    "gun": "vehicles/%s/components/guns.xml",
    "engine": "vehicles/%s/components/engines.xml",
    "fuelTank": "vehicles/%s/components/fuelTanks.xml",
    "radio": "vehicles/%s/components/radios.xml",
}
COMPONENT_GROUP_TYPES = {
    "chassis": "vehicleChassis",
    "turret": "vehicleTurret",
    "gun": "vehicleGun",
    "engine": "vehicleEngine",
    "fuelTank": "vehicleFuelTank",
    "radio": "vehicleRadio",
}


def _parse_component_file(section, group_name: str, nation_id: int) -> Dict[str, Component]:
    """Read ``<ids>`` + ``<shared>`` of a nation component file."""
    ids_section = section.subsection("ids")
    components: Dict[str, Component] = {}
    if ids_section is None:
        return components
    for name, text in ids_section.values.items():
        if name == "value":
            continue
        component_id = _int_from_text(text)
        if component_id < 0:
            continue
        components[name] = Component(
            type_name=COMPONENT_GROUP_TYPES[group_name],
            name=name,
            id=component_id,
            nation_id=nation_id,
        )
    shared = section.subsection("shared")
    if shared is not None:
        for entry in shared.children:
            component = components.get(entry.name)
            if component is None:
                continue
            price, currency = _read_price(entry)
            component.price_credits = price if currency == "credits" else 0
            component.price_gold = price if currency == "gold" else 0
            component.level = entry.value_int("level", 0)
            component.unlocks = _read_unlocks(entry)
            if group_name == "gun":
                _read_gun_ammo(entry, component)
    return components


def _read_gun_ammo(section, gun: Component) -> None:
    """The ``<maxAmmo>``/``<clip>``/``<shots>`` of one gun of ``guns.xml``.

    ``common/items/vehicles.getDefaultAmmoForGun`` turns exactly these numbers
    into the default ammunition of a gun, which is what the client shows (and
    sends back in ``CMD_SET_AND_FILL_LAYOUTS``) for a vehicle whose layout the
    server has not customized yet.
    """
    gun.max_ammo = section.value_int("maxAmmo", 0)
    clip = section.subsection("clip")
    gun.clip_size = max(1, clip.value_int("count", 1)) if clip is not None else 1
    shots = section.subsection("shots")
    if shots is None:
        return
    gun.shots = [(shot.name, shot.value_float("defaultPortion", 1.0)) for shot in shots.children]


def _parse_devices(source: DefsSource) -> Dict[int, dict]:
    """The optional devices of ``vehicles/common/optional_devices.xml``.

    Only what the local server has to check is kept: the device's own ``<tags>``,
    whether it can be taken off without paying (``<removable>`` defaults to
    ``false`` in the client's own reader), the tags it cannot live with
    (``<incompatibleTags><installed>``) and the vehicle tags its
    ``<vehicleFilter>`` includes or excludes.
    """
    from .items import make_int_compact_descr_by_id

    path = "vehicles/common/optional_devices.xml"
    devices: Dict[int, dict] = {}
    if not source.has(path):
        return devices
    for entry in source.read_section(path).children:
        if entry.name in ("ids", "shared", "icons"):
            continue
        device_id = entry.value_int("id", -1)
        if device_id < 0:
            continue
        incompatible = entry.subsection("incompatibleTags")
        include_tags: List[str] = []
        exclude_tags: List[str] = []
        vehicle_filter = entry.subsection("vehicleFilter")
        if vehicle_filter is not None:
            for side, target in (("include", include_tags), ("exclude", exclude_tags)):
                side_section = vehicle_filter.subsection(side)
                if side_section is None:
                    continue
                for vehicle_section in side_section.subsections("vehicle"):
                    target.extend(vehicle_section.value_tokens("tags"))
        try:
            compact = make_int_compact_descr_by_id("optionalDevice", 15, device_id)
        except ValueError:
            continue
        devices[compact] = {
            "id": device_id,
            "tags": entry.value_tokens("tags"),
            "removable": entry.value_bool("removable", False),
            "incompatibleTags": incompatible.value_tokens("installed") if incompatible is not None else [],
            "includeTags": include_tags,
            "excludeTags": exclude_tags,
        }
    return devices


def _parse_shells(source: DefsSource, nation: str, nation_id: int) -> Dict[str, Component]:
    """``name -> shell`` of one nation's ``components/shells.xml``.

    Unlike the other component files, the shells are written as top level
    sections (``<_7.92mm_AP><id>96</id><price>3</price>...</_7.92mm_AP>``), so
    they need their own reader.  The name table is what links a gun's
    ``<shots>`` to a compact descriptor.
    """
    path = "vehicles/%s/components/shells.xml" % nation
    shells: Dict[str, Component] = {}
    if not source.has(path):
        return shells
    for entry in source.read_section(path).children:
        if entry.name in ("ids", "shared", "icons"):
            continue
        shell_id = entry.value_int("id", -1)
        if shell_id < 0:
            continue
        price, currency = _read_price(entry)
        shells[entry.name] = Component(
            type_name="shell",
            name=entry.name,
            id=shell_id,
            nation_id=nation_id,
            price_credits=price if currency == "credits" else 0,
            price_gold=price if currency == "gold" else 0,
        )
    return shells


def _read_unlocks(section) -> List[Tuple[str, str, int]]:
    unlocks = section.subsection("unlocks")
    result: List[Tuple[str, str, int]] = []
    if unlocks is None:
        return result
    for entry in unlocks.children:
        group = _normalise_unlock_group(entry.name)
        name = entry.value("value", "").split()
        if not name:
            continue
        result.append((group, name[0], entry.value_int("cost", 0)))
    return result


def _normalise_unlock_group(name: str) -> str:
    mapping = {
        "chassis": "chassis",
        "turrets": "turret",
        "guns": "gun",
        "engines": "engine",
        "fuelTanks": "fuelTank",
        "radios": "radio",
        "vehicle": "vehicle",
    }
    return mapping.get(name, name)


def _read_price(section, name: str = "price") -> Tuple[int, str]:
    """Read ``<price>`` the way the client does.

    ``vehicles._readVehicleType`` adds the ``premium`` tag when the price is
    paid in gold, and the currency is written as a child element of ``<price>``
    (``<price>\t11800\t<gold></gold></price>``); a plain number means credits.
    """
    entry = section.subsection(name)
    if entry is None:
        return 0, "credits"
    amount = _int_from_text(entry.values.get("value", ""))
    if amount <= 0:
        amount = _int_from_text(entry.values.get(name, ""))
    for child in entry.children:
        currency = child.name.strip().lower()
        if currency in ("gold", "credits", "crystal"):
            return amount, currency
    for currency in ("gold", "credits", "crystal"):
        if currency in entry.values:
            return amount, currency
    return amount, "credits"


def _int_from_text(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return -1
    try:
        return int(text)
    except ValueError:
        return -1


#: The files that price the items which are not part of a vehicle.  Shells are
#: stored per nation, equipment and optional devices once for all nations
#: (``nations.NONE_INDEX``), exactly as ``common/items`` numbers them.
EXTRA_PRICE_FILES = (
    ("vehicles/common/equipments.xml", "equipment", "common"),
    ("vehicles/common/optional_devices.xml", "optionalDevice", "common"),
)


def _parse_extra_prices(source: DefsSource, nations: Sequence[str]) -> Dict[int, Tuple[int, int]]:
    """``compactDescr -> (credits, gold)`` of shells, equipment and devices.

    The three files write their prices as ``<price>`` with an optional currency
    child (``<price>\t3000\t</price>``, ``<price>\t50\t<gold></gold></price>``),
    which is the same shape the vehicles use.
    """
    from .items import make_int_compact_descr_by_id

    prices: Dict[int, Tuple[int, int]] = {}

    def read(path: str, item_type: str, nation_id: int) -> None:
        if not source.has(path):
            return
        section = source.read_section(path)
        for entry in section.subsections():
            item_id = entry.value_int("id", -1)
            if item_id < 0:
                continue
            price, currency = _read_price(entry)
            if price <= 0:
                continue
            credits = price if currency == "credits" else 0
            gold = price if currency == "gold" else 0
            try:
                compact = make_int_compact_descr_by_id(item_type, nation_id, item_id)
            except ValueError:
                continue
            prices[compact] = (credits, gold)

    for nation in nations:
        read("vehicles/%s/components/shells.xml" % nation, "shell", nyc.NATION_INDICES[nation])
    for path, item_type, _scope in EXTRA_PRICE_FILES:
        # ``nations.NONE_INDEX`` is 15: the item does not belong to a nation.
        read(path, item_type, 15)
    return prices


def load_vehicle_catalog(source: DefsSource, nations: Sequence[str] = nyc.NATIONS) -> VehicleCatalog:
    vehicles: Dict[int, VehicleType] = {}
    shells: Dict[int, Dict[str, Component]] = {}
    for nation in nations:
        nation_id = nyc.NATION_INDICES[nation]
        shells[nation_id] = _parse_shells(source, nation, nation_id)
        components: Dict[str, Dict[str, Component]] = {}
        for group_name, pattern in COMPONENT_GROUP_FILES.items():
            path = pattern % nation
            if not source.has(path):
                continue
            components[group_name] = _parse_component_file(source.read_section(path), group_name, nation_id)

        list_path = "vehicles/%s/list.xml" % nation
        if not source.has(list_path):
            continue
        list_section = source.read_section(list_path)
        for entry in list_section.subsections():
            vehicle_name = entry.name
            innation_id = entry.value_int("id", -1)
            if innation_id < 0:
                continue
            price, currency = _read_price(entry)
            vehicle = VehicleType(
                name=vehicle_name,
                nation=nation,
                nation_id=nation_id,
                innation_id=innation_id,
                level=entry.value_int("level", 0),
                tags=tuple(entry.value_tokens("tags")),
                price_credits=price if currency == "credits" else 0,
                price_gold=price if currency == "gold" else 0,
                user_string=entry.value("userString", ""),
                short_user_string=entry.value("shortUserString", ""),
                not_in_shop=entry.value_bool("notInShop", False),
            )
            vehicle_path = _find_vehicle_xml(source, nation, vehicle_name)
            if vehicle_path is not None:
                _apply_vehicle_xml(vehicle, source.read_section(vehicle_path), components)
            vehicles[vehicle.compact_descr] = vehicle
    return VehicleCatalog(vehicles, _parse_extra_prices(source, nations), shells, _parse_devices(source))


_VEHICLE_FILE_INDEX: Dict[str, Dict[str, str]] = {}


def _find_vehicle_xml(source: DefsSource, nation: str, vehicle_name: str) -> Optional[str]:
    cache_key = "%s|%s" % (id(source), nation)
    index = _VEHICLE_FILE_INDEX.get(cache_key)
    if index is None:
        index = {}
        for path in source.list_vehicle_files(nation):
            base = path.rsplit("/", 1)[-1][:-4]
            index.setdefault(base.lower(), path)
        _VEHICLE_FILE_INDEX[cache_key] = index
    return index.get(vehicle_name.lower())


def _read_crew_roles(section) -> Tuple[Tuple[str, ...], ...]:
    """The crew slots of ``<crew>``, mirroring ``vehicles._readCrew``.

    Every child is one slot; the space separated text of the element lists the
    extra roles the member of that slot takes over (a T-34 commander is also
    its gunner), which is what ``common/items/utils`` relies on.
    """
    crew = section.subsection("crew")
    if crew is None:
        return ()
    slots = []
    for child in crew.children:
        roles = [child.name]
        for extra in child.value_tokens("value"):
            if extra in tankmen_module.ROLES and extra != child.name and extra not in roles:
                roles.append(extra)
        slots.append(tuple(roles))
    return tuple(slots)


def _apply_vehicle_xml(vehicle: VehicleType, section, components: Dict[str, Dict[str, Component]]) -> None:
    vehicle.crew_roles = _read_crew_roles(section)
    # ``vehicles.VehicleType.__init__``: the camouflage factor of the vehicle
    # itself, which the legacy camouflage prices are multiplied with.
    camouflage = section.subsection("camouflage")
    if camouflage is not None:
        vehicle.camouflage_price_factor = camouflage.value_float(
            "priceFactor", vehicle.camouflage_price_factor)
    groups: Dict[str, List[Component]] = {}
    for group_name in ("chassis", "engines", "fuelTanks", "radios"):
        group_section = section.subsection(group_name)
        if group_section is None:
            continue
        group_key = {
            "chassis": "chassis",
            "engines": "engine",
            "fuelTanks": "fuelTank",
            "radios": "radio",
        }[group_name]
        members = []
        table = components.get(group_key, {})
        for entry in group_section.children:
            component = table.get(entry.name)
            if component is None:
                component = Component(
                    type_name=COMPONENT_GROUP_TYPES[group_key],
                    name=entry.name,
                    id=-1,
                    nation_id=vehicle.nation_id,
                )
            unlocks = _read_unlocks(entry)
            local_price, local_currency = _read_price(entry)
            if unlocks or local_price or entry.value_int("level", 0):
                component = Component(
                    type_name=component.type_name,
                    name=component.name,
                    id=component.id,
                    nation_id=component.nation_id,
                    # The vehicle file repeats a module that the nation component
                    # file owns; keep the shared price when the local entry has none.
                    price_credits=(local_price if local_currency == "credits" else 0)
                    or component.price_credits,
                    price_gold=local_price if local_currency == "gold" else component.price_gold,
                    level=entry.value_int("level", component.level),
                    unlocks=unlocks or component.unlocks,
                )
            members.append(component)
        groups[group_name] = [component for component in members if component.id >= 0]

    turret_groups: List[TurretGroup] = []
    turret_table = components.get("turret", {})
    gun_table = components.get("gun", {})
    index = 0
    while True:
        turret_section = section.subsection("turrets%d" % index)
        if turret_section is None:
            break
        for entry in turret_section.children:
            turret = turret_table.get(entry.name)
            if turret is None:
                turret = Component(
                    type_name="vehicleTurret",
                    name=entry.name,
                    id=-1,
                    nation_id=vehicle.nation_id,
                )
            guns = []
            guns_section = entry.subsection("guns")
            if guns_section is not None:
                for gun_entry in guns_section.children:
                    gun = gun_table.get(gun_entry.name)
                    if gun is None:
                        gun = Component(
                            type_name="vehicleGun",
                            name=gun_entry.name,
                            id=-1,
                            nation_id=vehicle.nation_id,
                        )
                    guns.append(gun)
            if turret.id >= 0:
                turret_groups.append(
                    TurretGroup(turret=turret, guns=[gun for gun in guns if gun.id >= 0], position=index)
                )
        index += 1

    vehicle.groups = groups
    vehicle.turrets = turret_groups


__all__ = [
    "Catalog",
    "CatalogError",
    "Component",
    "LayoutKey",
    "NyBox",
    "NyCatalog",
    "NyChest",
    "NyCollectionReward",
    "NySlot",
    "NyToy",
    "NyVariadicDiscount",
    "NyVariadicTankman",
    "TurretGroup",
    "VehicleCatalog",
    "VehicleType",
    "layout_key",
    "load_ny_catalog",
    "load_vehicle_catalog",
    "parse_layout_key",
]
