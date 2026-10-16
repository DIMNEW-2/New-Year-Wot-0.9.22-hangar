"""``python3 -m server verify`` - the self check a user runs before the game.

The command has to be honest: green when the server side is in place, and a
readable report when it is not.  These tests run it against a real installation
(any installation fits) and against a folder that is not one.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import check_client_assets as checker  # noqa: E402
import common  # noqa: E402
from server import doctor, loader  # noqa: E402


def _report(source, **kwargs):
    stream = io.StringIO()
    code = doctor.run(source=source, stream=stream, **kwargs)
    return code, stream.getvalue()


def _titles(report: str):
    return [line.strip()[7:] for line in report.splitlines() if line.startswith("  [")]


# ---------------------------------------------------------------------------
# a real installation
# ---------------------------------------------------------------------------
def test_the_doctor_is_green(runner: common.Runner) -> None:
    """Every check the local server depends on passes."""
    code, report = _report(runner.client)
    runner.equal(code, 0, "the doctor is green:\n%s" % report)
    runner.check("verify: the local server is ready" in report, "and says so")
    for title in ("the game data was found", "the New Year definitions are complete",
                  "the crew name lists are there", "the handshake answers",
                  "the account diff is complete", "the price cache is streamed",
                  "the shop tables of the installation are read",
                  "a loot box opens", "a garage command is answered",
                  "the dossier cache is streamed", "the account dossier is answered",
                  "an unsupported command is refused"):
        runner.check(any(title in line for line in _titles(report)),
                     "the report has a line about %r" % title)


def test_the_doctor_checks_the_wire(runner: common.Runner) -> None:
    """The session part really talks to a started server."""
    code, report = _report(runner.client)
    runner.equal(code, 0, "green")
    runner.check("the price cache is streamed" in report, "the shop cache was streamed")
    runner.check("an unsupported command is refused" in report,
                 "and an unsupported command is refused instead of faked")
    runner.check("CMD_REPAIR" in report, "a garage command was exercised")
    runner.check("a second sync is answered" in report,
                 "the client's repeated sync was answered too")
    runner.check("the loot box updates the account" in report,
                 "opening a box pushed a diff")
    runner.check("a badge of the profile is selected" in report
                 or "no badge to select" in report,
                 "the profile commands were exercised too")


def test_the_doctor_can_skip_the_session(runner: common.Runner) -> None:
    """``session=False`` only looks at the installation."""
    code, report = _report(runner.client, session=False)
    runner.equal(code, 0, "green:\n%s" % report)
    runner.check("the handshake answers" not in report, "no session was started")
    runner.check("The installation" in report, "only the installation was checked")


def test_the_doctor_reports_a_missing_mod_and_space(runner: common.Runner) -> None:
    """An installation without the mod or the hangar space is a warning, not a failure."""
    code, report = _report(runner.client, session=False)
    runner.equal(code, 0, "a warning keeps the run green:\n%s" % report)
    warnings = [line for line in report.splitlines() if line.startswith("  [warn]")]
    runner.check(warnings, "the report has warnings to read")


# ---------------------------------------------------------------------------
# a folder that is not an installation
# ---------------------------------------------------------------------------
def test_a_wrong_folder_is_a_failure(runner: common.Runner) -> None:
    """A folder that holds no game data is reported instead of crashing."""
    root = tempfile.mkdtemp(prefix="wotny-not-a-client-")
    try:
        with open(os.path.join(root, "readme.txt"), "w") as handle:
            handle.write("no game data here\n")
        code, report = _report(root)
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    runner.equal(code, 1, "the run fails:\n%s" % report)
    runner.check("the installation is unreadable" in report, "and names the problem")


def test_the_parser_offers_verify(runner: common.Runner) -> None:
    """``python3 -m server verify`` is wired up (a user's first command)."""
    from server import __main__ as cli

    parser = cli._build_parser()
    args = parser.parse_args(["verify", "--client", "/tmp"])
    runner.equal(args.command, "verify", "the command is parsed")
    runner.check(callable(getattr(cli, "_command_verify", None)),
                 "and has an entry point")


# ---------------------------------------------------------------------------
# the paths that only a packed (release) installation takes
# ---------------------------------------------------------------------------
def _packed_installation(source: str, root: str, space: bool = True) -> str:
    """A ``res/scripts.pkg``-shaped archive of an unpacked ``scripts`` folder."""
    import shutil
    import zipfile

    scripts = loader.find_client(source).scripts
    packages = os.path.join(root, "res", "packages")
    os.makedirs(packages)
    archive_path = os.path.join(root, "res", "scripts.pkg")
    with zipfile.ZipFile(archive_path, "w") as archive:
        prefix = "scripts/"
        for folder, _dirs, files in os.walk(scripts):
            for name in files:
                if not name.endswith((".xml", ".py")):
                    continue
                path = os.path.join(folder, name)
                archive.write(path, prefix + os.path.relpath(path, scripts).replace(os.sep, "/"))
        if space:
            archive.writestr("spaces/h07_newyear_2015/space.settings",
                             "<root><hangarSettings/></root>")
    return root


def test_the_hangar_space_is_found_in_a_package(runner: common.Runner) -> None:
    """``find_hangar_space`` looks inside the packages, not only at file names."""
    import tempfile

    root = tempfile.mkdtemp(prefix="wotny-packed-client-")
    try:
        _packed_installation(runner.client, root)
        found, detail = doctor.find_hangar_space(root)
        runner.check(found, "the space was found: %s" % detail)
        runner.check("space.settings" in detail, "and the report names the member")
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_a_packed_installation_is_fully_checked(runner: common.Runner) -> None:
    """A release layout (scripts.pkg, packages) passes the whole check."""
    import tempfile

    root = tempfile.mkdtemp(prefix="wotny-packed-client-")
    try:
        _packed_installation(runner.client, root)
        with open(os.path.join(root, "res", "packages", "h07_newyear_2015.pkg"), "wb"):
            pass  # an (empty) package named after the space is enough
        code, report = _report(root)
        runner.equal(code, 0, "the packed installation is green:\n%s" % report)
        runner.check("the New Year hangar space is in the packages" in report,
                     "the space check ran")
        runner.check("the New Year definitions are complete" in report,
                     "the catalogue was read")
        runner.check("the client mod is not installed yet" in report,
                     "and the missing mod is only a warning")
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_the_table_checker_reads_a_packed_installation(runner: common.Runner) -> None:
    """``tools/check_client_assets.py`` reads the client sources from the archive."""
    import tempfile

    root = tempfile.mkdtemp(prefix="wotny-packed-client-")
    try:
        _packed_installation(runner.client, root, space=False)
        output = io.StringIO()
        status = checker.check(root, stream=output)
        report = output.getvalue()
        runner.equal(status, 0, "the packed client agrees with the tables:\n%s" % report)
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
