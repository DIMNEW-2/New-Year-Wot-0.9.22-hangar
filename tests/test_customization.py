"""The appearance items of the installation: camouflages, paints, decals.

The client prices and numbers them itself, so the server has to read exactly the
same data:

* the compact descriptor is
  ``makeIntCompactDescrByID('customizationItem', cType, itemId)``
  (``c11n_components.BaseCustomizationItem.makeIntDescr``), which is why the
  depot is read as ``inventory['customization'][ITEMS][cType][itemID]``,
* the price comes from the item's own ``<price>`` or, when it has none, from the
  price group it names (``customization/priceGroups/list.xml``),
* the season is a bit mask (``SeasonType``), the flags are ``notInShop`` and the
  tags of the ``vehicleBound``/``rare`` family.

The client's XML dialect is checked here as well: the customization files
declare their namespaces as *elements* and use the prefixes in element names,
which a strict parser refuses.  The account side of the same window - what
buying, selling, mounting and renting do to the depot, the outfits and the
wallet - is covered by the second half of the file.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import math

import common  # noqa: E402
from server import loader  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc import customization as customization_module  # noqa: E402
from server.account_rpc.requests import RequestDispatcher  # noqa: E402
from server.account_rpc.state import AccountState  # noqa: E402
from server.game import c11n_constants as cc  # noqa: E402
from server.game import c11n_outfit as outfitting  # noqa: E402
from server.game import customization as c11n  # noqa: E402
from server.game import defs, items as items_module, ny_constants as nyc  # noqa: E402

CUSTOMIZATION_ITEM = items_module.ITEM_TYPES["customizationItem"]

#: ``getAllItemsFromOutfit``: what an outfit needs from the depot.
outfit_module_items = outfitting.items_of_outfit


def _catalogue(runner) -> c11n.CustomizationCatalog:
    source = loader.open_client_defs(runner.client)
    catalogue = c11n.CustomizationCatalog.load(source)
    if not catalogue.count:
        raise common.Skip("the installation ships no customization items")
    return catalogue


# ---------------------------------------------------------------------------
# what the client writes
# ---------------------------------------------------------------------------
def test_every_kind_of_item_is_read_and_priced(runner: common.Runner) -> None:
    """Paints, camouflages and decals are all there and all priced."""
    catalogue = _catalogue(runner)
    for c_type in c11n.SIMPLE_TYPES:
        items = catalogue.items_of(c_type)
        runner.check(bool(items), "%s items were read (%d)" % (c11n.TYPE_NAMES[c_type],
                                                               len(items)))
        unpriced = [item.id for item in items if not item.priced]
        runner.check(not unpriced,
                     "every %s has a price (missing: %s)" % (c11n.TYPE_NAMES[c_type],
                                                             unpriced[:5]))


def test_the_price_group_decides_the_price(runner: common.Runner) -> None:
    """A camouflage without a price of its own takes its price group's."""
    catalogue = _catalogue(runner)
    item = catalogue.get(c11n.make_int_descr(c11n.TYPE_CAMOUFLAGE, 15300))
    if item is None:
        raise common.Skip("camouflage 15300 is not in this installation")
    runner.equal(item.group_name, "legacy camo china", "the price group of the item")
    runner.equal(item.price, (0, 50), "the price of the group (the file says 50 gold)")
    group = catalogue.groups["legacy camo china"]
    runner.equal(group.price, item.price, "the group and the item agree")
    runner.check(not item.not_in_shop, "and the item is sold in the shop")


def test_the_compact_descriptor_is_the_clients_own(runner: common.Runner) -> None:
    """``makeIntCompactDescrByID('customizationItem', cType, id)``."""
    catalogue = _catalogue(runner)
    checked = 0
    for item in catalogue.items.values():
        expected = (int(item.id) << 8) + CUSTOMIZATION_ITEM + (int(item.c_type) << 4)
        if item.compact_descr != expected:
            runner.check(False, "%s %s has compact %s, expected %s"
                         % (item.type_name, item.id, item.compact_descr, expected))
            return
        if items_module.get_type_of_compact_descr(item.compact_descr) != CUSTOMIZATION_ITEM:
            runner.check(False, "%s %s does not read back as a customization item"
                         % (item.type_name, item.id))
            return
        checked += 1
    runner.check(checked > 100, "every one of the %d items reads back" % checked)


