"""The local account server (``Account`` in the client's terminology).

* :mod:`server.account_rpc.commands` - the opcodes and response codes of
  ``common/AccountCommands.py``,
* :mod:`server.account_rpc.state` - the account itself (wallet, garage, event),
* :mod:`server.account_rpc.requests` - one handler per account command,
* :mod:`server.account_rpc.data` - client payloads (system messages, ...),
* :mod:`server.account_rpc.server` - the socket the in-client bridge talks to.

The client-side half lives in ``client_mod/res_mods/0.9.22.0.1/scripts`` and is
loaded by the game as an ordinary mod.
"""

from __future__ import annotations

from .commands import CMD_SYNC_DATA, RES_FAILURE, RES_SUCCESS
from .data import sys_message_action
from .requests import RequestDispatcher, Response
from .server import DEFAULT_HOST, DEFAULT_PORT, PROTOCOL_VERSION, AccountServer
from .state import AccountState, TankmanEntry, VehicleEntry

__all__ = [
    "AccountServer",
    "AccountState",
    "CMD_SYNC_DATA",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "PROTOCOL_VERSION",
    "RequestDispatcher",
    "RES_FAILURE",
    "RES_SUCCESS",
    "RequestDispatcher",
    "Response",
    "TankmanEntry",
    "VehicleEntry",
    "sys_message_action",
]
