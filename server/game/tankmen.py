"""Tankman descriptors: nation configs, passports and compact descriptors.

The client's ``common/items/tankmen.py`` packs a tankman into the byte string
that travels in the inventory (``inventory[8]['compDescr'][invID]``) and
unpacks it again to build the crew GUI.  The server has to produce exactly the
same bytes, so this module mirrors:

* ``generateCompactDescr(passport, vehicleTypeID, role, roleLevel, ...)`` - the
  packing order, the ``<B4Hi`` block and the optional dossier descriptor,
* the nation configuration read from ``item_defs/tankmen/<nation>.xml``
  (``<groups>``/``<premiumGroups>`` with weighted name/icon lists), which the
  client uses to resolve ``firstNameID``/``lastNameID``/``iconID``.

Nothing here is random by default: :class:`TankmanFactory` takes a
``random.Random`` so tests and the server can both be deterministic.
"""

from __future__ import annotations

import random
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import ny_constants as nyc

#: ``common/items/components/skills_constants.py``: ``('reserved', 'commander',
#: 'radioman', 'driver', 'gunner', 'loader', ...)``.
SKILL_NAMES = (
    "commander",
    "radioman",
    "driver",
    "gunner",
    "loader",
)
SKILL_INDICES = {name: index for index, name in enumerate(SKILL_NAMES, start=1)}
ROLES = ("commander", "radioman", "driver", "gunner", "loader")
ROLE_INDICES = dict(SKILL_INDICES)

MIN_ROLE_LEVEL = 50
MAX_SKILL_LEVEL = 100
SKILL_LEVELS_PER_RANK = 25

#: ``ITEM_TYPES.tankman`` of the client (see ``server.game.items``).
TANKMAN_ITEM_TYPE = 8


class TankmenError(Exception):
    pass


@dataclass
class NationGroup:
    """One weighted group of names of ``item_defs/tankmen/<nation>.xml``."""

    name: str
    premium: bool = False
    is_female: bool = False
    not_in_shop: bool = False
    weight: float = 0.0
    tags: Tuple[str, ...] = ()
    roles: Tuple[str, ...] = ()
    first_names: Dict[int, str] = field(default_factory=dict)
    last_names: Dict[int, str] = field(default_factory=dict)
    icons: Dict[int, str] = field(default_factory=dict)

    @property
    def first_name_ids(self) -> List[int]:
        return sorted(self.first_names)

    @property
    def last_name_ids(self) -> List[int]:
        return sorted(self.last_names)

    @property
    def icon_ids(self) -> List[int]:
        return sorted(self.icons)

    def is_usable(self) -> bool:
        """A group can only produce a tankman when all three lists are filled."""
        return bool(self.first_names and self.last_names and self.icons)


@dataclass
class NationConfig:
    nation: str
    groups: List[NationGroup] = field(default_factory=list)
    premium_groups: List[NationGroup] = field(default_factory=list)

    def group(self, group_id: int, premium: bool = False) -> Optional[NationGroup]:
        groups = self.premium_groups if premium else self.groups
        if 0 <= group_id < len(groups):
            return groups[group_id]
        return None

    def usable_groups(self, premium: bool = False, female: Optional[bool] = None) -> List[NationGroup]:
        groups = [
            group
            for group in (self.premium_groups if premium else self.groups)
            if group.is_usable() and (female is None or group.is_female == female)
        ]
        return groups

    def female_groups(self) -> List[NationGroup]:
        """Groups that can produce a tankwoman (the NY event's reward)."""
        result = [group for group in self.groups if group.is_usable() and group.is_female]
        if not result:
            result = [group for group in self.premium_groups if group.is_usable() and group.is_female]
        return result


def read_nation_config(source, nation: str) -> NationConfig:
    """Read ``item_defs/tankmen/<nation>.xml`` through a :class:`DefsSource`."""
    section = source.read_section("tankmen/%s.xml" % nation)
    config = NationConfig(nation=nation)
    for key, premium in (("normalGroups", False), ("groups", False), ("premiumGroups", True)):
        groups_section = section.subsection(key)
        if groups_section is None:
            continue
        # ``<groups>`` is only a fallback spelling used by older builds; when
        # ``normalGroups`` exists it already filled the list.
        if not premium and config.groups and key != "normalGroups":
            continue
        target = config.premium_groups if premium else config.groups
        for entry in groups_section.children:
            target.append(_read_group(entry, premium))
    if not config.groups and not config.premium_groups:
        raise TankmenError("tankmen/%s.xml has no groups" % nation)
    return config


