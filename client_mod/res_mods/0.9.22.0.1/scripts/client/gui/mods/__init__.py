# -*- coding: utf-8 -*-
"""The client's mod loader, accepting ``.py`` sources as well as ``.pyc``.

This file replaces ``res/scripts/client/gui/mods/__init__.py``: a release build
of the client only looks for ``mod_*.pyc`` (``_MOD_NAME_POSTFIX``), while the
mod in this folder is shipped as Python 2.7 *sources* so it can be read and
edited without a compiler.  The logic below is the stock one with the file name
check widened; every other package in ``gui/mods`` keeps working, and a
``mod_*.pyc`` a user drops in is still picked up first.
"""

import importlib
import ResMgr
import string
from constants import IS_DEVELOPMENT
from debug_utils import LOG_DEBUG, LOG_ERROR, LOG_CURRENT_EXCEPTION, LOG_WARNING
from shared_utils import forEach

_mods = {}
if IS_DEVELOPMENT:
    _MOD_NAME_POSTFIXES = ('.py', '.pyc')
else:
    _MOD_NAME_POSTFIXES = ('.pyc', '.py')


def init():
    global _mods
    _mods = _findValidMODs()
    forEach(lambda mod: _callModMethod(mod, 'init'), _mods.itervalues())


def fini():
    forEach(lambda mod: _callModMethod(mod, 'fini'), _mods.itervalues())
    _mods.clear()


def sendEvent(eventName, *args, **kwargs):
    forEach(lambda mod: _callModMethod(mod, eventName, *args, **kwargs), _mods.itervalues())


def _callModMethod(mod, methodName, *args, **kwargs):
    try:
        return getattr(mod, methodName)(*args, **kwargs)
    except AttributeError:
        pass
    except Exception:
        LOG_CURRENT_EXCEPTION()


def _getPostfix(scriptName):
    """The postfix of a mod file name, or ``None`` when it is not a mod."""
    if not scriptName.startswith('mod_'):
        return None
    for postfix in _MOD_NAME_POSTFIXES:
        if scriptName.endswith(postfix):
            return postfix
    return None


def _findValidMODs(path=None, package=None):
    result = {}
    path = path or __path__[0]
    package = package or __package__
    modsFolder = ResMgr.openSection(path)
    if modsFolder:
        found = {}
        for scriptName in set(map(string.lower, modsFolder.keys())):
            postfix = _getPostfix(scriptName)
            if postfix is None:
                continue
            moduleName = '%s.%s' % (package, scriptName[:-len(postfix)])
            # A compiled mod wins over the same mod shipped as source.
            if moduleName in found and postfix != '.pyc':
                continue
            found[moduleName] = scriptName
        for moduleName in sorted(found):
            scriptName = found[moduleName]
            try:
                result[moduleName] = importlib.import_module(moduleName)
                LOG_DEBUG('Gui mod found', moduleName, result[moduleName])
            except ImportError:
                LOG_WARNING('There is problem while import gui mod', package, scriptName)
                LOG_CURRENT_EXCEPTION()
            except Exception:
                LOG_WARNING('There is problem while import gui mod', package, scriptName)
                LOG_CURRENT_EXCEPTION()

    else:
        LOG_ERROR('There is no mods folder found', __path__)
    return result
