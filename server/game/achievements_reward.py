"""The ``achievementsReward`` section of the shop cache.

The client renders the crystal (bond) a medal pays with it:

* ``ShopRequester.getAchievementReward(achievement, arenaType)``
  (``client/gui/shared/utils/requesters/ShopRequester.py``) reads the section,
  returns ``None`` while ``isEnabled`` is false, and otherwise looks the
  achievement up in ``medals`` - keyed by ``(recordID, 0)``, where ``recordID``
  is what the client's own ``RECORD_DB_IDS[achievement.getRecordName()]`` says -
  and walks the group ids that entry holds until it finds one whose
  ``arenaTypes`` contains the asked-for arena,
* ``AchievementCrystalRewardField`` (``client/gui/shared/tooltips/
  achievement.py``) then renders ``bonus`` (a ``Money`` dictionary, so
  ``{'crystal': N}``) multiplied per vehicle level by
  ``vehicleMultipliers[level - 1]``.

Both halves of that contract are client data: the record ids come from
``common/dossiers2/custom/records.py`` of the *installation* (read below, so the
ids are the ones this client knows), and the arena type the achievemens window
asks for is ``ARENA_GUI_TYPE.RANDOM`` = 1 (the same number the requester's own
``ARENA_BONUS_TYPE.REGULAR`` default has).

The *amounts* are the live game's: the planner decides what a medal pays.  What
this module publishes is the medal-bond schedule the 9.20.1 patch introduced and
0.9.22 still used (announced by Wargaming with the bond table:
``https://worldoftanks.eu/en/news/general-news/920-1-bonds-and-medals/``) -
one crystal times the bonds a medal earns at that tank tier.  The table is the
same one the medals themselves pay in this build, so the tooltip of the awards
window shows real numbers instead of an empty block.
"""

from __future__ import annotations

import ast
import re
from typing import Callable, Dict, Mapping, Optional

#: The record table of the client.  ``from dossiers2.custom.records import
#: RECORD_DB_IDS`` on the client side; in the installation the package lives
#: under ``scripts/common``.
RECORDS_PATH = "common/dossiers2/custom/records.py"

#: The block of the record table the medals are.
ACHIEVEMENTS_BLOCK = "achievements"

#: ``common/constants.ARENA_GUI_TYPE.RANDOM``, which is also the
#: ``ARENA_BONUS_TYPE.REGULAR`` the requester defaults to.
ARENA_TYPE_RANDOM = 1

#: What one bond of a medal is worth - the bonus the multipliers scale.
CRYSTAL_BONUS = {"crystal": 1}

#: The medal-bond schedule: ``{recordName: (tiers IV-VII, VIII-IX, X)}``, a zero
#: meaning the medal cannot earn bonds at that tier.  Per battle, so premium
#: time and the like do not multiply it.
MEDAL_BONDS = {
    "mainGun": (1, 1, 2),
    "supporter": (1, 1, 2),
    "steelwall": (1, 1, 2),
    "medalPascucci": (1, 1, 3),
    "warrior": (1, 1, 3),
    "sniper2": (1, 1, 3),
    "medalLehvaslaiho": (1, 1, 0),
    "evileye": (1, 3, 5),
    "medalOrlik": (1, 3, 0),
    "invader": (1, 3, 5),
    "defender": (1, 3, 5),
    "scout": (1, 3, 5),
    "medalDumitru": (3, 5, 7),
    "medalOskin": (3, 5, 0),
    "medalStark": (3, 5, 7),
    "huntsman": (3, 5, 7),
    "medalGore": (3, 5, 7),
    "medalTamadaYoshio": (3, 5, 0),
    "medalKolobanov": (5, 7, 10),
    "medalFadin": (5, 7, 10),
    "medalNikolas": (5, 7, 0),
    "medalBillotte": (5, 10, 10),
    "medalBurda": (5, 10, 0),
    "medalDeLanglade": (5, 10, 10),
    "medalBrunoPietro": (5, 10, 10),
    "medalTarczay": (5, 10, 10),
    "heroesOfRassenay": (5, 10, 15),
    "medalHalonen": (3, 5, 0),
    "medalRadleyWalters": (3, 5, 7),
    "medalLafayettePool": (5, 10, 10),
}

#: The lowest and the highest tank tier the schedule speaks about.
MIN_TIER = 4
MAX_TIER = 10

#: The disabled section: what a reader that indexes ``groups``/``medals`` finds
#: instead of ``None``.  Published when the installation carries no record table
#: (a packed build whose scripts cannot be read, an old catalog export).
DISABLED = {"isEnabled": False, "groups": {}, "medals": {}}

#: Python 2 long literals (``4000000001L``) are not Python 3 syntax.
_LONG_LITERAL = re.compile(r"\b(\d+)[lL]\b")


def disabled() -> dict:
    """A fresh copy of :data:`DISABLED`."""
    return {"isEnabled": False, "groups": {}, "medals": {}}


def medal_reward(name: str, tier: int) -> int:
    """The bonds ``name`` pays on a tank of ``tier`` (zero: it pays none)."""
    bonds = MEDAL_BONDS.get(name)
    if bonds is None or not MIN_TIER <= int(tier) <= MAX_TIER:
        return 0
    if name in ("medalRadleyWalters", "medalLafayettePool") and int(tier) < 5:
        return 0
    if name == "medalHalonen" and int(tier) > 8:
        return 0
    column = 0 if tier <= 7 else (1 if tier <= 9 else 2)
    return int(bonds[column])


