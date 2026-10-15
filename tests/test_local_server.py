"""The local server seen through its own socket: the path the client mod takes.

This is the closest thing to the real client that can run without Windows: the
test starts :class:`server.account_rpc.server.AccountServer`, connects a socket
exactly like the mod does, sends the same frames (``hello``, ``cmd``) and then
applies the answers the way ``client/Account.py`` does:

* a full ``welcome`` diff for the first sync,
* an incremental diff for every command, which must echo the revision the
  client sent (``prevRev``) or the client refuses it,
* ``eventsData`` as a ``('eventsData', '_r')`` entry holding a zlib-compressed
  Python-2 pickle of the quests,
* New Year rewards as a chat ``sysMessage`` action of type 29.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc.server import PROTOCOL_VERSION, AccountServer  # noqa: E402
from server.account_rpc.state import AccountState  # noqa: E402
from server.proto import pack_message, py2_loads, read_frame, unpack_message  # noqa: E402


class Bridge(object):
    """A miniature client mod: the framing of ``server.proto`` over TCP."""

    def __init__(self, port: int):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=30.0)
        self.reader = self.socket.makefile("rb")
        self.writer = self.socket.makefile("wb")
        self.pending = []
        self.diffs = []
        self.messages = []
        self.streams = []

    def send(self, message) -> None:
        self.writer.write(pack_message(message))
        self.writer.flush()

    def recv(self):
        return unpack_message(read_frame(self.reader))

    def hello(self, revision: int = 0, client: str = "test") -> dict:
        self.send({"type": "hello", "protocol": PROTOCOL_VERSION, "client": client})
        welcome = self.recv()
        assert welcome["type"] == "welcome", welcome
        return welcome

    def command(self, request_id: int, code: int, args: tuple = ()):
        """Send one ``_doCmd*`` and collect its answer, diffs and sys messages."""
        self.send({"type": "cmd", "requestID": request_id, "code": code, "args": tuple(args)})
        response = None
        diffs = []
        actions = []
        streams = []
        while True:
            message = self.recv()
            kind = message.get("type")
            if kind == "diff":
                diffs.append(message["diff"])
            elif kind == "sysMessage":
                actions.append(message["action"])
            elif kind == "stream":
                streams.append(message)
            elif kind == "response":
                response = message
                break
            else:  # a push from the server between answers
                self.messages.append(message)
        self.diffs = diffs
        self.streams = streams
        return response, diffs, actions

    def close(self) -> None:
        try:
            self.send({"type": "bye"})
        except OSError:
            pass
        self.writer.close()
        self.reader.close()
        self.socket.close()


def _synchronize(cache: dict, diff: dict) -> dict:
    """``client/Account.py``'s view of one diff (full sync when there is no prevRev)."""
    full = "prevRev" not in diff
    if full:
        cache.clear()
    for key, value in diff.items():
        if isinstance(key, tuple):
            name, action = key
            if action == "_r":
                cache.setdefault(name, {})
                if isinstance(value, dict):
                    cache[name] = dict(value)
                else:
                    cache[name] = value
        elif key in ("rev", "prevRev"):
            cache[key] = value
        else:
            target = cache.setdefault(key, {})
            if isinstance(value, dict) and isinstance(target, dict):
                target.update(value)
            else:
                cache[key] = value
    return cache


def _sys_message(action: dict) -> dict:
    """``{'type': ..., 'data': ...}`` of one chat action the server pushed.

    The action is the ``data`` of a ``sysMessage`` chat action
    (``common/chat_shared.py``): ``action['data']['type']`` is the
    ``SYS_MESSAGE_TYPE`` and ``action['data']['data']`` the message itself.
    """
    return action["data"]


def _events_quests(cache: dict) -> dict:
    """The quests the client would read from ``BigWorld.player().eventsData``."""
    from server.game.events import EVENT_CLIENT_DATA

    blob = cache.get("eventsData") or {}
    payload = blob[EVENT_CLIENT_DATA["QUEST"]]
    return py2_loads(zlib.decompress(payload))


def test_welcome_and_framing(runner: common.Runner) -> None:
    """The bridge gets a full account diff and can sync it."""
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        welcome = bridge.hello()
        runner.equal(welcome["protocol"], PROTOCOL_VERSION, "protocol version")
        runner.equal(welcome["account"]["name"], state.name, "account name")
        cache = _synchronize({}, welcome["diff"])
        runner.check(cache["rev"] >= 1, "the welcome diff carries a revision")
        runner.equal(len(cache["inventory"][1]["compDescr"]), len(state.vehicles),
                     "the garage of the welcome diff")
        runner.check("newYear" in cache, "the welcome diff carries the New Year data")
        runner.check("eventsData" in cache, "the welcome diff carries the events data")
        settings = cache.get("serverSettings") or {}
        runner.check("wallet" in settings and "file_server" in settings,
                     "the welcome diff carries the server settings the lobby reads")
        bridge.close()
    finally:
        server.stop()


