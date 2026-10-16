"""The appearance side of the account: the depot and the outfits.

The client knows two halves of customization, and both live in
``inventory[GUI_ITEM_TYPE.CUSTOMIZATION]`` (``constants.CustomizationInvData``):

======================  =====================================================
``ITEMS`` (1)           ``{cType: {itemID: {vehIntCD: count}}}`` - the depot.
                        The count under the key ``0`` is what the player owns
                        without binding it to a tank, every other key is the
                        *vehicle type* the item is bound to
                        (``Customization.boundInventoryCount`` is indexed by
                        ``vehicle.intCD``).
``OUTFITS`` (2)         ``{vehIntCD: {season: (compDescr, isEnabled)}}`` - what
                        is mounted on a vehicle.  The client reads the
                        hand-made outfits under the four seasons and the
                        ``SeasonType.ALL`` entry as a *style*: if it is enabled
                        it takes the ``styleId`` out of the descriptor and
                        builds the seasonal outfits from the style's own data
                        (``Vehicle._parseStyledOutfits``).
``UNLOCKS`` (3)         the appearance items a token unlocked - unused by this
                        build, published empty so the client finds the key.
======================  =====================================================

This module owns that section:

* :class:`CustomizationInventory` - the data, its JSON form and the diff
  sections the client applies,
* :class:`CustomizationKeeper` - the four commands of the customization window
  (``CMD_BUY_C11N_ITEMS``, ``CMD_SELL_C11N_ITEMS``, ``CMD_VEH_APPLY_STYLE``,
  ``CMD_VEH_APPLY_OUTFIT``), including the money and the depot bookkeeping that
  goes with them.

The rules the client follows and this module mirrors:

* an item is bought *unbound*, or - when its ``vehicleBound`` tag says so, which
  is what a rented style carries - for the vehicle the request names,
* mounting an outfit takes the items out of the depot and puts them on the
  vehicle; whatever the depot is short of is bought at the item's price, which
  is what the "buy and install" button of the client means
  (``Shop.buyAndEquipOutfit``),
* replacing an outfit gives its items back to the depot, so trying outfits on
  loses nothing,
* a *rentable* style (``<isRent>``) is not owned but rented for ``rentCount``
  battles: the client stores the battles left as the bound count of the style
  (``Style.getRentInfo``).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..game import c11n_constants as cc
from ..game import c11n_outfit as outfit_module
from ..game import customization as c11n
from ..game import items as items_module
from . import commands as cmd
from .purchases import PurchaseError

#: ``constants.GUI_ITEM_TYPE.CUSTOMIZATION`` - the inventory section.
CUSTOMIZATION_INVENTORY = items_module.GUI_ITEM_TYPE.CUSTOMIZATION
#: ``common/items.ITEM_TYPES['customizationItem']`` - the depot item type.
CUSTOMIZATION_ITEM = items_module.ITEM_TYPES["customizationItem"]

#: ``constants.CustomizationInvData``.
ITEMS = 1
OUTFITS = 2
UNLOCKS = 3

#: The key of the depot entry that is not bound to any vehicle.
UNBOUND = 0

#: The error reasons of the customization processors
#: (``makeI18nError('customization/{}'.format(msg))``); when the answer carries
#: no reason at all the client falls back to ``customization/server_error``.
ERROR_NOT_ENOUGH_MONEY = "not_enough_money"
ERROR_WRONG_ARGS = "wrong_args"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_SERVER = "server_error"


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


class CustomizationInventory:
    """The ``inventory[12]`` section: the depot and the outfits of one account."""

    def __init__(self):
        #: ``{cType: {itemID: {vehIntCD: count}}}`` (``CustomizationInvData.ITEMS``).
        self.items: Dict[int, Dict[int, Dict[int, int]]] = {}
        #: ``{vehIntCD: {season: (descriptor, isEnabled)}}``.
        self.outfits: Dict[int, Dict[int, Tuple[bytes, bool]]] = {}
        #: What the last diff published (see :meth:`sections`).
        self._published: Dict[int, Dict[int, Dict[int, int]]] = {}
        self._published_outfits: Dict[int, Dict[int, Tuple[bytes, bool]]] = {}

    # -- the depot ------------------------------------------------------
    def count(self, c_type: int, item_id: int, vehicle_type: int = UNBOUND) -> int:
        """How many of one item the account owns for a vehicle (0 = unbound)."""
        return _as_int(self.items.get(_as_int(c_type), {})
                       .get(_as_int(item_id), {})
                       .get(_as_int(vehicle_type), 0))

    def total(self, c_type: int, item_id: int, vehicle_type: int = None) -> int:
        """What the account owns of one item: unbound plus, optionally, bound.

        With ``vehicle_type=None`` *every* binding is counted (the depot view of
        the item); with a vehicle type only the unbound pile and that vehicle's.
        """
        by_vehicle = self.items.get(_as_int(c_type), {}).get(_as_int(item_id), {})
        if vehicle_type is None:
            return sum(_as_int(value) for value in by_vehicle.values())
        return (self.count(c_type, item_id, UNBOUND)
                + (self.count(c_type, item_id, vehicle_type)
                   if _as_int(vehicle_type) != UNBOUND else 0))

    def add(self, c_type: int, item_id: int, count: int = 1,
            vehicle_type: int = UNBOUND) -> int:
        """Put ``count`` items into the depot; returns the new count."""
        c_type, item_id, vehicle_type = _as_int(c_type), _as_int(item_id), _as_int(vehicle_type)
        count = _as_int(count)
        if count <= 0:
            return self.count(c_type, item_id, vehicle_type)
        by_item = self.items.setdefault(c_type, {}).setdefault(item_id, {})
        by_item[vehicle_type] = _as_int(by_item.get(vehicle_type, 0)) + count
        return by_item[vehicle_type]

    def remove(self, c_type: int, item_id: int, count: int = 1,
               vehicle_type: int = UNBOUND) -> int:
        """Take ``count`` items out of the depot; the entry goes away at zero."""
        c_type, item_id, vehicle_type = _as_int(c_type), _as_int(item_id), _as_int(vehicle_type)
        by_item = self.items.get(c_type, {}).get(item_id)
        if not by_item:
            return 0
        remaining = _as_int(by_item.get(vehicle_type, 0)) - _as_int(count)
        if remaining > 0:
            by_item[vehicle_type] = remaining
        else:
            remaining = 0
            by_item.pop(vehicle_type, None)
            if not by_item:
                by_type = self.items.get(c_type, {})
                by_type.pop(item_id, None)
                if not by_type:
                    self.items.pop(c_type, None)
        return remaining

    def take(self, c_type: int, item_id: int, count: int = 1,
             vehicle_type: int = UNBOUND) -> int:
        """Take what is there - the unbound pile first; returns how much."""
        wanted = max(0, _as_int(count))
        taken = 0
        free = min(wanted, self.count(c_type, item_id, UNBOUND))
        if free:
            self.remove(c_type, item_id, free, UNBOUND)
            taken += free
        if taken < wanted and _as_int(vehicle_type) != UNBOUND:
            bound = min(wanted - taken, self.count(c_type, item_id, vehicle_type))
            if bound:
                self.remove(c_type, item_id, bound, vehicle_type)
                taken += bound
        return taken

    def item_ids(self) -> Iterable[Tuple[int, int]]:
        """Every ``(cType, itemID)`` the depot knows."""
        for c_type, by_item in self.items.items():
            for item_id in by_item:
                yield int(c_type), int(item_id)

    # -- the outfits ----------------------------------------------------
    def outfit(self, vehicle_type: int, season: int) -> Optional[Tuple[bytes, bool]]:
        """The ``(descriptor, isEnabled)`` mounted for one vehicle and season."""
        return self.outfits.get(_as_int(vehicle_type), {}).get(_as_int(season))

    def outfit_descr(self, vehicle_type: int, season: int) -> Optional[bytes]:
        entry = self.outfit(vehicle_type, season)
        return entry[0] if entry else None

    def set_outfit(self, vehicle_type: int, season: int, descr: bytes,
                   is_enabled: bool = True) -> None:
        self.outfits.setdefault(_as_int(vehicle_type), {})[_as_int(season)] = (
            bytes(descr), bool(is_enabled))

    def clear_outfit(self, vehicle_type: int, season: int) -> Optional[Tuple[bytes, bool]]:
        """Drop one mounted outfit; returns what was mounted."""
        by_season = self.outfits.get(_as_int(vehicle_type))
        if not by_season:
            return None
        removed = by_season.pop(_as_int(season), None)
        if not by_season:
            self.outfits.pop(_as_int(vehicle_type), None)
        return removed

    def outfits_of(self, vehicle_type: int) -> Dict[int, Tuple[bytes, bool]]:
        return dict(self.outfits.get(_as_int(vehicle_type), {}))

    def vehicles_with_outfits(self) -> List[int]:
        return sorted(self.outfits)

    # -- the diff sections ----------------------------------------------
    def full_sections(self) -> dict:
        """The whole section, as a *full* sync publishes it."""
        self.mark_published()
        return {
            ITEMS: {int(c_type): {int(item_id): {int(vehicle): _as_int(count)
                                                 for vehicle, count in by_vehicle.items()}
                                  for item_id, by_vehicle in by_item.items()}
                    for c_type, by_item in self.items.items()},
            OUTFITS: {int(vehicle): {int(season): (bytes(descr), bool(enabled))
                                     for season, (descr, enabled) in by_season.items()}
                      for vehicle, by_season in self.outfits.items()},
            UNLOCKS: {},
        }

    def sections(self, item_types: Sequence[Tuple[int, int]] = (),
                 vehicle_types: Sequence[int] = ()) -> dict:
        """The delta of the named items and vehicles.

        The client merges the section into its cache with ``synchronizeDicts``,
        so a count that dropped to zero travels as an explicit ``0`` - leaving
        the key out would keep the old count in the client and its customization
        window would offer an item the account does not own any more.
        """
        section: Dict[int, dict] = {}
        item_section: Dict[int, dict] = {}
        for c_type, item_id in item_types:
            c_type, item_id = _as_int(c_type), _as_int(item_id)
            current = dict(self.items.get(c_type, {}).get(item_id, {}))
            published = self._published.get(c_type, {}).get(item_id, {})
            delta = {vehicle: _as_int(count) for vehicle, count in current.items()
                     if published.get(vehicle) != _as_int(count)}
            delta.update({vehicle: 0 for vehicle in published if vehicle not in current})
            if delta:
                item_section.setdefault(c_type, {})[item_id] = delta
            by_item = self._published.setdefault(c_type, {})
            if current:
                by_item[item_id] = current
            else:
                by_item.pop(item_id, None)
                if not by_item:
                    self._published.pop(c_type, None)
        if item_section:
            section[ITEMS] = item_section
        outfit_section: Dict[int, dict] = {}
        for vehicle_type in vehicle_types:
            vehicle_type = _as_int(vehicle_type)
            current = dict(self.outfits.get(vehicle_type, {}))
            published = self._published_outfits.get(vehicle_type, {})
            delta: Dict[int, object] = {
                season: (bytes(descr), bool(enabled))
                for season, (descr, enabled) in current.items()
                if published.get(season) != (bytes(descr), bool(enabled))}
            for season in published:
                if season not in current:
                    # ``synchronizeDicts`` removes a key whose value is ``None``.
                    delta[season] = None
            if delta:
                outfit_section[vehicle_type] = delta
            if current:
                self._published_outfits[vehicle_type] = current
            else:
                self._published_outfits.pop(vehicle_type, None)
        if outfit_section:
            section[OUTFITS] = outfit_section
        return section

    def mark_published(self) -> None:
        """Remember the current state as published (what a full sync means)."""
        self._published = {c_type: {item_id: dict(by_vehicle)
                                    for item_id, by_vehicle in by_item.items()}
                           for c_type, by_item in self.items.items()}
        self._published_outfits = {vehicle: dict(by_season)
                                   for vehicle, by_season in self.outfits.items()}

    # -- the account file -----------------------------------------------
    def to_json(self) -> dict:
        return {
            "items": {
                str(c_type): {str(item_id): {str(vehicle): _as_int(count)
                                             for vehicle, count in by_vehicle.items()}
                              for item_id, by_vehicle in by_item.items()}
                for c_type, by_item in self.items.items()
            },
            # A descriptor is a binary string; it is kept readable in JSON the
            # same way the tankman descriptors are (``latin-1`` round trips
            # every byte).
            "outfits": {
                str(vehicle): {str(season): [descr.decode("latin-1"), bool(enabled)]
                               for season, (descr, enabled) in by_season.items()}
                for vehicle, by_season in self.outfits.items()
            },
        }

    @classmethod
    def from_json(cls, payload: dict) -> "CustomizationInventory":
        inventory = cls()
        for c_type, by_item in (payload.get("items") or {}).items():
            for item_id, by_vehicle in (by_item or {}).items():
                for vehicle, count in (by_vehicle or {}).items():
                    count = _as_int(count)
                    if count:
                        inventory.add(c_type, item_id, count, _as_int(vehicle))
        for vehicle, by_season in (payload.get("outfits") or {}).items():
            for season, entry in (by_season or {}).items():
                if not entry:
                    continue
                descr = entry[0]
                enabled = entry[1] if len(entry) > 1 else True
                inventory.set_outfit(vehicle, season, _descr_bytes(descr), bool(enabled))
        inventory.mark_published()
        return inventory


def _descr_bytes(value) -> bytes:
    """The bytes of a descriptor that travelled through JSON."""
    if isinstance(value, bytes):
        return value
    return str(value or "").encode("latin-1")


class CustomizationKeeper:
    """The customization commands of the account, with the money they cost."""

    def __init__(self, state, shopkeeper=None):
        from .purchases import Shopkeeper

        self.state = state
        self.shop = shopkeeper if shopkeeper is not None else Shopkeeper(state)
        self.catalogue: c11n.CustomizationCatalog = state.catalog.customization
        self.inventory: CustomizationInventory = state.customization

    # -- helpers --------------------------------------------------------
    def item(self, compact_descr: int) -> c11n.C11nItem:
        """The catalogue entry of a customization item."""
        item = self.catalogue.get(compact_descr)
        if item is None:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_NOT_AVAILABLE)
        return item

    def _item_parts(self, compact_descr: int) -> Tuple[int, int]:
        """``(cType, itemID)`` of a compact descriptor."""
        _type, c_type, item_id = items_module.parse_int_compact_descr(compact_descr)
        return int(c_type), int(item_id)

    def sell_value(self, compact_descr: int, count: int = 1) -> int:
        """Credits refunded for one item, as ``Shop.getSellPrice`` computes them."""
        return self.shop.sell_value(int(compact_descr), CUSTOMIZATION_ITEM, count)

    def vehicle_type_descr(self, vehicle_inv_id: int) -> int:
        """The *type* compact descriptor of a garage vehicle (0 when unknown).

        The depot of the client is indexed by the vehicle *type*, not by the
        inventory id: its ``Customization.fullInventoryCount`` looks up
        ``boundInventoryCount.get(vehicle.intCD, 0)``, and ``Vehicle.intCD`` is
        the type descriptor - while the requests carry the inventory id.
        """
        inv_id = _as_int(vehicle_inv_id)
        if inv_id <= 0:
            return 0
        entry = self.state.vehicles.get(inv_id)
        if entry is None:
            return 0
        return int(entry.compact_descr)

    def depot_key(self, vehicle_inv_id: int, item: c11n.C11nItem) -> int:
        """The depot entry an item belongs to: the vehicle for a bound item."""
        if not item.is_vehicle_bound():
            return UNBOUND
        return self.vehicle_type_descr(vehicle_inv_id) or UNBOUND

    def _charge_currency(self, item: c11n.C11nItem, count: int = 1) -> None:
        """Charge the price of an item the way the shop does (gold wins)."""
        credits, gold = item.price
        if gold > 0:
            self.shop.charge(gold=gold * count)
        else:
            self.shop.charge(credits=credits * count)

    def local_diff(self, item_types=(), vehicle_types=()) -> dict:
        """The account diff a customization change publishes."""
        inventory = self.state.partial_inventory_diff()
        section = self.inventory.sections(item_types=item_types,
                                         vehicle_types=vehicle_types)
        if section:
            inventory[CUSTOMIZATION_INVENTORY] = section
        return self.state.incremental_diff(extra={
            "inventory": inventory,
            "economics": self.state.economics_diff(),
            "stats": self.state.purchase_stats_diff(),
        })

    def _outcome(self, item_types=(), vehicle_types=(), ext=None) -> dict:
        return {
            "code": cmd.RES_SUCCESS,
            "ext": ext,
            "diff": self.local_diff(item_types, vehicle_types),
        }

    # -- CMD_BUY_C11N_ITEMS (118) ---------------------------------------
    def buy(self, vehicle_inv_id: int, wanted: Sequence[Tuple[int, int]]) -> dict:
        """Buy appearance items; the request is ``[rev, vehInvID, intCD, count…]``.

        The pairs come from the client's ``{intCD: count}`` table
        (``Shop.buyCustomizations``), so an item appears once, with the number of
        copies the player chose (every region of a paint needs its own copy).
        """
        if not wanted:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        if len(wanted) > cc.MAX_ITEMS_FOR_BUY_OPERATION:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        # ``0`` buys unbound items (the depot view of the window), anything else
        # has to be a vehicle of the garage: the depot of the client is indexed
        # by the vehicle *type*, so an unknown inventory id cannot be served.
        vehicle_inv_id = _as_int(vehicle_inv_id)
        if vehicle_inv_id and not self.vehicle_type_descr(vehicle_inv_id):
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        touched: List[Tuple[int, int]] = []
        for compact_descr, count in wanted:
            item = self.item(compact_descr)
            count = _as_int(count)
            if count <= 0:
                raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
            if item.not_in_shop:
                raise PurchaseError(cmd.RES_NOT_AVAILABLE, ERROR_NOT_AVAILABLE)
            self._charge_currency(item, count)
            self.inventory.add(item.c_type, item.id, count,
                               self.depot_key(vehicle_inv_id, item))
            touched.append((item.c_type, item.id))
        return self._outcome(item_types=touched,
                             vehicle_types=[self.vehicle_type_descr(vehicle_inv_id)])

    # -- CMD_SELL_C11N_ITEMS (117) --------------------------------------
    def sell(self, compact_descr: int, count: int, vehicle_inv_id: int) -> dict:
        """Sell appearance items back (``Shop.sellCustomizations``)."""
        item = self.item(compact_descr)
        count = _as_int(count)
        if count <= 0:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        vehicle_type = self.vehicle_type_descr(vehicle_inv_id)
        if self.inventory.total(item.c_type, item.id, vehicle_type) < count:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_NOT_AVAILABLE)
        self.inventory.take(item.c_type, item.id, count, vehicle_type)
        self.shop.refund(self.sell_value(compact_descr, count))
        return self._outcome(item_types=[(item.c_type, item.id)],
                             vehicle_types=[vehicle_type])

    # -- CMD_VEH_APPLY_STYLE (116) --------------------------------------
    def apply_style(self, vehicle_inv_id: int, style_id: int) -> dict:
        """Buy (or rent) a style and put it on a vehicle (``StyleApplier``).

        A style replaces the hand-made outfit of the vehicle: the client reads
        the ``OUTFITS[SeasonType.ALL]`` entry, and when it is enabled it takes
        the style id out of the descriptor and builds the seasonal outfits from
        the style's own data (``Vehicle._parseStyledOutfits``), so the descriptor
        published here only has to carry the id - the client's ``style.outfits``
        already holds the paintings.
        """
        vehicle_type = self.vehicle_type_descr(vehicle_inv_id)
        if not vehicle_type:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        if _as_int(style_id) == 0:
            # ``StyleApplier`` without a style: take the current one off.
            self.inventory.clear_outfit(vehicle_type, cc.SeasonType.ALL)
            self.state.revision += 1
            return self._outcome(vehicle_types=[vehicle_type],
                                 ext={"styleID": 0, "styleCD": 0})
        style = self.item(c11n.make_int_descr(c11n.TYPE_STYLE, style_id))
        if style.not_in_shop:
            raise PurchaseError(cmd.RES_NOT_AVAILABLE, ERROR_NOT_AVAILABLE)
        if not self.inventory.total(style.c_type, style.id, vehicle_type):
            # Not owned yet: rent it (battles left become the bound count) or buy
            # it (one copy, bound to the vehicle) - whichever the item says.
            self._charge_currency(style)
            self.inventory.add(style.c_type, style.id,
                               style.rent_count if style.is_rent else 1, vehicle_type)
        returned = self._mount_style(vehicle_type, style)
        return self._outcome(item_types=[(style.c_type, style.id)] + returned,
                             vehicle_types=[vehicle_type],
                             ext={"styleID": int(style.id), "styleCD": style.compact_descr})

    def _mount_style(self, vehicle_type: int, style: c11n.C11nItem) -> List[Tuple[int, int]]:
        """Publish a style as the ``ALL`` season outfit of a vehicle.

        The hand-made outfits of the vehicle give their items back to the depot -
        they are not mounted any more - and the caller publishes exactly those
        depot entries.
        """
        returned = self._dismount_custom(vehicle_type)
        component = style.outfits.get(cc.SeasonType.ALL) or outfit_module.Outfit()
        component.style_id = int(style.id)
        self.inventory.set_outfit(vehicle_type, cc.SeasonType.ALL,
                                  outfit_module.make_comp_descr(component), True)
        self.state.revision += 1
        return returned

    def _dismount_custom(self, vehicle_type: int) -> List[Tuple[int, int]]:
        """Take every hand-made outfit off a vehicle and return its items."""
        returned: List[Tuple[int, int]] = []
        for season in list(self.inventory.outfits_of(vehicle_type)):
            if season == cc.SeasonType.ALL:
                continue
            entry = self.inventory.clear_outfit(vehicle_type, season)
            if entry is None:
                continue
            try:
                outfit = outfit_module.parse_outfit_descr(entry[0])
            except outfit_module.OutfitError:
                continue
            returned.extend(self._return_items(outfit, vehicle_type))
        return returned

    def _return_items(self, outfit: outfit_module.Outfit,
                      vehicle_type: int) -> List[Tuple[int, int]]:
        """Give the items of an outfit back to the depot of a vehicle."""
        returned: List[Tuple[int, int]] = []
        for compact, count in outfit_module.items_of_outfit(outfit, self.catalogue).items():
            c_type, item_id = self._item_parts(compact)
            if c_type == c11n.TYPE_STYLE:
                continue
            self.inventory.add(c_type, item_id, count, vehicle_type)
            returned.append((c_type, item_id))
        return returned

    # -- CMD_VEH_APPLY_OUTFIT (119) -------------------------------------
    def apply_outfit(self, vehicle_inv_id: int, season: int, descr) -> dict:
        """Mount a hand-made outfit (``Shop.buyAndEquipOutfit``).

        The outfit arrives as the client's own descriptor string; whatever it
        names that the depot is short of is bought at the item's price - that is
        what the "buy and install" button means - and everything else is taken
        out of the depot for as long as the outfit is mounted.  The items of the
        outfit the vehicle wore before go back to the depot.
        """
        vehicle_type = self.vehicle_type_descr(vehicle_inv_id)
        if not vehicle_type:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        try:
            outfit = outfit_module.parse_outfit_descr(descr)
        except outfit_module.OutfitError:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        season = _as_int(season)
        if season not in cc.SeasonType.SEASONS:
            raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_WRONG_ARGS)
        if outfit.style_id:
            # An outfit that names a style *is* that style: the client builds such
            # an outfit from the style's own data (``Vehicle._parseStyledOutfits``
            # takes the id out of the descriptor), so it is bought or rented the
            # same way ``CMD_VEH_APPLY_STYLE`` does it - never handed out.
            return self.apply_style(vehicle_inv_id, int(outfit.style_id))

        # A style and a hand-made outfit are two ways to dress the same vehicle and
        # the client prefers the style (``Vehicle.getOutfit`` reads the styled
        # outfits first), so mounting a hand-made outfit takes the style off - the
        # style itself stays in the depot and can be put back for free.
        self.inventory.clear_outfit(vehicle_type, cc.SeasonType.ALL)
        # What the vehicle wears now is free again.
        touched: List[Tuple[int, int]] = []
        current = self.inventory.clear_outfit(vehicle_type, season)
        if current is not None:
            try:
                touched.extend(self._return_items(
                    outfit_module.parse_outfit_descr(current[0]), vehicle_type))
            except outfit_module.OutfitError:
                pass

        bought: List[Tuple[int, int]] = []
        for compact, count in sorted(outfit_module.items_of_outfit(outfit, self.catalogue).items()):
            c_type, item_id = self._item_parts(compact)
            item = self.catalogue.get(compact)
            if item is None:
                raise PurchaseError(cmd.RES_WRONG_ARGS, ERROR_NOT_AVAILABLE)
            taken = self.inventory.take(c_type, item_id, count, vehicle_type)
            if taken < count:
                if item.not_in_shop:
                    raise PurchaseError(cmd.RES_NOT_AVAILABLE, ERROR_NOT_AVAILABLE)
                self._charge_currency(item, count - taken)
                bought.append((c_type, item_id))
            touched.append((c_type, item_id))
        self.inventory.set_outfit(vehicle_type, season,
                                  outfit_module.make_comp_descr(outfit), True)
        self.state.revision += 1
        return self._outcome(item_types=touched, vehicle_types=[vehicle_type],
                             ext={"mounted": [list(pair) for pair in touched],
                                  "bought": [list(pair) for pair in bought]})


__all__ = [
    "CUSTOMIZATION_INVENTORY",
    "CUSTOMIZATION_ITEM",
    "CustomizationInventory",
    "CustomizationKeeper",
    "ERROR_NOT_AVAILABLE",
    "ERROR_NOT_ENOUGH_MONEY",
    "ERROR_SERVER",
    "ERROR_WRONG_ARGS",
    "ITEMS",
    "OUTFITS",
    "UNBOUND",
    "UNLOCKS",
]
