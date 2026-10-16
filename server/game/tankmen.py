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

#: ``common/items/components/skills_constants.py::SKILL_NAMES`` - the *padded*
#: table of the client: a role or a skill is stored in a descriptor as its index
#: in this tuple (``reserved`` entries are holes, and
#: ``SKILL_INDICES``/``generateCompactDescr`` skip them).
SKILL_NAMES = (
    "reserved", "commander", "radioman", "driver", "gunner", "loader",
    "repair", "fireFighting", "camouflage", "brotherhood", "reserved", "reserved",
    "reserved", "reserved", "reserved", "reserved", "commander_tutor", "commander_eagleEye",
    "commander_sixthSense", "commander_expert", "commander_universalist", "reserved", "reserved", "reserved",
    "reserved", "reserved", "reserved", "reserved", "driver_virtuoso", "driver_smoothDriving",
    "driver_badRoadsKing", "driver_rammingMaster", "driver_tidyPerson", "reserved", "reserved", "reserved",
    "reserved", "gunner_gunsmith", "gunner_sniper", "gunner_smoothTurret", "gunner_rancorous", "reserved",
    "reserved", "reserved", "reserved", "reserved", "loader_pedant", "loader_desperado",
    "loader_intuition", "reserved", "reserved", "reserved", "reserved", "radioman_inventor",
    "radioman_finder", "radioman_retransmitter", "radioman_lastEffort", "reserved", "reserved", "reserved",
    "reserved",
)

SKILL_INDICES = {
    name: index
    for index, name in enumerate(SKILL_NAMES)
    if not name.startswith("reserved")
}
ROLES = ("commander", "radioman", "driver", "gunner", "loader")
ROLE_INDICES = {name: SKILL_INDICES[name] for name in ROLES}

MIN_ROLE_LEVEL = 50
MAX_SKILL_LEVEL = 100
SKILL_LEVELS_PER_RANK = 25

#: ``MAX_FREE_XP``/``_LEVELUP_K1``/``_LEVELUP_K2`` of ``common/items/tankmen.py``:
#: what one level of a role or a skill costs in experience.
MAX_FREE_XP = 2000000000
LEVELUP_K1 = 50.0
LEVELUP_K2 = 100.0

#: ``skills_constants.COMMON_SKILLS``/``SKILLS_BY_ROLES``: which skill a role may
#: learn (``TankmanDescr.addSkill`` refuses everything else).
COMMON_SKILLS = ("repair", "fireFighting", "camouflage", "brotherhood")
SKILLS_BY_ROLES = {
    "commander": COMMON_SKILLS + ("commander_tutor", "commander_expert",
                                  "commander_universalist", "commander_sixthSense",
                                  "commander_eagleEye"),
    "driver": COMMON_SKILLS + ("driver_tidyPerson", "driver_smoothDriving",
                               "driver_virtuoso", "driver_badRoadsKing",
                               "driver_rammingMaster"),
    "gunner": COMMON_SKILLS + ("gunner_smoothTurret", "gunner_sniper",
                               "gunner_rancorous", "gunner_gunsmith"),
    "loader": COMMON_SKILLS + ("loader_pedant", "loader_desperado", "loader_intuition"),
    "radioman": COMMON_SKILLS + ("radioman_finder", "radioman_inventor",
                                 "radioman_lastEffort", "radioman_retransmitter"),
}
ACTIVE_SKILLS = tuple(sorted({skill for skills in SKILLS_BY_ROLES.values() for skill in skills}))

#: ``_makeLevelXpCosts()``: ``costs[level]`` is the experience a crew member of
#: ``level`` has spent, so ``costs[level + 1] - costs[level]`` is one level.
LEVEL_XP_COSTS = [0] * (MAX_SKILL_LEVEL + 1)
_previous = 0
for _level in range(1, len(LEVEL_XP_COSTS)):
    _previous += int(round(LEVELUP_K1 * pow(LEVELUP_K2, float(_level - 1) / MAX_SKILL_LEVEL)))
    LEVEL_XP_COSTS[_level] = _previous
del _previous, _level


