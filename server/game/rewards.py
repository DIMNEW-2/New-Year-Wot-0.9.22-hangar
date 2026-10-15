"""The reward tables of the New Year event that the client does not ship.

The client owns the decorations, the boxes, the chests and the certificate
tables (``item_defs/new_year/*.xml``), but *what a box contains* was server data
in the live game.  This module builds those tables from the player's own vehicle
catalogue, so a local server can hand out the vehicles, tankwomen and
certificates the interface shows:

* :class:`VehicleRewardChooser` - a premium vehicle for a national setting
  (boxes, chests and the level 10 reward),
* :func:`tankman_reward` - the ``tankmen`` bonus record the client's barracks
  and ``TankmenBonus`` formatter read,
* :func:`vehicle_reward` - one entry of the ``vehicles`` bonus record.

Everything here works on nation ids and nation-local vehicle ids
(``VehicleType.innation_id``), because that is what a tankman descriptor and the
client's bonus records are built from.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import ny_constants as nyc
from .catalog import Catalog, VehicleType

#: Vehicles the event handed out per national setting, most wanted first.  The
#: names are the client's own file/type names; entries this build does not know
#: are skipped, and a setting without a usable entry falls back to "the best
#: premium vehicle of the setting's nations" (see
#: :meth:`VehicleRewardChooser.choose`).
PREFERRED_VEHICLES: Dict[str, Tuple[str, ...]] = {
    "soviet": (
        "R61_Object252",
        "R146_STG",
        "Ch01_Type59",
        "R122_T44_100B",
        "Ch03_WZ-111",
    ),
    "traditionalWestern": (
        "F74_AMX_M4_1949_Liberte",
        "F73_M4A1_Revalorise",
        "F65_FCM_50t",
        "GB70_N_FV4202_105",
        "F89_Canon_dassaut_de_105",
    ),
    "modernWestern": (
        "G51_Lowe",
        "A13_T34_hvy",
        "G112_KanonenJagdPanzer",
        "A45_M6A2E1",
        "A80_T26_E4_SuperPershing",
    ),
    "asian": (
        "Ch01_Type59",
        "Ch03_WZ-111",
        "J18_STA_2_3",
        "Ch23_112",
        "Ch39_WZ120_1G_FT",
    ),
}

#: Tags that keep a vehicle out of the reward pool (event-only tanks, tanks a
#: player cannot own).
EXCLUDED_TAGS = ("secret", "unrecoverable", "premiumIGR", "special", "observer")

#: A gold price below this is a placeholder for "not for sale", not a real price.
MIN_GOLD_PRICE = 1000

#: Default level of a box/chest vehicle: the event's tier 8.
REWARD_VEHICLE_LEVEL = 8


@dataclass(frozen=True)
class VehicleReward:
    """One delivered vehicle, as it travels in a bonus record."""

    vehicle: VehicleType
    crew_level: int = 100
    slots: Tuple = ()

    @property
    def type_comp_descr(self) -> int:
        return self.vehicle.compact_descr

    def as_bonus(self) -> Dict:
        """``{'typeCompDescr': cd, 'crew': [], 'slots': []}`` for the sys message."""
        return {
            "typeCompDescr": self.type_comp_descr,
            "crew": [],
            "slots": list(self.slots),
        }

    def as_quest_value(self) -> Dict:
        """``{intCD: vehInfo}`` - how a *quest* carries vehicles.

        ``bonuses.VehiclesBonus.getVehicles`` iterates the value of a quest's
        ``vehicles`` bonus as ``{intCD: vehInfo}``, while the New Year system
        message iterates a *list* of such dictionaries
        (``_NewYearBoxStorage.__extractBonuses``), so both shapes exist and this
        module produces one of each.
        """
        return {
            self.type_comp_descr: {
                "crewLvl": self.crew_level,
                "noCrew": False,
                "slots": list(self.slots),
            }
        }


class VehicleRewardChooser:
    """Picks the vehicles the event hands out."""

    def __init__(self, catalog: Catalog, preferred: Dict[str, Tuple[str, ...]] = None):
        self.catalog = catalog
        self.preferred = dict(preferred or PREFERRED_VEHICLES)
        self._preferred_vehicles: Dict[str, List[VehicleType]] = {}
        for setting, names in self.preferred.items():
            chosen = []
            for name in names:
                vehicle = catalog.vehicles.get_by_name(name)
                if vehicle is not None:
                    chosen.append(vehicle)
            self._preferred_vehicles[setting] = chosen
        self._fallback: Dict[str, List[VehicleType]] = {}
        self._best_for_setting: Dict[str, List[VehicleType]] = {}
        self._choices: Dict[str, List[VehicleType]] = {}

    # -- the pool --------------------------------------------------------
    def nations_of(self, setting: str) -> List[int]:
        return [
            nation_id
            for nation_id, setting_id in nyc.SETTING_ID_BY_NATION_ID.items()
            if nyc.NATIONAL_SETTINGS[setting_id] == setting
        ]

    def pool(self, setting: str, level: int = REWARD_VEHICLE_LEVEL) -> List[VehicleType]:
        """Every vehicle that may be handed out for a national setting."""
        key = "%s|%d" % (setting, level)
        cached = self._fallback.get(key)
        if cached is not None:
            return cached
        nations = set(self.nations_of(setting))
        vehicles = [
            vehicle
            for vehicle in self.catalog.vehicles.all()
            if vehicle.nation_id in nations
            and vehicle.is_premium
            and vehicle.level == level
            and vehicle.price_gold >= MIN_GOLD_PRICE
            and not any(tag in vehicle.tags for tag in EXCLUDED_TAGS)
        ]
        vehicles.sort(key=lambda vehicle: (-vehicle.price_gold, vehicle.compact_descr))
        self._fallback[key] = vehicles
        return vehicles

    def candidates(self, setting: str, level: int = REWARD_VEHICLE_LEVEL) -> List[VehicleType]:
        """The preferred vehicles of a setting, extended by its whole pool."""
        key = "%s|%d" % (setting, level)
        cached = self._choices.get(key)
        if cached is not None:
            return cached
        result: List[VehicleType] = []
        seen = set()
        for vehicle in self._preferred_vehicles.get(setting, ()):
            if vehicle.level == level and vehicle.compact_descr not in seen:
                seen.add(vehicle.compact_descr)
                result.append(vehicle)
        for vehicle in self.pool(setting, level):
            if vehicle.compact_descr not in seen:
                seen.add(vehicle.compact_descr)
                result.append(vehicle)
        self._choices[key] = result
        return result

    # -- choosing --------------------------------------------------------
    def choose(
        self,
        setting: str,
        rng: random.Random,
        level: int = REWARD_VEHICLE_LEVEL,
        exclude: Iterable[int] = (),
        owned: Iterable[int] = (),
    ) -> Optional[VehicleType]:
        """A vehicle for a box/chest of ``setting``.

        Vehicles the account already has are dropped, then the preferred list is
        tried (its head is twice as likely as its tail), then the whole pool.
        """
        excluded = set(exclude or ())
        owned = set(owned or ())
        for source in (self.candidates(setting, level), self.pool(setting, REWARD_VEHICLE_LEVEL)):
            usable = [
                vehicle
                for vehicle in source
                if vehicle.compact_descr not in excluded and vehicle.compact_descr not in owned
            ]
            if not usable:
                continue
            weights = [max(1, len(usable) - index) for index in range(len(usable))]
            return _weighted_choice(usable, weights, rng)
        return None

    def choose_any(self, rng: random.Random, level: int = REWARD_VEHICLE_LEVEL,
                   owned: Iterable[int] = ()) -> Optional[VehicleType]:
        """A vehicle when the setting is unknown (the "random nation" box)."""
        setting = rng.choice(list(nyc.NATIONAL_SETTINGS))
        vehicle = self.choose(setting, rng, level=level, owned=owned)
        if vehicle is not None:
            return vehicle
        for other in nyc.NATIONAL_SETTINGS:
            vehicle = self.choose(other, rng, level=level, owned=owned)
            if vehicle is not None:
                return vehicle
        return None

    # -- payloads --------------------------------------------------------
    def reward(self, vehicle: VehicleType, crew_level: int = 100) -> VehicleReward:
        return VehicleReward(vehicle=vehicle, crew_level=crew_level)


def _weighted_choice(items: Sequence, weights: Sequence[float], rng: random.Random):
    total = float(sum(weights))
    if total <= 0:
        return rng.choice(list(items))
    threshold = rng.random() * total
    offset = 0.0
    for item, weight in zip(items, weights):
        offset += weight
        if threshold <= offset:
            return item
    return items[-1]


def vehicle_reward(vehicle: VehicleType, crew_level: int = 100, slots: Sequence = ()) -> VehicleReward:
    return VehicleReward(vehicle=vehicle, crew_level=crew_level, slots=tuple(slots))


def tankman_bonus_record(passport, role: str, vehicle_type_id: int, role_level: int = 100,
                         is_premium: bool = False, is_female: bool = True,
                         free_xp: int = 0, skills: Sequence[str] = (),
                         free_skills: Sequence[str] = ()) -> Dict:
    """The ``tankmen`` bonus record of the client (``bonuses.TankmenBonus``).

    ``_TankmanInfoRecord`` of ``gui/server_events/bonuses`` reads exactly these
    fields, and its ``getTankmenGroups`` looks the vehicle up by
    ``(nationID, vehicleTypeID)``, so the reference has to be a real vehicle of
    the client's catalogue - which is why the event fills it with the vehicle
    the tankwoman was recruited for.
    """
    return {
        "nationID": int(passport.nation_id),
        "role": role,
        "vehicleTypeID": int(vehicle_type_id),
        "firstNameID": int(passport.first_name_id),
        "fnGroupID": int(passport.group_id),
        "lastNameID": int(passport.last_name_id),
        "lnGroupID": int(passport.group_id),
        "iconID": int(passport.icon_id),
        "iGroupID": int(passport.group_id),
        "isPremium": bool(is_premium),
        "roleLevel": int(role_level),
        "freeXP": int(free_xp),
        "skills": list(skills),
        "isFemale": bool(is_female),
        "freeSkills": list(free_skills),
    }


def toy_list(toys: Iterable[int]) -> List[int]:
    """The ``ny18Toys`` bonus value (``bonuses.NYToyBonuses``)."""
    return [int(toy_id) for toy_id in toys]


__all__ = [
    "EXCLUDED_TAGS",
    "MIN_GOLD_PRICE",
    "PREFERRED_VEHICLES",
    "REWARD_VEHICLE_LEVEL",
    "VehicleReward",
    "VehicleRewardChooser",
    "tankman_bonus_record",
    "toy_list",
    "vehicle_reward",
]
