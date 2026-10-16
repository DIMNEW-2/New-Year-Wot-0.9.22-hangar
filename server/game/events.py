"""The ``eventsData`` blobs the client needs for the New Year event.

The 0.9.22 client keeps the quests, actions and event notifications inside
``BigWorld.player().eventsData`` - a dictionary of ``int -> zlib(pickle(payload))``
blobs (see ``client/account_helpers/AccountSyncData`` and
``client/gui/server_events/EventsCache``).  Everything the New Year interface
reads comes from there:

* ``EVENT_CLIENT_DATA.QUEST`` - ``{questID: questData}``; the loot of a chest is
  the ``bonus`` section of the hidden quest with the chest's id, which the client
  reads in ``_NewYearChestStorage._handleRequestResult``,
* ``EVENT_CLIENT_DATA.ACTION`` - a *list* of action dictionaries, each with an
  ``id``; the New Year action opens the event in the lobby,
* ``EVENT_CLIENT_DATA.NOTIFICATIONS`` - a list of server notifications; the
  ``cmd_change_hangar`` one switches the hangar space to the New Year scene and
  an unprefixed one (``ACTION_NOTIFY_TYPE``) becomes a message in the client's
  service channel (see :meth:`EventDataBuilder.action_notification`).

This module builds those payloads from the catalogue and the event state; the
transport (account diff key ``eventsData`` or ``('eventsData', '_r')`` for a full
sync) is handled by :mod:`server.account_rpc.data`.
"""

from __future__ import annotations

import json
import time
import zlib
from typing import Dict, List, Optional

from ..proto import py2_dumps
from . import customization as c11n
from . import ny_constants as nyc
from .catalog import Catalog

#: ``common/constants.py`` - ``EVENT_CLIENT_DATA``.
EVENT_CLIENT_DATA = {
    "ACTION": 1,
    "ACTION_REV": 2,
    "QUEST": 3,
    "QUEST_REV": 4,
    "INGAME_EVENTS": 7,
    "INGAME_EVENTS_REV": 8,
    "NOTIFICATIONS": 9,
    "NOTIFICATIONS_REV": 10,
    "PERSONAL_QUEST": 11,
    "PERSONAL_QUEST_REV": 12,
    "FALLOUT": 15,
    "FALLOUT_REV": 16,
    "SQUAD_BONUSES": 17,
    "SQUAD_BONUSES_REV": 18,
    "FEATURES_SWITCH": 19,
    "FEATURES_SWITCH_REV": 20,
    "ACTION_ENTITIES": 21,
    "ACTION_ENTITIES_REV": 22,
    "ANNOUNCED_ACTION_DATA": 23,
    "ANNOUNCED_ACTION_DATA_REV": 24,
}

#: ``common/constants.py`` - ``EVENT_TYPE`` values used below.
EVENT_TYPE_ACTION = 1
EVENT_TYPE_BATTLE_QUEST = 2
EVENT_TYPE_TOKEN_QUEST = 3

#: ``client/gui/ClientHangarSpace`` - the event notification that swaps the
#: hangar scene.  The name is *not* fixed: ``NEW_YEAR_HANGAR_PATH`` of the
#: decompiled controller is a leftover in some builds (the 0.9.22 CH one names
#: its sounds ``hangar_h07_newyear_2018_*``), so the space the client actually
#: ships is detected in its packages
#: (``server.loader.guess_new_year_space``) and used instead of this default.
NEW_YEAR_HANGAR_PATH = "h07_newyear_2015"
CMD_CHANGE_HANGAR = "cmd_change_hangar"
CMD_CHANGE_HANGAR_PREMIUM = "cmd_change_hangar_prem"
#: ``client/MusicControllerWWISE`` - optional hangar sound overrides.
CMD_CHANGE_HANGAR_AMBIENT = "cmd_change_hangar_ambient"
CMD_CHANGE_HANGAR_MUSIC = "cmd_change_hangar_music"

