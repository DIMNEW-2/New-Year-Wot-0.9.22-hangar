# -*- coding: utf-8 -*-
"""Framing of the socket the local server listens on.

One message is a length prefixed, zlib compressed pickle (protocol 2).  This is
the mirror of ``server/proto/py2pickle.py`` on the client side: the server runs
Python 3 and writes pickles Python 2.7 can read (protocol 2, ``bytes`` through
``_codecs.encode``), the client writes plain protocol 2 pickles back.
"""

import cPickle
import struct
import zlib

LENGTH_FORMAT = '!I'
LENGTH_SIZE = struct.calcsize(LENGTH_FORMAT)

#: A frame is capped so a corrupt length prefix cannot exhaust memory.
MAX_FRAME_SIZE = 32 * 1024 * 1024

PICKLE_PROTOCOL = 2


class ProtocolError(Exception):
    pass


def pack_message(message):
    payload = zlib.compress(cPickle.dumps(message, PICKLE_PROTOCOL))
    return struct.pack(LENGTH_FORMAT, len(payload)) + payload


def unpack_message(frame):
    if len(frame) > MAX_FRAME_SIZE:
        raise ProtocolError('frame is too large: %d bytes' % len(frame))
    try:
        return cPickle.loads(zlib.decompress(frame))
    except Exception as error:
        raise ProtocolError('cannot decode frame: %s' % (error,))


def _read_exactly(stream, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise ProtocolError('connection closed after %d of %d bytes'
                                % (size - remaining, size))
        chunks.append(chunk)
        remaining -= len(chunk)
    return ''.join(chunks)


def read_frame(stream):
    """Read one length prefixed frame from a file object (``sock.makefile``)."""
    header = _read_exactly(stream, LENGTH_SIZE)
    size = struct.unpack(LENGTH_FORMAT, header)[0]
    if size > MAX_FRAME_SIZE:
        raise ProtocolError('frame is too large: %d bytes' % size)
    return _read_exactly(stream, size)


def pack_stream(value):
    """``(header, payload)`` of a streamed account answer.

    The client reads a streamed answer with ``Account.onStreamComplete``:
    ``data`` is ``zlib(pickle(obj))`` and the header is
    ``(isCorrupted, origPacketLen, packetLen, origCrc32, crc32)``.
    """
    payload = zlib.compress(cPickle.dumps(value, PICKLE_PROTOCOL))
    crc = zlib.crc32(payload) & 0xffffffff
    return (False, len(payload), len(payload), crc, crc), payload