def test_the_types_do_not_collide(runner: common.Runner) -> None:
    """The same id in two types is two different items."""
    catalogue = _catalogue(runner)
    seen = {}
    collisions = []
    for item in catalogue.items.values():
        key = (item.c_type, item.id)
        if key in seen:
            collisions.append(key)
        seen[key] = item
    runner.check(not collisions, "no duplicate (type, id): %s" % collisions[:5])
    same_id = [item for item in catalogue.items.values() if item.id == 128]
    if len(same_id) > 1:
        compacts = {item.compact_descr for item in same_id}
        runner.equal(len(compacts), len(same_id),
                     "id 128 in %d types has %d different compact descriptors"
                     % (len(same_id), len(compacts)))


def test_seasons_are_bit_masks(runner: common.Runner) -> None:
    """Every item is applicable in at least one season."""
    catalogue = _catalogue(runner)
    bad = ["%s %s" % (item.type_name, item.id)
           for item in catalogue.items.values() if not item.seasons]
    runner.check(not bad, "every item has a season (%s)" % bad[:5])
    seasons = {item.season for item in catalogue.items.values()}
    runner.check(seasons <= {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
                 "the seasons are the bits of SeasonType: %s" % sorted(seasons))
    all_items = [item for item in catalogue.items.values() if item.season == c11n.SEASON_ALL]
    runner.check(bool(all_items), "some items fit every season (%d)" % len(all_items))


def test_not_in_shop_items_are_marked(runner: common.Runner) -> None:
    """The ``legacy notInShop`` group is not sold."""
    catalogue = _catalogue(runner)
    marked = catalogue.not_in_shop()
    runner.check(bool(marked), "the installation has items the shop must not sell")
    for compact in marked[:20]:
        item = catalogue.items[compact]
        runner.check(item.not_in_shop, "%s %s is marked" % (item.type_name, item.id))
    if "legacy notInShop" in catalogue.groups:
        runner.check(catalogue.groups["legacy notInShop"].not_in_shop,
                     "and the price group itself says so")


def test_the_item_filter_is_read(runner: common.Runner) -> None:
    """The nations of a ``vehicleFilter`` are read as nation ids."""
    catalogue = _catalogue(runner)
    item = catalogue.get(c11n.make_int_descr(c11n.TYPE_CAMOUFLAGE, 15300))
    if item is None:
        raise common.Skip("camouflage 15300 is not in this installation")
    runner.equal(item.nations, (nyc.NATION_INDICES["china"],),
                 "the camouflage is filtered to China")
    with_filter = [value for value in catalogue.items.values() if value.nations]
    runner.check(len(with_filter) > 10, "%d items carry a nation filter" % len(with_filter))


def test_the_catalogue_round_trips_through_json(runner: common.Runner) -> None:
    """The exported catalogue file carries the same items and prices."""
    catalogue = _catalogue(runner)
    restored = c11n.CustomizationCatalog.from_json(catalogue.to_json())
    runner.equal(restored.count, catalogue.count, "the item count")
    runner.equal(restored.prices(), catalogue.prices(), "the prices")
    sample = catalogue.items_of(c11n.TYPE_CAMOUFLAGE)[:50]
    for item in sample:
        other = restored.get(item.compact_descr)
        runner.check(other is not None and other.as_json() == item.as_json(),
                     "%s %s came back unchanged" % (item.type_name, item.id))


# ---------------------------------------------------------------------------
# the XML dialect
# ---------------------------------------------------------------------------
def test_the_client_xml_dialect_is_stripped(runner: common.Runner) -> None:
    """Namespace declarations and prefixed element names are dropped."""
    source = ('<list.xml>\n'
              '  <xmlns:xmlref>\thttp://bwt/xmlref\t</xmlns:xmlref>\n'
              '  <china:Ch05_T34_248>\tussr\t</china:Ch05_T34_248>\n'
              '  <itemGroup><id>\t7\t</id></itemGroup>\n'
              '</list.xml>')
    stripped = defs.strip_xml_namespaces(source)
    if "xmlns" in stripped or "china:" in stripped:
        raise AssertionError("the declarations are still there: %r" % stripped)
    groups = defs.parse_xml(source).subsections("itemGroup")
    if len(groups) != 1:
        raise AssertionError("the item group was lost: %r" % (groups,))
    if groups[0].value_int("id", 0) != 7:
        raise AssertionError("the item group lost its values")


def test_a_file_without_namespaces_is_untouched(runner: common.Runner) -> None:
    """Files that are valid XML stay exactly as they are."""
    source = "<root>\n  <price>\t50\t<gold></gold>\n  </price>\n</root>"
    if defs.strip_xml_namespaces(source) != source:
        raise AssertionError("a plain file was rewritten")
    section = defs.parse_xml(source)
    if section.subsection("price") is None:
        raise AssertionError("the section is gone")


def test_the_whole_installation_still_reads(runner: common.Runner) -> None:
    """The namespace stripping did not break the game data of the installation."""
    catalog = loader.load_catalog(runner.client)
    catalog.validate()
    runner.check(len(catalog.vehicles.all()) > 100,
                 "the vehicles are still read (%d)" % len(catalog.vehicles.all()))
    runner.check(len(catalog.new_year.toys) > 10,
                 "the New Year decorations are still read (%d)" % len(catalog.new_year.toys))
    runner.check(len(catalog.devices) > 10,
                 "the optional devices are still read (%d)" % len(catalog.devices))


# ---------------------------------------------------------------------------
# the account side: the depot, the outfits and the money
#
# The tests drive ``RequestDispatcher`` with the arguments the client's own
# helpers write (``client/account_helpers/Shop.py``):
#
# * ``buyCustomizations(vehInvID, {intCD: count})`` - one flat
#   ``_doCmdIntArr([rev, vehInvID, intCD, count, ...])``,
# * ``sellCustomizations(vehInvID, itemCD, count)`` -
#   ``_doCmdInt4(rev, itemCD, count, vehInvID)`` (the vehicle last),
# * ``buyAndEquipStyle(vehInvID, styleID)`` - ``_doCmdInt3(rev, vehInvID, styleID)``,
# * ``buyAndEquipOutfit(vehInvID, season, outfitDescr)`` - the one command with two
#   argument arrays, ``_doCmdIntArrStrArr([rev, vehInvID, season], [descr])``.
#
# What they have to produce is what the client's caches read back:
# ``inventory[12][1]`` (the depot - ``{vehIntCD: count}`` per item, ``0`` being
# the unbound pile the client pops into ``_inventoryCount``), ``inventory[12][2]``
# (``{vehIntCD: {season: (descr, isEnabled)}}``, where the ``SeasonType.ALL``
# entry is the style) and the wallet sections.
# ---------------------------------------------------------------------------
def _keeper(runner):
    """``(state, CustomizationKeeper)`` of a fresh account, without a socket."""
    state = common.new_account(runner.client)
    if not state.catalog.customization.count:
        raise common.Skip("the installation ships no customization items")
    return state, customization_module.CustomizationKeeper(state)


def _vehicle(state):
    """``(invID, typeCompDescr)`` of one garage vehicle."""
    if not state.vehicles:
        raise common.Skip("the account has no vehicles")
    inv_id = min(state.vehicles)
    return inv_id, int(state.vehicles[inv_id].compact_descr)


def _catalogue_item(state, c_type, *, in_shop=True, vehicle_bound=None, season=None):
    """One priced item of the installation that matches the requirements."""
    for item in state.catalog.customization.items_of(c_type):
        if not item.priced:
            continue
        if item.not_in_shop is bool(in_shop):
            continue
        if vehicle_bound is not None and item.is_vehicle_bound() != bool(vehicle_bound):
            continue
        if season is not None and not item.season & season:
            continue
        return item
    raise common.Skip("no %s item in this installation matches (%s)"
                      % (c11n.TYPE_NAMES[c_type], "in shop" if in_shop else "out of shop"))


def _bound_item(state):
    """An item of the shop that carries the ``vehicleBound`` tag.

    In this build only styles are sold that way: the camouflages with the tag
    belong to the event and are not for sale (the New Year boxes hand them out,
    and they go to the depot of the vehicle the player applied them to).
    """
    for item in state.catalog.customization.items.values():
        if item.priced and not item.not_in_shop and item.is_vehicle_bound():
            return item
    raise common.Skip("the installation sells no vehicleBound item")


def _style(state):
    """A style the shop sells (the installation has rentable and buyable ones)."""
    for style in state.catalog.customization.styles():
        if style.priced and not style.not_in_shop:
            return style
    raise common.Skip("the installation has no style for sale")


def _command(keeper, opcode, args):
    """One request through the dispatcher, the way the socket serves it.

    A refused request carries no diff at all - only the code and the reason.
    """
    return RequestDispatcher(keeper.state).handle(1, opcode, args)


def _answer(response):
    """The single diff of an accepted request."""
    if not response.diffs:
        raise AssertionError("the answer carries code %s and no diff" % (response.code,))
    return response.diffs[0]


def _depot(state, compact_descr, vehicle_type=0):
    """``inventory[12][1][cType][itemID][vehicle]`` of one item."""
    c_type, item_id = items_module.parse_int_compact_descr(int(compact_descr))[1:]
    return state.customization.count(c_type, item_id, int(vehicle_type))


def _customization(diff):
    """The ``inventory[12]`` section of a diff."""
    return (diff.get("inventory") or {}).get(customization_module.CUSTOMIZATION_INVENTORY) or {}


def _items(diff):
    """``inventory[12][1]`` - the depot entries the diff carries."""
    return _customization(diff).get(customization_module.ITEMS) or {}


def _outfits(diff):
    """``inventory[12][2]`` - the outfits the diff carries."""
    return _customization(diff).get(customization_module.OUTFITS) or {}


def _wallet(state):
    return (state.credits, state.gold)


def _spent(before, after):
    """The wallet difference of two ``(credits, gold)`` pairs."""
    spent = {}
    if before[0] != after[0]:
        spent["credits"] = before[0] - after[0]
    if before[1] != after[1]:
        spent["gold"] = before[1] - after[1]
    return spent


def _price_of(item, count=1):
    """What buying ``count`` items costs - the currency the price names."""
    credits, gold = item.price
    return {"gold": gold * count} if gold > 0 else {"credits": credits * count}


def _sell_price(state, item, count=1):
    """``Shop.getSellPrice``: ``ceil(0.5 * (credits + gold * exchangeRate))``."""
    credits, gold = item.price
    rate = state.shop_payload()["exchangeRate"]
    return int(math.ceil(0.5 * (credits + gold * rate))) * count


def _outfit(**kwargs):
    """An outfit descriptor the client could have built."""
    return outfitting.make_comp_descr(outfitting.Outfit(**kwargs))


def test_the_depot_is_published_in_the_full_sync(runner: common.Runner) -> None:
    """``inventory[12]`` is there, with its three sections, on a first sync."""
    state = common.new_account(runner.client)
    section = state.full_diff()["inventory"].get(
        customization_module.CUSTOMIZATION_INVENTORY)
    runner.check(section is not None, "a full sync publishes inventory[12]")
    if section is None:
        return
    for key, name in ((customization_module.ITEMS, "ITEMS"),
                      (customization_module.OUTFITS, "OUTFITS"),
                      (customization_module.UNLOCKS, "UNLOCKS")):
        runner.check(isinstance(section.get(key), dict), "the empty account has %s" % name)


def test_buying_appearance_items_charges_and_stocks_the_depot(runner: common.Runner) -> None:
    """``CMD_BUY_C11N_ITEMS``: flat pairs in, the depot delta and the wallet out."""
    state, keeper = _keeper(runner)
    item = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS,
                        ([state.revision, 0, item.compact_descr, 2],))
    runner.equal(response.code, cmd.RES_SUCCESS, "the purchase is accepted")
    runner.equal(_spent(before, _wallet(state)), _price_of(item, 2),
                 "the price was charged (%s)" % (item.price,))
    runner.equal(_depot(state, item.compact_descr), 2, "the depot holds what was bought")
    runner.equal(_items(_answer(response)).get(item.c_type, {}).get(item.id), {0: 2},
                 "and the diff publishes it under the unbound key")
    runner.equal(_answer(response)["economics"]["gold"], state.gold,
                 "the wallet travels with the same diff")
    # Buying more of the same item publishes only the difference.
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS,
                        ([state.revision, 0, item.compact_descr, 3],))
    runner.equal(_items(_answer(response)).get(item.c_type, {}).get(item.id), {0: 5},
                 "the next answer carries the count the depot ended up with")
    runner.equal(_spent(before, _wallet(state)), _price_of(item, 3), "and its price was charged")


