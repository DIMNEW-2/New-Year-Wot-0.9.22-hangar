"""Camouflages, paints, decals, modifications and styles of the client's data.

The appearance items live next to the vehicles in the installation
(``customization/``) and every one of them is priced there:

* ``customization/priceGroups/list.xml`` names *price groups* and gives each of
  them a price (``legacy camo ussr`` is 50 gold) and a ``notInShop`` flag,
* ``customization/<folder>/list.xml`` holds ``<itemGroup>`` sections - a
  prototype (its price group, its season, its vehicle filter) with the items
  themselves as children (``<camouflage>``, ``<paint>``, ``<decal>``),
  each with an ``<id>`` and optionally its own ``<price>`` override.

Only ``<folder>/list.xml`` is read, exactly like the client: its
``readCustomizationCacheFromXml`` (``common/items/readers/c11n_readers.py``)
opens ``<name>s/list.xml`` for every kind and never looks at the other files of
the folder (the per-nation ``styles/*.xml`` are not referenced from
``styles/list.xml``, so they are not part of the client's cache either).

A customization item is numbered like every other item: its compact descriptor
is ``items.makeIntCompactDescrByID('customizationItem', cType, itemId)``
(``common/items/components/c11n_components.py::makeIntDescr``), so ``cType``
takes the place the nation has for the other item types - which is why the
client's ``InventoryRequester`` reads the depot as
``inventory['customization'][ITEMS][cType][itemID]``.

What is read here is what a *server* needs: the price of every item, whether the
shop may sell it, what an outfit of a style consists of and how much paint a
region of a tank costs.  The client draws the item (textures, palettes, sizes,
filters) from its own data, and it filters the items it is allowed to apply, so
the vehicle filter is read only as far as the pictures need it
(``nations``/``levels``/``tags`` of the ``include`` sections).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import c11n_constants as constants
from . import c11n_outfit as outfit_module
from . import items as items_module
from . import ny_constants as nyc
from .defs import DefsSource

#: Re-exported: the client's ``CustomizationType`` / ``SeasonType`` / ``ItemTags``
#: values, under the names the rest of the server uses.
TYPE_PAINT = constants.CustomizationType.PAINT
TYPE_CAMOUFLAGE = constants.CustomizationType.CAMOUFLAGE
TYPE_DECAL = constants.CustomizationType.DECAL
TYPE_STYLE = constants.CustomizationType.STYLE
TYPE_MODIFICATION = constants.CustomizationType.MODIFICATION

#: ``CustomizationTypeNames``: the name of a type in the client's language.
TYPE_NAMES = dict(constants.TYPE_NAMES)
#: ``customTypeName -> cType``: what a reward payload's ``custType`` names
#: (``gui/server_events/bonuses.py``: ``GUI_ITEM_TYPE_INDICES.get(itemTypeName)``).
TYPE_IDS_BY_NAME = {name: c_type for c_type, name in TYPE_NAMES.items()}

#: ``CustomizationType._SIMPLE_TYPES``: the items that live in the depot and are
#: applied per region, as opposed to a whole-vehicle style.
SIMPLE_TYPES = constants.CustomizationType._SIMPLE_TYPES

#: The item types the server keeps in a vehicle's depot table.
LAYOUT_TYPES = (TYPE_PAINT, TYPE_CAMOUFLAGE, TYPE_DECAL, TYPE_STYLE, TYPE_MODIFICATION)

SEASON_WINTER = constants.SeasonType.WINTER
SEASON_SUMMER = constants.SeasonType.SUMMER
SEASON_DESERT = constants.SeasonType.DESERT
SEASON_EVENT = constants.SeasonType.EVENT
SEASON_ALL = constants.SeasonType.ALL
SEASON_IDS = dict(constants.SEASON_IDS)
SEASONS = constants.SeasonType.SEASONS

TAG_VEHICLE_BOUND = constants.ItemTags.VEHICLE_BOUND
TAG_RARE = constants.ItemTags.RARE

#: ``c11n_constants.RENT_DEFAULT_BATTLES``: the battles a rented style gives.
RENT_DEFAULT_BATTLES = constants.RENT_DEFAULT_BATTLES

#: The folders of the installation and the type each one holds.  A folder is a
#: ``<name>s/list.xml`` with one ``<name>`` section per item (see
#: ``readCustomizationCacheFromXml``).
ITEM_FOLDERS: Tuple[Tuple[str, str, int], ...] = (
    ("paints", "paint", TYPE_PAINT),
    ("camouflages", "camouflage", TYPE_CAMOUFLAGE),
    ("decals", "decal", TYPE_DECAL),
    ("modifications", "modification", TYPE_MODIFICATION),
    ("styles", "style", TYPE_STYLE),
)

PRICE_GROUPS_PATH = "customization/priceGroups/list.xml"


def make_int_descr(c_type: int, item_id: int) -> int:
    """The compact descriptor of one customization item."""
    return items_module.make_int_compact_descr_by_id("customizationItem", int(c_type),
                                                     int(item_id))


def item_type_of(compact_descr: int) -> int:
    """The ``cType`` of a customization item's compact descriptor."""
    item_type, c_type, _item_id = items_module.parse_int_compact_descr(int(compact_descr))
    if item_type != items_module.ITEM_TYPES["customizationItem"]:
        raise ValueError("not a customization item: %r" % (compact_descr,))
    return c_type


