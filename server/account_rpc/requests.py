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
from . import data as data_module
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


class RequestDispatcher:
    """Handles the commands the local server knows how to serve."""

    def __init__(self, state: AccountState, logger: logging.Logger = None):
        self.state = state
        self.log = logger or LOG
        self._message_id = 0
        self._handlers = {
            cmd.CMD_SYNC_DATA: self.on_sync_data,
            cmd.CMD_SYNC_SHOP: self.on_sync_shop,
            cmd.CMD_REQ_SERVER_STATS: self.on_server_stats,
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
            cmd.CMD_SYNC_DOSSIERS,
            cmd.CMD_REPAIR,
            cmd.CMD_VEH_SETTINGS,
            cmd.CMD_EQUIP,
            cmd.CMD_EQUIP_OPTDEV,
            cmd.CMD_EQUIP_SHELLS,
            cmd.CMD_EQUIP_EQS,
            cmd.CMD_EQUIP_TMAN,
            cmd.CMD_SET_AND_FILL_LAYOUTS,
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

    # -- New Year event -------------------------------------------------
    def _new_year_response(self, result: ny_module.NewYearResult, diffs: int = 1,
                           events_data: bool = False) -> Response:
        """Apply an event result: one diff plus the ``tokenQuests`` sys message."""
        self.state.apply_new_year_result(result)
        diff = self.state.incremental_diff(
            result, events_data=events_data or bool(result.chest_loot)
        )
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
        response = self._new_year_response(result)
        # The new crew member has to appear in the barracks right away, so the
        # inventory travels with the same diff (the client's
        # ``ClientNewYear``/barracks read it from there).
        response.diffs[0]["inventory"] = {8: self.state.tankman_inventory_diff()}
        return response

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