#: ``client/gui/scaleform/SystemMessagesInterface.__processNotifications``
#: turns every notification whose type starts with neither ``cmd_`` nor
#: ``promo_`` into a message of the player's service channel
#: (``SCH_CLIENT_MSG_TYPE.ACTION_NOTIFY_TYPE``), which
#: ``ActionNotificationFormatter`` renders into the ``actionBegin``/``actionEnd``
#: template of the lobby.  A ``promo_`` notification instead opens a browser
#: window (``PromoController._processPromo`` parses its payload as a URL), so the
#: local server never sends one.
ACTION_NOTIFY_TYPE = "action"
PROMO_NOTIFICATION_PREFIX = "promo_"
CMD_NOTIFICATION_PREFIX = "cmd_"

#: The New Year action of the event; the client's NY views are opened for it.
NEW_YEAR_ACTION_ID = "ny18:action"
NEW_YEAR_EVENT_TAG = "ny18"

#: ``common/items/new_year_types.NY_STATE``.
NY_STATE_NOT_STARTED = 0
NY_STATE_IN_PROGRESS = 1
NY_STATE_SUSPENDED = 2
NY_STATE_FINISHED = 3

#: Seconds a New Year campaign lasts (the event ran for a bit over a month).
EVENT_DURATION = 30 * 24 * 3600


#: The order of the languages a text is tried in.  ``helpers.getLocalizedData``
#: (``client/helpers/__init__.py``) looks up ``getClientLanguage()`` first, then
#: ``constants.DEFAULT_LANGUAGE`` - which is ``cn`` for the Chinese build this
#: server runs against - and finally takes whatever the dictionary holds, so the
#: Chinese text is also what a client of another language falls back to.
LANGUAGES = ("cn", "ru", "en")


def localized(cn: str = "", ru: str = "", en: str = "") -> Dict[str, str]:
    """The text of a server message, by client language.

    A message of the *server* travels as a dictionary of languages instead of
    one string (``helpers.getLocalizedData`` of the client picks the entry), and
    an empty text is left out: the client skips a notification with no text.
    """
    result = {}
    for language, text in zip(LANGUAGES, (cn, ru, en)):
        if text:
            result[language] = text
    return result


def _blob(payload) -> bytes:
    """``zlib(pickle(payload))`` - the wire form of one ``eventsData`` entry."""
    return zlib.compress(py2_dumps(payload))


