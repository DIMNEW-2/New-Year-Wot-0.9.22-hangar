"""The client's dossier format (``server/game/dossier.py``).

The client parses a dossier descriptor with its own ``dossiers2`` module: the
header is the version followed by one size per block *in layout order* and every
block is read from its own byte range, so the server has to write exactly that
layout.  The tables in :mod:`server.game.dossier` were read back from the build's
own ``dossiers2/custom/layouts.py``; these tests pin them down (a block that
disappears or moves would silently shift every block after it) and exercise the
read/write pair the server uses.

The file needs no installation: it is pure format.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common  # noqa: E402
from server.game import dossier as dossier_module  # noqa: E402


def test_the_layout_is_the_one_the_client_unpacks(runner):
    """Versions, block counts and formats of ``dossiers2/custom/layouts.py``."""
    runner.equal(dossier_module.ACCOUNT_DOSSIER_VERSION, 109, "the account dossier version")
    runner.equal(dossier_module.VEHICLE_DOSSIER_VERSION, 99, "the vehicle dossier version")
    runner.equal(dossier_module.TANKMAN_DOSSIER_VERSION, 66, "the tankman dossier version")
    runner.equal(len(dossier_module.ACCOUNT_BLOCKS), 66, "the account dossier describes 66 blocks")
    runner.equal(len(dossier_module.VEHICLE_BLOCKS), 39, "the vehicle dossier describes 39 blocks")
    runner.equal(len(dossier_module.TANKMAN_BLOCKS), 2, "the tankman dossier describes 2 blocks")
    # ``DictDossierBlockDescr`` holds ``{badgeID: time}`` and the ``total`` block
    # the five records of ``TOTAL_BLOCK_LAYOUT``.
    runner.equal(dossier_module.DICT_BLOCKS[dossier_module.ACCOUNT][dossier_module.BADGES_BLOCK],
                 ("I", "I"), "the badges are an int keyed int dictionary")
    runner.equal([name for name, _format in dossier_module.TOTAL_RECORDS],
                 ["creationTime", "lastBattleTime", "battleLifeTime", "treesCut", "mileage"],
                 "the records of the total block, in order")
    runner.equal(dossier_module.VERSION_FORMAT, "H", "the version is a short")
    runner.equal(dossier_module.BLOCK_SIZE_FORMAT, "H", "a block size is a short")
    # 2 bytes of version plus two per block, as ``DossierDescr`` reads it.
    runner.equal(dossier_module.header_size(dossier_module.ACCOUNT),
                 2 * (1 + len(dossier_module.ACCOUNT_BLOCKS)),
                 "the header of an account descriptor")


def test_an_empty_dossier_is_only_a_header(runner):
    descr = dossier_module.empty(dossier_module.ACCOUNT)
    runner.equal(len(descr), dossier_module.header_size(dossier_module.ACCOUNT),
                 "an empty dossier is the header alone")
    version, blocks = dossier_module.parse(descr)
    runner.equal(version, dossier_module.ACCOUNT_DOSSIER_VERSION, "the version travels back")
    runner.equal(blocks, {}, "no block has a payload")
    runner.equal(dossier_module.read_total(descr)["creationTime"], 0,
                 "an empty account was never made")
    runner.equal(dossier_module.read_badges(descr), {}, "and earned nothing")


def test_blocks_are_written_in_layout_order(runner):
    """The sizes have no names on the wire - the order is the layout."""
    badges = {2: 1000, 7: 2000}
    descr = dossier_module.build(dossier_module.ACCOUNT, {
        dossier_module.TOTAL_BLOCK: dossier_module.pack_total(1500000000, 0, 0),
        dossier_module.BADGES_BLOCK: dossier_module.pack_badges(badges),
    })
    numbers = struct.unpack("<" + "H" * (len(dossier_module.ACCOUNT_BLOCKS) + 1),
                            descr[:dossier_module.header_size(dossier_module.ACCOUNT)])
    runner.equal(numbers[0], dossier_module.ACCOUNT_DOSSIER_VERSION, "the header starts with the version")
    sizes = dict(zip(dossier_module.ACCOUNT_BLOCKS, numbers[1:]))
    runner.equal(sizes[dossier_module.BADGES_BLOCK], 16, "two badges are two int pairs")
    runner.equal(sizes[dossier_module.TOTAL_BLOCK],
                 struct.calcsize("<IIIHI"), "the total block is the five records of the layout")
    runner.equal(sum(sizes.values()), len(descr) - dossier_module.header_size(dossier_module.ACCOUNT),
                 "every byte after the header belongs to a block")
    runner.equal(dossier_module.read_badges(descr), badges, "the badges travel back")
    total = dossier_module.read_total(descr)
    runner.equal(total["creationTime"], 1500000000, "the creation time travels back")
    runner.equal(total["mileage"], 0, "a record the caller did not name stays zero")


def test_an_unknown_block_is_refused(runner):
    try:
        dossier_module.build(dossier_module.ACCOUNT, {"noSuchBlock": b"\x00\x00"})
    except dossier_module.DossierError:
        pass
    else:
        raise AssertionError("a block the layout does not have has to be refused")

    try:
        dossier_module.build(dossier_module.ACCOUNT, {"total": b"\x00" * 100000})
    except dossier_module.DossierError:
        pass
    else:
        raise AssertionError("a payload longer than a block size can hold has to be refused")


def test_the_dictionary_helpers_match_the_client(runner):
    """``DictDossierBlockDescr`` reads pairs until the payload ends - no count."""
    payload = dossier_module.pack_dict([(1, 10), (2, 20)])
    runner.equal(payload, struct.pack("<IIII", 1, 10, 2, 20), "the pairs are packed one by one")
    runner.equal(dossier_module.read_dict(payload), {1: 10, 2: 20}, "and read back")
    try:
        dossier_module.read_dict(b"\x00\x00\x00")
    except dossier_module.DossierError:
        pass
    else:
        raise AssertionError("a payload that is not a whole number of pairs has to be refused")


def test_every_dossier_type_round_trips(runner):
    for dossier_type in (dossier_module.ACCOUNT, dossier_module.VEHICLE, dossier_module.TANKMAN):
        descr = dossier_module.empty(dossier_type)
        version, blocks = dossier_module.parse(descr)
        runner.equal(version, dossier_module.VERSIONS[dossier_type],
                     "the version of the %s dossier" % (dossier_type,))
        runner.equal(blocks, {}, "the %s dossier has no block" % (dossier_type,))
        try:
            dossier_module.build(dossier_type + "x", {})
        except dossier_module.DossierError:
            pass
        else:
            raise AssertionError("an unknown dossier type has to be refused")


if __name__ == "__main__":
    sys.exit(common.main(sys.modules[__name__]))