def test_a_vehicle_bound_item_is_stored_for_that_vehicle(runner: common.Runner) -> None:
    """A ``vehicleBound`` item goes to that vehicle's own part of the depot."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    item = _bound_item(state)
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS,
                        ([state.revision, inv_id, item.compact_descr, 1],))
    runner.equal(response.code, cmd.RES_SUCCESS, "the bound item is bought")
    runner.equal(_depot(state, item.compact_descr, vehicle_type), 1,
                 "it sits under the vehicle type of the garage vehicle")
    runner.equal(_depot(state, item.compact_descr), 0, "and not in the unbound pile")
    runner.equal(_items(_answer(response)).get(item.c_type, {}).get(item.id),
                 {vehicle_type: 1}, "which is what the diff says")
    # An unknown vehicle cannot be served: the depot is indexed by vehicle type.
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS,
                        ([state.revision, 99999, item.compact_descr, 1],))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an unknown vehicle is refused")
    runner.equal(response.error, customization_module.ERROR_WRONG_ARGS, "with a reason")
    runner.equal(_wallet(state), before, "and nothing is charged")


def test_items_the_shop_does_not_sell_are_refused(runner: common.Runner) -> None:
    """``notInShopItems`` of the shop cache is enforced, not only published."""
    state, keeper = _keeper(runner)
    item = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=False)
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS,
                        ([state.revision, 0, item.compact_descr, 1],))
    runner.equal(response.code, cmd.RES_NOT_AVAILABLE, "the item is not for sale")
    runner.equal(response.error, customization_module.ERROR_NOT_AVAILABLE,
                 "and the client is told why")
    runner.equal(_depot(state, item.compact_descr), 0, "nothing was stocked")
    runner.equal(_wallet(state), before, "and nothing was charged")
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, 0, 900000, 1],))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an item the catalogue does not know")
    runner.equal(response.error, customization_module.ERROR_NOT_AVAILABLE, "is not available")


def test_a_malformed_shopping_list_is_refused(runner: common.Runner) -> None:
    """An odd pair count or an empty list is refused, not half served."""
    state, keeper = _keeper(runner)
    item = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True)
    other = _catalogue_item(state, c11n.TYPE_PAINT, in_shop=True)
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS,
                        ([state.revision, 0, item.compact_descr, 1, other.compact_descr],))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an odd tail is refused")
    runner.equal(response.error, customization_module.ERROR_WRONG_ARGS, "wrong_args")
    response = _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, 0],))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an empty list is refused too")
    runner.equal(_depot(state, item.compact_descr), 0, "no item of the odd list was stocked")
    runner.equal(_wallet(state), before, "and nothing was charged")


def test_selling_refunds_the_price_the_shop_shows(runner: common.Runner) -> None:
    """``CMD_SELL_C11N_ITEMS``: ``ceil(0.5 * (credits + gold * 400))`` each."""
    state, keeper = _keeper(runner)
    item = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    price = _sell_price(state, item)
    _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, 0, item.compact_descr, 2],))
    before = state.credits
    response = _command(keeper, cmd.CMD_SELL_C11N_ITEMS,
                        (state.revision, item.compact_descr, 1, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the sale is accepted")
    runner.equal(state.credits, before + price, "one item is refunded (%d)" % price)
    runner.equal(_depot(state, item.compact_descr), 1, "one item is left in the depot")
    runner.equal(_items(_answer(response)).get(item.c_type, {}).get(item.id), {0: 1},
                 "the depot delta")
    # The last copy of an entry travels as an explicit zero: a diff that leaves
    # the key out would keep the client's cache at one and its panels would offer
    # an item the account does not own any more.
    response = _command(keeper, cmd.CMD_SELL_C11N_ITEMS,
                        (state.revision, item.compact_descr, 1, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the last copy is sold")
    runner.equal(_items(_answer(response)).get(item.c_type, {}).get(item.id), {0: 0},
                 "and published as an explicit zero")
    runner.equal(state.credits, before + price * 2, "both copies were refunded")
    runner.equal(_depot(state, item.compact_descr), 0, "the depot is empty again")


def test_selling_more_than_the_depot_holds_is_refused(runner: common.Runner) -> None:
    """The depot is the authority, not the client's cache."""
    state, keeper = _keeper(runner)
    item = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True)
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_SELL_C11N_ITEMS,
                        (state.revision, item.compact_descr, 1, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "selling what is not there is refused")
    runner.equal(response.error, customization_module.ERROR_NOT_AVAILABLE, "not_available")
    response = _command(keeper, cmd.CMD_SELL_C11N_ITEMS,
                        (state.revision, item.compact_descr, 0, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "and so is a zero count")
    runner.equal(_wallet(state), before, "the wallet was not touched")
    # An item bound to a vehicle can only be sold while that vehicle is named.
    inv_id, vehicle_type = _vehicle(state)
    bound = _bound_item(state)
    _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, inv_id, bound.compact_descr, 1],))
    response = _command(keeper, cmd.CMD_SELL_C11N_ITEMS,
                        (state.revision, bound.compact_descr, 1, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS,
                 "the unbound pile does not hold the bound copy")
    response = _command(keeper, cmd.CMD_SELL_C11N_ITEMS,
                        (state.revision, bound.compact_descr, 1, inv_id))
    runner.equal(response.code, cmd.RES_SUCCESS, "naming the vehicle sells it")
    runner.equal(_depot(state, bound.compact_descr, vehicle_type), 0, "and empties its entry")


def test_an_outfit_costs_one_item_per_region(runner: common.Runner) -> None:
    """A paint covering the whole tank needs one paint per region."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    paint = _catalogue_item(state, c11n.TYPE_PAINT, in_shop=True, season=cc.SeasonType.WINTER)
    outfit = outfitting.Outfit(paints=[outfitting.Paint(
        id=paint.id, applied_to=cc.ApplyArea.USER_PAINT_ALLOWED_REGIONS_VALUE)])
    wanted = outfitting.items_of_outfit(outfit, state.catalog.customization)
    if wanted.get(paint.compact_descr, 0) <= 1:
        raise common.Skip("the paint covers a single region in this installation")
    before = _wallet(state)
    descr = outfitting.make_comp_descr(outfit)
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, cc.SeasonType.WINTER], [descr]))
    runner.equal(response.code, cmd.RES_SUCCESS, "the outfit is mounted")
    runner.equal(_spent(before, _wallet(state)),
                 _price_of(paint, wanted[paint.compact_descr]),
                 "every region was paid for (%d)" % wanted[paint.compact_descr])
    runner.equal(response.ext["bought"], [[paint.c_type, paint.id]],
                 "the answer says what had to be bought")
    runner.equal(response.ext["mounted"], [[paint.c_type, paint.id]], "and what was mounted")
    entry = _outfits(_answer(response)).get(vehicle_type, {}).get(cc.SeasonType.WINTER)
    runner.equal(entry, (descr, True), "the outfit is published for the season")
    runner.equal(_depot(state, paint.compact_descr, vehicle_type), 0,
                 "the paints went onto the tank, not into the depot")


def test_mounting_an_outfit_takes_its_items_from_the_depot(runner: common.Runner) -> None:
    """What the depot holds is used before anything is bought."""
    state, keeper = _keeper(runner)
    inv_id, _vehicle_type = _vehicle(state)
    camo = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, 0, camo.compact_descr, 2],))
    outfit = outfitting.Outfit(camouflages=[outfitting.Camouflage(
        id=camo.id, applied_to=cc.ApplyArea.CHASSIS)])
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, cc.SeasonType.SUMMER],
                         [outfitting.make_comp_descr(outfit)]))
    runner.equal(response.code, cmd.RES_SUCCESS, "the outfit is mounted")
    runner.equal(response.ext["bought"], [], "nothing had to be bought")
    runner.equal(_wallet(state), before, "and nothing was charged")
    runner.equal(_depot(state, camo.compact_descr), 1, "one camouflage left the depot")
    runner.equal(_items(_answer(response)).get(camo.c_type, {}).get(camo.id), {0: 1},
                 "the depot delta names what is left")


def test_replacing_an_outfit_returns_the_old_items(runner: common.Runner) -> None:
    """Trying outfits on loses nothing: the replaced one goes back to the depot."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    first = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    others = [item for item in state.catalog.customization.items_of(c11n.TYPE_CAMOUFLAGE)
              if item.priced and not item.not_in_shop and item.id != first.id]
    if not others:
        raise common.Skip("the installation has a single camouflage")
    second = others[0]
    _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, 0, first.compact_descr, 1],))
    _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
             ([state.revision, inv_id, cc.SeasonType.SUMMER],
              [_outfit(camouflages=[outfitting.Camouflage(
                  id=first.id, applied_to=cc.ApplyArea.CHASSIS)])]))
    runner.equal(_depot(state, first.compact_descr), 0, "the first outfit took its item")
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, cc.SeasonType.SUMMER],
                         [_outfit(camouflages=[outfitting.Camouflage(
                             id=second.id, applied_to=cc.ApplyArea.CHASSIS)])]))
    runner.equal(response.code, cmd.RES_SUCCESS, "the second outfit is mounted")
    runner.equal(_spent(before, _wallet(state)), _price_of(second),
                 "only the new item was paid for")
    runner.equal(_depot(state, first.compact_descr, vehicle_type), 1,
                 "the first item is back in the depot, bound to the vehicle")
    runner.equal(_items(_answer(response)).get(first.c_type, {}).get(first.id),
                 {vehicle_type: 1}, "and the same diff says so")


