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
from server.game import goodies as goodies_module  # noqa: E402
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


def _premium_socket_pair(runner):
    """``(state, server, bridge)`` of a running server whose garage is premium.

    A trade-in needs a premium vehicle on both sides of the deal
    (``Vehicle.canTradeIn``/``canTradeOff``), and the default garage of an
    account holds the researchable tanks only.
    """
    state = common.new_account(runner.client, garage_mode="premium")
    server = AccountServer(state, port=0, save_path=None).start()
    bridge = Bridge(server.port)
    bridge.hello()
    return state, server, bridge


def _owned_premium(state, max_level=10):
    """``(entry, type)`` of the cheapest premium vehicle the account owns."""
    owned = []
    for entry in state.vehicles.values():
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        if vehicle is not None and vehicle.is_premium and vehicle.level <= max_level:
            owned.append((entry, vehicle))
    if not owned:
        raise common.Skip("the account owns no premium vehicle")
    return sorted(owned, key=lambda pair: (pair[1].level, pair[0].inv_id))[0]


def _premium_to_buy(state):
    """A premium vehicle of the catalogue the account does not own yet."""
    for vehicle in sorted(state.catalog.vehicles.all(),
                          key=lambda item: (item.level, item.compact_descr)):
        if (vehicle.is_premium and vehicle.price_gold > 0
                and vehicle.compact_descr not in state.owned_vehicle_comp_descrs()):
            return vehicle
    raise common.Skip("every premium vehicle is already owned")


def test_the_trade_in_config_is_the_clients(runner: common.Runner) -> None:
    """The shop carries a ``tradeIn`` the client's ``TradeInData`` accepts."""
    state, server, bridge = _premium_socket_pair(runner)
    try:
        response, diffs, actions = bridge.command(2, cmd.CMD_SYNC_SHOP, (0, 0, 0))
        runner.equal(response["code"], cmd.RES_STREAM, "the shop sync answer")
        shop = py2_loads(zlib.decompress(bridge.recv()["data"]))
        config = shop["tradeIn"]
        # ``TradeInData(**tradeIn)`` unpacks the dictionary into a named tuple,
        # so an extra or missing key would break the client's own shop.
        runner.equal(sorted(config), sorted(shop_module.TRADE_IN),
                     "exactly the keys the client's TradeInData declares")
        runner.equal(config["sellPriceFactor"], shop_module.TRADE_IN["sellPriceFactor"],
                     "the discount factor")
        runner.check(config["sellPriceFactor"] > 0, "the trade-in is enabled")
        runner.check(len(config["allowedVehicleLevels"]) >= 5,
                     "the levels a trade-in is allowed on")
        runner.equal((shop.get("defaults") or {}).get("tradeIn"), config,
                     "the fallback table carries it too")
        bridge.close()
    finally:
        server.stop()


