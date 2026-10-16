"""Reading a client's data files: packers, code pages and the reports.

A release installation keeps its data inside ``res/packages/scripts.pkg``, and a
build is free to compress a member on its own, to write it in UTF-16 or in the
code page of its language - the file is still the file.  :mod:`server.game.payload`
undoes what it recognizes, :mod:`server.game.defs` reports what it could not, and
the loader finds the hangar space of the build instead of assuming one.

These tests build such packages themselves (the layout is the one the client's
own ``item_defs`` has), so they run without an installation - except the two that
copy the sample files of the one the suite was pointed at.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import sys
import tempfile
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server import loader  # noqa: E402
from server.game import defs as defs_module  # noqa: E402
from server.game import payload  # noqa: E402

XML = b'<?xml version="1.0" encoding="utf-8"?>\n<toys.xml>\n  <toy><id>1</id></toy>\n</toys.xml>\n'


class _Workspace(object):
    """A temporary folder that is cleaned up afterwards."""

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="wotny-payload-")
        return self.root

    def __exit__(self, *_exception):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)
        return False


def _package(root: str, name: str = "scripts.pkg") -> str:
    folder = os.path.join(root, "res", "packages")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return os.path.join(folder, name)


# ---------------------------------------------------------------------------
# the payload reader itself
# ---------------------------------------------------------------------------
def test_the_wrappers_a_build_can_put_around_a_file(runner: common.Runner) -> None:
    """Every compression a packer may use is undone, and nothing else is."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("toys.xml", XML)
    cases = (
        ("plain", XML, "utf-8"),
        ("a one-file zip", buffer.getvalue(), "zip"),
        ("gzip", gzip.compress(XML), "gzip"),
        ("zlib", zlib.compress(XML), "zlib"),
        ("raw deflate", zlib.compress(XML)[2:-4], "raw deflate"),
        ("bzip2", bz2.compress(XML), "bzip2"),
        ("xz", lzma.compress(XML), "xz"),
        ("zlib in gzip", gzip.compress(zlib.compress(XML)), "gzip+zlib"),
    )
    for name, data, expected in cases:
        text, note = payload.read_payload(data)
        runner.equal(text.encode("utf-8"), XML, "%s reads back unchanged" % name)
        runner.check(expected in note, "%s is named in the note: %s" % (name, note))
    # A payload that only *looks* like a codec is left alone: a file must never
    # be destroyed by a wrong guess.
    harmless = b"<?xml version='1.0'?><root/>"
    runner.equal(payload.read_payload(harmless)[0].encode("utf-8"), harmless,
                 "a plain file is not touched")


def test_the_encodings_a_build_can_write(runner: common.Runner) -> None:
    """UTF-16, a BOM and the code page of a localized build all read."""
    text = "<toys.xml><toy>中国</toy></toys.xml>"
    for name, data, expected in (
        ("utf-8", text.encode("utf-8"), "utf-8"),
        ("utf-8 with a BOM", text.encode("utf-8-sig"), "utf-8-sig"),
        ("utf-16", text.encode("utf-16"), "utf-16-le"),
        ("utf-16 without a BOM", text.encode("utf-16-le"), "utf-16-le"),
        ("GBK", text.encode("gb18030"), "gb18030"),
    ):
        decoded, encoding = payload.read_payload(data)
        runner.equal(decoded, text, "%s decodes" % name)
        runner.equal(encoding, expected, "and is reported as %s" % expected)
    declared = ('<?xml version="1.0" encoding="GBK"?>\n<root>中文</root>').encode("gb18030")
    runner.equal(payload.read_payload(declared)[0].encode("gb18030").decode("gb18030"),
                 declared.decode("gb18030"), "the code page of the declaration is used")


def test_a_payload_that_is_not_xml_says_what_it_is(runner: common.Runner) -> None:
    """``describe`` is the message a user reports and a maintainer acts on."""
    report = payload.describe("new_year/toys.xml", zlib.compress(XML), "scripts.pkg")
    runner.check("new_year/toys.xml" in report, "the file is named: %s" % report)
    runner.check("zlib" in report, "and the wrapper it traveled in")
    runner.check("78 9c" in report, "the first bytes are shown")
    runner.check("78 9c" in payload.preview(zlib.compress(XML)), "as hex")
    # The guesses of ``format_name``/``sniff`` line up with the content.
    runner.equal(payload.format_name(zlib.compress(XML)), "zlib-compressed",
                 "a zlib stream is named as one")
    runner.equal(payload.format_name(XML), "XML", "XML is named as XML")
    runner.equal(payload.format_name(XML.decode().encode("utf-16")),
                 "utf-16-le text (XML)", "and UTF-16 spells out the byte order")
    runner.equal(payload.sniff(XML)["xml"], True, "``sniff`` says whether it is XML")
    runner.equal(payload.sniff(zlib.compress(XML))["unwrapped"], "zlib",
                 "and what had to be undone")