def test_a_style_is_paid_for_and_put_on(runner: common.Runner) -> None:
    """``CMD_VEH_APPLY_STYLE``: pay, bind the style to the vehicle, publish ALL."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    style = _style(state)
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, style.id))
    runner.equal(response.code, cmd.RES_SUCCESS, "the style is applied")
    runner.equal(_spent(before, _wallet(state)), _price_of(style), "the style was paid for")
    runner.equal(response.ext, {"styleID": int(style.id), "styleCD": style.compact_descr},
                 "the answer names the style")
    runner.equal(_depot(state, style.compact_descr, vehicle_type),
                 style.rent_count if style.is_rent else 1,
                 "a rented style counts its battles as the bound count")
    entry = _outfits(_answer(response)).get(vehicle_type, {}).get(cc.SeasonType.ALL)
    if entry is None:
        raise AssertionError("the diff does not publish the ALL season")
    descr, enabled = entry
    runner.check(enabled, "and it is enabled")
    runner.equal(outfitting.parse_outfit_descr(descr).style_id, style.id,
                 "the descriptor carries the style id")
    runner.equal(sorted(style.outfits), list(cc.SeasonType.SEASONS),
                 "the client builds the seasons of a style out of its own data, "
                 "so the style has one outfit per season")
    # Putting the same style on again is free: the depot holds it by now.
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, style.id))
    runner.equal(response.code, cmd.RES_SUCCESS, "applying it again works")
    runner.equal(_wallet(state), before, "and costs nothing")


def test_applying_a_style_takes_the_hand_made_outfit_off(runner: common.Runner) -> None:
    """A vehicle wears either a style or its seasons, never both."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    camo = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
             ([state.revision, inv_id, cc.SeasonType.SUMMER],
              [_outfit(camouflages=[outfitting.Camouflage(
                  id=camo.id, applied_to=cc.ApplyArea.CHASSIS)])]))
    runner.equal(_depot(state, camo.compact_descr), 0, "the camouflage is on the vehicle")
    style = _style(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, style.id))
    runner.equal(response.code, cmd.RES_SUCCESS, "the style is applied")
    section = _outfits(_answer(response)).get(vehicle_type, {})
    runner.equal(section.get(cc.SeasonType.SUMMER), None,
                 "the hand-made season is removed from the client's cache")
    runner.check(cc.SeasonType.ALL in section, "and the style is published")
    runner.equal(_depot(state, camo.compact_descr, vehicle_type), 1,
                 "the item of the hand-made outfit is back in the depot")
    runner.equal(_items(_answer(response)).get(camo.c_type, {}).get(camo.id),
                 {vehicle_type: 1}, "and the diff names it")


