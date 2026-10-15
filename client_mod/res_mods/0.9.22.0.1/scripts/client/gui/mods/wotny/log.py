# -*- coding: utf-8 -*-
"""A tiny logger that survives the client's own logging setup.

The client prints to ``python.log``; the local server writes its own file next
to it (``wotny.log``) so a report from a user is easy to read.  Everything the
logger does is wrapped: logging must never be the reason the mod fails.
"""

import os
import tempfile
import time
import traceback

from gui.mods.wotny import config

_PREFIX = '[wotny]'
_stream = None
_stream_path = None


def _candidate_paths():
    if config.LOG_PATH:
        yield config.LOG_PATH
    yield os.path.join(tempfile.gettempdir(), 'wotny.log')
    try:
        # ``python.log`` and the client's preferences live in the game folder.
        import BigWorld
        preferences = BigWorld.wg_getPreferencesFilePath()
        yield os.path.join(os.path.dirname(preferences), 'wotny.log')
    except Exception:
        pass


def path():
    """The file the log is currently written to (may be ``None``)."""
    if _stream_path is not None:
        return _stream_path
    for candidate in _candidate_paths():
        try:
            directory = os.path.dirname(candidate)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            return candidate
        except Exception:
            continue
    return None


def _open():
    global _stream, _stream_path
    if _stream is not None:
        return _stream
    candidate = path()
    if not candidate:
        return None
    try:
        _stream = open(candidate, 'a')
        _stream_path = candidate
    except Exception:
        _stream = None
    return _stream


def write(message):
    """Append one line to the mod log and to the client's stdout."""
    text = '%s %s %s\n' % (_PREFIX, time.strftime('%H:%M:%S'), message)
    try:
        stream = _open()
        if stream is not None:
            stream.write(text)
            stream.flush()
    except Exception:
        pass
    try:
        print(text.rstrip('\n'))
    except Exception:
        pass


def info(message, *args):
    write(message % args if args else message)


def warning(message, *args):
    write('WARNING: ' + (message % args if args else message))


def exception(message, *args):
    write('ERROR: ' + (message % args if args else message))
    try:
        stream = _open()
        if stream is not None:
            traceback.print_exc(file=stream)
            stream.flush()
    except Exception:
        pass
    try:
        traceback.print_exc()
    except Exception:
        pass


def close():
    global _stream
    try:
        if _stream is not None:
            _stream.close()
    except Exception:
        pass
    _stream = None
