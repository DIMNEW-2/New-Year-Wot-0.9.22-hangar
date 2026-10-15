"""``python3 -m server verify``: the checks a run of the game depends on.

The desktop side of this project can be checked without Windows: the game data
of the player's own installation, the New Year definitions, the crew name
lists, and - most importantly - a whole session over the real wire protocol,
with the same frames the client mod sends.

Two things cannot be checked from here and are reported as *warnings* with an
explanation instead of failures:

* the New Year hangar space (``spaces/h07_newyear_2015``) - it lives in the
  client's own packages, so the installations's archives are searched for it,
* the client mod - it has to be copied into ``res_mods`` of the installation.

The report is what the user reads before starting the game, so every line says
what was checked and what to do when it went wrong.
"""

from __future__ import annotations

import os
import socket
import sys
import zipfile
import zlib
from typing import List, Optional, Tuple

from . import loader
from .account_rpc import commands as cmd
from .account_rpc.server import AccountServer
from .account_rpc.state import AccountState
from .game.defs import DefsError
from .proto import pack_message, py2_loads, read_frame, unpack_message

OK = "ok"
WARN = "warn"
FAIL = "fail"

#: The hangar space the New Year notification switches the client to
#: (``server/game/events.py::NEW_YEAR_HANGAR_PATH``).
HANGAR_SPACE = "spaces/h07_newyear_2015"

#: Where the mod has to be for the client to load it, relative to the game
#: folder (see ``client_mod/README.md``).
MOD_PATH = os.path.join("res_mods", "0.9.22.0.1", "scripts", "client", "gui",
                        "mods", "mod_wotny_local.py")

_MARKS = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}

#: Sections a full account diff has to carry (the caches of the lobby read
#: every one of them: ``client/Account.py`` -> ``ItemsCache``, ``EventsCache``,
#: ``SettingsCache``, ``Stats``, ``ClientNewYear``).
DIFF_SECTIONS = ("inventory", "stats", "account", "economics", "tokens",
                 "newYear", "goodies", "serverSettings", "eventsData")


class Report(object):
    """The lines of one check, and how many of them are bad news."""

    def __init__(self, title: str = ""):
        self.title = title
        self.lines: List[Tuple[str, str, str]] = []

    def add(self, status: str, title: str, detail: str = "") -> None:
        self.lines.append((status, title, detail))

    def ok(self, title: str, detail: str = "") -> None:
        self.add(OK, title, detail)

    def warn(self, title: str, detail: str = "") -> None:
        self.add(WARN, title, detail)

    def fail(self, title: str, detail: str = "") -> None:
        self.add(FAIL, title, detail)

    def count(self, status: str) -> int:
        return len([line for line in self.lines if line[0] == status])

    @property
    def problems(self) -> int:
        return self.count(FAIL)

    @property
    def warnings(self) -> int:
        return self.count(WARN)

    def render(self, stream=sys.stdout) -> None:
        if self.title:
            stream.write("%s\n" % self.title)
        for status, title, detail in self.lines:
            stream.write("  %s %s\n" % (_MARKS.get(status, status), title))
            if detail:
                for line in str(detail).splitlines():
                    stream.write("         %s\n" % line)


# ---------------------------------------------------------------------------
# The installation
# ---------------------------------------------------------------------------
def _archives_to_search(client_root: str, limit: int = 64) -> List[str]:
    """The client's packages - the ones that can hold a space first."""
    folders = [os.path.join(client_root, "res", "packages"),
               os.path.join(client_root, "res"),
               client_root]
    found: List[str] = []
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            if os.path.splitext(name)[1].lower() not in (".pkg", ".zip"):
                continue
            if path not in found:
                found.append(path)
        if found:
            break

    def rank(path: str):
        lowered = os.path.basename(path).lower()
        interesting = ("space" in lowered or "hangar" in lowered or "newyear" in lowered
                       or "res" == lowered)
        return (0 if interesting else 1, lowered)

    return sorted(found, key=rank)[:limit]


