"""Reading a data file of the client: compression wrappers and encodings.

The XML of a client is *usually* plain UTF-8, but the files travel in containers
that a build can pack its own way: the ``.pkg`` archives of a release
installation store their members in a zip, and a packer is free to compress a
member's content *again* (or to store a member that is a zip of its own).  A file
can also be UTF-16, or use the code page of a localized build.

:mod:`server.game.defs` reads the client data, and a wrong guess there ends in a
bare "cannot parse XML" - which is exactly what a user cannot act on.  So every
payload goes through this module first:

* :func:`unwrap` undoes the compression wrappers it recognizes (gzip, zlib, raw
  deflate, bzip2, xz, zstd) and says what it undid,
* :func:`decode` turns bytes into text and tries the BOM, the code page the XML
  declaration names, UTF-8 and then the code pages a localized build uses (a
  Chinese build is a real client too),
* :func:`describe` renders what a payload *looks like* - the first bytes in hex
  and as text, the guessed format - for an error message a user can report,
* :func:`sniff` is the machine-readable version of :func:`describe`, used by
  ``python3 -m server inspect``.

Nothing here raises: a payload that cannot be made sense of is returned as it
is, and the caller decides (the XML parser complains, and :func:`describe`
makes that complaint readable).
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import zipfile
import zlib
from typing import Optional, Tuple

#: BOMs, longest first (UTF-32 starts with the UTF-16 marker).
_BOMS = (
    ("utf-32-le", b"\xff\xfe\x00\x00"),
    ("utf-32-be", b"\x00\x00\xfe\xff"),
    ("utf-8-sig", b"\xef\xbb\xbf"),
    ("utf-16-le", b"\xff\xfe"),
    ("utf-16-be", b"\xfe\xff"),
)

#: ``encoding="…"`` of an XML declaration (the first line of a text file).
_DECLARATION_ENCODING = re.compile(rb"encoding\s*=\s*[\"']([A-Za-z0-9_.:+-]+)[\"']")

#: The code pages worth trying after UTF-8, most specific first: a Chinese build
#: writes GBK (which GB18030 is a superset of), a localized one may write 1251.
_FALLBACK_ENCODINGS = ("gb18030", "cp936", "cp1251", "cp1252", "latin-1")

#: Magic numbers of the containers a payload can travel in.
GZIP_MAGIC = b"\x1f\x8b"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
ZIP_MAGIC = b"PK\x03\x04"

#: How often a payload may be unwrapped (a zip in a zip in a zip is enough).
MAX_UNWRAP = 3

#: Bytes shown in :func:`describe`.
PREVIEW = 48


def is_zlib(data: bytes) -> bool:
    """``True`` for a zlib stream (``78 01``/``78 9c``/``78 da``/…)."""
    if len(data) < 2 or (data[0] & 0x0F) != 8 or (data[0] >> 4) > 7:
        return False
    return (data[0] << 8 | data[1]) % 31 == 0


def _first_bytes(data: bytes) -> bytes:
    """The payload without a leading BOM (for the "is this XML" questions)."""
    for _name, bom in _BOMS:
        if data.startswith(bom):
            return data[len(bom):]
    return data


def looks_like_xml(data: bytes) -> bool:
    """``True`` when the payload starts with an XML declaration or a tag."""
    head = _first_bytes(data).lstrip()
    if not head:
        return False
    if head[:1] == b"<":
        return True
    # UTF-16 without a BOM: every other byte is a NUL.
    if len(head) > 4 and head[1:2] == b"\x00" and head[3:4] == b"\x00":
        return head[0:1] == b"<"
    if len(head) > 4 and head[0:1] == b"\x00" and head[2:3] == b"\x00":
        return head[1:2] == b"<"
    return False


def looks_like_text(data: bytes) -> bool:
    """``True`` when a payload is probably readable text (no binary soup)."""
    if not data:
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        # Text in UTF-16 has NULs in every other byte; anything else is binary.
        odd = sample[1::2].count(0)
        even = sample[0::2].count(0)
        return odd > len(sample) // 4 or even > len(sample) // 4
    printable = sum(1 for byte in sample
                    if byte in (9, 10, 13) or 32 <= byte < 127 or byte >= 128)
    return printable >= int(len(sample) * 0.95)


def _zstd_decompress(data: bytes) -> Optional[bytes]:
    try:  # Python 3.14 ships this module; older ones need the package.
        from compression import zstd as _zstd  # type: ignore

        return _zstd.decompress(data)
    except Exception:  # noqa: BLE001 - the module is optional
        pass
    try:
        import zstandard  # type: ignore

        return zstandard.ZstdDecompressor().decompress(data)
    except Exception:  # noqa: BLE001
        return None


def _read_nested_zip(data: bytes) -> Optional[bytes]:
    """The single member of a zip that *is* the payload (a packer may do that)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if len(members) == 1:
                return archive.read(members[0])
    except Exception:  # noqa: BLE001 - not a zip, or not a usable one
        return None
    return None


