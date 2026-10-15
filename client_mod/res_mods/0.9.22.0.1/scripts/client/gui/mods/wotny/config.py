# -*- coding: utf-8 -*-
"""Settings of the local server mod.

Every value can be overridden from the environment so a user never has to edit
the mod to move the server or to turn parts of it off:

===========================  ==================================================
``WOTNY_HOST``               host of the local server (default ``127.0.0.1``)
``WOTNY_PORT``               port of the local server (default ``28422``)
``WOTNY_ACCOUNT``            account name shown in the lobby
``WOTNY_AUTO_CONNECT``       ``0`` disables the automatic login
``WOTNY_CONNECT_DELAY``      seconds to wait before the automatic login
``WOTNY_LOG``                file the mod writes its log to
``WOTNY_NO_HANGAR``          ``1`` keeps the stock hangar space (no New Year
                             scene switch)
===========================  ==================================================
"""

import os

VERSION = '1.0.0'

#: The address of the local server (``server/__main__.py serve``).
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 28422

#: The name of the account the local server hands out.
DEFAULT_ACCOUNT = 'local_account'

#: Seconds between two connection attempts while the game boots.
CONNECT_RETRY_PERIOD = 1.0

#: Seconds the mod waits after the login screen appears before it connects.
DEFAULT_CONNECT_DELAY = 3.0

#: Seconds between two polls of the bridge's incoming queue.
POLL_PERIOD = 0.05

#: Seconds without a message from the server after which the mod reconnects.
IDLE_TIMEOUT = 30.0


def _flag(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ('0', 'no', 'false', 'off', '')


def _number(name, default, cast):
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


HOST = os.environ.get('WOTNY_HOST') or DEFAULT_HOST
PORT = _number('WOTNY_PORT', DEFAULT_PORT, int)
ACCOUNT_NAME = os.environ.get('WOTNY_ACCOUNT') or DEFAULT_ACCOUNT
AUTO_CONNECT = _flag('WOTNY_AUTO_CONNECT', True)
CONNECT_DELAY = _number('WOTNY_CONNECT_DELAY', DEFAULT_CONNECT_DELAY, float)
LOG_PATH = os.environ.get('WOTNY_LOG') or ''
USE_NEW_YEAR_HANGAR = not _flag('WOTNY_NO_HANGAR', False)

#: ``auth_realm`` of the login parameters.  The stock client sends the realm of
#: the build (``constants.AUTH_REALM``); the local server ignores it but the
#: client's own connection manager rewrites it anyway.
SERVER_URL = '%s:%d' % (HOST, PORT)