def test_a_style_can_be_taken_off_a_vehicle(runner: common.Runner) -> None:
    """``StyleApplier`` without a style (``styleID = 0``) removes it."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    style = _style(state)
    _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, style.id))
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the style is taken off")
    runner.equal(response.ext, {"styleID": 0, "styleCD": 0}, "the answer clears the style")
    runner.equal(_outfits(_answer(response)).get(vehicle_type, {}).get(cc.SeasonType.ALL),
                 None, "the client's cache loses the ALL season")
    runner.equal(_wallet(state), before, "taking it off is free")
    runner.equal(_depot(state, style.compact_descr, vehicle_type),
                 style.rent_count if style.is_rent else 1, "the style stays in the depot")
    # And it goes back on without paying twice.
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, style.id))
    runner.equal(response.code, cmd.RES_SUCCESS, "it goes back on")
    runner.equal(_wallet(state), before, "for free")


def test_a_style_the_shop_does_not_sell_is_refused(runner: common.Runner) -> None:
    """``notInShop`` styles cannot be applied, and unknown ones do not exist."""
    state, keeper = _keeper(runner)
    inv_id, _vehicle_type = _vehicle(state)
    styles = [style for style in state.catalog.customization.styles() if style.not_in_shop]
    if not styles:
        raise common.Skip("the installation sells every style")
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE,
                        (state.revision, inv_id, styles[0].id))
    runner.equal(response.code, cmd.RES_NOT_AVAILABLE, "the style is not for sale")
    runner.equal(response.error, customization_module.ERROR_NOT_AVAILABLE, "not_available")
    runner.equal(_wallet(state), before, "nothing was charged")
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, 4242))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an unknown style id is refused")
    response = _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, 99999, styles[0].id))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "and so is an unknown vehicle")


def test_an_outfit_that_names_a_style_is_that_style(runner: common.Runner) -> None:
    """An ``Outfit`` with a ``styleId`` is bought and mounted the way a style is."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    style = _style(state)
    before = _wallet(state)
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, cc.SeasonType.WINTER],
                         [_outfit(style_id=style.id)]))
    runner.equal(response.code, cmd.RES_SUCCESS, "the outfit is mounted")
    runner.equal(_spent(before, _wallet(state)), _price_of(style),
                 "the style behind it had to be paid for")
    entry = _outfits(_answer(response)).get(vehicle_type, {}).get(cc.SeasonType.ALL)
    if entry is None:
        raise AssertionError("it is not published as the ALL season")
    runner.equal(outfitting.parse_outfit_descr(entry[0]).style_id, style.id,
                 "with the style id in the descriptor")