def find_hangar_space(client_root: str) -> Tuple[bool, str]:
    """Does this installation ship ``spaces/h07_newyear_2015``?"""
    wanted = HANGAR_SPACE.lower()
    name = os.path.basename(HANGAR_SPACE).lower()
    archives = _archives_to_search(client_root)
    for path in archives:
        if name in os.path.basename(path).lower():
            return True, "the package %s is named after it" % os.path.basename(path)
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    member = info.filename.replace("\\", "/").lower().lstrip("/")
                    if member.startswith(wanted + "/") or member == wanted:
                        return True, "%s is in %s" % (info.filename.strip("/"),
                                                      os.path.basename(path))
        except (zipfile.BadZipFile, OSError, RuntimeError):
            continue
    return False, "searched %d archive(s) of the installation" % len(archives)


def check_installation(root: str) -> Report:
    """Everything the game needs from the installation."""
    report = Report("The installation")
    try:
        layout = loader.find_client(root)
    except loader.ClientNotFound as error:
        report.fail("the installation is unreadable",
                    "%s\n%s" % (error, loader.describe_installation(root)))
        return report
    report.ok("game data found", loader.source_description(root))

    catalog = None
    try:
        catalog = loader.load_catalog(root)
        catalog.validate()
    except (DefsError, ValueError) as error:
        report.fail("the game data of the installation is incomplete", str(error))
    if catalog is not None:
        ny = catalog.new_year
        detail = "%d vehicles, %d decorations, %d slots, %d boxes, %d chests" % (
            len(catalog.vehicles.all()), len(ny.toys), len(ny.slots),
            len(ny.boxes), len(ny.chests))
        if not (ny.toys and ny.boxes and ny.chests and ny.collection_rewards):
            report.fail("the New Year definitions are empty", detail)
        else:
            report.ok("New Year definitions", detail)

    try:
        configs = loader.load_tankmen_configs(root)
    except Exception as error:  # noqa: BLE001 - the data decides
        configs = {}
        report.warn("cannot read the crew name lists", str(error))
    if configs:
        report.ok("crew name lists", "%d nations" % len(configs))
    else:
        report.warn("no crew name lists",
                    "crew members and the New Year tankwoman would be missing;\n"
                    "the installation needs res/scripts or res/scripts.pkg")

    installed, detail = find_hangar_space(layout.root)
    if installed:
        report.ok("the New Year hangar space (%s)" % HANGAR_SPACE, detail)
    else:
        report.warn("the New Year hangar space (%s) was not found" % HANGAR_SPACE,
                    "%s - the hangar would stay the stock one.\n"
                    "Set WOTNY_NO_HANGAR=1 in the client environment to accept that, "
                    "or point\nserver/game/events.py::NEW_YEAR_HANGAR_PATH at the space "
                    "this build ships." % detail)

    mod = os.path.join(layout.root, MOD_PATH)
    repo_scripts = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client_mod", "res_mods", "0.9.22.0.1", "scripts")
    if os.path.isfile(mod):
        report.ok("the client mod is installed", mod)
    else:
        report.warn("the client mod is not installed yet",
                    "copy it into the game folder:\n"
                    "  %s\n  ->  %s"
                    % (repo_scripts, os.path.join(layout.root, "res_mods", "0.9.22.0.1",
                                                  "scripts")))
    return report


