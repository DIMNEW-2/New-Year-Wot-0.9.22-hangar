"""Wire protocol shared by the local server and the in-client bridge."""

from __future__ import annotations

from .py2pickle import (
    LENGTH_FORMAT,
    MAX_FRAME_SIZE,
    PICKLE_PROTOCOL,
    PROTOCOL_VERSION,
    ProtocolError,
    pack_message,
    pack_stream,
    py2_dumps,
    py2_loads,
    read_frame,
    unpack_message,
    unpack_stream,
    write_frame,
)

__all__ = [
    "LENGTH_FORMAT",
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