# ---------------------------------------------------------------------------
# reading them through a source
# ---------------------------------------------------------------------------
def _packed_source(root: str, members) -> defs_module.DefsSource:
    path = _package(root)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr("scripts/item_defs/" + name, data, zipfile.ZIP_STORED)
    return defs_module.open_defs(path)


def test_a_package_whose_members_are_wrapped_still_reads(runner: common.Runner) -> None:
    """A ``scripts.pkg`` that compresses on its own is read like any other."""
    with _Workspace() as root:
        source = _packed_source(root, (
            ("new_year/toys.xml", XML),
            ("new_year/slots.xml", zlib.compress(XML)),
            ("new_year/boxes.xml", zlib.compress(XML)[2:-4]),
            ("new_year/chests.xml", XML.decode().encode("utf-16")),
            ("new_year/variadic_discounts.xml", gzip.compress(XML)),
        ))
        for name in ("toys", "slots", "boxes", "chests", "variadic_discounts"):
            section = source.read_section("new_year/%s.xml" % name)
            runner.equal(len(section.children), 1, "%s.xml is parsed" % name)
            runner.equal(section.children[0].value_int("id"), 1, "with its values")
        runner.equal(source.read_text("new_year/toys.xml").strip()[:5], "<?xml",
                     "``read_text`` goes through the same reader")
        runner.check(source.members(), "the source lists what it can read")


def test_a_member_that_is_not_xml_names_itself(runner: common.Runner) -> None:
    """The reader never says "cannot parse XML" without saying *which* file."""
    with _Workspace() as root:
        broken = b"\x9c\x01\x00not xml at all\xff"
        source = _packed_source(root, (("new_year/toys.xml", XML),
                                      ("new_year/slots.xml", broken)))
        runner.equal(len(source.read_section("new_year/toys.xml").children), 1,
                     "the good member reads")
        try:
            source.read_section("new_year/slots.xml")
        except defs_module.DefsError as error:
            message = str(error)
        else:
            raise AssertionError("the broken member was accepted")
        runner.check("new_year/slots.xml" in message,
                     "the failing file is named: %s" % message)
        runner.check("first :" in message, "the first bytes are shown")
        runner.check("scripts.pkg" in message, "and the package it came from")


def test_a_missing_member_is_reported_as_missing(runner: common.Runner) -> None:
    with _Workspace() as root:
        source = _packed_source(root, (("new_year/toys.xml", XML),))
        runner.equal(source.read_section("new_year/toys.xml").name, "toys.xml",
                     "a present member reads")
        runner.equal(source.has("new_year/slots.xml"), False, "``has`` sees the gap")
        try:
            source.read_section("new_year/slots.xml")
        except defs_module.DefsError as error:
            runner.check("missing client data file" in str(error), "and says so")
        else:
            raise AssertionError("a missing member was accepted")


