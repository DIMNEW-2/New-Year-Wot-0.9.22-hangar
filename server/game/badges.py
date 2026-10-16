"""The badges of the profile (``item_defs/badges.xml``).

A badge is what the client shows instead of a full medal in the player's
profile: ``gui/shared/utils/requesters/badges_requester._readBadges`` reads the
installation's own file and turns every ``<badge>`` into a dictionary, and
``CMD_SELECT_BADGES`` then only tells the server *which* of them the player
picked.  The local server needs the same table for the one thing the client
cannot check for it: whether an id in a request is a badge at all.

The file is written in the client's generic ``resource_helper`` dialect - one
``<name>`` plus a ``<value>`` of ``<item>`` entries - so the reader below walks
those items back into the mapping the client builds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .defs import DefsError, DefsSource

#: ``ITEM_DEFS_PATH + 'badges.xml'`` of the client.
BADGES_PATH = "badges.xml"


class BadgesError(DefsError):
    """The installation describes a badge the server cannot read."""


@dataclass(frozen=True)
class Badge:
    """One ``<badge>`` of the installation."""

    id: int
    name: str
    #: The client's defaults: ``weight`` -1 and ``type`` 0 when the file leaves
    #: them out (``_readBadges`` fills them in the same way).
    weight: float = -1.0
    type: int = 0
    group: int = 0
    cls: int = 0
    #: Every other item of the badge's ``<value>``, by name.
    data: Dict[str, Any] = field(default_factory=dict, compare=False)


def _read_items(section) -> Dict[str, Any]:
    """The ``<value><item>`` dictionary of one ``<badge>``."""
    values: Dict[str, Any] = {}
    value_section = section.subsection("value")
    if value_section is None:
        return values
    for item in value_section.subsections("item"):
        key = item.value("name")
        if key:
            values[key] = item.value("value", "")
    return values


def _as_int(values: Dict[str, Any], key: str, default: int = 0) -> int:
    raw = values.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default


def _as_float(values: Dict[str, Any], key: str, default: float = -1.0) -> float:
    raw = values.get(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def read_badges(source: DefsSource) -> Dict[int, Badge]:
    """Every badge of the installation, keyed by the id the client uses."""
    section = source.read_section(BADGES_PATH)
    container = section.subsection("badges") or section
    badges: Dict[int, Badge] = {}
    for entry in container.subsections("badge"):
        name = (entry.value("name") or "").strip()
        values = _read_items(entry)
        if "id" not in values:
            raise BadgesError("badge %r of %s has no id" % (name, BADGES_PATH))
        badge_id = _as_int(values, "id", -1)
        if badge_id < 0:
            raise BadgesError("badge %r of %s has a broken id %r"
                              % (name, BADGES_PATH, values.get("id")))
        badges[badge_id] = Badge(
            id=badge_id,
            name=name,
            weight=_as_float(values, "weight"),
            type=_as_int(values, "type"),
            group=_as_int(values, "group"),
            cls=_as_int(values, "class"),
            data=values,
        )
    return badges


def load_badges(source: DefsSource) -> Dict[int, Badge]:
    """Like :func:`read_badges`, but an unreadable file is simply no badges.

    A ``catalog.json`` export (``--catalog``) carries no raw XML, and a build
    without the file can still play everything else, so the badge table is the
    one catalogue section the server tolerates being empty.
    """
    try:
        return read_badges(source)
    except Exception:
        return {}


def badge_ids(badges: Optional[Dict[int, Badge]]) -> Dict[int, str]:
    """``{id: name}`` - the shape the requests layer validates against."""
    return {badge_id: badge.name for badge_id, badge in (badges or {}).items()}


__all__ = [
    "BADGES_PATH",
    "Badge",
    "BadgesError",
    "badge_ids",
    "load_badges",
    "read_badges",
]
