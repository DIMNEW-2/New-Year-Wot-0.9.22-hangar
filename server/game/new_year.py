"""Server-side New Year 2018 event ("Новогоднее наступление") of 0.9.22.

The client owns no event logic at all: every number it paints comes from the
account diff and every action it offers is one of the seven New Year requests.
This module is the authoritative side of that contract:

* state - what the account has collected (toys, placed slots, fragments, boxes,
  chests, variadic discounts and tankmen, collection ratings),
* rules - which toys a box or the crafting workshop hands out, when a chest or
  a discount certificate is earned,
* diffs - the ``newYear``/``tokens``/``goodies`` sections of the account diff
  that make the client render the new state.

The tables the client also knows (craft costs, probabilities, atmosphere and
collection bounds) live in :mod:`server.game.ny_constants` and
:mod:`server.game.ny_rules` and are used unchanged here, so both sides always
agree on prices and progress.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import items as items_module
from . import goodies as goodies_module
from . import ny_constants as nyc
from . import rewards as rewards_module
from .rewards import REWARD_VEHICLE_LEVEL
from . import ny_rules
from .catalog import NyCatalog

#: A toy the player has more than this many copies of can be broken for
#: fragments; duplicates from boxes auto-convert, exactly like in the event.
DUPLICATE_FRAGMENT_RATIO = 1.0

#: Loot table of ``ny18:box:*`` - the New Year loot boxes.
#:
#: Retail ships these weights on the server side only, so the local server owns
#: them.  ``toys`` is the (min, max) number of decorations a box contains,
#: ``extra_toy_chance`` the chance of one more, and ``rare_chance``/``reward``
#: describe what the box drops besides decorations (vehicles for the "big"
#: boxes, credits and gold for the small ones).
BOX_LOOT = {
    "toys": (2, 3),
    "extra_toy_chance": 0.25,
    "fragments_bonus": 0.15,
    "credits": (15000, 45000),
    "gold": (0, 0),
    "premium": 0,
    #: Chance of a premium vehicle, and the tier it comes from (the event's
    #: boxes handed out tier 8 premiums).
    "vehicle_chance": 0.0,
    "vehicle_level": 8,
    "tankman_chance": 0.0,
}

#: Present boxes of the event contain a tankwoman recruit choice and premium
#: days (they were the "gift" boxes a player could send to a friend).
PRESENT_BOX_BONUS = {
    "soviet": {"premium": 3, "tankman_token": True},
    "traditionalWestern": {"premium": 3, "tankman_token": True},
    "modernWestern": {"premium": 3, "tankman_token": True},
    "asian": {"premium": 3, "tankman_token": True},
}

#: What ``ny18:chest:<level>`` contains.  A chest always carries decorations
#: (ranked by the chest level), money and - on the levels the client's reward
#: screen expects it - a premium vehicle, a tankwoman recruit token and the
#: vehicle discount certificate.  ``vehicle_setting`` overrides the national
#: setting of the vehicle, ``None`` meaning "any nation".
CHEST_LOOT = {
    1: {"toy_rank": 5, "toys": 2, "credits": 100000, "gold": 250, "vehicle": True,
        "vehicle_setting": "soviet", "discount_token": True},
    2: {"toy_rank": 4, "toys": 2, "credits": 75000, "gold": 150, "discount_token": True},
    3: {"toy_rank": 4, "toys": 2, "credits": 60000, "gold": 100, "discount_token": True},
    4: {"toy_rank": 3, "toys": 2, "credits": 45000, "gold": 75, "tankman_token": True,
        "discount_token": True},
    5: {"toy_rank": 3, "toys": 2, "credits": 40000, "gold": 50, "vehicle": True,
        "vehicle_setting": "traditionalWestern", "vehicle_level": 6},
    6: {"toy_rank": 2, "toys": 2, "credits": 35000, "gold": 50, "tankman_token": True,
        "discount_token": True},
    7: {"toy_rank": 2, "toys": 2, "credits": 30000, "gold": 25, "premium": 3,
        "discount_token": True},
    8: {"toy_rank": 1, "toys": 2, "credits": 25000, "gold": 25, "tankman_token": True,
        "vehicle": True, "vehicle_setting": "modernWestern", "vehicle_level": 7,
        "discount_token": True},
    9: {"toy_rank": 1, "toys": 3, "credits": 20000, "gold": 25, "premium": 7,
        "discount_token": True},
    10: {"toy_rank": 1, "toys": 3, "credits": 15000, "gold": 25, "tankman_token": True,
         "vehicle": True, "vehicle_setting": "asian", "vehicle_level": 8,
         "discount_token": True},
}

#: Every atmosphere level grants the chest of that level and, from level 2 on,
#: the vehicle discount certificate of the same level.  Levels 4/6/8/10 also
#: grant a tankwoman recruit token, which is why the client only ships
#: ``ny18:vt:4``, ``:6``, ``:8`` and ``:10`` (see ``variadic_tankmen.xml``).
TANKMAN_LEVELS = (4, 6, 8, 10)
DISCOUNT_FIRST_LEVEL = 2


class NewYearError(Exception):
    """Raised when the client asks for something the event cannot do."""


@dataclass
class NewYearResult:
    """Everything one event action produced.

    ``diff``   the ``newYear``/``tokens`` sections of the account diff,
    ``bonuses`` the payload of the ``tokenQuests`` system message the client
               turns into the reward window of a box,
    ``chestLoot`` the loot of an opened chest, keyed by chest id.  A chest's
               reward is *not* taken from the system message: the client reads
               the ``bonus`` of the hidden quest with the chest's id
               (``_NewYearChestStorage._handleRequestResult``), so the server
               has to refresh that quest through ``eventsData`` - see
               :meth:`server.game.events.EventDataBuilder.set_chest_loot`.
    """

    diff: Dict[str, dict] = field(default_factory=dict)
    bonuses: Optional[dict] = None
    chest_loot: Dict[str, dict] = field(default_factory=dict)
    notifications: List[str] = field(default_factory=list)

    def merge(self, other: "NewYearResult") -> "NewYearResult":
        self.diff = _merge_diff(self.diff, other.diff)
        if other.bonuses:
            self.bonuses = other.bonuses
        self.chest_loot.update(other.chest_loot)
        self.notifications.extend(other.notifications)
        return self


def _merge_diff(target: Dict[str, dict], source: Dict[str, dict]) -> Dict[str, dict]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merged = dict(target[key])
            merged.update(value)
            target[key] = merged
        else:
            target[key] = value
    return target


class NewYearState:
    """The persisted part of the event (JSON friendly for the save file)."""

    def __init__(self, catalog: NyCatalog, state: int = nyc.NY_STATE.IN_PROGRESS):
        self.catalog = catalog
        self.state = state
        self.level = 1
        self.toy_fragments = 0
        self.slots: List[int] = [nyc.INVALID_TOY_ID] * len(catalog.slots)
        self.inventory_toys: Dict[int, List[int]] = {}
        self.toy_collection: set = set()
        self.boxes: Dict[str, int] = {}
        self.chests: Dict[str, int] = {}
        self.variadic_discounts: Dict[str, int] = {}
        self.variadic_tankmen: Dict[str, int] = {}
        self.tankman_next_choices: Dict[int, Tuple[int, int, int, int]] = {}
        self.collection_ratings: List[int] = [0] * len(nyc.NATIONAL_SETTINGS)
        self.collection_rewards: set = set()
        self.goodies: Dict[int, Tuple[int, int, int]] = {}

    # -- persistence ----------------------------------------------------
    def to_json(self) -> dict:
        return {
            "state": self.state,
            "level": self.level,
            "toyFragments": self.toy_fragments,
            "slots": list(self.slots),
            "inventoryToys": {toy_id: list(counts) for toy_id, counts in self.inventory_toys.items()},
            "toyCollection": sorted(self.toy_collection),
            "boxes": dict(self.boxes),
            "chests": dict(self.chests),
            "variadicDiscounts": dict(self.variadic_discounts),
            "variadicTankmen": dict(self.variadic_tankmen),
            "tankmanNextChoices": {
                nation_id: list(choice) for nation_id, choice in self.tankman_next_choices.items()
            },
            "collectionRatings": list(self.collection_ratings),
            "collectionRewards": sorted(self.collection_rewards),
            "goodies": {goodie_id: list(value) for goodie_id, value in self.goodies.items()},
        }

    @classmethod
    def from_json(cls, payload: dict, catalog: NyCatalog) -> "NewYearState":
        state = cls(catalog, int(payload.get("state", nyc.NY_STATE.IN_PROGRESS)))
        state.level = int(payload.get("level", 1))
        state.toy_fragments = int(payload.get("toyFragments", 0))
        slots = payload.get("slots") or [nyc.INVALID_TOY_ID] * len(catalog.slots)
        state.slots = [int(toy_id) for toy_id in slots]
        if len(state.slots) != len(catalog.slots):
            state.slots = (state.slots + [nyc.INVALID_TOY_ID] * len(catalog.slots))[: len(catalog.slots)]
        state.inventory_toys = {
            int(toy_id): [int(counts[0]), int(counts[1]) if len(counts) > 1 else 0]
            for toy_id, counts in (payload.get("inventoryToys") or {}).items()
        }
        state.toy_collection = {int(toy_id) for toy_id in payload.get("toyCollection") or ()}
        for key in ("boxes", "chests", "variadicDiscounts", "variadicTankmen"):
            value = payload.get(key) or {}
            setattr(
                state,
                {"boxes": "boxes", "chests": "chests", "variadicDiscounts": "variadic_discounts", "variadicTankmen": "variadic_tankmen"}[key],
                {str(k): int(v) for k, v in value.items()},
            )
        state.tankman_next_choices = {
            int(nation_id): tuple(int(part) for part in choice)
            for nation_id, choice in (payload.get("tankmanNextChoices") or {}).items()
        }
        ratings = payload.get("collectionRatings") or []
        state.collection_ratings = [int(value) for value in ratings] + [0] * (
            len(nyc.NATIONAL_SETTINGS) - len(ratings)
        )
        state.collection_rewards = set(payload.get("collectionRewards") or ())
        state.goodies = {
            int(goodie_id): tuple(int(part) for part in value)
            for goodie_id, value in (payload.get("goodies") or {}).items()
        }
        return state


class NewYearEvent:
    """Applies the New Year requests to a :class:`NewYearState`."""

    def __init__(
        self,
        catalog: NyCatalog,
        state: NewYearState = None,
        rng: random.Random = None,
        tankmen=None,
        goodies=None,
        vehicles=None,
        owned_vehicles=None,
    ):
        self.catalog = catalog
        self.state = state or NewYearState(catalog)
        self.rng = rng or random.Random()
        self.tankmen = tankmen
        #: ``{goodieID: GoodieDefinition}`` of ``server_xml/goodies.xml``.
        self.goodies = goodies or {}
        #: :class:`server.game.rewards.VehicleRewardChooser` for the box/chest
        #: vehicles; without one the event hands out no vehicles.
        self.vehicles = vehicles
        #: ``callable() -> set of vehicle compact descriptors`` the account owns,
        #: so a reward never duplicates a tank of the garage.
        self.owned_vehicles = owned_vehicles

    # -- helpers --------------------------------------------------------
    @property
    def toy_descrs(self) -> Dict[int, dict]:
        """``{toyID: {'atmosphere': n}}`` for :mod:`server.game.ny_rules`."""
        return {toy.id: {"atmosphere": toy.atmosphere} for toy in self.catalog.toys.values()}

    def atmosphere(self) -> int:
        return ny_rules.get_total_atmosphere(self.toy_descrs, self.state.slots)

    def atmosphere_level(self) -> int:
        return ny_rules.get_atmosphere_level(self.atmosphere())

    def collection_level(self, setting_id: int) -> int:
        return ny_rules.get_collection_level(self.state.collection_ratings[setting_id])

    def placed_toys(self) -> List[int]:
        return [toy_id for toy_id in self.state.slots if toy_id != nyc.INVALID_TOY_ID]

    def total_toys(self) -> int:
        placed = set(self.placed_toys())
        return len(placed) + len(self.state.toy_collection)

    def available_toys(self) -> List[int]:
        """Toy ids the player owns (placed toys count as owned)."""
        owned = set(self.state.toy_collection)
        owned.update(self.placed_toys())
        return sorted(owned)

    def collectible(self, toy_id: int) -> bool:
        """``True`` when the toy is not in the collection yet (so it is new)."""
        return toy_id not in self.state.toy_collection

    # -- diff building --------------------------------------------------
    def full_new_year_diff(self) -> dict:
        """The ``newYear`` section of a full sync."""
        counts = {
            toy_id: [counts[0], counts[1]] for toy_id, counts in sorted(self.state.inventory_toys.items())
        }
        return {
            "toyCollection": ny_rules.pack_toy_collection(self.state.toy_collection),
            "slots": list(self.state.slots),
            "inventoryToys": counts,
            "level": self.state.level,
            "toyFragments": self.state.toy_fragments,
            "tankmanNextChoices": {
                nation_id: list(choice) for nation_id, choice in sorted(self.state.tankman_next_choices.items())
            },
        }

    def token_diff(self, changed: Iterable[str] = None) -> dict:
        """The ``tokens`` section of a diff (``{token: (expiryTime, count)}``)."""
        tokens = {}
        for tokens_dict in (
            self.state.boxes,
            self.state.chests,
            self.state.variadic_discounts,
            self.state.variadic_tankmen,
        ):
            for token, count in tokens_dict.items():
                tokens[token] = (None, int(count))
        if changed is not None:
            allowed = set(changed)
            tokens = {token: value for token, value in tokens.items() if token in allowed}
        return tokens

    def goodies_diff(self) -> dict:
        """``{goodieID: (state, finishTime, count)}`` as ``GoodiesRequester`` reads it."""
        return {
            "goodies": {
                goodie_id: list(value) for goodie_id, value in sorted(self.state.goodies.items())
            }
        }

    # -- accounting -----------------------------------------------------
    def _add_toys(self, toy_ids: Sequence[int]) -> Tuple[List[int], List[int], int]:
        """Store toys, returning ``(all_toys, new_toys, fragments)``.

        A duplicate of an already collected toy is broken into fragments on the
        spot, exactly like the event does when a box rolls a decoration the
        player already has; the first copy of a toy raises the collection
        rating of its national setting.
        """
        fragments = 0
        new_toys = []
        stored = []
        for toy_id in toy_ids:
            toy = self.catalog.toys.get(toy_id)
            if toy is None:
                continue
            stored.append(toy_id)
            count = self.state.inventory_toys.setdefault(toy_id, [0, 0])
            count[0] += 1
            count[1] += 1
            if self.collectible(toy_id):
                self.state.toy_collection.add(toy_id)
                self.state.collection_ratings[toy.setting_id] += toy.rating
                new_toys.append(toy_id)
            else:
                fragments += int(toy.fragments * DUPLICATE_FRAGMENT_RATIO)
        if fragments:
            self.state.toy_fragments += fragments
        return stored, new_toys, fragments

    def _grant_collection_rewards(self) -> List[str]:
        """Hand out a setting's collection reward when its level is maxed.

        The reward itself is the installation's own set of event camouflages of
        that setting (see :func:`server.game.events.collection_reward_customizations`);
        it travels as the ``customizations`` bonus of the answer, which is what
        the client's sys message and its awards window read.
        """
        granted = []
        for reward in self.catalog.collection_rewards:
            if reward.id in self.state.collection_rewards:
                continue
            if self.collection_level(reward.setting_id) >= nyc.MAX_COLLECTION_LEVEL:
                self.state.collection_rewards.add(reward.id)
                granted.append(reward.id)
        return granted


    def _set_level(self, level: int) -> Optional[NewYearResult]:
        """Grant everything an atmosphere level unlocks (chest, discount, crew)."""
        if level <= self.state.level:
            return None
        self.state.level = level
        result = NewYearResult(diff={"newYear": {"level": level}})
        chest_id = self.catalog.chest_id_by_level.get(level)
        if chest_id:
            self.state.chests[chest_id] = self.state.chests.get(chest_id, 0) + 1
            result.diff.setdefault("tokens", {})[chest_id] = (None, self.state.chests[chest_id])
            result.notifications.append("NY_LEVEL_UP")
        discount_token = "ny18:vd:%d" % level
        if level >= DISCOUNT_FIRST_LEVEL and discount_token in self.catalog.variadic_discounts:
            self.state.variadic_discounts[discount_token] = self.state.variadic_discounts.get(discount_token, 0) + 1
            result.diff.setdefault("tokens", {})[discount_token] = (
                None,
                self.state.variadic_discounts[discount_token],
            )
        tankman_token = "ny18:vt:%d" % level
        if level in TANKMAN_LEVELS and tankman_token in self.catalog.variadic_tankmen:
            self.state.variadic_tankmen[tankman_token] = self.state.variadic_tankmen.get(tankman_token, 0) + 1
            result.diff.setdefault("tokens", {})[tankman_token] = (
                None,
                self.state.variadic_tankmen[tankman_token],
            )
            self._refresh_tankman_choices()
            result.diff["newYear"]["tankmanNextChoices"] = {
                nation_id: list(choice)
                for nation_id, choice in sorted(self.state.tankman_next_choices.items())
            }
        return result

    def _sync_level(self) -> Optional[NewYearResult]:
        return self._set_level(self.atmosphere_level())

    def _refresh_tankman_choices(self) -> None:
        """Publish the next tankwoman choice for every nation that has a token.

        The client renders the pending choice straight from
        ``tankmanNextChoices`` (``_TankmanDiscountsStorage.getTankmanNextChoices``),
        so a nation keeps its slot until its token is spent.
        """
        if self.tankmen is None:
            return
        for nation_id in range(len(nyc.NATIONS)):
            if not self._has_tankman_token():
                self.state.tankman_next_choices.pop(nation_id, None)
                continue
            if nation_id in self.state.tankman_next_choices:
                continue
            try:
                passport = self.tankmen.make_female_passport(nation_id)
            except Exception:
                continue
            self.state.tankman_next_choices[nation_id] = passport.as_choice()

    def _has_tankman_token(self) -> bool:
        """``True`` while the account owns an unspent tankwoman recruit token."""
        return any(count > 0 for count in self.state.variadic_tankmen.values())

    # -- requests -------------------------------------------------------
    def open_box(self, box_id: str) -> NewYearResult:
        """``openBox(id)`` - one New Year loot box."""
        box = self.catalog.box(box_id)
        if box is None:
            raise NewYearError("unknown box %r" % (box_id,))
        if self.state.boxes.get(box_id, 0) <= 0:
            raise NewYearError("no %s in the account" % (box_id,))
        self.state.boxes[box_id] -= 1

        loot = dict(BOX_LOOT)
        if box.present and box.setting in PRESENT_BOX_BONUS:
            loot.update(PRESENT_BOX_BONUS[box.setting])

        setting = box.setting
        if setting is None:
            setting = self.rng.choice(list(nyc.NATIONAL_SETTINGS))
        toys = self._roll_box_toys(setting, loot)

        stored, new_toys, fragments = self._add_toys(toys)
        bonuses = {
            "completedQuestIDs": [box_id],
            "ny18Toys": stored,
        }
        if fragments:
            bonuses["toyFragments"] = fragments
        credits = self.rng.randint(*loot["credits"]) if loot.get("credits") else 0
        if credits:
            bonuses["credits"] = credits
        gold = self.rng.randint(*loot["gold"]) if loot.get("gold") else 0
        if gold:
            bonuses["gold"] = gold
        premium = loot.get("premium", 0)
        if premium and self.rng.random() < 0.5:
            bonuses["premium"] = premium
            premium = 0
        if loot.get("vehicle_chance") and self.rng.random() < loot["vehicle_chance"]:
            vehicle = self._roll_reward_vehicle(setting, level=loot.get("vehicle_level"))
            if vehicle is not None:
                bonuses.setdefault("vehicles", []).append(self._vehicle_bonus(vehicle))
        if loot.get("tankman_chance") and self.rng.random() < loot["tankman_chance"]:
            tankman = self._roll_reward_tankman(setting)
            if tankman is not None:
                bonuses.setdefault("tankmen", []).append(tankman)
        result = NewYearResult(
            diff={
                "tokens": {box_id: (None, self.state.boxes[box_id])},
                "newYear": {
                    "inventoryToys": {
                        toy_id: list(self.state.inventory_toys[toy_id]) for toy_id in stored
                    },
                    "toyFragments": self.state.toy_fragments,
                    "toyCollection": ny_rules.pack_toy_collection(self.state.toy_collection),
                },
            },
            bonuses=bonuses,
        )
        self._add_box_tokens(loot, result)
        rewards = self._grant_collection_rewards()
        if rewards:
            bonuses["completedQuestIDs"].extend(rewards)
            result.diff.setdefault("newYear", {})["collectionRewards"] = sorted(self.state.collection_rewards)
        level_up = self._sync_level()
        if level_up is not None:
            result.merge(level_up)
        return result

    def _add_box_tokens(self, loot: dict, result: "NewYearResult") -> None:
        """Give the recruit/discount tokens a box carries.

        The client learns about them from the ``tokens`` section of the diff
        (``ClientNewYear.synchronize`` sorts every ``ny18:`` token into boxes,
        chests, discount certificates or tankwoman choices).
        """
        if loot.get("tankman_token"):
            token = self._random_tankman_token()
            if token is not None:
                self.state.variadic_tankmen[token] = self.state.variadic_tankmen.get(token, 0) + 1
                self._refresh_tankman_choices()
                result.diff.setdefault("tokens", {})[token] = (None, self.state.variadic_tankmen[token])
                result.diff.setdefault("newYear", {})["tankmanNextChoices"] = {
                    nation: list(choice)
                    for nation, choice in sorted(self.state.tankman_next_choices.items())
                }
        if loot.get("discount_token"):
            token = self._random_discount_token()
            if token is not None:
                self.state.variadic_discounts[token] = self.state.variadic_discounts.get(token, 0) + 1
                result.diff.setdefault("tokens", {})[token] = (None, self.state.variadic_discounts[token])

    def _roll_box_toys(self, setting: str, loot: dict) -> List[int]:
        count = self.rng.randint(*loot["toys"])
        if self.rng.random() < loot.get("extra_toy_chance", 0.0):
            count += 1
        toys = []
        for _ in range(count):
            rank = ny_rules.make_random_choice(nyc.CRAFT.PROBABILITY_FOR_RANK, self.rng)
            type_id = ny_rules.make_random_choice(nyc.CRAFT.PROBABILITY_FOR_TYPE_ID, self.rng)
            setting_id = nyc.NATIONAL_SETTINGS_IDS_BY_NAME[setting]
            toy_id = ny_rules.choose_toy_using_craft_rules(
                self.catalog.toy_groups, type_id, setting_id, rank, self.rng
            )
            if toy_id is not None:
                toys.append(toy_id)
        return toys

    def owned_vehicle_comp_descrs(self) -> set:
        if self.owned_vehicles is None:
            return set()
        try:
            return set(self.owned_vehicles())
        except Exception:  # pragma: no cover - defensive
            return set()

    def _roll_reward_vehicle(self, setting: Optional[str] = None, level: Optional[int] = None):
        """A premium vehicle of the box's nation set, or ``None``.

        The client does not ship the "which vehicle does a New Year box give"
        table (that was server data in the live game), so
        :class:`server.game.rewards.VehicleRewardChooser` builds one from the
        player's own vehicle catalogue.
        """
        if self.vehicles is None:
            return None
        owned = self.owned_vehicle_comp_descrs()
        if level is None:
            level = REWARD_VEHICLE_LEVEL
        if setting is None:
            return self.vehicles.choose_any(self.rng, level=level, owned=owned)
        return self.vehicles.choose(setting, self.rng, level=level, owned=owned)

    def _vehicle_bonus(self, vehicle, level: Optional[int] = None) -> Dict:
        """The ``vehicles`` entry of a New Year system message.

        ``_NewYearBoxStorage.__extractBonuses`` iterates the value as a *list*
        of ``{intCD: vehInfo}`` dictionaries, so that is what goes out here.
        """
        reward = self.vehicles.reward(vehicle) if self.vehicles is not None else None
        if reward is None:  # pragma: no cover - defensive
            return {vehicle.compact_descr: {"crewLvl": 100}}
        return reward.as_quest_value()

    def _roll_reward_tankman(self, setting: str):
        if self.tankmen is None:
            return None
        nation_ids = [
            nation_id
            for nation_id, value in nyc.SETTING_ID_BY_NATION_ID.items()
            if nyc.NATIONAL_SETTINGS[value] == setting
        ]
        if not nation_ids:
            return None
        nation_id = self.rng.choice(nation_ids)
        try:
            passport = self.tankmen.make_female_passport(nation_id)
        except Exception:
            return None
        vehicle_type_id = self._tankman_vehicle_type_id(nation_id)
        return rewards_module.tankman_bonus_record(
            passport, "commander", vehicle_type_id, role_level=100, is_female=True
        )

    def _tankman_vehicle_type_id(self, nation_id: int) -> int:
        """A real vehicle of ``nation_id`` for a tankman record.

        ``bonuses.TankmenBonus.getTankmenGroups`` looks the vehicle up by
        ``(nationID, vehicleTypeID)`` and crashes on an unknown one, so the
        record always references a vehicle of the client's own catalogue.
        """
        if self.vehicles is not None:
            for vehicle in self.vehicles.catalog.vehicles.all():
                if vehicle.nation_id == nation_id and not vehicle.is_secret:
                    return vehicle.innation_id
        return 0

    def _random_tankman_token(self) -> Optional[str]:
        """One of the ``ny18:vt:*`` recruit tokens the client knows."""
        candidates = sorted(
            token for token in self.catalog.variadic_tankmen if self.catalog.variadic_tankmen[token]
        )
        if not candidates:
            return None
        level = self.atmosphere_level()
        ranked = [
            token
            for token in candidates
            if self.catalog.variadic_tankmen[token].level <= level
        ]
        return self.rng.choice(sorted(ranked or candidates))

    def _random_discount_token(self) -> Optional[str]:
        candidates = sorted(self.catalog.variadic_discounts)
        if not candidates:
            return None
        level = self.atmosphere_level()
        ranked = [
            token
            for token in candidates
            if self._discount_level(token) <= level
        ]
        return self.rng.choice(sorted(ranked or candidates))

    @staticmethod
    def _discount_level(token: str) -> int:
        """The atmosphere level a ``ny18:vd:<level>`` certificate belongs to."""
        try:
            return int(str(token).rsplit(":", 2)[-1])
        except ValueError:
            return 0

    def open_chest(self, chest_id: str) -> NewYearResult:
        """``openChest(id)`` - one New Year chest.

        The reward of a chest is shown by the client's *hidden quest* with the
        chest's id (``_NewYearChestStorage._handleRequestResult`` reads
        ``quest.getBonuses()``), while the account side of the reward travels in
        the usual system message.  Both are produced from one loot roll here:
        the quest bonus goes into ``result.chest_loot`` (the account state and
        the events builder turn it into a refreshed ``eventsData``), the credits,
        gold, decorations and vehicles into ``result.bonuses``.
        """
        chest = self.catalog.chest(chest_id)
        if chest is None:
            raise NewYearError("unknown chest %r" % (chest_id,))
        if self.state.chests.get(chest_id, 0) <= 0:
            raise NewYearError("no %s in the account" % (chest_id,))
        self.state.chests[chest_id] -= 1

        loot = CHEST_LOOT.get(chest.level, CHEST_LOOT[max(CHEST_LOOT)])
        bonuses: Dict = {"completedQuestIDs": [chest_id]}
        quest_bonus: Dict = {}
        diff: Dict = {"tokens": {chest_id: (None, self.state.chests[chest_id])}}

        # -- decorations --------------------------------------------------
        rank = int(loot.get("toy_rank", 1))
        toy_ids = []
        for _ in range(int(loot.get("toys", 1))):
            setting_id = self.rng.randrange(len(nyc.NATIONAL_SETTINGS))
            type_id = ny_rules.make_random_choice(nyc.CRAFT.PROBABILITY_FOR_TYPE_ID, self.rng)
            toy_id = ny_rules.choose_toy_using_craft_rules(
                self.catalog.toy_groups, type_id, setting_id, rank, self.rng
            )
            if toy_id is not None:
                toy_ids.append(toy_id)
        if toy_ids:
            stored, _new, fragments = self._add_toys(toy_ids)
            bonuses["ny18Toys"] = stored
            quest_bonus["ny18Toys"] = list(stored)
            diff["newYear"] = {
                "inventoryToys": {toy_id: list(self.state.inventory_toys[toy_id]) for toy_id in stored},
                "toyFragments": self.state.toy_fragments,
                "toyCollection": ny_rules.pack_toy_collection(self.state.toy_collection),
            }
            if fragments:
                bonuses["toyFragments"] = fragments
                quest_bonus["toyFragments"] = fragments

        # -- money ---------------------------------------------------------
        credits = int(loot.get("credits", 0))
        if credits:
            bonuses["credits"] = credits
            quest_bonus["credits"] = credits
        gold = int(loot.get("gold", 0))
        if gold:
            bonuses["gold"] = gold
            quest_bonus["gold"] = gold
        premium = int(loot.get("premium", 0))
        if premium:
            bonuses["premium"] = premium
            quest_bonus["premium"] = premium

        # -- the box vehicle ----------------------------------------------
        if loot.get("vehicle"):
            vehicle = self._roll_reward_vehicle(loot.get("vehicle_setting"), loot.get("vehicle_level"))
            if vehicle is not None:
                bonuses.setdefault("vehicles", []).append(self._vehicle_bonus(vehicle))
                quest_bonus["vehicles"] = self._vehicle_bonus(vehicle)

        # -- a fresh tankwoman or discount certificate ---------------------
        token = None
        if loot.get("tankman_token"):
            token = self._random_tankman_token()
            if token is not None:
                self.state.variadic_tankmen[token] = self.state.variadic_tankmen.get(token, 0) + 1
                self._refresh_tankman_choices()
                diff.setdefault("tokens", {})[token] = (None, self.state.variadic_tankmen[token])
                diff.setdefault("newYear", {})["tankmanNextChoices"] = {
                    nation: list(choice)
                    for nation, choice in sorted(self.state.tankman_next_choices.items())
                }
        if loot.get("discount_token"):
            token = self._random_discount_token()
            if token is not None:
                self.state.variadic_discounts[token] = self.state.variadic_discounts.get(token, 0) + 1
                diff.setdefault("tokens", {})[token] = (None, self.state.variadic_discounts[token])

        result = NewYearResult(diff=diff, bonuses=bonuses, chest_loot={chest_id: quest_bonus})
        rewards = self._grant_collection_rewards()
        if rewards:
            bonuses["completedQuestIDs"].extend(rewards)
        return result

    def craft(self, toy_type: Optional[int], setting_id: Optional[int], rank: Optional[int]) -> NewYearResult:
        """``craft(toyType, toyNation, toyLevel)`` - one visit to the workshop.

        ``None``/``-1`` on any axis means "the workshop picks it", which is
        exactly what :func:`server.game.ny_rules.choose_toy_using_craft_rules`
        does; the price is the client's own
        :func:`server.game.ny_rules.calculate_craft_cost`.
        """
        toy_type = None if toy_type in (None, nyc.INVALID_TOY_ID) else int(toy_type)
        setting_id = None if setting_id in (None, nyc.INVALID_TOY_ID) else int(setting_id)
        rank = None if rank in (None, nyc.INVALID_TOY_ID) else int(rank)
        if toy_type is not None and not 0 <= toy_type < len(nyc.TOY_TYPES):
            raise NewYearError("unknown toy type %r" % (toy_type,))
        if setting_id is not None and not 0 <= setting_id < len(nyc.NATIONAL_SETTINGS):
            raise NewYearError("unknown setting %r" % (setting_id,))
        if rank is not None and not 1 <= rank <= nyc.MAX_TOY_RANK:
            raise NewYearError("unknown rank %r" % (rank,))

        cost = ny_rules.calculate_craft_cost(toy_type is not None, setting_id is not None, rank)
        if cost is None:
            raise NewYearError("cannot price the request")
        if self.state.toy_fragments < cost:
            raise NewYearError("not enough toy fragments: %d of %d" % (self.state.toy_fragments, cost))

        toy_id = ny_rules.choose_toy_using_craft_rules(
            self.catalog.toy_groups,
            nyc.INVALID_TOY_ID if toy_type is None else toy_type,
            nyc.INVALID_TOY_ID if setting_id is None else setting_id,
            nyc.INVALID_TOY_ID if rank is None else rank,
            self.rng,
        )
        if toy_id is None:
            raise NewYearError("no decoration for that combination")
        self.state.toy_fragments -= cost
        stored, _new, fragments = self._add_toys([toy_id])
        result = NewYearResult(
            diff={
                "newYear": {
                    "toyFragments": self.state.toy_fragments,
                    "inventoryToys": {toy_id: list(self.state.inventory_toys[toy_id])},
                    "toyCollection": ny_rules.pack_toy_collection(self.state.toy_collection),
                }
            },
            bonuses={"ny18Toys": stored},
        )
        if fragments:
            result.bonuses["toyFragments"] = fragments
        coins = self.goodies.get("craftCoins")
        if coins:
            result.bonuses["gold"] = int(coins)
        return result

    def break_toys(self, toys) -> NewYearResult:
        """``breakToys([(toyID, count), ...])`` - decor -> toy fragments."""
        if not toys:
            raise NewYearError("no decorations to break")
        placed = self.placed_toys()
        available: Dict[int, int] = {}
        for toy_id, counts in self.state.inventory_toys.items():
            available[toy_id] = counts[0]
        for toy_id in placed:
            available[toy_id] = available.get(toy_id, 0) + 1

        fragments = 0
        removed: Dict[int, int] = {}
        for toy_id, count in toys:
            toy_id = int(toy_id)
            count = int(count)
            toy = self.catalog.toys.get(toy_id)
            if toy is None:
                raise NewYearError("unknown decoration %r" % (toy_id,))
            if count <= 0:
                continue
            if available.get(toy_id, 0) < count:
                raise NewYearError("not enough copies of decoration %d" % toy_id)
            available[toy_id] -= count
            removed[toy_id] = removed.get(toy_id, 0) + count
            fragments += toy.fragments * count

        for toy_id, count in removed.items():
            counts = self.state.inventory_toys.get(toy_id)
            if counts is None:
                continue
            taken = min(count, counts[0])
            counts[0] -= taken
            counts[1] = min(counts[1], counts[0])
            # A broken decoration keeps its (now empty) entry: the client's
            # ``_NewYearInventoryStorage`` reads every value as a
            # ``ToyInventoryData`` tuple and only a positive count makes a
            # decoration hangable, so a zero count is both safe and printable.
        self.state.toy_fragments += fragments
        return NewYearResult(
            diff={
                "newYear": {
                    "toyFragments": self.state.toy_fragments,
                    "inventoryToys": {
                        toy_id: list(counts) for toy_id, counts in self.state.inventory_toys.items()
                    },
                }
            },
            bonuses={"toyFragments": fragments},
        )

    def fill_slot(self, slot_id: int, toy_id: int) -> NewYearResult:
        """``fillSlot(slotID, toyID)`` - hang (or clear) a decoration."""
        if not 0 <= slot_id < len(self.state.slots):
            raise NewYearError("unknown slot %r" % (slot_id,))
        if toy_id != nyc.INVALID_TOY_ID:
            toy = self.catalog.toys.get(int(toy_id))
            if toy is None:
                raise NewYearError("unknown decoration %r" % (toy_id,))
            if toy_id not in self.available_toys():
                raise NewYearError("decoration %d is not in the account" % toy_id)
            if toy.type != self.catalog.slots[slot_id].type:
                raise NewYearError(
                    "decoration %d (%s) does not fit slot %d (%s)"
                    % (toy_id, toy.type, slot_id, self.catalog.slots[slot_id].type)
                )
        self.state.slots[slot_id] = int(toy_id)
        result = NewYearResult(diff={"newYear": {"slots": list(self.state.slots)}})
        level_up = self._sync_level()
        if level_up is not None:
            result.merge(level_up)
        return result

    def select_discount(self, goodie_id: int, variadic_discount_id: str) -> NewYearResult:
        """``selectDiscount(goodieID, variadicDiscountID)`` - claim a certificate."""
        goodie_id = int(goodie_id)
        if variadic_discount_id not in self.catalog.variadic_discounts:
            raise NewYearError("unknown discount %r" % (variadic_discount_id,))
        if self.state.variadic_discounts.get(variadic_discount_id, 0) <= 0:
            raise NewYearError("no %s in the account" % (variadic_discount_id,))
        descriptor = self.catalog.variadic_discounts[variadic_discount_id]
        low, high = descriptor.goodies_range
        if not low <= goodie_id <= high:
            raise NewYearError("goodie %d is outside %s" % (goodie_id, variadic_discount_id))
        definition = self.goodies.get(goodie_id)
        if self.goodies and definition is None:
            raise NewYearError("goodie %d is not an active offer" % (goodie_id,))
        self.state.variadic_discounts[variadic_discount_id] -= 1
        self.state.goodies[goodie_id] = (goodies_module.GOODIE_STATE_ACTIVE, 0, 1)
        return NewYearResult(
            diff={
                "tokens": {
                    variadic_discount_id: (None, self.state.variadic_discounts[variadic_discount_id])
                },
                ("goodies", "_r"): self.goodies_diff(),
            }
        )

    def select_tankman(
        self,
        nation_id: int,
        vehicle_innation_id: int,
        role_id: int,
        variadic_tankman_id: str,
    ) -> NewYearResult:
        """``selectTankman(nation, vehicle, role, token)`` - recruit the tankwoman.

        The client expects the new crew member in ``tankmen`` of the response so
        it can show it in the barracks, plus a refreshed ``tankmanNextChoices``
        when another token is still in the account.
        """
        if not 0 <= nation_id < len(nyc.NATIONS):
            raise NewYearError("unknown nation %r" % (nation_id,))
        if variadic_tankman_id not in self.catalog.variadic_tankmen:
            raise NewYearError("unknown tankman %r" % (variadic_tankman_id,))
        if self.state.variadic_tankmen.get(variadic_tankman_id, 0) <= 0:
            raise NewYearError("no %s in the account" % (variadic_tankman_id,))
        role = ROLE_BY_ID.get(int(role_id))
        if role is None:
            raise NewYearError("unknown role %r" % (role_id,))

        self.state.variadic_tankmen[variadic_tankman_id] -= 1
        result = NewYearResult(
            diff={"tokens": {variadic_tankman_id: (None, self.state.variadic_tankmen[variadic_tankman_id])}}
        )
        if self.tankmen is not None:
            try:
                passport = self.tankmen.make_female_passport(nation_id)
            except Exception as error:
                raise NewYearError("cannot create a tankwoman: %s" % (error,))
            result.bonuses = {
                "tankmen": [
                    {
                        "nationID": nation_id,
                        "vehicleTypeID": int(vehicle_innation_id),
                        "role": role,
                        "roleLevel": 100,
                        "passport": passport.as_tuple(),
                    }
                ]
            }
            result.diff["newYear"] = {"tankmanNextChoices": {}}
        self._refresh_tankman_choices()
        result.diff.setdefault("newYear", {})["tankmanNextChoices"] = {
            nation: list(choice) for nation, choice in sorted(self.state.tankman_next_choices.items())
        }
        return result

    # -- developer helpers (mirroring the client's DEV commands) ---------
    def add_toys(self, toys) -> NewYearResult:
        stored, _new, fragments = self._add_toys([int(toy_id) for toy_id in toys])
        result = NewYearResult(
            diff={
                "newYear": {
                    "inventoryToys": {
                        toy_id: list(self.state.inventory_toys[toy_id]) for toy_id in stored
                    },
                    "toyCollection": ny_rules.pack_toy_collection(self.state.toy_collection),
                    "toyFragments": self.state.toy_fragments,
                }
            }
        )
        if fragments:
            result.bonuses = {"toyFragments": fragments}
        return result

    def add_token(self, token: str, count: int = 1) -> NewYearResult:
        """``addTokenDev`` - hand the account one more of a New Year token.

        The token may be one the account already has (a box, a chest, a
        discount certificate, a tankwoman) or any id the event knows, which is
        how the client's own DEV helpers hand out their first one.
        """
        known = {
            "boxes": self.catalog.boxes,
            "chests": self.catalog.chests,
            "variadic_discounts": self.catalog.variadic_discounts,
            "variadic_tankmen": self.catalog.variadic_tankmen,
        }
        for name, storage in (
            ("boxes", self.state.boxes),
            ("chests", self.state.chests),
            ("variadic_discounts", self.state.variadic_discounts),
            ("variadic_tankmen", self.state.variadic_tankmen),
        ):
            if token in storage or token in known[name]:
                storage[token] = storage.get(token, 0) + count
                return NewYearResult(diff={"tokens": {token: (None, storage[token])}})
        raise NewYearError("unknown token %r" % (token,))

    def see_toys(self, toys) -> NewYearResult:
        """``seeToys`` - the client's "mark as seen" bookkeeping."""
        changed = {}
        for toy_id in toys:
            counts = self.state.inventory_toys.get(int(toy_id))
            if counts is None:
                continue
            if counts[1]:
                counts[1] = 0
                changed[toy_id] = list(counts)
        if not changed:
            return NewYearResult()
        return NewYearResult(diff={"newYear": {"inventoryToys": changed}})

    def stock(self, boxes: int = 5, chests: int = 1, fragments: int = 1000,
              present_boxes: int = 1) -> NewYearResult:
        """Fill the event up so a fresh offline account can play all of it.

        The live event hands boxes out for battles and chests for the atmosphere
        level, neither of which an offline account can earn; the local server
        therefore stocks every container the client knows (the "present" boxes
        are the gifts of the event and carry the most valuable loot) and gives
        the workshop enough toy fragments to be usable from the first minute.
        """
        diff_tokens = {}
        for name, storage in (
            ("boxes", self.state.boxes),
            ("chests", self.state.chests),
        ):
            catalog = self.catalog.boxes if name == "boxes" else self.catalog.chests
            for container_id in sorted(catalog):
                present = container_id.endswith("p") or container_id in ("ny18:box:sp",)
                count = present_boxes if present else (boxes if name == "boxes" else chests)
                if count:
                    storage[container_id] = storage.get(container_id, 0) + count
                    diff_tokens[container_id] = (None, storage[container_id])
        self.state.toy_fragments += fragments
        return NewYearResult(
            diff={
                "tokens": diff_tokens,
                "newYear": {"toyFragments": self.state.toy_fragments},
            }
        )

    def set_state(self, state: int) -> NewYearResult:
        self.state.state = int(state)
        return NewYearResult()


#: ``ROLE_ID`` of ``AccountCommands``/``tankmen`` (see ``SKILL_INDICES``).
ROLE_BY_ID = {1: "commander", 2: "radioman", 3: "driver", 4: "gunner", 5: "loader"}


__all__ = [
    "BOX_LOOT",
    "CHEST_LOOT",
    "DUPLICATE_FRAGMENT_RATIO",
    "NewYearError",
    "NewYearEvent",
    "NewYearResult",
    "NewYearState",
    "ROLE_BY_ID",
]
