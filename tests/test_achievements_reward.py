"""The ``achievementsReward`` section of the shop cache.

The client's awards window asks ``ShopRequester.getAchievementReward`` what a
medal pays in crystals (bonds).  The section is built from the *installation*:
the record ids come from the client's own ``common/dossiers2/custom/records.py``
and the amounts follow the medal-bond schedule of the build (see
:mod:`server.game.achievements_reward`).  The tests below keep the shape the
client reads, the schedule itself and the two ways the table reaches the shop
cache (a client tree and a catalog JSON export) honest.
"""

from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server import loader  # noqa: E402
from server.game import achievements_reward as ar  # noqa: E402
from server.game import shop as shop_module  # noqa: E402

#: A source shaped like the client's ``records.py``: the literal table, the
#: ``update`` of the cut records and a Python 2 long literal in both.
RECORDS_SOURCE = """\
RECORDS = (('achievements', 'mainGun', 'p', 'I', 4000000001L),
 ('achievements', 'defender', 'p', 'I', 4000000001L))
RECORD_DB_IDS = {('achievements', 'mainGun'): 228,
 ('achievements', 'defender'): 35,
 ('total', 'mileage'): 4000000001L}
DB_ID_TO_RECORD = dict([ (value, key) for key, value in RECORD_DB_IDS.iteritems() ])
RECORD_DB_IDS.update({('vehTypeFrags', ''): 863, ('playerBadges', ''): 1145})
"""

#: The client of the installation, or ``None`` when its scripts are packed.
CATALOG = None


def _catalog(runner: common.Runner):
    global CATALOG
    if CATALOG is None:
        CATALOG = loader.load_catalog(runner.client)
    return CATALOG


# ---------------------------------------------------------------------------
# the schedule
# ---------------------------------------------------------------------------
def test_the_schedule_is_the_published_one(runner: common.Runner) -> None:
    """The medal-bond table of 9.20.1, tier by tier."""
    runner.equal([ar.medal_reward("mainGun", tier) for tier in (3, 4, 7, 8, 9, 10, 11)],
                 [0, 1, 1, 1, 1, 2, 0],
                 "``mainGun`` pays one bond up to tier IX and two on tier X")
    runner.equal([ar.medal_reward("heroesOfRassenay", tier) for tier in (4, 7, 8, 9, 10)],
                 [5, 5, 10, 10, 15], "and the hardest medal pays the most")
    runner.equal(ar.medal_reward("medalHalonen", 10), 0,
                 "``medalHalonen`` stops paying above tier VIII")
    runner.equal(ar.medal_reward("medalRadleyWalters", 4), 0,
                 "``medalRadleyWalters`` starts at tier V")
    runner.equal(ar.medal_reward("medalRadleyWalters", 5), 3, "and pays there")
    runner.equal(ar.medal_reward("noSuchMedal", 6), 0, "an unknown medal pays nothing")

    multipliers = ar.vehicle_multipliers("mainGun")
    runner.equal(len(multipliers), ar.MAX_TIER,
                 "the client indexes this list with the tank tier")
    runner.equal(multipliers, [0, 0, 0, 1, 1, 1, 1, 1, 1, 2],
                 "below tier IV the reward is zeroed by the schedule itself")
    runner.check(all(len(ar.vehicle_multipliers(name)) == ar.MAX_TIER
                     for name in ar.MEDAL_BONDS),
                 "every medal of the table carries one value per tier")


# ---------------------------------------------------------------------------
# reading the client's table
# ---------------------------------------------------------------------------
def test_the_reader_takes_the_clients_own_table(runner: common.Runner) -> None:
    """``RECORD_DB_IDS`` is read out of the client's source, not imported."""
    table = ar.read_record_db_ids(RECORDS_SOURCE)
    runner.check(table is not None, "the literal table is read")
    runner.equal(table[("achievements", "mainGun")], 228, "with its ids")
    runner.equal(table[("total", "mileage")], 4000000001,
                 "Python 2 longs are ints again")
    runner.equal(table[("playerBadges", "")], 1145,
                 "and the ``update`` of the cut records is merged")

    runner.equal(ar.read_record_db_ids(""), None, "an empty file is no table")
    runner.equal(ar.read_record_db_ids("RECORD_DB_IDS = build()"), None,
                 "a computed table is not guessed")
    runner.equal(ar.read_record_db_ids("this is not python ("), None,
                 "a file that is not Python is not a failure either")


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def test_the_section_is_the_shape_the_client_reads(runner: common.Runner) -> None:
    """``getAchievementReward``/``AchievementCrystalRewardField`` and its keys."""
    section = ar.build({("achievements", "mainGun"): 228,
                        ("achievements", "defender"): 35,
                        ("total", "mileage"): 1})
    runner.equal(section["isEnabled"], True, "the section is enabled")
    runner.equal(sorted(section["groups"]), sorted(section["medals"][(228, 0)] + section["medals"][(35, 0)]),
                 "every group is reachable through ``medals``")
    runner.equal(sorted(section["medals"]), [(35, 0), (228, 0)],
                 "``medals`` is keyed by the client's record id")
    for group_id, group in sorted(section["groups"].items()):
        runner.equal(sorted(group), ["arenaTypes", "bonus", "vehicleMultipliers"],
                     "a group of %s carries the three keys the tooltip reads" % group_id)
        runner.check(ar.ARENA_TYPE_RANDOM in group["arenaTypes"],
                     "and is offered for the arena type the window asks for")
        runner.equal(group["bonus"], ar.CRYSTAL_BONUS, "the bonus is a Money dictionary")
        runner.check("crystal" in group["bonus"], "in crystals (bonds)")
        runner.equal(len(group["vehicleMultipliers"]), ar.MAX_TIER,
                     "with one multiplier per tank tier")
        runner.check(section["medals"][(35, 0)] == [group_id] or section["medals"][(228, 0)] == [group_id],
                     "and the medal of %s points at it" % group_id)

    runner.equal(ar.build({}), ar.disabled(),
                 "an installation without the medals gets the disabled section")
    runner.equal(sorted(ar.disabled()), ["groups", "isEnabled", "medals"],
                 "which still carries the dictionaries a reader indexes")


