"""The local server the in-client bridge talks to.

The 0.9.22 client is a Python 2.7 application that has no way to point its
account connection at a plain socket; that is what the client mod
(``client_mod/res_mods/0.9.22.0.1/scripts/client/gui/mods``) is for.  The mod
replaces the account request pipe with a socket, and *this* module is the other
end of it:

* the mod connects and sends its revision, the server answers with the full
  account state (the same diff an online server would stream after login),
* every command the client sends (``_doCmd*``) is dispatched to
  :class:`server.account_rpc.requests.RequestDispatcher`,
* anything the server has to push (account diffs, New Year service-channel
  messages) travels back on the same socket.

Framing is one length-prefixed, ``zlib``-compressed, Python-2-readable pickle
per message (see :mod:`server.proto`).  The server never opens a listening
socket on a public interface by default: it binds ``127.0.0.1`` and the mod
connects to it from inside the client process.
"""

from __future__ import annotations

import logging
import socket
import socketserver
import threading
import time
from typing import List, Optional

from ..proto import ProtocolError, pack_message, read_frame, unpack_message
from . import commands as cmd
from .requests import RequestDispatcher, Response
from .state import AccountState

LOG = logging.getLogger("wotny.server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 28422
PROTOCOL_VERSION = 1

#: Response to a request the client sent but the server has not answered yet.
MAX_REQUESTS_PER_MESSAGE = 1


class Session:
    """One connected client bridge."""

    def __init__(self, address, wfile, state: AccountState, server: "AccountServer"):
        self.address = address
        self.wfile = wfile
        self.state = state
        self.server = server
        self.dispatcher = RequestDispatcher(state, logger=server.log)
        self.connected_at = time.time()
        self.last_seen = self.connected_at
        self._lock = threading.Lock()

    # -- sending --------------------------------------------------------
    def send(self, message) -> None:
        with self._lock:
            self.wfile.write(pack_message(message))
            self.wfile.flush()

    def send_response(self, request_id: int, response: Response) -> None:
        """Answer one request: the account update first, then the response.

        The live server pushes the resulting ``diff`` (and any chat message the
        request produced) *before* it answers the request, and the client relies
        on that order: the callbacks of ``_doCmd*`` look at the freshly synced
        caches (a chest's reward, for instance, is read from the hidden quest
        that arrives in ``eventsData``).
        """
        for diff in response.diffs:
            self.send({"type": "diff", "diff": diff})
        for action in response.actions:
            self.send({"type": "sysMessage", "action": action})
        self.send(
            {
                "type": "response",
                "requestID": int(request_id),
                "code": int(response.code),
                "error": response.error,
                "ext": response.ext_blob(),
            }
        )
        # A streamed answer follows its response: the client subscribes to the
        # request id when it sees ``RES_STREAM`` and unpacks the stream with the
        # callback of ``Account.onStreamComplete``.
        stream = response.stream_blob()
        if stream is not None:
            header, payload = stream
            self.send(
                {
                    "type": "stream",
                    "requestID": int(request_id),
                    "desc": tuple(header),
                    "data": payload,
                }
            )

    def send_welcome(self, diff: dict) -> None:
        self.send(
            {
                "type": "welcome",
                "protocol": PROTOCOL_VERSION,
                "account": {"name": self.state.name, "databaseID": 1},
                "diff": diff,
            }
        )

    # -- receiving ------------------------------------------------------
    def handle_message(self, message) -> bool:
        self.last_seen = time.time()
        kind = message.get("type")
        if kind == "hello":
            LOG.info("bridge connected from %s (protocol %s, client %s)",
                     self.address, message.get("protocol"), message.get("client"))
            self.send_welcome(self.dispatcher.on_sync_data(0, (0,)).ext)
            return True
        if kind == "cmd":
            return self.handle_command(message)
        if kind == "ping":
            self.send({"type": "pong", "time": time.time()})
            return True
        if kind == "bye":
            return False
        LOG.warning("unknown bridge message %r", kind)
        return True

    def handle_command(self, message) -> bool:
        request_id = int(message.get("requestID", 0))
        opcode = int(message.get("code", 0))
        args = message.get("args") or ()
        response = self.dispatcher.handle(request_id, opcode, args)
        if request_id == cmd.REQUEST_ID_NO_RESPONSE or response.code == cmd.RES_NON_PLAYER:
            # Fire-and-forget requests get no answer, but their diffs do travel.
            for diff in response.diffs:
                self.send({"type": "diff", "diff": diff})
            for action in response.actions:
                self.send({"type": "sysMessage", "action": action})
            return True
        self.send_response(request_id, response)
        return True


class AccountServer:
    """Owns the account, the save file and the listening socket."""

    def __init__(
        self,
        state: AccountState,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        log: logging.Logger = None,
        save_path: Optional[str] = None,
        save_every: float = 30.0,
    ):
        self.state = state
        self.host = host
        self.port = port
        self.log = log or LOG
        self.save_path = save_path
        self.save_every = save_every
        self.sessions: List[Session] = []
        self._tcpd: Optional[socketserver.ThreadingTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._last_save = time.time()
        #: Sessions are handled in their own threads and every one of them saves
        #: the account when it closes, so the write has to be serialised.
        self._save_lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------
    def start(self) -> "AccountServer":
        server = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(30.0)
                wfile = self.request.makefile("wb")
                session = Session(self.client_address, wfile, server.state, server)
                server.sessions.append(session)
                server.log.info("session %s opened", self.client_address)
                try:
                    while True:
                        try:
                            frame = read_frame(self.request.makefile("rb"))
                        except ProtocolError as error:
                            server.log.info("session %s finished: %s", self.client_address, error)
                            break
                        try:
                            message = unpack_message(frame)
                        except ProtocolError as error:
                            server.log.warning("bad message from %s: %s", self.client_address, error)
                            continue
                        if not session.handle_message(message):
                            break
                        server.maybe_save()
                except (socket.timeout, OSError) as error:
                    server.log.info("session %s dropped: %s", self.client_address, error)
                finally:
                    try:
                        wfile.close()
                    except OSError:
                        pass
                    if session in server.sessions:
                        server.sessions.remove(session)
                    server.save()
                    server.log.info("session %s closed", self.client_address)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._tcpd = Server((self.host, self.port), Handler)
        self.port = self._tcpd.server_address[1]
        self._thread = threading.Thread(target=self._tcpd.serve_forever, name="wotny-accept", daemon=True)
        self._thread.start()
        self.log.info("listening on %s:%s", self.host, self.port)
        return self

    def serve_forever(self) -> None:
        if self._tcpd is None:
            self.start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.log.info("interrupted, saving the account")
        finally:
            self.stop()

    def stop(self) -> None:
        self.save()
        if self._tcpd is not None:
            self._tcpd.shutdown()
            self._tcpd.server_close()
            self._tcpd = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    # -- the account file ------------------------------------------------
    def maybe_save(self) -> None:
        if self.save_path and time.time() - self._last_save > self.save_every:
            self.save()

    def save(self) -> None:
        if not self.save_path:
            return
        import json
        import os

        with self._save_lock:
            directory = os.path.dirname(os.path.abspath(self.save_path))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            tmp_path = self.save_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(self.state.to_json(), handle, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp_path, self.save_path)
            self._last_save = time.time()
        self.log.debug("account saved to %s", self.save_path)

    # -- pushing ---------------------------------------------------------
    def broadcast(self, message) -> None:
        for session in list(self.sessions):
            try:
                session.send(message)
            except OSError as error:
                self.log.info("cannot push to %s: %s", session.address, error)
                if session in self.sessions:
                    self.sessions.remove(session)

    def push_diff(self, diff: dict) -> None:
        diff.setdefault("vehicleConfigs", self.state.vehicle_configurations())
        self.broadcast({"type": "diff", "diff": diff})

    def push_sys_message(self, action: dict) -> None:
        self.broadcast({"type": "sysMessage", "action": action})


__all__ = [
    "AccountServer",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "PROTOCOL_VERSION",
    "Session",
]
