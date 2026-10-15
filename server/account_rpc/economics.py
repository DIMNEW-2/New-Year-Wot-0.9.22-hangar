"""The wallet commands the lobby's dialogs send.

Their argument order is the client's (``client/account_helpers/Stats.py``):

==========================  ===================================================
``CMD_UNLOCK`` (201)        ``Int3 (vehTypeCompDescr, unlockIdx, 0)``
``CMD_EXCHANGE`` (202)      ``Int3 (shopRev, gold, 0)`` - gold for credits at
                            ``shop.exchangeRate``
``CMD_FREE_XP_CONV`` (203)  ``IntArr [shopRev, xp, useDiscount,
                            *vehTypeCompDescrs]`` - the experience of those
                            vehicles becomes free experience
``CMD_PREMIUM`` (204)       ``Int3 (shopRev, days, arenaUniqueID)`` - ``days``
                            of premium account for ``premiumCost[days]`` gold
``CMD_UNLOCK_ALL`` (10004)  ``Int3 (0, 0, 0)`` - the debug "research everything"
==========================  ===================================================

The prices and rates are the ones the client's own dialogs show: the gold of an
exchange comes from ``shop.exchangeRate``, the premium price from
``shop.premiumCost`` and the free-experience rate from
``shop.freeXPConversion`` (``ShopCommonStats``), so the number in the dialog and
the number the local server charges never disagree.

``CMD_UNLOCK`` (research) is the one command of the group this build cannot
serve faithfully: the local garage *grants* every researchable vehicle (see
:meth:`server.account_rpc.state.AccountState.grant_garage`), so the client's own
``UnlockItemValidator`` refuses to send the request at all
(``already_unlocked``).  The handler therefore only answers the ones that still
arrive - an item that is already researched - and logs anything else.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from ..game import items as items_module
from . import state as state_module
from .purchases import PurchaseError
from .state import AccountState

LOG = logging.getLogger("wotny.economics")

#: The error text of a refusal.  The client builds the message key as
#: ``exchange/<errStr>`` / ``exchangeXP/<errStr>`` with ``server_error`` as the
#: default, so every refusal reports something the localization always has.
ERROR_SERVER = "server_error"
ERROR_NOT_ENOUGH_MONEY = "not_enough_money"

#: How much experience may be converted for free per request when the client
#: asks for a free conversion (``shop.freeXPConversionLimit``).
DEFAULT_FREE_XP_LIMIT = 100000


class Wallet(object):
    """The gold/credits/experience commands of one account."""

    def __init__(self, state: AccountState):
        self.state = state

    # -- helpers --------------------------------------------------------
    def shop(self) -> dict:
        return self.state.shop_payload()

    def _result(self, ext: Optional[dict] = None) -> dict:
        """The shape :meth:`RequestDispatcher._purchase` expects."""
        return {"code": 0, "ext": ext, "diff": self.state.wallet_diff()}

    def _exchange_rate(self) -> int:
        rate = int(self.shop().get("exchangeRate") or 0)
        return rate if rate > 0 else 400

    def _free_xp_conversion(self) -> Tuple[int, int]:
        """``(discrecity, cost)`` of ``ShopCommonStats.freeXPConversion``."""
        rate = self.shop().get("freeXPConversion") or (25, 1)
        try:
            discrecity, cost = int(rate[0]), int(rate[1])
        except (TypeError, ValueError, IndexError):
            return (25, 1)
        return (discrecity or 25, cost or 1)

    def _free_xp_limit(self) -> int:
        limit = self.shop().get("freeXPConversionLimit")
        try:
            return int(limit)
        except (TypeError, ValueError):
            return DEFAULT_FREE_XP_LIMIT

    # -- gold exchange (``CMD_EXCHANGE``) -------------------------------
    def exchange(self, gold: int) -> dict:
        """``CMD_EXCHANGE`` - ``gold`` becomes ``gold * exchangeRate`` credits."""
        gold = int(gold)
        if gold <= 0:
            raise PurchaseError(-2, ERROR_SERVER)
        if self.state.gold < gold:
            raise PurchaseError(-1, ERROR_NOT_ENOUGH_MONEY)
        rate = self._exchange_rate()
        self.state.gold -= gold
        self.state.credits += gold * rate
        LOG.info("exchange: %d gold -> %d credits (rate %d)", gold, gold * rate, rate)
        return self._result()

    # -- premium account (``CMD_PREMIUM``) -----------------------------
    def buy_premium(self, days: int) -> dict:
        """``CMD_PREMIUM`` - ``days`` of premium account for ``premiumCost`` gold."""
        days = int(days)
        costs = dict(self.shop().get("premiumCost") or {})
        price = None
        for key, value in costs.items():
            if int(key) == days:
                price = int(value)
                break
        if price is None or days <= 0:
            raise PurchaseError(-2, ERROR_SERVER)
        if self.state.gold < price:
            raise PurchaseError(-1, ERROR_NOT_ENOUGH_MONEY)
        self.state.gold -= price
        self.state.add_premium_days(days)
        LOG.info("premium: %d days for %d gold (until %s)",
                 days, price, self.state.premium_expiry)
        return self._result()

    # -- free experience (``CMD_FREE_XP_CONV``) -------------------------
    def convert_free_xp(self, xp: int, use_discount: bool = 0,
                        vehicle_types: Sequence[int] = ()) -> dict:
        """``CMD_FREE_XP_CONV`` - vehicle experience becomes free experience.

        The client sends the vehicles whose experience is meant to be spent and
        the amount it priced (``FreeXPExchanger``: ``gold = round(cost * xp /
        discrecity)``, and ``0`` for a free conversion).  The local server takes
        the experience from those vehicles in the order it received them and
        charges the gold the same formula produces.
        """
        xp = int(xp)
        if xp <= 0:
            raise PurchaseError(-2, ERROR_SERVER)
        sources = [int(value) for value in vehicle_types]
        available = sum(max(0, int(self.state.veh_type_xp.get(compact, 0)))
                        for compact in sources)
        if available < xp:
            raise PurchaseError(-1, ERROR_SERVER)
        free = bool(int(use_discount))
        if free:
            if xp > self._free_xp_limit():
                raise PurchaseError(-1, ERROR_SERVER)
            gold = 0
        else:
            discrecity, cost = self._free_xp_conversion()
            gold = int(round(cost * xp / float(discrecity)))
            if gold and self.state.gold < gold:
                raise PurchaseError(-1, ERROR_NOT_ENOUGH_MONEY)
        if gold:
            self.state.gold -= gold
        remaining = xp
        for compact in sources:
            if remaining <= 0:
                break
            current = max(0, int(self.state.veh_type_xp.get(compact, 0)))
            spend = min(current, remaining)
            if not spend:
                continue
            self.state.veh_type_xp[compact] = current - spend
            remaining -= spend
        self.state.free_xp += xp
        LOG.info("free xp: %d from %s for %d gold", xp, sources, gold)
        return self._result()

    # -- research (``CMD_UNLOCK`` / ``CMD_UNLOCK_ALL``) -----------------
    def unlock(self, veh_type_compact: int, unlock_index: int) -> dict:
        """``CMD_UNLOCK`` - everything is researched already (see the module docstring)."""
        veh_type_compact = int(veh_type_compact)
        if not veh_type_compact or self.state.catalog.vehicles.get(veh_type_compact) is None:
            raise PurchaseError(-1, ERROR_SERVER)
        LOG.info("unlock %s#%s: the local garage is fully researched, nothing to do",
                 veh_type_compact, int(unlock_index))
        return self._result()

    def unlock_all(self) -> dict:
        """``CMD_UNLOCK_ALL`` - grant every vehicle of the catalogue (debug command)."""
        granted = self.state.grant_garage(mode=state_module.GARAGE_RESEARCHABLE)
        LOG.info("unlock all: %d more vehicles are researched", granted)
        return self._result()


__all__ = ["ERROR_NOT_ENOUGH_MONEY", "ERROR_SERVER", "Wallet"]