def _read_group(section, premium: bool) -> NationGroup:
    roles = tuple(section.value_tokens("roles"))
    return NationGroup(
        name=section.name,
        premium=premium,
        is_female=section.value("sex", "male").strip().lower() == "female",
        not_in_shop=section.value_bool("notInShop", False),
        weight=section.value_float("weight", 0.0),
        tags=tuple(section.value_tokens("tags")),
        roles=roles,
        first_names=_read_id_list(section.subsection("firstNames")),
        last_names=_read_id_list(section.subsection("lastNames")),
        icons=_read_id_list(section.subsection("icons")),
    )


def _read_id_list(section) -> Dict[int, str]:
    """``<firstNames><_11001> value </_11001> ...`` -> ``{11001: value}``."""
    if section is None:
        return {}
    result: Dict[int, str] = {}
    for entry in section.children:
        name = entry.name
        if not name.startswith("_"):
            continue
        try:
            content_id = int(name[1:])
        except ValueError:
            continue
        # ``_build_named`` stores leaf text under the ``value`` key; keep the
        # text of an element that also carries children as a fallback.
        text = entry.values.get("value") or entry.value("value", "")
        if text:
            result[content_id] = text.strip()
        elif entry.values:
            first = sorted(entry.values.items())[0][1]
            result[content_id] = str(first).strip()
    return result


def generate_compact_descr(
    passport: Sequence,
    vehicle_type_id: int,
    role: str,
    role_level: int = MAX_SKILL_LEVEL,
    skills: Sequence[str] = (),
    free_skills: Sequence[str] = (),
    last_skill_level: int = MAX_SKILL_LEVEL,
    rank_index: int = 0,
    levels_to_next_rank: int = 0,
    free_xp: int = 0,
    dossier_compact_descr: bytes = b"",
) -> bytes:
    """Mirror of ``common/items/tankmen.generateCompactDescr``.

    ``passport`` is ``(nationID, isPremium, isFemale, firstNameID, lastNameID,
    iconID)`` and the returned bytes are what the client's ``TankmanDescr``
    parses back (see ``__initFromCompactDescr``).
    """
    if role not in ROLE_INDICES:
        raise TankmenError("unknown role %r" % (role,))
    if not MIN_ROLE_LEVEL <= role_level <= MAX_SKILL_LEVEL:
        role_level = MAX_SKILL_LEVEL
    nation_id, is_premium, is_female, first_name_id, last_name_id, icon_id = passport
    header = TANKMAN_ITEM_TYPE + (int(nation_id) << 4)
    compact = struct.pack(
        "4B",
        header,
        int(vehicle_type_id) & 0xFF,
        ROLE_INDICES[role],
        int(role_level),
    )
    all_skills = [SKILL_INDICES[skill] for skill in free_skills]
    all_skills.extend(SKILL_INDICES[skill] for skill in skills)
    num_skills = len(all_skills)
    compact += struct.pack("%dB" % (1 + num_skills), num_skills, *all_skills)
    compact += bytes([last_skill_level & 0xFF if num_skills else 0])
    flags = (1 if is_female else 0) | ((1 if is_premium else 0) << 1) | (len(free_skills) << 2)
    packed_rank = (int(rank_index) & 0x1F) | ((int(levels_to_next_rank) & 0x7FF) << 5)
    compact += struct.pack(
        "<B4Hi",
        flags,
        int(first_name_id),
        int(last_name_id),
        int(icon_id),
        packed_rank,
        int(free_xp),
    )
    compact += dossier_compact_descr or b""
    return compact


@dataclass
class Passport:
    nation_id: int
    is_premium: bool
    is_female: bool
    first_name_id: int
    last_name_id: int
    icon_id: int
    group_id: int = 0
    group_name: str = ""

    def as_tuple(self) -> Tuple[int, bool, bool, int, int, int]:
        return (
            self.nation_id,
            self.is_premium,
            self.is_female,
            self.first_name_id,
            self.last_name_id,
            self.icon_id,
        )

    def as_choice(self) -> Tuple[int, int, int, int]:
        """``(groupID, firstNameID, lastNameID, iconID)`` - the NY choice tuple."""
        return (self.group_id, self.first_name_id, self.last_name_id, self.icon_id)


