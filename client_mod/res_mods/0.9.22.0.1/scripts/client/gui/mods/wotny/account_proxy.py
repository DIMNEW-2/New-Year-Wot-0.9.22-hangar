# -*- coding: utf-8 -*-
"""The ``base`` of a client-only Account: a mailbox that talks to the server.

``Account._doCmd*`` (``scripts/client/Account.py``) resolves the command method
on ``self.base`` and calls it with ``(requestID, command, *args)``.  For an
account the local server creates, ``base`` is this object: every command is
forwarded over the socket and the answers are handed back to the client through
the entity methods the engine would normally call:

* ``Account.onCmdResponse`` / ``Account.onCmdResponseExt`` for an answer,
* ``Account.update`` for an account diff the server pushes,
* ``Account.onChatAction`` for a chat action (the New Year loot boxes report
  their reward through a ``tokenQuests`` system message),
* ``Account.onStreamComplete`` for a streamed answer (the shop).
"""

import cPickle

from gui.mods.wotny import log

#: ``AccountCommands.CMD_SYNC_DATA`` - the request that carries the first
#: account diff.  The lobby is shown once its answer has arrived.
CMD_SYNC_DATA = 100

#: The base calls the client makes that a local server answers with a no-op:
#: ``ConnectionManager.checkClientServerVersions`` uses ``requestToken`` to
#: reject a client that does not match the server, the chat channel of the lobby
#: sends its commands (``chatCommandFromClient``/``inviteCommand``/
#: ``ackCommand``, see ``client_common/ClientChat``), the connection manager
#: keeps the entity alive (``keepAliveFor``/``stopKeepingAlive``) and the client
#: pushes its own properties (``sendPropertiesTo``/``setAction``).  None of them
#: is forwarded to the local server - there is nothing to answer with - and none
#: of them is a failure.
_IGNORED_CALLS = ('requestToken', 'logStreamCorruption', 'sendPropertiesTo',
                  'keepAliveFor', 'stopKeepingAlive', 'chatCommandFromClient',
                  'inviteCommand', 'ackCommand', 'onClientDeath', 'setAction',
                  'cancelEntityCheckOut', 'giveClientTo', 'destroyIfNoKeepers')


class RemoteServer(object):
    """The stand-in ``bigworld Account.base``; forwards everything to the mod."""

    def __init__(self, bridge):
        self._bridge = bridge
        self.commands = 0

    # -- the methods the client actually calls ---------------------------
    def doCmdStr(self, requestID, command, value):
        self._send(requestID, command, (value,))

    def doCmdIntStr(self, requestID, command, first, value):
        self._send(requestID, command, (first, value))

    def doCmdInt3(self, requestID, command, first, second, third):
        self._send(requestID, command, (first, second, third))

    def doCmdInt4(self, requestID, command, first, second, third, fourth):
        self._send(requestID, command, (first, second, third, fourth))

    def doCmdInt2Str(self, requestID, command, first, second, value):
        self._send(requestID, command, (first, second, value))

    def doCmdIntArr(self, requestID, command, values):
        self._send(requestID, command, (list(values),))

    def doCmdIntStrArr(self, requestID, command, first, values):
        self._send(requestID, command, (first, list(values)))

    def doCmdIntArrStrArr(self, requestID, command, ints, strings):
        self._send(requestID, command, (list(ints), list(strings)))

    # -- everything else is a fire-and-forget call we do not serve --------
    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in _IGNORED_CALLS:
            # Routine lobby traffic: the chat channel asks for the last
            # service messages (``ClientChat.requestLastSysMessages`` ->
            # ``base.chatCommandFromClient``), the connection manager keeps
            # the entity alive and the client pushes its own properties.  A
            # local server has nothing to answer with and nothing to fix, so
            # these stay out of the log unless ``WOTNY_VERBOSE=1``.
            def quietly_ignored(*args, **kwargs):
                log.debug('ignored account base call %s%r', name, args)
                return None

            return quietly_ignored

        def ignored(*args, **kwargs):
            log.info('ignored account base call %s%r', name, args)
            return None

        return ignored

    def _send(self, requestID, command, args):
        self.commands += 1
        log.info('-> cmd %s (request %s) %r', command, requestID, args)
        self._bridge.send_command(requestID, command, args)


