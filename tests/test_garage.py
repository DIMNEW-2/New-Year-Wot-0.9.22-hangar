"""The garage: modules, ammunition, equipment, devices and crew.

Every command here is written by the client's own helpers
(``client/account_helpers/Inventory.py``), and what an operation must produce is
what the client's caches read from the answer:

* the descriptor in ``inventory[1]['compDescr']`` (the hangar rebuilds the 3D
  model from it after every fitting command),
* ``shells``/``shellsLayout`` for the ammunition panel,
* ``eqs``/``eqsLayout`` for the consumables panel,
* ``crew`` as one *list* per vehicle, ``None`` for an empty seat
  (``Vehicle._buildCrew`` looks every non-``None`` entry up in ``inventory[8]``),
* the depot counts the panels price against.

The tests drive the same socket the mod uses, so the argument shapes are the
ones the client really writes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc import garage as garage_module  # noqa: E402
from server.account_rpc.server import AccountServer  # noqa: E402
from server.game import items as items_module  # noqa: E402
from server.game import shop as shop_module  # noqa: E402
from test_local_server import Bridge  # noqa: E402

VEHICLE = items_module.ITEM_TYPES["vehicle"]
GUN = items_module.ITEM_TYPES["vehicleGun"]
OPTIONAL_DEVICE = items_module.ITEM_TYPES["optionalDevice"]
SHELL = items_module.ITEM_TYPES["shell"]
EQUIPMENT = items_module.ITEM_TYPES["equipment"]
TANKMAN = items_module.ITEM_TYPES["tankman"]

_REQUEST_ID = [0]


def _next_request() -> int:
    _REQUEST_ID[0] += 1
    return _REQUEST_ID[0]


def _socket_pair(runner):
    """``(state, server, bridge)`` of a running local server."""
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    bridge = Bridge(server.port)
    bridge.hello()
    return state, server, bridge


def _not_owned(state, compact_descr: int) -> bool:
    return state.item_count(items_module.get_type_of_compact_descr(compact_descr),
                            compact_descr) == 0


def _buy(bridge, state, compact: int, count: int = 1):
    """Buy an item into the depot and return the answer."""
    return bridge.command(_next_request(), cmd.CMD_BUY_ITEM,
                          (state.revision, compact, count, 0))


def _vehicle_with_two_guns(state):
    """``(entry, vehicle_type, spare_gun)`` of a tank whose turret takes two guns."""
    for entry in sorted(state.vehicles.values(), key=lambda item: item.inv_id):
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        if vehicle is None:
            continue
        group = vehicle.find_turret_group(entry.turrets[0][0])
        if group is None or len(group.guns) < 2:
            continue
        current = int(entry.turrets[0][1])
        for gun in group.guns:
            if gun.id != current and _not_owned(state, gun.compact_descr):
                return entry, vehicle, gun
    raise common.Skip("no vehicle with a spare gun in the catalogue")


def _device(state, removable: bool, vehicle_type=None):
    """A ``(compactDescr, id)`` of an optional device the vehicle may carry."""
    tags = set(vehicle_type.tags or ()) if vehicle_type is not None else None
    for compact, device in state.catalog.devices.items():
        if bool(device.get("removable", True)) != removable:
            continue
        included = device.get("includeTags")
        excluded = device.get("excludeTags")
        if tags is not None:
            # The same filter the server applies: an include list requires one
            # of its tags, an exclude list refuses the vehicle.
            if included and not tags & set(included):
                continue
            if excluded and tags & set(excluded):
                continue
        elif included or excluded:
            continue
        return compact, compact >> 8 & 0xFF
    raise common.Skip("no optional device of that kind in the catalogue")


def _current_key(state, entry):
    vehicle = state.catalog.vehicles.get(entry.compact_descr)
    gun = vehicle.find_gun(entry.turrets[0][1])
    turret = vehicle.find_turret_group(entry.turrets[0][0]).turret
    return (turret.compact_descr, gun.compact_descr)


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------
def test_equip_module(runner: common.Runner) -> None:
    """``CMD_EQUIP`` mounts a gun, consumes it from the depot and returns the old one."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry, vehicle, gun = _vehicle_with_two_guns(state)
        old_gun_compact = None
        for candidate in vehicle.find_turret_group(entry.turrets[0][0]).guns:
            if candidate.id == entry.turrets[0][1]:
                old_gun_compact = candidate.compact_descr
        before_descr = entry.in_battle_descr(vehicle)
        price = state.price_of(gun.compact_descr)
        runner.check(price is not None, "the gun has a shop price")
        wallet = (state.credits, state.gold)
        response, diffs, actions = _buy(bridge, state, gun.compact_descr)
        runner.equal(response["code"], cmd.RES_SUCCESS, "the gun is bought")
        runner.equal(state.item_count(GUN, gun.compact_descr), 1, "the depot holds it")
        if price[1]:
            runner.equal(wallet[1] - state.gold, price[1], "the gold it costs")
        else:
            runner.equal(wallet[0] - state.credits, price[0], "the credits it costs")
        wallet = (state.credits, state.gold)

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP, (entry.inv_id, gun.compact_descr, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the gun is mounted")
        runner.equal(entry.turrets[0][1], gun.id, "the descriptor carries the new gun")
        runner.equal(state.item_count(GUN, gun.compact_descr), 0, "the depot gave it up")
        runner.equal(state.item_count(GUN, old_gun_compact), 1, "the old gun is spare")
        runner.equal((state.credits, state.gold), wallet, "mounting is free")
        new_descr = entry.in_battle_descr(vehicle)
        runner.check(new_descr != before_descr, "the descriptor changed")

        diff = diffs[-1] if diffs else {}
        inventory = diff.get("inventory", {}).get(VEHICLE, {})
        runner.equal(inventory.get("compDescr", {}).get(entry.inv_id), new_descr,
                     "the diff carries the new descriptor")
        runner.equal(diff.get("inventory", {}).get(GUN, {}).get(gun.compact_descr), 0,
                     "the diff drops the mounted gun from the depot")
        bridge.close()
    finally:
        server.stop()


def test_equip_unknown_module_is_refused(runner: common.Runner) -> None:
    """A module the vehicle cannot take is refused and changes nothing."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        before = [list(pair) for pair in entry.turrets]
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP, (entry.inv_id, 0x01020002, 0))
        runner.check(response["code"] < 0, "the answer is a failure (%s)" % response["code"])
        runner.check(response["error"], "the refusal carries a message key")
        runner.equal([list(pair) for pair in entry.turrets], before,
                     "the descriptor is untouched")
        bridge.close()
    finally:
        server.stop()


def test_equipping_a_gun_empties_the_loaded_ammunition(runner: common.Runner) -> None:
    """The shells of the old gun go back to the depot, the new layout starts empty."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry, vehicle, gun = _vehicle_with_two_guns(state)
        loaded = list(entry.shells)
        runner.check(loaded, "the vehicle carries shells")
        _buy(bridge, state, gun.compact_descr)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP, (entry.inv_id, gun.compact_descr, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the gun is mounted")
        runner.equal(entry.shells, [], "nothing is loaded in the new gun")
        for index in range(0, len(loaded), 2):
            compact, count = int(loaded[index]), int(loaded[index + 1])
            runner.equal(state.item_count(SHELL, compact), count,
                         "the shells of the old layout are back in the depot")
        key = _current_key(state, entry)
        runner.equal(entry.shells_layout.get(key), [],
                     "the client sees an empty layout for the new gun")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# optional devices
# ---------------------------------------------------------------------------
def test_optional_devices_lifecycle(runner: common.Runner) -> None:
    """A removable device is mounted, taken off, and only ever exists once."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        compact, _device_id = _device(state, removable=True, vehicle_type=vehicle)
        before = entry.in_battle_descr(vehicle)
        _buy(bridge, state, compact)

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_OPTDEV,
            ([state.shop_payload()["rev"], entry.inv_id, compact, 0, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the device is mounted")
        runner.equal(state.item_count(OPTIONAL_DEVICE, compact), 0, "the depot gave it up")
        runner.check(entry.devices[0] is not None, "the device is in the descriptor")
        runner.check(entry.in_battle_descr(vehicle) != before,
                     "the descriptor changed")

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_OPTDEV,
            ([state.shop_payload()["rev"], entry.inv_id, 0, 0, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the device comes off")
        runner.equal(entry.devices[0], None, "the slot is empty")
        runner.equal(state.item_count(OPTIONAL_DEVICE, compact), 1, "the device is spare")
        bridge.close()
    finally:
        server.stop()


def test_non_removable_device_costs_gold_to_keep(runner: common.Runner) -> None:
    """A ``<removable>false</removable>`` device is destroyed for free and kept for gold."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        compact, _device_id = _device(state, removable=False, vehicle_type=vehicle)
        cost = dict(state.shop_payload().get("paidRemovalCost") or {})
        gold = int(cost.get("gold", 0) or 0)
        runner.check(gold > 0, "the shop publishes a removal price")
        _buy(bridge, state, compact)
        revision = state.shop_payload()["rev"]

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_OPTDEV, ([revision, entry.inv_id, compact, 1, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the device is mounted")

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_OPTDEV, ([revision, entry.inv_id, 0, 1, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "it can be destroyed")
        runner.equal(state.item_count(OPTIONAL_DEVICE, compact), 0, "destroying loses it")

        _buy(bridge, state, compact)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_OPTDEV, ([revision, entry.inv_id, compact, 1, 0],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the device is mounted again")
        wallet = state.gold
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_OPTDEV, ([revision, entry.inv_id, 0, 1, 1],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "it can be taken off for gold")
        runner.equal(state.item_count(OPTIONAL_DEVICE, compact), 1, "the depot keeps it")
        runner.equal(wallet - state.gold, gold, "the paid removal costs gold")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# ammunition and consumables
# ---------------------------------------------------------------------------
def test_set_and_fill_layouts(runner: common.Runner) -> None:
    """``CMD_SET_AND_FILL_LAYOUTS`` saves the layout and buys what is missing."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        gun = vehicle.find_gun(entry.turrets[0][1])
        layout = []
        wanted = []
        for name, _portion in gun.shots:
            shell = state.catalog.vehicles.shell(vehicle.nation_id, name)
            layout.extend((shell.compact_descr, 5))
            wanted.append((shell.compact_descr, 5))
        before = (state.credits, state.gold)
        revision = state.shop_payload()["rev"]
        args = [revision, entry.inv_id, len(layout)] + layout + [0, 0]
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_SET_AND_FILL_LAYOUTS, (args,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the layout is accepted")
        runner.equal(entry.shells, layout, "the vehicle carries the new layout")

        from server.proto import py2_loads
        ext = py2_loads(response["ext"]) if response.get("ext") else {}
        paid_credits = paid_gold = 0
        for compact, price, count in ext.get("shells", []):
            runner.equal(state.item_count(SHELL, compact), 0,
                         "what was bought went straight into the vehicle")
            paid_credits += price[0] * count
            paid_gold += price[1] * count
        runner.equal(before[0] - state.credits, paid_credits, "the credits that were paid")
        runner.equal(before[1] - state.gold, paid_gold, "the gold that was paid")
        runner.check(entry.shells_layout.get(_current_key(state, entry)) == layout,
                     "the layout is kept per (turret, gun)")

        diff = diffs[-1] if diffs else {}
        vehicles = diff.get("inventory", {}).get(VEHICLE, {})
        runner.equal(vehicles.get("shells", {}).get(entry.inv_id), layout,
                     "the diff publishes the loaded shells")
        runner.equal(vehicles.get("shellsLayout", {}).get(entry.inv_id, {}),
                     {_current_key(state, entry): layout},
                     "the diff publishes the layout table")

        # The same layout again costs nothing: everything is loaded already.
        wallet = (state.credits, state.gold)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_SET_AND_FILL_LAYOUTS, (args,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "filling twice is accepted")
        runner.equal((state.credits, state.gold), wallet, "and costs nothing")
        runner.equal(entry.shells, layout, "the layout is unchanged")
        bridge.close()
    finally:
        server.stop()


def test_layouts_without_ammunition(runner: common.Runner) -> None:
    """An empty layout empties the vehicle and refills the depot."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        loaded = list(entry.shells)
        runner.check(loaded, "the vehicle carries shells")
        args = [state.shop_payload()["rev"], entry.inv_id, 0, 0, 0]
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_SET_AND_FILL_LAYOUTS, (args,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "an empty layout is accepted")
        runner.equal(entry.shells, [], "nothing is loaded any more")
        for index in range(0, len(loaded), 2):
            compact, count = int(loaded[index]), int(loaded[index + 1])
            runner.equal(state.item_count(SHELL, compact), count,
                         "the shells are back in the depot")
        bridge.close()
    finally:
        server.stop()


def test_equipment_layout(runner: common.Runner) -> None:
    """Consumables are mounted from the depot, bought when missing, kept in four slots."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        prices = []
        for compact, price in state.shop_payload()["items"]["itemPrices"].items():
            if items_module.get_type_of_compact_descr(compact) != EQUIPMENT:
                continue
            if price[0] <= 0 and price[1] <= 0:
                continue
            prices.append((compact, price))
        prices.sort(key=lambda item: (item[1][0] + item[1][1] * 1000, item[0]))
        if len(prices) < 2:
            raise common.Skip("the shop prices fewer than two consumables")
        (owned, owned_price), (bought, bought_price) = prices[0], prices[1]
        _buy(bridge, state, owned)
        runner.equal(state.item_count(EQUIPMENT, owned), 1, "one consumable is spare")

        layout = [owned, 1, bought, 1, 0, 1, 0, 0]
        before = (state.credits, state.gold)
        rider = entry.equipment[3]
        args = [state.shop_payload()["rev"], entry.inv_id, 0, 0, len(layout)] + layout
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_SET_AND_FILL_LAYOUTS, (args,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the layout is accepted")
        runner.equal(entry.equipment[:3], [owned, bought, 0], "the three slots")
        runner.equal(entry.equipment[3], rider, "the battle booster slot is untouched")
        runner.equal(state.item_count(EQUIPMENT, owned), 0, "the spare one was used")
        runner.equal(state.item_count(EQUIPMENT, bought), 0, "the missing one was bought")
        credits = bought_price[0]
        gold = bought_price[1]
        runner.equal(before[0] - state.credits, credits, "the credits that were paid")
        runner.equal(before[1] - state.gold, gold, "the gold that was paid")

        diff = diffs[-1] if diffs else {}
        eqs = diff.get("inventory", {}).get(VEHICLE, {}).get("eqs", {}).get(entry.inv_id)
        runner.equal(eqs, entry.equipment, "the diff publishes the layout")
        runner.equal(len(eqs), 4, "four slots, as VehicleEquipment expects")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# crew
# ---------------------------------------------------------------------------
def test_equip_tankman_moves_crew_between_seats(runner: common.Runner) -> None:
    """``CMD_EQUIP_TMAN`` empties a seat, fills it again and reports both vehicles."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        roles = state.crew_roles_of(entry)
        runner.check(len(roles) >= 2, "the vehicle has at least two seats")
        moved = entry.crew[1]
        runner.check(moved, "the second seat is taken")

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_TMAN, (entry.inv_id, 1, -1))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the seat is emptied")
        runner.equal(entry.crew[1], None, "the seat is empty, and empty is ``None``")
        runner.equal(state.tankmen[moved].vehicle_inv_id, -1, "the crew member is free")

        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_TMAN, (entry.inv_id, 1, moved))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the seat is filled again")
        runner.equal(entry.crew[1], moved, "the crew member is back")
        runner.equal(state.tankmen[moved].vehicle_inv_id, entry.inv_id,
                     "and is assigned to the vehicle")
        diff = diffs[-1] if diffs else {}
        tankmen = diff.get("inventory", {}).get(TANKMAN, {})
        runner.check(moved in tankmen.get("vehicle", {}),
                     "the diff publishes the crew member")
        crew = diff.get("inventory", {}).get(VEHICLE, {}).get("crew", {})
        runner.equal(crew.get(entry.inv_id)[1], moved, "and the vehicle's crew list")
        bridge.close()
    finally:
        server.stop()


def test_buy_and_equip_tankman(runner: common.Runner) -> None:
    """``CMD_BUY_AND_EQUIP_TMAN`` recruits into the seat and charges the crew cost."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EQUIP_TMAN, (entry.inv_id, 0, -1))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the first seat is emptied")
        wallet = (state.credits, state.gold)
        known = len(state.tankmen)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_BUY_AND_EQUIP_TMAN,
            (state.revision, entry.inv_id, 0, 1))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the crew member is recruited")
        from server.proto import py2_loads
        ext = py2_loads(response["ext"]) if response.get("ext") else {}
        runner.check(ext.get("tmanInvID") in state.tankmen, "ext names the new crew member")
        runner.check(isinstance(ext.get("tmanCompDescr"), bytes),
                     "ext carries the passport")
        runner.equal(len(state.tankmen), known + 1, "one more crew member")
        runner.equal(entry.crew[0], ext["tmanInvID"], "it sits in the seat")
        credits, gold = state.shop_payload()["tankmanCost"][1]["credits"], \
            state.shop_payload()["tankmanCost"][1]["gold"]
        runner.equal(wallet[0] - state.credits, credits, "the credits that were paid")
        runner.equal(wallet[1] - state.gold, gold, "the gold that was paid")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# repair, settings and the unchanged sections
# ---------------------------------------------------------------------------
def test_repair_and_settings(runner: common.Runner) -> None:
    """``CMD_REPAIR`` pays the bill, ``CMD_VEH_SETTINGS`` flips one flag."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        entry.repair = (500, 0)
        wallet = state.credits
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_REPAIR, (entry.inv_id, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the vehicle is repaired")
        runner.equal(entry.repair[0], 0, "nothing is owed any more")
        runner.equal(wallet - state.credits, 500, "the repair bill was paid")
        repair = diffs[-1]["inventory"][VEHICLE]["repair"][entry.inv_id]
        runner.equal(tuple(repair), (0, 0), "the diff publishes the cleared bill")

        flag = garage_module.VEHICLE_SETTINGS_FLAG["AUTO_REPAIR"]
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_VEH_SETTINGS, (entry.inv_id, flag, 1))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the setting is stored")
        runner.check(entry.settings & flag, "the flag is set")
        settings = diffs[-1]["inventory"][VEHICLE]["settings"][entry.inv_id]
        runner.equal(settings, entry.settings, "the diff publishes the settings")
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_VEH_SETTINGS, (entry.inv_id, flag, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the setting is cleared")
        runner.check(not entry.settings & flag, "the flag is gone")
        bridge.close()
    finally:
        server.stop()


def test_inventory_sections_keep_their_expensive_shapes(runner: common.Runner) -> None:
    """The shapes the client indexes without a guard (``Vehicle``, ``Inventory``)."""
    state, server, bridge = _socket_pair(runner)
    try:
        response, diffs, actions = bridge.command(_next_request(), cmd.CMD_SYNC_DATA, (0, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the sync is answered")
        from server.proto import py2_loads
        diff = py2_loads(response["ext"]) if response.get("ext") else {}
        vehicles = diff["inventory"][VEHICLE]
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        inv_id = entry.inv_id
        roles = state.crew_roles_of(entry)
        runner.equal(len(vehicles["crew"][inv_id]), len(roles), "one crew entry per seat")
        for value in vehicles["crew"][inv_id]:
            runner.check(value is None or isinstance(value, int), "a seat or a crew member")
        for name in ("eqs", "eqsLayout"):
            runner.equal(len(vehicles[name][inv_id]), 4, "%s has four slots" % name)
        runner.check(isinstance(vehicles["lock"][inv_id], tuple), "the lock is a pair")
        runner.equal(len(vehicles["lock"][inv_id]), 2, "lock is (reason, time)")
        runner.check(isinstance(vehicles["repair"][inv_id], tuple), "repair is a pair")
        runner.check(isinstance(vehicles["compDescr"][inv_id], bytes),
                     "the descriptor is a byte string")
        key = _current_key(state, entry)
        runner.check(isinstance(vehicles["shellsLayout"][inv_id], dict),
                     "the layout table is a dict")
        runner.check(all(isinstance(item, tuple) and len(item) == 2
                         for item in vehicles["shellsLayout"][inv_id]),
                     "keyed by (turret, gun)")
        runner.equal(vehicles["shells"][inv_id], entry.shells, "the loaded shells")
        runner.check(key in vehicles["shellsLayout"][inv_id],
                     "the stock layout of the vehicle is published")
        bridge.close()
    finally:
        server.stop()


def test_garage_survives_a_restart(runner: common.Runner) -> None:
    """Fitting, ammunition and devices come back from the saved account."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry, vehicle, gun = _vehicle_with_two_guns(state)
        _buy(bridge, state, gun.compact_descr)
        bridge.command(_next_request(), cmd.CMD_EQUIP, (entry.inv_id, gun.compact_descr, 0))
        compact, _device_id = _device(state, removable=True, vehicle_type=vehicle)
        _buy(bridge, state, compact)
        bridge.command(_next_request(), cmd.CMD_EQUIP_OPTDEV,
                       ([state.shop_payload()["rev"], entry.inv_id, compact, 2, 0],))
        layout = []
        new_gun = vehicle.find_gun(entry.turrets[0][1])
        for name, _portion in new_gun.shots:
            shell = state.catalog.vehicles.shell(vehicle.nation_id, name)
            layout.extend((shell.compact_descr, 2))
        args = [state.shop_payload()["rev"], entry.inv_id, len(layout)] + layout + [0, 0]
        bridge.command(_next_request(), cmd.CMD_SET_AND_FILL_LAYOUTS, (args,))

        from server.account_rpc.state import AccountState
        payload = state.to_json()
        reloaded = AccountState.from_json(payload, state.catalog,
                                          tankman_factory=state.tankman_factory)
        saved = reloaded.vehicles[entry.inv_id]
        runner.equal([list(pair) for pair in saved.turrets],
                     [list(pair) for pair in entry.turrets],
                     "the mounted gun is saved")
        runner.equal(saved.devices, entry.devices, "the mounted devices are saved")
        runner.equal(saved.shells, entry.shells, "the loaded shells are saved")
        runner.equal({tuple(key): value for key, value in saved.shells_layout.items()},
                     {tuple(key): value for key, value in entry.shells_layout.items()},
                     "the layouts are saved")
        runner.equal(saved.equipment, entry.equipment, "the consumables are saved")
        runner.equal(saved.crew, entry.crew, "the crew is saved")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# the invariants the client's own validator checks
# ---------------------------------------------------------------------------
def _client_split_devices(data: bytes, turret_positions: int):
    """``_splitVehicleCompactDescr`` + ``VehicleDescr.__init__`` of the client.

    The flags byte counts the slots from the *last* one upwards and the device
    ids follow packed from the last occupied slot backwards, so this is the only
    reading that decides which slot a mounted device ends up in.
    """
    import struct

    index = 10 + turret_positions * 4
    flags = data[index]
    index += 1
    occupied = [bit for bit in range(3) if flags & 1 << bit]
    devices = data[index:index + len(occupied) * 2]
    result = [None, None, None]
    position = 2
    bits = flags & 7
    while bits:
        if bits & 1:
            result[position] = struct.unpack("<H", devices[:2])[0]
            devices = devices[2:]
        bits >>= 1
        position -= 1
    return result


def test_optional_device_slots_match_the_client(runner: common.Runner) -> None:
    """A device mounted in slot *i* is read back from slot *i* by the client."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        compact, device_id = _device(state, removable=True, vehicle_type=vehicle)
        _buy(bridge, state, compact, 1)
        revision = state.shop_payload()["rev"]
        for slot in range(3):
            response, diffs, actions = bridge.command(
                _next_request(), cmd.CMD_EQUIP_OPTDEV,
                ([revision, entry.inv_id, compact, slot, 0],))
            runner.equal(response["code"], cmd.RES_SUCCESS,
                         "the device is mounted in slot %d" % slot)
            data = entry.in_battle_descr(vehicle)
            devices = _client_split_devices(data, len(vehicle.turrets_by_position()))
            runner.equal(devices[slot], device_id,
                         "the client reads the device from slot %d" % slot)
            # take it off again (it is the only one in the depot)
            response, diffs, actions = bridge.command(
                _next_request(), cmd.CMD_EQUIP_OPTDEV,
                ([revision, entry.inv_id, 0, slot, 0],))
            runner.equal(response["code"], cmd.RES_SUCCESS,
                         "the device comes off slot %d again" % slot)
        bridge.close()
    finally:
        server.stop()


def test_every_vehicle_descriptor_round_trips(runner: common.Runner) -> None:
    """The descriptor of every vehicle of the garage can be read back."""
    from server.game import vehicle_descr as descr_module

    state = common.new_account(runner.client)
    failures = []
    for entry in state.vehicles.values():
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        if vehicle is None:
            failures.append("vehicle %s has no catalogue entry" % entry.compact_descr)
            continue
        try:
            parsed = descr_module.parse_compact_descr(
                entry.in_battle_descr(vehicle), len(vehicle.turrets_by_position()))
        except Exception as error:  # noqa: BLE001 - the message is the report
            failures.append("%s: %s" % (vehicle.name, error))
            continue
        if parsed["nationID"] != vehicle.nation_id or parsed["vehicleTypeID"] != vehicle.innation_id:
            failures.append("%s: header mismatch" % vehicle.name)
        positions = sorted(vehicle.turrets_by_position())
        if [tuple(pair) for pair in parsed["turrets"]] != [tuple(pair) for pair in entry.turrets]:
            failures.append("%s: turrets %r != %r" % (vehicle.name, parsed["turrets"],
                                                      entry.turrets))
        if len(parsed["turrets"]) != len(positions):
            failures.append("%s: %d turret positions" % (vehicle.name, len(parsed["turrets"])))
    runner.check(not failures, "every descriptor round trips (%d problems: %s)"
                 % (len(failures), "; ".join(failures[:5])))


def test_the_client_validator_accepts_the_garage(runner: common.Runner) -> None:
    """The invariants ``AccountValidator`` raises on hold for the whole account."""
    state = common.new_account(runner.client)
    problems = []
    for entry in state.vehicles.values():
        vehicle = state.catalog.vehicles.get(entry.compact_descr)
        if vehicle is None:
            problems.append("unknown vehicle %s" % entry.compact_descr)
            continue
        crew = [value for value in entry.crew if value is not None]
        if len(crew) > len(vehicle.crew_roles):
            problems.append("%s carries %d crew for %d roles"
                            % (vehicle.name, len(crew), len(vehicle.crew_roles)))
        for index, device in enumerate(entry.devices or ()):
            if device is None:
                continue
            if not device and device != 0:
                continue
            compact = items_module.make_int_compact_descr_by_id("optionalDevice",
                                                                vehicle.nation_id, device)
            if compact not in state.catalog.devices:
                problems.append("%s has device %s that the catalogue does not know"
                                % (vehicle.name, device))
        # The grid of the descriptor is exactly the client's own layout.
        positions = len(vehicle.turrets_by_position())
        expected = 10 + positions * 4 + 1
        expected += 2 * len([value for value in (entry.devices or ()) if value is not None])
        if len(entry.in_battle_descr(vehicle)) != expected:
            problems.append("%s has a %d byte descriptor, expected %d"
                            % (vehicle.name, len(entry.in_battle_descr(vehicle)), expected))
    for item_type, items in state.items.items():
        for compact in items:
            if state.price_of(compact) is None:
                problems.append("depot item %s (type %s) is unknown to the shop"
                                % (compact, item_type))
    for compact in state.elite_vehicles:
        if state.catalog.vehicles.get(compact) is None:
            problems.append("elite vehicle %s is not in the catalogue" % compact)
    runner.check(not problems, "the account would pass AccountValidator "
                               "(%d problems: %s)" % (len(problems), "; ".join(problems[:5])))


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