# ---------------------------------------------------------------------------
# the hangar space of the build
# ---------------------------------------------------------------------------
def test_the_space_is_detected_instead_of_assumed(runner: common.Runner) -> None:
    """Whatever the build ships is what the notification names."""
    with _Workspace() as root:
        path = _package(root, "hangar.pkg")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("spaces/h07_newyear_2018/space.settings", "<root/>")
            archive.writestr("spaces/h07_newyear_2018/space.settings.bin", "")
            archive.writestr("spaces/normandy/space.settings", "<root/>")
        names, stats = loader.find_spaces(root)
        runner.equal(names, ["h07_newyear_2018", "normandy"], "every space is found")
        runner.equal(stats["archives"], 1, "and the counters count")
        runner.equal(stats["readable"], 1, "the readable archives")
        space, spaces, _stats = loader.guess_new_year_space(root)
        runner.equal(space, "h07_newyear_2018", "the New Year space wins over the rest")

        # A build that renames its space still gets its own hangar.
        with zipfile.ZipFile(_package(root, "hangar2.pkg"), "w") as archive:
            archive.writestr("spaces/h07_ny2019/space.settings", "<root/>")
        space, spaces, _stats = loader.guess_new_year_space(root)
        runner.equal(space, "h07_newyear_2018",
                     "a known name still wins over an unknown one")
        # A build from an *older* event falls back to the name it uses.
        with _Workspace() as other:
            with zipfile.ZipFile(_package(other, "hangar.pkg"), "w") as archive:
                archive.writestr("spaces/h07_newyear_2015/space.settings", "<root/>")
            space, spaces, _stats = loader.guess_new_year_space(other)
            runner.equal(space, "h07_newyear_2015", "an older event space is used too")
        with _Workspace() as other:
            with zipfile.ZipFile(_package(other, "hangar.pkg"), "w") as archive:
                archive.writestr("spaces/ny_new_year_2019/space.settings", "<root/>")
            space, spaces, _stats = loader.guess_new_year_space(other)
            runner.equal(space, "ny_new_year_2019",
                         "a renamed space is still recognized as the event's")
        with _Workspace() as other:
            with zipfile.ZipFile(_package(other, "content.pkg"), "w") as archive:
                archive.writestr("spaces/normandy/space.settings", "<root/>")
            space, spaces, _stats = loader.guess_new_year_space(other)
            runner.equal(space, None, "a build without an event space says so")


def test_an_unreadable_package_does_not_stop_the_search(runner: common.Runner) -> None:
    """A part package that is not a zip is skipped, the rest is searched."""
    with _Workspace() as root:
        with open(_package(root, "part_01.pkg"), "wb") as handle:
            handle.write(b"this is not a zip at all")
        with zipfile.ZipFile(_package(root, "hangar.pkg"), "w") as archive:
            archive.writestr("spaces/h07_newyear_2018/space.settings", "<root/>")
        names, stats = loader.find_spaces(root)
        runner.equal(names, ["h07_newyear_2018"], "the space of the readable one")
        runner.equal(stats["archives"], 2, "both archives were looked at")
        runner.equal(stats["readable"], 1, "one of them is a zip")


# ---------------------------------------------------------------------------
# the client scripts (the medal rewards read one of them)
# ---------------------------------------------------------------------------
def test_the_client_scripts_are_read_from_the_package(runner: common.Runner) -> None:
    """``client_script_reader`` finds a script whether it is packed or not."""
    with _Workspace() as root:
        with zipfile.ZipFile(_package(root), "w") as archive:
            archive.writestr("scripts/common/dossiers2/custom/records.py",
                             "RECORD_DB_IDS = {('total', 'mileage'): 1}\n")
            archive.writestr("scripts/item_defs/new_year/toys.xml", XML)
        reader = loader.client_script_reader(root)
        text = reader("common/dossiers2/custom/records.py")
        runner.check(text and "RECORD_DB_IDS" in text,
                     "the script is read out of the package")
        runner.equal(reader("common/nope.py"), None, "an absent script is ``None``")

        # The same tree unpacked reads through the folder, and a *folder* that
        # is not a scripts tree (``res/packages``) is not mistaken for one.
        unpacked = os.path.join(root, "res", "unpacked")
        target = os.path.join(unpacked, "common", "dossiers2", "custom")
        os.makedirs(target)
        with open(os.path.join(target, "records.py"), "w") as handle:
            handle.write("RECORD_DB_IDS = {'x': 2}\n")
        runner.check(isinstance(loader.client_script_reader(root), type(reader)),
                     "a reader is a callable either way")
        runner.check("'x': 2" in loader.client_script_reader(unpacked)(
            "common/dossiers2/custom/records.py"),
            "a folder of a *scripts* tree is read as one")


def test_the_hint_of_the_layout_names_the_package(runner: common.Runner) -> None:
    """``find_client`` points at the package, not at its folder."""
    with _Workspace() as root:
        with zipfile.ZipFile(_package(root), "w") as archive:
            archive.writestr("scripts/item_defs/new_year/toys.xml", XML)
            archive.writestr("scripts/item_defs/vehicles/list.xml", "<root/>")
        layout = loader.find_client(root)
        runner.check(layout.scripts.endswith("scripts.pkg"),
                     "``scripts`` is the package itself: %s" % layout.scripts)
        report = loader.describe_installation(root)
        runner.check("scripts.pkg" in report, "and the report says so")


# ---------------------------------------------------------------------------
# the same, with the files of a real installation
# ---------------------------------------------------------------------------
#: The ways a packer may hand a member to the reader, one per index modulo.
MEMBER_STYLES = ("stored", "zlib", "raw deflate", "gzip", "bzip2", "xz",
                 "utf-16-le", "utf-16 (with a BOM)", "a zip around it", "gb18030")