# ---------------------------------------------------------------------------
# A whole session over the wire
# ---------------------------------------------------------------------------
class _Session(object):
    """The smallest client there is: the framing of ``server.proto`` over TCP."""

    def __init__(self, port: int, timeout: float = 30.0):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.reader = self.socket.makefile("rb")
        self.writer = self.socket.makefile("wb")
        self.pushed = []
        self.streams = []

    def send(self, message) -> None:
        self.writer.write(pack_message(message))
        self.writer.flush()

    def recv(self):
        return unpack_message(read_frame(self.reader))

    def command(self, request_id: int, code: int, args: tuple = ()):
        """Send one ``_doCmd*`` and collect ``(response, diffs, messages)``."""
        self.send({"type": "cmd", "requestID": request_id, "code": code,
                   "args": tuple(args)})
        response, diffs, messages = None, [], []
        while True:
            message = self.recv()
            kind = message.get("type")
            if kind == "diff":
                diffs.append(message["diff"])
            elif kind == "sysMessage":
                messages.append(message["action"])
            elif kind == "stream":
                self.streams.append(message)
            elif kind == "response":
                response = message
                break
            else:
                self.pushed.append(message)
        return response, diffs, messages

    def hello(self, client: str = "verify") -> dict:
        from .account_rpc.server import PROTOCOL_VERSION

        self.send({"type": "hello", "protocol": PROTOCOL_VERSION, "client": client})
        welcome = self.recv()
        if welcome.get("type") != "welcome":
            raise RuntimeError("the server did not answer the handshake: %r" % (welcome,))
        return welcome

    def close(self) -> None:
        try:
            self.send({"type": "bye"})
        except OSError:
            pass
        for stream in (self.writer, self.reader, self.socket):
            try:
                stream.close()
            except OSError:
                pass


def _sections_of(diff) -> set:
    """The names of a diff's top level sections (``('eventsData','_r')`` -> name)."""
    names = set()
    for key in diff or {}:
        if isinstance(key, tuple):
            names.add(key[0])
        elif key not in ("rev", "prevRev"):
            names.add(key)
    return names


def _first_box(state) -> Optional[str]:
    for box, count in sorted(state.new_year.state.boxes.items()):
        if count:
            return box
    return None


def _first_vehicle(state) -> int:
    entries = sorted(state.vehicles.values(), key=lambda entry: entry.inv_id)
    return entries[0].inv_id if entries else 0