class EventDataBuilder:
    """Builds every ``eventsData`` payload of the New Year event."""

    def __init__(self, catalog: Catalog, start_time: Optional[float] = None,
                 hangar_path: str = None):
        self.catalog = catalog
        self.start_time = float(start_time if start_time is not None else time.time())
        #: The hangar space the notification switches the client to.  A build
        #: that ships another space gets that one (see
        #: :data:`NEW_YEAR_HANGAR_PATH`); ``python3 -m server --hangar``
        #: overrides it.
        self.hangar_path = str(hangar_path or NEW_YEAR_HANGAR_PATH)
        #: ``{chestID: bonus}`` of the chests that were opened (see
        #: :meth:`set_chest_loot`); the baseline of a closed chest comes from
        #: :data:`CHEST_QUESTS`.
        self._chest_loot: Dict[str, dict] = {}
        #: ``NY_STATE`` of the event; the client reads it from the
        #: ``INGAME_EVENTS`` blob of ``eventsData``
        #: (``ClientNewYear.__onEventsDataChanged``).
        self.new_year_state = NY_STATE_IN_PROGRESS
        #: Optional sound overrides for the New Year hangar; the client's sound
        #: banks decide the event names, so they are off unless configured.
        self.hangar_ambient = ""
        self.hangar_music = ""
        #: The service-channel messages the full sync announces (see
        #: :meth:`action_notification`) and whether they are sent at all
        #: (``python3 -m server --no-announce`` keeps them out of the client).
        self.announcements: List[dict] = []
        self.service_messages = True
        self._announcement_ids: set = set()

    def set_new_year_state(self, state: int) -> None:
        """Switch the state of the event; the client picks it up on the next sync."""
        self.new_year_state = int(state)

    def set_hangar_sounds(self, ambient: str = "", music: str = "") -> None:
        """Override the hangar ambient/music (``cmd_change_hangar_*``).

        ``music`` may be ``"name"`` or ``"name|premiumName"`` - exactly what
        ``client/MusicControllerWWISE`` splits on.
        """
        self.hangar_ambient = ambient or ""
        self.hangar_music = music or ""

    # -- quests ---------------------------------------------------------
    def _conditions(self) -> dict:
        """The condition skeleton every ``Quest`` requires.

        ``Quest.__init__`` reads ``conditions['preBattle']['account'|'vehicle'|
        'battle']``, ``conditions['common']`` and ``conditions['postBattle']``
        unconditionally, so all five sections have to exist even for a hidden
        reward quest that is never shown.
        """
        return {
            "preBattle": {"account": {}, "vehicle": {}, "battle": {}},
            "common": {},
            "postBattle": {},
        }

    def set_chest_loot(self, chest_loot: Dict[str, dict]) -> None:
        """Remember what each opened chest contained.

        A chest's reward is read from the ``bonus`` of its hidden quest
        (``_NewYearChestStorage._handleRequestResult``), so the server stores the
        loot of the last opening per chest id and publishes the refreshed quest
        with the next ``eventsData`` update (see
        :meth:`server.account_rpc.state.AccountState.apply_new_year_result`).
        """
        for chest_id, bonus in (chest_loot or {}).items():
            self._chest_loot[chest_id] = dict(bonus or {})

    def chest_loot(self, chest_id: str) -> dict:
        return dict(self._chest_loot.get(chest_id, {}))

    def chest_quests(self) -> Dict[str, dict]:
        """``{chestID: quest data}`` - the loot table of every chest level."""
        quests = {}
        for chest in sorted(self.catalog.new_year.chests.values(), key=lambda chest: chest.level):
            quests[chest.id] = self.chest_quest(chest)
        return quests

    def box_quest(self, box) -> dict:
        """The hidden quest the client demands before it opens a box.

        ``NYRequestController.__validateBoxesData`` looks the box id up with
        ``eventsCache.getHiddenQuests`` and refuses the request when there is no
        such quest, so every box needs one.  The payload of the box comes from
        the system message (``_NewYearBoxStorage.__handleSysMsg``), so the quest
        itself only has to exist and carry the description of what a box is.
        """
        return {
            "type": EVENT_TYPE_TOKEN_QUEST,
            "hidden": True,
            "name": "Новогодний ящик",
            "description": "Откройте ящик, чтобы получить украшения и награды",
            "startTime": self.start_time - 3600,
            "finishTime": self.start_time + EVENT_DURATION,
            "bonus": {},
            "conditions": self._conditions(),
            "requiredToken": box.id,
            "unlimited": True,
        }

    def box_quests(self) -> Dict[str, dict]:
        return {box.id: self.box_quest(box) for box in sorted(self.catalog.new_year.boxes.values(), key=lambda b: b.id)}

    def chest_quest(self, chest) -> dict:
        """The hidden quest of one chest; its ``bonus`` is what the client shows.

        Until the chest is opened the bonus is the level's baseline table
        (:data:`CHEST_QUESTS`); every opening replaces it with what was actually
        rolled (:meth:`set_chest_loot`).
        """
        loot = dict(CHEST_QUESTS.get(chest.level, CHEST_QUESTS[1]))
        loot.update(self._chest_loot.get(chest.id, {}))
        bonus = loot
        return {
            "type": EVENT_TYPE_TOKEN_QUEST,
            "hidden": True,
            "name": "Новогодний сундук %d" % chest.level,
            "description": loot.get("description", ""),
            "startTime": self.start_time - 3600,
            "finishTime": self.start_time + EVENT_DURATION,
            "bonus": bonus,
            "conditions": self._conditions(),
            "requiredToken": chest.id,
            "unlimited": True,
        }

    def collection_reward_quests(self) -> Dict[str, dict]:
        """The hidden quests of the four collection rewards (``ny18:cr:*``)."""
        quests = {}
        for reward in self.catalog.new_year.collection_rewards:
            quests[reward.id] = {
                "type": EVENT_TYPE_TOKEN_QUEST,
                "hidden": True,
                "name": "Коллекция %s" % reward.setting,
                "description": "Награда за полную коллекцию украшений",
                "startTime": self.start_time - 3600,
                "finishTime": self.start_time + EVENT_DURATION,
                "bonus": {
                    "customizations": collection_reward_customizations(
                        self.catalog.customization, reward.setting),
                    "tokens": {reward.id: {"expires": {"at": None}, "count": 1}},
                },
                "conditions": self._conditions(),
                "requiredToken": reward.id,
                "unlimited": True,
            }
        return quests

    def event_quests(self) -> Dict[str, dict]:
        """Visible New Year quests: the box dailies of "Новогоднее наступление"."""
        quests = {}
        quests["ny18:quest:daily"] = {
            "type": EVENT_TYPE_BATTLE_QUEST,
            "name": "Новогоднее наступление: бой",
            "description": "Проведите бой и получите новогодний ящик",
            "startTime": self.start_time - 3600,
            "finishTime": self.start_time + EVENT_DURATION,
            "groupID": "ny18:group",
            "bonus": {"tokens": {"ny18:box:r": {"expires": {"at": None}, "count": 1}}},
            "conditions": self._conditions(),
            "iconID": "ny18",
        }
        quests["ny18:quest:shop"] = {
            "type": EVENT_TYPE_TOKEN_QUEST,
            "name": "Новогоднее наступление: ящики",
            "description": "Обменяйте жетоны Нового года на ящики",
            "startTime": self.start_time - 3600,
            "finishTime": self.start_time + EVENT_DURATION,
            "groupID": "ny18:group",
            "bonus": {"tokens": {"ny18:box:s": {"expires": {"at": None}, "count": 1}}},
            "conditions": self._conditions(),
            "iconID": "ny18",
        }
        return quests

    def quests(self) -> Dict[str, dict]:
        """Every quest of the event, hidden ones last (they are the loot tables)."""
        quests = {}
        quests.update(self.event_quests())
        quests.update(self.chest_quests())
        quests.update(self.box_quests())
        quests.update(self.collection_reward_quests())
        return quests

    # -- actions --------------------------------------------------------
    def actions(self) -> List[dict]:
        """The action list; the New Year action drives the event views."""
        return [
            {
                "id": NEW_YEAR_ACTION_ID,
                "type": EVENT_TYPE_ACTION,
                "name": "Новогоднее наступление",
                "description": "Украсьте ёлку, соберите коллекцию и откройте все ящики",
                "startTime": self.start_time - 3600,
                "finishTime": self.start_time + EVENT_DURATION,
                "data": {
                    "tag": NEW_YEAR_EVENT_TAG,
                    "hangar": NEW_YEAR_HANGAR_PATH,
                    "questIDs": ["ny18:quest:daily", "ny18:quest:shop"],
                },
            }
        ]

    # -- notifications ---------------------------------------------------
    @staticmethod
    def notification(notification_type: str, data, text: Optional[dict] = None,
                     required_tokens: Optional[List[str]] = None) -> dict:
        """One server notification of ``EVENT_CLIENT_DATA.NOTIFICATIONS``.

        The client wraps every entry into ``account_shared.NotificationItem``,
        which accesses ``type``, ``text``, ``data`` and ``requiredTokens``
        unconditionally (and joins them into one string), so all four keys have
        to be present.  ``text`` maps a client language to the message it shows
        (``helpers.getLocalizedData``); an empty dictionary means "nothing to
        show" and keeps the notification out of the player's message list.
        """
        return {
            "type": notification_type,
            "text": dict(text or {}),
            "data": data,
            "requiredTokens": list(required_tokens or ()),
        }

    def set_hangar_path(self, path: str) -> None:
        """Use another hangar space (``--hangar``, the detected one)."""
        if path:
            self.hangar_path = str(path)

    def hangar_notification(self, notification_type: str, path: str = None) -> dict:
        """One ``cmd_change_hangar`` notification.

        ``gui.ClientHangarSpace.__onEventNotificationsChanged`` parses the
        payload as JSON and reads ``hangar`` (plus an optional hexadecimal
        ``visibilityMask``) from it; a non-JSON payload is taken as the path
        itself, so both forms work and the JSON one is what the live server sent.
        """
        return self.notification(
            notification_type,
            json.dumps({"hangar": str(path or self.hangar_path),
                        "visibilityMask": "FFFFFFFF"}),
        )

    def action_notification(self, text: dict, event_type: str = ACTION_NOTIFY_TYPE) -> dict:
        """One service-channel notification (``ACTION_NOTIFY_TYPE``).

        The text travels as a dictionary of languages
        (``helpers.getLocalizedData`` picks the one the client speaks), the
        ``type`` is what ``ActionNotificationFormatter`` passes to the message
        template as its icon source, and ``data`` only has to exist - the handler
        of a non-``cmd_`` notification reads ``type`` and ``text`` alone
        (``SystemMessagesInterface.__processNotifications``).
        """
        return self.notification(event_type, text.get("cn") or text.get("ru") or "",
                                 text=text)

    def announce(self, key: str, text: dict, event_type: str = ACTION_NOTIFY_TYPE) -> bool:
        """Announce something once per campaign (keyed by ``key``).

        ``True`` means this very call added an announcement - i.e. the answer
        that provoked it has something new to tell the player and should carry
        :meth:`notifications_diff`; ``False`` means the message was already sent
        (or is switched off) and the client has seen it.
        """
        if not self.service_messages or key in self._announcement_ids:
            return False
        self._announcement_ids.add(key)
        self.announcements.append(self.action_notification(text, event_type))
        return True

    def notifications(self) -> List[dict]:
        """Server notifications - the New Year hangar scene in particular.

        ``cmd_change_hangar`` switches the hangar of a regular account and
        ``cmd_change_hangar_prem`` the one of a premium account
        (``gui.ClientHangarSpace``); both are sent so the New Year scene shows
        up whichever state the account is in.
        """
        result = [
            self.hangar_notification(CMD_CHANGE_HANGAR),
            self.hangar_notification(CMD_CHANGE_HANGAR_PREMIUM),
        ]
        if self.hangar_ambient:
            result.append(self.notification(CMD_CHANGE_HANGAR_AMBIENT, self.hangar_ambient))
        if self.hangar_music:
            result.append(self.notification(CMD_CHANGE_HANGAR_MUSIC, self.hangar_music))
        if self.service_messages:
            result.extend(self.announcements)
        return result

    # -- payloads --------------------------------------------------------
    def ingame_events(self) -> dict:
        """``EVENT_CLIENT_DATA.INGAME_EVENTS``: the state of the New Year event.

        ``ClientNewYear.__onEventsDataChanged`` reads
        ``ingameEvents['newYearEvent']['state']`` and drives the whole New Year
        interface from it, so this blob is what actually switches the event on.
        """
        return {"newYearEvent": {"state": int(self.new_year_state)}}

    def events_data(self, notifications: bool = True) -> Dict[int, bytes]:
        """The full ``eventsData`` map (as it arrives in a full account sync).

        ``notifications`` decides whether the notification section travels with
        the payload.  The client treats *any* notification list it receives under
        ``('eventsData', '_r')`` as freshly added
        (``Account.__synchronizeEventNotifications``) and reloads the hangar
        space for every ``cmd_change_hangar`` entry in it, so an update that
        merely opens a chest must not repeat the notifications of the full sync.
        """
        data = {
            EVENT_CLIENT_DATA["QUEST"]: _blob(self.quests()),
            EVENT_CLIENT_DATA["QUEST_REV"]: _blob(self._revision()),
            EVENT_CLIENT_DATA["ACTION"]: _blob(self.actions()),
            EVENT_CLIENT_DATA["ACTION_REV"]: _blob(self._revision()),
            EVENT_CLIENT_DATA["NOTIFICATIONS"]: _blob(self.notifications()),
            EVENT_CLIENT_DATA["NOTIFICATIONS_REV"]: _blob(self._revision()),
            EVENT_CLIENT_DATA["INGAME_EVENTS"]: _blob(self.ingame_events()),
            EVENT_CLIENT_DATA["INGAME_EVENTS_REV"]: _blob(self._revision()),
            EVENT_CLIENT_DATA["ACTION_ENTITIES"]: _blob([]),
            EVENT_CLIENT_DATA["ACTION_ENTITIES_REV"]: _blob(self._revision()),
            EVENT_CLIENT_DATA["ANNOUNCED_ACTION_DATA"]: _blob({}),
            EVENT_CLIENT_DATA["ANNOUNCED_ACTION_DATA_REV"]: _blob(self._revision()),
        }
        if not notifications:
            data.pop(EVENT_CLIENT_DATA["NOTIFICATIONS"], None)
            data.pop(EVENT_CLIENT_DATA["NOTIFICATIONS_REV"], None)
        return data

    def notifications_diff(self) -> Dict[str, dict]:
        """The ``eventsData`` section that tells the client of a new message.

        An *update* may carry the notification list in the plain
        ``diff['eventsData']`` form (and not under ``('eventsData', '_r')``,
        which the client reads as "all of these are new" and would repeat every
        message of the full sync): ``Account._update`` calls
        ``__synchronizeEventNotifications`` *before* ``__synchronizeCacheDict``
        and that helper pops ``NOTIFICATIONS``/``NOTIFICATIONS_REV`` out of the
        dictionary, so nothing else tries to merge them as blobs.  The client
        then compares the list with the one it cached - ``NotificationItem``
        hashes the type, the texts, the data and the required tokens - and hands
        only the additions to its listeners (``SystemMessagesInterface``), which
        is what makes a New Year level-up show up in the service channel right
        in the answer that raised the level.

        The list travels whole, not as the single new entry: the client replaces
        its cache with what arrives and diffs it against the previous list, so
        sending every notification (the old ones included) is what keeps old
        messages from being reported again.
        """
        if not self.service_messages:
            return {}
        return {
            "eventsData": {
                EVENT_CLIENT_DATA["NOTIFICATIONS"]: _blob(self.notifications()),
                EVENT_CLIENT_DATA["NOTIFICATIONS_REV"]: _blob(self._revision()),
            }
        }

    def events_data_diff(self) -> Dict[int, bytes]:
        """The ``eventsData`` payloads of an incremental update.

        The values are ``zlib(pickle)`` blobs, which is what every reader of
        ``BigWorld.player().eventsData`` expects (``EventsCache.__getEventsData``,
        ``ClientNewYear``), so account updates have to travel under the
        ``('eventsData', '_r')`` replacement key: ``Account.__synchronizeCacheDict``
        does ``repDict.clear(); repDict.update(repl)`` for it, while a plain
        ``diff['eventsData']`` entry must be a dictionary of dictionaries
        (``repDict[k].update(v)``) and would break the readers.

        The whole map is sent and not only the changed sections: the client
        replaces its ``eventsData`` with what arrives here, and
        ``ClientNewYear.__onEventsDataChanged`` needs the revision entries of
        every section to keep the cache consistent.
        """
        return self.events_data(notifications=False)

    def _revision(self):
        # ``*_REV`` entries are opaque to the client; a monotonic counter is
        # enough and keeps diagnostics readable.
        return int(self.start_time)


