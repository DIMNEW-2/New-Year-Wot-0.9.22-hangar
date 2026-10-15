# -*- coding: utf-8 -*-
"""Pretends the login server is up and hands the client a local account.

The 0.9.22 client cannot be pointed at a different account server from a config
file: ``connection_mgr.ConnectionManager.__connect`` calls ``BigWorld.connect``
with the host it resolved from the login screen and the engine builds the
``Account`` entity from what the server asks it to build.  The mod therefore

1. registers the local server as a *predefined host* and redirects
   ``ConnectionManager.initiateConnection`` to it,
2. answers the engine's ``BigWorld.connect`` itself: it reports
   ``LOGIN_STATUS.LOGGED_ON`` through the very callback the connection manager
   passed in (so all of its own events fire) and then builds the account,
3. creates the client-only ``Account`` entity (``BigWorld.createSpace`` +
   ``createEntity`` + ``player``) with the properties ``Account.__init__`` reads
   (``name``, ``initialServerSettings``, the client/server version), and
4. swallows ``BigWorld.clearAllSpaces`` for the duration of
   ``Account.onBecomePlayer``, which otherwise destroys the space the local
   account lives in.

Everything else (diffs, answers, chat actions) is the bridge's job.
"""

import cPickle
import json
import time

import BigWorld

from gui.mods.wotny import config
from gui.mods.wotny import log

#: ``connection_mgr.LOGIN_STATUS.LOGGED_ON``
LOGIN_STATUS_LOGGED_ON = 5

#: Login parameters of the fake session (``Manager.initiateLogin`` builds the
#: same dict; ``auth_method`` is ``CONNECTION_METHOD.BASIC``).
LOGIN_METHOD_BASIC = 'basic'


