"""The account the local server serves: wallet, garage, crew and the event.

The client applies what this module produces through its own
``Account._update`` (``client/Account.py``), so every section of the diff has to
match the shape the client's helpers read:

======================  =====================================================
``diff['account']``     simple account values (``Stats``: ``_ACCOUNT_STATS``)
``diff['stats']``       ``Stats.synchronize`` keys - ``unlocks``,
                        ``eliteVehicles``, ``vehTypeXP``, ``slots``,
                        ``berths``, ``freeXP``, ``vehicles`` ...
``diff['inventory']``   ``{itemType: {'compDescr': {...}, ...}}`` for vehicles
                        and tankmen, ``{intCD: count}`` for the rest, and the
                        appearance section (``ITEMS``/``OUTFITS``) for the
                        customization window and the outfits of the vehicles
``diff['tokens']``      ``{token: (expiryTime, count)}``
``diff['newYear']``     the New Year state (``ClientNewYear.synchronize``)
``diff[('eventsData','_r')]`` / ``diff['eventsData']``
                        quests, actions and notifications (see
                        :mod:`server.game.events`)
``diff[('goodies','_r')]``  personal discounts and boosters
======================  =====================================================

A full sync omits ``prevRev`` (that is what makes the client treat the diff as a
full sync) and uses the ``('section', '_r')`` replacement keys; an incremental
diff carries ``prevRev``/``rev`` and only the changed leaves.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..game import items as items_module
from ..game import new_year as ny_module
from ..game import ny_constants as nyc
from ..game import shop as shop_module
from ..game import rewards as rewards_module
from ..game import tankmen as tankmen_module
from ..game import vehicle_descr as descr_module
from ..game import catalog as catalog_module
from ..game import customization as c11n_types
from ..game import dossier as dossier_module
from . import customization as customization_module
from ..game.catalog import Catalog, VehicleType
from ..game import events as events_module
from ..game import goodies as goodies_module
from ..game.events import EventDataBuilder

LOG = logging.getLogger("wotny.state")

#: ``GUI_ITEM_TYPE`` indexes of the client (``gui.shared.gui_items``).
VEHICLE = items_module.GUI_ITEM_TYPE.VEHICLE
TANKMAN = items_module.GUI_ITEM_TYPE.TANKMAN

#: ``vehicles.NUM_EQUIPMENT_SLOTS`` - the four slots of a regular equipment
#: layout.  ``VehicleEquipment`` builds a container of exactly this size out of
#: the ``eqs``/``eqsLayout`` lists of the inventory, so they are padded.
EQUIPMENT_LAYOUT_SIZE = 4


def _pad_slots(values, size: int = EQUIPMENT_LAYOUT_SIZE) -> List[int]:
    """``values`` as a fixed-size list of ints (missing slots become ``0``)."""
    padded = [int(value or 0) for value in list(values or ())[:size]]
    return padded + [0] * (size - len(padded))

#: ``constants.ACCOUNT_ATTR`` - what ``account['attrs']`` carries.  The premium
#: bit is the one the lobby reads (``account_helpers.isPremiumAccount``), and it
#: is bit 32, so the attribute is the 64-bit value the entity def declares.
ACCOUNT_ATTR_PREMIUM = 4294967296

#: The database id of the local account (``welcome.databaseID``, the
#: ``databaseID`` of a dossier or rating request).  The client keys the dossiers
#: of other players by it, so the one account this server has is always ``1``.
DATABASE_ID = 1

#: Starting wallet of a fresh local account.
DEFAULT_CREDITS = 10000000
DEFAULT_GOLD = 25000
DEFAULT_FREE_XP = 50000

#: Vehicles the starting garage skips: the client marks test and event tanks
#: ``secret``/``unrecoverable`` in its own ``list.xml``, and premium tanks are
#: what the New Year boxes hand out (they are not part of the free garage).
SKIP_TAGS = ("secret", "unrecoverable")

#: ``grant_garage`` modes.
GARAGE_RESEARCHABLE = "researchable"  # every tank a player can research
GARAGE_PREMIUM = "premium"            # only the premium tanks
GARAGE_ALL = "all"                    # everything the client knows


@dataclass
class VehicleEntry:
    """One vehicle of the garage."""

    inv_id: int
    name: str
    nation_id: int
    innation_id: int
    compact_descr: int
    level: int = 1
    tags: Tuple[str, ...] = ()
    #: ``{'chassis': id, 'engine': id, 'fuelTank': id, 'radio': id}``.
    components: Dict[str, int] = field(default_factory=dict)
    #: ``[[turretID, gunID], ...]`` - one pair per turret position.
    turrets: List[Tuple[int, int]] = field(default_factory=list)
    #: Tankman inventory ids that make up the crew.
    crew: List[int] = field(default_factory=list)
    settings: int = 0
    #: The equipment the vehicle carries (``eqs``): one compact descriptor per
    #: slot, ``0`` for an empty one.  ``NUM_EQUIPMENT_SLOTS`` = 4 - three
    #: regular consumables and one battle booster
    #: (``gui_items.vehicle_equipment``).
    equipment: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    #: The saved equipment layout (``eqsLayout``), the same four slots.
    equipment_layout: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    #: The loaded ammunition (``shells``), flat ``[intCD, count, ...]`` pairs.
    shells: List[int] = field(default_factory=list)
    #: The ammunition layout of every ``(turret, gun)`` pair (``shellsLayout``,
    #: keyed by the client's ``Vehicle.shellsLayoutIdx``).
    shells_layout: Dict[Tuple[int, int], List[int]] = field(default_factory=dict)
    repair: Tuple[int, int] = (0, 0)
    #: ``(lock reason, lock time)`` - ``Vehicle.isLocked`` indexes both.
    lock: Tuple[int, int] = (0, 0)
    #: The optional devices of the three descriptor slots, as *nation-local*
    #: ids (``None`` = empty - device id ``0`` is a real device, the toolbox).
    #: The client reads them out of the vehicle descriptor itself
    #: (``VehicleDescr.optionalDevices``), so they are part of
    #: :meth:`in_battle_descr` and not of a separate inventory section.
    devices: List[Optional[int]] = field(default_factory=lambda: [None, None, None])
    #: The vehicle compact descriptor the client stores under
    #: ``inventory[VEHICLE]['compDescr'][invID]`` (built lazily).
    descriptor: Optional[bytes] = None

    @property
    def is_premium(self) -> bool:
        return "premium" in self.tags

    def in_battle_descr(self, vehicle: VehicleType) -> bytes:
        """The descriptor of this vehicle, as the client's inventory stores it."""
        if self.descriptor is None:
            configuration = dict(self.components)
            if self.turrets:
                configuration["turrets"] = [tuple(pair) for pair in self.turrets]
            devices = [None if value is None else int(value) for value in (self.devices or ())]
            self.descriptor = descr_module.make_compact_descr(
                vehicle, configuration, optional_devices=(devices + [None, None, None])[:3]
            )
        return self.descriptor


def _read_equipment_layout(value) -> List[int]:
    """``eqsLayout`` is a flat list of four compact descriptors.

    Older saves kept it as a mapping, so a mapping is accepted and its values
    are flattened into slot order.
    """
    if isinstance(value, dict):
        slots = [0] * 4
        for key, values in value.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(slots):
                slots[index] = int(values) if not isinstance(values, (list, tuple)) else int(values[0] or 0)
        return slots
    slots = [int(item) for item in (value or ())]
    slots = (slots + [0, 0, 0, 0])[:4]
    return slots


@dataclass
class TankmanEntry:
    """One crew member of the barracks."""

    inv_id: int
    compact_descr: bytes
    nation_id: int
    vehicle_inv_id: int = -1
    role: str = "commander"
    #: The role level packed into the descriptor (50/75/100 for the three
    #: recruitment options of ``Shop.buyTankman``).
    role_level: int = 100
    passport: Optional[tankmen_module.Passport] = None


