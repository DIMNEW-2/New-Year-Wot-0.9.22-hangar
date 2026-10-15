"""Shared helpers for the test suite.

The suite is a set of plain Python 3 scripts (the game client is Python 2.7 and
there is no ``pytest`` in a World of Tanks installation), so every test file
defines ``test_*`` functions and runs them through :class:`Runner`.  Run them
all at once with::

    python3 tests/run_all.py --client /path/to/World_of_Tanks

The tests never bundle game data: they read the player's own ``item_defs`` the
same way the server does.  When no installation is available the tests that
need one are reported as skipped instead of failing.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server import loader  # noqa: E402  (needs the path above)

#: Environment variables that may point at the installation (see
#: :func:`server.loader.candidate_client_roots`).
CLIENT_ENV_VARS = ("WOTNY_CLIENT", "WOT_CLIENT_DIR", "WOT_DIR")

_SKIP = object()


class Skip(Exception):
    """Raised by a test that cannot run in this environment."""


def add_client_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--client", help="path to the World of Tanks installation")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every check")


def find_client(explicit: str = None) -> str:
    """The installation the tests run against, or ``None``."""
    if explicit:
        return explicit
    for variable in CLIENT_ENV_VARS:
        value = os.environ.get(variable)
        if value:
            return value
    return loader.guess_client()


def require_client(explicit: str = None) -> str:
    root = find_client(explicit)
    if not root:
        raise Skip("no World of Tanks installation found (pass --client or set %s)"
                   % CLIENT_ENV_VARS[0])
    return root


def load_game(explicit: str = None):
    """``(catalog, tankmen_configs)`` of the installation under test."""
    root = require_client(explicit)
    catalog = loader.load_catalog(root)
    try:
        tankmen = loader.load_tankmen_configs(root)
    except Exception:  # pragma: no cover - the crew is optional for most tests
        tankmen = {}
    return catalog, tankmen


def new_account(explicit: str = None, **kwargs):
    """A ready-to-play :class:`server.account_rpc.state.AccountState`."""
    from server.account_rpc.state import AccountState

    catalog, tankmen = load_game(explicit)
    return AccountState.new(catalog, tankmen_configs=tankmen, **kwargs)


class Runner(object):
    """Collects the outcome of the ``test_*`` functions of one module."""

    def __init__(self, name: str, client: str = None, verbose: bool = False):
        self.name = name
        self.client = client
        self.verbose = verbose
        self.passed = 0
        self.skipped = 0
        self.failures = []

    def run(self, function) -> None:
        label = function.__name__
        try:
            function(self)
        except Skip as error:
            self.skipped += 1
            print("  SKIP %s: %s" % (label, error))
        except Exception:
            self.failures.append((label, traceback.format_exc()))
            print("  FAIL %s" % (label,))
            print(traceback.format_exc().rstrip())
        else:
            self.passed += 1
            print("  ok   %s" % (label,))

    def report(self) -> int:
        print("%s: %d passed, %d skipped, %d failed"
              % (self.name, self.passed, self.skipped, len(self.failures)))
        return 1 if self.failures else 0

    # -- small helpers used by the tests themselves ----------------------
    def check(self, condition, message: str) -> None:
        if not condition:
            raise AssertionError(message)
        if self.verbose:
            print("       - %s" % (message,))

    def equal(self, actual, expected, message: str) -> None:
        if actual != expected:
            raise AssertionError("%s: expected %r, got %r" % (message, expected, actual))
        if self.verbose:
            print("       - %s == %r" % (message, actual))


def main(module, run_names=None, argv=None) -> int:
    """Entry point of a single test file."""
    parser = argparse.ArgumentParser(description=module.__doc__)
    add_client_argument(parser)
    args = parser.parse_args(argv)

    runner = Runner(os.path.basename(module.__file__), client=args.client, verbose=args.verbose)
    names = run_names or sorted(name for name in dir(module) if name.startswith("test_"))
    for name in names:
        runner.run(getattr(module, name))
    return runner.report()


__all__ = [
    "CLIENT_ENV_VARS",
    "REPO_ROOT",
    "Runner",
    "Skip",
    "add_client_argument",
    "find_client",
    "load_game",
    "main",
    "new_account",
    "require_client",
]