#: Loot shown by ``NYRewards``/``NYMissionRewardWindow`` for a chest level.
#: ``ny18Toys`` is a list of toy ids (``Ny18ToysFormatter``) and ``vehicles`` is
#: a ``{compactDescr: vehInfo}`` map (``VehiclesBonus``); level 1 carries a tank
#: because the client's reward window expects a ``VehiclesBonus`` there.
CHEST_QUESTS = {
    1: {"credits": 100000, "gold": 250, "ny18Toys": [], "vehicles": {}},
    2: {"credits": 75000, "gold": 150, "ny18Toys": []},
    3: {"credits": 60000, "gold": 100, "ny18Toys": []},
    4: {"credits": 45000, "gold": 75, "ny18Toys": []},
    5: {"credits": 40000, "gold": 50, "ny18Toys": []},
    6: {"credits": 35000, "gold": 50, "ny18Toys": []},
    7: {"credits": 30000, "gold": 25, "ny18Toys": []},
    8: {"credits": 25000, "gold": 25, "ny18Toys": []},
    9: {"credits": 20000, "gold": 25, "ny18Toys": []},
    10: {"credits": 15000, "gold": 25, "ny18Toys": []},
}

#: The ``SeasonType`` values a collection reward's camouflage set covers (the
#: event camouflages of ``item_defs/customization`` come in three per nation).
REWARD_SEASONS = (c11n.SEASON_WINTER, c11n.SEASON_SUMMER, c11n.SEASON_DESERT)


