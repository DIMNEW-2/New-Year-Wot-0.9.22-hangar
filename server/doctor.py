"""``python3 -m server verify`` - check everything a run of the game needs.

The doctor looks at the player's own installation, at the New Year data the
event is built from, and then starts the local server and talks to it exactly
like the client mod does.  Every line of its report says what was checked, and
the hints say what to do when a check failed:

* **the installation** - a layout the loader understands (packed ``scripts.pkg``
  or an unpacked ``item_defs`` tree), the catalogue it yields, the crew name
  lists, and whether the client ships the New Year hangar space
  (``spaces/h07_newyear_2015/space.settings``, looked for inside the packages),
* **the mod** - ``res_mods/0.9.22.0.1/scripts/client/gui/mods/mod_wotny_local.py``
  in the game folder; without it the client never talks to the local server,
* **the session** - handshake, account diff, streamed shop cache, opening a loot
  box, a garage command, and an unsupported command that has to be refused.

The report is the interface: it never raises, so a broken installation produces
a readable list of problems instead of a traceback.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import zlib
from typing import List, Optional, Sequence, Tuple

from . import loader
from .account_rpc import commands as cmd
from .account_rpc.server import PROTOCOL_VERSION, AccountServer
from .account_rpc.state import AccountState
from .game import dossier as dossier_module
from .game.defs import DefsError
from .proto import pack_message, py2_loads, read_frame, unpack_message

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARKS = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}

#: How many space names a report may print before it becomes a wall of text.
SPACES_SHOWN = 10

#: The hangar space a build without a detectable New Year space would fall back
#: to (``server/game/events.py::NEW_YEAR_HANGAR_PATH``); ``ClientHangarSpace``
#: resolves it through ``ResMgr.openSection(path + '/space.settings')``, which
#: searches the packages of the installation - so the space has to *be* there,
#: which is why the real one is detected (``server.loader.guess_new_year_space``).
HANGAR_SPACE = "spaces/h07_newyear_2015"

#: Where the mod has to be for the client to load it (``client_mod/README.md``).
MOD_RELATIVE_PATH = os.path.join("res_mods", "0.9.22.0.1", "scripts", "client",
                                 "gui", "mods", "mod_wotny_local.py")

#: Sections a full account diff has to carry: every lobby cache reads one of
#: them at login (``ItemsCache``, ``Stats``, ``EventsCache``, ``SettingsCache``,
#: ``ClientNewYear``).
DIFF_SECTIONS = ("inventory", "stats", "account", "economics", "tokens", "newYear",
                 "goodies", "serverSettings", "eventsData")

#: How many packages are opened while looking for the hangar space (a release
#: installation keeps its spaces in ``res/packages``).
ARCHIVES_TO_SCAN = 64


class Line(object):
    """One check of the report."""

    __slots__ = ("status", "title", "detail")

    def __init__(self, status: str, title: str, detail: str = ""):
        self.status = status
        self.title = title
        self.detail = detail or ""


class Report(object):
    """A titled list of checks with a renderer and a verdict."""

    def __init__(self, title: str = ""):
        self.title = title
        self.lines: List[Line] = []

    def add(self, status: str, title: str, detail: str = "") -> None:
        self.lines.append(Line(status, title, detail))

    def ok(self, title: str, detail: str = "") -> None:
        self.add(OK, title, detail)

    def warn(self, title: str, detail: str = "") -> None:
        self.add(WARN, title, detail)

    def fail(self, title: str, detail: str = "") -> None:
        self.add(FAIL, title, detail)

    def count(self, status: str) -> int:
        return len([line for line in self.lines if line.status == status])

    @property
    def failures(self) -> int:
        return self.count(FAIL)

    @property
    def warnings(self) -> int:
        return self.count(WARN)

    def title_of(self, needle: str) -> bool:
        return any(needle in line.title for line in self.lines)

    def render(self, stream=sys.stdout) -> None:
        if self.title:
            stream.write("%s\n" % self.title)
        for line in self.lines:
            stream.write("  %s %s\n" % (_MARKS[line.status], line.title))
            for extra in str(line.detail).splitlines():
                stream.write("         %s\n" % extra)


# ---------------------------------------------------------------------------
# the installation
# ---------------------------------------------------------------------------
def find_hangar_space(client_root: str, space: str = None) -> Tuple[bool, str]:
    """Look for the New Year hangar space inside the packages.

    ``space`` names one space to look for (``spaces/<name>/space.settings``,
    the form the client resolves); without it the spaces of
    :data:`server.loader.NEW_YEAR_SPACES` are tried in order and any name that
    mentions the event is accepted - a build that renamed its space still gets a
    hangar instead of a black screen.
    """
    _names, stats = loader.find_spaces(client_root)
    if not stats["archives"]:
        return False, "the installation has no res/packages to look in"
    if space:
        wanted = os.path.basename(space).lower()
        for name in loader.find_spaces(client_root)[0]:
            if name.lower() == wanted:
                return True, "spaces/%s is in the packages" % name
        return False, "looked for spaces/%s in %d package(s)" % (wanted, stats["archives"])
    found, spaces, stats = loader.guess_new_year_space(client_root)
    if found:
        return True, ("spaces/%s/space.settings is in the packages (this build "
                      "ships %d space(s))" % (found, len(spaces)))
    if spaces:
        return False, ("%d space(s) found, none of them a New Year one: %s\n"
                       "pass --hangar <name> to pick one of them"
                       % (len(spaces), ", ".join(spaces[:10])))
    return False, ("no space at all in %d package(s) (%d readable) - is this the "
                   "installation the game runs from?"
                   % (stats["archives"], stats["readable"]))


def check_installation(client_root: str, catalog=None, tankmen_configs=None) -> Report:
    """Everything the installation has to give the server."""
    report = Report("The installation")
    try:
        layout = loader.find_client(client_root)
    except loader.ClientNotFound as error:
        report.fail("the installation is unreadable",
                    "%s\n%s" % (error, loader.describe_installation(client_root)))
        return report
    report.ok("the game data was found", loader.source_description(client_root))

    if catalog is None:
        try:
            catalog = loader.load_catalog(client_root)
            catalog.validate()
        except (DefsError, ValueError) as error:
            report.fail("the game data is incomplete", str(error))
            catalog = None
    if catalog is not None:
        ny = catalog.new_year
        detail = ("%d vehicles, %d decorations, %d slots, %d boxes, %d chests"
                  % (len(catalog.vehicles.all()), len(ny.toys), len(ny.slots),
                     len(ny.boxes), len(ny.chests)))
        if ny.toys and ny.boxes and ny.chests and ny.collection_rewards:
            report.ok("the New Year definitions are complete", detail)
        else:
            report.fail("the New Year definitions are empty", detail)

    if tankmen_configs is None:
        reasons: List[str] = []
        try:
            tankmen_configs = loader.load_tankmen_configs(client_root, errors=reasons)
        except Exception as error:  # noqa: BLE001 - the data decides
            tankmen_configs = {}
            reasons.append(str(error))
        reasons_holder = reasons
    else:
        reasons_holder = []
    if tankmen_configs:
        report.ok("the crew name lists are there",
                  "%d nations (crew members and the New Year tankwomen)"
                  % len(tankmen_configs))
    else:
        report.warn("no crew name lists",
                    "crew members and the New Year tankwomen would be missing;\n"
                    "looked in %s" % loader.source_description(client_root)
                    + ("\nhere is why:\n  " + "\n  ".join(reasons_holder)
                       if reasons_holder else ""))

    found, detail = find_hangar_space(layout.root)
    if found:
        report.ok("the New Year hangar space is in the packages", detail)
    else:
        report.warn("the New Year hangar space was not found",
                    "%s - the hangar would stay the stock one.\n"
                    "Pass --hangar <name> to use another space of this build (the "
                    "client's\nown gui/hangars.xml lists them)." % detail)

    # The session below builds the very account the game gets, so the space the
    # notification will name is part of the report.
    space, spaces, _stats = loader.guess_new_year_space(layout.root)
    if space:
        report.ok("the event will use the hangar space of this build",
                  "spaces/%s of %d space(s)" % (space, len(spaces)))

    mod = os.path.join(layout.root, MOD_RELATIVE_PATH)
    if os.path.isfile(mod):
        report.ok("the client mod is installed", mod)
    else:
        report.warn("the client mod is not installed yet",
                    "without it the client never reaches the local server.  Copy:\n"
                    "  %s\n"
                    "into %s"
                    % (_client_mod_path(), os.path.join(layout.root, "res_mods",
                                                        "0.9.22.0.1", "scripts")))
    return report


def _client_mod_path() -> str:
    """``<repo>/client_mod/res_mods/0.9.22.0.1/scripts`` of this checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "client_mod", "res_mods", "0.9.22.0.1",
                        "scripts")


