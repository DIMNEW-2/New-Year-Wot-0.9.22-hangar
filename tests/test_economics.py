"""The wallet: gold for credits, premium account, free experience, research.

The dialogs of the lobby send these commands through ``Stats``
(``client/account_helpers/Stats.py``) and each of them reads the *shop cache*
for its price first (``getExchangeRate``, ``getPremiumCost``,
``getFreeXPConversion``), so the numbers the tests check are the numbers the
client shows in its own confirmation windows.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc import economics as economics_module  # noqa: E402
from server.account_rpc.server import AccountServer  # noqa: E402
from server.account_rpc.state import ACCOUNT_ATTR_PREMIUM, AccountState  # noqa: E402
from server.game import items as items_module  # noqa: E402
from server.proto import py2_loads  # noqa: E402
from test_local_server import Bridge  # noqa: E402

_REQUEST_ID = [500]


def _next_request() -> int:
    _REQUEST_ID[0] += 1
    return _REQUEST_ID[0]


def _socket_pair(runner, **kwargs):
    """``(state, server, bridge)`` of a running local server."""
    state = common.new_account(runner.client, **kwargs)
    server = AccountServer(state, port=0, save_path=None).start()
    bridge = Bridge(server.port)
    bridge.hello()
    return state, server, bridge


def _money_diff(diff):
    return diff.get("economics", {})


# ---------------------------------------------------------------------------
# gold <-> credits (``CMD_EXCHANGE``)
# ---------------------------------------------------------------------------
def test_exchange_gold_for_credits(runner: common.Runner) -> None:
    """The client's dialog exchange: ``gold * exchangeRate`` credits."""
    state, server, bridge = _socket_pair(runner)
    try:
        rate = state.shop_payload()["exchangeRate"]
        runner.check(rate > 0, "the shop publishes an exchange rate")
        gold = 100
        credits, wallet = state.credits, state.gold
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EXCHANGE, (state.shop_payload()["rev"], gold, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the exchange is accepted")
        runner.equal(wallet - state.gold, gold, "the gold left the wallet")
        runner.equal(state.credits - credits, gold * rate, "the credits arrived")
        diff = diffs[-1] if diffs else {}
        runner.equal(_money_diff(diff).get("gold"), state.gold, "the diff publishes the gold")
        runner.equal(_money_diff(diff).get("credits"), state.credits,
                     "the diff publishes the credits")
        bridge.close()
    finally:
        server.stop()


def test_exchange_is_refused_without_gold(runner: common.Runner) -> None:
    """More gold than the account has is refused and nothing moves."""
    state, server, bridge = _socket_pair(runner)
    try:
        wallet = (state.credits, state.gold)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_EXCHANGE, (state.shop_payload()["rev"], state.gold + 1, 0))
        runner.check(response["code"] < 0, "the answer is a failure (%s)" % response["code"])
        runner.equal(response["error"], economics_module.ERROR_NOT_ENOUGH_MONEY,
                     "the client can render the reason")
        runner.equal((state.credits, state.gold), wallet, "the wallet is untouched")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# premium account (``CMD_PREMIUM``)
# ---------------------------------------------------------------------------
def test_buy_premium(runner: common.Runner) -> None:
    """A premium packet costs ``premiumCost[days]`` gold and starts the clock."""
    state, server, bridge = _socket_pair(runner)
    try:
        costs = state.shop_payload()["premiumCost"]
        days = sorted(int(day) for day in costs)[0]
        price = int(costs[days])
        now = int(time.time())
        wallet = state.gold
        credits = state.credits
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_PREMIUM, (state.shop_payload()["rev"], days, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the premium account is bought")
        runner.equal(wallet - state.gold, price, "the gold that was paid")
        runner.equal(state.credits, credits, "premium is not paid in credits")
        runner.check(state.premium_expiry >= now + days * 86400,
                     "the premium runs at least %d days" % days)
        diff = diffs[-1] if diffs else {}
        account = diff.get("account", {})
        runner.equal(account.get("premiumExpiryTime"), state.premium_expiry,
                     "the diff publishes the expiry")
        runner.check(account.get("attrs", 0) & ACCOUNT_ATTR_PREMIUM,
                     "and the premium bit the lobby reads")
        bridge.close()
    finally:
        server.stop()


def test_premium_extends_an_existing_account(runner: common.Runner) -> None:
    """Buying twice adds the days instead of replacing them."""
    state, server, bridge = _socket_pair(runner)
    try:
        costs = state.shop_payload()["premiumCost"]
        days = sorted(int(day) for day in costs)[0]
        state.add_premium_days(days)
        first = state.premium_expiry
        state.gold += int(costs[days]) * 2
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_PREMIUM, (state.shop_payload()["rev"], days, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the second packet is bought")
        runner.equal(state.premium_expiry - first, days * 86400,
                     "the days are added to the running account")
        bridge.close()
    finally:
        server.stop()


def test_premium_is_refused_for_an_unknown_packet(runner: common.Runner) -> None:
    """A day count the shop does not price is refused."""
    state, server, bridge = _socket_pair(runner)
    try:
        costs = {int(day) for day in state.shop_payload()["premiumCost"]}
        days = 1
        while days in costs:
            days += 1
        wallet = (state.credits, state.gold)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_PREMIUM, (state.shop_payload()["rev"], days, 0))
        runner.check(response["code"] < 0, "the answer is a failure (%s)" % response["code"])
        runner.equal((state.credits, state.gold), wallet, "the wallet is untouched")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# free experience (``CMD_FREE_XP_CONV``)
# ---------------------------------------------------------------------------
def test_free_xp_conversion(runner: common.Runner) -> None:
    """Vehicle experience becomes free experience for the gold the dialog shows."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        compact = entry.compact_descr
        state.veh_type_xp[compact] = 1000
        discrecity, cost = state.shop_payload()["freeXPConversion"]
        xp = 500
        gold = int(round(cost * xp / float(discrecity)))
        free_xp, wallet = state.free_xp, state.gold
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_FREE_XP_CONV,
            ([state.shop_payload()["rev"], xp, 0, compact],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the conversion is accepted")
        runner.equal(state.free_xp - free_xp, xp, "the free experience arrived")
        runner.equal(state.veh_type_xp[compact], 1000 - xp, "the vehicle experience is spent")
        runner.equal(wallet - state.gold, gold, "the gold the client priced")
        diff = diffs[-1] if diffs else {}
        stats = diff.get("stats", {})
        runner.equal(stats.get("freeXP"), state.free_xp, "the diff publishes the free XP")
        runner.equal(stats.get("vehTypeXP", {}).get(compact), state.veh_type_xp[compact],
                     "and the experience of the vehicle")
        bridge.close()
    finally:
        server.stop()


def test_free_xp_conversion_refusals(runner: common.Runner) -> None:
    """More experience than the vehicles hold, and past the free limit, is refused."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        compact = entry.compact_descr
        state.veh_type_xp[compact] = 10
        wallet = (state.credits, state.gold, state.free_xp)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_FREE_XP_CONV,
            ([state.shop_payload()["rev"], 100, 0, compact],))
        runner.check(response["code"] < 0, "the answer is a failure (%s)" % response["code"])
        runner.equal((state.credits, state.gold, state.free_xp), wallet,
                     "nothing was converted")
        runner.equal(state.veh_type_xp[compact], 10, "the vehicle experience is intact")
        # A vehicle the account does not own has no experience at all.
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_FREE_XP_CONV,
            ([state.shop_payload()["rev"], 100, 0, 0x00010002],))
        runner.check(response["code"] < 0, "an unknown vehicle is refused")
        bridge.close()
    finally:
        server.stop()


