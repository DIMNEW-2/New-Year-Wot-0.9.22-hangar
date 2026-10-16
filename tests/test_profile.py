"""The profile: the dossiers the client parses, the badges and the ratings.

Every request the lobby's profile window makes is driven through the real
dispatcher (the same one the socket server calls) and the answers are checked
for what the *client* would read:

* ``stats['dossier']`` and the ``dossier`` of a 504/505 answer are compact
  descriptors the client's ``dossiers2`` builds a dossier out of; the local
  server writes the version and the two blocks it really knows (the date of the
  account and the badges it earned) and leaves every statistic block zero sized
  (see :mod:`server.game.dossier`),
* ``CMD_SYNC_DOSSIERS`` is answered with the streamed ``(version, [...])``
  ``DossierCache`` unpacks (``SyncController`` -> ``zlib`` + ``cPickle``),
* ``CMD_SELECT_BADGES`` publishes the selection the way ``ClientBadges`` reads
  it (``diff['badges']``) and refuses an id the installation's own
  ``item_defs/badges.xml`` does not have,
* ``CMD_SET_DOSSIER_FIELD`` lands in the ``SPA`` cache, which is where the client
  looks up dossier paths (``StatsRequester.SPA``).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server import loader  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc import profile as profile_module  # noqa: E402
from server.account_rpc.requests import RequestDispatcher  # noqa: E402
from server.account_rpc.state import AccountState  # noqa: E402
from server.game import badges as badges_module  # noqa: E402
from server.game import defs as defs_module  # noqa: E402
from server.game import dossier as dossier_module  # noqa: E402
from server.proto import unpack_stream  # noqa: E402

SPA_PATH = "/common/goldfish_bonus_applied/"


def _dispatcher(runner, **kwargs):
    state = common.new_account(runner.client, **kwargs)
    return RequestDispatcher(state), state


def _badge_id(state):
    """One badge id of the installation under test."""
    badges = state.catalog.badges
    if not badges:
        raise common.Skip("the installation describes no badges")
    return sorted(badges)[0]


def test_the_sync_carries_the_dossier_and_the_badges(runner):
    dispatcher, state = _dispatcher(runner)
    diff = state.full_diff()
    runner.check("stats" in diff and "dossier" in diff["stats"],
                 "the stats section carries the account dossier")
    # The client's ``DossierBuilder.build`` reads the version out of the header
    # and then one size per block, so the descriptor has to be the client's own
    # format - and its statistics have to be zero: this account never fought.
    descr = diff["stats"]["dossier"]
    version, blocks = dossier_module.parse(descr)
    runner.equal(version, dossier_module.ACCOUNT_DOSSIER_VERSION,
                 "the dossier carries the version of this build")
    runner.equal(sorted(blocks), sorted([dossier_module.TOTAL_BLOCK,
                                         dossier_module.BADGES_BLOCK]),
                 "a fresh account has a date and its badges, nothing else")
    total = dossier_module.read_total(descr)
    runner.equal(total["creationTime"], int(state.created_at),
                 "the dossier remembers when the account was made")
    runner.equal(total["lastBattleTime"], 0,
                 "an account without battles has no last battle")
    runner.equal(total["battleLifeTime"], 0, "and no time in battle")
    runner.check(state.achieved_badges, "the account earned the installation's badges")
    runner.equal(dossier_module.read_badges(descr), dict(state.achieved_badges),
                 "every earned badge is in the dossier")
    runner.equal(diff["badges"], [], "no badge is selected yet")
    runner.equal(diff["cache"]["SPA"], {}, "no dossier field was written yet")


def test_the_cache_carries_the_wallet_flag_and_the_vehicle_locks(runner):
    """``Stats.synchronize``/``Inventory.synchronize`` read these out of ``cache``."""
    dispatcher, state = _dispatcher(runner)
    cache = state.full_diff()["cache"]
    runner.equal(cache["mayConsumeWalletResources"], True,
                 "the wallet may consume resources (``StatsRequester``)")
    runner.equal(cache["vehsLock"], {},
                 "no vehicle is locked: the local server has no battles")
    runner.check("SPA" in cache, "the dossier paths travel in the cache section")


def test_the_client_can_ask_for_both_dossiers(runner):
    dispatcher, state = _dispatcher(runner)
    response = dispatcher.handle(1, cmd.CMD_REQ_ACCOUNT_DOSSIER, (1, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the account dossier is answered")
    version, blocks = dossier_module.parse(response.ext["dossier"])
    runner.equal(version, dossier_module.ACCOUNT_DOSSIER_VERSION,
                 "the account dossier is the one of this build")
    runner.check(blocks.get(dossier_module.TOTAL_BLOCK),
                 "the account dossier carries its date")

    response = dispatcher.handle(2, cmd.CMD_REQ_VEHICLE_DOSSIER, (1, 12345, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the vehicle dossier is answered")
    version = dossier_module.parse(response.ext["dossier"])[0]
    runner.equal(version, dossier_module.VEHICLE_DOSSIER_VERSION,
                 "the vehicle dossier is the one of this build")


def test_the_player_info_describes_this_account(runner):
    dispatcher, state = _dispatcher(runner)
    response = dispatcher.handle(1, cmd.CMD_REQ_PLAYER_INFO, (state.database_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the player info is answered")
    # ``Account.requestPlayerInfo`` reads exactly these keys.
    for key in ("databaseID", "dossier", "clanDBID", "clanInfo", "globalRating",
                "eSportSeasons", "ranked"):
        runner.check(key in response.ext, "the player info carries %r" % (key,))
    runner.equal(response.ext["databaseID"], state.database_id, "the account id travels back")
    runner.equal(dossier_module.parse(response.ext["dossier"])[0],
                 dossier_module.ACCOUNT_DOSSIER_VERSION,
                 "the dossier of the account is the fresh one of this build")
    runner.equal(response.ext["clanDBID"], 0, "the account is in no clan")

    response = dispatcher.handle(2, cmd.CMD_REQ_PLAYER_CLAN_INFO, (state.database_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the clan info is answered")
    runner.equal(response.ext["clanInfo"], None, "there is no clan to describe")


def test_the_global_rating_is_zero(runner):
    dispatcher, state = _dispatcher(runner)
    response = dispatcher.handle(1, cmd.CMD_REQ_PLAYER_GLOBAL_RATING, (state.database_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the rating is answered")
    runner.equal(response.ext["globalRating"], 0, "the rating of the account")

    # ``UsersInfoController`` iterates ``ext`` itself as ``{databaseID: rating}``.
    response = dispatcher.handle(2, cmd.CMD_REQ_PLAYERS_GLOBAL_RATING, ([1, 7], []))
    runner.equal(response.code, cmd.RES_SUCCESS, "the ratings are answered")
    runner.equal(sorted(response.ext), [1, 7], "every requested id is answered")
    runner.equal(set(response.ext.values()), {0}, "every rating is zero")


def test_the_dossier_sync_is_a_stream(runner):
    dispatcher, state = _dispatcher(runner)
    response = dispatcher.handle(1, cmd.CMD_SYNC_DOSSIERS, (5, 1000, 0))
    runner.equal(response.code, cmd.RES_STREAM, "the dossier sync is streamed")
    blob = response.stream_blob()
    runner.check(blob is not None, "a streamed answer carries a payload")
    # ``SyncController.__onSyncStreamComplete``: zlib.decompress + cPickle.loads.
    version, dossiers = unpack_stream(blob[1])
    runner.equal(version, 5, "the version the client sent travels back")
    runner.equal(list(dossiers), [], "this account has no vehicle dossiers")


def test_a_badge_of_the_installation_can_be_selected(runner):
    dispatcher, state = _dispatcher(runner)
    badge_id = _badge_id(state)
    response = dispatcher.handle(1, cmd.CMD_SELECT_BADGES, ([badge_id],))
    runner.equal(response.code, cmd.RES_SUCCESS, "the selection is accepted")
    runner.equal(state.badges, [badge_id], "the account keeps the selection")
    runner.equal(response.diffs[-1]["badges"], [badge_id],
                 "the diff publishes the selection the way ClientBadges reads it")

    response = dispatcher.handle(2, cmd.CMD_SELECT_BADGES, ())
    runner.equal(response.code, cmd.RES_SUCCESS, "an empty selection clears the badges")
    runner.equal(response.diffs[-1]["badges"], [], "nothing is selected any more")


def test_an_unknown_badge_is_refused(runner):
    dispatcher, state = _dispatcher(runner)
    _badge_id(state)
    response = dispatcher.handle(1, cmd.CMD_SELECT_BADGES, ([9999],))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "a badge the client does not know is refused")
    runner.equal(response.error, profile_module.ERROR_WRONG_ARGS, "the error key of a bad badge")
    runner.equal(state.badges, [], "a refused selection changes nothing")


def test_a_dossier_field_travels_in_the_spa_cache(runner):
    dispatcher, state = _dispatcher(runner)
    response = dispatcher.handle(1, cmd.CMD_SET_DOSSIER_FIELD, (1, SPA_PATH))
    runner.equal(response.code, cmd.RES_SUCCESS, "the field is accepted")
    runner.equal(response.diffs[-1]["cache"]["SPA"][SPA_PATH], 1,
                 "the field lands in the cache the client reads it from")
    runner.equal(state.dossier_fields[SPA_PATH], 1, "the account keeps the field")
    runner.equal(state.full_diff()["cache"]["SPA"][SPA_PATH], 1,
                 "a full sync publishes the fields again")

    response = dispatcher.handle(2, cmd.CMD_SET_DOSSIER_FIELD, (1, "common/relative"))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "a relative dossier path is refused")


def test_the_badge_and_the_fields_survive_a_restart(runner):
    dispatcher, state = _dispatcher(runner)
    badge_id = _badge_id(state)
    dispatcher.handle(1, cmd.CMD_SELECT_BADGES, ([badge_id],))
    dispatcher.handle(2, cmd.CMD_SET_DOSSIER_FIELD, (1, SPA_PATH))

    restored = AccountState.from_json(state.to_json(), state.catalog)
    runner.equal(restored.badges, [badge_id], "the badges come back")
    runner.equal(restored.dossier_fields, {SPA_PATH: 1}, "the dossier fields come back")


def test_the_badge_table_is_the_installations(runner):
    root = common.require_client(runner.client)
    source = loader.open_client_defs(root)
    try:
        badges = badges_module.read_badges(source)
    finally:
        source.close()
    if not badges:
        raise common.Skip("the installation describes no badges")

    catalog, _tankmen = common.load_game(runner.client)
    runner.equal(sorted(catalog.badges), sorted(badges),
                 "the catalogue carries the badges of the installation")
    for badge in badges.values():
        runner.check(bool(badge.name), "badge %d has a name" % (badge.id,))
        runner.check(isinstance(badge.weight, float), "badge %d has a weight" % (badge.id,))
    # ``BadgesRequester`` defaults the two optional items of a badge.
    runner.check(all(badge.type == 0 for badge in badges.values())
                 or any(badge.type for badge in badges.values()),
                 "the badge types are read")
    runner.equal(badges_module.badge_ids(badges).get(1, ""),
                 badges[1].name if 1 in badges else "",
                 "the validation table maps ids to names")


def test_an_installation_without_badges_still_plays(runner):
    """A ``catalog.json`` export carries no raw XML: no badges, no refusals."""
    source = loader.open_client_defs(common.require_client(runner.client))
    try:
        empty = badges_module.load_badges(defs_module.CatalogJsonSource({}))
    finally:
        source.close()
    runner.equal(empty, {}, "an empty source is simply no badges")

    catalog, _tankmen = common.load_game(runner.client)
    catalog.badges = {}
    state = AccountState.new(catalog, tankmen_configs=None)
    dispatcher = RequestDispatcher(state)
    response = dispatcher.handle(1, cmd.CMD_SELECT_BADGES, ([1],))
    runner.equal(response.code, cmd.RES_WRONG_ARGS,
                 "without the file every badge is refused (and the client checks too)")


if __name__ == "__main__":
    sys.exit(common.main(sys.modules[__name__]))
