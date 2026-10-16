"""``python3 -m server`` - run the local World of Tanks 0.9.22 server.

Usage::

    python3 -m server --client /path/to/World_of_Tanks
    python3 -m server verify --client /path/to/World_of_Tanks
    python3 -m server --catalog catalog.json --save server/data/account.json
    python3 -m server info --client /path/to/World_of_Tanks

The server reads the player's own installation (no game data is bundled, see
:mod:`server.loader`), prepares a local account with a garage and the New Year
2018 event switched on, and then waits for the in-client bridge (the mod under
``client_mod/``) to connect on ``127.0.0.1``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import loader
from .account_rpc.server import DEFAULT_HOST, DEFAULT_PORT, AccountServer
from .game.defs import DefsError
from .account_rpc.state import AccountState

DEFAULT_SAVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "account.json")
DEFAULT_CATALOG_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "catalog.json")


def _configure_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m server",
        description="Local server for World of Tanks 0.9.22 (New Year hangar, loot boxes, "
                    "New Year offensive)",
    )
    parser.add_argument("--client", help="path to the World of Tanks installation")
    parser.add_argument("--catalog", help="path to catalog.json (tools/extract_client_catalog.py)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port (default: %(default)s)")
    parser.add_argument("--save", default=DEFAULT_SAVE, help="account file (default: %(default)s)")
    parser.add_argument("--fresh", action="store_true", help="ignore the saved account and start over")
    parser.add_argument("--hangar", help="hangar space the event switches to "
                                         "(default: the one the installation ships)")
    parser.add_argument("--no-announce", dest="announce", action="store_false",
                        help="do not announce the event in the client's service channel "
                             "(the New Year hangar notification is still sent)")
    parser.add_argument("--data", default=DEFAULT_CATALOG_CACHE,
                        help="account file (default: %(default)s)")
    parser.add_argument("--free-xp", type=int, default=None, help="override the starting free XP")
    parser.add_argument("--credits", type=int, default=None, help="override the starting credits")
    parser.add_argument("--gold", type=int, default=None, help="override the starting gold")
    parser.add_argument("--only-nation", action="append", default=None,
                        help="fill the garage with one nation only (repeatable)")
    parser.add_argument("--garage", default="researchable", choices=("researchable", "premium", "all"),
                        help="what the starting garage contains (default: %(default)s)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=("serve", "verify", "info", "extract", "inspect"),
                        help="what to do (default: serve)")
    return parser


def _resolve_source(args) -> str:
    if args.catalog:
        return args.catalog
    if args.client:
        return args.client
    guess = loader.guess_client()
    if guess:
        logging.info("using the installation found at %s", guess)
        return guess
    raise SystemExit(
        "no World of Tanks installation found; pass --client /path/to/World_of_Tanks "
        "or --catalog catalog.json"
    )


def _load_catalog(args, source: str):
    logging.info("reading game data from %s", loader.source_description(source))
    try:
        catalog = loader.load_catalog(source)
    except loader.ClientNotFound as error:
        # A wrong or unsupported installation: explain the layout instead of
        # letting a traceback be the answer.
        raise SystemExit(
            "%s\n\n%s\n\nPass --client with the folder that holds WorldOfTanks.exe "
            "(or --catalog for a catalog.json export).\n%s"
            % (error, loader.describe_installation(source),
               _installation_hint(source))
        )
    except DefsError as error:
        raise SystemExit("cannot read the game data of %s: %s" % (source, error))
    catalog.validate()
    logging.info(
        "catalogue: %d vehicles, %d decorations, %d boxes, %d chests",
        len(catalog.vehicles.all()), len(catalog.new_year.toys),
        len(catalog.new_year.boxes), len(catalog.new_year.chests),
    )
    return catalog


def _detected_hangar(args, source: str = None) -> str:
    """The New Year hangar space the client of this installation can load.

    ``--hangar`` wins, then what the packages say, then nothing (the built-in
    default of :data:`server.game.events.NEW_YEAR_HANGAR_PATH` stays in place).
    The space is *detected*, not assumed: a build that ships another name gets
    its own space instead of a black screen.
    """
    if getattr(args, "hangar", None):
        return args.hangar
    root = source or getattr(args, "client", None)
    if not root or not os.path.isdir(root):
        return None
    try:
        space, spaces, stats = loader.guess_new_year_space(root)
    except OSError as error:
        logging.warning("could not look for the hangar space: %s", error)
        return None
    if space:
        logging.info("the New Year hangar space of this installation is %s", space)
        return space
    if spaces:
        logging.warning(
            "no New Year hangar space among the %d space(s) of the installation (%s); "
            "the hangar would stay the stock one - pass --hangar <name>",
            len(spaces), ", ".join(spaces[:6]),
        )
    return None


def _installation_hint(source: str) -> str:
    """A short note about what the looked-for folders are called."""
    return ("Typical layouts:\n"
            "  <game>\\res\\scripts.pkg          (the release build, packed)\n"
            "  <game>\\res\\scripts\\item_defs   (an unpacked build)\n"
            "  <game>\\WorldOfTanks.exe")


def _build_account(args, catalog, source: str) -> AccountState:
    from .game import ny_constants as nyc

    tankmen_configs = {}
    if not os.path.isfile(source):
        try:
            tankmen_configs = loader.load_tankmen_configs(source)
        except Exception as error:  # pragma: no cover - data dependent
            logging.warning("tankman name lists unavailable: %s", error)
    else:
        client = loader.guess_client()
        if client:
            try:
                tankmen_configs = loader.load_tankmen_configs(client)
            except Exception as error:  # pragma: no cover
                logging.warning("tankman name lists unavailable: %s", error)

    overrides = {}
    if args.credits is not None:
        overrides["credits"] = args.credits
    if args.gold is not None:
        overrides["gold"] = args.gold
    if args.free_xp is not None:
        overrides["free_xp"] = args.free_xp

    if tankmen_configs:
        logging.info("tankman name lists loaded for %d nations", len(tankmen_configs))
    else:
        logging.warning("no tankman name lists: crew members and the New Year tankwoman "
                        "will be unavailable")

    state = AccountState.new(catalog, tankmen_configs=tankmen_configs,
                             garage_mode=getattr(args, "garage", "researchable"),
                             hangar_path=_detected_hangar(args, source), **overrides)
    state.events.service_messages = bool(getattr(args, "announce", True))
    nation_ids = None
    if args.only_nation:
        nation_ids = [nyc.NATION_INDICES[name] for name in args.only_nation if name in nyc.NATION_INDICES]
        state.vehicles.clear()
        state.tankmen.clear()
        state.unlocks.clear()
        state.elite_vehicles.clear()
    if nation_ids:
        state.grant_garage(nation_ids=nation_ids, mode=args.garage)
    return state


def _load_account(args, catalog, source: str) -> AccountState:
    if args.save and os.path.isfile(args.save) and not args.fresh:
        import json

        with open(args.save, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        tankman_factory = None
        if not os.path.isfile(source):
            try:
                configs = loader.load_tankmen_configs(source)
                if configs:
                    from .game import tankmen as tankmen_module

                    tankman_factory = tankmen_module.TankmanFactory(configs)
            except Exception as error:  # pragma: no cover
                logging.warning("tankman name lists unavailable: %s", error)
        state = AccountState.from_json(payload, catalog, tankman_factory=tankman_factory)
        # An account saved by an earlier run (or by another installation) keeps
        # the space *this* client ships - the saved one is only a fallback.
        detected = _detected_hangar(args, source)
        if detected:
            state.events.set_hangar_path(detected)
            state.hangar_path = detected
        logging.info(
            "account loaded from %s: %d vehicles, %d crew, level %d, %.0f credits",
            args.save, len(state.vehicles), len(state.tankmen),
            state.new_year.state.level, state.credits,
        )
        return state
    state = _build_account(args, catalog, source)
    logging.info(
        "new account: %d vehicles, %d crew, %d decorations in boxes",
        len(state.vehicles), len(state.tankmen), sum(state.new_year.state.boxes.values()),
    )
    return state


def _command_verify(args) -> int:
    """``python3 -m server verify`` - check the installation and a session."""
    from . import doctor

    source = args.client or args.catalog or loader.guess_client()
    if not source:
        print("no World of Tanks installation found; pass --client <game folder>")
        return 1
    code = doctor.run(catalog=None, source=source)
    if code == 0:
        print("")
        print("Start the server with:  python3 -m server serve --client %s" % source)
        print("Then start the game (the mod logs in by itself, see client_mod/README.md).")
    return code


def _command_info(args) -> int:
    source = _resolve_source(args)
    catalog = _load_catalog(args, source)
    ny = catalog.new_year
    print("source          : %s" % loader.source_description(source))
    print("vehicles        : %d" % len(catalog.vehicles.all()))
    print("decorations     : %d (in %d groups)" % (len(ny.toys), len(ny.toy_groups)))
    print("slots           : %d" % len(ny.slots))
    print("loot boxes      : %s" % ", ".join(sorted(ny.boxes)))
    print("chests          : %s" % ", ".join(sorted(ny.chests)))
    print("discount certs  : %s" % ", ".join(sorted(ny.variadic_discounts)))
    print("tankman certs   : %s" % ", ".join(sorted(ny.variadic_tankmen)))
    print("collection rew. : %s" % ", ".join(sorted(reward.id for reward in ny.collection_rewards)))
    return 0


def _command_inspect(args) -> int:
    """Show what the server sees when it is pointed at an installation."""
    target = args.client or args.catalog or loader.guess_client() or os.getcwd()
    report = loader.describe_installation(target)
    print(report)
    if "state           : ok" not in report:
        return 1
    try:
        catalog = loader.load_catalog(target)
        catalog.validate()
    except Exception as error:  # noqa: BLE001 - the report *is* the output
        print("catalogue       : unreadable")
        for line in str(error).splitlines():
            print("  %s" % line.strip())
        # A data file the reader could not parse is exactly the case this
        # command exists for: show what the packages hold.
        _report_packages(target)
        return 1
    print("catalogue       : %d vehicles, %d decorations, %d boxes, %d chests"
          % (len(catalog.vehicles.all()), len(catalog.new_year.toys),
             len(catalog.new_year.boxes), len(catalog.new_year.chests)))
    if getattr(args, "packages", False) or True:
        _report_packages(target)
    return 0


#: The files ``inspect`` sniffs: one of every reader the server has (the event,
#: the vehicle catalogue, the crew names, the dossier records).
PROBE_ENTRIES = (
    "item_defs/new_year/toys.xml",
    "item_defs/vehicles/china/list.xml",
    "item_defs/vehicles/common/customization.xml",
    "item_defs/tankmen/china.xml",
    "common/dossiers2/custom/records.py",
)


def _report_packages(target: str) -> None:
    """What the packages of an installation hold - the diagnostic for a build
    whose data the loader cannot read."""
    try:
        layout = loader.find_client(target)
    except loader.ClientNotFound:
        return
    spaces, stats = loader.find_spaces(layout.root)
    space, _spaces, _stats = loader.guess_new_year_space(layout.root, spaces=spaces)
    print("packages        : %d archive(s), %d readable, %d member(s)"
          % (stats["archives"], stats["readable"], stats["members"]))
    print("hangar spaces   : %s" % (", ".join(spaces[:12]) or "none found"))
    print("event space     : %s" % (space or "none found (the stock hangar would stay)"))
    if not stats["archives"]:
        return
    print("script package  : %s" % (layout.item_defs if os.path.isfile(layout.item_defs)
                                    else "unpacked"))
    try:
        from .game import defs as defs_module

        source = defs_module.open_defs(layout.item_defs)
    except Exception as error:  # noqa: BLE001 - the report is the output
        print("payloads        : cannot open (%s)" % error.splitlines()[0])
        return
    print("members         : %d" % len(source.members()))
    for rel_path in PROBE_ENTRIES:
        name = rel_path.split("/", 1)[1] if rel_path.startswith("item_defs/") \
            else rel_path
        if rel_path.startswith("item_defs/"):
            info = source.sniff(name)
        else:
            text = loader.client_script_reader(target)(rel_path)
            info = {"format": "text" if text else "missing", "size": len(text or ""),
                    "unwrapped": "", "encoding": "", "xml": False, "head": ""}
        if info is None:
            print("  %-34s missing" % rel_path)
            continue
        print("  %-34s %-20s %8d bytes  %s%s"
              % (rel_path, info["format"], info["size"],
                 info["encoding"], "  (unwrapped: %s)" % info["unwrapped"]
                 if info["unwrapped"] else ""))
        if not info["xml"] and not rel_path.endswith(".py"):
            print("    first bytes: %s" % info["head"])


def _command_extract(args) -> int:
    from tools import extract_client_catalog

    source = _resolve_source(args)
    target = args.data if args.data.endswith(".json") else DEFAULT_CATALOG_CACHE
    extract_client_catalog.extract(source, target, include_vehicles=True)
    print("written %s" % target)
    return 0


def _command_serve(args) -> int:
    source = _resolve_source(args)
    catalog = _load_catalog(args, source)
    state = _load_account(args, catalog, source)
    server = AccountServer(state, host=args.host, port=args.port, save_path=args.save)
    server.start()
    print("")
    print("Local World of Tanks 0.9.22 server is up.")
    print("  game data : %s" % loader.source_description(source))
    print("  bridge    : %s:%d (the client mod connects here)" % (server.host, server.port))
    print("  account   : %s" % (args.save or "<not saved>"))
    print("  garage    : %d vehicles, %d crew" % (len(state.vehicles), len(state.tankmen)))
    print("  hangar    : spaces/%s" % state.events.hangar_path)
    print("  event     : Новогоднее наступление, level %d, %d boxes, %d chests" % (
        state.new_year.state.level,
        sum(state.new_year.state.boxes.values()),
        sum(state.new_year.state.chests.values()),
    ))
    print("")
    print("Press Ctrl+C to stop (the account is saved on exit).")
    server.serve_forever()
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    if args.command == "inspect":
        return _command_inspect(args)
    if args.command == "verify":
        return _command_verify(args)
    if args.command == "info":
        return _command_info(args)
    if args.command == "extract":
        return _command_extract(args)
    return _command_serve(args)


if __name__ == "__main__":
    sys.exit(main())
