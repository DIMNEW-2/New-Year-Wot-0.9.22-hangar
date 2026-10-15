"""Client-facing payloads built on top of :class:`AccountState`.

These helpers turn the account state and the results of New Year actions into
the exact structures the client consumes:

* :func:`sys_message_action` - a chat action carrying a service-channel system
  message (``type`` = ``tokenQuests``), which is how the client learns what a
  loot box contained (``_NewYearBoxStorage.__handleSysMsg``),
* :func:`notification` - one ``eventsData`` notification (the New Year hangar),
* :func:`server_stats` - the ``receiveServerStats`` payload.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from . import commands as cmd

#: ``SCH_CLIENT_MSG_TYPE`` of the client messenger (``messenger.m_constants``).
SCH_CLIENT_MSG_TYPE = {
    "nyBoxOpened": "NYBoxOpened",
    "nyChestOpened": "NYChestOpened",
    "nySettingCollected": "NYSettingCollected",
    "nyPresentBoxReceived": "NYPresentBoxReceived",
    "nyLevelUp": "NYLevelUp",
}


def sys_message_action(message_id: int, message_type: int, data: dict, personal: bool = False,
                       importance: int = cmd.SYS_MESSAGE_IMPORTANCE_NORMAL,
                       user_id: int = 0, active: bool = True,
                       started_at: Optional[int] = None, finished_at: Optional[int] = None) -> dict:
    """One ``CHAT_ACTIONS.sysMessage`` chat action of ``common/chat_shared``.

    The client's ``ServiceChannelMessage.fromChatAction`` copies
    ``chatAction['data']`` into the message fields, so the payload of the system
    message (here: the New Year bonuses) lives inside ``data['data']``.
    """
    now = int(time.time())
    action_id = cmd.CHAT_ACTION_PERSONAL_SYS_MESSAGE if personal else cmd.CHAT_ACTION_SYS_MESSAGE
    payload = {
        "messageId": int(message_id),
        "userId": int(user_id),
        "type": int(message_type),
        "importance": int(importance),
        "active": bool(active),
        "personal": bool(personal),
        "sentTime": now,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "createdAt": now,
        "data": dict(data or {}),
    }
    return {
        "action": action_id,
        "channel": 0,
        "actionResponse": cmd.CHAT_RESPONSE_SUCCESS,
        "group": cmd.CHAT_MEMBER_GROUP_MEMBER,
        "originator": -1,
        "originatorNickName": "",
        "requestID": -1,
        "data": payload,
        "time": now,
        "sentTime": now,
        "flags": 0,
    }


def box_opened_action(bonuses: dict, message_id: int) -> dict:
    """The ``tokenQuests`` system message that carries a box's reward."""
    return sys_message_action(
        message_id=message_id,
        message_type=cmd.SYS_MESSAGE_TYPE_TOKEN_QUESTS,
        data=bonuses,
    )


def notification(notification_type: str, data: str) -> dict:
    """One entry of ``EVENT_CLIENT_DATA.NOTIFICATIONS``."""
    return {"type": notification_type, "data": data}


def server_stats() -> dict:
    """The payload of ``Account.receiveServerStats`` (``gui.shared.utils``)."""
    return {
        "cluster": {"name": "local", "url": ""},
        "periphery": {"id": 0, "name": "local"},
        "battlesLeft": -1,
        "queue": {"isInQueue": False, "isInPrebattle": False},
        "vehTypeLocks": {},
        "globalVehicleLocks": {},
        "restrictions": {},
        "dailyPlayHours": [0],
        "playLimits": ((7 * 24 * 3600, ""), (24 * 3600, "")),
        "freeVehiclesLeft": "",
        "premiumExpiryTime": 0,
        "attrs": 0,
        "mayConsumeWalletResources": 1,
        "isFinPswdVerified": False,
        "oldVehInvIDs": (),
        "serverUTC": int(time.time()),
    }


def new_year_bonus_payload(result, chest_quest_bonus: Optional[dict] = None) -> Dict:
    """The system-message payload of one New Year action.

    ``result`` is a :class:`server.game.new_year.NewYearResult`; a chest also
    publishes the bonus of its hidden quest
    (``_NewYearChestStorage._handleRequestResult`` reads the quest, not the
    message, so the message only carries the ids).
    """
    bonuses = dict(result.bonuses or {})
    bonuses.setdefault("completedQuestIDs", [])
    return bonuses


__all__ = [
    "SCH_CLIENT_MSG_TYPE",
    "box_opened_action",
    "new_year_bonus_payload",
    "notification",
    "server_stats",
    "sys_message_action",
]
