"""The shop: the price cache, buying and selling, over the real socket.

The client's own helpers send these commands (``Shop.buyVehicle``,
``Shop.buy``, ``Inventory.__sellItem_onShopSynced``, ``Stats.buySlot``), and
what a purchase must produce is what the client's caches read from the answer:
the wallet in ``economics``, the new inventory entries in ``inventory`` and the
price the dialog showed.  The tests drive the same socket the mod uses, so the
command shapes are the ones the client really writes.
"""

from __future__ import annotations

import math
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc.server import AccountServer  # noqa: E402
from server.account_rpc.state import AccountState  # noqa: E402
from server.game import items as items_module  # noqa: E402
from server.game import shop as shop_module  # noqa: E402
from server.proto import py2_loads  # noqa: E402
from test_local_server import Bridge  # noqa: E402

SHELL = items_module.ITEM_TYPES["shell"]
EQUIPMENT = items_module.ITEM_TYPES["equipment"]
OPTIONAL_DEVICE = items_module.ITEM_TYPES["optionalDevice"]


def _socket_pair(runner):
    """``(state, server, bridge)`` of a running local server."""
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    bridge = Bridge(server.port)
    bridge.hello()
    return state, server, bridge


def _prices_of(state, item_type: int):
    """``[(compactDescr, credits, gold), ...]`` of one item type, cheapest first."""
    prices = []
    for compact, price in state.shop_payload()["items"]["itemPrices"].items():
        if items_module.get_type_of_compact_descr(compact) != item_type:
            continue
        if price[0] <= 0 and price[1] <= 0:
            continue
        prices.append((compact, price[0], price[1]))
    return sorted(prices, key=lambda entry: (entry[1] + entry[2] * 1000, entry[0]))


def _not_owned_vehicle(state):
    """A vehicle the account does not own yet (a premium one, by default)."""
    catalog = state.catalog
    for vehicle in catalog.vehicles.playable():
        if vehicle.is_premium and vehicle.price_gold > 0:
            return vehicle
    for vehicle in catalog.vehicles.playable():
        if vehicle.compact_descr not in state.owned_vehicle_comp_descrs():
            return vehicle
    raise common.Skip("every catalogue vehicle is already owned")


def test_shop_payload_prices_every_item(runner: common.Runner) -> None:
    """Vehicles, modules, shells, equipment and devices all carry a price."""
    state, server, bridge = _socket_pair(runner)
    try:
        response, diffs, actions = bridge.command(2, cmd.CMD_SYNC_SHOP, (0, 0, 0))
        runner.equal(response["code"], cmd.RES_STREAM, "the shop sync answer")
        shop = py2_loads(zlib.decompress(bridge.recv()["data"]))
        prices = shop["items"]["itemPrices"]
        runner.check(len(prices) > 1000, "the price table is complete (%d)" % len(prices))
        for name, item_type in (("shells", SHELL), ("equipment", EQUIPMENT),
                                ("optional devices", OPTIONAL_DEVICE)):
            found = [compact for compact in prices
                     if items_module.get_type_of_compact_descr(compact) == item_type]
            runner.check(found, "%s are priced (%d)" % (name, len(found)))
        # The client indexes these without a default, so they must be there.
        for key in ("camouflagePriceFactors", "notInShopCamouflages",
                    "inscriptionGroupPriceFactors", "notInShopInscriptionGroups"):
            value = shop["items"][key]
            runner.check(isinstance(value, list) and len(value) >= 10,
                         "items.%s covers the nations" % key)
        for key in ("slotsPrices", "berthsPrices", "tankmanCost", "premiumCost",
                    "refSystem", "dropSkillsCost", "exchangeRate",
                    "sellPriceFactor", "restore_config"):
            runner.check(key in shop, "the shop carries %s" % key)
        runner.equal(shop["exchangeRate"], shop_module.EXCHANGE_RATE, "the gold rate")
        runner.equal(shop["sellPriceFactor"], shop_module.SELL_PRICE_FACTOR,
                     "the sell price factor")
        runner.check(len(shop["tankmanCost"]) == 3, "three recruitment options")
        bridge.close()
    finally:
        server.stop()