class TankmanFactory:
    """Builds valid tankman descriptors from the client's nation configs."""

    def __init__(self, configs: Dict[str, NationConfig], rng: Optional[random.Random] = None):
        self.configs = dict(configs)
        self.rng = rng or random.Random()

    def config(self, nation_id: int) -> NationConfig:
        name = nyc.NATIONS[nation_id] if 0 <= nation_id < len(nyc.NATIONS) else None
        config = self.configs.get(name) if name else None
        if config is None:
            raise TankmenError("no tankmen configuration for nation %r" % (name,))
        return config

    def make_passport(
        self,
        nation_id: int,
        role: str = "commander",
        female: Optional[bool] = None,
        premium: bool = False,
        vehicle_name: Optional[str] = None,
    ) -> Passport:
        """Pick a name/icon triple for a new tankman.

        Group choice walks the weighted list of the nation like the client's
        ``passportProducer`` does, restricted to the requested sex.  Tags are
        used to prefer a group bound to ``vehicle_name`` (the client does the
        same when it previews a crew member for a vehicle).
        """
        config = self.config(nation_id)
        groups = config.usable_groups(premium=premium, female=female)
        if not groups and premium:
            groups = config.usable_groups(premium=False, female=female)
        if not groups and female:
            # The ordinary name list of a nation holds no women; the tankwomen
            # of the New Year event live in its premium groups (``women1``,
            # ``women_Loza``, ...), which is exactly what the client's own
            # ``TankmenGroups`` shows for a female crew member.
            groups = config.usable_groups(premium=True, female=True)
            if not groups:
                groups = config.female_groups()
        if not groups:
            raise TankmenError("no tankman groups for nation %d" % nation_id)

        preferred = [
            group
            for group in groups
            if (vehicle_name and vehicle_name in group.tags) or (role in group.roles)
        ]
        pool = preferred or groups
        group = self._weighted_choice(pool)
        return self._passport_from_group(config, group)

    def make_female_passport(self, nation_id: int, vehicle_name: Optional[str] = None) -> Passport:
        """A tankwoman for the New Year reward (the event's own choice list)."""
        return self.make_passport(nation_id, female=True, vehicle_name=vehicle_name)

    def _weighted_choice(self, groups: Sequence[NationGroup]) -> NationGroup:
        weights = [max(group.weight, 0.0) for group in groups]
        if not any(weights):
            weights = [1.0] * len(groups)
        threshold = self.rng.random() * sum(weights)
        offset = 0.0
        for group, weight in zip(groups, weights):
            offset += weight
            if threshold <= offset:
                return group
        return groups[-1]

    def _passport_from_group(self, config: NationConfig, group: NationGroup) -> Passport:
        first_ids = group.first_name_ids
        last_ids = group.last_name_ids
        icon_ids = group.icon_ids
        count = min(len(first_ids), len(last_ids), len(icon_ids))
        if not count:
            raise TankmenError("tankman group %r is empty" % (group.name,))
        position = self.rng.randrange(count)
        # ``groupID`` is an index into the list the group belongs to, so the
        # client resolves the same names back (``isPremium``/``isFemale`` of the
        # tankman decide which list it looks in).
        groups = config.premium_groups if group.premium else config.groups
        group_id = groups.index(group) if group in groups else 0
        return Passport(
            nation_id=nyc.NATION_INDICES[config.nation],
            is_premium=group.premium,
            is_female=group.is_female,
            first_name_id=first_ids[position],
            last_name_id=last_ids[position],
            icon_id=icon_ids[position],
            group_id=group_id,
            group_name=group.name,
        )

    def make_tankman_descr(
        self,
        passport: Passport,
        vehicle_type_id: int,
        role: str,
        role_level: int = MAX_SKILL_LEVEL,
        skills: Sequence[str] = (),
        free_skills: Sequence[str] = (),
        rank_index: int = 0,
        levels_to_next_rank: int = 0,
    ) -> bytes:
        return generate_compact_descr(
            passport.as_tuple(),
            vehicle_type_id=vehicle_type_id,
            role=role,
            role_level=role_level,
            skills=skills,
            free_skills=free_skills,
            rank_index=rank_index,
            levels_to_next_rank=levels_to_next_rank,
        )


def parse_compact_descr(compact_descr) -> dict:
    """Best-effort reader of the header block, used by tests and the tools."""
    if isinstance(compact_descr, str):
        compact_descr = compact_descr.encode("latin-1")
    header, vehicle_type_id, role_id, role_level, num_skills = struct.unpack("5B", bytes(compact_descr[:5]))
    return {
        "itemType": header & 0x0F,
        "nationID": header >> 4,
        "vehicleTypeID": vehicle_type_id,
        "roleID": role_id,
        "role": SKILL_NAMES[role_id - 1] if 1 <= role_id <= len(SKILL_NAMES) else None,
        "roleLevel": role_level,
        "numSkills": num_skills,
    }


__all__ = [
    "MAX_SKILL_LEVEL",
    "MIN_ROLE_LEVEL",
    "NationConfig",
    "NationGroup",
    "Passport",
    "ROLES",
    "ROLE_INDICES",
    "SKILL_INDICES",
    "SKILL_LEVELS_PER_RANK",
    "SKILL_NAMES",
    "TANKMAN_ITEM_TYPE",
    "TankmanFactory",
    "TankmenError",
    "generate_compact_descr",
    "parse_compact_descr",
    "read_nation_config",
]
