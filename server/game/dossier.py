"""The dossier compact descriptors of the client (``common/dossiers2``).

The client keeps the statistics of an account, of a vehicle and of a crew
member in *dossiers*.  A dossier is a compact descriptor:

* a 16-bit version (``H``),
* one 16-bit *size* per block of the layout, in layout order,
* the block payloads, one after another, in the same order
  (``dossiers2.common.DossierDescr`` / ``DossierBuilder``).

An account that never fought is represented by an **empty descriptor**:
``DossierBuilder.build('')`` packs the current version with every block zero
sized, and the client is written for that (every block is then built empty on
demand, ``DossierDescr.__getitem__``).  That is what this server publishes by
default, and it is why the local server never needs to know the block layout to
answer a dossier request.

Two things a description alone cannot express are the *blocks themselves*:

* the badges of a player **are** the ``playerBadges`` block of the account
  dossier - a dictionary of ``badgeID -> time it was earned``
  (``dossiers2.ui.achievements.BADGES_BLOCK``).  ``gui.shared.gui_items.badge``
  reads exactly that dictionary to decide whether a badge of the installation's
  ``item_defs/badges.xml`` was earned, so a badge that is not in this block
  cannot be selected in the client at all;
* the creation time of the account is ``total['creationTime']``, the date the
  profile window shows.

So this module mirrors the layout of build 0.9.22 and can pack and unpack those
descriptors.  The block names and their order are the client's own - the
``accountDossierLayout``/``vehicleDossierLayout``/``tmanDossierLayout`` tuples of
``common/dossiers2/custom/*_layout.py``, the versions are
``ACCOUNT_DOSSIER_VERSION``/``VEHICLE_DOSSIER_VERSION``/``TANKMAN_DOSSIER_VERSION``
of ``common/dossiers2/custom/updaters.py`` and the record formats are the
``RECORDS`` table of ``common/dossiers2/custom/records.py``.
``tests/test_dossier.py`` re-derives the three layouts from those files whenever
they are readable next to the game data (the decompiled client, or an unpacked
installation) and fails if this table drifts.

Only the blocks the server actually writes are described here; every other block
travels zero sized, which is a legal empty block for the client.
"""

from __future__ import annotations

import struct
from typing import Dict, Iterable, Mapping, Tuple

#: The three dossiers the account has (``constants.DOSSIER_TYPE``).
ACCOUNT = "account"
VEHICLE = "vehicle"
TANKMAN = "tankman"

#: ``VERSION_RECORD_FORMAT``/``BLOCK_SIZE_RECORD_FORMAT`` of
#: ``dossiers2/custom/layouts.py``.
VERSION_FORMAT = "H"
BLOCK_SIZE_FORMAT = "H"

ACCOUNT_DOSSIER_VERSION = 109
VEHICLE_DOSSIER_VERSION = 99
TANKMAN_DOSSIER_VERSION = 66

VERSIONS = {
    ACCOUNT: ACCOUNT_DOSSIER_VERSION,
    VEHICLE: VEHICLE_DOSSIER_VERSION,
    TANKMAN: TANKMAN_DOSSIER_VERSION,
}

#: The block of the account dossier that holds the earned badges
#: (``dossiers2.ui.achievements.BADGES_BLOCK``).
BADGES_BLOCK = "playerBadges"

#: The block that holds the dates of a dossier (``TOTAL_BLOCK_LAYOUT``).
TOTAL_BLOCK = "total"

ACCOUNT_BLOCKS = (
    "a15x15", "a15x15_2", "clan", "clan2",
    "company", "company2", "a7x7", "achievements",
    "vehTypeFrags", "a15x15Cut", "rareAchievements", "total",
    "a7x7Cut", "max15x15", "max7x7", "achievements7x7",
    "historical", "maxHistorical", "historicalAchievements", "historicalCut",
    "uniqueAchievements", "fortBattles", "maxFortBattles", "fortBattlesCut",
    "fortSorties", "maxFortSorties", "fortSortiesCut", "fortBattlesInClan",
    "maxFortBattlesInClan", "fortSortiesInClan", "maxFortSortiesInClan", "fortAchievements",
    "singleAchievements", "clanAchievements", "rated7x7", "maxRated7x7",
    "achievementsRated7x7", "rated7x7Cut", "globalMapMiddle", "globalMapChampion",
    "globalMapAbsolute", "maxGlobalMapMiddle", "maxGlobalMapChampion", "maxGlobalMapAbsolute",
    "globalMapCommonCut", "fallout", "falloutCut", "maxFallout",
    "falloutAchievements", "ranked", "maxRanked", "rankedCut",
    "rankedSeasons", "rankedCurrent", "rankedPrevious", "maxRankedCurrent",
    "maxRankedPrevious", "rankedCurrentCut", "rankedPreviousCut", "rankedCurrentCycle",
    "rankedPreviousCycle", "a30x30", "a30x30Cut", "max30x30",
    "markOfMasteryCut", "playerBadges",
)