class AccountState:
    """Everything the local server knows about the player."""

    def __init__(
        self,
        catalog: Catalog,
        name: str = "local_account",
        rng: random.Random = None,
        tankman_factory: tankmen_module.TankmanFactory = None,
        start_time: float = None,
        credits: int = DEFAULT_CREDITS,
        gold: int = DEFAULT_GOLD,
        free_xp: int = DEFAULT_FREE_XP,
    ):
        self.catalog = catalog
        self.name = name
        self.rng = rng or random.Random(20180101)
        self.created_at = float(start_time if start_time is not None else time.time())

        self.credits = int(credits)
        self.gold = int(gold)
        self.free_xp = int(free_xp)
        self.slots = 20
        self.berths = 20
        self.premium_expiry = 0
        self.tutorials_completed = 0xFFFFFFFF & ~(1 << 1)  # no bootcamp prompts
        self.is_premium = False

        self.vehicles: Dict[int, VehicleEntry] = {}
        self.tankmen: Dict[int, TankmanEntry] = {}
        #: ``recycleBin.tankmen.buffer``: ``invID -> (compDescr, dismissedAt)``
        #: of the crew members the player dismissed.  The client shows them with
        #: a *negative* inventory id and restores them through
        #: ``CMD_TMAN_RESTORE``.
        self.dismissed_tankmen: Dict[int, Tuple[bytes, float]] = {}
        self.items: Dict[int, Dict[int, int]] = {}
        #: ``inventory[CUSTOMIZATION]``: the appearance items of the depot and
        #: the outfits mounted on the vehicles (see
        #: :mod:`server.account_rpc.customization`).
        self.customization = customization_module.CustomizationInventory()
        #: What the last diff published per item type (see :meth:`_item_section`).
        self._published_items: Dict[int, Dict[int, int]] = {}
        self.tokens: Dict[str, Tuple[Optional[int], int]] = {}
        self.unlocks: set = set()
        self.elite_vehicles: set = set()
        #: ``stats.vehTypeXP``: the experience of every *vehicle type* (battles
        #: are out of scope, so it stays empty unless something grants XP).
        self.veh_type_xp: Dict[int, int] = {}
        self.int_user_settings: Dict[str, dict] = {}

        #: The id of this account (see :data:`DATABASE_ID`); the dossier and the
        #: rating requests answer for it.
        self.database_id = DATABASE_ID
        #: ``account['globalRating']``.  Ratings are earned in battles, and this
        #: server has none, so every account starts - and stays - at zero.
        self.global_rating = 0
        #: ``diff['badges']``: the badges the profile shows
        #: (``CMD_SELECT_BADGES``; see :mod:`server.account_rpc.profile`).
        self.badges: List[int] = []
        #: ``playerBadges`` of the account dossier: ``{badgeID: time earned}``.
        #: This is the *only* source of "was this badge earned" the client has
        #: (``Badge.isAchieved``), see :meth:`award_badges`.
        self.achieved_badges: Dict[int, int] = {}
        #: ``cache.SPA``: one value per dossier path, as written by
        #: ``CMD_SET_DOSSIER_FIELD``.
        self.dossier_fields: Dict[str, int] = {}

        self.tankman_factory = tankman_factory
        self.events = EventDataBuilder(catalog, start_time=self.created_at)
        #: The vehicles the New Year boxes and chests hand out (built from the
        #: client's own vehicle catalogue, see :mod:`server.game.rewards`).
        self.vehicle_chooser = rewards_module.VehicleRewardChooser(catalog)
        #: The personal offers of the event: one New Year vehicle discount per
        #: vehicle and atmosphere level (see :mod:`server.game.goodies`).  The
        #: same table is published in the shop cache - where the client reads
        #: the definitions - and handed to the event, which only accepts the id
        #: of a level's own range.
        self.goodie_table = goodies_module.new_year_vehicle_discounts(catalog)
        self.new_year = ny_module.NewYearEvent(
            catalog.new_year,
            rng=self.rng,
            tankmen=tankman_factory,
            vehicles=self.vehicle_chooser,
            owned_vehicles=self.owned_vehicle_comp_descrs,
            goodies=self.goodie_table.definitions,
        )
        self.revision = 1
        self._notifications: List[dict] = []
        self._shop_builder = None

    # ------------------------------------------------------------------
    # Building the starting account
    # ------------------------------------------------------------------
    @classmethod
    def new(
        cls,
        catalog: Catalog,
        tankmen_configs: Dict[str, tankmen_module.NationConfig] = None,
        garage_mode: str = GARAGE_RESEARCHABLE,
        achieved_badges: Sequence[int] = None,
        **kwargs,
    ) -> "AccountState":
        """A ready-to-play account: a garage, a crew and the event switched on."""
        state = cls(catalog, **kwargs)
        if tankmen_configs:
            state.tankman_factory = tankmen_module.TankmanFactory(tankmen_configs, state.rng)
            state.new_year.tankmen = state.tankman_factory
        state.grant_garage(mode=garage_mode)
        # ``None`` awards every badge the installation describes: a local
        # account cannot earn one (no battle, no ranked season), and an empty
        # badge page would be less useful than the installation's own table.
        # ``award_badges`` takes an explicit list as well, which is what an
        # account started from a hand-written file uses (see
        # :meth:`AccountState.award_badges`).
        state.award_badges(achieved_badges)
        state.start_new_year()
        return state

    def grant_garage(
        self,
        nation_ids: Sequence[int] = None,
        mode: str = GARAGE_RESEARCHABLE,
        only_premium: bool = False,
    ) -> int:
        """Fill the garage with the vehicles of the catalogue.

        ``mode`` decides what a fresh account owns: every researchable tank
        (``researchable``, the default), only the premium ones (``premium``) or
        everything the client knows (``all``).
        """
        if only_premium:
            mode = GARAGE_PREMIUM
        granted = 0
        for vehicle in sorted(self.catalog.vehicles.all(), key=lambda v: v.compact_descr):
            if nation_ids is not None and vehicle.nation_id not in nation_ids:
                continue
            if any(tag in vehicle.tags for tag in SKIP_TAGS):
                continue
            if mode == GARAGE_RESEARCHABLE and vehicle.is_premium:
                continue
            if mode == GARAGE_PREMIUM and not vehicle.is_premium:
                continue
            self.add_vehicle(vehicle)
            granted += 1
        return granted

    def owned_vehicle_comp_descrs(self) -> set:
        """The vehicle types of the garage (``VehicleType.compact_descr``)."""
        return {entry.compact_descr for entry in self.vehicles.values()}

    def add_vehicle(self, vehicle: VehicleType, crew: bool = True,
                    shells: bool = True) -> VehicleEntry:
        """Put one vehicle (with a stock crew) into the garage.

        ``shells`` follows ``BUY_VEHICLE_FLAG.SHELLS``: a vehicle bought without
        the flag arrives empty, one bought with it carries the default
        ammunition of its stock gun (which the buyer paid for).
        """
        inv_id = self._next_vehicle_inv_id()
        entry = VehicleEntry(
            inv_id=inv_id,
            name=vehicle.name,
            nation_id=vehicle.nation_id,
            innation_id=vehicle.innation_id,
            compact_descr=vehicle.compact_descr,
            level=vehicle.level,
            tags=tuple(vehicle.tags),
            components=dict(vehicle.stock_configuration()),
            turrets=list(vehicle.stock_configuration().get("turrets", ())),
        )
        entry.components.pop("turrets", None)
        if shells:
            self.load_default_ammo(entry, vehicle)
        if crew and self.tankman_factory is not None:
            entry.crew = self._create_crew(entry)
        self.vehicles[inv_id] = entry
        self.unlocks.add(vehicle.compact_descr)
        self.elite_vehicles.add(vehicle.compact_descr)
        return entry

    def load_default_ammo(self, entry: VehicleEntry, vehicle: VehicleType = None) -> int:
        """Load the stock gun's default ammunition into a new vehicle.

        The client falls back to ``gun.defaultAmmo`` only when the server sends
        no layout at all (``Vehicle._parseShells``), so the local server sends
        the same numbers itself: a freshly granted or bought vehicle carries the
        shells its own ``guns.xml`` prescribes.
        """
        vehicle = vehicle or self._vehicle_type(entry)
        if vehicle is None:
            return 0
        turret, gun = self.stock_gun(entry, vehicle)
        if turret is None or gun is None:
            return 0
        ammo = self.catalog.vehicles.default_ammo(vehicle, gun.id)
        if not ammo:
            return 0
        entry.shells = list(ammo)
        entry.shells_layout[catalog_module.layout_key(turret.compact_descr, gun.compact_descr)] = list(ammo)
        return len(ammo) // 2

    @staticmethod
    def stock_gun_for(vehicle: VehicleType):
        """The stock ``(turret, gun)`` of a vehicle *type* (no entry needed)."""
        group = vehicle.turrets[0] if vehicle.turrets else None
        if group is None:
            return None, None
        return group.turret, (group.guns[0] if group.guns else None)

    @staticmethod
    def stock_gun(entry: VehicleEntry, vehicle: VehicleType):
        """The ``(turret, gun)`` components a vehicle's descriptor carries now."""
        pair = entry.turrets[0] if entry.turrets else None
        if pair is None:
            group = vehicle.turrets[0] if vehicle.turrets else None
            if group is None:
                return None, None
            return group.turret, (group.guns[0] if group.guns else None)
        turret_id, gun_id = int(pair[0]), int(pair[1])
        group = vehicle.find_turret_group(turret_id)
        turret = group.turret if group is not None else None
        gun = vehicle.find_gun(gun_id)
        if gun is None and group is not None and group.guns:
            gun = group.guns[0]
        return turret, gun

    def _create_crew(self, entry: VehicleEntry) -> List[int]:
        """A stock crew (100% role) for a vehicle.

        The crew slots come from the vehicle's own ``<crew>`` section (see
        :attr:`server.game.catalog.VehicleType.crew_roles`), so a Hummel gets
        six members with two loaders and a T-34 gets four, its commander also
        covering the gunner role.
        """
        crew = []
        for role in self.crew_roles_of(entry):
            tankman = self.recruit_tankman(entry.nation_id, entry.innation_id, role, entry.inv_id)
            # A seat without a crew member stays in the list as ``None``: the
            # client indexes the crew by slot.
            crew.append(tankman.inv_id if tankman is not None else None)
        return crew

    def crew_roles_of(self, entry: VehicleEntry) -> List[str]:
        """The role of every crew slot of a vehicle, in slot order."""
        return self.crew_roles_of_type(self._vehicle_type(entry))

    @staticmethod
    def crew_roles_of_type(vehicle: Optional[VehicleType]) -> List[str]:
        """The role of every crew slot of a vehicle type, in slot order."""
        slots = tuple(vehicle.crew_roles) if vehicle is not None else ()
        if not slots:
            slots = (("commander",), ("driver",), ("gunner",), ("loader",), ("radioman",))
        return [slot[0] for slot in slots]

    def recruit_tankman(
        self,
        nation_id: int,
        vehicle_innation_id: int,
        role: str,
        vehicle_inv_id: int = -1,
        female: bool = False,
        role_level: int = 100,
    ) -> Optional[TankmanEntry]:
        """Create a crew member and add it to the barracks."""
        if self.tankman_factory is None:
            return None
        try:
            if female:
                passport = self.tankman_factory.make_female_passport(nation_id)
            else:
                passport = self.tankman_factory.make_passport(nation_id, role=role)
            compact = self.tankman_factory.make_tankman_descr(
                passport, vehicle_innation_id, role, role_level=int(role_level)
            )
        except Exception:
            return None
        inv_id = self._next_tankman_inv_id()
        entry = TankmanEntry(
            inv_id=inv_id,
            compact_descr=compact,
            nation_id=nation_id,
            vehicle_inv_id=vehicle_inv_id,
            role=role,
            role_level=int(role_level),
            passport=passport,
        )
        self.tankmen[inv_id] = entry
        return entry

    def _next_vehicle_inv_id(self) -> int:
        return max(self.vehicles) + 1 if self.vehicles else 1

    def _next_tankman_inv_id(self) -> int:
        return max(self.tankmen) + 1 if self.tankmen else 1

    def _vehicle_type(self, entry: VehicleEntry) -> Optional[VehicleType]:
        return self.catalog.vehicles.get(entry.compact_descr)

    def vehicle_type(self, entry: Optional[VehicleEntry]) -> Optional[VehicleType]:
        """The vehicle *type* of a garage entry (``None`` when unknown)."""
        return self._vehicle_type(entry) if entry is not None else None

    def vehicle_type_of(self, tankman: TankmanEntry) -> Optional[VehicleType]:
        """The vehicle a crew member sits in (``None`` when it is not in one)."""
        return self.vehicle_type(self.vehicles.get(tankman.vehicle_inv_id))

    # ------------------------------------------------------------------
    # The crew members (``server.account_rpc.barracks``)
    # ------------------------------------------------------------------
    def tankman_config(self, nation_id: int):
        """The nation configuration of the crew (name/rank tables)."""
        factory = self.tankman_factory
        if factory is None:
            return None
        name = nyc.NATIONS[nation_id] if 0 <= int(nation_id) < len(nyc.NATIONS) else None
        return factory.configs.get(name) if name else None

    def tankman_descr(self, entry: TankmanEntry):
        """A mutable ``TankmanDescr`` of one crew member, with its rank table."""
        config = self.tankman_config(entry.nation_id)
        rank_ids = config.role_rank_ids(entry.role) if config is not None else ()
        return tankmen_module.TankmanDescr(entry.compact_descr, rank_ids=rank_ids)

    def set_tankman_descr(self, entry: TankmanEntry, descr) -> None:
        """Store a rewritten descriptor and refresh what the entry caches."""
        entry.compact_descr = descr.pack()
        entry.nation_id = int(descr.nation_id)
        entry.role = descr.role
        entry.role_level = int(descr.role_level)
        config = self.tankman_config(entry.nation_id)
        group_id = 0
        group_name = ""
        if config is not None:
            groups = (config.premium_groups or config.groups) if descr.is_premium \
                else config.groups
            group_id = config.find_group(descr.is_female, descr.first_name_id,
                                         descr.last_name_id, descr.icon_id,
                                         premium=bool(descr.is_premium))
            if 0 <= group_id < len(groups):
                group_name = groups[group_id].name
        entry.passport = tankmen_module.Passport(
            nation_id=int(descr.nation_id), is_premium=bool(descr.is_premium),
            is_female=bool(descr.is_female), first_name_id=int(descr.first_name_id),
            last_name_id=int(descr.last_name_id), icon_id=int(descr.icon_id),
            group_id=int(group_id), group_name=group_name)

    # -- the recycle bin of the barracks --------------------------------
    def free_berths(self) -> int:
        """Berths the barracks still has.

        Only the crew members that are *not* sitting in a vehicle take up a
        berth - the client's own ``BarracksSlotsValidator`` counts
        ``getTankmen(~REQ_CRITERIA.TANKMAN.IN_TANK)`` against
        ``stats.tankmenBerthsCount``, so the server checks the same thing.
        """
        in_barracks = sum(1 for entry in self.tankmen.values()
                          if entry.vehicle_inv_id in (-1, None))
        return max(0, int(self.berths) - in_barracks)

    def has_free_berth(self) -> bool:
        return self.free_berths() > 0

    def dismissed_at(self, inv_id: int) -> Optional[float]:
        record = self.dismissed_tankmen.get(int(inv_id))
        return None if record is None else float(record[1])

    def has_dismissed_tankman(self, inv_id: int) -> bool:
        return int(inv_id) in self.dismissed_tankmen

    def dismiss_tankman(self, entry: TankmanEntry, now: float = None) -> None:
        """Move a crew member out of the barracks into the recycle bin."""
        self.tankmen.pop(entry.inv_id, None)
        entry.vehicle_inv_id = -1
        self.dismissed_tankmen[int(entry.inv_id)] = (
            entry.compact_descr, float(now if now is not None else time.time()))

    def restore_tankman(self, inv_id: int) -> TankmanEntry:
        """Put a dismissed crew member back into the barracks."""
        inv_id = int(inv_id)
        record = self.dismissed_tankmen.pop(inv_id, None)
        if record is None:
            raise KeyError(inv_id)
        compact_descr, _dismissed_at = record
        descr = None
        config = self.tankman_config(tankmen_module.parse_compact_descr(compact_descr)["nationID"])
        rank_ids = ()
        if config is not None:
            role = tankmen_module.parse_compact_descr(compact_descr)["role"]
            rank_ids = config.role_rank_ids(role) if role else ()
        descr = tankmen_module.TankmanDescr(compact_descr, rank_ids=rank_ids)
        entry = TankmanEntry(
            inv_id=inv_id,
            compact_descr=compact_descr,
            nation_id=int(descr.nation_id),
            vehicle_inv_id=-1,
            role=descr.role,
            role_level=int(descr.role_level),
        )
        self.tankmen[inv_id] = entry
        self.set_tankman_descr(entry, descr)
        return entry

    def recycle_bin_diff(self, inv_ids=()) -> dict:
        """The ``recycleBin`` section of a diff (only the named crew members)."""
        buffer: Dict = {}
        for inv_id in inv_ids or ():
            inv_id = int(inv_id)
            record = self.dismissed_tankmen.get(inv_id)
            if record is None:
                buffer[inv_id] = None
            else:
                buffer[inv_id] = (record[0], record[1])
        return {"tankmen": {"buffer": buffer}}

    # ------------------------------------------------------------------
    # The New Year event
    # ------------------------------------------------------------------
    def start_new_year(self, state: int = nyc.NY_STATE.IN_PROGRESS) -> None:
        """Switch the event on and give the account its first boxes.

        Every box the client knows gets a handful of copies - including the
        "present" boxes, which are the gifts of the event and therefore cannot
        be earned by the player otherwise.
        """
        self.new_year.state.state = state
        self.new_year.stock()
        self.revision += 1
        self.announce_new_year()

    def announce_new_year(self) -> bool:
        """Announce the event in the client's service channel.

        A notification whose type starts with neither ``cmd_`` nor ``promo_``
        becomes a message of the lobby's service channel
        (``SystemMessagesInterface.__processNotifications`` ->
        ``ActionNotificationFormatter``), which is how the live server announced
        a running event; the texts are the local server's own and travel by
        language (``helpers.getLocalizedData``).  The announcement rides in the
        notification list of the *full* sync only - the client treats any
        notification list of an update as freshly added, so repeating it there
        would re-open the space and the message on every diff.
        """
        announced = self.events.announce(
            "ny18:started:%s" % self.new_year.state.state,
            events_module.localized(
                cn="新年攻势：收集玩具、开启箱子并获得坦克折扣！",
                ru="Новогоднее наступление: собирайте игрушки, открывайте "
                   "ящики и получайте скидки на технику!",
                en="New Year Offensive: collect toys, open boxes and claim "
                   "vehicle discounts!",
            ),
        )
        LOG.info("the event announced itself (state %s)", self.new_year.state.state)
        return announced

    def announce_new_year_level(self, level: int) -> bool:
        """Announce the atmosphere level the tree just reached.

        ``NewYearResult.notifications`` carries ``NY_LEVEL_UP`` whenever a
        decoration raised the level (``NewYearEvent._set_level``); the level-up
        window itself is opened by the client (``_NewYearController`` reacts to
        the new ``level`` in the diff), so what the server adds is the line in
        the service channel - one message per level, never twice.
        """
        level = int(level)
        return self.events.announce(
            "ny18:level:%d" % level,
            events_module.localized(
                cn="新年树升到了 %d 级！" % level,
                ru="Новогодняя ёлка выросла до %d уровня!" % level,
                en="Your New Year tree reached level %d!" % level,
            ),
        )

    def sync_tokens(self) -> Dict[str, Tuple[Optional[int], int]]:
        """Merge the event tokens into the account token table."""
        for token, value in self.new_year.token_diff().items():
            self.tokens[token] = value
        return self.tokens

    # ------------------------------------------------------------------
    # Diffs
    # ------------------------------------------------------------------
    def _inventory_diff(self) -> dict:
        inventory: Dict[int, dict] = {VEHICLE: {}, TANKMAN: {}}
        vehicles = inventory[VEHICLE]
        vehicles["compDescr"] = self._vehicle_descrs()
        # ``crew`` is one *list* per vehicle, indexed by crew slot: the client
        # walks it slot by slot and reads ``None`` as an empty seat
        # (``Vehicle._buildCrew``), so a mapping would lose the slot order.
        vehicles["crew"] = {entry.inv_id: list(entry.crew) for entry in self.vehicles.values()}
        vehicles["settings"] = {entry.inv_id: entry.settings for entry in self.vehicles.values()}
        vehicles["eqs"] = {entry.inv_id: list(entry.equipment) for entry in self.vehicles.values()}
        vehicles["eqsLayout"] = {entry.inv_id: list(entry.equipment_layout) for entry in self.vehicles.values()}
        vehicles["shells"] = {entry.inv_id: list(entry.shells) for entry in self.vehicles.values()}
        vehicles["shellsLayout"] = {entry.inv_id: dict(entry.shells_layout) for entry in self.vehicles.values()}
        vehicles["repair"] = {entry.inv_id: tuple(entry.repair) for entry in self.vehicles.values()}
        vehicles["lock"] = {entry.inv_id: tuple(entry.lock) for entry in self.vehicles.values()}

        inventory[TANKMAN].update(self.tankman_inventory_diff())

        for item_type, values in self.items.items():
            inventory[item_type] = dict(values)
        # The appearance items: the depot and the outfits of every vehicle.  A
        # full sync publishes the whole section, which is also what makes the
        # client's caches and this account start from the same state.
        inventory[customization_module.CUSTOMIZATION_INVENTORY] = \
            self.customization.full_sections()
        return inventory

    def tankman_inventory_diff(self) -> dict:
        """The ``inventory[8]`` section (``compDescr`` + ``vehicle`` per crew)."""
        return {
            "compDescr": {entry.inv_id: entry.compact_descr for entry in self.tankmen.values()},
            "vehicle": {entry.inv_id: entry.vehicle_inv_id for entry in self.tankmen.values()},
        }

    def _vehicle_descrs(self) -> Dict[int, bytes]:
        """``invID -> vehicle compact descriptor`` for the whole garage."""
        result: Dict[int, bytes] = {}
        for entry in self.vehicles.values():
            vehicle = self._vehicle_type(entry)
            if vehicle is None:
                continue
            try:
                result[entry.inv_id] = entry.in_battle_descr(vehicle)
            except descr_module.VehicleDescrError:
                continue
        return result

    # ------------------------------------------------------------------
    # The profile: the account dossier, its badges and the vehicle locks
    # ------------------------------------------------------------------
    def award_badges(self, badge_ids: Sequence[int] = None, when: int = None) -> None:
        """Mark badges of the installation as earned.

        A badge is earned in the live game by a ranked season, a personal
        mission or a global map result - none of which can happen here, because
        this server has no battles.  ``None`` therefore awards every badge the
        catalogue knows (``item_defs/badges.xml``, see
        :mod:`server.game.badges`), so the badge page of the client is usable;
        pass a sequence of ids for a smaller set and an empty one for none.
        ``when`` is the time the badge was earned and defaults to the creation
        time of the account.
        """
        if badge_ids is None:
            badge_ids = sorted(self.catalog.badges)
        stamps = int(when if when is not None else self.created_at)
        for badge_id in badge_ids:
            self.achieved_badges[int(badge_id)] = stamps

    def account_dossier(self) -> bytes:
        """``stats['dossier']`` - the compact descriptor of the account dossier.

        The descriptor is the client's own format (:mod:`server.game.dossier`):
        the ``total`` block carries the date of the account (the profile window
        shows it) and ``playerBadges`` the badges it earned.  Every statistics
        block stays empty - this account never fought a battle, and its dossier
        says so.
        """
        return dossier_module.build(dossier_module.ACCOUNT, {
            dossier_module.TOTAL_BLOCK: dossier_module.pack_total(int(self.created_at)),
            dossier_module.BADGES_BLOCK: dossier_module.pack_badges(self.achieved_badges),
        })

    def vehicle_locks(self) -> Dict[int, int]:
        """``cache['vehsLock']`` - the lock of every locked vehicle.

        The client merges this section into its per-vehicle ``lock`` entry
        (``Inventory.synchronize`` -> ``synchronizeDicts``) and reloads the
        hangar and the tech tree from it (``CurrentVehicle``,
        ``techtree/listeners``), so the values have the shape the ``inventory``
        section publishes.  The live game locks a vehicle while it is in a
        battle; only a lock that is really set on a garage entry travels here.
        """
        return {entry.inv_id: tuple(entry.lock) for entry in self.vehicles.values()
                if int(entry.lock[0])}

    def _stats_diff(self) -> dict:
        return {
            # ``stats['dossier']``: the creation time of the account and the
            # badges it earned, every statistic block empty (see
            # :meth:`account_dossier`).
            "dossier": self.account_dossier(),
            "slots": self.slots,
            "berths": self.berths,
            "freeXP": self.free_xp,
            "unlocks": set(self.unlocks),
            "eliteVehicles": set(self.elite_vehicles),
            "vehTypeXP": self.veh_type_xp_diff(),
            "tutorialsCompleted": self.tutorials_completed,
            "vehicleSellsLeft": 0,
            "freeTMenLeft": 0,
            "freeVehiclesLeft": 0,
        }

    def veh_type_xp_diff(self) -> Dict[int, int]:
        """``stats.vehTypeXP``: every owned vehicle plus anything with XP.

        ``Stats.synchronize`` merges this section key by key
        (``synchronizeDicts``), so publishing the whole table is idempotent and
        an update only has to carry the vehicles it changed.
        """
        result = {entry.compact_descr: int(self.veh_type_xp.get(entry.compact_descr, 0))
                  for entry in self.vehicles.values()}
        for compact, xp in self.veh_type_xp.items():
            result.setdefault(int(compact), int(xp))
        return result

    def account_attrs(self) -> int:
        """``account['attrs']`` - the premium bit when the account has premium."""
        if self.premium_expiry > time.time() or self.is_premium:
            return ACCOUNT_ATTR_PREMIUM
        return 0

    def account_diff(self) -> dict:
        """The ``account`` section of a diff (premium state, rating)."""
        return {
            "attrs": self.account_attrs(),
            "premiumExpiryTime": int(self.premium_expiry),
            "globalRating": int(self.global_rating),
        }

    def add_premium_days(self, days: int) -> int:
        """Extend the premium account by ``days``; returns the new expiry."""
        days = int(days)
        if days > 0:
            base = max(self.premium_expiry, int(time.time()))
            self.premium_expiry = base + days * 86400
            self.is_premium = True
        return int(self.premium_expiry)

    def wallet_diff(self) -> dict:
        """The diff a wallet command publishes (economics, account, stats)."""
        return self.incremental_diff(extra={
            "economics": self.economics_diff(),
            "account": self.account_diff(),
            "stats": self._stats_diff(),
        })

    def vehicle_configurations(self) -> Dict[int, dict]:
        """What the client needs to pack a vehicle descriptor for each tank.

        The descriptor string itself is built inside the client (it owns
        ``items.vehicles`` and the ``item_defs`` data), so the server sends the
        chosen modules by their nation-local ids and the bridge assembles the
        descriptor with the client's own API.
        """
        return {
            entry.inv_id: {
                "name": entry.name,
                "nationID": entry.nation_id,
                "innationID": entry.innation_id,
                "level": entry.level,
                "components": dict(entry.components),
                "turrets": [list(pair) for pair in entry.turrets],
            }
            for entry in self.vehicles.values()
        }

    def full_diff(self) -> dict:
        """A complete snapshot (``prevRev`` absent -> the client replaces state).

        The client reads ``prevRev`` to tell a full sync from a diff
        (``Account._update``) and its ``AccountSyncData`` refuses a diff whose
        ``prevRev`` is not the revision it asked from, so a full sync simply
        leaves the key out - and ``AccountSyncData.synchronize`` then clears its
        caches before applying the sections.
        """
        self.revision += 1
        self.sync_tokens()
        # A full sync replaces the client's caches, so every item travels again
        # and the delta memory starts from what was just published.
        self._published_items = {item_type: dict(values)
                                 for item_type, values in self.items.items() if values}
        self.events.set_new_year_state(self.new_year.state.state)
        return {
            "rev": self.revision,
            "inventory": self._inventory_diff(),
            "stats": self._stats_diff(),
            "account": self.account_diff(),
            "economics": {
                "credits": self.credits,
                "gold": self.gold,
                "freeXP": self.free_xp,
            },
            "tokens": dict(self.tokens),
            # The client's ``ClientRecycleBin`` merges the sections of a diff
            # into ``cache['recycleBin']`` and its ``RecycleBinRequester`` reads
            # ``tankmen``/``vehicles`` from there, so the dismissed crew members
            # travel in the sync as the descriptors they were dismissed with.
            "recycleBin": self.recycle_bin_diff(self.dismissed_tankmen),
            "newYear": self.new_year.full_new_year_diff(),
            ("goodies", "_r"): self.new_year.goodies_diff(),
            ("serverSettings", "_r"): self.server_settings(),
            ("eventsData", "_r"): self.events.events_data(),
            # The badges of the profile (``ClientBadges.synchronize``) and the
            # dossier fields (``Stats.synchronize`` -> ``cache['SPA']``).
            "badges": list(self.badges),
            # ``Stats.synchronize`` reads these three out of ``cache``:
            # ``SPA`` is the dossier paths ``CMD_SET_DOSSIER_FIELD`` wrote and
            # ``vehsLock`` the lock of every vehicle.  ``mayConsumeWalletResources``
            # is the flag the whole wallet hangs on (``StatsRequester
            # .mayConsumeWalletResources`` defaults to *false*, which would leave
            # ``WalletController`` syncing forever and refuse every spend), so the
            # full sync publishes it with the ``serverStats`` field of the same
            # name (``server/account_rpc/data.py``).
            "cache": {"SPA": dict(self.dossier_fields),
                      "vehsLock": self.vehicle_locks(),
                      "mayConsumeWalletResources": True},
        }

    def incremental_diff(
        self,
        new_year_result: ny_module.NewYearResult = None,
        extra: dict = None,
        events_data: bool = False,
    ) -> dict:
        """A diff with only what changed, applied on top of the client's state.

        ``events_data`` forces the ``('eventsData', '_r')`` replacement section
        into the diff; it is needed whenever the event state (``INGAME_EVENTS``)
        changes, because the client cannot apply an incremental ``eventsData``
        dictionary (see :meth:`server.game.events.EventDataBuilder.events_data_diff`).
        """
        previous = self.revision
        self.revision += 1
        diff: Dict = {"prevRev": previous, "rev": self.revision}
        if new_year_result is not None and new_year_result.diff:
            for key, value in new_year_result.diff.items():
                diff[key] = value
        if extra:
            for key, value in extra.items():
                if key in diff and isinstance(diff[key], dict) and isinstance(value, dict):
                    merged = dict(diff[key])
                    merged.update(value)
                    diff[key] = merged
                else:
                    diff[key] = value
        if events_data:
            # Without the notifications: the client reloads the hangar space
            # whenever it is handed the notification list again.
            self.events.set_new_year_state(self.new_year.state.state)
            diff[("eventsData", "_r")] = self.events.events_data_diff()
        return diff

    @property
    def shop_builder(self) -> shop_module.ShopBuilder:
        """The shop cache builder of this installation (prices, revisions)."""
        if self._shop_builder is None:
            self._shop_builder = shop_module.ShopBuilder(
                self.catalog, goodies=self.goodie_table)
        return self._shop_builder

    def shop_payload(self) -> dict:
        """The shop cache (``CMD_SYNC_SHOP``, streamed to the client)."""
        return self.shop_builder.payload()

    def price_of(self, compact_descr: int):
        """``(credits, gold)`` of one shop item, or ``None`` when it is unpriced."""
        return self.shop_builder.price(compact_descr)

    # ------------------------------------------------------------------
    # Items of the depot (shells, equipment, consumables, devices)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Personal offers (the ``goodies`` section of the account)
    # ------------------------------------------------------------------
    def active_goodie_ids(self) -> List[int]:
        """The ids of the goodies this account has (``('goodies', '_r')``)."""
        return [int(uid) for uid in self.new_year.state.goodies]

    def goodie_for_vehicle(self, compact_descr: int):
        """The best offer on a vehicle this account owns, or ``None``.

        ``ShopRequester.getPersonalVehicleDiscountPrice`` reads the same two
        tables the same way (a definition of the shop cache, matched against the
        ids of the account section), so the price the client shows and the price
        this server charges come from one goodie.
        """
        return self.goodie_table.best_for_vehicle(
            int(compact_descr), active=self.active_goodie_ids())

    def consume_goodie(self, uid: int) -> Dict[int, list]:
        """Use a goodie up and publish the account section without it.

        A goodie whose counter reaches zero leaves the account
        (``common/goodies/Goodies.remove``) - and that is what makes the client
        drop the discount: ``ShopRequester.__personalDiscountsByTarget`` keeps
        only the definitions whose id is still in the account's own goodies.
        """
        state = self.new_year.state
        current = state.goodies.get(int(uid))
        if current is not None:
            remaining = int(current[2]) - 1
            if remaining > 0:
                state.goodies[int(uid)] = (int(current[0]), int(current[1]), remaining)
            else:
                state.goodies.pop(int(uid), None)
        return self.new_year.goodies_diff()

    def item_count(self, item_type: int, compact_descr: int) -> int:
        return int(self.items.get(int(item_type), {}).get(int(compact_descr), 0))

    def add_item(self, item_type: int, compact_descr: int, count: int = 1) -> int:
        """Put ``count`` items of one type into the depot; returns the new count."""
        values = self.items.setdefault(int(item_type), {})
        values[int(compact_descr)] = int(values.get(int(compact_descr), 0)) + int(count)
        return values[int(compact_descr)]

    def remove_item(self, item_type: int, compact_descr: int, count: int = 1) -> int:
        """Take ``count`` items out of the depot; the entry goes away at zero."""
        values = self.items.get(int(item_type), {})
        compact_descr = int(compact_descr)
        remaining = int(values.get(compact_descr, 0)) - int(count)
        if remaining > 0:
            values[compact_descr] = remaining
        else:
            values.pop(compact_descr, None)
        return max(0, remaining)

    def _vehicle_sections(self, entry: "VehicleEntry") -> Dict[str, dict]:
        """Every ``inventory[1]`` section of one vehicle, keyed by its invID.

        The shapes are the ones the client's own ``InventoryRequester`` and
        ``Vehicle`` read: ``crew`` a *list* with one entry per crew slot (a
        ``None`` is an empty seat), ``eqs``/``eqsLayout`` flat lists of the four
        equipment slots, ``shells``/``shellsLayout`` the pair encoded layouts and
        ``lock``/``repair`` two-tuples.
        """
        inv_id = entry.inv_id
        sections = {
            "crew": {inv_id: [int(tman_id) if tman_id else None for tman_id in entry.crew]},
            "settings": {inv_id: entry.settings},
            # ``VehicleEquipment`` builds a fixed-size container out of these
            # lists (``_VehicleConsumables`` asserts the length), so both are
            # padded to the four slots of a regular layout.
            "eqs": {inv_id: _pad_slots(entry.equipment, EQUIPMENT_LAYOUT_SIZE)},
            "eqsLayout": {inv_id: _pad_slots(entry.equipment_layout,
                                             EQUIPMENT_LAYOUT_SIZE)},
            "shells": {inv_id: list(entry.shells)},
            "shellsLayout": {inv_id: dict(entry.shells_layout)},
            "repair": {inv_id: tuple(entry.repair)},
            "lock": {inv_id: tuple(entry.lock)},
        }
        vehicle = self._vehicle_type(entry)
        if vehicle is not None:
            try:
                sections["compDescr"] = {inv_id: entry.in_battle_descr(vehicle)}
            except descr_module.VehicleDescrError:
                sections["compDescr"] = {}
        return sections

    def partial_inventory_diff(self, vehicle_inv_ids=(), tankman_inv_ids=(),
                               item_types=()) -> dict:
        """A diff with only the named vehicles, crew members and item types.

        A vehicle or a crew member that is no longer in the account travels as
        ``None``: ``synchronizeDicts`` removes the key from the client's cache.
        """
        inventory: Dict = {VEHICLE: {}, TANKMAN: {}}
        vehicles = inventory[VEHICLE]
        for inv_id in vehicle_inv_ids:
            inv_id = int(inv_id)
            entry = self.vehicles.get(inv_id)
            if entry is None:
                for name in ("compDescr", "crew", "settings", "eqs", "eqsLayout",
                             "shells", "shellsLayout", "repair", "lock"):
                    vehicles.setdefault(name, {})[inv_id] = None
                continue
            for name, section in self._vehicle_sections(entry).items():
                vehicles.setdefault(name, {}).update(section)
        tankmen = inventory[TANKMAN]
        for inv_id in tankman_inv_ids:
            inv_id = int(inv_id)
            entry = self.tankmen.get(inv_id)
            if entry is None:
                tankmen.setdefault("compDescr", {})[inv_id] = None
                tankmen.setdefault("vehicle", {})[inv_id] = None
                continue
            tankmen.setdefault("compDescr", {})[inv_id] = entry.compact_descr
            tankmen.setdefault("vehicle", {})[inv_id] = entry.vehicle_inv_id
        for item_type in item_types:
            item_type = int(item_type)
            inventory[item_type] = self._item_section(item_type)
        return inventory

    def _item_section(self, item_type: int) -> Dict[int, int]:
        """The depot delta of one item type, ``0`` for what is gone.

        The client's ``InventoryRequester`` merges the sections it receives into
        its cache key by key (``synchronizeDicts``/``update``), so an item that
        is no longer in the depot has to travel as an explicit ``0``: leaving the
        key out would keep the old count in the client and its panels would offer
        a shell the account does not own any more.
        """
        item_type = int(item_type)
        current = dict(self.items.get(item_type) or {})
        published = self._published_items.get(item_type) or {}
        section: Dict[int, int] = {cd: count for cd, count in current.items()
                                   if published.get(cd) != count}
        section.update({cd: 0 for cd in published if cd not in current})
        self._published_items[item_type] = {cd: count for cd, count in current.items() if count}
        return section

    def economics_diff(self) -> dict:
        """The wallet, in the section the client's ``Stats`` reads."""
        return {
            "credits": self.credits,
            "gold": self.gold,
            "freeXP": self.free_xp,
        }

    def purchase_stats_diff(self) -> dict:
        """What changes in ``stats`` when a vehicle is bought or sold.

        The client *grows* ``unlocks``/``eliteVehicles`` from a diff
        (``Stats.synchronize``), so publishing the full sets is idempotent.
        """
        return {
            "slots": self.slots,
            "berths": self.berths,
            "freeXP": self.free_xp,
            "unlocks": set(self.unlocks),
            "eliteVehicles": set(self.elite_vehicles),
            "vehTypeXP": {entry.compact_descr: 0 for entry in self.vehicles.values()},
        }

    def server_settings(self) -> Dict[str, Any]:
        """The ``serverSettings`` the client's lobby reads at startup.

        ``Account.__synchronizeServerSettings`` installs this into
        ``g_accountRepository.serverSettings``, and the lobby indexes several
        keys directly (``wallet`` in ``WalletController.onLobbyStarted``,
        ``file_server`` in ``ClanCache``, ``regional_settings`` in the quest
        helpers), so the local server always provides all of them.
        """
        return {
            "file_server": {
                "clan_emblems": {"url_template": ""},
                "rare_achievements_images": {"url_template": ""},
                "missions_token_image": {"url_template": ""},
            },
            # ``(useGold, useFreeXP)``: the player may pay with gold and convert
            # free XP to crew XP in the garage.
            "wallet": [True, True],
            "regional_settings": {
                "starting_day_of_a_new_week": 3,
                "starting_time_of_a_new_day": 0,
                "starting_time_of_a_new_game_day": 3,
            },
            "roaming": [0, 0, []],
            "isPotapovQuestEnabled": True,
            "isEventEnabled": True,
            "ranked_config": {},
            "hallOfFame": {},
            "clanProfile": {},
            "strongholdSettings": {"wgshHostUrl": ""},
            "spgRedesignFeatures": {},
            "rankedBattles": {},
            "dailyQuest": {},
            "shop": {"isEnabled": False},
            "recruit": {"isEnabled": False},
            "sortie": {"isEnabled": False},
        }

    def set_new_year_state(self, state: int) -> None:
        """Switch the New Year event on/off and push the new state to the client."""
        self.new_year.set_state(state)
        self.events.set_new_year_state(state)
        self.revision += 1

    def apply_new_year_result(self, result: ny_module.NewYearResult) -> dict:
        """Book the side effects of an event action on the account itself.

        Returns what the account *gained* (``vehicles``/``tankmen`` inventory ids
        and the appearance items, as ``(cType, itemID)`` pairs), because the
        client only learns from a diff: the caller publishes it with
        :meth:`granted_account_diff`.
        """
        bonuses = result.bonuses or {}
        before_vehicles = set(self.vehicles)
        before_tankmen = set(self.tankmen)
        if "credits" in bonuses:
            self.credits += int(bonuses["credits"])
        if "gold" in bonuses:
            self.gold += int(bonuses["gold"])
        if "premium" in bonuses:
            # ``add_premium_days`` keeps the expiry and the premium bit in one
            # place, so a box and the premium dialog cannot disagree.
            self.add_premium_days(int(bonuses["premium"]))
        for vehicle_data in bonuses.get("vehicles", ()) or ():
            self._grant_vehicle_bonus(vehicle_data)
        for tankman_data in bonuses.get("tankmen", ()) or ():
            self._grant_tankman_bonus(tankman_data)
        if "tankmen" in bonuses and isinstance(bonuses["tankmen"], list):
            for tankman_data in bonuses["tankmen"]:
                self._attach_tankman_to_vehicle(tankman_data)
        granted_items = []
        for item_data in bonuses.get("customizations", ()) or ():
            item = self._grant_customization_bonus(item_data)
            if item is not None:
                granted_items.append(item)
        # A collection reward is the bonus of *its own* quest - the system message
        # of the box only names the quest ids it completed - so those items come
        # from the quest, not from the box.
        for item_data in self._collection_reward_items(bonuses.get("completedQuestIDs")):
            item = self._grant_customization_bonus(item_data)
            if item is not None:
                granted_items.append(item)
        if result.chest_loot:
            # The client shows a chest's reward from its hidden quest, so the
            # loot of this opening has to reach ``eventsData`` (see
            # ``EventDataBuilder.set_chest_loot``).
            self.events.set_chest_loot(result.chest_loot)
        # ``NY_LEVEL_UP`` is the event telling the server the tree grew: the
        # message goes to the service channel, and the caller publishes it with
        # ``events.notifications_diff()`` (see ``announced`` below).
        announced = ("NY_LEVEL_UP" in result.notifications
                     and self.announce_new_year_level(self.new_year.state.level))
        return {
            "vehicles": sorted(set(self.vehicles) - before_vehicles),
            "tankmen": sorted(set(self.tankmen) - before_tankmen),
            "customizations": granted_items,
            "announced": announced,
        }

    def granted_account_diff(self, granted: dict) -> dict:
        """The ``inventory``/``stats`` sections of what a result just granted.

        A reward the account books itself (a box vehicle with its crew, a
        recruited tankwoman, the camouflage of a collection reward) is invisible
        to the client until its caches are told about it, and they only read
        diffs - so every piece of the reward travels in the answer that granted
        it: the vehicles (with their crew lists, because a result may seat a
        recruited crew member on a tank), the crew members and the depot entries
        of the appearance items.
        """
        inventory: Dict = {}
        tankman_inv_ids = [int(inv_id) for inv_id in (granted.get("tankmen") or ())]
        vehicle_inv_ids = [int(inv_id) for inv_id in (granted.get("vehicles") or ())]
        for inv_id in tankman_inv_ids:
            entry = self.tankmen.get(inv_id)
            if entry is not None and entry.vehicle_inv_id not in (-1, None):
                vehicle_inv_ids.append(int(entry.vehicle_inv_id))
        if vehicle_inv_ids or tankman_inv_ids:
            for key, value in self.partial_inventory_diff(
                    vehicle_inv_ids=vehicle_inv_ids,
                    tankman_inv_ids=tankman_inv_ids).items():
                if value:
                    inventory[key] = value
        item_types = [(int(c_type), int(item_id))
                      for c_type, item_id in (granted.get("customizations") or ())]
        if item_types:
            section = self.customization.sections(item_types=item_types)
            if section:
                inventory[customization_module.CUSTOMIZATION_INVENTORY] = section
        sections: Dict = {}
        if inventory:
            sections["inventory"] = inventory
        if granted.get("vehicles"):
            # A new vehicle is research/XP bookkeeping of the account as well
            # (``Stats.synchronize`` grows ``unlocks``/``eliteVehicles`` and
            # reads ``vehTypeXP``).
            sections["stats"] = self.purchase_stats_diff()
        return sections

    def _collection_reward_items(self, completed_quest_ids) -> List[dict]:
        """The appearance items of the collection rewards among the quest ids.

        A collection reward is the camouflage set of the setting's nation, taken
        from the installation itself (``item_defs/customization`` marks them
        ``vehicleBound`` and keeps them out of the shop) - the live server's own
        reward table is not part of the client, see
        :func:`server.game.events.collection_reward_customizations`.
        """
        rewards = getattr(self.catalog.new_year, "collection_reward_by_id", {})
        items: List[dict] = []
        for quest_id in completed_quest_ids or ():
            reward = rewards.get(str(quest_id))
            if reward is None:
                continue
            items.extend(events_module.collection_reward_customizations(
                self.catalog.customization, reward.setting))
        return items

    def _grant_customization_bonus(self, item_data: dict) -> Optional[Tuple[int, int]]:
        """Put one appearance item of a reward into the depot.

        The payload of a reward is the client's own
        (``gui/server_events/bonuses.py::CustomizationsBonus``): ``custType`` is
        the item type name, ``id`` the item id and ``value`` the count, plus an
        optional ``vehTypeCompDescr`` that binds it to a vehicle.  An item the
        installation does not know is dropped - the client would not be able to
        show it either.
        """
        c_type = c11n_types.TYPE_IDS_BY_NAME.get(str(item_data.get("custType")))
        item_id = int(item_data.get("id", 0) or 0)
        count = int(item_data.get("value", 1) or 1)
        if c_type is None or count <= 0:
            return None
        if self.catalog.customization.get(c11n_types.make_int_descr(c_type, item_id)) is None:
            return None
        vehicle_type = int(item_data.get("vehTypeCompDescr", 0) or 0)
        self.customization.add(c_type, item_id, count, vehicle_type)
        return (c_type, item_id)

    def _grant_vehicle_bonus(self, vehicle_data) -> None:
        """Add the vehicles one ``vehicles`` bonus of the event names.

        The payload is what ``VehiclesBonus.getVehicles`` walks: a dictionary
        ``{typeCompDescr: {'crewLvl': ..., 'noCrew': ...}}`` (the box and chest
        bonuses of ``ClientNewYear`` go out as a *list* of those).  A vehicle the
        account already owns is left alone - the client finds it in its garage by
        the type descriptor anyway.
        """
        if not isinstance(vehicle_data, dict):
            return
        compacts = [key for key in vehicle_data if isinstance(key, int)]
        if not compacts and vehicle_data.get("typeCompDescr"):
            # The quest flavour of the same bonus names the type explicitly.
            compacts = [int(vehicle_data["typeCompDescr"])]
            vehicle_data = {compacts[0]: vehicle_data}
        for compact in compacts:
            vehicle = self.catalog.vehicles.get(int(compact))
            if vehicle is None or vehicle.compact_descr in self.owned_vehicle_comp_descrs():
                continue
            info = vehicle_data.get(compact) or {}
            self.add_vehicle(vehicle, crew=not info.get("noCrew", False))

    def _grant_tankman_bonus(self, tankman_data: dict) -> None:
        if self.tankman_factory is None:
            return
        nation_id = int(tankman_data.get("nationID", 0))
        role = tankman_data.get("role", "commander")
        vehicle_innation_id = int(tankman_data.get("vehicleTypeID", 0))
        passport_data = tankman_data.get("passport")
        try:
            if passport_data:
                passport = tankmen_module.Passport(*[int(part) for part in passport_data])
            else:
                passport = self.tankman_factory.make_female_passport(nation_id)
            compact = self.tankman_factory.make_tankman_descr(
                passport, vehicle_innation_id, role, role_level=int(tankman_data.get("roleLevel", 100))
            )
        except Exception:
            return
        inv_id = self._next_tankman_inv_id()
        self.tankmen[inv_id] = TankmanEntry(
            inv_id=inv_id,
            compact_descr=compact,
            nation_id=nation_id,
            role=role,
            passport=passport,
        )
        tankman_data["invID"] = inv_id

    def _attach_tankman_to_vehicle(self, tankman_data: dict) -> None:
        """Seat a granted crew member in the vehicle its bonus named.

        ``TankmenBonus`` of the client only *shows* the crew member; the account
        has to put it somewhere.  It goes into the first free seat of the
        vehicle type the bonus named (a vehicle of its nation otherwise) whose
        crew slot accepts its role - never next to the crew list, because the
        client indexes the crew by slot and a list longer than the vehicle's
        ``crewRoles`` would make it show a seat that does not exist.  Without a
        free seat it stays in the barracks.
        """
        inv_id = tankman_data.get("invID")
        if inv_id is None:
            return
        entry = self.tankmen.get(int(inv_id))
        if entry is None:
            return
        compact = self._vehicle_type_compact_for(entry, tankman_data.get("vehicleTypeID"))
        candidates = [vehicle for vehicle in self.vehicles.values()
                      if vehicle.compact_descr == compact]
        candidates.extend(vehicle for vehicle in self.vehicles.values()
                          if vehicle.nation_id == entry.nation_id
                          and vehicle not in candidates)
        for vehicle in candidates:
            if self.seat_tankman(entry, vehicle):
                return

    def seat_tankman(self, entry: TankmanEntry, vehicle_entry: VehicleEntry,
                     slots: Sequence[str] = None) -> bool:
        """Seat a crew member if a slot of the vehicle's crew accepts its role."""
        roles = list(slots) if slots is not None else self.crew_roles_of(vehicle_entry)
        if not roles:
            return False
        while len(vehicle_entry.crew) < len(roles):
            vehicle_entry.crew.append(None)
        del vehicle_entry.crew[len(roles):]
        for slot, slot_roles in enumerate(roles):
            if entry.role not in slot_roles:
                continue
            if vehicle_entry.crew[slot] not in (-1, None):
                continue
            vehicle_entry.crew[slot] = entry.inv_id
            entry.vehicle_inv_id = vehicle_entry.inv_id
            return True
        return False

    def _vehicle_type_compact_for(self, tankman: TankmanEntry, vehicle_type_id=None) -> int:
        """The vehicle type a crew member belongs to (the bonus may name it)."""
        if vehicle_type_id is not None:
            for vehicle in self.vehicles.values():
                if (vehicle.nation_id == tankman.nation_id
                        and vehicle.innation_id == int(vehicle_type_id)):
                    return vehicle.compact_descr
        for vehicle in self.vehicles.values():
            if vehicle.nation_id == tankman.nation_id:
                return vehicle.compact_descr
        return -1

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def to_json(self) -> dict:
        from ..proto import py2_loads  # noqa: F401  (symmetry with from_json)

        return {
            "name": self.name,
            "createdAt": self.created_at,
            "credits": self.credits,
            "gold": self.gold,
            "freeXP": self.free_xp,
            "slots": self.slots,
            "berths": self.berths,
            "premiumExpiry": self.premium_expiry,
            "tutorialsCompleted": self.tutorials_completed,
            "vehicles": {
                str(inv_id): {
                    "name": entry.name,
                    "nationID": entry.nation_id,
                    "innationID": entry.innation_id,
                    "level": entry.level,
                    "tags": list(entry.tags),
                    "components": {key: int(value) for key, value in entry.components.items()},
                    "turrets": [list(pair) for pair in entry.turrets],
                    "crew": list(entry.crew),
                    "settings": entry.settings,
                    "equipment": list(entry.equipment),
                    "equipmentLayout": list(entry.equipment_layout),
                    "shells": list(entry.shells),
                    # A layout is keyed by the ``(turret, gun)`` pair, which JSON
                    # cannot use as a key; ``"turret:gun"`` is the encoding
                    # ``parse_layout_key`` reads back.
                    "shellsLayout": {"%d:%d" % key: list(value)
                                     for key, value in entry.shells_layout.items()},
                    "repair": list(entry.repair),
                    "lock": list(entry.lock),
                    "devices": list(entry.devices),
                }
                for inv_id, entry in sorted(self.vehicles.items())
            },
            "tankmen": {
                str(inv_id): {
                    "nationID": entry.nation_id,
                    "role": entry.role,
                    "roleLevel": entry.role_level,
                    "vehicleInvID": entry.vehicle_inv_id,
                    # Compact descriptors are bytes; keep them readable in JSON.
                    "compactDescr": entry.compact_descr.decode("latin-1"),
                }
                for inv_id, entry in sorted(self.tankmen.items())
            },
            "dismissedTankmen": {
                str(inv_id): [record[0].decode("latin-1"), record[1]]
                for inv_id, record in sorted(self.dismissed_tankmen.items())
            },
            "badges": list(self.badges),
            "achievedBadges": {str(badge_id): int(when)
                               for badge_id, when in sorted(self.achieved_badges.items())},
            "dossierFields": {path: int(value)
                              for path, value in sorted(self.dossier_fields.items())},
            "unlocks": sorted(self.unlocks),
            "eliteVehicles": sorted(self.elite_vehicles),
            "vehTypeXP": {str(compact): int(xp) for compact, xp in self.veh_type_xp.items()},
            "items": {
                str(item_type): {str(cd): count for cd, count in values.items()}
                for item_type, values in self.items.items()
            },
            "customization": self.customization.to_json(),
            "newYear": self.new_year.state.to_json(),
        }

    @classmethod
    def from_json(cls, payload: dict, catalog: Catalog, tankman_factory=None, rng=None) -> "AccountState":
        state = cls(
            catalog,
            name=payload.get("name", "local_account"),
            rng=rng,
            tankman_factory=tankman_factory,
            start_time=payload.get("createdAt"),
            credits=int(payload.get("credits", DEFAULT_CREDITS)),
            gold=int(payload.get("gold", DEFAULT_GOLD)),
            free_xp=int(payload.get("freeXP", DEFAULT_FREE_XP)),
        )
        state.slots = int(payload.get("slots", 20))
        state.berths = int(payload.get("berths", 20))
        state.premium_expiry = int(payload.get("premiumExpiry", 0))
        state.tutorials_completed = int(payload.get("tutorialsCompleted", 0))
        for inv_id, data in (payload.get("vehicles") or {}).items():
            entry = VehicleEntry(
                inv_id=int(inv_id),
                name=data["name"],
                nation_id=int(data["nationID"]),
                innation_id=int(data["innationID"]),
                compact_descr=items_module.make_vehicle_compact_descr(
                    int(data["nationID"]), int(data["innationID"])
                ),
                level=int(data.get("level", 1)),
                tags=tuple(data.get("tags", ())),
                components={key: int(value) for key, value in data.get("components", {}).items()},
                turrets=[tuple(pair) for pair in data.get("turrets", ())],
                crew=[None if tman is None else int(tman)
                      for tman in data.get("crew", ())],
                settings=int(data.get("settings", 0)),
                equipment=[int(value) for value in data.get("equipment", (0, 0, 0, 0))],
                equipment_layout=_read_equipment_layout(data.get("equipmentLayout")),
                shells=[int(value) for value in data.get("shells", ())],
                shells_layout={
                    catalog_module.parse_layout_key(key): [int(v) for v in value]
                    for key, value in (data.get("shellsLayout") or {}).items()
                },
                repair=tuple(data.get("repair", (0, 0))),
                lock=tuple(data.get("lock", (0, 0))),
                devices=[None if value is None else int(value)
                         for value in data.get("devices", (None, None, None))],
            )
            state.vehicles[entry.inv_id] = entry
        for inv_id, data in (payload.get("tankmen") or {}).items():
            entry = TankmanEntry(
                inv_id=int(inv_id),
                compact_descr=str(data["compactDescr"]).encode("latin-1"),
                nation_id=int(data.get("nationID", 0)),
                role=data.get("role", "commander"),
                role_level=int(data.get("roleLevel", 100)),
                vehicle_inv_id=int(data.get("vehicleInvID", -1)),
            )
            state.tankmen[entry.inv_id] = entry
        state.dismissed_tankmen = {
            int(inv_id): (record[0].encode("latin-1"), float(record[1]))
            for inv_id, record in (payload.get("dismissedTankmen") or {}).items()
        }
        state.badges = [int(badge) for badge in payload.get("badges") or ()]
        state.achieved_badges = {int(badge_id): int(when)
                                 for badge_id, when in (payload.get("achievedBadges") or {}).items()}
        state.dossier_fields = {str(path): int(value)
                                for path, value in (payload.get("dossierFields") or {}).items()}
        state.unlocks = set(payload.get("unlocks") or ())
        state.elite_vehicles = set(payload.get("eliteVehicles") or ())
        state.veh_type_xp = {int(compact): int(xp)
                             for compact, xp in (payload.get("vehTypeXP") or {}).items()}
        state.items = {
            int(item_type): {int(cd): int(count) for cd, count in values.items()}
            for item_type, values in (payload.get("items") or {}).items()
        }
        state._published_items = {item_type: dict(values)
                                  for item_type, values in state.items.items() if values}
        state.customization = customization_module.CustomizationInventory.from_json(
            payload.get("customization") or {})
        state.new_year.state = ny_module.NewYearState.from_json(payload.get("newYear") or {}, catalog.new_year)
        state.new_year.tankmen = tankman_factory
        state.sync_tokens()
        return state


__all__ = [
    "AccountState",
    "DATABASE_ID",
    "DEFAULT_CREDITS",
    "DEFAULT_FREE_XP",
    "DEFAULT_GOLD",
    "SKIP_TAGS",
    "TANKMAN",
    "TankmanEntry",
    "VEHICLE",
    "VehicleEntry",
]