def test_sync_command_echoes_revision(runner: common.Runner) -> None:
    """``CMD_SYNC_DATA`` answers with the client's own revision as ``prevRev``."""
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        welcome = bridge.hello()
        cache = _synchronize({}, welcome["diff"])
        revision = cache["rev"]

        response, diffs, actions = bridge.command(1, cmd.CMD_SYNC_DATA, (revision, 0, 0, 0, 0))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the sync answer code")
        for diff in diffs:
            if "prevRev" in diff:  # an incremental diff
                runner.equal(diff["prevRev"], revision, "the echoed revision")
                _synchronize(cache, diff)
        bridge.close()
    finally:
        server.stop()


def test_open_box_over_the_socket(runner: common.Runner) -> None:
    """A loot box opened through the bridge: newYear diff + system message."""
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        cache = _synchronize({}, bridge.hello()["diff"])
        box_id = sorted(state.catalog.new_year.boxes)[0]
        response, diffs, actions = bridge.command(2, cmd.CMD_NEW_YEAR_OPEN_BOX, (box_id,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the answer code")
        runner.check(bool(diffs), "the box produced a diff")
        for diff in diffs:
            _synchronize(cache, diff)
        runner.check(bool(actions), "the box produced a system message")
        message = _sys_message(actions[0])
        runner.equal(message["type"], cmd.SYS_MESSAGE_TYPE_TOKEN_QUESTS, "message type")
        runner.check(box_id in message["data"]["completedQuestIDs"],
                     "the message names the box")
        runner.check("ny18Toys" in message["data"], "the message carries the toys")
        runner.equal(state.new_year.state.boxes[box_id], 4, "the box was consumed")
        bridge.close()
    finally:
        server.stop()


def test_open_chest_publishes_loot(runner: common.Runner) -> None:
    """A chest hands its reward out through its hidden quest in ``eventsData``."""
    state = common.new_account(runner.client)
    chest_id = sorted(state.catalog.new_year.chests)[0]
    state.new_year.state.chests[chest_id] = 2
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        cache = _synchronize({}, bridge.hello()["diff"])
        quests = _events_quests(cache)
        runner.check(chest_id in quests, "the chest has a hidden quest")

        response, diffs, actions = bridge.command(3, cmd.CMD_NEW_YEAR_OPEN_BOX, (chest_id,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the answer code")
        for diff in diffs:
            _synchronize(cache, diff)
        message = _sys_message(actions[0])
        runner.equal(message["type"], cmd.SYS_MESSAGE_TYPE_TOKEN_QUESTS, "message type")
        runner.check(chest_id in message["data"]["completedQuestIDs"],
                     "the message names the chest")

        quests = _events_quests(cache)
        bonus = quests[chest_id]["bonus"]
        runner.check(bonus, "the hidden quest now carries the loot")
        runner.equal(bonus.get("credits"), message["data"].get("credits"),
                     "the quest and the message agree on the credits")
        runner.check(bonus.get("vehicles") is not None or bonus.get("gold"),
                     "the chest rolled something worth showing")
        bridge.close()
    finally:
        server.stop()


def test_account_survives_a_restart(runner: common.Runner) -> None:
    """The account file keeps the garage, the boxes and the New Year state."""
    import tempfile

    state = common.new_account(runner.client)
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    try:
        server = AccountServer(state, port=0, save_path=path).start()
        try:
            bridge = Bridge(server.port)
            bridge.hello()
            bridge.command(4, cmd.CMD_NEW_YEAR_OPEN_BOX, ("ny18:box:r",))
            bridge.close()
        finally:
            server.stop()

        restored = AccountState.from_json(json.load(open(path, encoding="utf-8")), state.catalog)
        runner.equal(len(restored.vehicles), len(state.vehicles), "the garage came back")
        runner.equal(restored.new_year.state.boxes["ny18:box:r"], 4, "the box stayed consumed")
        runner.equal(restored.new_year.state.level, state.new_year.state.level, "the level")
    finally:
        os.unlink(path)


def test_hangar_notifications_ride_only_in_the_full_sync(runner: common.Runner) -> None:
    """The New Year hangar scene arrives once; updates never repeat it.

    ``Account.__synchronizeEventNotifications`` reports *every* notification it
    receives as freshly added, and ``ClientHangarSpace`` recreates the hangar for
    each ``cmd_change_hangar`` entry, so a chest opened later must not carry the
    notification list again.
    """
    from server.game.events import CMD_CHANGE_HANGAR, EVENT_CLIENT_DATA, NEW_YEAR_HANGAR_PATH

    state = common.new_account(runner.client)
    chest_id = sorted(state.catalog.new_year.chests)[0]
    state.new_year.state.chests[chest_id] = 2
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        cache = _synchronize({}, bridge.hello()["diff"])
        blob = cache["eventsData"]
        runner.check(EVENT_CLIENT_DATA["NOTIFICATIONS"] in blob,
                     "the full sync carries the notification list")
        notifications = py2_loads(zlib.decompress(blob[EVENT_CLIENT_DATA["NOTIFICATIONS"]]))
        types = [entry["type"] for entry in notifications]
        runner.check(CMD_CHANGE_HANGAR in types, "the hangar notification is there")
        hangar = json.loads(
            [entry for entry in notifications if entry["type"] == CMD_CHANGE_HANGAR][0]["data"]
        )
        runner.equal(hangar["hangar"], NEW_YEAR_HANGAR_PATH, "the New Year scene path")
        for entry in notifications:
            runner.check(set(("type", "text", "data", "requiredTokens")) <= set(entry),
                         "every notification is NotificationItem-shaped")

        response, diffs, actions = bridge.command(5, cmd.CMD_NEW_YEAR_OPEN_BOX, (chest_id,))
        runner.equal(response["code"], cmd.RES_SUCCESS, "the answer code")
        updated = [diff for diff in diffs if _has_events_data(diff)]
        runner.check(bool(updated), "opening the chest updates the events data")
        for diff in updated:
            sections = diff[("eventsData", "_r")]
            runner.check(EVENT_CLIENT_DATA["NOTIFICATIONS"] not in sections
                         and EVENT_CLIENT_DATA["NOTIFICATIONS_REV"] not in sections,
                         "the update does not repeat the notifications")
            runner.check(EVENT_CLIENT_DATA["QUEST"] in sections,
                         "the update still carries the quests")
        for diff_part in diffs:
            _synchronize(cache, diff_part)
        quests = _events_quests(cache)
        runner.check(chest_id in quests, "the client still sees the chest quest")
        bridge.close()
    finally:
        server.stop()


def test_server_settings_shape(runner: common.Runner) -> None:
    """``serverSettings`` has the keys and shapes the lobby indexes directly."""
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        cache = _synchronize({}, bridge.hello()["diff"])
        settings = cache["serverSettings"]
        runner.equal(len(settings["roaming"]), 3, "roaming is [home, current, servers]")
        wallet = settings["wallet"]
        runner.equal(len(wallet), 2, "wallet is (useGold, useFreeXP)")
        file_server = settings["file_server"]
        runner.check(bool(file_server), "the file server is configured")
        for url in file_server.values():
            runner.check("url_template" in url, "every file server entry has a url_template")
        regional = settings["regional_settings"]
        for key in ("starting_day_of_a_new_week", "starting_time_of_a_new_day",
                    "starting_time_of_a_new_game_day"):
            runner.check(key in regional, "the regional settings carry %s" % key)
        bridge.close()
    finally:
        server.stop()


def _has_events_data(diff) -> bool:
    return ("eventsData", "_r") in diff


def test_shop_sync_is_a_stream(runner: common.Runner) -> None:
    """``CMD_SYNC_SHOP`` answers with ``RES_STREAM`` and the price cache.

    ``Shop.__onSyncDataReceived`` reads ``sellPriceFactor`` from the unpacked
    payload and every price lookup asserts the revision of the cache, so both
    have to travel with the stream; the client subscribes to it through
    ``Account.onStreamComplete``/``SyncController``.
    """
    state = common.new_account(runner.client)
    server = AccountServer(state, port=0, save_path=None).start()
    try:
        bridge = Bridge(server.port)
        bridge.hello()
        response, diffs, actions = bridge.command(6, cmd.CMD_SYNC_SHOP, (0, 0, 0))
        runner.equal(response["code"], cmd.RES_STREAM, "the answer code")
        # The stream follows its response: ``SyncController`` subscribes to the
        # request id only after it has seen ``RES_STREAM``.
        frame = bridge.recv()
        runner.equal(frame["type"], "stream", "the answer is followed by a stream")
        runner.equal(frame["requestID"], 6, "the stream belongs to the request")
        desc = tuple(frame["desc"])
        runner.equal(len(desc), 5, "the stream header has five fields")
        runner.check(not desc[0], "the stream is not corrupt")
        runner.equal(desc[1], desc[2], "the sizes of an uncompressed stream")
        shop = py2_loads(zlib.decompress(frame["data"]))
        runner.check("sellPriceFactor" in shop,
                     "the shop carries the sell price factor the client copies")
        runner.check(shop["rev"] >= 1, "the shop carries a revision")
        prices = shop["items"]["itemPrices"]
        runner.check(len(prices) > 100, "the shop prices the vehicles and modules")
        vehicle = sorted(state.owned_vehicle_comp_descrs())[0]
        runner.check(vehicle in prices, "an owned vehicle has a price")
        runner.check(isinstance(shop.get("defaults"), dict),
                     "the shop carries the client's defaults")
        bridge.close()
    finally:
        server.stop()


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
