"""The shop cache the client synchronizes with ``CMD_SYNC_SHOP``.

``AccountSyncData``/``Shop`` ask for the shop with
``CMD_SYNC_SHOP(clientRev, dataLen, dataCrc)``; the answer is a stream
(``RES_STREAM`` + ``Account.onStreamComplete``) whose payload is the whole shop
dictionary.  Everything the lobby, the depot and the garage read from that cache
is built here:

===================  =====================================================
``rev``              cache revision; ``getSellPrice`` asserts the revision it
                     was asked for matches the cache
``sellPriceFactor``  ``Shop.__onSyncDataReceived`` copies it to
                     ``sellPriceModif``, the multiplier of the sell price
``exchangeRate``     credits per gold
``exchangeRateForShellsAndEqs``
                     the same for shells and equipment
``crystalExchangeRate``
                     credits per crystal
``items``            ``itemPrices`` (every vehicle, module, shell, equipment
                     and optional device) plus the per-nation customization
                     tables a requester indexes without guarding an empty one
``defaults``         what ``ShopCommonStats`` falls back to for a missing key
===================  =====================================================

Prices come from the player's own ``item_defs`` (the same tables the client
shows in its tooltips), so nothing is hard-coded except the values the client
itself falls back to - the shop publishes exactly those, which is why the price
a dialog shows and the price the local server charges are the same number.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .catalog import Catalog

#: The revision of the shop cache; every price lookup asserts it, so it only
#: changes when the prices do.
SHOP_REV = 1

#: ``ShopRequester``'s own fallbacks (``_DEFAULT_SELL_PRICE_MODIF``,
#: ``_DEFAULT_EXCHANGE_RATE``, ``_DEFAULT_CRYSTAL_EXCHANGE_RATE``).
SELL_PRICE_FACTOR = 0.5
EXCHANGE_RATE = 400
CRYSTAL_EXCHANGE_RATE = 200

#: ``ShopCommonStats.getNextSlotPrice`` reads ``(firstSlot, [price, ...])`` and
#: ``getNextBerthPackPrice`` reads ``(firstBerth, berthsPerPack, [price, ...])``;
#: both lists end with the price of every further pack (``item_price.py``).
SLOTS_PRICES = (0, [300])
BERTHS_PRICES = (0, 1, [300])

#: ``ShopCommonStats``' own defaults for the crew shop (gold amounts).
CHANGE_ROLE_COST = 600
PASSPORT_CHANGE_COST = 600
FEMALE_PASSPORT_CHANGE_COST = 600

#: The three recruitment choices of ``Shop.buyTankman``: what the client's
#: recruiter window lists is exactly this table, indexed by
#: ``tmanCostTypeIdx`` of ``CMD_BUY_TMAN``/``CMD_BUY_VEHICLE``.
TANKMAN_COST = (
    {"credits": 0, "gold": 0, "roleLevel": 50, "baseRoleLoss": 0.2,
     "classChangeRoleLoss": 0.2, "isPremium": False},
    {"credits": 20000, "gold": 0, "roleLevel": 75, "baseRoleLoss": 0.1,
     "classChangeRoleLoss": 0.1, "isPremium": False},
    {"credits": 0, "gold": 200, "roleLevel": 100, "baseRoleLoss": 0.0,
     "classChangeRoleLoss": 0.0, "isPremium": True},
)

#: ``<dropSkillsCost>`` of the client's ``Shop``: what forgetting every skill of
#: a crew member costs and how much of the experience comes back (index 2 is the
#: gold option of ``TankmanDropSkills``).
DROP_SKILLS_COST = (
    {"credits": 0, "gold": 0, "xpReuseFraction": 0.0},
    {"credits": 20000, "gold": 0, "xpReuseFraction": 0.25},
    {"credits": 0, "gold": 200, "xpReuseFraction": 1.0},
)

#: ``ShopRequester`` defaults for the premium account packages: days -> gold.
PREMIUM_COST = {1: 250, 3: 650, 7: 1250, 30: 2500, 180: 10000, 360: 18000}

#: What taking a complex optional device off costs (``paidRemovalCost``) and
#: what a "deluxe" one costs (``paidDeluxeRemovalCost``).
DEVICE_REMOVAL_COST = {"gold": 10}
DELUXE_REMOVAL_COST = {"crystal": 200}

#: ``ShopCommonStats.freeXPConversion`` = ``(discrecity, cost)``.
FREE_XP_CONVERSION = (25, 1)
FREE_XP_TO_TMAN_XP_RATE = 10

#: ``RefSystem.__update`` indexes ``posByXPinTeam`` directly and its helpers
#: index the other three values once the dict is not empty, so the disabled
#: shape always travels together.
REF_SYSTEM = {
    "periods": 0,
    "maxReferralXPPool": 0,
    "maxNumberOfReferrals": 0,
    "posByXPinTeam": 0,
}

#: ``accounts`` are indexed by nation id, so the tables must cover every nation
#: the client knows (``nations.NAMES`` has ten of them, ``NONE_INDEX`` is 15).
NATION_TABLE_SIZE = 16

#: Customizations, emblems and inscriptions are rentable for this many days.
CUSTOMIZATION_DAYS = 7

Price = Tuple[int, int]


def _empty_items(prices: Dict[int, Price]) -> dict:
    """The ``items`` section: prices plus every table a requester indexes."""
    return {
        "itemPrices": dict(prices),
        "vehiclesRentPrices": {},
        "vehiclesToSellForGold": set(),
        "vehicleSellPriceFactors": {},
        "notInShopItems": set(),
        "vehiclesNotToBuy": set(),
        # Customization 2.0 and the legacy requester index these arrays by
        # nation id without a default, so they always carry every nation.
        "camouflagePriceFactors": [{} for _index in range(NATION_TABLE_SIZE)],
        "notInShopCamouflages": [set() for _index in range(NATION_TABLE_SIZE)],
        "inscriptionGroupPriceFactors": [{} for _index in range(NATION_TABLE_SIZE)],
        "notInShopInscriptionGroups": [set() for _index in range(NATION_TABLE_SIZE)],
        "playerEmblemGroupPriceFactors": {},
        "notInShopPlayerEmblemGroups": set(),
        "vehicleCamouflagePriceFactors": {},
        "vehicleHornPriceFactors": {},
    }


def _goodies_table() -> dict:
    """The goodies section: the definitions, their prices and the exclusions."""
    return {"goodies": {}, "prices": {}, "notInShop": []}


def build_shop(catalog: Catalog, revision: int = SHOP_REV) -> dict:
    """The whole shop cache for ``CMD_SYNC_SHOP``.

    Every item the client can buy is priced from the installation's own
    ``item_defs``: vehicles and their modules, shells, equipment, optional
    devices and the appearance items (paints, camouflages, decals, modifications
    and styles).  ``defaults`` publishes the same tables, so a dialog that reads
    the fallback shows exactly what the local server charges.
    """
    prices = catalog.vehicles.item_prices()
    # ``FittingItem`` reads the price of a customization item out of the shop
    # cache like every other item (``shop.getItem``/``shop.defaults.getItemPrice``)
    # and the customization window prices its baskets with it, so the appearance
    # items travel in the same table.
    prices.update(catalog.customization.prices())
    not_in_shop = set(catalog.customization.not_in_shop())
    items = _empty_items(prices)
    defaults = _empty_items(prices)
    if not_in_shop:
        # ``DefaultShopRequester.getHiddens`` is the list the UI greys out; the
        # appearance items the client's own data marks ``notInShop`` (the legacy
        # camouflages of the event, the ``all`` styles) are not for sale.
        items["notInShopItems"] = set(not_in_shop)
        defaults["notInShopItems"] = set(not_in_shop)
    defaults.update(
        {
            "slotsPrices": SLOTS_PRICES,
            "berthsPrices": BERTHS_PRICES,
            "tankmanCost": TANKMAN_COST,
            "premiumCost": dict(PREMIUM_COST),
            "dropSkillsCost": _drop_skills_table(),
            "paidRemovalCost": dict(DEVICE_REMOVAL_COST),
            "paidDeluxeRemovalCost": dict(DELUXE_REMOVAL_COST),
            "freeXPConversion": FREE_XP_CONVERSION,
            "freeXPToTManXPRate": FREE_XP_TO_TMAN_XP_RATE,
            "goodies": _goodies_table(),
            "restore_config": _restore_config(),
        }
    )
    return {
        "rev": int(revision),
        "sellPriceFactor": SELL_PRICE_FACTOR,
        "sellPriceModif": SELL_PRICE_FACTOR,
        "exchangeRate": EXCHANGE_RATE,
        "exchangeRateForShellsAndEqs": EXCHANGE_RATE,
        "crystalExchangeRate": CRYSTAL_EXCHANGE_RATE,
        "isEnabledBuyingGoldShellsForCredits": True,
        "isEnabledBuyingGoldEqsForCredits": True,
        "items": items,
        "defaults": defaults,
        # The client reads the shop's own copy of these tables when it cannot
        # find the key in ``items``/``defaults``.
        "slotsPrices": SLOTS_PRICES,
        "berthsPrices": BERTHS_PRICES,
        "tankmanCost": TANKMAN_COST,
        "premiumCost": dict(PREMIUM_COST),
        "changeRoleCost": CHANGE_ROLE_COST,
        "passportChangeCost": PASSPORT_CHANGE_COST,
        "femalePassportChangeCost": FEMALE_PASSPORT_CHANGE_COST,
        "dropSkillsCost": _drop_skills_table(),
        "paidRemovalCost": dict(DEVICE_REMOVAL_COST),
        "paidDeluxeRemovalCost": dict(DELUXE_REMOVAL_COST),
        "freeXPConversion": FREE_XP_CONVERSION,
        "freeXPToTManXPRate": FREE_XP_TO_TMAN_XP_RATE,
        "dailyXPFactor": 2,
        "refSystem": dict(REF_SYSTEM),
        "restore_config": _restore_config(),
        "tradeIn": {},
        "camouflageCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "playerInscriptionCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "playerEmblemCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "goodies": _goodies_table(),
        "achievementsReward": {},
    }


def _drop_skills_table() -> dict:
    """``{index: price}`` of ``CMD_TMAN_DROP_SKILLS`` (the client's own indices)."""
    return {index: dict(cost) for index, cost in enumerate(DROP_SKILLS_COST)}


def _restore_config() -> dict:
    """The recycle bin's windows and prices.

    ``ShopRequester.tankmenRestoreConfig`` reads ``goldDuration``/``goldCost``
    when the client is Chinese and ``creditsDuration``/``creditsCost`` otherwise,
    and ``ItemsRequester.getTankmen`` throws a dismissed crew member away once
    ``billableDuration`` is over - so both currencies are published and the
    restore is free inside the window the client shows as free (the local server
    never takes gold for something the player cannot see the price of).
    """
    return {
        "tankmen": {"freeDuration": 2592000, "goldDuration": 2592000,
                    "goldCost": 100, "creditsDuration": 2592000,
                    "creditsCost": 100000, "limit": 100},
        "vehicles": {"premiumDuration": 604800, "actionCooldown": 0,
                     "restorePriceModif": 1.0},
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

    def price(self, compact_descr: int) -> Optional[Price]:
        """``(credits, gold)`` of one item, or ``None`` when it is not priced."""
        return self.payload()["items"]["itemPrices"].get(int(compact_descr))


__all__ = [
    "CHANGE_ROLE_COST",
    "DROP_SKILLS_COST",
    "CRYSTAL_EXCHANGE_RATE",
    "EXCHANGE_RATE",
    "FREE_XP_CONVERSION",
    "PREMIUM_COST",
    "SELL_PRICE_FACTOR",
    "SHOP_REV",
    "ShopBuilder",
    "TANKMAN_COST",
    "build_shop",
]
