"""Dispatch of the client's account commands onto :class:`AccountState`.

Every client call that goes through ``Account._doCmd*`` ends up here.  The
arguments are taken straight from the wire, exactly as the client sent them:

======================  =====================================================
``openBox``/``openChest``  one string - the box or chest id (both share
                        ``CMD_NEW_YEAR_OPEN_BOX``, so the id decides)
``craft``               3 ints - toy type, national setting, rank
``breakToys``           a *flat* int array ``[toyID, count, toyID, count, ...]``
                        (``ClientNewYear.breakToys`` flattens the pairs)
``fillSlot``            3 ints - slot, decoration, unused
``selectDiscount``      int + string - goodie id, variadic discount id
``selectTankman``       int array + string array - ``(nation, vehicle, role)``
                        and ``(variadicTankmanID,)``
``markToysAsSeen``      flat int array (toy ids with counts)
``addToys``/``addToken``  the development helpers of the client's debug panel
======================  =====================================================

The garage commands (``CMD_EQUIP``, ``CMD_SET_AND_FILL_LAYOUTS``, ...) are
handled by :mod:`server.account_rpc.garage`, which keeps the depot bookkeeping
of everything a vehicle carries in step with the client.

The dispatcher answers with a response code, an optional error string, an
optional pickled ``ext`` payload (the client picks it up in
``Account.onCmdResponseExt``) and the messages the bridge should push to the
client afterwards (account diffs and New Year service-channel messages).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..game import new_year as ny_module
from ..proto import pack_stream, py2_dumps
from . import commands as cmd
# ``customization`` has to be imported before ``purchases``: the module imports
# the shop's error class, and ``purchases`` pulls ``state`` in, which pulls
# ``customization`` back in (``server/account_rpc/__init__`` documents the
# layering).  ``barracks`` joins the same group for the same reason.
from . import customization as customization_module
from . import barracks as barracks_module
from . import data as data_module
from . import economics as economics_module
from . import garage as garage_module
from . import profile as profile_module
from . import purchases as purchases_module
from .state import AccountState

LOG = logging.getLogger("wotny.requests")

#: The error text of an NY request; the client turns it into the localization key
#: ``#ny:system_messages/request/error/<text>`` (``NY.system_messages_request_error``),
#: and only ``common``/``requestIsBeingProcess``/``time out``/``cooldown`` exist
#: there, so every server-side refusal uses the generic one.
NY_ERROR_COMMON = "common"


@dataclass
class Response:
    """What the bridge has to send back for one command."""

    code: int = cmd.RES_SUCCESS
    error: str = ""
    ext: Optional[dict] = None
    diffs: List[dict] = field(default_factory=list)
    actions: List[dict] = field(default_factory=list)
    #: A streamed answer (``Account.onStreamComplete``); the client subscribes
    #: to it when the response carries ``RES_STREAM``.
    stream: Optional[object] = None

    def ext_blob(self) -> Optional[bytes]:
        return py2_dumps(self.ext) if self.ext is not None else None

    def stream_blob(self):
        """``(header, payload)`` of :attr:`stream`, or ``None``."""
        if self.stream is None:
            return None
        return pack_stream(self.stream)


def flat_pairs(values: Sequence[int]) -> List[Tuple[int, int]]:
    """``[toyID, count, toyID, count]`` -> ``[(toyID, count), ...]``.

    Already paired input is passed through unchanged, so the helper accepts both
    the wire format of ``breakToys``/``markToysAsSeen`` and hand written pairs.
    """
    values = list(values or ())
    if values and isinstance(values[0], (list, tuple)):
        return [(int(pair[0]), int(pair[1]) if len(pair) > 1 else 1) for pair in values]
    result = []
    for index in range(0, len(values) - len(values) % 2, 2):
        result.append((int(values[index]), int(values[index + 1])))
    return result


def _flat_args(args: Sequence) -> List[int]:
    """The ints of a request that may arrive flat or wrapped in one list."""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return [_as_int(value) for value in args[0]]
    values = []
    for value in args:
        if isinstance(value, (list, tuple)):
            values.extend(_as_int(item) for item in value)
        else:
            values.append(_as_int(value))
    return values


def _split_arrays(args: Sequence) -> Tuple[List[int], List]:
    """``(intArr, strArr)`` of ``_doCmdIntArrStrArr`` requests."""
    if len(args) == 2 and isinstance(args[0], (list, tuple)) \
            and isinstance(args[1], (list, tuple)):
        return ([_as_int(value) for value in args[0]], list(args[1]))
    return (_flat_args(args), [])


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


class RequestDispatcher:
    """Handles the commands the local server knows how to serve."""

    def __init__(self, state: AccountState, logger: logging.Logger = None):
        self.state = state
        self.log = logger or LOG
        self.shop = purchases_module.Shopkeeper(state)
        self.garage = garage_module.Garage(state)
        self.customization = customization_module.CustomizationKeeper(state, self.shop)
        self.barracks = barracks_module.Barracks(state, self.shop)
        #: The profile: the dossiers, the badges and the rating requests
        #: (see :mod:`server.account_rpc.profile`).
        self.profile = profile_module.Profile(state)
        self.wallet = economics_module.Wallet(state)
        self._message_id = 0
        self._handlers = {
            cmd.CMD_SYNC_DATA: self.on_sync_data,
            cmd.CMD_SYNC_SHOP: self.on_sync_shop,
            cmd.CMD_BUY_VEHICLE: self.on_buy_vehicle,
            cmd.CMD_BUY_ITEM: self.on_buy_item,
            cmd.CMD_BUY_TMAN: self.on_buy_tman,
            cmd.CMD_SELL_VEHICLE: self.on_sell_vehicle,
            cmd.CMD_SELL_ITEM: self.on_sell_item,
            cmd.CMD_VEHICLE_TRADE_IN: self.on_trade_in_vehicle,
            cmd.CMD_QUERY_BALANCE_INFO: self.on_query_balance_info,
            cmd.CMD_BUY_SLOT: self.on_buy_slot,
            cmd.CMD_BUY_BERTHS: self.on_buy_berths,
            cmd.CMD_BUY_AND_EQUIP_ITEM: self.on_buy_and_equip_item,
            cmd.CMD_BUY_AND_EQUIP_TMAN: self.on_buy_and_equip_tankman,
            cmd.CMD_EQUIP: self.on_equip,
            cmd.CMD_EQUIP_OPTDEV: self.on_equip_opt_device,
            cmd.CMD_EQUIP_SHELLS: self.on_equip_shells,
            cmd.CMD_EQUIP_EQS: self.on_equip_equipments,
            cmd.CMD_EQUIP_TMAN: self.on_equip_tankman,
            cmd.CMD_REPAIR: self.on_repair,
            cmd.CMD_VEH_SETTINGS: self.on_change_setting,
            cmd.CMD_SET_AND_FILL_LAYOUTS: self.on_set_and_fill_layouts,
            cmd.CMD_VEH_APPLY_STYLE: self.on_apply_style,
            cmd.CMD_SELL_C11N_ITEMS: self.on_sell_c11n_items,
            cmd.CMD_BUY_C11N_ITEMS: self.on_buy_c11n_items,
            cmd.CMD_VEH_APPLY_OUTFIT: self.on_apply_outfit,
            cmd.CMD_DISMISS_TMAN: self.on_dismiss_tankman,
            cmd.CMD_RETURN_CREW: self.on_return_crew,
            cmd.CMD_TMAN_RESTORE: self.on_restore_tankman,
            cmd.CMD_TMAN_ADD_SKILL: self.on_add_tankman_skill,
            cmd.CMD_TMAN_DROP_SKILLS: self.on_drop_tankman_skills,
            cmd.CMD_TMAN_RESPEC: self.on_respec_tankman,
            cmd.CMD_TMAN_MULTI_RESPEC: self.on_multi_respec_tankman,
            cmd.CMD_TMAN_CHANGE_ROLE: self.on_change_tankman_role,
            cmd.CMD_TMAN_PASSPORT: self.on_change_tankman_passport,
            cmd.CMD_TRAINING_TMAN: self.on_train_tankman,
            cmd.CMD_ADD_TMAN_XP: self.on_add_tankman_xp,
            cmd.CMD_UNLOCK: self.on_unlock,
            cmd.CMD_EXCHANGE: self.on_exchange,
            cmd.CMD_FREE_XP_CONV: self.on_free_xp_conv,
            cmd.CMD_PREMIUM: self.on_premium,
            cmd.CMD_UNLOCK_ALL: self.on_unlock_all,
            cmd.CMD_REQ_SERVER_STATS: self.on_server_stats,
            cmd.CMD_REQ_PLAYER_INFO: self.on_player_info,
            cmd.CMD_REQ_ACCOUNT_DOSSIER: self.on_account_dossier,
            cmd.CMD_REQ_VEHICLE_DOSSIER: self.on_vehicle_dossier,
            cmd.CMD_REQ_PLAYER_CLAN_INFO: self.on_player_clan_info,
            cmd.CMD_REQ_PLAYER_GLOBAL_RATING: self.on_player_global_rating,
            cmd.CMD_REQ_PLAYERS_GLOBAL_RATING: self.on_players_global_rating,
            cmd.CMD_SYNC_DOSSIERS: self.on_sync_dossiers,
            cmd.CMD_SELECT_BADGES: self.on_select_badges,
            cmd.CMD_SET_DOSSIER_FIELD: self.on_set_dossier_field,
            cmd.CMD_NEW_YEAR_OPEN_BOX: self.on_open_box,
            cmd.CMD_NEW_YEAR_SELECT_DISCOUNT: self.on_select_discount,
            cmd.CMD_NEW_YEAR_SELECT_TANKMAN: self.on_select_tankman,
            cmd.CMD_NEW_YEAR_CRAFT: self.on_craft,
            cmd.CMD_NEW_YEAR_BREAK_TOYS: self.on_break_toys,
            cmd.CMD_NEW_YEAR_SLOT_FILL: self.on_fill_slot,
            cmd.CMD_NEW_YEAR_SEE_TOYS: self.on_see_toys,
            cmd.CMD_NEW_YEAR_ADD_TOYS_DEV: self.on_add_toys_dev,
            cmd.CMD_NEW_YEAR_ADD_TOKEN_DEV: self.on_add_token_dev,
        }
        #: Commands that are accepted and acknowledged without changing anything.
        #: The client asks for a lot of them while the lobby starts (settings,
        #: shop sync, dossiers, ...); answering ``RES_SUCCESS`` keeps it moving
        #: and the log records the fact so real gaps stay visible.
        self._acknowledged = {
            cmd.CMD_ADD_INT_USER_SETTINGS,
        }

    # -- entry point ----------------------------------------------------
    def handle(self, request_id: int, opcode: int, args: Sequence = ()) -> Response:
        handler = self._handlers.get(opcode)
        if handler is None:
            if opcode in self._acknowledged:
                self.log.info("command %s acknowledged (request %s)", opcode, request_id)
                return Response()
            self.log.info("unhandled command %s (request %s, args %r)", opcode, request_id, list(args))
            return Response(code=cmd.RES_FAILURE, error="unknown_command")
        try:
            return handler(request_id, args)
        except ny_module.NewYearError as error:
            self.log.info("command %s refused: %s", opcode, error)
            return Response(code=cmd.RES_FAILURE, error=NY_ERROR_COMMON)
        except (TypeError, ValueError, IndexError) as error:
            self.log.info("command %s got bad arguments %r: %s", opcode, list(args), error)
            return Response(code=cmd.RES_WRONG_ARGS, error=NY_ERROR_COMMON)
        except Exception:  # pragma: no cover - defensive
            self.log.exception("command %s failed", opcode)
            return Response(code=cmd.RES_FAILURE, error=NY_ERROR_COMMON)

    def next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id

    # -- sync -----------------------------------------------------------
    def on_sync_data(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SYNC_DATA`` - the client sends its revision and expects a diff.

        The answer is a full snapshot: a diff whose ``prevRev`` is missing makes
        ``AccountSyncData.synchronize`` clear its caches, and the client's
        ``__onSyncResponse`` only compares ``prevRev`` when the server sent one.
        """
        client_revision = int(args[0]) if args else 0
        diff = self.state.full_diff()
        self.log.info("full sync for revision %s -> %s (%d vehicles, %d crew)",
                      client_revision, diff["rev"], len(self.state.vehicles), len(self.state.tankmen))
        return Response(ext=diff)

    def on_server_stats(self, request_id: int, args: Sequence) -> Response:
        return Response(ext=data_module.server_stats())

    def on_sync_shop(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SYNC_SHOP`` - the client asks for the shop cache.

        The answer is a stream: the client's ``SyncController`` subscribes to the
        request id when it sees ``RES_STREAM`` and unpacks the payload with
        ``zlib`` + ``cPickle`` (``Account.onStreamComplete``), which is how
        ``Shop`` gets its cache and the lobby its prices.
        """
        shop = self.state.shop_payload()
        self.log.info("shop sync: revision %s, %d prices",
                      shop.get("rev"), len(shop.get("items", {}).get("itemPrices", {})))
        return Response(code=cmd.RES_STREAM, stream=shop)

    # -- the shop -------------------------------------------------------
    def _purchase(self, action, *args) -> Response:
        """Run one shop operation and publish the diff it produced."""
        try:
            outcome = action(*args)
        except (purchases_module.PurchaseError, garage_module.GarageError) as error:
            self.log.info("shop: refused (%s)", error)
            return Response(code=error.code, error=error.error)
        self.log.info("shop: %s -> %s", getattr(action, "__name__", action),
                      outcome.get("ext") or "ok")
        response = Response(ext=outcome.get("ext"), diffs=[outcome["diff"]])
        return response

    def on_buy_vehicle(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_VEHICLE`` - ``[rev, compDescr, flags, tmanCost, rent]``."""
        _revision, compact, flags, tman_cost, _rent = purchases_module.parse_buy_vehicle(args)
        return self._purchase(self.shop.buy_vehicle, compact, flags, tman_cost)

    def on_trade_in_vehicle(self, request_id: int, args: Sequence) -> Response:
        """``CMD_VEHICLE_TRADE_IN`` - ``IntArr [rev, vehInvID, vehCD, flags, tmanCost]``.

        The traded-off vehicle is the *old* one (``vehInvID``) and the compact
        descriptor is the new vehicle, exactly as ``Shop.tradeInVehicle`` sends
        it; the answer's ``ext['vehInvID']`` is the inventory id of the new one.
        """
        values = _flat_args(args)
        if len(values) < 3:
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=purchases_module.TRADE_IN_ERROR)
        return self._purchase(self.shop.trade_in, values[1], values[2],
                              values[3] if len(values) > 3 else 0,
                              values[4] if len(values) > 4 else 0)

    def on_query_balance_info(self, request_id: int, args: Sequence) -> Response:
        """``CMD_QUERY_BALANCE_INFO`` (10017) - ``doCmdStr('')``.

        The whole answer is ``ext``: the client's ``Account.requestWGMBalanceInfo``
        hands it to ``WGMBalanceInfoRequester`` unchanged and that requester walks
        the rows (see :meth:`server.account_rpc.economics.Wallet.balance_info`).
        """
        rows = self.wallet.balance_info()
        self.log.info("balance info: %d rows (%d credits, %d gold)",
                      len(rows), self.state.credits, self.state.gold)
        return Response(ext=rows)

    def on_buy_item(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_ITEM`` - ``(rev, itemShopID, count, goldForCredits)``."""
        compact = int(args[1])
        count = int(args[2]) if len(args) > 2 else 1
        gold_for_credits = bool(int(args[3])) if len(args) > 3 else False
        return self._purchase(self.shop.buy_item, compact, count, gold_for_credits)

    def on_buy_tman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_TMAN`` - ``(rev, vehTypeCompDescr, roleIdx, tmanCostTypeIdx)``."""
        compact = int(args[1])
        role_index = int(args[2]) if len(args) > 2 else 1
        tman_cost = int(args[3]) if len(args) > 3 else 0
        return self._purchase(self.shop.buy_tankman, compact, role_index, tman_cost)

    def on_sell_vehicle(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SELL_VEHICLE`` - ``[rev, vehInvID, dismissCrew, items…]``."""
        _revision, inv_id, dismiss_crew, from_vehicle, from_inventory = \
            purchases_module.parse_sell_vehicle(args)
        return self._purchase(self.shop.sell_vehicle, inv_id, bool(dismiss_crew),
                              from_vehicle, from_inventory)

    def on_sell_item(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SELL_ITEM`` - ``(rev, itemTypeIdx, itemInvID, count)``."""
        item_type = int(args[1])
        compact = int(args[2])
        count = int(args[3]) if len(args) > 3 else 1
        return self._purchase(self.shop.sell_item, item_type, compact, count)

    # -- the wallet -----------------------------------------------------
    def on_unlock(self, request_id: int, args: Sequence) -> Response:
        """``CMD_UNLOCK`` - ``Int3 (vehTypeCompDescr, unlockIdx, 0)``."""
        vehicle = int(args[0])
        index = int(args[1]) if len(args) > 1 else 0
        return self._purchase(self.wallet.unlock, vehicle, index)

    def on_exchange(self, request_id: int, args: Sequence) -> Response:
        """``CMD_EXCHANGE`` - ``Int3 (shopRev, gold, 0)``."""
        gold = int(args[1]) if len(args) > 1 else 0
        return self._purchase(self.wallet.exchange, gold)

    def on_free_xp_conv(self, request_id: int, args: Sequence) -> Response:
        """``CMD_FREE_XP_CONV`` - ``[shopRev, xp, useDiscount, *vehicles]``."""
        values = list(args[0]) if args and isinstance(args[0], (list, tuple)) else list(args)
        if len(values) < 3:
            return Response(code=cmd.RES_WRONG_ARGS, error=economics_module.ERROR_SERVER)
        return self._purchase(self.wallet.convert_free_xp,
                              int(values[1]), int(values[2]), values[3:])

    def on_premium(self, request_id: int, args: Sequence) -> Response:
        """``CMD_PREMIUM`` - ``Int3 (shopRev, days, arenaUniqueID)``."""
        days = int(args[1]) if len(args) > 1 else 0
        return self._purchase(self.wallet.buy_premium, days)

    def on_unlock_all(self, request_id: int, args: Sequence) -> Response:
        """``CMD_UNLOCK_ALL`` - the debug "research everything" command."""
        return self._purchase(self.wallet.unlock_all)

    def on_buy_slot(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_SLOT`` - one more garage slot."""
        return self._purchase(self.shop.buy_slot)

    def on_buy_berths(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_BERTHS`` - one more barracks berth pack."""
        return self._purchase(self.shop.buy_berths)

    # -- the garage -----------------------------------------------------
    def on_equip(self, request_id: int, args: Sequence) -> Response:
        """``CMD_EQUIP`` - ``Int3 (vehInvID, itemCompDescr, gunCompDescr)``."""
        veh_inv_id, compact = int(args[0]), int(args[1])
        gun = int(args[2]) if len(args) > 2 else 0
        return self._purchase(self.garage.equip, veh_inv_id, compact, gun)

    def on_equip_opt_device(self, request_id: int, args: Sequence) -> Response:
        """``CMD_EQUIP_OPTDEV`` - ``[shopRev, vehInvID, device, slot, isPaid]``."""
        _revision, values = garage_module.parse_shop_revision(args)
        if len(values) < 4:
            return Response(code=cmd.RES_WRONG_ARGS, error=garage_module.ERROR_SERVER)
        veh_inv_id, compact, slot = int(values[0]), int(values[1]), int(values[2])
        paid = bool(int(values[3]))
        return self._purchase(self.garage.equip_opt_device, veh_inv_id, compact, slot, paid)

    def on_equip_shells(self, request_id: int, args: Sequence) -> Response:
        """``CMD_EQUIP_SHELLS`` - ``[vehInvID, *shells]``."""
        values = list(args[0]) if args and isinstance(args[0], (list, tuple)) else list(args)
        if len(values) < 2:
            return Response(code=cmd.RES_WRONG_ARGS, error=garage_module.ERROR_SERVER)
        return self._purchase(self.garage.equip_shells, int(values[0]), values[1:])

    def on_equip_equipments(self, request_id: int, args: Sequence) -> Response:
        """``CMD_EQUIP_EQS`` - ``[vehInvID, eq1, eq2, eq3, booster]``."""
        values = list(args[0]) if args and isinstance(args[0], (list, tuple)) else list(args)
        if len(values) < 2:
            return Response(code=cmd.RES_WRONG_ARGS, error=garage_module.ERROR_SERVER)
        return self._purchase(self.garage.equip_equipments, int(values[0]), values[1:])

    def on_equip_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_EQUIP_TMAN`` - ``Int3 (vehInvID, slot, tmanInvID)``."""
        veh_inv_id, slot = int(args[0]), int(args[1])
        tman = int(args[2]) if len(args) > 2 else -1
        return self._purchase(self.garage.equip_tankman, veh_inv_id, slot, tman)

    # -- the barracks ----------------------------------------------------
    def _barracks(self, action, *args) -> Response:
        """Run one barracks operation and publish the diff it produced.

        Unlike the shop, a mutation that points at something the account does
        not have is a *bad request* (``RES_WRONG_ARGS``) rather than a refusal:
        the client only sends what its own cache holds.
        """
        try:
            outcome = action(*args)
        except barracks_module.BarracksError as error:
            self.log.info("barracks: refused (%s)", error)
            return Response(code=error.code, error=error.error)
        except (TypeError, ValueError, IndexError) as error:
            self.log.info("barracks: bad arguments (%s)", error)
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=barracks_module.ERROR_INVALID_OPERATION)
        return Response(ext=outcome.get("ext"), diffs=[outcome["diff"]])

    def on_dismiss_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_DISMISS_TMAN`` - ``Int3 (tmanInvID, 0, 0)``."""
        return self._barracks(self.barracks.dismiss, int(args[0]))

    def on_return_crew(self, request_id: int, args: Sequence) -> Response:
        """``CMD_RETURN_CREW`` - ``Int3 (vehInvID, 0, 0)``."""
        return self._barracks(self.barracks.return_crew, int(args[0]))

    def on_restore_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_RESTORE`` - ``Int3 (tmanInvID, 0, 0)``."""
        return self._barracks(self.barracks.restore, int(args[0]))

    def on_add_tankman_skill(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_ADD_SKILL`` - ``Int3 (tmanInvID, skillIdx, 0)``."""
        values = _flat_args(args)
        return self._barracks(self.barracks.add_skill, values[0], values[1])

    def on_drop_tankman_skills(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_DROP_SKILLS`` - ``Int3 (shopRev, tmanInvID, costIdx)``."""
        values = _flat_args(args)
        return self._barracks(self.barracks.drop_skills, values[1], values[2])

    def on_respec_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_RESPEC`` - ``Int4 (shopRev, tmanInvID, costIdx, vehCD)``."""
        values = _flat_args(args)
        return self._barracks(self.barracks.respec, values[1], values[2],
                              values[3] if len(values) > 3 else 0)

    def on_multi_respec_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_MULTI_RESPEC`` - ``IntArr [shopRev, vehCD, invID, costIdx…]``."""
        values = _flat_args(args)
        if len(values) < 4:
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=barracks_module.ERROR_INVALID_OPERATION)
        pairs = []
        rest = values[2:]
        for index in range(0, len(rest) - 1, 2):
            pairs.append((rest[index], rest[index + 1]))
        return self._barracks(self.barracks.multi_respec, pairs, values[1])

    def on_change_tankman_role(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_CHANGE_ROLE`` - ``Int4 (shopRev, tmanInvID, roleIdx, vehCD)``."""
        values = _flat_args(args)
        return self._barracks(self.barracks.change_role, values[1], values[2],
                              values[3] if len(values) > 3 else 0)

    def on_change_tankman_passport(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TMAN_PASSPORT`` - ``IntArr [shopRev, tmanInvID, flags, ids…]``."""
        values = _flat_args(args)
        if len(values) < 10:
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=barracks_module.ERROR_INVALID_OPERATION)
        return self._barracks(self.barracks.change_passport, values[1], values[2:])

    def on_train_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_TRAINING_TMAN`` - ``Int3 (shopRev, tmanInvID, gold)``."""
        return self._barracks(self.barracks.free_xp_to_tankman, int(args[1]), int(args[2]))

    def on_add_tankman_xp(self, request_id: int, args: Sequence) -> Response:
        """``CMD_ADD_TMAN_XP`` - ``Int3 (tmanInvID, xp, 0)``."""
        return self._barracks(self.barracks.add_xp, int(args[0]), int(args[1]))

    # -- the profile ------------------------------------------------------
    def _profile(self, action, *args) -> Response:
        """Run one profile operation and answer with its payload.

        A profile operation either describes what to *send* (the dossier, the
        clan info, a rating - a plain ``ext`` dictionary) or what *changed* (a
        badge selection, a dossier field - an outcome carrying a ``diff``).
        """
        try:
            outcome = action(*args)
        except profile_module.ProfileError as error:
            self.log.info("profile: refused (%s)", error)
            return Response(code=error.code, error=error.error)
        except (TypeError, ValueError, IndexError) as error:
            self.log.info("profile: bad arguments (%s)", error)
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=profile_module.ERROR_WRONG_ARGS)
        if isinstance(outcome, dict) and "diff" in outcome:
            return Response(ext=outcome.get("ext"), diffs=[outcome["diff"]])
        return Response(ext=outcome if isinstance(outcome, dict) else None)

    def on_player_info(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REQ_PLAYER_INFO`` - ``Int3 (databaseID, 0, 0)``."""
        return self._profile(self.profile.player_info_ext,
                             int(args[0]) if args else 0)

    def on_account_dossier(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REQ_ACCOUNT_DOSSIER`` - ``Int3 (accountID, 0, 0)``."""
        return self._profile(self.profile.dossier_ext)

    def on_vehicle_dossier(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REQ_VEHICLE_DOSSIER`` - ``Int3 (databaseID, vehCD, 0)``."""
        return self._profile(self.profile.dossier_ext,
                             int(args[1]) if len(args) > 1 else 0)

    def on_player_clan_info(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REQ_PLAYER_CLAN_INFO`` - ``Int3 (databaseID, 0, 0)``."""
        return self._profile(self.profile.clan_info_ext)

    def on_player_global_rating(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REQ_PLAYER_GLOBAL_RATING`` - ``Int3 (accountID, 0, 0)``."""
        return self._profile(self.profile.global_rating_ext,
                             int(args[0]) if args else 0)

    def on_players_global_rating(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REQ_PLAYERS_GLOBAL_RATING`` - ``IntArrStrArr (ids, [])``."""
        account_ids = args[0] if args and isinstance(args[0], (list, tuple)) else ()
        return self._profile(self.profile.global_ratings_ext, list(account_ids))

    def on_sync_dossiers(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SYNC_DOSSIERS`` - ``Int3 (version, maxChangeTime, 0)``.

        The answer is a stream: the client's ``DossierCache`` subscribes to the
        request id when it sees ``RES_STREAM`` and unpacks ``(version, [...])``
        with ``zlib`` + ``cPickle`` (``SyncController``).  The list is empty -
        this account has no vehicle dossiers to cache - and echoing the version
        keeps the client from clearing what it has.
        """
        version = int(args[0]) if args else 0
        change_time = int(args[1]) if len(args) > 1 else 0
        dossiers = self.profile.sync_dossiers(version, change_time)
        self.log.info("dossier sync: version %s, %d descriptor(s), max change time %s",
                      dossiers[0], len(dossiers[1]), change_time)
        return Response(code=cmd.RES_STREAM, stream=dossiers)

    def on_select_badges(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SELECT_BADGES`` - ``IntArr (badgeIDs)``."""
        badges = args[0] if args and isinstance(args[0], (list, tuple)) else args
        return self._profile(self.profile.select_badges, list(badges))

    def on_set_dossier_field(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SET_DOSSIER_FIELD`` - ``IntStr (value, path)``."""
        value = int(args[0]) if args else 0
        path = args[1] if len(args) > 1 else ""
        return self._profile(self.profile.set_dossier_field, value, path)

    def on_repair(self, request_id: int, args: Sequence) -> Response:
        """``CMD_REPAIR`` - ``Int3 (vehInvID, 0, 0)``."""
        return self._purchase(self.garage.repair, int(args[0]))

    def on_change_setting(self, request_id: int, args: Sequence) -> Response:
        """``CMD_VEH_SETTINGS`` - ``Int3 (vehInvID, setting, isOn)``."""
        veh_inv_id, setting = int(args[0]), int(args[1])
        is_on = bool(int(args[2])) if len(args) > 2 else False
        return self._purchase(self.garage.change_setting, veh_inv_id, setting, is_on)

    def on_set_and_fill_layouts(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SET_AND_FILL_LAYOUTS`` - save a layout and pay for what it lacks."""
        _revision, veh_inv_id, shells, equipment_type, eqs = garage_module.parse_set_and_fill(args)
        return self._purchase(self.garage.set_and_fill_layouts, veh_inv_id, shells,
                              equipment_type, eqs)

    def on_buy_and_equip_item(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_AND_EQUIP_ITEM`` - ``[rev, compDescr, vehInvID, slot, paid, gun]``."""
        _revision, values = garage_module.parse_shop_revision(args)
        if len(values) < 3:
            return Response(code=cmd.RES_WRONG_ARGS, error=garage_module.ERROR_SERVER)
        compact, veh_inv_id, slot = int(values[0]), int(values[1]), int(values[2])
        paid = bool(int(values[3])) if len(values) > 3 else False
        gun = int(values[4]) if len(values) > 4 else 0
        return self._purchase(self.garage.buy_and_equip_item, compact, veh_inv_id, slot, paid, gun)

    def on_buy_and_equip_tankman(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_AND_EQUIP_TMAN`` - ``Int4 (rev, vehInvID, slot, costIdx)``."""
        veh_inv_id = int(args[1]) if len(args) > 1 else -1
        slot = int(args[2]) if len(args) > 2 else -1
        cost_index = int(args[3]) if len(args) > 3 else 0
        return self._purchase(self.garage.buy_and_equip_tankman, veh_inv_id, slot, cost_index)

    # -- the customization window ---------------------------------------
    def on_buy_c11n_items(self, request_id: int, args: Sequence) -> Response:
        """``CMD_BUY_C11N_ITEMS`` - ``[rev, vehInvID, intCD, count, …]``.

        ``Shop.buyCustomizations`` writes the pairs of its ``{intCD: count}``
        table into one flat array, so an odd tail would be a request the client
        never makes - it is refused rather than half served.
        """
        values = _flat_args(args)
        if len(values) < 3 or (len(values) - 2) % 2:
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=customization_module.ERROR_WRONG_ARGS)
        pairs = [(int(values[index]), int(values[index + 1]))
                 for index in range(2, len(values) - 1, 2)]
        return self._purchase(self.customization.buy, int(values[1]), pairs)

    def on_sell_c11n_items(self, request_id: int, args: Sequence) -> Response:
        """``CMD_SELL_C11N_ITEMS`` - ``Int4 (rev, itemCD, count, vehInvID)``."""
        compact = int(args[1])
        count = int(args[2]) if len(args) > 2 else 1
        veh_inv_id = int(args[3]) if len(args) > 3 else 0
        return self._purchase(self.customization.sell, compact, count, veh_inv_id)

    def on_apply_style(self, request_id: int, args: Sequence) -> Response:
        """``CMD_VEH_APPLY_STYLE`` - ``Int3 (rev, vehInvID, styleID)``."""
        veh_inv_id = int(args[1]) if len(args) > 1 else 0
        style_id = int(args[2]) if len(args) > 2 else 0
        return self._purchase(self.customization.apply_style, veh_inv_id, style_id)

    def on_apply_outfit(self, request_id: int, args: Sequence) -> Response:
        """``CMD_VEH_APPLY_OUTFIT`` - ``[rev, vehInvID, season]`` + ``[outfitDescr]``.

        The outfit travels as the *string* ``Outfit.pack().makeCompDescr()``
        produced on the client (``Shop.buyAndEquipOutfit``), which is why the
        request is the one command of the account with two argument arrays.
        """
        ints, strings = _split_arrays(args)
        if len(ints) < 3 or not strings:
            return Response(code=cmd.RES_WRONG_ARGS,
                            error=customization_module.ERROR_WRONG_ARGS)
        return self._purchase(self.customization.apply_outfit,
                              int(ints[1]), int(ints[2]), strings[0])

    # -- New Year event -------------------------------------------------
    def _new_year_response(self, result: ny_module.NewYearResult, diffs: int = 1,
                           events_data: bool = False) -> Response:
        """Apply an event result: one diff plus the ``tokenQuests`` sys message.

        Whatever the result granted (a box vehicle, a recruited tankwoman, the
        camouflage of a collection reward) is booked on the account first and
        then published in the same diff - the client's caches only read diffs,
        so without it the reward would only appear after the next full sync.
        """
        granted = self.state.apply_new_year_result(result)
        diff = self.state.incremental_diff(
            result, events_data=events_data or bool(result.chest_loot)
        )
        diff.update(self.state.granted_account_diff(granted))
        if granted.get("announced"):
            # The answer itself tells the client of the new message: the
            # notification list rides in the plain ``eventsData`` key, which the
            # client diffs against the one it cached and forwards only the new
            # entries of (``Account.__synchronizeEventNotifications``).
            diff.update(self.state.events.notifications_diff())
        response = Response(ext={"newYear": dict(diff.get("newYear", {}))}, diffs=[diff])
        if result.bonuses is not None:
            response.actions.append(
                data_module.box_opened_action(result.bonuses, self.next_message_id())
            )
        return response

    def _container_response(self, container_id: str) -> Response:
        """``openBox``/``openChest`` - both share one opcode, the id decides."""
        if container_id in self.state.catalog.new_year.chests:
            result = self.state.new_year.open_chest(container_id)
            self.log.info("opened chest %s -> %s", container_id, result.bonuses)
        else:
            result = self.state.new_year.open_box(container_id)
            self.log.info("opened box %s -> %s", container_id, result.bonuses)
        return self._new_year_response(result)

    def on_open_box(self, request_id: int, args: Sequence) -> Response:
        return self._container_response(str(args[0]))

    def on_select_discount(self, request_id: int, args: Sequence) -> Response:
        goodie_id, variadic_id = int(args[0]), str(args[1])
        result = self.state.new_year.select_discount(goodie_id, variadic_id)
        self.log.info("discount %s (goodie %s) claimed", variadic_id, goodie_id)
        return self._new_year_response(result)

    def on_select_tankman(self, request_id: int, args: Sequence) -> Response:
        nation_id, vehicle_innation_id, role_id = (int(value) for value in args[0])
        variadic_id = str(args[1][0])
        result = self.state.new_year.select_tankman(nation_id, vehicle_innation_id, role_id, variadic_id)
        self.log.info("tankwoman recruited for nation %s (role %s)", nation_id, role_id)
        # The new crew member (and the tank she was seated on) travels in the
        # diff of the answer itself, so the barracks shows her right away.
        return self._new_year_response(result)

    def on_craft(self, request_id: int, args: Sequence) -> Response:
        toy_type, setting_id, rank = (int(value) for value in args)
        result = self.state.new_year.craft(
            None if toy_type < 0 else toy_type,
            None if setting_id < 0 else setting_id,
            None if rank < 0 else rank,
        )
        self.log.info("crafted a decoration (%s): %s", result.bonuses, result.diff.get("newYear"))
        return self._new_year_response(result)

    def on_break_toys(self, request_id: int, args: Sequence) -> Response:
        toys = flat_pairs(args[0] if args else ())
        result = self.state.new_year.break_toys(toys)
        self.log.info("broke %d decoration(s) into fragments", len(toys))
        return self._new_year_response(result)

    def on_fill_slot(self, request_id: int, args: Sequence) -> Response:
        slot_id, toy_id = int(args[0]), int(args[1])
        result = self.state.new_year.fill_slot(slot_id, toy_id)
        self.log.info("slot %s <- decoration %s", slot_id, toy_id)
        return self._new_year_response(result)

    def on_see_toys(self, request_id: int, args: Sequence) -> Response:
        toys = flat_pairs(args[0] if args else ())
        result = self.state.new_year.see_toys([pair[0] for pair in toys])
        self.log.debug("marked %d decoration(s) as seen", len(toys))
        return self._new_year_response(result)

    def on_add_toys_dev(self, request_id: int, args: Sequence) -> Response:
        toys = [int(value) for value in (args[0] if args else ())]
        result = self.state.new_year.add_toys(toys)
        self.log.info("DEV: added %d decoration(s)", len(toys))
        return self._new_year_response(result)

    def on_add_token_dev(self, request_id: int, args: Sequence) -> Response:
        token = str(args[0])
        result = self.state.new_year.add_token(token)
        self.log.info("DEV: added one %s", token)
        return self._new_year_response(result)


__all__ = ["NY_ERROR_COMMON", "RequestDispatcher", "Response", "flat_pairs"]
