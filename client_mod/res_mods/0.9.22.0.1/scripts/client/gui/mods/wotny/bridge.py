# -*- coding: utf-8 -*-
"""The socket between the client and the local server.

The game runs on one thread and the BigWorld API is not thread safe, so all
socket work happens in a worker thread and the messages are handed over through
two deques:

* the worker reads frames and appends them to ``_incoming``,
* the game thread drains ``_incoming`` from a ``BigWorld.callback`` timer and
  dispatches every message (``_on_message``),
* the game thread appends outgoing messages to ``_outgoing`` and the worker
  writes them.

The worker also reconnects on its own and reports the connection state, so the
rest of the mod only ever sees "connected" or "not connected".
"""

import collections
import socket
import threading
import time

from gui.mods.wotny import config
from gui.mods.wotny import log
from gui.mods.wotny import proto

_STATE_IDLE = 0
_STATE_CONNECTING = 1
_STATE_CONNECTED = 2


class Bridge(object):
    """One connection to the local server, driven by ``BigWorld.callback``."""

    def __init__(self, host=None, port=None, on_message=None, on_state=None):
        self.host = host or config.HOST
        self.port = port or config.PORT
        self.on_message = on_message
        self.on_state = on_state
        self._incoming = collections.deque()
        self._outgoing = collections.deque()
        self._lock = threading.Lock()
        self._thread = None
        self._stop = False
        self._poll_id = None
        self._state = _STATE_IDLE
        self._socket = None
        self._reader = None
        self._writer = None
        self._last_message_time = 0.0
        self._hello_sent = False

    # -- lifecycle ------------------------------------------------------
    def start(self):
        if self._thread is not None:
            return
        self._stop = False
        self._thread = threading.Thread(target=self._worker, name='wotny-bridge')
        self._thread.setDaemon(True)
        self._thread.start()
        self._schedule_poll()

    def stop(self):
        self._stop = True
        self._cancel_poll()
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            try:
                thread.join(2.0)
            except Exception:
                pass
        self._set_state(_STATE_IDLE)

    def is_connected(self):
        return self._state == _STATE_CONNECTED

    # -- sending --------------------------------------------------------
    def send(self, message):
        """Queue one message for the server (called from the game thread)."""
        self._outgoing.append(message)

    def greet(self):
        """Tell the server who just connected; it answers with the account."""
        self._hello_sent = True
        self.send({'type': 'hello', 'protocol': proto.PICKLE_PROTOCOL,
                   'client': 'wotny-mod-%s' % config.VERSION})

    def send_command(self, request_id, opcode, args=()):
        self.send({'type': 'cmd', 'requestID': int(request_id),
                   'code': int(opcode), 'args': tuple(args)})

    # -- the worker thread ----------------------------------------------
    def _worker(self):
        while not self._stop:
            if self._state != _STATE_CONNECTED:
                if not self._connect():
                    for _ in xrange(int(config.CONNECT_RETRY_PERIOD * 10)):
                        if self._stop:
                            return
                        time.sleep(0.1)
                    continue
            try:
                self._pump()
            except proto.ProtocolError as error:
                log.warning('bridge closed: %s', error)
            except Exception as error:
                log.warning('bridge error: %s', error)
            self._disconnect()
            if not self._stop:
                time.sleep(config.CONNECT_RETRY_PERIOD)

    def _connect(self):
        self._set_state(_STATE_CONNECTING)
        try:
            sock = socket.create_connection((self.host, self.port), 5.0)
        except Exception as error:
            log.info('waiting for the local server on %s:%s (%s)',
                     self.host, self.port, error)
            return False
        # Reads block on purpose: a partially read frame must never be
        # abandoned (that would desynchronise the stream).  The socket is
        # closed from ``stop()`` and ``_disconnect()`` to unblock the worker.
        self._socket = sock
        self._reader = sock.makefile('rb')
        self._writer = sock.makefile('wb')
        self._hello_sent = False
        self._last_message_time = time.time()
        self._set_state(_STATE_CONNECTED)
        log.info('connected to the local server on %s:%s', self.host, self.port)
        self.greet()
        return True

    def _disconnect(self):
        sock, self._socket = self._socket, None
        self._reader = None
        self._writer = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        self._set_state(_STATE_IDLE)

    def _pump(self):
        """Read what is available and write what the game thread queued."""
        sock = self._socket
        writer = self._writer
        while True:
            pending = None
            self._lock.acquire()
            try:
                if self._outgoing:
                    pending = list(self._outgoing)
                    self._outgoing.clear()
            finally:
                self._lock.release()
            if pending is None:
                break
            for message in pending:
                writer.write(proto.pack_message(message))
            try:
                writer.flush()
            except Exception:
                return

        frame = proto.read_frame(self._reader)
        self._last_message_time = time.time()
        message = proto.unpack_message(frame)
        self._incoming.append(message)

    # -- the game thread ------------------------------------------------
    def _schedule_poll(self):
        try:
            import BigWorld
            self._poll_id = BigWorld.callback(config.POLL_PERIOD, self._poll)
        except Exception:
            self._poll_id = None

    def _cancel_poll(self):
        if self._poll_id is not None:
            try:
                import BigWorld
                BigWorld.cancelCallback(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    def _poll(self):
        self._poll_id = None
        while self._incoming:
            message = self._incoming.popleft()
            try:
                if self.on_message is not None:
                    self.on_message(message)
            except Exception:
                log.exception('cannot handle %r', message.get('type'))
        self._schedule_poll()

    def _set_state(self, state):
        if state == self._state:
            return
        self._state = state
        if self.on_state is not None:
            try:
                self.on_state(state)
            except Exception:
                log.exception('connection state callback failed')
