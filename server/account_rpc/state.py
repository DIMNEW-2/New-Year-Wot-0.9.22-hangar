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
                        and tankmen, ``{intCD: count}`` for the rest
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
from ..game.catalog import Catalog, VehicleType
from ..game.events import EventDataBuilder

#: ``GUI_ITEM_TYPE`` indexes of the client (``gui.shared.gui_items``).
VEHICLE = items_module.GUI_ITEM_TYPE.VEHICLE
TANKMAN = items_module.GUI_ITEM_TYPE.TANKMAN

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
    equipment: List[int] = field(default_factory=lambda: [0, 0, 0])
    equipment_layout: Dict[int, List[int]] = field(default_factory=dict)
    shells: List[int] = field(default_factory=list)
    shells_layout: Dict[int, List[int]] = field(default_factory=dict)
    repair: Tuple[int, int] = (0, 0)
    lock: int = 0
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
            self.descriptor = descr_module.make_compact_descr(vehicle, configuration)
        return self.descriptor


@dataclass
class TankmanEntry:
    """One crew member of the barracks."""

    inv_id: int
    compact_descr: bytes
    nation_id: int
    vehicle_inv_id: int = -1
    role: str = "commander"
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
        self.items: Dict[int, Dict[int, int]] = {}
        self.tokens: Dict[str, Tuple[Optional[int], int]] = {}
        self.unlocks: set = set()
        self.elite_vehicles: set = set()
        self.int_user_settings: Dict[str, dict] = {}

        self.tankman_factory = tankman_factory
        self.events = EventDataBuilder(catalog, start_time=self.created_at)
        #: The vehicles the New Year boxes and chests hand out (built from the
        #: client's own vehicle catalogue, see :mod:`server.game.rewards`).
        self.vehicle_chooser = rewards_module.VehicleRewardChooser(catalog)
        self.new_year = ny_module.NewYearEvent(
            catalog.new_year,
            rng=self.rng,
            tankmen=tankman_factory,
            vehicles=self.vehicle_chooser,
            owned_vehicles=self.owned_vehicle_comp_descrs,
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
        **kwargs,
    ) -> "AccountState":
        """A ready-to-play account: a garage, a crew and the event switched on."""
        state = cls(catalog, **kwargs)
        if tankmen_configs:
            state.tankman_factory = tankmen_module.TankmanFactory(tankmen_configs, state.rng)
            state.new_year.tankmen = state.tankman_factory
        state.grant_garage(mode=garage_mode)
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

    def add_vehicle(self, vehicle: VehicleType, crew: bool = True) -> VehicleEntry:
        """Put one vehicle (with a stock crew) into the garage."""
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
        if crew and self.tankman_factory is not None:
            entry.crew = self._create_crew(entry)
        self.vehicles[inv_id] = entry
        self.unlocks.add(vehicle.compact_descr)
        self.elite_vehicles.add(vehicle.compact_descr)
        return entry

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
            if tankman is not None:
                crew.append(tankman.inv_id)
        return crew

    def crew_roles_of(self, entry: VehicleEntry) -> List[str]:
        """The role of every crew slot of a vehicle, in slot order."""
        vehicle = self._vehicle_type(entry)
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
                passport, vehicle_innation_id, role, role_level=100
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
        vehicles["crew"] = {entry.inv_id: {tman_id: None for tman_id in entry.crew}
                            for entry in self.vehicles.values()}
        vehicles["settings"] = {entry.inv_id: entry.settings for entry in self.vehicles.values()}
        vehicles["eqs"] = {entry.inv_id: list(entry.equipment) for entry in self.vehicles.values()}
        vehicles["eqsLayout"] = {entry.inv_id: dict(entry.equipment_layout) for entry in self.vehicles.values()}
        vehicles["shells"] = {entry.inv_id: list(entry.shells) for entry in self.vehicles.values()}
        vehicles["shellsLayout"] = {entry.inv_id: dict(entry.shells_layout) for entry in self.vehicles.values()}
        vehicles["repair"] = {entry.inv_id: tuple(entry.repair) for entry in self.vehicles.values()}
        vehicles["lock"] = {entry.inv_id: entry.lock for entry in self.vehicles.values()}

        inventory[TANKMAN].update(self.tankman_inventory_diff())

        for item_type, values in self.items.items():
            inventory[item_type] = dict(values)
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

    def _stats_diff(self) -> dict:
        return {
            "slots": self.slots,
            "berths": self.berths,
            "freeXP": self.free_xp,
            "unlocks": set(self.unlocks),
            "eliteVehicles": set(self.elite_vehicles),
            "vehTypeXP": {entry.compact_descr: 0 for entry in self.vehicles.values()},
            "tutorialsCompleted": self.tutorials_completed,
            "vehicleSellsLeft": 0,
            "freeTMenLeft": 0,
            "freeVehiclesLeft": 0,
        }

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
        self.events.set_new_year_state(self.new_year.state.state)
        return {
            "rev": self.revision,
            "inventory": self._inventory_diff(),
            "stats": self._stats_diff(),
            "account": {
                "attrs": 0,
                "premiumExpiryTime": self.premium_expiry,
                "globalRating": 0,
            },
            "economics": {
                "credits": self.credits,
                "gold": self.gold,
                "freeXP": self.free_xp,
            },
            "tokens": dict(self.tokens),
            "newYear": self.new_year.full_new_year_diff(),
            ("goodies", "_r"): self.new_year.goodies_diff(),
            ("serverSettings", "_r"): self.server_settings(),
            ("eventsData", "_r"): self.events.events_data(),
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
            self._shop_builder = shop_module.ShopBuilder(self.catalog)
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
        """Every ``inventory[1]`` section of one vehicle, keyed by its invID."""
        inv_id = entry.inv_id
        sections = {
            "crew": {inv_id: {tman_id: None for tman_id in entry.crew}},
            "settings": {inv_id: entry.settings},
            "eqs": {inv_id: list(entry.equipment)},
            "eqsLayout": {inv_id: dict(entry.equipment_layout)},
            "shells": {inv_id: list(entry.shells)},
            "shellsLayout": {inv_id: dict(entry.shells_layout)},
            "repair": {inv_id: tuple(entry.repair)},
            "lock": {inv_id: entry.lock},
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
            inventory[item_type] = dict(self.items.get(item_type, {}))
        return inventory

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

    def apply_new_year_result(self, result: ny_module.NewYearResult) -> None:
        """Book the side effects of an event action on the account itself."""
        bonuses = result.bonuses or {}
        if "credits" in bonuses:
            self.credits += int(bonuses["credits"])
        if "gold" in bonuses:
            self.gold += int(bonuses["gold"])
        if "premium" in bonuses:
            days = int(bonuses["premium"])
            if days:
                base = max(self.premium_expiry, int(time.time()))
                self.premium_expiry = base + days * 86400
                self.is_premium = True
        for vehicle_data in bonuses.get("vehicles", ()) or ():
            self._grant_vehicle_bonus(vehicle_data)
        for tankman_data in bonuses.get("tankmen", ()) or ():
            self._grant_tankman_bonus(tankman_data)
        if "tankmen" in bonuses and isinstance(bonuses["tankmen"], list):
            for tankman_data in bonuses["tankmen"]:
                self._attach_tankman_to_vehicle(tankman_data)
        if result.chest_loot:
            # The client shows a chest's reward from its hidden quest, so the
            # loot of this opening has to reach ``eventsData`` (see
            # ``EventDataBuilder.set_chest_loot``).
            self.events.set_chest_loot(result.chest_loot)

    def _grant_vehicle_bonus(self, vehicle_data: dict) -> None:
        """Add a vehicle the event granted.

        The sys message carries the vehicle as ``{'typeCompDescr': cd, ...}``
        (see ``ClientNewYear.__extractBonuses`` -> ``VehiclesBonus``), so the
        server only has to find the type in the catalogue.
        """
        compact = vehicle_data.get("typeCompDescr")
        vehicle = self.catalog.vehicles.get(int(compact)) if compact else None
        if vehicle is None:
            return
        self.add_vehicle(vehicle)

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
        inv_id = tankman_data.get("invID")
        if inv_id is None:
            return
        entry = self.tankmen.get(int(inv_id))
        if entry is None:
            return
        for vehicle in self.vehicles.values():
            if vehicle.compact_descr == self._vehicle_type_compact_for(entry):
                vehicle.crew.append(entry.inv_id)
                entry.vehicle_inv_id = vehicle.inv_id
                return

    def _vehicle_type_compact_for(self, tankman: TankmanEntry) -> int:
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
                    "equipmentLayout": {str(k): list(v) for k, v in entry.equipment_layout.items()},
                    "shells": list(entry.shells),
                    "shellsLayout": {str(k): list(v) for k, v in entry.shells_layout.items()},
                    "repair": list(entry.repair),
                    "lock": entry.lock,
                }
                for inv_id, entry in sorted(self.vehicles.items())
            },
            "tankmen": {
                str(inv_id): {
                    "nationID": entry.nation_id,
                    "role": entry.role,
                    "vehicleInvID": entry.vehicle_inv_id,
                    # Compact descriptors are bytes; keep them readable in JSON.
                    "compactDescr": entry.compact_descr.decode("latin-1"),
                }
                for inv_id, entry in sorted(self.tankmen.items())
            },
            "unlocks": sorted(self.unlocks),
            "eliteVehicles": sorted(self.elite_vehicles),
            "items": {
                str(item_type): {str(cd): count for cd, count in values.items()}
                for item_type, values in self.items.items()
            },
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
                crew=[int(tman) for tman in data.get("crew", ())],
                settings=int(data.get("settings", 0)),
                equipment=[int(value) for value in data.get("equipment", (0, 0, 0))],
                equipment_layout={
                    int(key): [int(v) for v in value] for key, value in data.get("equipmentLayout", {}).items()
                },
                shells=[int(value) for value in data.get("shells", ())],
                shells_layout={
                    int(key): [int(v) for v in value] for key, value in data.get("shellsLayout", {}).items()
                },
                repair=tuple(data.get("repair", (0, 0))),
                lock=int(data.get("lock", 0)),
            )
            state.vehicles[entry.inv_id] = entry
        for inv_id, data in (payload.get("tankmen") or {}).items():
            entry = TankmanEntry(
                inv_id=int(inv_id),
                compact_descr=str(data["compactDescr"]).encode("latin-1"),
                nation_id=int(data.get("nationID", 0)),
                role=data.get("role", "commander"),
                vehicle_inv_id=int(data.get("vehicleInvID", -1)),
            )
            state.tankmen[entry.inv_id] = entry
        state.unlocks = set(payload.get("unlocks") or ())
        state.elite_vehicles = set(payload.get("eliteVehicles") or ())
        state.items = {
            int(item_type): {int(cd): int(count) for cd, count in values.items()}
            for item_type, values in (payload.get("items") or {}).items()
        }
        state.new_year.state = ny_module.NewYearState.from_json(payload.get("newYear") or {}, catalog.new_year)
        state.new_year.tankmen = tankman_factory
        state.sync_tokens()
        return state


__all__ = [
    "AccountState",
    "DEFAULT_CREDITS",
    "DEFAULT_FREE_XP",
    "DEFAULT_GOLD",
    "SKIP_TAGS",
    "TANKMAN",
    "TankmanEntry",
    "VEHICLE",
    "VehicleEntry",
]