def test_buy_vehicle(runner: common.Runner) -> None:
    """``CMD_BUY_VEHICLE`` pays for the tank and publishes the new inventory id."""
    state, server, bridge = _socket_pair(runner)
    try:
        vehicle = _not_owned_vehicle(state)
        credits, gold = vehicle.price_credits, vehicle.price_gold
        before = (state.credits, state.gold)
        response, diffs, actions = bridge.command(
            3, cmd.CMD_BUY_VEHICLE, ([state.revision, vehicle.compact_descr,
                                      purchases_crew_flag(), 0, -1],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the purchase is accepted")
        ext = py2_loads(response["ext"]) if response["ext"] else {}
        inv_id = ext.get("vehInvID")
        runner.check(inv_id in state.vehicles, "the vehicle is in the garage")
        runner.equal(state.vehicles[inv_id].compact_descr, vehicle.compact_descr,
                     "the garage entry is the vehicle that was bought")
        diff = diffs[-1] if diffs else {}
        inventory = diff.get("inventory", {}).get(items_module.ITEM_TYPES["vehicle"], {})
        runner.check(inv_id in inventory.get("compDescr", {}),
                     "the new descriptor travels with the diff")
        runner.check(isinstance(inventory["compDescr"][inv_id], bytes),
                     "the descriptor is a byte string")
        runner.check(inv_id in inventory.get("crew", {}), "the crew of the new vehicle")
        economics = diff.get("economics", {})
        if gold:
            runner.equal(before[1] - economics.get("gold", before[1]), gold,
                         "the gold that was paid")
        else:
            runner.equal(before[0] - economics.get("credits", before[0]), credits,
                         "the credits that were paid")
        runner.check(vehicle.compact_descr in diff.get("stats", {}).get("unlocks", set()),
                     "the vehicle is unlocked for the client")
        bridge.close()
    finally:
        server.stop()


def purchases_crew_flag() -> int:
    from server.account_rpc.purchases import BUY_VEHICLE_FLAG_CREW

    return BUY_VEHICLE_FLAG_CREW


def test_buy_vehicle_twice_is_refused(runner: common.Runner) -> None:
    """A vehicle is bought once; the second request is refused, not paid for."""
    state, server, bridge = _socket_pair(runner)
    try:
        inv_id = min(state.vehicles)
        compact = state.vehicles[inv_id].compact_descr
        credits = state.credits
        response, diffs, actions = bridge.command(
            4, cmd.CMD_BUY_VEHICLE, ([state.revision, compact, 0, 0, -1],))
        runner.check(response["code"] < 0, "the answer is a failure (%s)" % response["code"])
        runner.equal(response["error"], "ALREADY_BOUGHT", "the reason")
        runner.equal(state.credits, credits, "no money was taken")
        bridge.close()
    finally:
        server.stop()


def test_buy_and_sell_items(runner: common.Runner) -> None:
    """Items are bought and sold for exactly what the client's dialogs show."""
    state, server, bridge = _socket_pair(runner)
    try:
        compact, credits, gold = _prices_of(state, SHELL)[0]
        runner.check(credits > 0 and gold == 0, "the shell is paid in credits")
        before = state.credits
        response, diffs, actions = bridge.command(
            5, cmd.CMD_BUY_ITEM, (state.revision, compact, 10, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the shells are bought")
        runner.equal(state.item_count(SHELL, compact), 10, "the depot holds them")
        runner.equal(before - state.credits, credits * 10, "the price of ten shells")
        response, diffs, actions = bridge.command(
            6, cmd.CMD_SELL_ITEM, (state.revision, SHELL, compact, 4))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the shells are sold")
        runner.equal(state.item_count(SHELL, compact), 6, "the depot lost four")
        refund = int(math.ceil(shop_module.SELL_PRICE_FACTOR * credits)) * 4
        runner.equal(state.credits, before - credits * 10 + refund,
                     "the refund matches Shop.getSellPrice")
        diff = diffs[-1] if diffs else {}
        runner.equal(diff.get("inventory", {}).get(SHELL, {}).get(compact), 6,
                     "the depot count travels with the diff")
        bridge.close()
    finally:
        server.stop()


def test_refusals(runner: common.Runner) -> None:
    """A request the account cannot pay for is refused and changes nothing."""
    state, server, bridge = _socket_pair(runner)
    try:
        compact, credits, gold = _prices_of(state, EQUIPMENT)[0]
        wallet = (state.credits, state.gold)
        response, diffs, actions = bridge.command(
            7, cmd.CMD_BUY_ITEM, (state.revision, compact, 10 ** 6, 0))
        runner.check(response["code"] < 0, "too expensive to buy (%s)" % response["code"])
        runner.equal((state.credits, state.gold), wallet, "the wallet is untouched")
        runner.equal(state.item_count(EQUIPMENT, compact), 0, "nothing was delivered")
        # An unknown item is refused as well.
        response, diffs, actions = bridge.command(
            8, cmd.CMD_BUY_ITEM, (state.revision, 0x0000FFFF, 1, 0))
        runner.check(response["code"] < 0, "an unknown item is refused")
        # Selling what the account does not own.
        response, diffs, actions = bridge.command(
            9, cmd.CMD_SELL_ITEM, (state.revision, EQUIPMENT, compact, 1))
        runner.check(response["code"] < 0, "selling nothing is refused")
        bridge.close()
    finally:
        server.stop()


def test_buy_slot_and_berths(runner: common.Runner) -> None:
    """Garage slots and barracks berths cost the gold the shop publishes."""
    state, server, bridge = _socket_pair(runner)
    try:
        slots, berths, gold = state.slots, state.berths, state.gold
        response, diffs, actions = bridge.command(10, cmd.CMD_BUY_SLOT, (state.revision, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the slot is bought")
        runner.equal(state.slots, slots + 1, "one more garage slot")
        runner.equal(gold - state.gold, shop_module.SLOTS_PRICES[1][0], "the slot price")
        gold = state.gold
        response, diffs, actions = bridge.command(11, cmd.CMD_BUY_BERTHS, (state.revision, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the berths are bought")
        runner.check(state.berths > berths, "more barracks berths")
        runner.equal(gold - state.gold, shop_module.BERTHS_PRICES[2][0], "the berth price")
        diff = diffs[-1] if diffs else {}
        runner.check("berths" in diff.get("stats", {}), "the stats carry the new berths")
        bridge.close()
    finally:
        server.stop()


def test_sell_vehicle_refunds_and_removes_it(runner: common.Runner) -> None:
    """A sold vehicle leaves the garage and its credits arrive."""
    state, server, bridge = _socket_pair(runner)
    try:
        inv_id = max(state.vehicles)
        entry = state.vehicles[inv_id]
        price = state.price_of(entry.compact_descr)
        before = state.credits
        crew = list(entry.crew)
        response, diffs, actions = bridge.command(
            12, cmd.CMD_SELL_VEHICLE, ([state.revision, inv_id, 1, 0, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the vehicle is sold")
        runner.check(inv_id not in state.vehicles, "the garage no longer has it")
        expected = int(math.ceil(shop_module.SELL_PRICE_FACTOR
                                 * (price[0] + price[1] * shop_module.EXCHANGE_RATE)))
        runner.equal(state.credits - before, expected, "the refund the client shows")
        diff = diffs[-1] if diffs else {}
        vehicles = diff.get("inventory", {}).get(items_module.ITEM_TYPES["vehicle"], {})
        runner.check(vehicles.get("compDescr", {}).get(inv_id) is None,
                     "the client is told to drop the vehicle")
        for tankman_id in crew:
            runner.check(tankman_id not in state.tankmen, "the crew left with it")
        bridge.close()
    finally:
        server.stop()


def test_state_survives_a_purchase(runner: common.Runner) -> None:
    """What was bought is still there after the server reloads the account."""
    state, server, bridge = _socket_pair(runner)
    try:
        compact, credits, gold = _prices_of(state, SHELL)[0]
        bridge.command(13, cmd.CMD_BUY_ITEM, (state.revision, compact, 3, 0))
        payload = state.to_json()
        reloaded = AccountState.from_json(payload, state.catalog,
                                          tankman_factory=state.tankman_factory)
        runner.equal(reloaded.item_count(SHELL, compact), 3, "the shells are saved")
        bridge.close()
    finally:
        server.stop()


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