# ---------------------------------------------------------------------------
# the session the mod has
# ---------------------------------------------------------------------------
class Client(object):
    """The smallest client there is: the framing of ``server.proto`` over TCP."""

    def __init__(self, port: int, host: str = "127.0.0.1", timeout: float = 60.0):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.reader = self.socket.makefile("rb")
        self.writer = self.socket.makefile("wb")

    def send(self, message) -> None:
        self.writer.write(pack_message(message))
        self.writer.flush()

    def recv(self) -> dict:
        return unpack_message(read_frame(self.reader))

    def hello(self, client: str = "verify") -> dict:
        """``hello`` -> ``welcome``; anything else is a protocol error."""
        self.send({"type": "hello", "protocol": PROTOCOL_VERSION, "client": client})
        message = self.recv()
        if message.get("type") != "welcome":
            raise RuntimeError("the server did not answer the handshake: %r" % (message,))
        return message

    def command(self, request_id: int, opcode: int, args: Sequence = ()) -> Tuple[dict, List[dict], List[dict]]:
        """Send one ``_doCmd*``; returns ``(response, diffs, sysMessages, streams)``."""
        self.send({"type": "cmd", "requestID": request_id, "code": opcode,
                   "args": tuple(args)})
        response: dict = {}
        diffs: List[dict] = []
        messages: List[dict] = []
        streams: List[dict] = []
        while True:
            message = self.recv()
            kind = message.get("type")
            if kind == "diff":
                diffs.append(message.get("diff") or {})
            elif kind == "sysMessage":
                messages.append(message.get("action") or {})
            elif kind == "stream":
                streams.append(message)
            elif kind == "response":
                response = message
                return response, diffs, messages, streams
            else:
                raise RuntimeError("unexpected message %r" % (message,))

    def close(self) -> None:
        try:
            self.send({"type": "bye"})
        except OSError:
            pass
        for part in (self.writer, self.reader, self.socket):
            try:
                part.close()
            except OSError:
                pass


