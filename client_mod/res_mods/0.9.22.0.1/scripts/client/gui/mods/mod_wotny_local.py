# -*- coding: utf-8 -*-
"""Entry point of the local server mod for World of Tanks 0.9.22 (CH HD).

``gui.mods`` imports every ``mod_*.pyc`` of this folder and calls ``init()``
once the client is ready (``gui/shared/personality.py``).  Everything else lives
in the :mod:`gui.mods.wotny` package next to this file; a failure here only
disables the local server, it never breaks the stock client.
"""


def init():
    try:
        from gui.mods.wotny import bootstrap
        bootstrap.init()
    except Exception:
        import traceback
        traceback.print_exc()


def fini():
    try:
        from gui.mods.wotny import bootstrap
        bootstrap.fini()
    except Exception:
        import traceback
        traceback.print_exc()
