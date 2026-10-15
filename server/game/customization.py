"""Camouflages, paints and decals of the client's own ``item_defs``.

The appearance items live next to the vehicles in the installation
(``customization/``) and every one of them is priced there:

* ``customization/priceGroups/list.xml`` names *price groups* and gives each of
  them a price (``legacy camo ussr`` is 50 gold) and a ``notInShop`` flag,
* ``customization/<folder>/list.xml`` holds ``<itemGroup>`` sections - a
  prototype (its price group, its season, its vehicle filter) with the items
  themselves as children (``<camouflage>``, ``<paint>``, ``<decal>``), each
  with an ``<id>`` and optionally its own ``<price>`` override.

A customization item is numbered like every other item: its compact descriptor
is ``items.makeIntCompactDescrByID('customizationItem', cType, itemId)``
(``common/items/components/c11n_components.py::makeIntDescr``), so
``cType`` takes the place the nation has for the other item types - which is why
the client's ``InventoryRequester`` reads the depot as
``inventory['customization'][ITEMS][cType][itemID]``.

What is read here is what a *server* needs: the price of every item and whether
the shop may sell it.  The client draws the item (textures, palettes, sizes,
filters) from its own data, and it filters the items it is allowed to apply, so
the vehicle filter is read only as far as the pictures need it
(``nations``/``levels``/``tags`` of the ``include`` sections).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import items as items_module
from .defs import DefsSource

#: ``common/items/components/c11n_constants.py::CustomizationType``.
TYPE_PAINT = 1
TYPE_CAMOUFLAGE = 2
TYPE_DECAL = 3
TYPE_STYLE = 4
TYPE_MODIFICATION = 5

LAYOUT_TYPES = {TYPE_PAINT, TYPE_CAMOUFLAGE, TYPE_DECAL, TYPE_STYLE, TYPE_MODIFICATION}

#: ``CustomizationTypeNames``: the name of a type in the client's language.
TYPE_NAMES = {
    TYPE_PAINT: "paint",
    TYPE_CAMOUFLAGE: "camouflage",
    TYPE_DECAL: "decal",
    TYPE_STYLE: "style",
    TYPE_MODIFICATION: "modification",
}

#: ``CustomizationType._SIMPLE_TYPES``: the items that live in the depot and are
#: applied per region, as opposed to a whole-vehicle style.
SIMPLE_TYPES = (TYPE_PAINT, TYPE_CAMOUFLAGE, TYPE_DECAL)

#: ``c11n_constants.SeasonType`` - a bit per season, ``ALL`` covers the lot.
SEASON_WINTER = 1
SEASON_SUMMER = 2
SEASON_DESERT = 4
SEASON_EVENT = 8
SEASON_ALL = SEASON_WINTER | SEASON_SUMMER | SEASON_DESERT | SEASON_EVENT

SEASON_IDS = {
    "winter": SEASON_WINTER,
    "summer": SEASON_SUMMER,
    "desert": SEASON_DESERT,
    "event": SEASON_EVENT,
    "all": SEASON_ALL,
}

#: ``c11n_constants.ItemTags``.
TAG_VEHICLE_BOUND = "vehicleBound"
TAG_RARE = "rare"

#: The folders of the installation and the type each one holds.
ITEM_FOLDERS: Tuple[Tuple[str, str, int], ...] = (
    ("paints", "paint", TYPE_PAINT),
    ("camouflages", "camouflage", TYPE_CAMOUFLAGE),
    ("decals", "decal", TYPE_DECAL),
)

PRICE_GROUPS_PATH = "customization/priceGroups/list.xml"


def make_int_descr(c_type: int, item_id: int) -> int:
    """The compact descriptor of one customization item."""
    return items_module.make_int_compact_descr_by_id("customizationItem", int(c_type),
                                                     int(item_id))


@dataclass
class C11nItem:
    """One camouflage, paint or decal, with the price the shop charges."""

    c_type: int
    id: int
    season: int = SEASON_ALL
    credits: int = 0
    gold: int = 0
    tags: Tuple[str, ...] = ()
    not_in_shop: bool = False
    group_name: str = ""
    #: ``include`` sections of the item's ``vehicleFilter``: the nations,
    #: levels and vehicle tags it fits.  Empty means "no restriction".
    nations: Tuple[int, ...] = ()
    levels: Tuple[int, ...] = ()
    vehicle_tags: Tuple[str, ...] = ()

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.c_type, "customizationItem")

    @property
    def compact_descr(self) -> int:
        return make_int_descr(self.c_type, self.id)

    @property
    def price(self) -> Tuple[int, int]:
        return (int(self.credits), int(self.gold))

    @property
    def priced(self) -> bool:
        return self.credits > 0 or self.gold > 0

    @property
    def seasons(self) -> List[int]:
        return [season for season in (SEASON_WINTER, SEASON_SUMMER, SEASON_DESERT,
                                      SEASON_EVENT) if self.season & season]

    def is_vehicle_bound(self) -> bool:
        return TAG_VEHICLE_BOUND in self.tags

    def as_json(self) -> dict:
        return {
            "type": self.c_type,
            "id": self.id,
            "season": self.season,
            "credits": self.credits,
            "gold": self.gold,
            "tags": list(self.tags),
            "notInShop": self.not_in_shop,
            "group": self.group_name,
            "nations": list(self.nations),
            "levels": list(self.levels),
            "vehicleTags": list(self.vehicle_tags),
        }

    @classmethod
    def from_json(cls, payload: dict) -> "C11nItem":
        return cls(
            c_type=int(payload["type"]),
            id=int(payload["id"]),
            season=int(payload.get("season", SEASON_ALL)),
            credits=int(payload.get("credits", 0)),
            gold=int(payload.get("gold", 0)),
            tags=tuple(payload.get("tags") or ()),
            not_in_shop=bool(payload.get("notInShop", False)),
            group_name=str(payload.get("group") or ""),
            nations=tuple(int(value) for value in payload.get("nations") or ()),
            levels=tuple(int(value) for value in payload.get("levels") or ()),
            vehicle_tags=tuple(payload.get("vehicleTags") or ()),
        )


@dataclass
class PriceGroup:
    """One ``<priceGroup>`` of the installation."""

    id: int
    name: str
    credits: int = 0
    gold: int = 0
    tags: Tuple[str, ...] = ()
    not_in_shop: bool = False

    @property
    def price(self) -> Tuple[int, int]:
        return (int(self.credits), int(self.gold))


class CustomizationCatalog:
    """Every appearance item of the installation, indexed by compact descriptor."""

    def __init__(self, items: Dict[int, C11nItem] = None,
                 groups: Dict[str, PriceGroup] = None):
        self.items: Dict[int, C11nItem] = {int(cd): item for cd, item in (items or {}).items()}
        self.groups: Dict[str, PriceGroup] = dict(groups or {})

    # -- lookups --------------------------------------------------------
    def get(self, compact_descr: int) -> Optional[C11nItem]:
        return self.items.get(int(compact_descr))

    def of_type(self, c_type: int) -> Dict[int, C11nItem]:
        return {item.id: item for item in self.items.values() if item.c_type == int(c_type)}

    def items_of(self, c_type: int) -> List[C11nItem]:
        return sorted((item for item in self.items.values() if item.c_type == int(c_type)),
                      key=lambda item: item.id)

    def prices(self) -> Dict[int, Tuple[int, int]]:
        """``compactDescr -> (credits, gold)`` of every priced item."""
        return {compact: item.price for compact, item in self.items.items() if item.priced}

    def not_in_shop(self) -> List[int]:
        return sorted(compact for compact, item in self.items.items() if item.not_in_shop)

    @property
    def count(self) -> int:
        return len(self.items)

    # -- reading the installation ---------------------------------------
    @classmethod
    def load(cls, source: DefsSource) -> "CustomizationCatalog":
        groups = cls._read_price_groups(source)
        items: Dict[int, C11nItem] = {}
        for folder, section_name, c_type in ITEM_FOLDERS:
            path = "customization/%s/list.xml" % folder
            if not source.has(path):
                continue
            for item in cls._read_item_file(source.read_section(path), section_name,
                                            c_type, groups):
                items[item.compact_descr] = item
        return cls(items, groups)

    @staticmethod
    def _read_price_groups(source: DefsSource) -> Dict[str, PriceGroup]:
        from .catalog import _read_price

        groups: Dict[str, PriceGroup] = {}
        if not source.has(PRICE_GROUPS_PATH):
            return groups
        for entry in source.read_section(PRICE_GROUPS_PATH).subsections("priceGroup"):
            name = entry.value("name", "") or ""
            if not name:
                continue
            price, currency = _read_price(entry)
            groups[name] = PriceGroup(
                id=entry.value_int("id", 0),
                name=name,
                credits=price if currency == "credits" else 0,
                gold=price if currency == "gold" else 0,
                tags=tuple(entry.value_tokens("tags")),
                not_in_shop=entry.value_bool("notInShop", False),
            )
        return groups

    @staticmethod
    def _read_item_file(section, section_name: str, c_type: int,
                        groups: Dict[str, PriceGroup]) -> List[C11nItem]:
        """``<itemGroup>`` sections -> the items with the price group's price."""
        from .catalog import _read_price

        result: List[C11nItem] = []
        for group in section.subsections("itemGroup"):
            prototype = {
                "season": _read_season(group, SEASON_ALL),
                "group": group.value("priceGroup", "") or "",
                "tags": tuple(group.value_tokens("tags")),
                "nations": _read_filter(group, "nations"),
                "levels": _read_filter_int(group, "levels"),
                "vehicle_tags": _read_filter(group, "tags"),
            }
            top_price, top_currency = _read_price(group)
            for entry in group.subsections(section_name):
                item_id = entry.value_int("id", -1)
                if item_id < 0:
                    continue
                price, currency = _read_price(entry)
                if price <= 0:
                    price, currency = top_price, top_currency
                group_name = entry.value("priceGroup", "") or prototype["group"]
                not_in_shop = entry.value_bool("notInShop", prototype.get("notInShop", False))
                price_group = groups.get(group_name)
                if price <= 0 and price_group is not None:
                    price = price_group.credits or price_group.gold
                    currency = "credits" if price_group.credits else "gold"
                    not_in_shop = not_in_shop or price_group.not_in_shop
                result.append(C11nItem(
                    c_type=c_type,
                    id=item_id,
                    season=_read_season(entry, prototype["season"]),
                    credits=price if currency == "credits" else 0,
                    gold=price if currency == "gold" else 0,
                    tags=tuple(sorted(set(prototype["tags"]) | set(entry.value_tokens("tags")))),
                    not_in_shop=bool(not_in_shop),
                    group_name=group_name,
                    nations=prototype["nations"],
                    levels=prototype["levels"],
                    vehicle_tags=prototype["vehicle_tags"],
                ))
        return result

    # -- the catalogue file ---------------------------------------------
    def to_json(self) -> dict:
        return {
            "items": [item.as_json() for item in
                      sorted(self.items.values(), key=lambda value: (value.c_type, value.id))],
            "groups": [{"name": group.name, "price": list(group.price),
                        "id": group.id, "tags": list(group.tags),
                        "notInShop": group.not_in_shop}
                       for group in sorted(self.groups.values(), key=lambda value: value.name)],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "CustomizationCatalog":
        items = {}
        for entry in payload.get("items") or ():
            item = C11nItem.from_json(entry)
            items[item.compact_descr] = item
        groups = {}
        for entry in payload.get("groups") or ():
            price = list(entry.get("price") or (0, 0))
            groups[entry["name"]] = PriceGroup(
                id=int(entry.get("id", 0)), name=entry["name"],
                credits=int(price[0]) if price else 0,
                gold=int(price[1]) if len(price) > 1 else 0,
                tags=tuple(entry.get("tags") or ()),
                not_in_shop=bool(entry.get("notInShop", False)),
            )
        return cls(items, groups)


# ---------------------------------------------------------------------------
# small readers of the item sections
# ---------------------------------------------------------------------------
def _read_season(section, default: int = SEASON_ALL) -> int:
    """``<season>`` - one name or a list of names, ``all`` covers everything."""
    if section is None:
        return int(default)
    tokens = section.value_tokens("season")
    if not tokens:
        value = section.value("season", "")
        tokens = str(value or "").split()
    season = 0
    for token in tokens:
        season |= SEASON_IDS.get(str(token).strip().lower(), 0)
    return season or int(default)


def _read_filter(group, key: str) -> Tuple[int, ...]:
    """The nations of the ``include`` sections of a ``<vehicleFilter>``."""
    from . import ny_constants as nyc

    values: List[int] = []
    for node in _filter_nodes(group):
        for token in node.value_tokens(key):
            nation_id = nyc.NATION_INDICES.get(str(token).strip())
            if nation_id is not None and nation_id not in values:
                values.append(nation_id)
    return tuple(values)


def _read_filter_int(group, key: str) -> Tuple[int, ...]:
    values: List[int] = []
    for node in _filter_nodes(group):
        for token in node.value_tokens(key):
            text = str(token).strip()
            if text.isdigit() and int(text) not in values:
                values.append(int(text))
    return tuple(values)


def _filter_nodes(group) -> List:
    """The ``<include>`` children of an ``<itemGroup>``'s ``<vehicleFilter>``."""
    filter_section = group.subsection("vehicleFilter")
    if filter_section is None:
        return []
    nodes = list(filter_section.subsections("include"))
    for child in getattr(filter_section, "children", ()):  # pragma: no cover - both shapes
        if getattr(child, "name", "") == "include" and child not in nodes:
            nodes.append(child)
    return nodes


__all__ = [
    "C11nItem",
    "CustomizationCatalog",
    "ITEM_FOLDERS",
    "LAYOUT_TYPES",
    "PRICE_GROUPS_PATH",
    "PriceGroup",
    "SEASON_ALL",
    "SEASON_IDS",
    "SEASON_SUMMER",
    "SEASON_WINTER",
    "SEASON_DESERT",
    "SIMPLE_TYPES",
    "TAG_RARE",
    "TAG_VEHICLE_BOUND",
    "TYPE_CAMOUFLAGE",
    "TYPE_DECAL",
    "TYPE_MODIFICATION",
    "TYPE_NAMES",
    "TYPE_PAINT",
    "TYPE_STYLE",
    "make_int_descr",
]