class OfflineLogin(object):
    """The patched login path."""

    def __init__(self, session):
        self.session = session
        self._client_server_version = None
        self._original_connect = None
        self._original_disconnect = None
        self._original_initiate = None
        self._original_account_init = None
        self._original_account_getattribute = None
        self._original_become_player = None
        self._original_clear_all_spaces = None
        self._account_type = None
        self._connection_manager = None
        self._connecting = False
        self._fake_connected = False
        self._host = None
        self._installed = False

    # -- installation ----------------------------------------------------
    def install(self):
        if self._installed:
            return
        from Account import PlayerAccount
        from connection_mgr import ConnectionManager

        self._account_type = PlayerAccount
        self._original_connect = BigWorld.connect
        self._original_disconnect = BigWorld.disconnect
        self._original_initiate = ConnectionManager.initiateConnection
        self._original_account_init = PlayerAccount.__dict__.get(
            '__init__', PlayerAccount.__init__)
        self._original_account_getattribute = PlayerAccount.__dict__.get(
            '__getattribute__', PlayerAccount.__getattribute__)
        self._original_become_player = PlayerAccount.__dict__.get(
            'onBecomePlayer', PlayerAccount.onBecomePlayer)
        self._original_clear_all_spaces = BigWorld.clearAllSpaces

        login = self

        # ``__serverResponseHandler`` calls this before it fires its own events;
        # the Chinese HD build has no WGC integration, so give it something to
        # call instead of letting the whole login abort on an AttributeError.
        if not hasattr(BigWorld, 'WGC_onServerResponse'):
            BigWorld.WGC_onServerResponse = lambda is_success: None
            log.info('BigWorld.WGC_onServerResponse stubbed')

        def connect(server, login_params, progress):
            if not login._is_local(server):
                return login._original_connect(server, login_params, progress)
            log.info('local login requested for %s', server)
            login._fake_connected = True
            login._connecting = True
            # The callback belongs to the connection manager: delivering the
            # answer through it sets its status code and fires onLoggedOn and
            # onConnected exactly like a real server answer.
            progress(1, LOGIN_STATUS_LOGGED_ON,
                     json.dumps({'name': config.ACCOUNT_NAME, 'errorMessage': '',
                                 'token2': '', 'security_msg': ''}))
            login._create_account()
            login._connecting = False
            return None

        def disconnect():
            if not login._fake_connected:
                return login._original_disconnect()
            log.info('disconnect requested by the client')
            login._fake_connected = False
            return None

        def initiate(manager, params, password, server_name):
            login._connection_manager = manager
            return login._original_initiate(manager, params, password,
                                            config.SERVER_URL)

        def account_init(account):
            if not login._connecting:
                return login._original_account_init(account)
            account.isOffline = True
            account.name = config.ACCOUNT_NAME
            account.initialServerSettings = dict(login.session.server_settings)
            property_name, property_value = login._read_client_server_version()
            setattr(account, property_name, property_value)
            account.fakeServer = login.session.remote_server
            return login._original_account_init(account)

        def account_getattribute(account, name):
            if name in ('base', 'cell', 'server'):
                try:
                    is_offline = login._original_account_getattribute(
                        account, 'isOffline')
                except AttributeError:
                    is_offline = False
                if is_offline:
                    return login._original_account_getattribute(
                        account, 'fakeServer')
            return login._original_account_getattribute(account, name)

        def become_player(account):
            is_offline = bool(getattr(account, 'isOffline', False))
            if not is_offline:
                return login._original_become_player(account)
            # ``PlayerAccount.onBecomePlayer`` starts with
            # ``BigWorld.clearAllSpaces()``, which would destroy the very space
            # the local account was created in.
            preserve = login._original_clear_all_spaces

            def keep_spaces(*args, **kwargs):
                return None

            BigWorld.clearAllSpaces = keep_spaces
            login.session.attach(account)
            try:
                result = login._original_become_player(account)
            finally:
                BigWorld.clearAllSpaces = preserve
            return result

        BigWorld.connect = connect
        BigWorld.disconnect = disconnect
        ConnectionManager.initiateConnection = initiate
        PlayerAccount.__init__ = account_init
        PlayerAccount.__getattribute__ = account_getattribute
        PlayerAccount.onBecomePlayer = become_player
        self._installed = True
        log.info('login patches installed (host %s)', config.SERVER_URL)

    def uninstall(self):
        if not self._installed:
            return
        BigWorld.connect = self._original_connect
        BigWorld.disconnect = self._original_disconnect
        BigWorld.clearAllSpaces = self._original_clear_all_spaces
        self._account_type.__init__ = self._original_account_init
        self._account_type.__getattribute__ = self._original_account_getattribute
        self._account_type.onBecomePlayer = self._original_become_player
        self._installed = False

    # -- the login flow ---------------------------------------------------
    def register_host(self):
        """Make the local server the host the login screen would pick."""
        from predefined_hosts import g_preDefinedHosts
        for host in g_preDefinedHosts._hosts:
            if host.url == config.SERVER_URL:
                self._host = host
                return
        host = g_preDefinedHosts._makeHostItem('Local server (New Year)',
                                               'Local', config.SERVER_URL)
        g_preDefinedHosts._hosts.append(host)
        g_preDefinedHosts._urlMap[config.SERVER_URL] = len(g_preDefinedHosts._hosts) - 1
        g_preDefinedHosts._nameMap[host.name] = len(g_preDefinedHosts._hosts) - 1
        self._host = host
        log.info('registered the local host %s', config.SERVER_URL)
        if config.AUTO_CONNECT:
            try:
                g_preDefinedHosts.autoLoginQuery = self._auto_login_query
            except Exception:
                log.exception('cannot patch the auto login query')

    def _auto_login_query(self, callback):
        """``g_preDefinedHosts.autoLoginQuery`` - the launcher's host lookup."""
        callback(self._host or config.SERVER_URL)

    def login_params(self):
        return {'login': config.ACCOUNT_NAME,
                'session': '',
                'temporary': '1',
                'auth_method': LOGIN_METHOD_BASIC}

    def connection_manager(self):
        if self._connection_manager is not None:
            return self._connection_manager
        try:
            from gui.shared.personality import ServicesLocator
            return ServicesLocator.connectionMgr
        except Exception:
            return None

    def connect(self):
        """Start the (fake) login if the client is not connected yet."""
        manager = self.connection_manager()
        if manager is None:
            return False
        try:
            if manager.isConnected():
                return True
        except Exception:
            return False
        try:
            log.info('starting the local login')
            manager.initiateConnection(self.login_params(), '',
                                       config.SERVER_URL)
        except Exception:
            log.exception('the local login failed')
            return False
        return True

    def is_connected(self):
        manager = self.connection_manager()
        try:
            return bool(manager is not None and manager.isConnected())
        except Exception:
            return False

    # -- building the account ---------------------------------------------
    def _create_account(self):
        try:
            space_id = BigWorld.createSpace()
            account_id = BigWorld.createEntity('Account', space_id, 0,
                                               (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), {})
            account = BigWorld.entities[account_id]
            BigWorld.player(account)
            log.info('created the local Account entity %s in space %s',
                     account_id, space_id)
            return account
        except Exception:
            log.exception('cannot create the local Account entity')
            self._fake_connected = False
            return None

    def _is_local(self, server):
        if not server:
            return True
        for prefix in (config.SERVER_URL, config.HOST + ':'):
            if server.startswith(prefix):
                return True
        return False

    def _read_client_server_version(self):
        """``requiredVersion_92200`` of the client's own ``Account.def``."""
        if self._client_server_version is not None:
            return self._client_server_version
        try:
            from account_shared import readClientServerVersion
            self._client_server_version = readClientServerVersion()
        except Exception:
            log.exception('cannot read the client/server version')
            self._client_server_version = ('requiredVersion_92200', '')
        return self._client_server_version
