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
                     and optional device), the per-nation customization tables
                     a requester indexes without guarding an empty one (the
                     legacy camouflage/inscription/emblem price factors and
                     their hidden lists, ``vehicleCamouflagePriceFactors``) and
                     ``notInShopItems``
``goodies``          the personal discounts of the event (see
                     :mod:`server.game.goodies`)
``achievementsReward``
                     the bond reward of an achievement (disabled: the local
                     server awards no bonds)
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
from .goodies import GoodieTable

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

#: ``<tradeIn>`` of the client's shop cache: swapping an old premium vehicle for a
#: discount on a new one (``gui/game_control/trade_in.py``).  ``sellPriceFactor``
#: is the number the client multiplies the *old* vehicle's gold buy price with
#: (``Vehicle.tradeOffPrice``), ``allowedVehicleLevels`` are the levels both
#: halves of the deal have to be and ``forbiddenVehicles`` is the list of type
#: compacts that can never be traded off.  Premium tanks live on levels 2 to 10,
#: so that is what the local server allows.
TRADE_IN = {
    "sellPriceFactor": 0.5,
    "allowedVehicleLevels": [2, 3, 4, 5, 6, 7, 8, 9, 10],
    "forbiddenVehicles": [],
    "minAcceptableSellPrice": 0,
}

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

#: ``ShopRequester.getAchievementReward`` reads this section and gives up when
#: ``isEnabled`` is false - which is what the local server publishes: bond
#: rewards belong to battle achievements, and there are no battles here.  The
#: shape travels complete anyway, so a reader that indexes ``groups``/``medals``
#: finds the dictionaries instead of ``None``.
ACHIEVEMENTS_REWARD = {"isEnabled": False, "groups": {}, "medals": {}}

#: ``accounts`` are indexed by nation id, so the tables must cover every nation
#: the client knows (``nations.NAMES`` has ten of them, ``NONE_INDEX`` is 15).
NATION_TABLE_SIZE = 16

#: Customizations, emblems and inscriptions are rentable for this many days.
CUSTOMIZATION_DAYS = 7

Price = Tuple[int, int]


def _items_section(catalog: Catalog, prices: Dict[int, Price]) -> dict:
    """The ``items`` section: prices plus every table a requester indexes.

    The appearance tables are the installation's own: the legacy camouflage,
    inscription and player emblem price factors with their ``notInShop`` lists
    come from ``vehicles/common/customization.xml``, ``vehicles/<nation>/
    customization.xml`` and ``common/player_emblems.xml`` (exactly the files the
    client's item data collects into ``pricesToCollect``), and
    ``vehicleCamouflagePriceFactors`` is the per-vehicle
    ``<camouflage><priceFactor>``.  A requester indexes the nation arrays
    directly, so they always carry every nation id.
    """
    legacy = catalog.legacy_customization
    not_in_shop = catalog.not_in_shop_items()
    return {
        "itemPrices": dict(prices),
        # The installation's ``list.xml`` has no ``<rent>`` section for any
        # vehicle and no ``sellPriceFactor``/``sellForGold``/``cannotBeBought``
        # flag either, so these four tables are exactly what the client's own
        # item data would collect from this installation: vehicles are sold at
        # the shop's ``sellPriceFactor`` for credits.
        "vehiclesRentPrices": {},
        "vehiclesToSellForGold": set(),
        "vehicleSellPriceFactors": {},
        "vehiclesNotToBuy": set(),
        "notInShopItems": set(not_in_shop),
        # Customization 2.0 and the legacy requester index these arrays by
        # nation id without a default, so they always carry every nation.
        "camouflagePriceFactors": legacy.camouflage_factors(NATION_TABLE_SIZE),
        "notInShopCamouflages": legacy.camouflage_hiddens(NATION_TABLE_SIZE),
        "inscriptionGroupPriceFactors": legacy.inscription_factors(NATION_TABLE_SIZE),
        "notInShopInscriptionGroups": legacy.inscription_hiddens(NATION_TABLE_SIZE),
        "playerEmblemGroupPriceFactors": legacy.emblem_factors(),
        "notInShopPlayerEmblemGroups": legacy.emblem_hiddens(),
        "vehicleCamouflagePriceFactors": catalog.vehicle_camouflage_price_factors(),
        "vehicleHornPriceFactors": {},
    }


def _goodies_table(goodies: GoodieTable = None) -> dict:
    """The goodies section: the definitions, their prices and the exclusions."""
    if goodies is None:
        return {"goodies": {}, "prices": {}, "notInShop": []}
    return goodies.shop_section()


def build_shop(catalog: Catalog, revision: int = SHOP_REV,
               goodies: GoodieTable = None) -> dict:
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
    # ``DefaultShopRequester.getHiddens`` is the list the UI greys out; every
    # item the client's own data keeps out of the shop travels in it (the
    # ``notInShop`` vehicles of ``list.xml`` and the appearance items of the
    # second customization system - the legacy camouflages, inscriptions and
    # emblems have hidden lists of their own).
    items = _items_section(catalog, prices)
    goodie_section = _goodies_table(goodies)
    # ``ShopRequester._response`` hands this dictionary to
    # ``DefaultShopRequester`` (``self.defaults.update(invData['defaults'])``)
    # and every table that requester falls back to is read through
    # ``getItemsData()`` - which is ``defaults['items']`` - so the whole items
    # section travels nested inside it, exactly as the live server sent it.  The
    # remaining keys are the ones ``DefaultShopRequester`` reads directly.
    defaults = {
        "items": _items_section(catalog, prices),
        "slotsPrices": SLOTS_PRICES,
        "berthsPrices": BERTHS_PRICES,
        "tradeIn": dict(TRADE_IN),
        "tankmanCost": TANKMAN_COST,
        "premiumCost": dict(PREMIUM_COST),
        "dropSkillsCost": _drop_skills_table(),
        "paidRemovalCost": dict(DEVICE_REMOVAL_COST),
        "paidDeluxeRemovalCost": dict(DELUXE_REMOVAL_COST),
        "freeXPConversion": FREE_XP_CONVERSION,
        "freeXPToTManXPRate": FREE_XP_TO_TMAN_XP_RATE,
        "goodies": goodie_section,
        "achievementsReward": dict(ACHIEVEMENTS_REWARD),
        "restore_config": _restore_config(),
    }
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
        # The client's ``TradeInData(**tradeIn)`` unpacks this dictionary into a
        # named tuple, so it carries exactly the four keys it declares.
        "tradeIn": dict(TRADE_IN),
        "camouflageCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "playerInscriptionCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "playerEmblemCost": {CUSTOMIZATION_DAYS: (0, 50)},
        "goodies": goodie_section,
        "achievementsReward": dict(ACHIEVEMENTS_REWARD),
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

    def __init__(self, catalog: Catalog, goodies: GoodieTable = None):
        self.catalog = catalog
        self.goodies = goodies
        self._payload: Optional[dict] = None

    def payload(self) -> dict:
        if self._payload is None:
            self._payload = build_shop(self.catalog, goodies=self.goodies)
        return self._payload

    def revision(self) -> int:
        return int(self.payload()["rev"])

    def price(self, compact_descr: int) -> Optional[Price]:
        """``(credits, gold)`` of one item, or ``None`` when it is not priced."""
        return self.payload()["items"]["itemPrices"].get(int(compact_descr))

    def goodie_definitions(self) -> Dict[int, "GoodieTable"]:
        """``{goodieID: definition}`` of the personal offers of the event."""
        goodies = self.goodies
        return dict(goodies.definitions) if goodies is not None else {}


__all__ = [
    "ACHIEVEMENTS_REWARD",
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