def test_an_empty_outfit_clears_a_season(runner: common.Runner) -> None:
    """The client sends an empty outfit to take one season's outfit off."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    camo = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
             ([state.revision, inv_id, cc.SeasonType.DESERT],
              [_outfit(camouflages=[outfitting.Camouflage(
                  id=camo.id, applied_to=cc.ApplyArea.CHASSIS)])]))
    runner.equal(_depot(state, camo.compact_descr), 0, "the camouflage is on the vehicle")
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, cc.SeasonType.DESERT], [""]))
    runner.equal(response.code, cmd.RES_SUCCESS, "the empty outfit is accepted")
    entry = _outfits(_answer(response)).get(vehicle_type, {}).get(cc.SeasonType.DESERT)
    runner.equal(entry, (outfitting.make_comp_descr(outfitting.Outfit()), True),
                 "the season now holds an empty outfit")
    runner.equal(_depot(state, camo.compact_descr, vehicle_type), 1,
                 "and the item is back in the depot")
    runner.equal(_items(_answer(response)).get(camo.c_type, {}).get(camo.id),
                 {vehicle_type: 1}, "which the diff publishes")


def test_a_broken_outfit_descriptor_is_refused(runner: common.Runner) -> None:
    """A descriptor the client could not have built is refused, not guessed."""
    state, keeper = _keeper(runner)
    inv_id, _vehicle_type = _vehicle(state)
    before = _wallet(state)
    for descr in ("\x09\x01", "\x04", "\x05\x10"):
        response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                            ([state.revision, inv_id, cc.SeasonType.SUMMER], [descr]))
        runner.equal(response.code, cmd.RES_WRONG_ARGS,
                     "the broken descriptor is refused (%r)" % (descr,))
        runner.equal(response.error, customization_module.ERROR_WRONG_ARGS, "wrong_args")
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, 3], [""]))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "season 3 is not a SeasonType")
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        (state.revision, inv_id, cc.SeasonType.SUMMER))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "no descriptor, no outfit")
    runner.equal(_wallet(state), before, "and nothing was charged")


def test_mounting_a_hand_made_outfit_takes_the_style_off(runner: common.Runner) -> None:
    """The client prefers a style over the seasons, so the style has to go."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    style = _style(state)
    _command(keeper, cmd.CMD_VEH_APPLY_STYLE, (state.revision, inv_id, style.id))
    camo = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    response = _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
                        ([state.revision, inv_id, cc.SeasonType.DESERT],
                         [_outfit(camouflages=[outfitting.Camouflage(
                             id=camo.id, applied_to=cc.ApplyArea.CHASSIS)])]))
    runner.equal(response.code, cmd.RES_SUCCESS, "the outfit is mounted over the style")
    section = _outfits(_answer(response)).get(vehicle_type, {})
    runner.equal(section.get(cc.SeasonType.ALL), None, "the style leaves the client's cache")
    runner.check(cc.SeasonType.DESERT in section, "and the outfit arrives")
    runner.equal(_depot(state, style.compact_descr, vehicle_type),
                 style.rent_count if style.is_rent else 1, "the style itself stays owned")


