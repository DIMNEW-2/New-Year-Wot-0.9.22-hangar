"""Buying and selling - the money side of the garage, the depot and the crew.

Every command here is sent by the client's own helpers, so the argument order is
theirs (``client/account_helpers/Shop.py`` / ``Inventory.py``):

===========================  ==================================================
``CMD_BUY_VEHICLE`` (301)    ``IntArr [rev, typeCompDescr, flags,
                             tmanCostTypeIdx, rentPeriod]``
``CMD_BUY_ITEM`` (302)       ``Int4 (rev, itemShopID, count, goldForCredits)``
``CMD_BUY_TMAN`` (303)       ``Int4 (rev, vehTypeCompDescr, roleIdx,
                             tmanCostTypeIdx)`` - the callback reads
                             ``ext['tmanInvID']``
``CMD_SELL_VEHICLE`` (304)   ``IntArr [rev, vehInvID, flags, len(items)…]``
``CMD_SELL_ITEM`` (305)      ``Int4 (rev, itemTypeIdx, itemInvID, count)`` -
                             ``itemInvID`` is the item's compact descriptor
``CMD_BUY_SLOT`` (205)       ``Int3 (rev, 0, 0)``
``CMD_BUY_BERTHS`` (206)     ``Int3 (rev, 0, 0)``
===========================  ==================================================

What a purchase costs and what a sale refunds is computed from the same shop
cache the client displays, so the number in the dialog and the number the local
server charges never disagree: a buy costs ``itemPrices[compact]`` and a sale
refunds ``ceil(sellPriceFactor * (credits + gold * exchangeRate))``
(``Shop.getSellPrice``, with the shells/equipment rate for those two types).
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

from ..game import items as items_module
from ..game import shop as shop_module
from .state import AccountState

ItemType = int
Price = Tuple[int, int]

#: ``BUY_VEHICLE_FLAG`` of ``common/AccountCommands.py``.
BUY_VEHICLE_FLAG_NONE = 0
BUY_VEHICLE_FLAG_CREW = 1
BUY_VEHICLE_FLAG_SHELLS = 16

#: ``_EQUIPMENT_TYPE``/``_SHELL`` of the shop's sell price: those two are
#: exchanged with ``exchangeRateForShellsAndEqs``.
SHELL_TYPE = items_module.ITEM_TYPES["shell"]
EQUIPMENT_TYPE = items_module.ITEM_TYPES["equipment"]
VEHICLE_TYPE = items_module.ITEM_TYPES["vehicle"]
TANKMAN_TYPE = items_module.ITEM_TYPES["tankman"]

#: ``Shop.buyTankman`` sends ``tankmen.SKILL_INDICES[role]`` and the role name
#: ``items.tankmen.SKILL_NAMES`` orders.
ROLE_BY_INDEX = {index: name for name, index in
                 zip(("commander", "radioman", "driver", "gunner", "loader"), range(1, 6))}

#: The cost of one garage slot and of one barracks berth pack (gold).
SLOT_PRICE = 300
BERTH_PACK_PRICE = 300
BERTH_PACK_SIZE = 1

#: How many slots/berths one command buys.
SLOTS_PER_COMMAND = 1
BERTHS_PER_COMMAND = 1


class PurchaseError(Exception):
    """A refused purchase or sale; ``code`` is the answer the client expects."""

    def __init__(self, code: int, error: str):
        Exception.__init__(self, error)
        self.code = int(code)
        self.error = error


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def parse_buy_vehicle(args: Sequence) -> Tuple[int, int, int, int, int]:
    """``[rev, compDescr, flags, tmanCostTypeIdx, rentPeriod]``."""
    values = list(args[0]) if args and isinstance(args[0], (list, tuple)) else list(args)
    if len(values) < 3:
        raise PurchaseError(-2, "INVALID_PURCHASE_REQUEST")
    flags = _as_int(values[2])
    tman_cost = _as_int(values[3]) if len(values) > 3 else 0
    rent_period = _as_int(values[4]) if len(values) > 4 else -1
    return _as_int(values[0]), _as_int(values[1]), flags, tman_cost, rent_period


def parse_sell_vehicle(args: Sequence) -> Tuple[int, int, int, list, list]:
    """``[rev, vehInvID, isCrewDismiss, len(itemsFromVehicle), …]``."""
    values = list(args[0]) if args and isinstance(args[0], (list, tuple)) else list(args)
    if len(values) < 4:
        raise PurchaseError(-2, "INVALID_SALE_REQUEST")
    from_vehicle_count = _as_int(values[3])
    tail = 4 + from_vehicle_count
    if from_vehicle_count < 0 or len(values) < tail + 1:
        raise PurchaseError(-2, "INVALID_SALE_REQUEST")
    items_from_vehicle = [_as_int(value) for value in values[4:tail]]
    from_inventory_count = _as_int(values[tail])
    end = tail + 1 + from_inventory_count
    if from_inventory_count < 0 or len(values) < end:
        raise PurchaseError(-2, "INVALID_SALE_REQUEST")
    items_from_inventory = [_as_int(value) for value in values[tail + 1:end]]
    return (_as_int(values[0]), _as_int(values[1]), _as_int(values[2]),
            items_from_vehicle, items_from_inventory)


class Shopkeeper(object):
    """The money side of one account."""

    def __init__(self, state: AccountState):
        self.state = state
        self.shop = state.shop_builder

    # -- helpers --------------------------------------------------------
    def price(self, compact_descr: int) -> Price:
        """``(credits, gold)`` of an item; a missing price means "not for sale"."""
        price = self.shop.price(compact_descr)
        if price is None:
            raise PurchaseError(-1, "ITEM_NOT_FOUND")
        return price

    def sell_value(self, compact_descr: int, item_type: int, count: int = 1) -> int:
        """Credits refunded for one item, exactly as ``Shop.getSellPrice`` does."""
        price = self.price(compact_descr)
        payload = self.shop.payload()
        rate = payload["exchangeRate"]
        if item_type in (SHELL_TYPE, EQUIPMENT_TYPE):
            rate = payload["exchangeRateForShellsAndEqs"]
        factor = payload["sellPriceFactor"]
        credits = int(math.ceil(factor * (price[0] + price[1] * rate)))
        return credits * max(0, int(count))

    def _charge(self, credits: int = 0, gold: int = 0) -> None:
        """Take the price: every currency the price names has to be there.

        A vehicle is priced in one currency, but the crew and the ammunition a
        purchase may include are priced separately, so a single purchase can
        cost credits *and* gold (the client's ``MoneyValidator`` checks the
        wallet the same way, per currency).
        """
        state = self.state
        if credits > state.credits or gold > state.gold:
            raise PurchaseError(-1, "NOT_ENOUGH_MONEY")
        state.credits -= int(credits)
        state.gold -= int(gold)

    def _pay(self, credits: int) -> None:
        self.state.credits += max(0, int(credits))

    #: Public faces of the two wallet helpers, for the other keepers of the
    #: account (the customization window charges and refunds the same way).
    def charge(self, credits: int = 0, gold: int = 0) -> None:
        self._charge(credits=credits, gold=gold)

    def refund(self, credits: int) -> None:
        self._pay(credits)

    def vehicle_type(self, compact_descr: int):
        vehicle = self.state.catalog.vehicles.get(compact_descr)
        if vehicle is None:
            raise PurchaseError(-2, "UNKNOWN_VEHICLE")
        return vehicle

    # -- buying ---------------------------------------------------------
    def buy_vehicle(self, compact_descr: int, flags: int, tman_cost_type_index: int = 0) -> dict:
        """``CMD_BUY_VEHICLE``: pay for a vehicle and put it in the garage.

        The answer's ``ext`` carries the new inventory id (the client's own
        ``buyVehicle`` callback only looks at the code, but a mod or a script
        that reads ``ext`` finds the same key the live server sends).
        """
        state = self.state
        vehicle = self.vehicle_type(compact_descr)
        if compact_descr in state.owned_vehicle_comp_descrs():
            raise PurchaseError(-1, "ALREADY_BOUGHT")
        crew = bool(flags & BUY_VEHICLE_FLAG_CREW)
        shells = bool(flags & BUY_VEHICLE_FLAG_SHELLS)
        credits, gold = int(vehicle.price_credits), int(vehicle.price_gold)
        if crew:
            members = len(state.crew_roles_of_type(vehicle))
            crew_credits, crew_gold = self.crew_cost(tman_cost_type_index)
            credits += crew_credits * members
            gold += crew_gold * members
        if shells:
            ammo_credits, ammo_gold = self.ammo_cost(vehicle, self._default_ammo(vehicle))
            credits += ammo_credits
            gold += ammo_gold
        self._charge(credits=credits, gold=gold)
        entry = state.add_vehicle(vehicle, crew=crew, shells=shells)
        return {
            "code": 0,
            "ext": {"vehInvID": entry.inv_id},
            "diff": self.purchase_diff(vehicle_inv_ids=[entry.inv_id],
                                       tankman_inv_ids=[t for t in entry.crew if t]),
        }

    def _default_ammo(self, vehicle) -> list:
        """The stock ammunition of a vehicle type (empty when it is unknown)."""
        try:
            turret, gun = self.state.stock_gun_for(vehicle)
        except Exception:  # pragma: no cover - a broken catalogue never bills
            return []
        if turret is None or gun is None:
            return []
        return self.state.catalog.vehicles.default_ammo(vehicle, gun.id)

    def buy_item(self, compact_descr: int, count: int, gold_for_credits: bool = False) -> dict:
        """``CMD_BUY_ITEM``: shells, equipment, consumables, optional devices."""
        state = self.state
        count = int(count)
        if count <= 0:
            raise PurchaseError(-2, "INVALID_PURCHASE_REQUEST")
        item_type = items_module.get_type_of_compact_descr(compact_descr)
        if item_type in (items_module.ITEM_TYPES["vehicle"], TANKMAN_TYPE):
            raise PurchaseError(-2, "INVALID_PURCHASE_REQUEST")
        credits, gold = self.price(compact_descr)
        payload = self.shop.payload()
        if gold > 0 and gold_for_credits:
            rate = (payload["exchangeRateForShellsAndEqs"]
                    if item_type in (SHELL_TYPE, EQUIPMENT_TYPE) else payload["exchangeRate"])
            credits, gold = gold * rate, 0
        if gold > 0:
            self._charge(gold=gold * count)
        else:
            self._charge(credits=credits * count)
        state.add_item(item_type, compact_descr, count)
        return {
            "code": 0,
            "ext": None,
            "diff": self.purchase_diff(item_types=[item_type]),
        }

    def buy_tankman(self, vehicle_compact_descr: int, role_index: int,
                    tman_cost_type_index: int = 0) -> dict:
        """``CMD_BUY_TMAN``: recruit one crew member for a vehicle type."""
        state = self.state
        vehicle = self.vehicle_type(vehicle_compact_descr)
        role = ROLE_BY_INDEX.get(int(role_index))
        if role is None:
            raise PurchaseError(-2, "INVALID_CREW_REQUEST")
        credits, gold = self.crew_cost(tman_cost_type_index)
        if gold > 0:
            self._charge(gold=gold)
        elif credits > 0:
            self._charge(credits=credits)
        tankman = state.recruit_tankman(
            vehicle.nation_id, vehicle.innation_id, role,
            role_level=self.crew_role_level(tman_cost_type_index),
        )
        if tankman is None:
            raise PurchaseError(-1, "CANNOT_RECRUIT")
        return {
            "code": 0,
            # ``Shop.buyTankman`` reads both keys out of the ext
            # (``ext.get('tmanInvID')`` / ``ext.get('tmanCompDescr')``).
            "ext": {"tmanInvID": tankman.inv_id, "tmanCompDescr": tankman.compact_descr},
            "diff": self.purchase_diff(tankman_inv_ids=[tankman.inv_id]),
        }

    def buy_slot(self) -> dict:
        """``CMD_BUY_SLOT``: one more garage slot for gold."""
        self._charge(gold=SLOT_PRICE * SLOTS_PER_COMMAND)
        self.state.slots += SLOTS_PER_COMMAND
        return {"code": 0, "ext": None, "diff": self.purchase_diff(stats=True)}

    def buy_berths(self) -> dict:
        """``CMD_BUY_BERTHS``: one more barracks berth pack for gold."""
        self._charge(gold=BERTH_PACK_PRICE * BERTHS_PER_COMMAND)
        self.state.berths += BERTH_PACK_SIZE * BERTHS_PER_COMMAND
        return {"code": 0, "ext": None, "diff": self.purchase_diff(stats=True)}

    def _crew_option(self, tman_cost_type_index: int) -> dict:
        table = self.shop.payload().get("tankmanCost") or shop_module.TANKMAN_COST
        index = int(tman_cost_type_index)
        if index < 0 or index >= len(table):
            return {}
        return dict(table[index] or {})

    def crew_cost(self, tman_cost_type_index: int) -> Price:
        """What the chosen recruitment option costs; the table is the shop's own."""
        option = self._crew_option(tman_cost_type_index)
        return int(option.get("credits", 0) or 0), int(option.get("gold", 0) or 0)

    def crew_role_level(self, tman_cost_type_index: int) -> int:
        """The role level the recruited crew member is packed with.

        ``Shop.buyTankman`` offers three options (``tankmanCost``); the client
        shows ``roleLevel`` (50/75/100) in the recruiter window, so the
        descriptor the account hands out carries the same number.
        """
        option = self._crew_option(tman_cost_type_index)
        return int(option.get("roleLevel", 100) or 100)

    def _crew_cost(self, tman_cost_type_index: int) -> Price:
        return self.crew_cost(tman_cost_type_index)

    def ammo_cost(self, vehicle, ammo) -> Price:
        """What the default ammunition of a gun costs (credits, gold).

        ``getCrewAndShellsSumPrice`` of the client charges
        ``shell.buyPrices.itemPrice.price * shell.defaultCount`` for every shell
        of ``vehicle.gun.defaultAmmo``, so the local server charges the same.
        """
        credits = gold = 0
        for index in range(0, len(ammo) - 1, 2):
            compact, count = int(ammo[index]), int(ammo[index + 1])
            if count <= 0:
                continue
            price = self.state.price_of(compact) or (0, 0)
            credits += max(0, int(price[0])) * count
            gold += max(0, int(price[1])) * count
        return credits, gold

    # -- selling --------------------------------------------------------
    def sell_vehicle(self, inv_id: int, dismiss_crew: bool = False,
                     items_from_vehicle: Sequence[int] = (),
                     items_from_inventory: Sequence[int] = ()) -> dict:
        """``CMD_SELL_VEHICLE``: refund the vehicle (and what it carried)."""
        state = self.state
        entry = state.vehicles.get(int(inv_id))
        if entry is None:
            raise PurchaseError(-1, "VEHICLE_NOT_FOUND")
        refund = self.sell_value(entry.compact_descr, VEHICLE_TYPE)
        removed_tankmen = []
        if dismiss_crew:
            removed_tankmen = [tankman_id for tankman_id in entry.crew if tankman_id]
            for tankman_id in removed_tankmen:
                state.tankmen.pop(tankman_id, None)
        for compact_descr in list(items_from_vehicle) + list(items_from_inventory):
            item_type = items_module.get_type_of_compact_descr(compact_descr)
            refund += self.sell_value(compact_descr, item_type)
            if item_type in state.items:
                state.remove_item(item_type, compact_descr, 1)
        del state.vehicles[entry.inv_id]
        self._pay(refund)
        return {
            "code": 0,
            "ext": None,
            "diff": self.purchase_diff(vehicle_inv_ids=[entry.inv_id],
                                       tankman_inv_ids=removed_tankmen,
                                       item_types=list(state.items.keys())),
        }

    def sell_item(self, item_type: int, compact_descr: int, count: int) -> dict:
        """``CMD_SELL_ITEM``: refund ``count`` items of one type."""
        state = self.state
        item_type = int(item_type)
        count = int(count)
        if count <= 0:
            raise PurchaseError(-2, "INVALID_SALE_REQUEST")
        if state.item_count(item_type, compact_descr) < count:
            raise PurchaseError(-1, "NOT_ENOUGH_ITEMS")
        refund = self.sell_value(compact_descr, item_type, count)
        state.remove_item(item_type, compact_descr, count)
        self._pay(refund)
        return {
            "code": 0,
            "ext": None,
            "diff": self.purchase_diff(item_types=[item_type]),
        }

    # -- diffs ----------------------------------------------------------
    def purchase_diff(self, vehicle_inv_ids=(), tankman_inv_ids=(), item_types=(),
                      stats: bool = False) -> dict:
        """The incremental diff a purchase or a sale has to publish."""
        inventory = self.state.partial_inventory_diff(
            vehicle_inv_ids=vehicle_inv_ids,
            tankman_inv_ids=tankman_inv_ids,
            item_types=item_types,
        )
        extra: Dict[str, dict] = {
            "inventory": inventory,
            "economics": self.state.economics_diff(),
            "stats": self.state.purchase_stats_diff(),
        }
        if stats or not any((vehicle_inv_ids, tankman_inv_ids, item_types)):
            extra["stats"] = self.state._stats_diff()
        return self.state.incremental_diff(extra=extra)


__all__ = [
    "BUY_VEHICLE_FLAG_CREW",
    "BUY_VEHICLE_FLAG_NONE",
    "BUY_VEHICLE_FLAG_SHELLS",
    "PurchaseError",
    "ROLE_BY_INDEX",
    "Shopkeeper",
    "parse_buy_vehicle",
    "parse_sell_vehicle",
]
