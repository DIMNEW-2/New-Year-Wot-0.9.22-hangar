"""Run every test file of the suite against one World of Tanks installation.

The client is Python 2.7 and has no ``pytest``, so the tests are plain scripts
(see :mod:`tests.common`); this runner just calls each of them with the same
arguments and adds up their results::

    python3 tests/run_all.py --client /path/to/World_of_Tanks

Every file is executed in its own interpreter on purpose - the socket tests
start and stop servers, and a fresh process keeps their ports, loggers and
temporary files apart.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

#: Files that are helpers rather than tests.
NOT_TESTS = ("common.py", "run_all.py")

_SUMMARY = re.compile(r"^(\S+): (\d+) passed, (\d+) skipped, (\d+) failed$", re.MULTILINE)


def test_files() -> list:
    return sorted(
        name
        for name in os.listdir(HERE)
        if name.startswith("test_") and name.endswith(".py") and name not in NOT_TESTS
    )


def run_one(name: str, args: list, timeout: float = None) -> tuple:
    """``(returncode, output)`` of one test file."""
    command = [sys.executable, os.path.join(HERE, name)] + args
    try:
        process = subprocess.run(command, cwd=REPO_ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        output = (expired.output or b"").decode("utf-8", "replace")
        return 124, output + "\n%s: timed out after %s s\n" % (name, timeout)
    return process.returncode, process.stdout.decode("utf-8", "replace")


def totals(output: str) -> tuple:
    """``(passed, skipped, failed)`` reported by one test file."""
    match = _SUMMARY.search(output)
    if not match:
        return 0, 0, 0
    return int(match.group(2)), int(match.group(3)), int(match.group(4))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", help="path to the World of Tanks installation")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="stream the output of every test file")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="seconds a single test file may take")
    parser.add_argument("tests", nargs="*", help="only these files (default: all)")
    args = parser.parse_args(argv)

    names = args.tests or test_files()
    forwarded = []
    if args.client:
        forwarded += ["--client", args.client]
    if args.verbose:
        forwarded.append("--verbose")

    passed = skipped = failed = 0
    broken = []
    for name in names:
        code, output = run_one(name, forwarded, timeout=args.timeout)
        file_passed, file_skipped, file_failed = totals(output)
        passed += file_passed
        skipped += file_skipped
        failed += file_failed
        if args.verbose or file_failed or code not in (0, 1):
            sys.stdout.write(output)
            if not output.endswith("\n"):
                sys.stdout.write("\n")
        elif file_skipped:
            print("  ....  %-28s %d passed, %d skipped" % (name, file_passed, file_skipped))
        else:
            print("  ok    %-28s %d passed" % (name, file_passed))
        if file_failed or code not in (0, 1):
            broken.append(name)

    print("=" * 60)
    print("run_all: %d passed, %d skipped, %d failed in %d file(s)"
          % (passed, skipped, failed, len(names)))
    if broken:
        print("failing file(s): %s" % ", ".join(broken))
    return 1 if failed or broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
