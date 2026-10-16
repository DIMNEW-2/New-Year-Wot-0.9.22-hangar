"""The profile of the account: dossiers, badges and the dossier fields.

The lobby's profile window (``gui/Scaleform/daapi/view/lobby/profile/*``) reads
the player's statistics from two places:

* the **account dossier** the sync carries in ``stats['dossier']`` - a compact
  descriptor the client parses with its own ``dossiers2`` module,
* the **dossier cache** of one descriptor per vehicle, which the client
  synchronizes on its own with ``CMD_SYNC_DOSSIERS``.

Both are empty here, and the client is written for exactly that: an empty
descriptor is a *new* dossier (``DossierBuilder.build('')`` packs the current
version with every block zero sized and ``_initializeNewDossier`` stamps the
creation time), so the local server never has to mirror the block layout of the
client's ``dossiers2/custom/records.py``.  An account that never fought has
nothing to report, and reporting zeros is what the live server does for one, too
- it just has a real descriptor to say it with.

The rest of the profile *is* state:

* ``CMD_SELECT_BADGES`` stores the badges the player picked and publishes them
  as ``diff['badges']`` - the client's ``ClientBadges`` puts that straight into
  ``cache['badges']`` and ``BadgesRequester.selected`` reads it back.  The badge
  ids are validated against the installation's own ``item_defs/badges.xml``;
  whether a badge was *earned* is a question for the dossier's achievements, and
  the client checks that itself before it sends the request
  (``processors.plugins.BadgesValidator``),
* ``CMD_SET_DOSSIER_FIELD`` writes one value under a dossier path.  The live
  server rewrites the dossier's ``common`` block; a local server that does not
  know the layout keeps the value in the ``SPA`` cache instead, which is where
  the client looks such paths up (``StatsRequester.SPA``,
  ``isGoldFishBonusApplied``),
* ``CMD_REQ_PLAYER_INFO``, ``CMD_REQ_PLAYER_CLAN_INFO`` and the two global
  rating requests answer for the one account this server has: its (empty)
  dossier, no clan and a rating of zero.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import commands as cmd

LOG = logging.getLogger("wotny.profile")

#: The client's own error keys.  A badge refusal travels to
#: ``#badges:server_error/<key>`` (``processors.common.BadgesSelector``); the
#: live server's list of keys is not part of the client, so the local one refuses
#: with the closest key it has.
ERROR_WRONG_ARGS = "wrong_args"
ERROR_SERVER = "server_error"


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

        Always empty: the client turns that into a fresh dossier of the current
        version, both for the account (``ItemsRequester.__getAccountDossierDescr``)
        and for a vehicle (``dossiers2.getVehicleDossierDescr``).
        """
        return b""

    def dossier_ext(self, vehicle_type_cd: int = None) -> dict:
        """``CMD_REQ_ACCOUNT_DOSSIER``/``CMD_REQ_VEHICLE_DOSSIER`` answer."""
        return {"dossier": self.dossier(vehicle_type_cd)}

    def sync_dossiers(self, version: int, max_change_time: int) -> Tuple[int, list]:
        """``CMD_SYNC_DOSSIERS`` answer: ``(version, [(ownerID, time, descr)])``.

        The client's ``DossierCache`` keeps the version it sent when the answer
        echoes it, and an empty list means "nothing changed and there is nothing
        cached", so every vehicle dossier the client asks for afterwards is built
        from an empty descriptor.
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
        """``CMD_SELECT_BADGES`` - store the selection and answer with its diff."""
        selected: List[int] = []
        for raw in badge_list or ():
            badge_id = int(raw)
            if badge_id in selected:
                continue
            if badge_id not in self.badges:
                raise ProfileError("badge %d is not a badge of the installation" % badge_id)
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
    "ERROR_SERVER",
    "ERROR_WRONG_ARGS",
    "Profile",
    "ProfileError",
]