def check_session(catalog, tankmen_configs=None) -> Report:
    """Start the server on a free port and talk to it like the client does."""
    report = Report("A session over the wire")
    state = AccountState.new(catalog, tankmen_configs=tankmen_configs or {})
    try:
        server = AccountServer(state, host="127.0.0.1", port=0, save_path=None).start()
    except OSError as error:
        report.fail("the local server cannot start", str(error))
        return report
    session = None
    try:
        session = _Session(server.port)
        welcome = session.hello()
        account = welcome.get("account") or {}
        sections = _sections_of(welcome.get("diff") or {})
        if account.get("name") and sections:
            report.ok("the bridge handshake answers",
                      "account %r, revision %s, %s"
                      % (account.get("name"), (welcome.get("diff") or {}).get("rev"),
                         ", ".join(sorted(sections))))
        else:
            report.fail("the handshake does not carry an account",
                        "welcome: %r" % (welcome,))

        response, diffs, _ = session.command(1, cmd.CMD_SYNC_DATA, (0, 0, 0, 0, 0))
        # ``AccountSyncData.__onSyncResponse`` reads the diff from the ext of the
        # answer (a pickle, unpacked by the bridge of the client), so that is
        # where the snapshot has to be.
        ext = response.get("ext")
        diff = py2_loads(ext) if isinstance(ext, bytes) else (diffs[-1] if diffs else {})
        missing = [name for name in DIFF_SECTIONS if name not in _sections_of(diff)]
        if response.get("code") != cmd.RES_SUCCESS:
            report.fail("CMD_SYNC_DATA is not answered", repr(response))
        elif missing:
            report.fail("the account diff is incomplete",
                        "missing: %s" % ", ".join(missing))
        else:
            report.ok("the account diff (CMD_SYNC_DATA)",
                      "%d sections, %d vehicles, %d crew members"
                      % (len(_sections_of(diff)), len(state.vehicles), len(state.tankmen)))

        response, _, _ = session.command(2, cmd.CMD_SYNC_SHOP, (0, 0, 0))
        frame = session.recv()
        if response.get("code") != cmd.RES_STREAM or frame.get("type") != "stream":
            report.fail("the price cache (CMD_SYNC_SHOP) is not streamed",
                        "answer %r, next frame %r" % (response, frame))
        else:
            shop = py2_loads(zlib.decompress(frame["data"]))
            prices = (shop.get("items") or {}).get("itemPrices") or {}
            if len(prices) > 100 and "sellPriceFactor" in shop:
                report.ok("the price cache (CMD_SYNC_SHOP)",
                          "%d prices, sell factor %s, exchange rate %s"
                          % (len(prices), shop.get("sellPriceFactor"),
                             shop.get("exchangeRate")))
            else:
                report.fail("the price cache is empty", "%d prices" % len(prices))

        box = _first_box(state)
        if box is None:
            report.warn("no loot box to open", "the event state holds no boxes")
        else:
            response, diffs, messages = session.command(3, cmd.CMD_NEW_YEAR_OPEN_BOX, (box,))
            payload = (messages[0].get("data") or {}) if messages else {}
            prizes = payload.get("data") or {}
            if response.get("code") != cmd.RES_SUCCESS:
                report.fail("opening a loot box is not answered", repr(response))
            elif payload.get("type") != cmd.SYS_MESSAGE_TYPE_TOKEN_QUESTS:
                report.fail("opening a loot box does not report its prizes",
                            "sys message: %r" % (payload,))
            else:
                report.ok("opening a loot box (%s)" % box,
                          "prize sections: %s" % ", ".join(sorted(prizes or ())))
                if "ny18Toys" in prizes:
                    report.ok("the loot box carries decorations",
                              "%d toy(s) per the client's own reward window"
                              % len(prizes.get("ny18Toys") or ()))

        response, _, _ = session.command(4, cmd.CMD_REPAIR, (_first_vehicle(state), 0, 0))
        if response.get("code") != cmd.RES_SUCCESS:
            report.warn("a garage command (CMD_REPAIR) is not answered", repr(response))
        else:
            report.ok("a garage command (CMD_REPAIR) is answered", "")

        response, _, _ = session.command(5, 99999, (0, 0, 0))
        if response.get("code") == cmd.RES_SUCCESS:
            report.warn("an unknown command is answered with success",
                        "the client would believe it did something")
        else:
            report.ok("an unknown command is refused",
                      "the client shows %r" % (response.get("error"),))
    except OSError as error:
        report.fail("the session with the local server failed", str(error))
    except Exception as error:  # noqa: BLE001 - the report is the interface
        report.fail("the session with the local server failed",
                    "%s: %s" % (type(error).__name__, error))
    finally:
        if session is not None:
            session.close()
        server.stop()
    return report


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------
def run(client: Optional[str] = None, catalog=None, source: Optional[str] = None,
        stream=sys.stdout, session: bool = True, tankmen_configs=None) -> int:
    """Check an installation and a session; returns a process exit code."""
    root = source or client
    if root:
        installation = check_installation(root)
    else:
        installation = Report("No installation given")
        installation.warn("nothing was checked against an installation",
                          "pass --client <game folder> to check one")
    if catalog is None and root:
        try:
            catalog = loader.load_catalog(root)
        except Exception as error:  # noqa: BLE001 - reported above
            if installation.problems == 0:
                installation.fail("cannot read the game data", str(error))

    sessions = None
    if session and catalog is not None:
        if tankmen_configs is None and root:
            try:
                tankmen_configs = loader.load_tankmen_configs(root)
            except Exception:  # noqa: BLE001 - reported by check_installation
                tankmen_configs = {}
        sessions = check_session(catalog, tankmen_configs=tankmen_configs)

    installation.render(stream)
    problems, warnings = installation.problems, installation.warnings
    if sessions is not None:
        stream.write("\n")
        sessions.render(stream)
        problems += sessions.problems
        warnings += sessions.warnings
    stream.write("\n")
    if problems:
        stream.write("verify: %d problem(s), %d warning(s) - the game would not work yet\n"
                     % (problems, warnings))
        return 1
    if warnings:
        stream.write("verify: everything the local server needs is in place "
                     "(%d warning(s) to read)\n" % warnings)
    else:
        stream.write("verify: everything the local server needs is in place\n")
    return 0


__all__ = ["Report", "check_installation", "check_session", "find_hangar_space", "run"]