@dataclass
class C11nItem:
    """One appearance item, with the price the shop charges for it."""

    c_type: int
    id: int
    season: int = SEASON_ALL
    credits: int = 0
    gold: int = 0
    tags: Tuple[str, ...] = ()
    not_in_shop: bool = False
    group_name: str = ""
    historical: bool = False
    #: A style that can only be rented (``<isRent>``): it is bound to the vehicle
    #: it was applied to for ``rent_count`` battles instead of being owned.
    is_rent: bool = False
    rent_count: int = RENT_DEFAULT_BATTLES
    #: ``<usages>`` of a paint: ``((area, cost), ...)`` - how much paint one
    #: region of the tank costs (``PaintItem.usageCosts``).
    usages: Tuple[Tuple[int, int], ...] = ()
    #: A style's outfits, one per season (``StyleItem.outfits``); empty for the
    #: items that are applied by hand.
    outfits: Dict[int, outfit_module.Outfit] = field(default_factory=dict)
    #: ``include`` sections of the item's ``<vehicleFilter>``: the nations,
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
        return [season for season in SEASONS if self.season & season]

    @property
    def usage_costs(self) -> Dict[int, int]:
        return {int(area): int(cost) for area, cost in self.usages}

    def is_vehicle_bound(self) -> bool:
        return TAG_VEHICLE_BOUND in self.tags

    def is_style(self) -> bool:
        return self.c_type == TYPE_STYLE

    def outfit_for_season(self, season: int) -> Optional[outfit_module.Outfit]:
        """The style's outfit for one season (``Style.getOutfit``)."""
        return self.outfits.get(int(season))

    def paint_amount(self, applied_to: int) -> int:
        """``PaintItem.getAmount``: how much paint a region mask needs."""
        return outfit_module.paint_amount(applied_to, self.usage_costs)

    def as_json(self) -> dict:
        payload = {
            "type": self.c_type,
            "id": self.id,
            "season": self.season,
            "credits": self.credits,
            "gold": self.gold,
            "tags": list(self.tags),
            "notInShop": self.not_in_shop,
            "group": self.group_name,
            "historical": bool(self.historical),
            "nations": list(self.nations),
            "levels": list(self.levels),
            "vehicleTags": list(self.vehicle_tags),
        }
        if self.usages:
            payload["usages"] = [[int(area), int(cost)] for area, cost in self.usages]
        if self.is_rent or self.rent_count != RENT_DEFAULT_BATTLES:
            payload["isRent"] = bool(self.is_rent)
            payload["rentCount"] = int(self.rent_count)
        if self.outfits:
            payload["outfits"] = {str(int(season)): outfit.as_dict()
                                  for season, outfit in sorted(self.outfits.items())}
        return payload

    @classmethod
    def from_json(cls, payload: dict) -> "C11nItem":
        outfits = {}
        for season, outfit in (payload.get("outfits") or {}).items():
            outfits[int(season)] = outfit_module.Outfit.from_dict(outfit)
        return cls(
            c_type=int(payload["type"]),
            id=int(payload["id"]),
            season=int(payload.get("season", SEASON_ALL)),
            credits=int(payload.get("credits", 0)),
            gold=int(payload.get("gold", 0)),
            tags=tuple(payload.get("tags") or ()),
            not_in_shop=bool(payload.get("notInShop", False)),
            group_name=str(payload.get("group") or ""),
            historical=bool(payload.get("historical", False)),
            is_rent=bool(payload.get("isRent", False)),
            rent_count=int(payload.get("rentCount", RENT_DEFAULT_BATTLES)),
            usages=tuple((int(entry[0]), int(entry[1]))
                         for entry in payload.get("usages") or ()),
            outfits=outfits,
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

    def styles(self) -> List[C11nItem]:
        return self.items_of(TYPE_STYLE)

    def prices(self) -> Dict[int, Tuple[int, int]]:
        """``compactDescr -> (credits, gold)`` of every priced item."""
        return {compact: item.price for compact, item in self.items.items() if item.priced}

    def not_in_shop(self) -> List[int]:
        return sorted(compact for compact, item in self.items.items() if item.not_in_shop)

    def event_camouflages(self, nations, season: int):
        """The camouflages the installation reserves for the event of a season.

        ``item_defs/customization/*`` marks them with the ``vehicleBound`` tag
        and keeps them out of the shop, three per nation and season; they are
        what a reward hands out (the live server's own reward table is not part
        of the client, so the local one takes the installation's).
        """
        wanted = {int(nation) for nation in nations}
        return [item for item in self.items_of(TYPE_CAMOUFLAGE)
                if item.is_vehicle_bound() and item.not_in_shop
                and int(item.season) == int(season)
                and wanted & set(int(nation) for nation in item.nations)]

    def of_layout_types(self) -> List[C11nItem]:
        return sorted((item for item in self.items.values() if item.c_type in LAYOUT_TYPES),
                      key=lambda item: (item.c_type, item.id))

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
        """``<itemGroup>`` sections -> the items with the price group's price.

        The client reads the price of an item in two steps
        (``c11n_readers._readItems``): first the item's own ``<price>``, then -
        because the item inherited the group's ``<priceGroup>`` or names one
        itself - the price of that price group, which *wins*.  The
        ``notInShop`` flag is the item's own flag or the price group's.
        """
        from .catalog import _read_price

        result: List[C11nItem] = []
        for group in section.subsections("itemGroup"):
            prototype_price, prototype_currency = _read_price(group)
            prototype = {
                "season": outfit_module.read_season(group, SEASON_ALL),
                "group": group.value("priceGroup", "") or "",
                "tags": tuple(group.value_tokens("tags")),
                "historical": group.value_bool("historical", False),
                "usages": _read_usages(group),
                "nations": _read_filter(group, "nations"),
                "levels": _read_filter_int(group, "levels"),
                "vehicle_tags": _read_filter(group, "tags"),
            }
            for entry in group.subsections(section_name):
                item_id = entry.value_int("id", -1)
                if item_id < 0:
                    continue
                price, currency = _read_price(entry)
                if price <= 0:
                    price, currency = prototype_price, prototype_currency
                own_not_in_shop = entry.value_bool("notInShop", False)
                group_name = entry.value("priceGroup", "") or prototype["group"]
                price_group = groups.get(group_name)
                if price_group is not None:
                    # ``_copyPriceForItem``: the group's price replaces the
                    # item's own one and its ``notInShop`` flag is added.
                    price = price_group.credits or price_group.gold
                    currency = "credits" if price_group.credits else "gold"
                    own_not_in_shop = own_not_in_shop or price_group.not_in_shop
                is_rent = entry.value_bool("isRent", False)
                # ``<usages>`` sits on the group (the client reads the prototype
                # with ``PaintXmlReader``), and an item may override one region.
                usages = dict(prototype["usages"])
                usages.update(dict(_read_usages(entry)))
                tags = set(prototype["tags"]) | set(entry.value_tokens("tags"))
                rent_count = entry.value_int("rentCount", RENT_DEFAULT_BATTLES)
                if is_rent:
                    # ``StyleXmlReader``: a rentable style is always vehicle bound.
                    tags.add(TAG_VEHICLE_BOUND)
                result.append(C11nItem(
                    c_type=c_type,
                    id=item_id,
                    season=outfit_module.read_season(entry, prototype["season"]),
                    credits=price if currency == "credits" else 0,
                    gold=price if currency == "gold" else 0,
                    tags=tuple(sorted(tags)),
                    not_in_shop=bool(own_not_in_shop),
                    group_name=group_name,
                    historical=bool(entry.value_bool("historical", prototype["historical"])),
                    is_rent=is_rent,
                    rent_count=max(1, rent_count),
                    usages=tuple(sorted(usages.items())),
                    outfits=outfit_module.read_outfits(entry, item_id) if c_type == TYPE_STYLE else {},
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
# the legacy appearance tables of the shop cache
# ---------------------------------------------------------------------------
#: The appearance items of the *first* customization system never left the
#: installation: ``item_defs/vehicles/common/customization.xml`` holds the
#: shared ones (ids 5001..65535) and ``item_defs/vehicles/<nation>/
#: customization.xml`` the national ones (ids 1..5000), read per nation exactly
#: like ``items.vehicles.Cache.customization`` does.  The emblems of a player
#: are the groups of ``item_defs/vehicles/common/player_emblems.xml``.
LEGACY_COMMON_PATH = "vehicles/common/customization.xml"
LEGACY_NATION_PATH = "vehicles/%s/customization.xml"
LEGACY_EMBLEMS_PATH = "vehicles/common/player_emblems.xml"

#: The two ``idsRange`` arguments of ``_readCustomization``: the common file is
#: read with the high range, a nation's own one with the low range.
LEGACY_COMMON_IDS = (5001, 65535)
LEGACY_NATION_IDS = (1, 5000)


@dataclass
class LegacyCustomization:
    """The legacy appearance tables of the shop cache.

    ``ShopRequester`` reads six tables that belong to the first customization
    system - the camouflages, inscriptions and player emblems with their
    ``priceFactor`` (the multiplier the client applies to the base price of the
    item) and the ones the client's own data keeps out of the shop::

        camouflagePriceFactors[nationID][camouflageID]      -> factor
        notInShopCamouflages[nationID]                      -> {camouflageID}
        inscriptionGroupPriceFactors[nationID][groupName]    -> factor
        notInShopInscriptionGroups[nationID]                -> {groupName}
        playerEmblemGroupPriceFactors[groupName]            -> factor
        notInShopPlayerEmblemGroups                         -> {groupName}

    Every one of them is collected by the client's own item data
    (``common/items/vehicles.py::_readCustomization``/``_readPlayerEmblems``
    fill ``items.pricesToCollect``), so the numbers here are the installation's
    and not the server's guess.  The keys are the client's own - camouflage ids
    and group names, *not* compact descriptors - because that is how the
    requesters index them.
    """

    camouflage_price_factors: Dict[int, Dict[int, float]] = field(default_factory=dict)
    not_in_shop_camouflages: Dict[int, set] = field(default_factory=dict)
    inscription_group_price_factors: Dict[int, Dict[str, float]] = field(default_factory=dict)
    not_in_shop_inscription_groups: Dict[int, set] = field(default_factory=dict)
    player_emblem_group_price_factors: Dict[str, float] = field(default_factory=dict)
    not_in_shop_player_emblem_groups: set = field(default_factory=set)

    # -- accessors of the shop cache ------------------------------------
    def camouflage_factors(self, size: int) -> List[dict]:
        """``camouflagePriceFactors``: one dictionary per nation id."""
        return [dict(self.camouflage_price_factors.get(nation_id, {})) for nation_id in range(size)]

    def camouflage_hiddens(self, size: int) -> List[set]:
        return [set(self.not_in_shop_camouflages.get(nation_id, ())) for nation_id in range(size)]

    def inscription_factors(self, size: int) -> List[dict]:
        return [dict(self.inscription_group_price_factors.get(nation_id, {}))
                for nation_id in range(size)]

    def inscription_hiddens(self, size: int) -> List[set]:
        return [set(self.not_in_shop_inscription_groups.get(nation_id, ()))
                for nation_id in range(size)]

    def emblem_factors(self) -> Dict[str, float]:
        return dict(self.player_emblem_group_price_factors)

    def emblem_hiddens(self) -> set:
        return set(self.not_in_shop_player_emblem_groups)

    @property
    def camo_count(self) -> int:
        return sum(len(table) for table in self.camouflage_price_factors.values())

    @property
    def emblem_group_count(self) -> int:
        return len(self.player_emblem_group_price_factors)

    # -- the catalogue file ---------------------------------------------
    def to_json(self) -> dict:
        return {
            "camouflages": [
                {"nation": nation_id,
                 "factors": {str(camo_id): factor for camo_id, factor in sorted(table.items())},
                 "notInShop": sorted(self.not_in_shop_camouflages.get(nation_id, ()))}
                for nation_id, table in sorted(self.camouflage_price_factors.items())
            ],
            "inscriptionGroups": [
                {"nation": nation_id,
                 "factors": {name: factor for name, factor in sorted(table.items())},
                 "notInShop": sorted(self.not_in_shop_inscription_groups.get(nation_id, ()))}
                for nation_id, table in sorted(self.inscription_group_price_factors.items())
            ],
            "emblemGroups": {
                "factors": {name: factor for name, factor
                            in sorted(self.player_emblem_group_price_factors.items())},
                "notInShop": sorted(self.not_in_shop_player_emblem_groups),
            },
        }

    @classmethod
    def from_json(cls, payload: dict) -> "LegacyCustomization":
        payload = payload or {}
        camouflages: Dict[int, Dict[int, float]] = {}
        hidden_camouflages: Dict[int, set] = {}
        for entry in payload.get("camouflages") or ():
            nation_id = int(entry["nation"])
            camouflages[nation_id] = {int(camo_id): float(factor)
                                      for camo_id, factor in (entry.get("factors") or {}).items()}
            hidden_camouflages[nation_id] = {int(camo_id)
                                             for camo_id in entry.get("notInShop") or ()}
        inscriptions: Dict[int, Dict[str, float]] = {}
        hidden_inscriptions: Dict[int, set] = {}
        for entry in payload.get("inscriptionGroups") or ():
            nation_id = int(entry["nation"])
            inscriptions[nation_id] = {str(name): float(factor)
                                       for name, factor in (entry.get("factors") or {}).items()}
            hidden_inscriptions[nation_id] = {str(name)
                                             for name in entry.get("notInShop") or ()}
        emblems = payload.get("emblemGroups") or {}
        return cls(
            camouflage_price_factors=camouflages,
            not_in_shop_camouflages=hidden_camouflages,
            inscription_group_price_factors=inscriptions,
            not_in_shop_inscription_groups=hidden_inscriptions,
            player_emblem_group_price_factors={str(name): float(factor)
                                               for name, factor in (emblems.get("factors") or {}).items()},
            not_in_shop_player_emblem_groups={str(name) for name in emblems.get("notInShop") or ()},
        )


def _read_camouflages(section, nation_id: int, ids_range: Tuple[int, int],
                      tables: LegacyCustomization) -> None:
    """``_readCamouflage``: the id, its price factor and its shop flag."""
    factors = tables.camouflage_price_factors.setdefault(nation_id, {})
    hidden = tables.not_in_shop_camouflages.setdefault(nation_id, set())
    container = section.subsection("camouflages")
    if container is None:
        return
    for camouflage in container.children:
        camo_id = camouflage.value_int("id", -1)
        if not ids_range[0] <= camo_id <= ids_range[1]:
            continue
        factors[camo_id] = camouflage.value_float("priceFactor", 0.0)
        if camouflage.value_bool("notInShop", False):
            hidden.add(camo_id)


def _read_inscription_groups(section, nation_id: int,
                             tables: LegacyCustomization) -> None:
    """``_readPlayerInscriptions``: one group per child of ``<inscriptions>``."""
    container = section.subsection("inscriptions")
    if container is None:
        return
    factors = tables.inscription_group_price_factors.setdefault(nation_id, {})
    hidden = tables.not_in_shop_inscription_groups.setdefault(nation_id, set())
    for group in container.children:
        factors[group.name] = group.value_float("priceFactor", 0.0)
        if group.value_bool("notInShop", False):
            hidden.add(group.name)


def load_legacy_customization(source: DefsSource, nations: Sequence[str]) -> LegacyCustomization:
    """Collect the legacy appearance tables of the installation.

    The common file is read for *every* nation (its items are shared, and that
    is how ``items.vehicles.Cache.customization(nationID)`` builds the tables),
    a nation's own file only for that nation.  A nation without any of the files
    simply keeps empty tables - the shop cache then publishes empty dictionaries
    for it, which is what a requester that indexes the array expects.
    """
    tables = LegacyCustomization()
    emblems = source.read_section(LEGACY_EMBLEMS_PATH) if source.has(LEGACY_EMBLEMS_PATH) else None
    if emblems is not None:
        for group in emblems.children:
            tables.player_emblem_group_price_factors[group.name] = group.value_float("priceFactor", 0.0)
            if group.value_bool("notInShop", False):
                tables.not_in_shop_player_emblem_groups.add(group.name)
    common = source.read_section(LEGACY_COMMON_PATH) if source.has(LEGACY_COMMON_PATH) else None
    for nation in nations:
        nation_id = nyc.NATION_INDICES.get(nation)
        if nation_id is None:
            continue
        if common is not None:
            _read_camouflages(common, nation_id, LEGACY_COMMON_IDS, tables)
            _read_inscription_groups(common, nation_id, tables)
        path = LEGACY_NATION_PATH % nation
        if not source.has(path):
            continue
        section = source.read_section(path)
        _read_camouflages(section, nation_id, LEGACY_NATION_IDS, tables)
        _read_inscription_groups(section, nation_id, tables)
    return tables


# ---------------------------------------------------------------------------
# small readers of the item sections
# ---------------------------------------------------------------------------
def _read_usages(entry) -> Tuple[Tuple[int, int], ...]:
    """``<usages>`` of a paint: the cost of covering one region of the tank.

    ``PaintXmlReader._readUsage`` reads ``<componentType>`` (a name or a list of
    ``ApplyArea`` names, or the number) and ``<cost>``, then applies the cost to
    every region the component type names.
    """
    usages: Dict[int, int] = {}
    node = entry.subsection("usages")
    if node is None:
        return ()
    for usage in node.children:
        areas = _read_apply_areas(usage.value_tokens("componentType"))
        if not areas:
            areas = _read_apply_areas([usage.value("componentType", "")])
        if not areas:
            raw = usage.value_int("componentType", -1)
            areas = [area for area in constants.ApplyArea.RANGE if raw > 0 and area & raw]
        cost = usage.value_int("cost", 1)
        for area in areas:
            usages[area] = cost
    return tuple(sorted(usages.items()))


#: ``ApplyArea`` names in the client's XML, longest first so that ``HULL_1`` is
#: not read as ``HULL``.
_APPLY_AREA_NAMES = {
    name: getattr(constants.ApplyArea, name)
    for name in dir(constants.ApplyArea)
    if name.isupper() and isinstance(getattr(constants.ApplyArea, name), int)
}


def _read_apply_areas(tokens) -> List[int]:
    """``ALL`` (or a list of area names / numbers) -> the single regions it covers."""
    areas: List[int] = []
    for token in tokens or ():
        text = str(token).strip()
        if not text:
            continue
        name = text.upper()
        if name in ("ALL", "RANGE"):
            value = constants.ApplyArea.ALL
        elif name in _APPLY_AREA_NAMES:
            value = _APPLY_AREA_NAMES[name]
        elif text.lstrip("-").isdigit():
            value = int(text)
        else:
            continue
        for area in constants.ApplyArea.RANGE:
            if value & area and area not in areas:
                areas.append(area)
    return areas


def _read_season(section, default: int = SEASON_ALL) -> int:
    """``<season>`` - one name or a list of names, ``all`` covers everything."""
    return outfit_module.read_season(section, default)


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
    "LEGACY_COMMON_IDS",
    "LEGACY_COMMON_PATH",
    "LEGACY_EMBLEMS_PATH",
    "LEGACY_NATION_IDS",
    "LEGACY_NATION_PATH",
    "LegacyCustomization",
    "LAYOUT_TYPES",
    "PRICE_GROUPS_PATH",
    "PriceGroup",
    "RENT_DEFAULT_BATTLES",
    "SEASONS",
    "SEASON_ALL",
    "SEASON_DESERT",
    "SEASON_EVENT",
    "SEASON_IDS",
    "SEASON_SUMMER",
    "SEASON_WINTER",
    "SIMPLE_TYPES",
    "TAG_RARE",
    "TAG_VEHICLE_BOUND",
    "TYPE_CAMOUFLAGE",
    "TYPE_DECAL",
    "TYPE_MODIFICATION",
    "TYPE_NAMES",
    "TYPE_PAINT",
    "TYPE_STYLE",
    "item_type_of",
    "load_legacy_customization",
    "make_int_descr",
]