def collection_reward_customizations(catalogue, setting: str) -> List[dict]:
    """The camouflage a collection reward of one setting hands out.

    The live server's own reward table is not part of the client, so the local
    one takes the installation's event camouflages (``vehicleBound`` and not in
    the shop) of the setting's nation - one per season - and names their *real*
    ids: the client resolves every entry against its own items cache
    (``CustomizationsBonusFormatter`` -> ``ICustomizationService.getItemByID``),
    and an id it cannot find breaks the awards window.
    """
    nations = [nyc.NATION_INDICES[name]
               for name in nyc.NATIONS_BY_SETTING.get(setting, ()) if name in nyc.NATION_INDICES]
    rewards = []
    for season in REWARD_SEASONS:
        found = catalogue.event_camouflages(nations, season)
        if not found:
            continue
        rewards.append({"custType": c11n.TYPE_NAMES[c11n.TYPE_CAMOUFLAGE],
                        "id": int(found[0].id), "value": 1})
    return rewards


__all__ = [
    "ACTION_NOTIFY_TYPE",
    "CHEST_QUESTS",
    "CMD_NOTIFICATION_PREFIX",
    "PROMO_NOTIFICATION_PREFIX",
    "REWARD_SEASONS",
    "collection_reward_customizations",
    "CMD_CHANGE_HANGAR",
    "CMD_CHANGE_HANGAR_AMBIENT",
    "CMD_CHANGE_HANGAR_MUSIC",
    "CMD_CHANGE_HANGAR_PREMIUM",
    "EVENT_CLIENT_DATA",
    "EVENT_TYPE_ACTION",
    "EVENT_TYPE_BATTLE_QUEST",
    "EVENT_TYPE_TOKEN_QUEST",
    "EventDataBuilder",
    "localized",
    "NEW_YEAR_ACTION_ID",
    "NEW_YEAR_EVENT_TAG",
    "LANGUAGES",
    "NEW_YEAR_HANGAR_PATH",
    "NY_STATE_FINISHED",
    "NY_STATE_IN_PROGRESS",
    "NY_STATE_NOT_STARTED",
    "NY_STATE_SUSPENDED",
]
