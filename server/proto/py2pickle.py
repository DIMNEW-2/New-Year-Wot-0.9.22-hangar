"""Pickles and frames readable by the Python 2.7 client of 0.9.22.

The game client of the supported build (Chinese HD ``0.9.22.0.1 #1513``) embeds
Python 2.7 and unpickles the payloads this server produces, so the streams have
to be written the way *Python 2's* pickle module writes them:

* protocol 2 (``pickle.dumps(..., 2)``) - Python 2.7 reads protocols 0-2 and the
  opcode set of protocol 2 contains no ``STACK_GLOBAL`` or other py3-only frame,
* ``str`` values become ``BINUNICODE`` and load as ``unicode`` on the client
  (Python 2 compares ASCII ``unicode`` and ``str`` keys as equal, which is what
  the client's ``diff.get('inventory')`` style lookups rely on),
* ``bytes`` values are pickled by Python 3 as ``_codecs.encode(data, 'latin1')``
  which Python 2 loads back as ``str`` - this is how the client's byte-typed
  fields (``toyCollection``, ``compDescr`` blobs, ...) must be sent.

``pack_stream``/``unpack_stream`` mirror the client's own framing
(``zlib``-compressed pickle plus a CRC32 header) used for streamed sync data,
``pack_message``/``unpack_message`` frame one pickled message with a length
prefix for the socket the client mod talks to.
"""

from __future__ import annotations

import pickle
import struct
import zlib

PROTOCOL_VERSION = 1

#: Protocol 2 is the newest protocol Python 2.7 understands without extensions.
PICKLE_PROTOCOL = 2

LENGTH_FORMAT = "!I"
LENGTH_SIZE = struct.calcsize(LENGTH_FORMAT)

#: A single frame is capped so a corrupt length prefix cannot exhaust memory.
MAX_FRAME_SIZE = 32 * 1024 * 1024


class ProtocolError(Exception):
    """Raised when a frame cannot be decrypted or decoded."""


def _to_wire(value):
    """Recursively convert ``value`` into something Python 2 can unpickle.

    ``bytes`` is intentionally left alone: Python 3 pickles it so that Python 2
    loads it as ``str``, which is what the client expects for its byte fields.
    """
    if isinstance(value, dict):
        return {_to_wire(key): _to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        converted = [_to_wire(item) for item in value]
        if isinstance(value, tuple):
            return tuple(converted)
        if isinstance(value, set):
            return set(converted)
        if isinstance(value, frozenset):
            return frozenset(converted)
        return converted
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ProtocolError("cannot pickle %r for the client" % (type(value).__name__,))


def py2_dumps(value, protocol: int = PICKLE_PROTOCOL) -> bytes:
    """Pickle ``value`` so that Python 2.7 of the client can load it."""
    return pickle.dumps(_to_wire(value), protocol)


def py2_loads(data):
    """Load a pickle produced by either side of the bridge."""
    return pickle.loads(data)


def pack_stream(value):
    """Return ``(header, payload)`` exactly like the client's streamed sync.

    ``header`` is ``(isCompressed, compressedSize, uncompressedSize, crc, crc)``;
    the payload is the Pickle stream this repository inlines into frames.
    """
    payload = zlib.compress(py2_dumps(value))
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return (False, len(payload), len(payload), crc, crc), payload


def unpack_stream(payload):
    """Inverse of the payload half of :func:`pack_stream`."""
    return py2_loads(zlib.decompress(payload))


def pack_message(message) -> bytes:
    """Frame one message for the socket bridge."""
    payload = zlib.compress(py2_dumps(message))
    return struct.pack(LENGTH_FORMAT, len(payload)) + payload


def unpack_message(frame: bytes):
    """Decode a frame produced by :func:`pack_message` (length prefix excluded)."""
    if len(frame) > MAX_FRAME_SIZE:
        raise ProtocolError("frame is too large: %d bytes" % len(frame))
    try:
        return py2_loads(zlib.decompress(frame))
    except Exception as error:  # pragma: no cover - defensive
        raise ProtocolError("cannot decode frame: %s" % (error,))


def read_frame(stream) -> bytes:
    """Read one length-prefixed frame from a socket-like object."""
    header = _read_exactly(stream, LENGTH_SIZE)
    (size,) = struct.unpack(LENGTH_FORMAT, header)
    if size > MAX_FRAME_SIZE:
        raise ProtocolError("frame is too large: %d bytes" % size)
    return _read_exactly(stream, size)


def write_frame(stream, message) -> None:
    """Write one framed message to a socket-like object."""
    stream.write(pack_message(message))


def _read_exactly(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise ProtocolError("connection closed after %d of %d bytes" % (size - remaining, size))
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


__all__ = [
    "LENGTH_FORMAT",
    "LENGTH_SIZE",
    "MAX_FRAME_SIZE",
    "PICKLE_PROTOCOL",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "pack_message",
    "pack_stream",
    "py2_dumps",
    "py2_loads",
    "read_frame",
    "unpack_message",
    "unpack_stream",
    "write_frame",
]
