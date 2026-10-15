# -*- coding: utf-8 -*-
"""Glues the bridge, the fake login and the account proxy together.

``init()`` is called by ``gui.mods`` once the client is up; from there:

1. the bridge connects to the local server (and reconnects on its own),
2. the login patches are installed and the local host is registered,
3. a timer triggers the login as soon as the client is able to log in,
4. when the account data has arrived, ``Account.showGUI`` shows the lobby with
   the New Year hangar (the notification of the first sync switched the space).
"""

import BigWorld

from gui.mods.wotny import config
from gui.mods.wotny import log
from gui.mods.wotny.account_proxy import AccountBridge, RemoteServer
from gui.mods.wotny.bridge import Bridge
from gui.mods.wotny.fake_login import OfflineLogin

_session = None


class Session(object):
    """One client session against the local server."""

    def __init__(self):
        self.bridge = Bridge(on_message=self._on_message)
        self.remote_server = RemoteServer(self.bridge)
        self.accounts = AccountBridge(self.bridge)
        self.login = OfflineLogin(self)
        self.server_settings = {}
        self._timer = None
        self._delay = config.CONNECT_DELAY
        self._login_requested = False
        self._show_gui_done = False
        self.accounts.on_sync_done = self._on_sync_done

    # -- lifecycle -------------------------------------------------------
    def start(self):
        self.login.install()
        self.login.register_host()
        self.bridge.start()
        self._schedule(config.CONNECT_DELAY)

    def fini(self):
        self._cancel()
        try:
            self.login.uninstall()
        except Exception:
            log.exception('cannot undo the login patches')
        try:
            self.bridge.stop()
        except Exception:
            log.exception('cannot stop the bridge')
        log.info('local server session finished')

    # -- the login timer -------------------------------------------------
    def _schedule(self, delay):
        self._cancel()
        self._timer = BigWorld.callback(max(delay, 0.5), self._tick)

    def _cancel(self):
        if self._timer is not None:
            try:
                BigWorld.cancelCallback(self._timer)
            except Exception:
                pass
            self._timer = None

    def _tick(self):
        self._timer = None
        try:
            if not self._show_gui_done:
                if self.accounts.account is None:
                    if not self._login_requested or not self.login.is_connected():
                        if self.login.connect():
                            self._login_requested = True
                if self.accounts.sync_done:
                    self._show_gui()
        except Exception:
            log.exception('the login timer failed')
        if not self._show_gui_done:
            self._schedule(config.CONNECT_RETRY_PERIOD)

    def _show_gui(self):
        self.accounts.show_gui()
        self._show_gui_done = True
        log.info('the lobby is being shown')

    def _on_sync_done(self, _accounts):
        log.info('the account data is in the client')

    # -- the bridge ------------------------------------------------------
    def _on_message(self, message):
        kind = message.get('type')
        if kind == 'welcome':
            settings = (message.get('diff') or {}).get(('serverSettings', '_r'))
            if settings:
                # ``Account.__init__`` copies this into the repository before the
                # first sync, and parts of the login UI read it from there.
                self.server_settings = dict(settings)
            self.accounts.on_message(message)
            return
        self.accounts.on_message(message)

    def attach(self, account):
        self.accounts.attach(account)


def init():
    global _session
    if _session is not None:
        return _session
    log.info('starting the local server mod %s (%s:%s)', config.VERSION,
             config.HOST, config.PORT)
    try:
        session = _session = Session()
        session.start()
    except Exception:
        log.exception('cannot start the local server mod')
        _session = None
    return _session


def fini():
    global _session
    if _session is None:
        return
    try:
        _session.fini()
    finally:
        _session = None


def session():
    return _session
