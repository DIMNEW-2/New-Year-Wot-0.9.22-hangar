"""Opcodes and response codes of the 0.9.22 account protocol.

Every constant here is copied from the client's own ``AccountCommands`` /
``AccountCommands``-companion modules of the supported build
(``World_of_Tanks_0.09.22.00.01_CH_1513_HD``), so the client and the local
server never disagree about what a request number means.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Response codes (``common/AccountCommands.py``)
# --------------------------------------------------------------------------
RES_FAILURE = -1
RES_WRONG_ARGS = -2
RES_NON_PLAYER = -3
RES_SHOP_DESYNC = -4
RES_COOLDOWN = -5
RES_HIDDEN_DOSSIER = -6
RES_CENTER_DISCONNECTED = -7
RES_TUTORIAL_DISABLED = -8
RES_NOT_AVAILABLE = -10
RES_BOOTCAMP_DISABLED = -11
RES_BOOTCAMP_ALREADY_RUNNING = -12
RES_SUCCESS = 0
RES_STREAM = 1
RES_CACHE = 2

# --------------------------------------------------------------------------
# Account commands (``common/AccountCommands.py``)
# --------------------------------------------------------------------------
CMD_SYNC_DATA = 100
CMD_EQUIP = 101
CMD_EQUIP_OPTDEV = 102
CMD_EQUIP_SHELLS = 103
CMD_EQUIP_EQS = 104
CMD_EQUIP_TMAN = 105
CMD_REPAIR = 106
CMD_VEH_SETTINGS = 107
CMD_SET_AND_FILL_LAYOUTS = 108

CMD_UNLOCK = 201
CMD_EXCHANGE = 202
CMD_FREE_XP_CONV = 203
CMD_PREMIUM = 204
CMD_BUY_SLOT = 205
CMD_BUY_BERTHS = 206

CMD_SYNC_SHOP = 300
CMD_BUY_VEHICLE = 301
CMD_BUY_ITEM = 302
CMD_BUY_TMAN = 303
CMD_SELL_VEHICLE = 304
CMD_SELL_ITEM = 305
CMD_BUY_AND_EQUIP_ITEM = 308
CMD_BUY_AND_EQUIP_TMAN = 309

CMD_REQ_SERVER_STATS = 501
CMD_REQ_PLAYER_INFO = 503

CMD_SYNC_DOSSIERS = 600

CMD_UNLOCK_ALL = 10004
CMD_BUY_GOODIE = 10014
CMD_ADD_INT_USER_SETTINGS = 1600

#: New Year 2018 event commands (``common/AccountCommands.py``).
CMD_NEW_YEAR_SLOT_FILL = 20161
CMD_NEW_YEAR_CRAFT = 20162
CMD_NEW_YEAR_BREAK_TOYS = 20163
CMD_NEW_YEAR_ADD_TOYS_DEV = 20164
CMD_NEW_YEAR_OPEN_BOX = 20165
CMD_NEW_YEAR_ADD_TOKEN_DEV = 20166
CMD_NEW_YEAR_SEE_TOYS = 20167
CMD_NEW_YEAR_SELECT_DISCOUNT = 20168
CMD_NEW_YEAR_SELECT_TANKMAN = 20169

#: ``REQUEST_ID_*`` of the client; requests with a reserved id never expect a
#: response, so the server must not answer them.
REQUEST_ID_NO_RESPONSE = 0
REQUEST_ID_PROTOTYPE = 1
REQUEST_ID_SYNC_DATA_NEEDED = 2
REQUEST_ID_SYNC_DATA_NOT_NEEDED = 3
REQUEST_ID_QUEUE_INFO = 5
REQUEST_ID_PREBATTLES = 6
REQUEST_ID_CLAN_MEMBERS = 7
REQUEST_ID_SERVER_STATS = 8
REQUEST_ID_PLAYER_INFO = 9
REQUEST_ID_UNRESERVED_MIN = 100

#: The command name the client's ``_doCmd*`` helpers use, mapped to the number
#: of positional arguments that follow the opcode on the wire.
COMMAND_ARITY = {
    "doCmdStr": 1,
    "doCmdIntStr": 2,
    "doCmdInt3": 3,
    "doCmdInt4": 4,
    "doCmdInt2Str": 3,
    "doCmdIntArr": 1,
    "doCmdIntStrArr": 2,
    "doCmdIntArrStrArr": 2,
}

# --------------------------------------------------------------------------
# New Year event constants (``client/new_year/requester/new_year_requester.py``)
# --------------------------------------------------------------------------
NY_CLIENT_CTRL_ERROR = -555
NY_CLIENT_VALIDATOR_ERROR = -556


class NYRequestType:
    OPEN_BOX = 1
    OPEN_CHEST = 2
    APPLY_VEH_DISCOUNT = 3
    RECRUIT_TANK_WOMAN = 4
    CRAFT_TOY = 5
    BREAK_TOYS = 6
    PLACE_TOY = 7


#: ``chat_shared.SYS_MESSAGE_TYPE.index()`` of ``tokenQuests`` - the system
#: message that carries the New Year box reward
#: (``client/new_year/new_year_controller._TOKEN_SYS_MSG_TYPE``).
SYS_MESSAGE_TYPE_TOKEN_QUESTS = 29
SYS_MESSAGE_TYPE_ADMIN_TEXT = 6
SYS_MESSAGE_TYPE_GOLD_RECEIVED = 4

#: ``chat_shared.SYS_MESSAGE_IMPORTANCE``.
SYS_MESSAGE_IMPORTANCE_NORMAL = 0
SYS_MESSAGE_IMPORTANCE_HIGH = 1

#: ``CHAT_ACTIONS`` ids of ``common/chat_shared.py``.  The enumeration is long
#: (``enter`` is 0, ``broadcast`` 1, ...); both ids were read back from the
#: build's own ``chat_shared`` and the client indexes its callbacks with them
#: (``ChatManager``/``ClientChat.onChatAction``).
CHAT_ACTION_SYS_MESSAGE = 21
CHAT_ACTION_PERSONAL_SYS_MESSAGE = 22

#: ``CHAT_RESPONSES.success`` - every ``onChatAction`` compares the response of
#: an action against it (``ClientChat.onChatAction``) and treats anything else
#: as a failure, so a pushed system message always has to carry it.
CHAT_RESPONSE_SUCCESS = 0

#: ``CHAT_MEMBER_GROUP.member`` - the default group of a chat action.
CHAT_MEMBER_GROUP_MEMBER = 1

#: ``EVENT_CLIENT_DATA.NOTIFICATIONS`` / ``NOTIFICATIONS_REV``.
NOTIFICATIONS_KEY = 9
NOTIFICATIONS_REV_KEY = 10

__all__ = [
    "CHAT_ACTION_PERSONAL_SYS_MESSAGE",
    "CHAT_ACTION_SYS_MESSAGE",
    "CHAT_MEMBER_GROUP_MEMBER",
    "CHAT_RESPONSE_SUCCESS",
    "CMD_ADD_INT_USER_SETTINGS",
    "CMD_BUY_AND_EQUIP_ITEM",
    "CMD_BUY_AND_EQUIP_TMAN",
    "CMD_BUY_BERTHS",
    "CMD_BUY_GOODIE",
    "CMD_BUY_ITEM",
    "CMD_BUY_SLOT",
    "CMD_BUY_TMAN",
    "CMD_BUY_VEHICLE",
    "CMD_EQUIP",
    "CMD_EQUIP_EQS",
    "CMD_EQUIP_OPTDEV",
    "CMD_EQUIP_SHELLS",
    "CMD_EQUIP_TMAN",
    "CMD_EXCHANGE",
    "CMD_FREE_XP_CONV",
    "CMD_NEW_YEAR_ADD_TOKEN_DEV",
    "CMD_NEW_YEAR_ADD_TOYS_DEV",
    "CMD_NEW_YEAR_BREAK_TOYS",
    "CMD_NEW_YEAR_CRAFT",
    "CMD_NEW_YEAR_OPEN_BOX",
    "CMD_NEW_YEAR_SEE_TOYS",
    "CMD_NEW_YEAR_SELECT_DISCOUNT",
    "CMD_NEW_YEAR_SELECT_TANKMAN",
    "CMD_NEW_YEAR_SLOT_FILL",
    "CMD_PREMIUM",
    "CMD_REPAIR",
    "CMD_REQ_PLAYER_INFO",
    "CMD_REQ_SERVER_STATS",
    "CMD_SELL_ITEM",
    "CMD_SELL_VEHICLE",
    "CMD_SET_AND_FILL_LAYOUTS",
    "CMD_SYNC_DATA",
    "CMD_SYNC_DOSSIERS",
    "CMD_SYNC_SHOP",
    "CMD_UNLOCK",
    "CMD_UNLOCK_ALL",
    "CMD_VEH_SETTINGS",
    "COMMAND_ARITY",
    "NOTIFICATIONS_KEY",
    "NOTIFICATIONS_REV_KEY",
    "NY_CLIENT_CTRL_ERROR",
    "NY_CLIENT_VALIDATOR_ERROR",
    "NYRequestType",
    "REQUEST_ID_NO_RESPONSE",
    "REQUEST_ID_UNRESERVED_MIN",
    "RES_FAILURE",
    "RES_NON_PLAYER",
    "RES_SHOP_DESYNC",
    "RES_STREAM",
    "RES_SUCCESS",
    "SYS_MESSAGE_IMPORTANCE_HIGH",
    "SYS_MESSAGE_IMPORTANCE_NORMAL",
    "SYS_MESSAGE_TYPE_ADMIN_TEXT",
    "SYS_MESSAGE_TYPE_GOLD_RECEIVED",
    "SYS_MESSAGE_TYPE_TOKEN_QUESTS",
]
