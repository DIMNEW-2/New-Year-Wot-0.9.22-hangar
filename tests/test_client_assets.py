"""The mirrored New Year tables against the client's own sources.

``server/game/ny_constants.py`` mirrors the rules the client renders the event
with (crafting prices, atmosphere levels, collection bonuses).  A wrong number
there means the interface shows one price and the server charges another, which
only a running client could reveal - so the tables are compared with the
client's ``common/items/new_year_types.py`` instead, with the checker the
project ships (``tools/check_client_assets.py``).

The client file is Python 2.  Its tables are literals, so they can be read and
evaluated without a Python 2 interpreter; the tests below also prove that the
checker actually notices a disagreement.
"""

from __future__ import annotations

import ast
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import common  # noqa: E402
import check_client_assets as checker  # noqa: E402
from server import loader  # noqa: E402
from server.game import ny_constants as nyc  # noqa: E402


def _scripts_source(runner: common.Runner):
    """The client's ``scripts`` folder, or ``None`` when it is not unpacked."""
    layout = loader.find_client(runner.client)
    folder = layout.scripts
    if not folder or not os.path.isdir(folder):
        raise common.Skip("the installation ships its scripts in an archive: "
                         "unpack res/scripts to compare the tables")
    if not os.path.isfile(os.path.join(folder, checker.NEW_YEAR_TYPES)):
        raise common.Skip("%s has no %s" % (folder, checker.NEW_YEAR_TYPES))
    return folder


# ---------------------------------------------------------------------------
# the tables themselves
# ---------------------------------------------------------------------------
def test_mirrored_tables_agree_with_the_client(runner: common.Runner) -> None:
    """Every mirrored table equals what the client's own source computes."""
    folder = _scripts_source(runner)
    output = io.StringIO()
    status = checker.check(folder, stream=output)
    report = output.getvalue()
    runner.equal(status, 0, "the checker is green:\n%s" % report)
    runner.check("all mirrored tables agree" in report, "the summary line is printed")


def test_the_nation_table_is_the_clients(runner: common.Runner) -> None:
    """The event keys its settings by nation index, so the order must match."""
    folder = _scripts_source(runner)
    source = checker.read_scripts_text(loader.find_client(folder), checker.NATIONS)
    runner.check(source is not None, "the client's nations module is readable")
    tables = checker.evaluate_client_module(source, checker.NATIONS)
    runner.equal(tuple(nyc.NATIONS), tuple(tables["NAMES"]), "the nation order matches")
    runner.equal(dict(nyc.NATION_INDICES), dict(tables["INDICES"]), "the indices match")


def test_every_craft_price_is_a_literal_of_the_client(runner: common.Runner) -> None:
    """The crafting tables of a rank are the client's own (a smell test for the mirror)."""
    folder = _scripts_source(runner)
    source = checker.read_scripts_text(loader.find_client(folder), checker.NEW_YEAR_TYPES)
    tables = checker.evaluate_client_module(source, checker.NEW_YEAR_TYPES)
    craft = tables["CRAFT"]
    runner.equal(nyc.CRAFT.FRAGMENTS_BY_RANK, tuple(craft["FRAGMENTS_BY_RANK"]),
                 "the fragments of a broken toy")
    runner.equal(nyc.CRAFT.COST_BY_RANK, tuple(craft["COST_BY_RANK"]),
                 "what it costs to craft a toy of a rank")
    runner.equal(nyc.CRAFT.RANDOM_TOY_COST, craft["RANDOM_TOY_COST"],
                 "the price of a random toy")


# ---------------------------------------------------------------------------
# the checker itself
# ---------------------------------------------------------------------------
def test_the_checker_notices_a_wrong_table(runner: common.Runner) -> None:
    """A single wrong number is reported as a failure (the check has teeth)."""
    folder = _scripts_source(runner)
    original = nyc.BOUND_ATMOSPHERE_BY_LEVEL
    nyc.BOUND_ATMOSPHERE_BY_LEVEL = tuple(original[:-1]) + (original[-1] + 1,)
    output = io.StringIO()
    try:
        status = checker.check(folder, stream=output)
    finally:
        nyc.BOUND_ATMOSPHERE_BY_LEVEL = original
    report = output.getvalue()
    runner.equal(status, 1, "the checker fails:\n%s" % report)
    runner.check("FAIL  BOUND_ATMOSPHERE_BY_LEVEL" in report, "and names the table")
    runner.check("disagreement" in report, "and counts the disagreement")