def test_trading_a_premium_vehicle_off(runner: common.Runner) -> None:
    """``CMD_VEHICLE_TRADE_IN`` pays the difference and swaps the vehicles."""
    state, server, bridge = _premium_socket_pair(runner)
    try:
        old_entry, old_vehicle = _owned_premium(state)
        new_vehicle = _premium_to_buy(state)
        crew = [tankman_id for tankman_id in old_entry.crew if tankman_id not in (-1, None)]
        discount = int(math.ceil(shop_module.TRADE_IN["sellPriceFactor"]
                                 * old_vehicle.price_gold))
        berths = state.free_berths()
        wallet = (state.credits, state.gold)
        devices_before = dict(state.items.get(OPTIONAL_DEVICE, {}))

        response, diffs, actions = bridge.command(
            3, cmd.CMD_VEHICLE_TRADE_IN,
            ([state.revision, old_entry.inv_id, new_vehicle.compact_descr, 0, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the trade-in is accepted")
        ext = py2_loads(response["ext"]) if response["ext"] else {}
        inv_id = ext.get("vehInvID")
        runner.check(inv_id in state.vehicles, "the new vehicle is in the garage")
        runner.equal(state.vehicles[inv_id].compact_descr, new_vehicle.compact_descr,
                     "the garage entry is the vehicle that was bought")
        runner.check(old_entry.inv_id not in state.vehicles,
                     "the traded-off vehicle left the garage")
        # ``VehicleTradeInProcessor`` charges the buy price minus the old
        # vehicle's trade-off price.
        expected_gold = max(0, int(new_vehicle.price_gold) - discount)
        runner.equal(wallet[0] - state.credits, int(new_vehicle.price_credits),
                     "the credits the client showed")
        runner.equal(wallet[1] - state.gold, expected_gold, "the gold after the discount")
        # ``BarracksSlotsValidator`` moves the crew of the old vehicle into the
        # barracks, so the berths it asked for are really taken.
        for tankman_id in crew:
            runner.equal(state.tankmen[tankman_id].vehicle_inv_id, -1,
                         "the crew member is back in the barracks")
        runner.equal(state.free_berths(), berths - len(crew), "the berths it asked for")
        # Everything the traded-off vehicle carried is in the depot, not gone.
        runner.check(len(state.items.get(OPTIONAL_DEVICE, {})) >= len(devices_before),
                     "the devices of the old vehicle came back")
        diff = diffs[-1] if diffs else {}
        vehicles = diff.get("inventory", {}).get(items_module.ITEM_TYPES["vehicle"], {})
        runner.check(vehicles.get("compDescr", {}).get(old_entry.inv_id) is None,
                     "the client is told to drop the old vehicle")
        runner.check(inv_id in vehicles.get("compDescr", {}),
                     "and to add the new one")
        runner.check("economics" in diff, "the wallet travels with the diff")
        bridge.close()
    finally:
        server.stop()


def test_a_trade_in_needs_a_free_berth(runner: common.Runner) -> None:
    """The crew of the traded-off vehicle has to fit into the barracks."""
    state, server, bridge = _premium_socket_pair(runner)
    try:
        old_entry, old_vehicle = _owned_premium(state)
        new_vehicle = _premium_to_buy(state)
        state.berths = 0
        state.vehicles[old_entry.inv_id].crew = [tankman_id for tankman_id in old_entry.crew
                                                 if tankman_id not in (-1, None)]
        for tankman_id in state.vehicles[old_entry.inv_id].crew:
            state.tankmen[tankman_id].vehicle_inv_id = old_entry.inv_id
        state.berths = 0
        runner.check(state.free_berths() == 0, "the barracks are full")
        wallet = (state.credits, state.gold)
        response, diffs, actions = bridge.command(
            4, cmd.CMD_VEHICLE_TRADE_IN,
            ([state.revision, old_entry.inv_id, new_vehicle.compact_descr, 0, 0],))
        runner.equal(response["code"], cmd.RES_FAILURE, "the trade-in is refused")
        runner.equal(response["error"], "not_enough_space", "the client's own error key")
        runner.equal((state.credits, state.gold), wallet, "nothing was paid")
        runner.check(old_entry.inv_id in state.vehicles, "the old vehicle is still there")
        bridge.close()
    finally:
        server.stop()


def test_only_premium_vehicles_are_traded(runner: common.Runner) -> None:
    """The client's own two validators, on the server side."""
    state, server, bridge = _premium_socket_pair(runner)
    try:
        # A premium garage holds premium vehicles only; a trade-in validator
        # is easiest to test with a researchable tank standing next to them.
        researchable = next((vehicle for vehicle in state.catalog.vehicles.all()
                             if not vehicle.is_premium and vehicle.compact_descr not in
                             state.owned_vehicle_comp_descrs()), None)
        if researchable is None:
            raise common.Skip("the catalogue holds no researchable vehicle")
        state.add_vehicle(researchable, crew=False, shells=False)
        new_vehicle = _premium_to_buy(state)
        entry = next(entry for entry in state.vehicles.values()
                     if entry.compact_descr == researchable.compact_descr)
        response, diffs, actions = bridge.command(
            5, cmd.CMD_VEHICLE_TRADE_IN,
            ([state.revision, entry.inv_id, new_vehicle.compact_descr, 0, 0],))
        runner.equal(response["error"], "vehicle_cannot_trade_off",
                     "a tank that cannot be traded off is refused")

        old_entry, _old_vehicle = _owned_premium(state)
        response, diffs, actions = bridge.command(
            6, cmd.CMD_VEHICLE_TRADE_IN,
            ([state.revision, old_entry.inv_id, researchable.compact_descr, 0, 0],))
        runner.equal(response["error"], "vehicle_cannot_trade_in",
                     "and so is one that cannot be traded in")
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


def _shop_payload(bridge) -> dict:
    """The streamed shop cache, unpacked the way ``Shop`` receives it."""
    response, _diffs, _actions = bridge.command(1, cmd.CMD_SYNC_SHOP, (0, 0, 0))
    if response["code"] != cmd.RES_STREAM:
        raise AssertionError("the shop sync is not a stream: %r" % (response,))
    return py2_loads(zlib.decompress(bridge.recv()["data"]))


def _cheapest_offer(state):
    """``(level, goodieID, definition)`` of the cheapest offer of the account.

    A certificate is always of *one* level - its id comes from that level's own
    range (``variadic_discounts.xml``) and ``NYSelectDiscount`` hands back both
    the id of the offer and the id of the certificate.  The client lists only
    the vehicles its owner does not have yet (``NYApplyDiscountOnVehiclePopup``
    filters with ``~REQ_CRITERIA.INVENTORY``), so the test picks the cheapest
    vehicle of the table that is still for sale - a tier 8 premium is cheaper
    than a tier 10, and the garage of the test account is full of tier 10s.
    """
    owned = set(state.owned_vehicle_comp_descrs())
    levels = {}
    for token, descriptor in state.catalog.new_year.variadic_discounts.items():
        levels[descriptor.goodies_range] = int(token.rsplit(":", 1)[1])
    best = None
    for goodie_id, definition in sorted(state.goodie_table.definitions.items()):
        if definition.target_value in owned:
            continue
        vehicle = state.catalog.vehicles.get(definition.target_value)
        if vehicle is None:
            continue
        money = (int(vehicle.price_gold), int(vehicle.price_credits))
        if best is None or money < best[0]:
            best = (money, goodie_id, definition)
    if best is None:
        raise common.Skip("the account owns every vehicle the shop sells")
    _money, goodie_id, definition = best
    for (low, high), level in levels.items():
        if low <= goodie_id <= high:
            return level, goodie_id, definition
    raise common.Skip("the offer %s is outside every certificate range" % (goodie_id,))


def test_the_shop_carries_the_legacy_appearance_tables(runner: common.Runner) -> None:
    """The six legacy tables the appearance requesters index without guarding."""
    state, server, bridge = _socket_pair(runner)
    try:
        shop = _shop_payload(bridge)
        items = shop["items"]
        # ``ShopRequester.getCamouflagesPriceFactors(vehTypeCD)`` does
        # ``self.getValue('camouflagePriceFactors')[...][nationID]``, so the
        # arrays have to carry every nation id - and the values are the
        # installation's own ``<priceFactor>`` numbers.
        legacy = state.catalog.legacy_customization
        # The price factors are dictionaries and the hidden lists are sets - the
        # very shapes the client's own item data collects into
        # (``common/items/__init__``) and queries with ``[nationID]`` / ``in``.
        for key, kind in (("camouflagePriceFactors", dict),
                          ("notInShopCamouflages", set),
                          ("inscriptionGroupPriceFactors", dict),
                          ("notInShopInscriptionGroups", set)):
            value = items[key]
            runner.equal(len(value), shop_module.NATION_TABLE_SIZE,
                         "%s covers every nation id" % (key,))
            runner.check(all(isinstance(entry, kind) for entry in value),
                         "%s is one %s per nation" % (key, kind.__name__))
            runner.equal((shop.get("defaults") or {}).get("items", {}).get(key), value,
                         "the fallback table carries %s too" % (key,))
        for nation_id, hidden in sorted(legacy.not_in_shop_camouflages.items()):
            runner.equal(items["notInShopCamouflages"][nation_id], set(hidden),
                         "nation %s carries the installation's hidden camouflages" % (nation_id,))
            break
        runner.check(isinstance(items["playerEmblemGroupPriceFactors"], dict),
                     "the player emblems are keyed by group name")
        runner.check(isinstance(items["notInShopPlayerEmblemGroups"], set),
                     "the hidden emblems are a set of group names")
        runner.check(isinstance(items["notInShopItems"], set),
                     "``notInShopItems`` is the set the shop greys out")
        if legacy.camo_count:
            runner.equal(
                sum(len(table) for table in items["camouflagePriceFactors"]),
                legacy.camo_count,
                "every camouflage of the installation is priced")
            runner.check(items["notInShopItems"],
                         "the installation's own not-in-shop items travel")
            runner.check(
                any(table for table in items["notInShopCamouflages"]),
                "and so do the hidden camouflages")
        runner.check(isinstance(items["vehicleCamouflagePriceFactors"], dict),
                     "``vehicleCamouflagePriceFactors`` is keyed by vehicle")
        factors = items["vehicleCamouflagePriceFactors"]
        if factors:
            runner.equal(len(factors), len(state.catalog.vehicles.all()),
                         "every vehicle of the catalogue carries its factor")
            for compact, factor in sorted(factors.items())[:5]:
                vehicle = state.catalog.vehicles.get(compact)
                runner.check(vehicle is not None,
                             "a vehicle camouflage factor belongs to a real vehicle")
                runner.equal(factor, vehicle.camouflage_price_factor,
                             "and is the installation's own ``<camouflage><priceFactor>``")
        # ``ShopRequester.getAchievementReward`` reads this section and gives up
        # on ``isEnabled`` alone - which is what a server without bonds sends.
        reward = shop["achievementsReward"]
        runner.equal(sorted(reward), ["groups", "isEnabled", "medals"],
                     "the achievement reward carries the client's three keys")
        runner.equal(reward["isEnabled"], False, "bond rewards are disabled")
        runner.check(isinstance(reward["groups"], dict) and isinstance(reward["medals"], dict),
                     "and both tables are dictionaries a reader can index")
        bridge.close()
    finally:
        server.stop()


def test_the_shop_publishes_the_new_year_offers(runner: common.Runner) -> None:
    """The ``goodies`` section the discounts of the event are read from."""
    state, server, bridge = _socket_pair(runner)
    try:
        shop = _shop_payload(bridge)
        section = shop["goodies"]
        runner.equal(sorted(section), ["goodies", "notInShop", "prices"],
                     "the goodies section carries the three keys an account needs")
        definitions = section["goodies"]
        runner.check(definitions, "the event offers discounts")
        runner.check(all(isinstance(uid, int) for uid in definitions),
                     "the ids are ints (``ShopRequester.discounts`` looks them up by id)")
        runner.equal((shop.get("defaults") or {}).get("goodies", {}).get("goodies"),
                     definitions, "the fallback table carries the offers too")

        # The id band of every level and the vehicle the offer is for.
        bands = {int(token.rsplit(":", 1)[1]): descriptor.goodies_range
                 for token, descriptor in state.catalog.new_year.variadic_discounts.items()}
        runner.check(bands, "the installation describes the event's certificates")
        prices = state.catalog.vehicles.item_prices()
        for uid, definition in sorted(definitions.items()):
            runner.equal(len(definition), 9,
                         "``_NamedGoodieData`` unpacks nine values")
            variety, target, enabled, lifetime, useby, counter, autostart, condition, resource = definition
            runner.equal(variety, goodies_module.GOODIE_VARIETY_DISCOUNT, "a discount, not a booster")
            runner.equal(target[0], goodies_module.GOODIE_TARGET_TYPE_ON_BUY_VEHICLE,
                         "the discount is for a vehicle")
            runner.equal(enabled, True, "the offer is enabled")
            runner.equal(condition, None, "no condition: the level decides")
            vehicle = state.catalog.vehicles.get(target[1])
            runner.check(vehicle is not None, "the target is a vehicle of the catalogue")
            runner.check(target[1] in prices, "and is priced in the shop")
            # ``ShopRequester.__getPriceWithDiscount`` discounts the currency the
            # resource names and ``Vehicle.buyPrices`` compares that price with the
            # shop's own, so the resource has to be the currency of the vehicle.
            expected = (goodies_module.GOODIE_RESOURCE_TYPE_GOLD if vehicle.price_gold
                        else goodies_module.GOODIE_RESOURCE_TYPE_CREDITS)
            runner.equal(resource[0], expected,
                         "the offer is paid in the currency of the vehicle")
            runner.check(0 < resource[1] <= 100, "the discount is a percentage")
            runner.equal(resource[2], True, "and travels as one")
            level = [level for level, (low, high) in bands.items() if low <= uid <= high]
            runner.check(len(level) == 1, "the id of the offer is inside one level's band")
            runner.equal(level[0], int(vehicle.level),
                         "and inside the band of the vehicle's own level")
        bridge.close()
    finally:
        server.stop()


def test_the_wallet_reports_its_balance(runner: common.Runner) -> None:
    """``CMD_QUERY_BALANCE_INFO`` - the rows of the WGM balance tooltip."""
    state, server, bridge = _socket_pair(runner)
    try:
        response, diffs, actions = bridge.command(1, cmd.CMD_QUERY_BALANCE_INFO, ("",))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the balance is answered")
        rows = py2_loads(response["ext"]) if response["ext"] else None
        # ``WGMBalanceInfoRequester.__processResponse`` builds its result out of
        # these rows; a missing key would show up as ``-`` in the tooltip.
        received = {}
        for row in rows or ():
            runner.check(set(("currency_code", "class", "amount")) <= set(row),
                         "a row carries the three fields the requester reads")
            runner.check("is_paid" in row["class"], "and says whether it was paid for")
            currency = row["currency_code"]
            runner.check(currency in ("gold", "credits"), "in a currency the client knows")
            received[currency + ("_purchased" if row["class"]["is_paid"] else "_earned")] = row["amount"]
        runner.equal(sorted(received), ["credits_earned", "credits_purchased",
                                        "gold_earned", "gold_purchased"],
                     "every row the tooltip shows is answered")
        runner.equal(received["gold_earned"], state.gold, "the gold of the account")
        runner.equal(received["credits_earned"], state.credits, "the credits of the account")
        runner.equal(received["gold_purchased"] + received["credits_purchased"], 0,
                     "nothing was bought for money on a local server")
        bridge.close()
    finally:
        server.stop()


def test_a_personal_offer_discounts_a_vehicle(runner: common.Runner) -> None:
    """A claimed certificate lowers the price, and the buy uses it up."""
    state, server, bridge = _socket_pair(runner)
    try:
        level, goodie_id, definition = _cheapest_offer(state)
        token = goodies_module.discount_token(level)
        vehicle = state.catalog.vehicles.get(definition.target_value)
        runner.check(vehicle.compact_descr not in state.owned_vehicle_comp_descrs(),
                     "the offer is for a vehicle the account does not own")
        runner.equal(definition.value, goodies_module.percent_of_level(level),
                     "the offer carries the percent of its level")

        # The certificate is a *level token* the event itself hands out on a
        # level up (``_set_level``); the client reaches the level by decorating
        # the tree, and the DEV helper of the client's own debug panel adds one
        # directly - which is what the test uses to get there without one
        # thousand decorations.
        response, diffs, actions = bridge.command(
            1, cmd.CMD_NEW_YEAR_ADD_TOKEN_DEV, (token,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the certificate was handed out")
        runner.equal(state.new_year.state.variadic_discounts[token], 1,
                     "the account owns one certificate of that level")

        # ``NYSelectDiscount`` hands the id of the offer and the certificate id.
        response, diffs, actions = bridge.command(
            2, cmd.CMD_NEW_YEAR_SELECT_DISCOUNT,
            (goodie_id, token, vehicle.name, vehicle.compact_descr, definition.value))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the certificate is claimed")
        runner.check(goodie_id in state.new_year.state.goodies, "the offer is active")
        # ``ClientGoodies`` *replaces* its whole cache with this section, so it
        # travels as the ``{'goodies': {id: (state, finishTime, count)}}``
        # dictionary ``GoodiesRequester`` reads.
        runner.equal(diffs[-1].get(("goodies", "_r")),
                     {"goodies": {goodie_id: [goodies_module.GOODIE_STATE_ACTIVE, 0, 1]}},
                     "the client is told which offers the account has")
        runner.equal(state.goodie_for_vehicle(vehicle.compact_descr).uid, goodie_id,
                     "the server prices the vehicle with that very offer")

        # ``VehicleBuyer._getPrice`` is ``buyPrices.itemPrice.price`` - the price
        # of the offer whenever it is cheaper, which it always is.
        credits, gold = state.credits, state.gold
        expected_credits, expected_gold = definition.discounted_price(
            vehicle.price_credits, vehicle.price_gold)
        runner.check((expected_credits, expected_gold) != (vehicle.price_credits, vehicle.price_gold),
                     "the discounted price is lower than the shop price")
        response, diffs, actions = bridge.command(
            3, cmd.CMD_BUY_VEHICLE, ([state.revision, vehicle.compact_descr, 0, 0, -1],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the vehicle can be bought")
        runner.equal((state.credits, state.gold), (credits - expected_credits, gold - expected_gold),
                     "exactly the discounted price was charged")
        runner.equal((("goodies", "_r") in diffs[-1]), True,
                     "the used offer travels in the answer")
        runner.equal(diffs[-1][("goodies", "_r")], {"goodies": {}},
                     "and the client drops the discount with it")
        runner.equal(state.new_year.state.variadic_discounts[token], 0,
                     "the certificate is spent")
        runner.equal(goodie_id in state.new_year.state.goodies, False,
                     "the offer is used up")
        response, diffs, actions = bridge.command(
            4, cmd.CMD_BUY_VEHICLE, ([state.revision, vehicle.compact_descr, 0, 0, -1],))
        runner.equal(response["error"], "ALREADY_BOUGHT", "the tank is in the garage now")
        bridge.close()
    finally:
        server.stop()


def test_the_discount_math_is_the_clients(runner: common.Runner) -> None:
    """``getPriceWithDiscount`` of the client, on the definitions the server sends."""
    table = common.new_account(runner.client).goodie_table
    runner.check(table.definitions, "the event offers discounts")
    percent = goodies_module.GoodieDefinition(
        uid=1, variety=goodies_module.GOODIE_VARIETY_DISCOUNT, target=(6, 123, None),
        resource=(goodies_module.GOODIE_RESOURCE_TYPE_CREDITS, 25, True))
    runner.equal(percent.apply(1000), 750, "a percentage takes its share off")
    runner.equal(percent.apply(3), 2, "and rounds down like the client")
    runner.equal(percent.apply(0), 0, "a free item stays free")
    absolute = goodies_module.GoodieDefinition(
        uid=2, variety=goodies_module.GOODIE_VARIETY_DISCOUNT, target=(6, 123, None),
        resource=(goodies_module.GOODIE_RESOURCE_TYPE_CREDITS, 200, False))
    runner.equal(absolute.apply(1000), 800, "an absolute discount is subtracted")
    runner.equal(absolute.apply(150), 0, "and never turns a price negative")
    runner.equal(percent.discounted_price(1000, 5), (750, 5),
                 "the credits offer leaves the gold untouched")
    gold = goodies_module.GoodieDefinition(
        uid=3, variety=goodies_module.GOODIE_VARIETY_DISCOUNT, target=(6, 123, None),
        resource=(goodies_module.GOODIE_RESOURCE_TYPE_GOLD, 50, True))
    runner.equal(gold.discounted_price(1000, 800), (1000, 400),
                 "the gold offer is the one of a gold priced vehicle")
    runner.equal(percent.as_tuple(),
                 (0, (6, 123, None), True, None, None, 0, False, None, (20, 25, True)),
                 "the wire shape is the one ``_NamedGoodieData`` unpacks")
    runner.equal(table.shop_section()["prices"], {},
                 "the offers of the event are earned, not sold")
    for uid, definition in sorted(table.definitions.items()):
        runner.check(definition.is_vehicle_discount(), "offer %s is a vehicle discount" % uid)
        credits, gold = definition.discounted_price(1000000, 1000000)
        runner.check(credits <= 1000000 and gold <= 1000000,
                     "offer %s never raises a price" % uid)


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