VEHICLE_BLOCKS = (
    "a15x15", "a15x15_2", "clan", "clan2",
    "company", "company2", "a7x7", "achievements",
    "vehTypeFrags", "total", "max15x15", "max7x7",
    "inscriptions", "emblems", "camouflages", "compensation",
    "achievements7x7", "historical", "maxHistorical", "uniqueAchievements",
    "fortBattles", "maxFortBattles", "fortSorties", "maxFortSorties",
    "fortAchievements", "singleAchievements", "clanAchievements", "rated7x7",
    "maxRated7x7", "globalMapCommon", "maxGlobalMapCommon", "fallout",
    "maxFallout", "falloutAchievements", "ranked", "maxRanked",
    "rankedSeasons", "a30x30", "max30x30",
)

TANKMAN_BLOCKS = (
    "total", "achievements",
)

#: The block names of every dossier, in the order the sizes travel in.
BLOCKS: Dict[str, Tuple[str, ...]] = {
    ACCOUNT: ACCOUNT_BLOCKS,
    VEHICLE: VEHICLE_BLOCKS,
    TANKMAN: TANKMAN_BLOCKS,
}

#: ``total``: ``creationTime``, ``lastBattleTime``, ``battleLifeTime``, ``treesCut``
#: and ``mileage`` of ``TOTAL_BLOCK_LAYOUT`` with their record formats.
TOTAL_RECORDS = (
    ("creationTime", "I"),
    ("lastBattleTime", "I"),
    ("battleLifeTime", "I"),
    ("treesCut", "H"),
    ("mileage", "I"),
)

#: ``(key format, value format)`` of the dictionary blocks the server writes
#: (``DictBlockBuilder`` of the layout).
DICT_BLOCKS = {
    ACCOUNT: {BADGES_BLOCK: ("I", "I")},
    VEHICLE: {},
    TANKMAN: {},
}


class DossierError(Exception):
    """A block the layout does not have, or a payload that does not fit."""


def block_names(dossier_type: str) -> Tuple[str, ...]:
    try:
        return BLOCKS[dossier_type]
    except KeyError:
        raise DossierError("unknown dossier type: %r" % (dossier_type,))


def header_format(dossier_type: str) -> str:
    """``'<H'`` plus one size per block, the format of a header (as the client)."""
    return "<" + VERSION_FORMAT + BLOCK_SIZE_FORMAT * len(block_names(dossier_type))


def header_size(dossier_type: str) -> int:
    return struct.calcsize(header_format(dossier_type))


def empty(dossier_type: str) -> bytes:
    """The descriptor of an empty dossier: the version and zero sized blocks."""
    return struct.pack(header_format(dossier_type), VERSIONS[dossier_type],
                       *([0] * len(block_names(dossier_type))))


def build(dossier_type: str, blocks: Mapping[str, bytes]) -> bytes:
    """Pack the named block payloads into one descriptor.

    The blocks a caller does not name travel zero sized, which the client reads
    as an empty block of that layout entry.
    """
    names = block_names(dossier_type)
    unknown = set(blocks) - set(names)
    if unknown:
        raise DossierError("%s has no block %s" % (dossier_type, ", ".join(sorted(unknown))))
    sizes = []
    payloads = []
    for name in names:
        payload = bytes(blocks.get(name, b""))
        if len(payload) > 0xFFFF:
            raise DossierError("the block %s does not fit (%d bytes)" % (name, len(payload)))
        sizes.append(len(payload))
        payloads.append(payload)
    return struct.pack(header_format(dossier_type), VERSIONS[dossier_type], *sizes) + b"".join(payloads)