def _try(call, data: bytes) -> Optional[bytes]:
    """Run a decompressor, keeping only a plausible result."""
    try:
        result = call(data)
    except Exception:  # noqa: BLE001 - every codec raises its own error type
        return None
    if isinstance(result, bytes) and result and len(result) < len(data) * 1024:
        return result
    return None


def _codec_name(data: bytes) -> str:
    if data.startswith(GZIP_MAGIC):
        return "gzip"
    if data.startswith(BZIP2_MAGIC):
        return "bzip2"
    if data.startswith(XZ_MAGIC):
        return "xz"
    if data.startswith(ZSTD_MAGIC):
        return "zstd"
    if is_zlib(data):
        return "zlib"
    return ""


def _plausible(result: bytes) -> bool:
    """A unwrapped payload worth keeping: readable, XML, or another wrapper."""
    return (looks_like_text(result) or looks_like_xml(result)
            or bool(_codec_name(result)) or result.startswith(ZIP_MAGIC))


def _unwrap_once(payload: bytes) -> Tuple[str, Optional[bytes]]:
    """``(codec, decompressed)`` of one wrapper, or ``("", None)``."""
    if not payload:
        return "", None
    for magic, codec, decompress in (
        (GZIP_MAGIC, "gzip", gzip.decompress),
        (BZIP2_MAGIC, "bzip2", bz2.decompress),
        (XZ_MAGIC, "xz", lzma.decompress),
    ):
        if payload.startswith(magic):
            return codec, _try(decompress, payload)
    if payload.startswith(ZSTD_MAGIC):
        return "zstd", _zstd_decompress(payload)
    if payload.startswith(ZIP_MAGIC):
        # A member that is itself a one-file zip: some packers wrap that way.
        result = _read_nested_zip(payload)
        if result is not None and _plausible(result):
            return "a zip around it", result
        return "", None
    if looks_like_xml(payload):
        return "", None
    if is_zlib(payload):
        result = _try(lambda raw: zlib.decompress(raw, 15), payload)
        if result is not None and _plausible(result):
            return "zlib", result
        return "", None
    # A *raw* deflate stream - what a packer that writes its own zlib header
    # produces - carries no magic number at all; the only way to know is to try
    # it, and only when the payload is not readable already.
    result = _try(lambda raw: zlib.decompress(raw, -15), payload)
    if result is not None and _plausible(result):
        return "raw deflate", result
    return "", None


def unwrap(data: bytes) -> Tuple[bytes, str]:
    """Undo the compression wrappers of ``data``.

    Returns the payload and a note like ``"zlib"`` (or ``"gzip+raw deflate"``)
    naming what was undone; an empty note means the payload travelled as it is.
    """
    notes = []
    payload = data
    for _round in range(MAX_UNWRAP):
        codec, result = _unwrap_once(payload)
        if result is None:
            break
        payload = result
        notes.append(codec)
    return payload, "+".join(notes)


def format_name(data: bytes) -> str:
    """What a payload looks like, in a few words (for a log or an error)."""
    if not data:
        return "empty"
    for encoding, bom in _BOMS:
        if data.startswith(bom):
            return "%s text%s" % (encoding, " (XML)" if looks_like_xml(data) else "")
    if data.startswith(ZIP_MAGIC):
        return "a nested zip archive"
    codec = _codec_name(data)
    if codec:
        return "%s-compressed" % codec
    if looks_like_xml(data):
        return "XML"
    if len(data) > 4 and data[1:2] == b"\x00" and data[3:4] == b"\x00":
        return "UTF-16LE text"
    if len(data) > 4 and data[0:1] == b"\x00" and data[2:3] == b"\x00":
        return "UTF-16BE text"
    if looks_like_text(data):
        return "text"
    return "binary data"


