"""The shop cache the client synchronizes with ``CMD_SYNC_SHOP``.

``AccountCommands.CMD_SYNC_SHOP`` is answered with ``RES_STREAM``: the client
subscribes to the stream (``AccountSyncData``/``SyncController``), the server
sends ``zlib(pickle(shop))`` as a stream and the client's ``Shop`` helper stores
the whole dictionary as its cache.  Everything the lobby reads from that cache
is built here (``client/account_helpers/Shop.py``):

===================  =====================================================
``rev``              cache revision; every price lookup asserts it matches
``sellPriceFactor``  the multiplier of the sell price (copied by the client
                     to ``sellPriceModif``)
``exchangeRate``     credits for one gold (items)
``exchangeRateForShellsAndEqs``
                     credits for one gold (shells and equipment)
``crystalExchangeRate``
                     credits for one crystal
``items``            ``itemPrices`` and the per-type exceptions
``defaults``         what ``ShopRequester`` falls back to when a key is
                     missing from the server answer
===================  =====================================================

The prices themselves come from the player's own ``item_defs`` (the same data
the client shows in its tooltips), so nothing is hard-coded here.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .catalog import Catalog

#: The revision of the shop cache; the client compares it with what it asks for
#: and shows an empty price when they disagree, so it only changes when the
#: prices do.
SHOP_REV = 1

#: ``Shop`` defaults of the live server (``client/account_helpers/Shop.py``
#: reads them straight out of the cache).
SELL_PRICE_FACTOR = 0.5
EXCHANGE_RATE = 400
CRYSTAL_EXCHANGE_RATE = 200

#: Days of the customization rent the client asks a price for.
CUSTOMIZATION_DAYS = 7

#: ``sellPriceFactor``-like configuration the client reads for the garages and
#: the depot: a vehicle is bought for credits or gold, never for crystals.
Price = Tuple[int, int]


def _components_of(vehicle) -> List:
    """Every module of a vehicle: the groups and the guns of every turret."""
    modules = []
    for components in (vehicle.groups or {}).values():
        modules.extend(components)
    for turret_group in vehicle.turrets or ():
        modules.append(turret_group.turret)
        modules.extend(turret_group.guns)
    return modules


def _item_prices(catalog: Catalog) -> Dict[int, Price]:
    """``compactDescr -> (credits, gold)`` of every vehicle and module."""
    prices: Dict[int, Price] = {}
    for vehicle in catalog.vehicles.all():
        prices[vehicle.compact_descr] = (max(0, int(vehicle.price_credits)),
                                         max(0, int(vehicle.price_gold)))
        for component in _components_of(vehicle):
            prices[component.compact_descr] = (max(0, int(component.price_credits)),
                                               max(0, int(component.price_gold)))
    return prices


def build_shop(catalog: Catalog, revision: int = SHOP_REV) -> dict:
    """The whole shop cache for ``CMD_SYNC_SHOP``."""
    item_prices = _item_prices(catalog)
    return {
        "rev": int(revision),
        "sellPriceFactor": SELL_PRICE_FACTOR,
        "exchangeRate": EXCHANGE_RATE,
        "exchangeRateForShellsAndEqs": EXCHANGE_RATE,
        "crystalExchangeRate": CRYSTAL_EXCHANGE_RATE,
        "sellForGold": False,
        "camouflageCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "playerInscriptionCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "playerEmblemCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "premiumCost": {},
        "tankmanCost": (),
        "items": {
            "itemPrices": item_prices,
            "vehiclesRentPrices": {},
            "vehiclesToSellForGold": {},
            "vehicleSellPriceFactors": {},
            "notInShopItems": [],
            "vehiclesNotToBuy": [],
        },
        "defaults": {
            "vehicleSellPriceFactor": SELL_PRICE_FACTOR,
            "tankmanCost": (),
        },
        "restore_config": {},
        "tradeIn": {},
        "goodies": {"goodies": {}, "prices": {}, "notInShop": []},
    }


class ShopBuilder(object):
    """Keeps the (expensive) price table of one installation."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._payload: Optional[dict] = None

    def payload(self) -> dict:
        if self._payload is None:
            self._payload = build_shop(self.catalog)
        return self._payload

    def revision(self) -> int:
        return int(self.payload()["rev"])


__all__ = ["EXCHANGE_RATE", "SELL_PRICE_FACTOR", "SHOP_REV", "ShopBuilder", "build_shop"]