def _style(data: bytes, index: int) -> bytes:
    """``data`` the way a packer with style number ``index % 10`` would ship it."""
    style = MEMBER_STYLES[index % len(MEMBER_STYLES)]
    if style == "stored":
        return data
    if style == "zlib":
        return zlib.compress(data)
    if style == "raw deflate":
        return zlib.compress(data)[2:-4]
    if style == "gzip":
        return gzip.compress(data)
    if style == "bzip2":
        return bz2.compress(data)
    if style == "xz":
        return lzma.compress(data)
    text = data.decode("utf-8", "replace")
    if style == "utf-16-le":
        return text.encode("utf-16-le")
    if style == "utf-16 (with a BOM)":
        return text.encode("utf-16")
    if style == "a zip around it":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("member.xml", data)
        return buffer.getvalue()
    return text.encode("gb18030", "replace")


def test_a_packed_sample_client_reads_like_an_unpacked_one(runner: common.Runner) -> None:
    """A packed build reads like the unpacked tree, whatever it does to a member.

    A release installation does not merely pack its definitions: it may compress
    a member on its own and write it in UTF-16 or in the code page of its
    language (a Chinese build does both).  Every tenth file of the sample tree
    therefore travels in another style, and the catalogue that comes out has to
    be the one of the unpacked tree.
    """
    client = common.require_client(runner.client)
    try:
        layout = loader.find_client(client)
    except Exception as error:  # noqa: BLE001
        raise common.Skip("no installation to compare against (%s)" % error)
    if os.path.isfile(layout.item_defs):
        raise common.Skip("the suite was pointed at a packed install already")
    files = []
    for folder, _dirs, names in os.walk(layout.item_defs):
        for name in names:
            path = os.path.join(folder, name)
            files.append((os.path.relpath(path, layout.item_defs).replace(os.sep, "/"), path))
    if len(files) < 100:
        raise common.Skip("the sample tree is too small to pack (%d files)" % len(files))
    with _Workspace() as root:
        packages = os.path.join(root, "res", "packages")
        os.makedirs(packages)
        styles = {}
        with zipfile.ZipFile(os.path.join(packages, "scripts.pkg"), "w",
                             zipfile.ZIP_DEFLATED) as archive:
            for index, (relative, path) in enumerate(sorted(files)):
                with open(path, "rb") as handle:
                    data = handle.read()
                if relative.endswith(".xml"):
                    data = _style(data, index)
                    styles[MEMBER_STYLES[index % len(MEMBER_STYLES)]] = \
                        styles.get(MEMBER_STYLES[index % len(MEMBER_STYLES)], 0) + 1
                archive.writestr("scripts/item_defs/" + relative, data)
        runner.check(len(styles) == len(MEMBER_STYLES),
                     "the sample build uses every packaging style: %s" % sorted(styles))
        runner.check(min(styles.values()) > 10,
                     "and enough files per style: %s" % sorted(styles.items()))
        with zipfile.ZipFile(os.path.join(packages, "hangar.pkg"), "w") as archive:
            archive.writestr("spaces/h07_newyear_2018/space.settings", "<root/>")
        _touch(os.path.join(root, "WorldOfTanks.exe"))
        unpacked = loader.load_catalog(layout.item_defs)
        packed = loader.load_catalog(root)
        runner.equal(len(packed.new_year.toys), len(unpacked.new_year.toys),
                     "the decorations of the tree read from the package")
        runner.equal(len(packed.vehicles.all()), len(unpacked.vehicles.all()),
                     "and so do the vehicles")
        runner.equal(sorted(int(vehicle.compact_descr) for vehicle in packed.vehicles.all()),
                     sorted(int(vehicle.compact_descr) for vehicle in unpacked.vehicles.all()),
                     "with the very same compact descriptors")
        runner.equal(len(packed.customization.items), len(unpacked.customization.items),
                     "the appearance items are all there")
        for name in ("boxes", "chests", "slots"):
            runner.equal(len(getattr(packed.new_year, name)),
                         len(getattr(unpacked.new_year, name)),
                         "the event's %s read from the package" % name)
        space, _spaces, _stats = loader.guess_new_year_space(root)
        runner.equal(space, "h07_newyear_2018", "and the space of the build is found")


def _touch(path: str) -> None:
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as handle:
        handle.write(b"")


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
