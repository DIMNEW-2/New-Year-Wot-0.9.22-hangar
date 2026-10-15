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
which a strict parser refuses.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server import loader  # noqa: E402
from server.game import customization as c11n  # noqa: E402
from server.game import defs, items as items_module, ny_constants as nyc  # noqa: E402

CUSTOMIZATION_ITEM = items_module.ITEM_TYPES["customizationItem"]


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


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
