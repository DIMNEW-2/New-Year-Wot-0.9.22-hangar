"""Finding the installation: unpacked trees, packed builds, nested folders.

The server reads the player's own game data, so it has to survive every layout a
World of Tanks installation comes in:

* ``<game>/res/scripts/item_defs`` (an unpacked build, what the tests use),
* ``<game>/res/scripts.pkg`` - a ZIP that holds ``scripts/item_defs/…``
  (what a release installation ships),
* the game one or two levels below the folder the user names
  (``<download>/World_of_Tanks_CN/…``),
* a catalog export (``catalog.json``) instead of an installation at all.

The tests build those layouts out of a handful of real XML files, so they run
without a game installed and without shipping any game data.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server import loader  # noqa: E402
from server.game.defs import DefsError, ZipSource, open_defs  # noqa: E402

#: The files a layout needs to be recognised and to load a (tiny) catalogue.
SAMPLE_FILES = (
    "item_types.xml",
    "new_year/toys.xml",
    "new_year/slots.xml",
    "new_year/new_year_types.xml",
    "new_year/boxes.xml",
    "new_year/chests.xml",
    "new_year/collection_rewards.xml",
    "new_year/variadic_discounts.xml",
    "new_year/variadic_tankmen.xml",
    "vehicles/common/equipments.xml",
    "vehicles/common/optional_devices.xml",
    "vehicles/ussr/list.xml",
    "vehicles/ussr/components/chassis.xml",
    "vehicles/ussr/components/turrets.xml",
    "vehicles/ussr/components/guns.xml",
    "vehicles/ussr/components/engines.xml",
    "vehicles/ussr/components/radios.xml",
    "vehicles/ussr/components/shells.xml",
    "tankmen/ussr.xml",
)


def _sample_source(runner) -> str:
    """A real ``item_defs`` folder of the installation the tests run against."""
    client = common.require_client(runner.client)
    if os.path.basename(os.path.normpath(client)) == "item_defs":
        source = client
    else:
        source = os.path.join(client, "item_defs")
        if not os.path.isdir(source):
            source = client
    if not os.path.isdir(source):
        raise common.Skip("no item_defs tree at %s" % source)
    return source


def _copy_tree(source: str, target: str, files=SAMPLE_FILES) -> int:
    """Copy the named files of ``source`` into ``target``; returns the count."""
    copied = 0
    for relative in files:
        origin = os.path.join(source, relative)
        if not os.path.isfile(origin):
            continue
        destination = os.path.join(target, relative)
        directory = os.path.dirname(destination)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        shutil.copyfile(origin, destination)
        copied += 1
    return copied


def _make_unpacked_install(runner, root: str) -> str:
    """``<root>/res/scripts/item_defs`` + a fake executable."""
    source = _sample_source(runner)
    item_defs = os.path.join(root, "res", "scripts", "item_defs")
    if not os.path.isdir(item_defs):
        os.makedirs(item_defs)
    written = _copy_tree(source, item_defs)
    runner.check(written >= 10, "the sample installation has enough files (%d)" % written)
    _touch(os.path.join(root, "WorldOfTanks.exe"))
    _touch(os.path.join(root, "res", "engine_config.xml"))
    return root


def _make_packed_install(runner, root: str) -> str:
    """A release-like build: the scripts live in ``res/scripts.pkg``."""
    source = _sample_source(runner)
    # A package keeps the same layout: ``scripts/item_defs/...``.
    staging = os.path.join(root, "_staging", "scripts", "item_defs")
    if not os.path.isdir(staging):
        os.makedirs(staging)
    written = _copy_tree(source, staging)
    runner.check(written >= 10, "the sample package has enough files (%d)" % written)
    package = os.path.join(root, "res", "scripts.pkg")
    if not os.path.isdir(os.path.dirname(package)):
        os.makedirs(os.path.dirname(package))
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for current, _directories, files in os.walk(os.path.join(root, "_staging")):
            for name in files:
                path = os.path.join(current, name)
                relative = os.path.relpath(path, os.path.join(root, "_staging"))
                archive.write(path, relative.replace("\\", "/"))
    shutil.rmtree(os.path.join(root, "_staging"), ignore_errors=True)
    # What a real installation also has, so the markers match.
    for relative in ("WorldOfTanks.exe", os.path.join("res", "engine_config.xml"),
                     os.path.join("res", "packages", "content.pkg")):
        path = os.path.join(root, relative)
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        _touch(path)
    return root


def _touch(path: str) -> None:
    with open(path, "wb") as handle:
        handle.write(b"")


def test_unpacked_installation(runner: common.Runner) -> None:
    """``res/scripts/item_defs`` is found and reads a catalogue."""
    with _workspace() as root:
        _make_unpacked_install(runner, root)
        layout = loader.find_client(root)
        runner.equal(layout.root, root, "the installation root")
        runner.check(layout.item_defs.endswith(os.path.join("res", "scripts", "item_defs")),
                     "the item_defs folder")
        catalog = loader.load_catalog(root)
        runner.check(len(catalog.new_year.toys) > 0, "the decorations of the tree")


def test_packed_installation(runner: common.Runner) -> None:
    """A release build keeps the same data inside ``res/scripts.pkg``."""
    with _workspace() as root:
        _make_packed_install(runner, root)
        layout = loader.find_client(root)
        runner.equal(layout.root, root, "the installation root")
        runner.check(layout.item_defs.endswith(os.path.join("res", "scripts.pkg")),
                     "the package is the data source")
        runner.check(loader.find_client_script_package(root),
                     "the package is reported by find_client_script_package")
        catalog = loader.load_catalog(root)
        runner.check(len(catalog.new_year.boxes) > 0, "the loot boxes of the package")
        runner.check(len(catalog.vehicles.all()) > 0, "the vehicles of the package")


def test_nested_installation(runner: common.Runner) -> None:
    """The game folder is often one level below the folder the user names."""
    with _workspace() as parent:
        root = os.path.join(parent, "World_of_Tanks_0.09.22.00.01_CH_1513_HD")
        os.makedirs(root)
        _make_unpacked_install(runner, root)
        layout = loader.find_client(parent)
        runner.equal(layout.root, root, "the nested installation was found")
        catalog = loader.load_catalog(parent)
        runner.check(len(catalog.new_year.chests) > 0, "the chests of the nested tree")


def test_scripts_package_named_directly(runner: common.Runner) -> None:
    """Passing the archive itself works as well."""
    with _workspace() as root:
        _make_packed_install(runner, root)
        package = os.path.join(root, "res", "scripts.pkg")
        layout = loader.find_client(package)
        runner.equal(layout.item_defs, package, "the archive the user named")
        source = open_defs(package)
        try:
            runner.check(isinstance(source, ZipSource), "a ZIP source")
            runner.check(source.read_text("new_year/boxes.xml") is not None,
                         "a file inside the archive is readable")
        finally:
            source.close()


def test_missing_installation_reports_the_layout(runner: common.Runner) -> None:
    """A wrong folder explains itself instead of raising a traceback."""
    with _workspace() as root:
        os.makedirs(os.path.join(root, "World_of_Tanks"))
        _touch(os.path.join(root, "readme.txt"))
        error = None
        try:
            loader.find_client(root)
        except loader.ClientNotFound as caught:
            error = caught
        runner.check(error is not None, "an unknown folder is refused")
        report = loader.describe_installation(root)
        runner.check("not usable" in report, "the report says the folder is unusable")
        runner.check("World_of_Tanks/" in report, "the report lists what is there")
        runner.check(os.path.join("res", "scripts", "item_defs") in str(error),
                     "the error names the folder that was looked for")


def test_catalog_json_is_read_as_data(runner: common.Runner) -> None:
    """A JSON file is opened as an export, never searched for an installation."""
    with _workspace() as root:
        path = os.path.join(root, "catalog.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"not": "a catalog"}')
        error = None
        try:
            loader.load_catalog(path)
        except DefsError as caught:
            error = caught
        except Exception as caught:  # a JSON export is data, not an install
            error = caught
        runner.check(error is not None, "the file is not accepted silently")
        runner.check(not isinstance(error, loader.ClientNotFound),
                     "the file is not treated as an installation to search")
        runner.check("catalog" in str(error), "the error names the catalog: %s" % error)


class _workspace(object):
    """A temporary folder that disappears with the ``with`` block."""

    def __enter__(self) -> str:
        self.path = tempfile.mkdtemp(prefix="wotny-install-")
        return self.path

    def __exit__(self, *error) -> bool:
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