def _sections(diff) -> set:
    """The section names of a diff (``('eventsData', '_r')`` -> ``eventsData``)."""
    names = set()
    for key in diff or {}:
        if isinstance(key, tuple):
            names.add(key[0])
        elif key not in ("rev", "prevRev"):
            names.add(key)
    return names


def _first_box(state: AccountState) -> Optional[str]:
    for box, count in sorted(state.new_year.state.boxes.items()):
        if count:
            return box
    return None


def check_session(catalog, tankmen_configs=None, stream=None, hangar_path=None) -> Report:
    """Start the server on a free port and drive it like the client does."""
    report = Report("A session with the local server")
    state = AccountState.new(catalog, tankmen_configs=tankmen_configs or {},
                             hangar_path=hangar_path)
    try:
        server = AccountServer(state, host="127.0.0.1", port=0, save_path=None,
                               log=logging.getLogger("server.verify")).start()
    except OSError as error:
        report.fail("the local server does not start", str(error))
        return report

    client = None
    try:
        client = Client(server.port)
        welcome = client.hello()
        account = welcome.get("account") or {}
        diff = welcome.get("diff") or {}
        if account.get("name") and _sections(diff):
            report.ok("the handshake answers",
                      "account %r, protocol %s, revision %s"
                      % (account.get("name"), welcome.get("protocol"), diff.get("rev")))
        else:
            report.fail("the handshake carries no account", repr(welcome))

        response, diffs, _, _ = client.command(1, cmd.CMD_SYNC_DATA, (0, 0, 0, 0, 0))
        # ``AccountSyncData.__onSyncResponse`` reads the diff from the ``ext`` of
        # the answer (the bridge unpacks the pickle for it), so that is where the
        # snapshot has to be.
        ext = response.get("ext")
        body = py2_loads(ext) if ext else (diffs[-1] if diffs else {})
        missing = [name for name in DIFF_SECTIONS if name not in _sections(body)]
        if response.get("code") != cmd.RES_SUCCESS:
            report.fail("CMD_SYNC_DATA is not answered", repr(response))
        elif missing:
            report.fail("the account diff is incomplete",
                        "missing: %s" % ", ".join(missing))
        else:
            report.ok("the account diff is complete",
                      "%d sections, %d vehicles, %d crew members"
                      % (len(_sections(body)), len(state.vehicles), len(state.tankmen)))

        response, _, _, streams = client.command(2, cmd.CMD_SYNC_SHOP, (0, 0, 0))
        frame = streams[0] if streams else client.recv()
        if response.get("code") != cmd.RES_STREAM or frame.get("type") != "stream":
            report.fail("the price cache (CMD_SYNC_SHOP) is not streamed",
                        "answer %r, next frame %r" % (response, frame))
        else:
            shop = py2_loads(zlib.decompress(frame.get("data")))
            prices = ((shop.get("items") or {}).get("itemPrices")) or {}
            if len(prices) > 100 and "sellPriceFactor" in shop:
                report.ok("the price cache is streamed",
                          "%d prices, sell factor %s, exchange rate %s, premium from "
                          "%d gold" % (len(prices), shop.get("sellPriceFactor"),
                                       shop.get("exchangeRate"),
                                       min((shop.get("premiumCost") or {0: 0}).values())))
            else:
                report.fail("the price cache is empty", "%d prices" % len(prices))
            # The parts of the cache that come from the client's own sources
            # (the record table of the dossiers, the appearance factors, the
            # personal offers built from the catalogue) are the ones a wrong or
            # packed installation can leave empty - worth a line of their own.
            items = shop.get("items") or {}
            offers = ((shop.get("goodies") or {}).get("goodies")) or {}
            reward = shop.get("achievementsReward") or {}
            appearance = sum(len(table) for table in (items.get("camouflagePriceFactors") or ()))
            report.ok("the shop tables of the installation are read",
                      "%d personal offers, %s, %d priced camouflages, %d not-in-shop items"
                      % (len(offers),
                         "%d medal rewards" % len(reward.get("medals") or {})
                         if reward.get("isEnabled") else "no medal rewards",
                         appearance, len(items.get("notInShopItems") or ())))

        box = _first_box(state)
        if box is None:
            report.warn("no loot box to open", "the event state holds no boxes")
        else:
            response, diffs, messages, _ = client.command(
                3, cmd.CMD_NEW_YEAR_OPEN_BOX, (box,))
            payload = (messages[0].get("data") or {}) if messages else {}
            bonus = payload.get("data") or {}
            if response.get("code") != cmd.RES_SUCCESS:
                report.fail("a loot box is not opened", repr(response))
            elif payload.get("type") != cmd.SYS_MESSAGE_TYPE_TOKEN_QUESTS:
                report.fail("the loot box reports no prizes",
                            "system message: %r" % (payload,))
            else:
                report.ok("a loot box opens and reports its prizes",
                          "%s: %s" % (box, ", ".join(sorted(bonus))))
                if bonus.get("ny18Toys"):
                    report.ok("the loot box hands out decorations",
                              "%d decoration(s)" % len(bonus["ny18Toys"]))
                if not diffs:
                    report.fail("opening a loot box does not update the account",
                                "no diff was pushed")
                else:
                    report.ok("the loot box updates the account",
                              "%d diff(s), %s"
                              % (len(diffs), ", ".join(sorted(_sections(diffs[-1])))))

        entries = sorted(state.vehicles.values(), key=lambda entry: entry.inv_id)
        vehicle = entries[0].inv_id if entries else 0
        response, _, _, _ = client.command(4, cmd.CMD_REPAIR, (vehicle, 0, 0))
        if response.get("code") == cmd.RES_SUCCESS:
            report.ok("a garage command is answered", "CMD_REPAIR on vehicle %s" % vehicle)
        else:
            report.warn("a garage command is not answered", repr(response))

        response, _, _, _ = client.command(5, cmd.CMD_SYNC_DATA, (0, 0, 0, 0, 0))
        if response.get("code") == cmd.RES_SUCCESS:
            report.ok("a second sync is answered", "revision %s" % state.revision)
        else:
            report.fail("a second sync is not answered", repr(response))

        response, _, _, streams = client.command(7, cmd.CMD_SYNC_DOSSIERS, (3, 0, 0))
        frame = streams[0] if streams else client.recv()
        if response.get("code") != cmd.RES_STREAM or frame.get("type") != "stream":
            report.fail("the dossier cache (CMD_SYNC_DOSSIERS) is not streamed",
                        "answer %r, next frame %r" % (response, frame))
        else:
            version, dossiers = py2_loads(zlib.decompress(frame.get("data")))
            if version == 3 and not dossiers:
                report.ok("the dossier cache is streamed",
                          "version %s, %d descriptor(s)" % (version, len(dossiers)))
            else:
                report.fail("the dossier cache answers with something else",
                            "version %r, %r" % (version, dossiers))

        response, _, _, _ = client.command(8, cmd.CMD_REQ_ACCOUNT_DOSSIER, (1, 0, 0))
        ext = py2_loads(response.get("ext")) if response.get("ext") else {}
        dossier = ext.get("dossier")
        if response.get("code") != cmd.RES_SUCCESS or not dossier:
            report.fail("the account dossier is not answered", repr(response))
        else:
            try:
                version, blocks = dossier_module.parse(dossier)
            except dossier_module.DossierError as error:
                report.fail("the account dossier cannot be read", str(error))
            else:
                # The client's ``DossierBuilder.build`` unpacks the dossier's own
                # *version* out of the descriptor, so the answer has to be a whole
                # dossier of the version the profile screens expect (an empty one
                # made the client build a descriptor of its own).
                report.ok("the account dossier is answered",
                          "version %s, %d block(s), %d bytes"
                          % (version, len(blocks), len(dossier)))

        badges = sorted(getattr(catalog, "badges", None) or ())
        if not badges:
            report.warn("no badge to select", "the installation describes no badges")
        else:
            response, diffs, _, _ = client.command(9, cmd.CMD_SELECT_BADGES, ([badges[0]],))
            selected = (diffs[-1].get("badges") if diffs else None)
            if response.get("code") == cmd.RES_SUCCESS and selected == [badges[0]]:
                report.ok("a badge of the profile is selected",
                          "badge %s, the diff publishes it" % badges[0])
            else:
                report.fail("selecting a badge does not publish it",
                            "answer %r, diff %r" % (response, diffs[-1:] or None))

        response, _, _, _ = client.command(6, 99999, (0, 0, 0))
        if response.get("code") == cmd.RES_SUCCESS:
            report.warn("an unsupported command is answered with success",
                        "the client would believe it did something")
        else:
            report.ok("an unsupported command is refused, not faked",
                      "the client shows %r" % (response.get("error"),))
    except (OSError, RuntimeError) as error:
        report.fail("the session with the local server failed", str(error))
    finally:
        if client is not None:
            client.close()
        server.stop()
    return report


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------
def run(client: Optional[str] = None, catalog=None, source: Optional[str] = None,
        stream=sys.stdout, session: bool = True, tankmen_configs=None,
        garage_mode: str = "researchable") -> int:
    """Check an installation (when given) and a session; the process exit code."""
    root = source or client
    if root:
        installation = check_installation(root, catalog=catalog,
                                          tankmen_configs=tankmen_configs)
    else:
        installation = Report("The installation")
        installation.warn("no installation was given",
                          "pass --client <game folder> to also check the game data")

    if catalog is None and root:
        try:
            catalog = loader.load_catalog(root)
        except Exception as error:  # noqa: BLE001 - reported by check_installation
            if not installation.failures:
                installation.fail("the game data cannot be read", str(error))

    session_report = None
    if session:
        if catalog is None:
            session_report = Report("A session with the local server")
            session_report.warn("the session was skipped",
                                "there is no catalogue to build an account from")
        else:
            if tankmen_configs is None and root:
                try:
                    tankmen_configs = loader.load_tankmen_configs(root)
                except Exception:  # noqa: BLE001 - reported by the installation check
                    tankmen_configs = {}
            # The space the event switches to is the one this client ships.
            hangar_path = None
            if root:
                try:
                    hangar_path = loader.guess_new_year_space(root)[0]
                except OSError:
                    hangar_path = None
            session_report = check_session(catalog, tankmen_configs=tankmen_configs,
                                           stream=stream, hangar_path=hangar_path)

    installation.render(stream)
    failures, warnings = installation.failures, installation.warnings
    if session_report is not None:
        stream.write("\n")
        session_report.render(stream)
        failures += session_report.failures
        warnings += session_report.warnings
    stream.write("\n")
    if failures:
        stream.write("verify: %d problem(s) and %d warning(s) - the game would not "
                     "work yet\n" % (failures, warnings))
        return 1
    if warnings:
        stream.write("verify: the local server is ready (%d warning(s) to read)\n"
                     % warnings)
    else:
        stream.write("verify: the local server is ready\n")
    return 0


__all__ = ["Client", "Line", "Report", "check_installation", "check_session",
           "find_hangar_space", "run"]