def preview(data: bytes, size: int = PREVIEW) -> str:
    """``hex | text`` of the first bytes - the line a bug report needs."""
    head = data[:size]
    hexed = " ".join("%02x" % byte for byte in head)
    text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in head)
    return "%s  |%s|%s" % (hexed, text, " …" if len(data) > size else "")


def decode(data: bytes) -> Tuple[str, str]:
    """Bytes to text.  Returns ``(text, encoding)``.

    The order is: the BOM the file carries, the code page its XML declaration
    names, UTF-8, and then the code pages a localized build uses.  A payload
    that decodes as none of them is returned as UTF-8 with the undecodable
    bytes replaced - the caller still gets *something* to show.
    """
    head = data[:4096]
    for encoding, bom in _BOMS:
        if data.startswith(bom):
            try:
                return data[len(bom):].decode(encoding), encoding
            except UnicodeDecodeError:
                break
    if b"\x00" in head:
        odd = head[1::2].count(0)
        even = head[0::2].count(0)
        if odd > len(head) // 4:
            try:
                return data.decode("utf-16-le"), "utf-16-le"
            except UnicodeDecodeError:
                pass
        elif even > len(head) // 4:
            try:
                return data.decode("utf-16-be"), "utf-16-be"
            except UnicodeDecodeError:
                pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    candidates = []
    declared = _DECLARATION_ENCODING.search(head)
    if declared:
        candidates.append(declared.group(1).decode("ascii", "replace"))
    candidates.extend(_FALLBACK_ENCODINGS)
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace"), "utf-8 (with replacements)"


def read_payload(data: bytes) -> Tuple[str, str]:
    """``(text, note)`` of one file: the wrappers undone and the encoding used."""
    payload, undone = unwrap(data)
    text, encoding = decode(payload)
    notes = []
    if undone:
        notes.append(undone)
    notes.append(encoding)
    return text, ", ".join(notes)


def sniff(data: bytes) -> dict:
    """A machine-readable summary of a payload (``python3 -m server inspect``)."""
    payload, undone = unwrap(data)
    text, encoding = decode(payload)
    return {
        "size": len(data),
        "format": format_name(data),
        "payloadSize": len(payload),
        "unwrapped": undone,
        "encoding": encoding,
        "xml": looks_like_xml(payload),
        "head": preview(data, 24),
        "text": text[:60].replace("\n", " "),
    }


def describe(rel_path: str, data: bytes, source: str = "") -> str:
    """A paragraph about a payload that could not be read.

    Everything a reader needs to see what is wrong: what the file looks like,
    whether a wrapper was recognized, and the first bytes in hex and as text.
    """
    payload, undone = unwrap(data)
    _text, encoding = decode(payload)
    lines = [
        "%s is not XML (%s%s)" % (rel_path, format_name(data),
                                  " in %s" % source if source else ""),
        "  size  : %d bytes%s" % (len(data), ", %d after unwrapping" % len(payload)
                                  if len(payload) != len(data) else ""),
        "  codec : %s" % (undone or "none recognized (gzip/zlib/raw deflate/bzip2/xz/zstd)"),
        "  text  : %s" % encoding,
        "  first : %s" % preview(data),
    ]
    if looks_like_text(payload) and not looks_like_xml(payload):
        lines.append("  note  : the payload reads as text but not as XML - it may be "
                     "a different file than expected")
    return "\n".join(lines)


__all__ = [
    "BZIP2_MAGIC",
    "GZIP_MAGIC",
    "MAX_UNWRAP",
    "PREVIEW",
    "XZ_MAGIC",
    "ZIP_MAGIC",
    "ZSTD_MAGIC",
    "decode",
    "describe",
    "format_name",
    "is_zlib",
    "looks_like_text",
    "looks_like_xml",
    "preview",
    "read_payload",
    "sniff",
    "unwrap",
]