def test_the_depot_and_the_outfits_survive_the_account_file(runner: common.Runner) -> None:
    """What was bought and mounted is still there after a restart."""
    state, keeper = _keeper(runner)
    inv_id, vehicle_type = _vehicle(state)
    item = _catalogue_item(state, c11n.TYPE_CAMOUFLAGE, in_shop=True, vehicle_bound=False)
    _command(keeper, cmd.CMD_BUY_C11N_ITEMS, ([state.revision, 0, item.compact_descr, 2],))
    descr = _outfit(camouflages=[outfitting.Camouflage(
        id=item.id, applied_to=cc.ApplyArea.CHASSIS)])
    _command(keeper, cmd.CMD_VEH_APPLY_OUTFIT,
             ([state.revision, inv_id, cc.SeasonType.SUMMER], [descr]))
    reloaded = AccountState.from_json(state.to_json(), state.catalog,
                                      tankman_factory=state.tankman_factory)
    runner.equal(reloaded.customization.count(item.c_type, item.id, 0), 1, "the depot came back")
    entry = reloaded.customization.outfit(vehicle_type, cc.SeasonType.SUMMER)
    runner.check(entry is not None, "the outfit came back")
    if entry is not None:
        runner.equal(entry[0], descr, "with its descriptor")
        runner.check(entry[1], "and its enabled flag")
    runner.equal(reloaded.customization.sections(item_types=[(item.c_type, item.id)],
                                                 vehicle_types=[vehicle_type]), {},
                 "a restarted account has nothing left to publish incrementally")


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