def vehicle_multipliers(name: str) -> list:
    """``vehicleMultipliers`` of one medal: its bonds per tank tier I-X.

    ``AchievementCrystalRewardField`` indexes the list with ``level - 1`` (and
    zeroes everything below the vehicle level the achievement asks for), so it
    always carries :data:`MAX_TIER` values.
    """
    return [medal_reward(name, tier) for tier in range(1, MAX_TIER + 1)]


def build(record_db_ids: Mapping[tuple, int]) -> dict:
    """The section for one installation's record table.

    Every medal of the schedule that the installation knows becomes one group
    (the group ids are the index of the medal in the sorted schedule - the
    client only uses them as keys of ``groups``) and one entry of ``medals``
    under ``(recordID, 0)``.
    """
    groups: Dict[int, dict] = {}
    medals: Dict[tuple, list] = {}
    for group_id, name in enumerate(sorted(MEDAL_BONDS)):
        record_id = (record_db_ids or {}).get((ACHIEVEMENTS_BLOCK, name))
        if record_id is None:
            continue
        groups[group_id] = {
            "arenaTypes": [ARENA_TYPE_RANDOM],
            "bonus": dict(CRYSTAL_BONUS),
            "vehicleMultipliers": vehicle_multipliers(name),
        }
        medals[(int(record_id), 0)] = [group_id]
    if not groups:
        return disabled()
    return {"isEnabled": True, "groups": groups, "medals": medals}


# ---------------------------------------------------------------------------
# reading the client's own table
# ---------------------------------------------------------------------------
def _literal(node) -> Optional[dict]:
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def read_record_db_ids(text: Optional[str]) -> Optional[Dict[tuple, int]]:
    """``RECORD_DB_IDS`` out of the source of the client's ``records.py``.

    The file is a Python 2 module, but the table itself is one big literal
    (``RECORD_DB_IDS = {...}`` plus an ``update({...})`` for the cut records),
    so it is read with :mod:`ast` instead of being imported - the same way
    ``tools/check_client_assets.py`` reads the client's other tables.  Anything
    that is not that shape (a different module, an unreadable archive) yields
    ``None`` and the section stays disabled.
    """
    if not text:
        return None
    try:
        tree = ast.parse(_LONG_LITERAL.sub(r"\1", text))
    except SyntaxError:
        return None
    table: Optional[Dict[tuple, int]] = None
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "RECORD_DB_IDS"
                for target in statement.targets):
            table = _literal(statement.value)
            continue
        # ``RECORD_DB_IDS.update({...})`` - the cut records of the dossier.
        if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr == "update"
                and isinstance(statement.value.func.value, ast.Name)
                and statement.value.func.value.id == "RECORD_DB_IDS"
                and statement.value.args):
            extra = _literal(statement.value.args[0])
            if table is not None and extra:
                table.update(extra)
    return table


def load(read_text: Callable[[str], Optional[str]]) -> dict:
    """The section for one installation (``read_text`` reads its client files).

    ``read_text`` is a callable, not a path: the installation may keep its
    scripts unpacked or in an archive, and :func:`server.loader.client_script_reader`
    knows both.  A client whose table cannot be read simply gets the disabled
    section - the shop cache has to synchronize either way.
    """
    text = None
    try:
        text = read_text(RECORDS_PATH)
    except Exception:  # noqa: BLE001 - a broken installation is not a crash
        text = None
    record_ids = read_record_db_ids(text)
    if not record_ids:
        return disabled()
    return build(record_ids)


# ---------------------------------------------------------------------------
# the JSON catalog
# ---------------------------------------------------------------------------
def to_json(section: Mapping) -> dict:
    """The section as JSON (``medals`` keys are tuples, so they become triples)."""
    return {
        "isEnabled": bool(section.get("isEnabled")),
        "groups": {str(group_id): dict(group)
                   for group_id, group in (section.get("groups") or {}).items()},
        "medals": [[int(record_id), int(index), [int(group) for group in groups]]
                   for (record_id, index), groups
                   in sorted((section.get("medals") or {}).items())],
    }


def from_json(payload: Mapping) -> dict:
    """The reverse of :func:`to_json`; a broken payload is the disabled section."""
    if not isinstance(payload, Mapping) or not payload.get("isEnabled"):
        return disabled()
    groups = {}
    for raw_id, group in (payload.get("groups") or {}).items():
        try:
            groups[int(raw_id)] = {
                "arenaTypes": [int(value) for value in (group.get("arenaTypes") or ())],
                "bonus": dict(group.get("bonus") or {}),
                "vehicleMultipliers": [int(value) for value in
                                       (group.get("vehicleMultipliers") or ())],
            }
        except (AttributeError, TypeError, ValueError):
            return disabled()
    medals = {}
    for entry in payload.get("medals") or ():
        try:
            record_id, index, ids = entry
            medals[(int(record_id), int(index))] = [int(value) for value in ids]
        except (TypeError, ValueError):
            return disabled()
    if not groups or not medals:
        return disabled()
    return {"isEnabled": True, "groups": groups, "medals": medals}


__all__ = [
    "ACHIEVEMENTS_BLOCK",
    "ARENA_TYPE_RANDOM",
    "CRYSTAL_BONUS",
    "DISABLED",
    "MAX_TIER",
    "MEDAL_BONDS",
    "MIN_TIER",
    "RECORDS_PATH",
    "build",
    "disabled",
    "from_json",
    "load",
    "medal_reward",
    "read_record_db_ids",
    "to_json",
    "vehicle_multipliers",
]
