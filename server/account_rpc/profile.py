"""The profile of the account: dossiers, badges and the dossier fields.

The lobby's profile window (``gui/Scaleform/daapi/view/lobby/profile/*``) reads
the player's statistics from two places:

* the **account dossier** the sync carries in ``stats['dossier']`` - a compact
  descriptor the client parses with its own ``dossiers2`` module,
* the **dossier cache** of one descriptor per vehicle, which the client
  synchronizes on its own with ``CMD_SYNC_DOSSIERS``.

The account dossier of this server carries the date of the account and the
badges it earned, and every statistics block of it stays empty - an account that
never fought has nothing else to report (:mod:`server.game.dossier`).  A vehicle
dossier is empty outright, which the client reads as a fresh dossier
(``dossiers2.getVehicleDossierDescr('')`` / ``DossierBuilder.build('')``).

The rest of the profile *is* state:

* ``CMD_SELECT_BADGES`` stores the badges the player picked and publishes them
  as ``diff['badges']`` - the client's ``ClientBadges`` puts that straight into
  ``cache['badges']`` and ``BadgesRequester.selected`` reads it back.  The ids
  are validated against the installation's own ``item_defs/badges.xml`` and
  against the badges the dossier says were earned - the same two rules the
  client applies itself before it sends the request
  (``processors.plugins.BadgesValidator`` reads ``Badge.isAchieved``, which is
  the ``playerBadges`` block of the account dossier),
* ``CMD_SET_DOSSIER_FIELD`` writes one value under a dossier path.  The live
  server rewrites a block of the dossier; the local one keeps the value in the
  ``SPA`` cache instead, which is where the client looks such paths up
  (``StatsRequester.SPA``, ``isGoldFishBonusApplied``),
* ``CMD_REQ_PLAYER_INFO``, ``CMD_REQ_PLAYER_CLAN_INFO`` and the two global
  rating requests answer for the one account this server has: its dossier, no
  clan and a rating of zero.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..game import dossier as dossier_module
from . import commands as cmd

LOG = logging.getLogger("wotny.profile")

#: The client's own error keys.  A badge refusal travels to
#: ``#badges:server_error/<key>`` (``processors.common.BadgesSelector``); the
#: live server's list of keys is not part of the client, so the local one refuses
#: with the closest key it has.
ERROR_WRONG_ARGS = "wrong_args"
ERROR_SERVER = "server_error"
#: ``processors.plugins.BadgesValidator`` refuses a badge that was not earned
#: with the same name (``NOT_UNLOCKED_BADGE``).
ERROR_NOT_UNLOCKED = "not_unlocked_badge"


class ProfileError(Exception):
    """A request the profile refuses; the caller turns it into a response."""

    def __init__(self, message: str, code: int = cmd.RES_WRONG_ARGS,
                 error: str = ERROR_WRONG_ARGS):
        super().__init__(message)
        self.code = int(code)
        self.error = error


class Profile(object):
    """The dossier, badge and rating requests of the account."""

    def __init__(self, state, badges: Dict[int, object] = None):
        self.state = state
        if badges is None:
            badges = getattr(getattr(state, "catalog", None), "badges", None) or {}
        #: ``{id: Badge}`` of the installation (empty for a ``catalog.json``
        #: export, which carries no raw XML - every badge is then refused).
        self.badges: Dict[int, object] = dict(badges)

    # -- dossiers -------------------------------------------------------
    def dossier(self, vehicle_type_cd: int = None) -> bytes:
        """The compact descriptor of a dossier.

        The account dossier is the one the server keeps: it carries the date of
        the account and the badges it earned (``server.game.dossier``, published
        as ``stats['dossier']`` and here for the profile of *other* players).
        A vehicle dossier carries no statistic at all - no battle was ever
        fought in one - but it still travels as a descriptor of the *vehicle*
        version with every block zero sized, so the client's ``DossierBuilder``
        reads the version it knows instead of falling back to its own empty
        descriptor (``build('')``).
        """
        if vehicle_type_cd:
            return dossier_module.empty(dossier_module.VEHICLE)
        return self.state.account_dossier()

    def dossier_ext(self, vehicle_type_cd: int = None) -> dict:
        """``CMD_REQ_ACCOUNT_DOSSIER``/``CMD_REQ_VEHICLE_DOSSIER`` answer."""
        return {"dossier": self.dossier(vehicle_type_cd)}

    def sync_dossiers(self, version: int, max_change_time: int) -> Tuple[int, list]:
        """``CMD_SYNC_DOSSIERS`` answer: ``(version, [(ownerID, time, descr)])``.

        The client's ``DossierCache`` keeps the version it sent when the answer
        echoes it, and an empty list means "nothing changed and there is nothing
        cached": every vehicle dossier the client asks for afterwards is answered
        by :meth:`dossier` with an empty one.  There is nothing else to sync -
        the local server has no battles, so no dossier ever changes.
        """
        return int(version), []

    def player_info_ext(self, account_id: int = 0) -> dict:
        """``CMD_REQ_PLAYER_INFO`` answer (``Account.requestPlayerInfo``)."""
        database_id = int(account_id) if int(account_id or 0) > 0 else self.database_id
        return {
            "databaseID": database_id,
            "dossier": self.dossier(),
            "clanDBID": 0,
            "clanInfo": None,
            "globalRating": self.global_rating,
            # ``DossierRequester.UserDossier`` iterates the seasons and asks for
            # the *ranked* info separately; neither exists for this account.
            "eSportSeasons": {},
            "ranked": {},
        }

    def clan_info_ext(self) -> dict:
        """``CMD_REQ_PLAYER_CLAN_INFO`` answer: the account is in no clan."""
        return {"clanDBID": 0, "clanInfo": None}

    @property
    def achieved_badges(self) -> Dict[int, int]:
        """``{badgeID: time earned}`` - ``playerBadges`` of the dossier."""
        return dict(getattr(self.state, "achieved_badges", None) or {})

    @property
    def database_id(self) -> int:
        return int(getattr(self.state, "database_id", 1))

    @property
    def global_rating(self) -> int:
        """The account's global rating; every account starts at zero."""
        return int(getattr(self.state, "global_rating", 0))

    def global_rating_ext(self, account_id: int = 0) -> dict:
        """``CMD_REQ_PLAYER_GLOBAL_RATING`` answer (``ext['globalRating']``)."""
        return {"globalRating": self.global_rating}

    def global_ratings_ext(self, account_ids: Sequence[int]) -> Dict[int, int]:
        """``CMD_REQ_PLAYERS_GLOBAL_RATING`` answer.

        The client passes ``ext`` itself to its callback and iterates it as
        ``{databaseID: rating}`` (``UsersInfoController._getGlobalRatings``), so
        the answer is not wrapped in a key of its own.
        """
        return {int(account_id): self.global_rating for account_id in account_ids or ()}

    # -- badges ---------------------------------------------------------
    def is_known_badge(self, badge_id: int) -> bool:
        return int(badge_id) in self.badges

    def select_badges(self, badge_list: Iterable[int]) -> dict:
        """``CMD_SELECT_BADGES`` - store the selection and answer with its diff.

        The client only offers badges it believes are earned
        (``processors.plugins.BadgesValidator`` reads ``Badge.isAchieved``, which
        is the ``playerBadges`` block of the account dossier), and the local
        server holds the same two rules: an id the installation does not know is
        ``wrong_args``, one that was not earned is ``not_unlocked_badge``.
        """
        selected: List[int] = []
        for raw in badge_list or ():
            badge_id = int(raw)
            if badge_id in selected:
                continue
            if badge_id not in self.badges:
                raise ProfileError("badge %d is not a badge of the installation" % badge_id)
            if badge_id not in self.achieved_badges:
                raise ProfileError("badge %d was not earned" % badge_id,
                                   error=ERROR_NOT_UNLOCKED)
            selected.append(badge_id)
        self.state.badges = selected
        LOG.info("the profile now shows the badges %s", selected)
        return {"diff": self.state.incremental_diff(extra={"badges": list(selected)})}

    # -- dossier fields -------------------------------------------------
    def set_dossier_field(self, value: int, path: str) -> dict:
        """``CMD_SET_DOSSIER_FIELD`` - ``IntStr (value, path)``."""
        path = str(path or "").strip()
        if not path.startswith("/"):
            raise ProfileError("the dossier path %r is not absolute" % (path,))
        self.state.dossier_fields[path] = int(value)
        return {"diff": self.state.incremental_diff(
            extra={"cache": {"SPA": {path: int(value)}}})}


__all__ = [
    "ERROR_NOT_UNLOCKED",
    "ERROR_SERVER",
    "ERROR_WRONG_ARGS",
    "Profile",
    "ProfileError",
]