class AccountBridge(object):
    """Applies the messages of the server to the Account entity of the client."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.account = None
        self.database_id = 1
        self.name = ''
        self.first_vehicle_inv_id = 0
        self.sync_done = False
        self._show_gui_scheduled = False
        self._show_gui_done = False
        self._welcome = None
        self.on_sync_done = None

    # -- the account entity ---------------------------------------------
    def attach(self, account):
        self.account = account
        log.info('account %r attached to the bridge', getattr(account, 'name', '?'))

    # -- messages from the server ----------------------------------------
    def on_message(self, message):
        kind = message.get('type')
        if kind == 'welcome':
            self._on_welcome(message)
        elif kind == 'diff':
            self.apply_diff(message.get('diff') or {})
        elif kind == 'sysMessage':
            self.apply_action(message.get('action'))
        elif kind == 'stream':
            self.apply_stream(message)
        elif kind == 'response':
            self.apply_response(message)
        elif kind == 'pong':
            pass
        else:
            log.info('unknown message from the server: %r', kind)

    def _on_welcome(self, message):
        self._welcome = message
        account = message.get('account') or {}
        self.database_id = int(account.get('databaseID', 1) or 1)
        self.name = account.get('name', '') or self.name
        diff = message.get('diff') or {}
        self._remember_vehicle(diff)
        log.info('welcome from the server: account %r (database %s), revision %s',
                 self.name, self.database_id, diff.get('rev'))
        # The diff itself is not applied here: the client asks for the account
        # data with CMD_SYNC_DATA on its own (AccountSyncData) and that answer is
        # what every cache in the lobby waits for.  Applying it twice would only
        # rebuild the same caches.

    def _remember_vehicle(self, diff):
        try:
            compDescrs = diff['inventory'][1]['compDescr']
            if compDescrs:
                self.first_vehicle_inv_id = sorted(compDescrs.keys())[0]
        except (KeyError, TypeError, IndexError):
            pass

    # -- account data ----------------------------------------------------
    def apply_diff(self, diff):
        account = self.account
        if account is None or not diff:
            return
        self._remember_vehicle(diff)
        try:
            account._update(True, diff)
        except Exception:
            log.exception('cannot apply the account diff')
            return
        log.info('applied a diff with revision %s %s', diff.get('rev'),
                 '' if diff.get('prevRev') is not None else '(full sync)')

    def apply_response(self, message):
        account = self.account
        if account is None:
            return
        request_id = int(message.get('requestID', 0))
        code = int(message.get('code', 0))
        error = message.get('error') or ''
        ext = message.get('ext')
        try:
            if ext is None:
                account.onCmdResponse(request_id, code, error)
            else:
                # ``Account.onCmdResponseExt`` (``client/Account.py``) does
                # ``ext = cPickle.loads(ext)`` itself, so the payload the server
                # sends is already a pickle and has to be passed through
                # unchanged.  Anything else (a plain object, e.g. from an older
                # server) is pickled here.
                if not isinstance(ext, str):
                    ext = cPickle.dumps(ext, 2)
                account.onCmdResponseExt(request_id, code, error, ext)
        except Exception:
            log.exception('cannot deliver the answer of request %s', request_id)
            return
        log.info('<- request %s answered with %s %s', request_id, code, error)
        if code >= 0:
            self._on_command_done(request_id)

    def _on_command_done(self, request_id):
        if self.sync_done:
            return
        # Every request is answered by the server, so the first successful answer
        # of the sync request means the account data is in: that is the moment
        # the live server shows the lobby (``Account.showGUI``).
        self.sync_done = True
        if self.on_sync_done is not None:
            try:
                self.on_sync_done(self)
            except Exception:
                log.exception('sync-done callback failed')

    # -- chat actions ----------------------------------------------------
    def apply_action(self, action):
        account = self.account
        if account is None or not action:
            return
        try:
            account.onChatAction(action)
        except Exception:
            log.exception('cannot deliver a chat action')

    # -- streamed answers ------------------------------------------------
    def apply_stream(self, message):
        account = self.account
        if account is None:
            return
        request_id = int(message.get('requestID', 0))
        desc = tuple(message.get('desc') or ())
        payload = message.get('data') or ''
        if len(desc) != 5:
            log.warning('bad stream header %r for request %s', desc, request_id)
            return
        try:
            account.onStreamComplete(request_id, desc, payload)
        except Exception:
            log.exception('cannot deliver the stream of request %s', request_id)

    # -- the GUI ----------------------------------------------------------
    def show_gui(self, is_premium=False):
        """``Account.showGUI`` - the answer of the login the client waits for."""
        account = self.account
        if account is None or self._show_gui_done:
            return
        self._show_gui_done = True
        import time
        ctx = {'databaseID': self.database_id,
               'currentVehInvID': self.first_vehicle_inv_id,
               'isInRandomQueue': False,
               'isInTutorialQueue': False,
               'isInUnitAssembler': False,
               'isLongDisconnectedFromCenter': False,
               'serverUTC': int(time.time()),
               'bootcampCompletedCount': 0,
               'bootcampRunCount': 0,
               'bootcampNeedAwarding': False}
        try:
            account.showGUI(cPickle.dumps(ctx, 2))
            log.info('showGUI sent (vehicle %s)', self.first_vehicle_inv_id)
        except Exception:
            log.exception('cannot show the lobby')
