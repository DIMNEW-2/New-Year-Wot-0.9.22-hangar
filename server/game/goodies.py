"""Personal discounts of the shop cache (``goodies``).

The shop cache has a section of its own for the personal offers of an account -
an item that changes the *price* of something instead of being an item the
player owns.  The client reads it in ``ShopRequester._preprocessValidData`` and
turns every entry into a ``_NamedGoodieData`` named tuple
(``variety, target, enabled, lifetime, useby, counter, autostart, condition,
resource``), which is why the section travels as plain tuples::

    goodies: {goodieID: (variety, target, enabled, lifetime, useby, counter,
                         autostart, condition, resource)}
    prices:  {goodieID: (credits, gold)}
    notInShop: [goodieID, ...]

with ``target = (targetType, targetValue, limit)`` and ``resource =
(resourceType, value, isPercentage)``
(``common/goodies/goodie_helpers.loadDefinitions``).

What the local server publishes are the **New Year vehicle discounts**: the
event hands out a ``ny18:vd:<level>`` certificate and the client then looks the
offers of that level up in the shop cache - every goodie whose id falls into the
``<goodiesRange>`` of the level's descriptor (``item_defs/new_year/
variadic_discounts.xml``, one hundred ids per level) is one

    "N% off this vehicle, paid in the currency the vehicle is priced in"

which is exactly what the rewards screen shows
(``_VehicleDiscountsStorage.__initLocalCache`` in ``client/gui/Scaleform/daapi/
view/lobby/ny/``) and what the shop and the tech tree price against
(``ShopRequester.personalVehicleDiscounts`` -> ``Vehicle.buyPrices`` - see
:mod:`server.account_rpc.purchases`).  The live game's own
``server_xml/goodies.xml`` is not part of the client, so the table is built from
the installation's catalogue: every vehicle of the level's tier the shop can
hand out, in compact descriptor order, is one offer of that level.

A claimed certificate is *state*, not a definition: the server marks the goodie
active in the account section (``('goodies', '_r')``, ``{id: (state,
finishTime, count)}``) and the client's ``personalGoodies`` then matches the
definition - which is what makes the discounted price appear.  Buying the
vehicle consumes it again (``GOODIE_STATE_USED``), the same lifecycle the live
server runs (``Goodies.load`` -> ``Goodies.remove``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence

#: ``common/goodies/goodie_constants`` - ``GOODIE_STATE``.
GOODIE_STATE_INACTIVE = 0
GOODIE_STATE_ACTIVE = 1
GOODIE_STATE_USED = 2

#: ``common/goodies/goodie_constants`` - ``GOODIE_VARIETY``.
GOODIE_VARIETY_DISCOUNT = 0
GOODIE_VARIETY_BOOSTER = 1

#: ``GOODIE_TARGET_TYPE.ON_BUY_VEHICLE`` - the discount a vehicle is bought with.
GOODIE_TARGET_TYPE_ON_BUY_VEHICLE = 6

#: ``GOODIE_RESOURCE_TYPE``.
GOODIE_RESOURCE_TYPE_GOLD = 10
GOODIE_RESOURCE_TYPE_CREDITS = 20

#: ``common/goodies/goodie_constants.MAX_ACTIVE_GOODIES``: more than this many
#: running goodies are not activated at once (``Goodies.activate``).
MAX_ACTIVE_GOODIES = 3

#: The prefix of an event discount certificate (``ny18:vd:<level>``).
VARIADIC_DISCOUNT_PREFIX = "ny18:vd:"

#: What one atmosphere level takes off the price of a vehicle, in percent.
#:
#: The ranges of the event's own ``variadic_discounts.xml`` say which goodie ids
#: belong to which level; the percentage itself is server side data of the live
#: game (like the loot of a box or the price of a shell), so the local server
#: publishes a table that grows with the level: the better the decorated tree,
#: the better the offer.  A level without an entry gets no offers at all.
NY_DISCOUNT_PERCENT = {
    2: 5,
    3: 10,
    4: 15,
    5: 20,
    6: 25,
    7: 30,
    8: 35,
    9: 40,
    10: 50,
}


def percent_of_level(level: int) -> int:
    """The discount percent of one certificate level (``0`` when unknown)."""
    return int(NY_DISCOUNT_PERCENT.get(int(level), 0))


def level_of_discount(discount_id: str) -> Optional[int]:
    """``ny18:vd:7`` -> ``7`` (``extractLevelFromDiscountID`` of the client)."""
    text = str(discount_id or "")
    if not text.startswith(VARIADIC_DISCOUNT_PREFIX):
        return None
    try:
        return int(text[len(VARIADIC_DISCOUNT_PREFIX):])
    except ValueError:
        return None


def discount_token(level: int) -> str:
    """``7`` -> ``ny18:vd:7`` - the token the event grants on a level up."""
    return "%s%d" % (VARIADIC_DISCOUNT_PREFIX, int(level))


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------
@dataclass
class GoodieDefinition:
    """One entry of the shop cache's ``goodies`` section.

    The nine fields are the ones the client unpacks; ``target`` is
    ``(targetType, targetValue, limit)`` and ``resource``
    ``(resourceType, value, isPercentage)``.  ``condition`` stays ``None``: the
    offers of the event are not gated by anything the client can check.
    """

    uid: int
    variety: int
    target: tuple
    resource: tuple
    enabled: bool = True
    lifetime: Optional[int] = None
    useby: Optional[int] = None
    counter: int = 0
    autostart: bool = False
    condition: Optional[tuple] = None

    #: The name of the vehicle the offer targets (for the log and the notices).
    title: str = ""

    def as_tuple(self) -> tuple:
        """The nine values of ``_NamedGoodieData``, in its order."""
        return (
            int(self.variety),
            tuple(self.target),
            bool(self.enabled),
            self.lifetime,
            self.useby,
            int(self.counter),
            bool(self.autostart),
            self.condition,
            tuple(self.resource),
        )

    # -- what the definition means --------------------------------------
    @property
    def target_type(self) -> int:
        return int(self.target[0])

    @property
    def target_value(self) -> int:
        return int(self.target[1])

    @property
    def resource_type(self) -> int:
        return int(self.resource[0])

    @property
    def value(self) -> int:
        return int(self.resource[1])

    @property
    def is_percentage(self) -> bool:
        return bool(self.resource[2])

    def is_vehicle_discount(self) -> bool:
        return (self.variety == GOODIE_VARIETY_DISCOUNT
                and self.target_type == GOODIE_TARGET_TYPE_ON_BUY_VEHICLE)

    def apply(self, price: int) -> int:
        """One price with the offer applied.
        ``common/goodies/goodie_helpers.getPriceWithDiscount``:
        a percentage discount is ``int(price - price * value / 100)``, an
        absolute one ``max(price - value, 0)``, and both clamp at zero - an
        offer can never turn a price into a debt.
        """
        price = max(0, int(price))
        if self.is_percentage:
            result = int(price - price * (self.value / 100.0))
            return result if result > 0 else 0
        return max(price - self.value, 0)

    def discounted_price(self, credits: int, gold: int) -> tuple:
        """``(credits, gold)`` with the offer applied to *its own* currency.

        ``ShopRequester.__getPriceWithDiscount`` rebuilds the price in the
        currency the resource names and ``Vehicle.buyPrices`` then picks that
        price whenever it is the cheaper one - so the offer of a gold priced
        vehicle (a premium tank) has to be a gold resource, and a credits priced
        vehicle takes a credits one.  :func:`new_year_vehicle_discounts` picks
        the resource that way, and the shop dialogue therefore shows exactly the
        price the server charges.
        """
        if self.resource_type == GOODIE_RESOURCE_TYPE_GOLD:
            return int(credits), self.apply(gold)
        return self.apply(credits), int(gold)


@dataclass
class GoodieTable:
    """The whole ``goodies`` section of one account."""

    definitions: Dict[int, GoodieDefinition] = field(default_factory=dict)
    #: ``{goodieID: (credits, gold)}`` - what buying the goodie itself costs.
    #: The offers of the event are earned, not sold, so this stays empty.
    prices: Dict[int, tuple] = field(default_factory=dict)
    #: Boosters the shop keeps out of its list (``getHiddenBoosters``).
    not_in_shop: List[int] = field(default_factory=list)

    def __contains__(self, uid) -> bool:
        return int(uid) in self.definitions

    def __iter__(self) -> Iterator[GoodieDefinition]:
        return iter(self.definitions.values())

    def __len__(self) -> int:
        return len(self.definitions)

    def get(self, uid) -> Optional[GoodieDefinition]:
        return self.definitions.get(int(uid))

    def shop_section(self) -> dict:
        """``{'goodies': ..., 'prices': ..., 'notInShop': ...}`` of the cache."""
        return {
            "goodies": {uid: definition.as_tuple()
                        for uid, definition in sorted(self.definitions.items())},
            "prices": {uid: tuple(price) for uid, price in sorted(self.prices.items())},
            "notInShop": list(self.not_in_shop),
        }

    def vehicle_discounts(self) -> Dict[int, GoodieDefinition]:
        """``{goodieID: definition}`` of the vehicle offers (``shop.discounts``)."""
        return {uid: definition for uid, definition in self.definitions.items()
                if definition.is_vehicle_discount()}

    def best_for_vehicle(self, compact_descr: int,
                         active: Sequence[int] = None) -> Optional[GoodieDefinition]:
        """The strongest offer on a vehicle among the goodies of an account.

        ``active`` are the ids of the account's own goodies (the
        ``('goodies', '_r')`` section): ``ShopRequester.personalVehicleDiscounts``
        keeps only the definitions whose id is in there, so without it every
        defined offer would look like a discount the player owns.
        """
        wanted = int(compact_descr)
        allowed = None if active is None else {int(uid) for uid in active}
        best = None
        for uid, definition in self.definitions.items():
            if not definition.is_vehicle_discount():
                continue
            if definition.target_value != wanted:
                continue
            if allowed is not None and uid not in allowed:
                continue
            if best is None or definition.value > best.value:
                best = definition
        return best


def for_sale_vehicles(catalog) -> List:
    """Every vehicle of the installation the shop can hand to a player.

    That is: playable (not a ``_bot``/``_training``/``_IGR`` hull or an observer
    - the filter ``VehicleCollection.playable`` applies to a garage), priced
    (``list.xml`` prints a ``<price>`` for the tech tree tanks and their modules
    and the premium ones are priced in gold) and *not* hidden from the shop
    (``<notInShop>`` in ``list.xml`` keeps reward vehicles out of the tree, and
    a discount on a vehicle nobody can buy would be a dead offer).
    """
    priced = catalog.vehicles.item_prices()
    hidden = set(catalog.not_in_shop_items())
    result = []
    for vehicle in catalog.vehicles.playable():
        price = priced.get(vehicle.compact_descr)
        if price is None or price == (0, 0):
            continue
        if vehicle.compact_descr in hidden:
            continue
        result.append(vehicle)
    return sorted(result, key=lambda vehicle: vehicle.compact_descr)


def new_year_vehicle_discounts(catalog, percent: Dict[int, int] = None,
                               vehicles: Sequence = None) -> GoodieTable:
    """The ``goodies`` section of the New Year event.

    One offer per certificate level and vehicle: the ids come from the level's
    own range (``ny18:vd:<level>`` - a hundred ids, ``variadic_discounts.xml``),
    the target is the vehicle's compact descriptor and the resource is the
    percentage of the level in the currency the vehicle is priced in.

    Which vehicle of a tier gets which id follows a rule the server can state:
    the candidates of the level are ordered by compact descriptor and take the
    ids of the range from its first one on - so the table is the same for every
    account and the client's own filters decide what a player sees (the apply
    dialogue hides the garage, ``NYApplyDiscountOnVehiclePopup`` filters with
    ``~REQ_CRITERIA.INVENTORY``).  A tier with more candidates than the range
    has ids keeps the first ones - the only place where the table drops one.
    """
    percent = dict(percent or NY_DISCOUNT_PERCENT)
    candidates = list(vehicles if vehicles is not None else for_sale_vehicles(catalog))
    by_level: Dict[int, List] = {}
    for vehicle in candidates:
        by_level.setdefault(int(vehicle.level), []).append(vehicle)
    for group in by_level.values():
        group.sort(key=lambda vehicle: vehicle.compact_descr)

    table = GoodieTable()
    for discount_id, descriptor in sorted(catalog.new_year.variadic_discounts.items()):
        level = level_of_discount(discount_id)
        if level is None:
            continue
        value = percent.get(level)
        if not value:
            continue
        low, high = (int(descriptor.goodies_range[0]), int(descriptor.goodies_range[1]))
        for index, vehicle in enumerate(by_level.get(level, ())):
            uid = low + index
            if uid > high:
                break
            resource_type = (GOODIE_RESOURCE_TYPE_GOLD if vehicle.price_gold
                             else GOODIE_RESOURCE_TYPE_CREDITS)
            table.definitions[uid] = GoodieDefinition(
                uid=uid,
                variety=GOODIE_VARIETY_DISCOUNT,
                target=(GOODIE_TARGET_TYPE_ON_BUY_VEHICLE, int(vehicle.compact_descr), None),
                resource=(resource_type, int(value), True),
                enabled=True,
                title="%d%% %s" % (int(value), vehicle.name),
            )
    return table


__all__ = [
    "GOODIE_RESOURCE_TYPE_CREDITS",
    "GOODIE_RESOURCE_TYPE_GOLD",
    "GOODIE_STATE_ACTIVE",
    "GOODIE_STATE_INACTIVE",
    "GOODIE_STATE_USED",
    "GOODIE_TARGET_TYPE_ON_BUY_VEHICLE",
    "GOODIE_VARIETY_BOOSTER",
    "GOODIE_VARIETY_DISCOUNT",
    "GoodieDefinition",
    "GoodieTable",
    "MAX_ACTIVE_GOODIES",
    "NY_DISCOUNT_PERCENT",
    "VARIADIC_DISCOUNT_PREFIX",
    "discount_token",
    "for_sale_vehicles",
    "level_of_discount",
    "new_year_vehicle_discounts",
    "percent_of_level",
]