def parse(descr: bytes) -> Tuple[int, Dict[str, bytes]]:
    """``(version, {block name: payload})`` of a descriptor.

    Only the non-empty blocks are returned, exactly like the client's
    ``DossierDescr`` builds a block on demand.
    """
    descr = bytes(descr or b"")
    if not descr:
        return 0, {}
    # The version decides which layout the descriptor has; the local server only
    # ever writes the current one, and reading an older descriptor is the
    # client's own ``updaters`` job.
    version = struct.unpack_from("<" + VERSION_FORMAT, descr)[0]
    dossier_type = next((name for name, latest in VERSIONS.items() if latest == version), None)
    if dossier_type is None:
        raise DossierError("no layout for dossier version %d" % version)
    names = block_names(dossier_type)
    header = header_format(dossier_type)
    if len(descr) < struct.calcsize(header):
        raise DossierError("the descriptor is shorter than its header")
    sizes = struct.unpack_from(header, descr)[1:]
    blocks: Dict[str, bytes] = {}
    offset = struct.calcsize(header)
    for name, size in zip(names, sizes):
        if size:
            blocks[name] = descr[offset:offset + size]
        offset += size
    if offset != len(descr):
        raise DossierError("the descriptor has %d trailing bytes" % (len(descr) - offset))
    return version, blocks


def pack_total(creation_time: int, last_battle_time: int = 0, battle_life_time: int = 0,
               trees_cut: int = 0, mileage: int = 0) -> bytes:
    """The ``total`` block: five records in ``TOTAL_BLOCK_LAYOUT`` order."""
    values = (int(creation_time), int(last_battle_time), int(battle_life_time),
              int(trees_cut), int(mileage))
    return struct.pack("<" + "".join(fmt for _name, fmt in TOTAL_RECORDS), *values)


def read_total(descr: bytes) -> Dict[str, int]:
    """``total`` as ``{record name: value}`` (zeros when the block is empty)."""
    _version, blocks = parse(descr)
    payload = blocks.get(TOTAL_BLOCK, b"")
    if not payload:
        return {name: 0 for name, _fmt in TOTAL_RECORDS}
    values = struct.unpack("<" + "".join(fmt for _name, fmt in TOTAL_RECORDS), payload)
    return {name: int(value) for (name, _fmt), value in zip(TOTAL_RECORDS, values)}


def pack_dict(pairs: Iterable[Tuple[int, int]], key_format: str = "I",
              value_format: str = "I") -> bytes:
    """A dictionary block: the records one after another, as the client's
    ``DictDossierBlockDescr`` reads them (there is no count in front)."""
    item_format = "<" + key_format + value_format
    return b"".join(struct.pack(item_format, int(key), int(value)) for key, value in pairs)


def read_dict(payload: bytes, key_format: str = "I", value_format: str = "I") -> Dict[int, int]:
    item_format = "<" + key_format + value_format
    item_size = struct.calcsize(item_format)
    if len(payload) % item_size:
        raise DossierError("a dictionary block of %d bytes is not a multiple of %d"
                           % (len(payload), item_size))
    count = len(payload) // item_size
    if not count:
        return {}
    values = struct.unpack("<" + (key_format + value_format) * count, payload)
    return dict(zip(values[0::2], values[1::2]))


def pack_badges(badges: Mapping[int, int]) -> bytes:
    """The ``playerBadges`` block: ``{badgeID: time it was earned}``."""
    specs = DICT_BLOCKS[ACCOUNT][BADGES_BLOCK]
    return pack_dict(sorted(badges.items()), *specs)


def read_badges(descr: bytes) -> Dict[int, int]:
    """The badges of an account dossier descriptor."""
    _version, blocks = parse(descr)
    specs = DICT_BLOCKS[ACCOUNT][BADGES_BLOCK]
    return read_dict(blocks.get(BADGES_BLOCK, b""), *specs)


__all__ = [
    "ACCOUNT",
    "ACCOUNT_BLOCKS",
    "ACCOUNT_DOSSIER_VERSION",
    "BADGES_BLOCK",
    "BLOCKS",
    "DICT_BLOCKS",
    "DossierError",
    "TANKMAN",
    "TANKMAN_BLOCKS",
    "TANKMAN_DOSSIER_VERSION",
    "TOTAL_BLOCK",
    "TOTAL_RECORDS",
    "VEHICLE",
    "VEHICLE_BLOCKS",
    "VEHICLE_DOSSIER_VERSION",
    "VERSIONS",
    "block_names",
    "build",
    "empty",
    "header_format",
    "header_size",
    "pack_badges",
    "pack_dict",
    "pack_total",
    "parse",
    "read_badges",
    "read_dict",
    "read_total",
]
