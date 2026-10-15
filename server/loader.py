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
from dataclasses import dataclass
from typing import Dict, Optional

from .game import tankmen as tankmen_module
from .game.catalog import Catalog
from .game.defs import DefsError, find_client_script_package, open_defs

#: Directories that hold the game data inside an installation.
ITEM_DEFS_CANDIDATES = (
    os.path.join("res", "scripts", "item_defs"),
    os.path.join("res_mods", "0.9.22.0.1", "scripts", "item_defs"),
    os.path.join("scripts", "item_defs"),
    "item_defs",
)
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
    """Return the layout of the installation at ``root``."""
    if not root or not os.path.isdir(root):
        raise ClientNotFound("no such directory: %r" % (root,))
    item_defs = None
    if _looks_like_item_defs(root):
        item_defs = root
    else:
        for candidate in ITEM_DEFS_CANDIDATES:
            path = os.path.join(root, candidate)
            if os.path.isdir(path) and _looks_like_item_defs(path):
                item_defs = path
                break
    if item_defs is None:
        raise ClientNotFound(
            "%r does not look like a World of Tanks 0.9.22 installation "
            "(no res/scripts/item_defs)" % (root,)
        )
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
    try:
        return open_defs(layout.item_defs)
    except DefsError:
        package = find_client_script_package(client_root)
        if package is None:
            raise
        return open_defs(package)


def source_description(path_or_client: str) -> str:
    if os.path.isfile(path_or_client):
        return "catalog file %s" % path_or_client
    return find_client(path_or_client).item_defs


def load_catalog(path_or_client: str) -> Catalog:
    """Load the catalogue from a client directory or an extracted JSON file."""
    if os.path.isfile(path_or_client):
        from .game.defs import open_json_export

        source = open_json_export(path_or_client)
        return Catalog.load(source)

    source = open_client_defs(path_or_client)
    try:
        return Catalog.load(source)
    finally:
        source.close()


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
    "candidate_client_roots",
    "find_client",
    "guess_client",
    "load_catalog",
    "load_tankmen_configs",
    "open_client_defs",
    "open_defs",
    "source_description",
]
