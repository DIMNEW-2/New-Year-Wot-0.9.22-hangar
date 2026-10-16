"""Loading the game data this server needs from the player's installation.

The client ships everything the server must agree with (item definitions, New
Year tables, tankman name lists, the game's own ``new_year_types.py``), so the
server reads the player's own copy instead of bundling game data.  Two entry
points cover every case:

* :func:`open_defs` - ``<game>/res/scripts/item_defs`` as a directory, or the
  packed ``<game>/res/scripts.pkg``,
* :func:`load_catalog` - the decoded catalogue, either from an extracted JSON
  file (``tools/extract_client_catalog.py``) or straight from the client tree.

The mod lives inside the client process and therefore uses the same API.
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from typing import Dict, Optional

from .game import tankmen as tankmen_module
from .game import achievements_reward as achievements_reward_module
from .game.catalog import Catalog
from .game.defs import DefsError, find_client_script_package as _find_package, open_defs

#: Directories that hold the game data inside an installation.
ITEM_DEFS_CANDIDATES = (
    os.path.join("res", "scripts", "item_defs"),
    os.path.join("res_mods", "0.9.22.0.1", "scripts", "item_defs"),
    os.path.join("scripts", "item_defs"),
    "item_defs",
)
#: The scripts travel either unpacked (``res/scripts``) or inside an archive
#: (``res/scripts.pkg``); a release installation ships the archive.
SCRIPTS_CANDIDATES = (
    os.path.join("res", "scripts.pkg"),
    os.path.join("res", "scripts"),
    os.path.join("scripts.pkg"),
    "scripts",
)
SERVER_XML_CANDIDATES = (
    os.path.join("res", "scripts", "server_xml"),
    os.path.join("res", "scripts.pkg"),
)
#: Archives that may carry ``scripts/item_defs``, in the order they are tried.
PACKAGE_CANDIDATES = (
    os.path.join("res", "scripts.pkg"),
    os.path.join("res", "scripts.zip"),
    os.path.join("res", "packages", "scripts.pkg"),
    os.path.join("res_mods", "0.9.22.0.1", "scripts.pkg"),
    "scripts.pkg",
    "scripts.zip",
)
#: Files/directories that say "this is a World of Tanks installation".
INSTALL_MARKERS = (
    os.path.join("res", "packages"),
    os.path.join("res", "scripts.pkg"),
    os.path.join("res", "scripts"),
    os.path.join("res", "engine_config.xml"),
    "WorldOfTanks.exe",
    "wotlauncher.exe",
)
#: How deep to look for the installation inside the directory the user named
#: (an unpacked archive often has one more level, e.g. ``World_of_Tanks_CN``).
MAX_SEARCH_DEPTH = 3


class ClientNotFound(DefsError):
    """Raised when no usable World of Tanks installation was found."""


@dataclass
class ClientLayout:
    """Where the interesting files of one installation live."""

    root: str
    item_defs: str
    scripts: str
    server_xml: Optional[str] = None

    @property
    def version(self) -> str:
        return "0.9.22.0.1"


def find_client(root: str) -> ClientLayout:
    """Return the layout of the installation at ``root``.

    A release installation keeps its game data in an archive
    (``res/scripts.pkg``) and often has the game itself one level below the
    folder the user points at (``<download>/World_of_Tanks_CN/…``), so both are
    searched.  When nothing is found the error names every place that was
    checked - see also ``python3 -m server inspect --client <path>``.
    """
    raw = os.path.abspath(os.path.expanduser(root)) if root else ""
    if os.path.isfile(raw) and _is_script_package(raw):
        # The user named the archive itself (``res/scripts.pkg``).
        if _archive_has_item_defs(raw):
            folder = os.path.dirname(raw)
            return ClientLayout(root=folder, item_defs=raw, scripts=raw,
                                server_xml=raw)
        raise ClientNotFound("%s does not contain scripts/item_defs" % raw)
    root = _normalise_root(root)
    if not root:
        raise ClientNotFound("no such directory: %r" % (root,))
    tried = []
    for candidate in _candidate_roots(root):
        layout = _layout_here(candidate, tried)
        if layout is not None:
            return layout
    raise ClientNotFound(
        "%r does not look like a World of Tanks 0.9.22 installation.\n"
        "checked, in this order:\n  %s\n"
        "run \"python3 -m server inspect --client %s\" for the contents of "
        "these folders" % (root, "\n  ".join(tried), root)
    )


def _normalise_root(root: str) -> Optional[str]:
    if not root:
        return None
    root = os.path.abspath(os.path.expanduser(root))
    if os.path.isdir(root):
        return root
    if os.path.isfile(root):
        # A ``scripts.pkg`` (or a catalog JSON) was named: use its folder.
        return os.path.dirname(root)
    return None


def _candidate_roots(root: str):
    """``root`` itself plus the installations nested inside it."""
    yield root
    if _looks_like_item_defs(root):
        return
    for depth in range(1, MAX_SEARCH_DEPTH + 1):
        for nested in _walk_directories(root, depth):
            yield nested


def _walk_directories(root: str, depth: int):
    """Directories exactly ``depth`` levels below ``root``, in name order."""
    if depth <= 0:
        yield root
        return
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return
    for name in entries:
        if name.startswith(".") or name.lower() in _IGNORED_DIRECTORIES:
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        for nested in _walk_directories(path, depth - 1):
            yield nested


#: Folders that never hold an installation; skipping them keeps the search fast.
_IGNORED_DIRECTORIES = frozenset((
    "__pycache__", "node_modules", "system volume information", "$recycle.bin",
    "windows", "program files", "program files (x86)",
))


def _layout_here(root: str, tried: list) -> Optional[ClientLayout]:
    """The layout of ``root``, or ``None`` when it is not an installation."""
    item_defs = None
    if _looks_like_item_defs(root):
        item_defs = root
    else:
        for candidate in ITEM_DEFS_CANDIDATES:
            path = os.path.join(root, candidate)
            tried.append(path)
            if os.path.isdir(path) and _looks_like_item_defs(path):
                item_defs = path
                break
    if item_defs is None:
        # The scripts of a release build live in an archive.
        package = find_client_script_package(root)
        if package is not None:
            item_defs = package
    if item_defs is None:
        return None
    scripts = None
    for candidate in SCRIPTS_CANDIDATES:
        path = os.path.join(root, candidate)
        if os.path.isdir(path) or os.path.isfile(path):
            scripts = path
            break
    if scripts is None:
        # An unpacked ``item_defs`` tree is enough on its own: the surrounding
        # folder is the ``scripts`` folder the item definitions belong to.
        scripts = os.path.dirname(item_defs)
    server_xml = None
    for candidate in SERVER_XML_CANDIDATES:
        path = os.path.join(root, candidate)
        if os.path.exists(path):
            server_xml = path
            break
    return ClientLayout(root=root, item_defs=item_defs, scripts=scripts, server_xml=server_xml)


def _looks_like_item_defs(path: str) -> bool:
    return (os.path.isdir(os.path.join(path, "new_year"))
            and os.path.isdir(os.path.join(path, "vehicles")))


def find_client_script_package(client_dir: str) -> Optional[str]:
    """The archive holding the scripts of an installation, or ``None``.

    ``res/scripts.pkg`` of a release build is found first; a build that renamed
    or moved its package is found by looking at the archives of the folder.
    """
    if not client_dir:
        return None
    for candidate in PACKAGE_CANDIDATES:
        path = os.path.join(client_dir, candidate)
        if os.path.isfile(path) and _archive_has_item_defs(path):
            return path
    for path in _archive_names(client_dir):
        if _archive_has_item_defs(path):
            return path
    return _find_package(client_dir)


def _archive_names(client_dir: str) -> list:
    """Every readable archive below ``client_dir``, shallowest first."""
    found = []
    folders = ("res", os.path.join("res", "packages"), "", os.path.join("res_mods", "0.9.22.0.1"))
    for folder in folders:
        directory = os.path.join(client_dir, folder) if folder else client_dir
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path) and _is_script_package(path):
                found.append(path)
    return found


def _archive_has_item_defs(path: str) -> bool:
    """``True`` when the archive carries an ``item_defs`` tree."""
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename.replace("\\", "/").lower()
                if name.startswith(("scripts/item_defs/", "item_defs/",
                                    "res/scripts/item_defs/")):
                    return True
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return False
    return False


def candidate_client_roots() -> list:
    """Places worth trying when the user did not pass ``--client``."""
    roots = []
    for variable in ("WOTNY_CLIENT", "WOT_CLIENT_DIR", "WOT_DIR"):
        value = os.environ.get(variable)
        if value:
            roots.append(value)
    roots.append(os.getcwd())
    for name in ("World_of_Tanks", "World_of_Tanks_CN", "WoT"):
        parent = os.path.expanduser("~")
        roots.append(os.path.join(parent, name))
    return roots


def guess_client() -> Optional[str]:
    for root in candidate_client_roots():
        try:
            find_client(root)
        except ClientNotFound:
            continue
        return root
    return None


def open_client_defs(client_root: str):
    """Open the ``item_defs`` of an installation (unpacked tree or ``scripts.pkg``)."""
    layout = find_client(client_root)
    return open_defs(layout.item_defs)


def source_description(path_or_client: str) -> str:
    if os.path.isfile(path_or_client) and not _is_script_package(path_or_client):
        return "catalog file %s" % path_or_client
    return find_client(path_or_client).item_defs


def _is_script_package(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".pkg", ".zip")


def describe_installation(root: str) -> str:
    """A human readable report of what an installation looks like.

    Used by ``python3 -m server inspect``: it never raises, so it can explain a
    layout the server cannot read yet.
    """
    lines = []
    root = _normalise_root(root) or root
    lines.append("path            : %s" % root)
    if not os.path.isdir(root):
        lines.append("state           : not a directory")
        lines.append("hint            : --client wants the folder that holds "
                     "WorldOfTanks.exe")
        parent = os.path.dirname(os.path.abspath(root))
        if os.path.isdir(parent):
            lines.append("nearby (%s) :" % parent)
            for name in _directory_listing(parent, limit=20):
                lines.append("  %s" % name)
        return "\n".join(lines)
    try:
        layout = find_client(root)
    except ClientNotFound as error:
        lines.append("state           : not usable")
        lines.append(str(error))
        lines.append("contents        :")
        for name in _directory_listing(root):
            lines.append("  %s" % name)
        return "\n".join(lines)
    lines.append("state           : ok")
    lines.append("root            : %s" % layout.root)
    lines.append("item defs       : %s" % layout.item_defs)
    lines.append("scripts         : %s" % layout.scripts)
    lines.append("server xml      : %s" % (layout.server_xml or "-"))
    lines.append("version folder  : %s" % layout.version)
    mods = os.path.join(layout.root, "res_mods", layout.version)
    lines.append("res_mods        : %s" % ("present" if os.path.isdir(mods) else "absent"))
    return "\n".join(lines)


def _directory_listing(root: str, depth: int = 1, limit: int = 40) -> list:
    """A short tree of ``root`` for the diagnostics of ``inspect``."""
    result = []
    try:
        entries = sorted(os.listdir(root))
    except OSError as error:
        return ["<unreadable: %s>" % error]
    for name in entries[:limit]:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            result.append("%s/" % name)
            if depth > 1:
                for nested in _directory_listing(path, depth - 1, limit):
                    result.append("  %s" % nested)
        else:
            result.append(name)
    if len(entries) > limit:
        result.append("... and %d more" % (len(entries) - limit))
    return result


def load_catalog(path_or_client: str) -> Catalog:
    """Load the catalogue from a client directory or an extracted JSON file."""
    if os.path.isfile(path_or_client) and not _is_script_package(path_or_client):
        from .game.defs import open_json_export

        source = open_json_export(path_or_client)
        return Catalog.load(source)

    source = open_client_defs(path_or_client)
    try:
        catalog = Catalog.load(source)
    finally:
        source.close()
    # The ``achievementsReward`` section of the shop cache needs a file of the
    # client's *scripts* (the record ids of its dossiers), which is not part of
    # ``item_defs`` - and a catalog JSON export carries the table it was
    # exported with.  An installation whose scripts cannot be read keeps the
    # disabled section.
    catalog.achievements_reward = achievements_reward_module.load(
        client_script_reader(path_or_client))
    return catalog


def client_script_reader(client_root: str):
    """A callable reading one file of an installation's ``scripts`` tree.

    The tree of a release build lives in ``res/scripts.pkg`` and an unpacked one
    in ``res/scripts``; both are understood, and the folder may be named
    directly (the ``scripts`` folder itself, a ``scripts.pkg``) or be the client
    root.  The returned callable answers a relative path (``common/…``) with the
    text of the file or ``None`` - a reader that never raises, so a caller can
    use it against any installation.
    """
    folder = None
    try:
        layout = find_client(client_root)
    except ClientNotFound:
        layout = None
    if layout is not None:
        folder = layout.scripts
    if not folder:
        root = _normalise_root(client_root)
        for candidate in ([root] if root else []) + [os.path.join(root, name)
                                                     for name in ("res", "scripts", "common")]:
            if not candidate or not os.path.isdir(candidate):
                continue
            if os.path.isdir(os.path.join(candidate, "common")):
                folder = candidate
                break
            if os.path.isdir(os.path.join(candidate, "res", "scripts")):
                folder = os.path.join(candidate, "res", "scripts")
                break
    if not folder and os.path.isfile(client_root or "") and _is_script_package(client_root):
        folder = client_root
    if folder and os.path.isdir(folder):
        def read_from_folder(rel_path, _folder=folder):
            path = os.path.join(_folder, rel_path.replace("/", os.sep))
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    return handle.read()
            except OSError:
                return None

        return read_from_folder

    archive = None
    if folder and os.path.isfile(folder):
        archive = folder
    else:
        archive = find_client_script_package(_normalise_root(client_root) or client_root)
    if archive:
        def read_from_archive(rel_path, _archive=archive):
            wanted = "/" + rel_path.lower()
            with_suffix = "/scripts" + wanted
            try:
                with zipfile.ZipFile(_archive) as handle:
                    for info in handle.infolist():
                        name = info.filename.replace("\\", "/").lower().lstrip("/")
                        if name.endswith(wanted) or name.endswith(with_suffix):
                            return handle.read(info.filename).decode("utf-8", "replace")
            except (zipfile.BadZipFile, OSError, RuntimeError):
                return None
            return None

        return read_from_archive

    def no_scripts(rel_path):
        return None

    return no_scripts


def load_tankmen_configs(client_root: str, nations=None) -> Dict[str, tankmen_module.NationConfig]:
    """Read ``item_defs/tankmen/<nation>.xml`` of the installation."""
    from .game import ny_constants as nyc

    source = open_client_defs(client_root)
    configs: Dict[str, tankmen_module.NationConfig] = {}
    try:
        for nation in nations or nyc.NATIONS:
            try:
                configs[nation] = tankmen_module.read_nation_config(source, nation)
            except Exception:
                continue
    finally:
        source.close()
    return configs


__all__ = [
    "ClientLayout",
    "ClientNotFound",
    "client_script_reader",
    "candidate_client_roots",
    "describe_installation",
    "find_client",
    "find_client_script_package",
    "guess_client",
    "load_catalog",
    "load_tankmen_configs",
    "open_client_defs",
    "open_defs",
    "source_description",
]