def test_free_conversion_uses_the_free_limit(runner: common.Runner) -> None:
    """``useDiscount`` converts without gold, but only up to the free limit."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        compact = entry.compact_descr
        state.veh_type_xp[compact] = 1000
        wallet = state.gold
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_FREE_XP_CONV,
            ([state.shop_payload()["rev"], 100, 1, compact],))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the free conversion is accepted")
        runner.equal(state.gold, wallet, "a free conversion costs no gold")
        runner.equal(state.veh_type_xp[compact], 900, "the experience was spent")
        limit = int(state.shop_payload().get("freeXPConversionLimit",
                                             economics_module.DEFAULT_FREE_XP_LIMIT))
        state.veh_type_xp[compact] = limit * 2
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_FREE_XP_CONV,
            ([state.shop_payload()["rev"], limit + 1, 1, compact],))
        runner.check(response["code"] < 0, "past the limit it is refused")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# research (``CMD_UNLOCK`` / ``CMD_UNLOCK_ALL``)
# ---------------------------------------------------------------------------
def test_unlock_of_an_owned_vehicle_changes_nothing(runner: common.Runner) -> None:
    """The local garage is fully researched, so a research request is inert."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        unlocks = set(state.unlocks)
        wallet = (state.credits, state.gold, state.free_xp)
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_UNLOCK, (entry.compact_descr, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the request is answered")
        runner.equal(state.unlocks, unlocks, "nothing was researched")
        runner.equal((state.credits, state.gold, state.free_xp), wallet, "nothing was paid")
        response, diffs, actions = bridge.command(
            _next_request(), cmd.CMD_UNLOCK, (0xFEEB, 0, 0))
        runner.check(response["code"] < 0, "a vehicle that does not exist is refused")
        bridge.close()
    finally:
        server.stop()


def test_unlock_all_grants_the_researchable_garage(runner: common.Runner) -> None:
    """``CMD_UNLOCK_ALL`` fills a garage that was started with premium tanks only."""
    state, server, bridge = _socket_pair(runner, garage_mode="premium")
    try:
        owned = len(state.vehicles)
        response, diffs, actions = bridge.command(_next_request(), cmd.CMD_UNLOCK_ALL, (0, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the command is accepted")
        runner.check(len(state.vehicles) > owned,
                     "more vehicles (%d -> %d)" % (owned, len(state.vehicles)))
        runner.check(all(compact in state.unlocks for compact in state.owned_vehicle_comp_descrs()),
                     "every owned vehicle is unlocked")
        bridge.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# the wallet and the XP table survive a restart
# ---------------------------------------------------------------------------
def test_wallet_survives_a_restart(runner: common.Runner) -> None:
    """Premium, free experience and the vehicle XP table are saved."""
    state, server, bridge = _socket_pair(runner)
    try:
        entry = min(state.vehicles.values(), key=lambda item: item.inv_id)
        state.veh_type_xp[entry.compact_descr] = 1234
        state.free_xp += 42
        state.add_premium_days(3)
        payload = state.to_json()
        reloaded = AccountState.from_json(payload, state.catalog,
                                          tankman_factory=state.tankman_factory)
        runner.equal(reloaded.veh_type_xp.get(entry.compact_descr), 1234,
                     "the vehicle experience is saved")
        runner.equal(reloaded.free_xp, state.free_xp, "the free experience is saved")
        runner.equal(reloaded.premium_expiry, state.premium_expiry, "the premium is saved")
        runner.check(reloaded.account_attrs() & ACCOUNT_ATTR_PREMIUM,
                     "and the premium bit follows the expiry")
        bridge.close()
    finally:
        server.stop()


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