def test_a_missing_client_source_is_a_failure(runner: common.Runner) -> None:
    """An installation without the client's sources is reported, not ignored."""
    root = tempfile.mkdtemp(prefix="wotny-assets-")
    try:
        os.makedirs(os.path.join(root, "res", "scripts", "item_defs", "vehicles"))
        os.makedirs(os.path.join(root, "res", "scripts", "item_defs", "new_year"))
        with open(os.path.join(root, "res", "scripts", "item_defs", "vehicles", "list.xml"),
                  "w") as handle:
            handle.write("<root/>")
        os.makedirs(os.path.join(root, "res", "scripts", "common", "items"))
        with open(os.path.join(root, "res", "scripts", "common", "nations.py"), "w") as handle:
            handle.write("NAMES = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', "
                         "'japan', 'czech', 'sweden', 'poland')\n"
                         "INDICES = dict(((n, i) for i, n in enumerate(NAMES)))\n")
        output = io.StringIO()
        status = checker.check(root, stream=output)
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    report = output.getvalue()
    runner.equal(status, 1, "the missing file fails the run:\n%s" % report)
    runner.check(checker.NEW_YEAR_TYPES in report, "and is named")


def test_an_unevaluable_table_says_why(runner: common.Runner) -> None:
    """A table that needs the nation module is explained instead of blamed."""
    note = checker._note_for_missing("SETTING_BY_NATION_ID", None)
    runner.check("nations.py" in note, "the note names the file: %s" % note)
    note = checker._note_for_missing("NATIONAL_SETTINGS", {"NAMES": ()})
    runner.check("no SETTING_BY_NATION_ID" in note or "has no" in note,
                 "otherwise the client file is blamed: %s" % note)


# ---------------------------------------------------------------------------
# the mod's own sources
# ---------------------------------------------------------------------------
def _client_mod_root() -> str:
    """``client_mod`` of this repository."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "client_mod")


def _mod_root() -> str:
    """``client_mod/res_mods/<version>/scripts/client/gui/mods`` of this repo."""
    return os.path.join(_client_mod_root(), "res_mods", "0.9.22.0.1", "scripts",
                        "client", "gui", "mods")


def _parsed(path: str):
    with open(path) as handle:
        return ast.parse(handle.read())


def _names_of(node) -> set:
    """Every ``Name``/``Attribute`` the node mentions (a cheap reference scan)."""
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def test_the_mod_answers_the_routine_base_calls_quietly(runner: common.Runner) -> None:
    """``_IGNORED_CALLS`` is what the bridge's ``__getattr__`` treats as routine.

    The client's own lobby talks to its ``base`` all the time - the chat channel
    asks for the last service messages (``ClientChat.requestLastSysMessages`` ->
    ``base.chatCommandFromClient``), the connection manager keeps the entity
    alive and the client pushes its properties.  The mod answers every one of
    them with a no-op and only ``WOTNY_VERBOSE=1`` writes a line for them, so a
    ``wotny.log`` a user reports stays readable.  The mod runs in the client's
    Python 2, so the file is parsed, not imported.
    """
    proxy = os.path.join(_mod_root(), "wotny", "account_proxy.py")
    tree = _parsed(proxy)
    ignored = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", None) == "_IGNORED_CALLS" for target in node.targets):
            ignored = ast.literal_eval(node.value)
            break
    runner.check(ignored, "the mod names the calls it ignores on purpose")
    runner.check("chatCommandFromClient" in ignored,
                 "the chat commands of the lobby are among them: %r" % (ignored,))
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"]
    runner.equal(len(functions), 1, "the base mailbox has exactly one ``__getattr__``")
    runner.check("_IGNORED_CALLS" in _names_of(functions[0]),
                 "and it consults the list instead of logging every call")

    log_source = open(os.path.join(_mod_root(), "wotny", "log.py")).read()
    log_tree = ast.parse(log_source)
    defined = {node.name for node in ast.walk(log_tree)
               if isinstance(node, ast.FunctionDef)}
    runner.check("debug" in defined, "the logger has a level for those calls")
    runner.check("config.VERBOSE" in log_source,
                 "and writes only when the mod is asked to be verbose")
    config_tree = _parsed(os.path.join(_mod_root(), "wotny", "config.py"))
    runner.check("VERBOSE" in _names_of(config_tree), "the flag is a config value")

    # Every file of the package is Python 2 that a Python 2 client parses: the
    # sources ship as sources (the release client would otherwise ignore them).
    package = os.path.join(_mod_root(), "wotny")
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py"):
            continue
        runner.check(bool(_parsed(os.path.join(package, name))),
                     "%s parses" % name)

    # The environment table of the mod's README is the list of variables the
    # config reads - a variable a user is told to set has to exist.
    variables = {node.value for node in ast.walk(config_tree)
                 if isinstance(node, ast.Constant)
                 and isinstance(node.value, str) and node.value.startswith("WOTNY_")}
    runner.check(variables, "the config reads the environment")
    with open(os.path.join(_client_mod_root(), "README.md")) as handle:
        readme = handle.read()
    documented = {line.split("`")[1] for line in readme.splitlines()
                  if line.startswith("| `WOTNY_")}
    runner.equal(sorted(documented), sorted(variables),
                 "the documented variables are the ones the config reads")


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