def test_the_section_survives_a_catalog_export(runner: common.Runner) -> None:
    """The table rides in ``catalog.json`` and comes back unchanged."""
    catalog = _catalog(runner)
    payload = catalog.to_json()
    runner.check("achievementsReward" in payload, "the export carries the section")
    exported = payload["achievementsReward"]
    runner.equal(exported["isEnabled"], catalog.achievements_reward["isEnabled"],
                 "with the very state of the installation")
    runner.equal(ar.from_json(exported), catalog.achievements_reward,
                 "and reads back into the same section")
    runner.check(all(len(entry) == 3 for entry in exported["medals"]),
                 "``medals`` travels as triples: JSON has no tuple keys")
    # A catalogue from an older export (or one whose scripts were unreadable)
    # publishes the disabled section instead of a missing one.
    reloaded = type(catalog).from_json(dict(payload, achievementsReward=None))
    runner.equal(reloaded.achievements_reward, ar.disabled(),
                 "a payload without the section is not a failure")


# ---------------------------------------------------------------------------
# the installation and the shop cache
# ---------------------------------------------------------------------------
def test_the_section_comes_from_the_installation(runner: common.Runner) -> None:
    """The ids are the client's own, whatever the installation carries."""
    catalog = _catalog(runner)
    read_text = loader.client_script_reader(runner.client)
    text = read_text(ar.RECORDS_PATH)
    if not text:
        raise common.Skip("the installation keeps its scripts packed")
    record_ids = ar.read_record_db_ids(text)
    runner.check(record_ids, "the client's record table is readable")
    runner.equal(catalog.achievements_reward, ar.build(record_ids),
                 "the catalogue publishes exactly that table")
    section = catalog.achievements_reward
    runner.equal(section["isEnabled"], True, "the medals of this build are found")
    known = {record_ids.get((ar.ACHIEVEMENTS_BLOCK, name)) for name in ar.MEDAL_BONDS}
    for record_id, index in sorted(section["medals"]):
        runner.check(record_id in known,
                     "the record id %d is a medal of the client's own table" % record_id)
        runner.equal(index, 0, "the client asks for ``(recordID, 0)``")
    runner.equal(len(section["groups"]), len(section["medals"]),
                 "one group per medal the installation knows")


def test_every_medal_of_the_installation_is_offered(runner: common.Runner) -> None:
    """The medals the build knows are exactly the ones the section offers."""
    catalog = _catalog(runner)
    read_text = loader.client_script_reader(runner.client)
    text = read_text(ar.RECORDS_PATH)
    if not text:
        raise common.Skip("the installation keeps its scripts packed")
    record_ids = ar.read_record_db_ids(text)
    known = {name for (block, name) in record_ids if block == ar.ACHIEVEMENTS_BLOCK}
    missing = sorted(set(ar.MEDAL_BONDS) - known)
    runner.equal(missing, [], "every medal of the schedule is a record of the client")
    runner.equal(len(catalog.achievements_reward["medals"]), len(ar.MEDAL_BONDS),
                 "and every one of them is offered by the section")


def test_the_shop_publishes_the_section(runner: common.Runner) -> None:
    """Both copies of the shop cache carry the section the catalogue holds."""
    catalog = _catalog(runner)
    payload = shop_module.build_shop(catalog)
    runner.equal(payload["achievementsReward"], catalog.achievements_reward,
                 "the shop publishes the table of the installation")
    runner.equal(payload["defaults"]["achievementsReward"], catalog.achievements_reward,
                 "and so does the fallback table ``DefaultShopRequester`` reads")
    runner.check(payload["achievementsReward"] is not None,
                 "the section is never ``None``")
    # A catalogue that carries no table at all (an export without the section)
    # still publishes the shape a requester indexes.
    plain = copy.copy(catalog)
    del plain.achievements_reward
    fallback = shop_module.build_shop(plain)["achievementsReward"]
    runner.equal(fallback, ar.disabled(), "with the disabled section as the fallback")
    runner.equal(sorted(fallback), ["groups", "isEnabled", "medals"],
                 "whose dictionaries are there to be indexed")


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