def level_up_xp_cost(from_level: int, skill_seq_num: int) -> int:
    """``TankmanDescr.levelUpXpCost``: one level of the n-th skill."""
    costs = LEVEL_XP_COSTS
    return 2 ** int(skill_seq_num) * (costs[from_level + 1] - costs[from_level])

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
    #: ``<roleRanks>`: role -> tuple of rank ids (indices into :attr:`ranks`).
    role_ranks: Dict[str, Tuple[int, ...]] = field(default_factory=dict)
    #: ``<ranks>``: the rank names in the order the client numbers them.
    ranks: List[str] = field(default_factory=list)

    def role_rank_ids(self, role: str) -> Tuple[int, ...]:
        return tuple(self.role_ranks.get(role, ()))

    def find_group(self, is_female: bool, first_name_id: int, last_name_id: int,
                   icon_id: int, premium: bool = False) -> int:
        """``findGroupsByIDs(...)``: the id of the group a passport belongs to."""
        groups = self.premium_groups if premium else self.groups
        for index, group in enumerate(groups):
            if group.is_female != bool(is_female):
                continue
            if first_name_id in group.first_names and last_name_id in group.last_names \
                    and icon_id in group.icons:
                return index
        return 0

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
    # ``_readRanks``/``_readRoleRanks``: a rank is identified by its index in
    # ``<ranks>``, and ``<roleRanks>`` maps a role to the ranks it can hold.
    ranks_section = section.subsection("ranks")
    if ranks_section is not None:
        config.ranks = [child.name for child in ranks_section.children]
    role_ranks = section.subsection("roleRanks")
    if role_ranks is not None:
        for role in ROLES:
            names = role_ranks.value_tokens(role)
            try:
                config.role_ranks[role] = tuple(config.ranks.index(name) for name in names)
            except ValueError:  # pragma: no cover - a broken installation
                continue
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
    rank_index: Optional[int] = None,
    levels_to_next_rank: Optional[int] = None,
    free_xp: int = 0,
    dossier_compact_descr: bytes = b"",
    max_rank_index: Optional[int] = None,
) -> bytes:
    """Mirror of ``common/items/tankmen.generateCompactDescr``.

    ``passport`` is ``(nationID, isPremium, isFemale, firstNameID, lastNameID,
    iconID)`` and the returned bytes are what the client's ``TankmanDescr``
    parses back (see ``__initFromCompactDescr``).

    The rank of the crew member is usually *derived*: the client turns the role
    level and the levels of the learned skills into a rank of the nation's own
    table (``<roleRanks>``).  Passing ``rank_index``/``levels_to_next_rank``
    writes them as they are, which is what an updated descriptor needs.
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
    if rank_index is None or levels_to_next_rank is None:
        rank, levels = _derived_rank(role_level, all_skills, last_skill_level, max_rank_index)
    else:
        rank, levels = int(rank_index), int(levels_to_next_rank)
    packed_rank = (rank & 0x1F) | ((levels & 0x7FF) << 5)
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


def _derived_rank(role_level: int, skills: Sequence[int], last_skill_level: int,
                  max_rank_index: Optional[int]) -> Tuple[int, int]:
    """The ``divmod`` the client does at the end of ``generateCompactDescr``."""
    total_level = int(role_level) - MIN_ROLE_LEVEL
    if skills:
        total_level += (len(skills) - 1) * MAX_SKILL_LEVEL + int(last_skill_level)
    rank, levels = divmod(max(0, total_level), SKILL_LEVELS_PER_RANK)
    levels = SKILL_LEVELS_PER_RANK - levels
    if max_rank_index is not None:
        rank = min(rank, max(0, int(max_rank_index)))
        if rank == max_rank_index:
            # ``__updateRankAtSkillLevelUp`` never moves past the last rank
            levels = 0
    return rank, levels


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
        rank_index: Optional[int] = None,
        levels_to_next_rank: Optional[int] = None,
    ) -> bytes:
        """The descriptor of a fresh crew member.

        The rank is *derived* from the level and the skills (the client does the
        same in ``generateCompactDescr``), so a 100% crew member of a nation
        whose table starts at a higher rank is not shown as a private.
        """
        config = self.configs.get(nyc.NATIONS[passport.nation_id]) \
            if 0 <= passport.nation_id < len(nyc.NATIONS) else None
        rank_ids = config.role_rank_ids(role) if config is not None else ()
        return generate_compact_descr(
            passport.as_tuple(),
            vehicle_type_id=vehicle_type_id,
            role=role,
            role_level=role_level,
            skills=skills,
            free_skills=free_skills,
            rank_index=rank_index,
            levels_to_next_rank=levels_to_next_rank,
            max_rank_index=(len(rank_ids) - 1) if rank_ids else None,
        )


#: ``common/items/vehicles.VEHICLE_CLASS_TAGS`` - the class of a vehicle, used
#: by the retraining rules.
VEHICLE_CLASS_TAGS = frozenset(("lightTank", "mediumTank", "heavyTank", "SPG", "AT-SPG"))


class TankmanDescr(object):
    """``common/items/tankmen.TankmanDescr`` - the state behind the bytes.

    The client keeps every crew member in one compact descriptor and mutates it
    when the player learns a skill, retrains, changes the role or the passport.
    The server has to do the same arithmetic, because the client recomputes the
    item from the descriptor the server sends: a wrong ``roleLevel`` or a missing
    skill is visible in the personal file immediately.
    """

    def __init__(self, compact_descr: bytes, rank_ids=(), max_rank_index: Optional[int] = None):
        self.dossier = b""
        #: The rank table of the nation (``<roleRanks>`` of the role); the rank
        #: of a crew member only exists for the label the client shows.
        self.rank_ids = tuple(rank_ids)
        if max_rank_index is None and self.rank_ids:
            max_rank_index = len(self.rank_ids) - 1
        self.max_rank_index = max_rank_index
        self._read(compact_descr)

    # -- reading --------------------------------------------------------
    def _read(self, compact_descr: bytes) -> None:
        data = bytes(compact_descr)
        header, vehicle_type_id, role_id, role_level, num_skills = struct.unpack(
            "5B", data[:5])
        if header & 0x0F != TANKMAN_ITEM_TYPE:
            raise TankmenError("not a tankman descriptor")
        if not 0 <= role_id < len(SKILL_NAMES):
            raise TankmenError("unknown role %s" % role_id)
        if role_level > MAX_SKILL_LEVEL:
            raise TankmenError("role level %s" % role_level)
        self.nation_id = header >> 4
        self.vehicle_type_id = vehicle_type_id
        self.role = SKILL_NAMES[role_id]
        if self.role not in ROLES:
            raise TankmenError("unknown role %s" % role_id)
        self.role_level = role_level
        skills = []
        for skill_id in data[5:5 + num_skills]:
            if not 0 <= skill_id < len(SKILL_NAMES):
                raise TankmenError("unknown skill %s" % skill_id)
            skills.append(SKILL_NAMES[skill_id])
        self.skills = skills
        self.last_skill_level = data[5 + num_skills] if num_skills else 0
        offset = 6 + num_skills
        flags = data[offset]
        self.is_female = bool(flags & 1)
        self.is_premium = bool(flags & 2)
        self.free_skills_number = flags >> 2
        if self.free_skills_number == len(self.skills) and self.free_skills_number:
            # ``__initFromCompactDescr``: free skills are always at 100%
            self.last_skill_level = MAX_SKILL_LEVEL
        block = data[offset + 1:offset + 13].ljust(12, b"\x00")
        self.first_name_id, self.last_name_id, self.icon_id, rank, self.free_xp = \
            struct.unpack("<4Hi", block)
        self.rank_index = rank & 31
        self.levels_to_next_rank = rank >> 5
        self.dossier = data[offset + 13:]

    # -- writing --------------------------------------------------------
    def pack(self) -> bytes:
        """``TankmanDescr.makeCompactDescr``."""
        return generate_compact_descr(
            self.passport,
            vehicle_type_id=self.vehicle_type_id,
            role=self.role,
            role_level=self.role_level,
            skills=self.skills[self.free_skills_number:],
            free_skills=self.skills[:self.free_skills_number],
            last_skill_level=self.last_skill_level,
            rank_index=self.rank_index,
            levels_to_next_rank=self.levels_to_next_rank,
            free_xp=self.free_xp,
            dossier_compact_descr=self.dossier,
            max_rank_index=self.max_rank_index,
        )

    @property
    def passport(self) -> Tuple[int, bool, bool, int, int, int]:
        return (self.nation_id, self.is_premium, self.is_female,
                self.first_name_id, self.last_name_id, self.icon_id)

    @property
    def free_skills(self) -> List[str]:
        return list(self.skills[:self.free_skills_number])

    def skill_levels(self):
        """``(skill, level)`` pairs; only the last skill may be below 100."""
        for index, skill in enumerate(self.skills):
            level = MAX_SKILL_LEVEL if skill != self.skills[-1] else self.last_skill_level
            yield (skill, level)

    def skill_level(self, skill: str) -> Optional[int]:
        if skill not in self.skills:
            return None
        return MAX_SKILL_LEVEL if skill != self.skills[-1] else self.last_skill_level

    def total_xp(self) -> int:
        """``TankmanDescr.totalXP``: everything the crew member is worth."""
        xp = self.free_xp + LEVEL_XP_COSTS[self.role_level]
        num_skills = len(self.skills) - self.free_skills_number
        if num_skills:
            xp += LEVEL_XP_COSTS[self.last_skill_level] * 2 ** num_skills
            for index in range(1, num_skills):
                xp += LEVEL_XP_COSTS[MAX_SKILL_LEVEL] * 2 ** index
        return xp

    # -- experience -----------------------------------------------------
    def add_xp(self, xp: int) -> None:
        """``TankmanDescr.addXP``: levels up the role, then the last skill."""
        self.free_xp = min(MAX_FREE_XP, self.free_xp + int(xp))
        while self.role_level < MAX_SKILL_LEVEL:
            cost = level_up_xp_cost(self.role_level, 0)
            if cost > self.free_xp:
                break
            self.free_xp -= cost
            self.role_level += 1
            self.update_rank_on_level_up()
        if self.role_level == MAX_SKILL_LEVEL and self.skills:
            self.level_up_last_skill()

    def level_up_last_skill(self) -> None:
        num_skills = len(self.skills) - self.free_skills_number
        while self.last_skill_level < MAX_SKILL_LEVEL:
            cost = level_up_xp_cost(self.last_skill_level, num_skills)
            if cost > self.free_xp:
                break
            self.free_xp -= cost
            self.last_skill_level += 1
            self.update_rank_on_level_up()

    # -- skills ---------------------------------------------------------
    def can_add_skill(self, skill: str) -> bool:
        if skill not in ACTIVE_SKILLS or skill in self.skills:
            return False
        if skill not in SKILLS_BY_ROLES.get(self.role, ()):
            return False
        if self.role_level != MAX_SKILL_LEVEL:
            return False
        if self.skills and self.last_skill_level != MAX_SKILL_LEVEL:
            return False
        return True

    def add_skill(self, skill: str) -> None:
        if not self.can_add_skill(skill):
            raise TankmenError("cannot learn %s" % (skill,))
        self.skills.append(skill)
        self.last_skill_level = 0
        self.level_up_last_skill()

    def is_free_drop_skills(self) -> bool:
        """``TankmanDescr.isFreeDropSkills``: nothing but free skills learned."""
        if len(self.skills) < 1 + self.free_skills_number:
            return True
        if (len(self.skills) == 1 + self.free_skills_number
                and self.last_skill_level == 0):
            return True
        return False

    def drop_skills(self, xp_reuse_fraction: float = 0.0) -> None:
        """``TankmanDescr.dropSkills``: forget every learned skill."""
        if not 0.0 <= xp_reuse_fraction <= 1.0:
            raise TankmenError("bad xp reuse fraction")
        if not self.skills:
            raise TankmenError("no skills to drop")
        previous_total = self.total_xp()
        if self.levels_to_next_rank != 0:
            num_skills = len(self.skills) - self.free_skills_number
            if num_skills < 1:
                raise TankmenError("cannot drop free skills")
            self.levels_to_next_rank += self.last_skill_level
            if num_skills > 1:
                self.levels_to_next_rank += MAX_SKILL_LEVEL * (num_skills - 1)
        del self.skills[self.free_skills_number:]
        self.last_skill_level = MAX_SKILL_LEVEL if self.free_skills_number else 0
        if xp_reuse_fraction != 0.0:
            self.add_xp(int(xp_reuse_fraction * (previous_total - self.total_xp())))

    # -- the role, the vehicle and the passport -------------------------
    def respecialize(self, new_vehicle_type_id: int, vehicle_tags, new_vehicle_tags,
                     min_new_role_level: int = MIN_ROLE_LEVEL,
                     vehicle_change_role_level_loss: float = 0.0,
                     class_change_role_level_loss: float = 0.0) -> None:
        """``TankmanDescr.respecialize``: retrain onto another vehicle.

        ``vehicle_tags``/``new_vehicle_tags`` are the tags of the vehicle the
        crew member was trained on and of the vehicle it is retrained to; a
        different class of vehicle costs the class-change part of the level loss.
        """
        role_level_loss = 0.0
        if int(new_vehicle_type_id) != self.vehicle_type_id:
            role_level_loss += vehicle_change_role_level_loss
        same_class = bool(set(vehicle_tags) & set(new_vehicle_tags) & VEHICLE_CLASS_TAGS)
        if not same_class:
            role_level_loss += class_change_role_level_loss
        new_level = int(round(self.role_level * (1.0 - role_level_loss)))
        new_level = max(int(min_new_role_level), new_level)
        self.vehicle_type_id = int(new_vehicle_type_id)
        if new_level > self.role_level:
            self.update_rank_on_level_up(new_level - self.role_level)
            self.role_level = new_level
        elif new_level < self.role_level:
            if self.levels_to_next_rank != 0:
                self.levels_to_next_rank += self.role_level - new_level
            self.role_level = new_level
            self.add_xp(0)

    def change_role(self, role: str, min_role_level: int = MIN_ROLE_LEVEL) -> None:
        """Learn another role: the client sends the role index of the new one."""
        if role not in ROLES:
            raise TankmenError("unknown role %r" % (role,))
        if role == self.role:
            raise TankmenError("already the %s" % role)
        for skill in list(self.skills):
            if skill not in SKILLS_BY_ROLES.get(role, ()):
                # A skill the new role cannot hold would make the client's own
                # ``TankmanDescr`` refuse the whole descriptor.
                index = self.skills.index(skill)
                del self.skills[index]
        self.role = role
        if self.role_level < min_role_level:
            self.role_level = min_role_level

    def validate_passport(self, is_premium: bool, is_female, fn_group_id: int,
                          first_name_id: Optional[int], ln_group_id: int,
                          last_name_id: Optional[int], i_group_id: int,
                          icon_id: Optional[int], groups) -> Tuple[bool, str, Optional[tuple]]:
        """``TankmanDescr.validatePassport`` against the nation's groups."""
        if is_female is None:
            is_female = self.is_female
        for group_id, section, value, label in (
                (fn_group_id, "firstNames", first_name_id, "first name"),
                (ln_group_id, "lastNames", last_name_id, "last name"),
                (i_group_id, "icons", icon_id, "icon")):
            if value is None:
                continue
            if not 0 <= group_id < len(groups):
                return (False, "Invalid %s group" % label.split()[0], None)
            group = groups[group_id]
            if group.not_in_shop:
                return (False, "Not in shop", None)
            if bool(group.is_female) != bool(is_female):
                return (False, "Invalid group sex", None)
            known = {"firstNames": group.first_names, "lastNames": group.last_names,
                     "icons": group.icons}[section]
            if value not in known:
                return (False, "Invalid %s" % label, None)
        return (True, "", (
            bool(is_female),
            self.first_name_id if first_name_id is None else int(first_name_id),
            self.last_name_id if last_name_id is None else int(last_name_id),
            self.icon_id if icon_id is None else int(icon_id),
        ))

    def replace_passport(self, ctx) -> None:
        """``TankmanDescr.replacePassport``."""
        self.is_female, self.first_name_id, self.last_name_id, self.icon_id = ctx

    def update_rank_on_level_up(self, num_levels: int = 1) -> None:
        """``TankmanDescr.__updateRankAtSkillLevelUp``.

        Without the nation's rank table the rank stays where it is - the client
        only uses it for the label of the crew member.
        """
        if num_levels < self.levels_to_next_rank:
            self.levels_to_next_rank -= num_levels
            return
        if not self.rank_ids:
            # Without the nation's rank table there is nothing to promote to.
            return
        max_rank = max(0, len(self.rank_ids) - 1)
        while num_levels >= self.levels_to_next_rank > 0:
            num_levels -= self.levels_to_next_rank
            self.rank_index = min(self.rank_index + 1, max_rank)
            self.levels_to_next_rank = (SKILL_LEVELS_PER_RANK
                                       if self.rank_index < max_rank else 0)


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
        "role": SKILL_NAMES[role_id] if 0 <= role_id < len(SKILL_NAMES) else None,
        "roleLevel": role_level,
        "numSkills": num_skills,
    }


__all__ = [
    "ACTIVE_SKILLS",
    "COMMON_SKILLS",
    "LEVEL_XP_COSTS",
    "MAX_FREE_XP",
    "MAX_SKILL_LEVEL",
    "SKILLS_BY_ROLES",
    "TankmanDescr",
    "VEHICLE_CLASS_TAGS",
    "level_up_xp_cost",
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
