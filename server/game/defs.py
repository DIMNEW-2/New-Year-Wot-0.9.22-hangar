"""Access to the game client's ``item_defs`` XML files.

The local server never ships game data.  It reads the catalogs out of the
player's own installation, exactly like the client does:

* ``<client>/res/scripts/item_defs/...`` when the scripts are unpacked;
* ``<client>/res/scripts.pkg`` (a ZIP container) otherwise;
* a JSON export produced by ``tools/extract_client_catalog.py`` (portable, used
  by tests and by the bundled demo catalog).

The XML dialect of the client is plain XML with repeated section names, so
``xml.etree.ElementTree`` is enough.  Elements whose text is whitespace-only are
treated as sections, everything else as values.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ElementTree
import zipfile

from . import payload as payload_module

ITEM_DEFS_PREFIXES = (
    "scripts/item_defs/",
    "item_defs/",
    "res/scripts/item_defs/",
    "res_mods/item_defs/",
)


class DefsError(Exception):
    """Raised when the client data cannot be located or parsed."""


class XmlSection:
    """A parsed XML section: values by name plus repeated child sections."""

    __slots__ = ("name", "values", "children", "_by_name")

    def __init__(self, name, values=None, children=None):
        self.name = name
        self.values = values or {}
        self.children = children or []
        self._by_name = None

    def value(self, name, default=None, cast=None):
        raw = self.values.get(name, default)
        if raw is None or cast is None:
            return raw
        return cast(raw)

    def value_int(self, name, default=0):
        raw = self.values.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            try:
                return int(float(raw))
            except ValueError:
                return default

    def value_float(self, name, default=0.0):
        raw = self.values.get(name)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def value_tokens(self, name):
        raw = self.values.get(name, "")
        return raw.split()

    def value_bool(self, name, default=False):
        raw = self.values.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in ("true", "1", "yes")

    def subsections(self, name=None):
        if name is None:
            return list(self.children)
        return [child for child in self.children if child.name == name]

    def subsection(self, name):
        for child in self.children:
            if child.name == name:
                return child
        return None

    def is_section(self, name):
        return self.subsection(name) is not None

    def __iter__(self):
        return iter(self.children)

    def __len__(self):
        return len(self.children)

    def __repr__(self):  # pragma: no cover - debugging helper
        return "<XmlSection %s values=%d children=%d>" % (self.name, len(self.values), len(self.children))


def _build_named(name, element) -> XmlSection:
    """Convert one XML element into a section.

    BigWorld writes a section as ``<name>children</name>``; a leaf keeps its
    payload as text (``<id> 2 </id>``) and a branch may *also* carry text before
    its children (``<chassis> Hummel_1944 <cost>4065</cost></chassis>``), which is
    stored under the ``value`` key so that mixed content survives.

    Every child element is kept as a section (document order preserved) and leaf
    children are additionally exposed through ``values`` for convenience, so a
    caller can treat ``<fuelTanks><Medium>shared</Medium></fuelTanks>`` both as a
    member list and as a name → text mapping.
    """
    values = {}
    children = []
    text = (element.text or "").strip()
    if text:
        values["value"] = text
    for child in element:
        section = _build_named(child.tag, child)
        children.append(section)
        if not section.children and "value" in section.values:
            values.setdefault(child.tag, section.values["value"])
        elif not section.children:
            values.setdefault(child.tag, "")
    return XmlSection(name, values, children)


def _build(element) -> XmlSection:
    return _build_named(element.tag, element)


#: The client's XML dialect declares its namespaces as *elements*
#: (``<xmlns:xmlref>\thttp://bwt/xmlref\t</xmlns:xmlref>``) and then uses the
#: prefix in element names (``<china:Ch05_T34_248>``), which a strict parser
#: refuses.  Both are stripped before parsing, so the customization files
#: (``customization/**``) read exactly like every other file.
_XMLNS_DECLARATION = re.compile(r"<xmlns:[A-Za-z_][\w.-]*\s*>[^<]*</xmlns:[A-Za-z_][\w.-]*\s*>")
_TAG_PREFIX = re.compile(r"(</?)([A-Za-z_][\w.-]*):")


def strip_xml_namespaces(text: str) -> str:
    """Drop the namespace declarations and the element prefixes of WoT XML."""
    if ":" not in text:
        return text
    text = _XMLNS_DECLARATION.sub("", text)
    return _TAG_PREFIX.sub(r"\1", text)


def parse_xml(text: str, origin: str = "") -> XmlSection:
    text = strip_xml_namespaces(text)
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise DefsError("cannot parse XML%s: %s"
                        % (" (%s)" % origin if origin else "", error))
    # The root element repeats the file name in the client data
    # (``<toys.xml>``); its children are the real sections.
    return _build(root)


class DefsSource:
    """Base class: reads ``item_defs`` members by relative path."""

    description = "unknown"

    def json_payload(self):
        """Return the exported catalog when this source is a JSON export."""
        return None

    def read_bytes(self, rel_path: str):
        """The raw bytes of one member, or ``None`` when it does not exist."""
        raise NotImplementedError

    def read_text(self, rel_path: str):
        """One member as text.

        The bytes go through :mod:`server.game.payload` first, so a member that
        a build compressed on its own (or wrote in UTF-16, or in the code page
        of a localized build) reads like any other file.
        """
        data = self.read_bytes(rel_path)
        if data is None:
            return None
        return payload_module.read_payload(data)[0]

    def has(self, rel_path: str) -> bool:
        return self.read_bytes(rel_path) is not None

    def read_section(self, rel_path: str) -> XmlSection:
        """One member parsed as XML.

        A member that is not XML raises a :class:`DefsError` that *describes*
        the payload - the format, the compression wrapper if there was one, the
        encoding and the first bytes - because that message is what a user
        reports and what a maintainer acts on.
        """
        data = self.read_bytes(rel_path)
        if data is None:
            raise DefsError("missing client data file: %s" % rel_path)
        text, _note = payload_module.read_payload(data)
        try:
            return parse_xml(text)
        except DefsError as error:
            raise DefsError("%s\n  parser: %s"
                            % (payload_module.describe(rel_path, data, self.description),
                               error))

    def sniff(self, rel_path: str):
        """A summary of one member (``python3 -m server inspect``)."""
        data = self.read_bytes(rel_path)
        return None if data is None else payload_module.sniff(data)

    def members(self):
        """The relative paths this source can read (may be empty)."""
        return []

    def list_vehicle_files(self, nation: str):
        """Relative paths of ``vehicles/<nation>/*.xml`` (excluding ``list.xml``)."""
        raise NotImplementedError

    def close(self):
        pass


class DirectorySource(DefsSource):
    """Reads an unpacked ``item_defs`` tree."""

    def __init__(self, item_defs_dir):
        self.root = os.path.abspath(item_defs_dir)
        self.description = "directory %s" % self.root

    def read_bytes(self, rel_path):
        candidate = os.path.join(self.root, *rel_path.split("/"))
        if not os.path.isfile(candidate):
            return None
        with open(candidate, "rb") as handle:
            return handle.read()

    def members(self):
        found = []
        for folder, _dirs, names in os.walk(self.root):
            for name in names:
                path = os.path.join(folder, name)
                found.append(os.path.relpath(path, self.root).replace(os.sep, "/"))
        return sorted(found)

    def list_vehicle_files(self, nation):
        folder = os.path.join(self.root, "vehicles", nation)
        if not os.path.isdir(folder):
            return []
        result = []
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".xml") or name.lower() == "list.xml":
                continue
            result.append("vehicles/%s/%s" % (nation, name))
        return result


class ZipSource(DefsSource):
    """Reads ``res/scripts.pkg`` and any archive holding the same layout.

    Both the ``scripts.pkg`` container of the supported build and plain ZIP
    archives of an unpacked ``scripts`` folder are handled: every archive member
    is tried against the known ``item_defs`` prefixes and the archive is indexed
    by its suffix.
    """

    def __init__(self, archive_path):
        self.path = os.path.abspath(archive_path)
        try:
            self._archive = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError) as error:
            raise DefsError("%s is not a readable archive: %s" % (self.path, error))
        self._index = {}
        for info in self._archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").lstrip("/")
            lowered = name.lower()
            for prefix in ITEM_DEFS_PREFIXES:
                if lowered.startswith(prefix):
                    self._index.setdefault(name[len(prefix):], name)
        self.description = "archive %s (%d item_defs entries)" % (self.path, len(self._index))

    def read_bytes(self, rel_path):
        member = self._index.get(rel_path) or self._index.get(rel_path.lower())
        if member is None:
            return None
        try:
            return self._archive.read(member)
        except KeyError:  # pragma: no cover - index drift
            return None
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
            # An entry the archive cannot hand out (a compression method this
            # Python does not implement, a truncated member): the reason is
            # worth more than ``None``, and the caller reports it.
            raise DefsError("cannot read %s from %s: %s" % (member, self.path, error))

    def members(self):
        return sorted(self._index)

    def compression_methods(self) -> dict:
        """``{method: entries}`` of the archive - a diagnostic for ``inspect``."""
        counts = {}
        for info in self._archive.infolist():
            if info.is_dir():
                continue
            counts[info.compress_type] = counts.get(info.compress_type, 0) + 1
        return counts

    def list_vehicle_files(self, nation):
        prefix = "vehicles/%s/" % nation
        lowered_prefix = prefix.lower()
        result = []
        for key in self._index:
            lowered = key.lower()
            if not lowered.startswith(lowered_prefix) or not lowered.endswith(".xml"):
                continue
            if lowered.endswith("/list.xml"):
                continue
            result.append(key)
        return sorted(result)

    def close(self):
        self._archive.close()


class CatalogJsonSource(DefsSource):
    """A portable JSON export of the catalogs (see ``tools/extract_client_catalog.py``)."""

    def __init__(self, payload, origin="<json>"):
        self.payload = payload
        self.description = "json export %s" % origin

    def json_payload(self):
        return self.payload

    def json_payload_placeholder(self):  # pragma: no cover - kept for symmetry
        return self.payload

    def read_bytes(self, rel_path):
        return None

    def list_vehicle_files(self, nation):
        return []


def _looks_like_item_defs(path) -> bool:
    if not os.path.isdir(path):
        return False
    return os.path.isdir(os.path.join(path, "new_year")) and os.path.isdir(os.path.join(path, "vehicles"))


def open_json_export(path) -> CatalogJsonSource:
    """Open a JSON catalog export (``catalog.json`` of the export tool)."""
    import json as _json

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = _json.load(handle)
    except (OSError, ValueError) as error:
        raise DefsError("cannot read JSON catalog %s: %s" % (path, error))
    if not isinstance(payload, dict) or "newYear" not in payload:
        raise DefsError("%s is not a catalog export (no 'newYear' section)" % path)
    return CatalogJsonSource(payload, origin=os.path.abspath(path))


def open_defs(source, client_dir=None):
    """Build a :class:`DefsSource` from a user supplied path.

    ``source`` may be the client root, a ``scripts`` folder, an ``item_defs``
    folder or an archive.  ``client_dir`` is used when ``source`` is a relative
    ``item_defs`` path.
    """
    if source is None:
        raise DefsError("no client data source given")

    source = os.path.abspath(os.path.expanduser(source))
    if os.path.isdir(source):
        candidates = [
            source,
            os.path.join(source, "item_defs"),
            os.path.join(source, "data", "item_defs"),
            os.path.join(source, "res", "scripts", "item_defs"),
            os.path.join(source, "scripts", "item_defs"),
        ]
        for candidate in candidates:
            if _looks_like_item_defs(candidate):
                return DirectorySource(candidate)
        raise DefsError(
            "no item_defs folder found under %s (looked in: %s)" % (source, ", ".join(candidates))
        )
    if os.path.isfile(source):
        return ZipSource(source)
    raise DefsError("client data source does not exist: %s" % source)


def find_client_script_package(client_dir):
    """Return the first readable ``scripts.pkg``-like archive of a client install."""
    if not client_dir:
        return None
    candidates = [
        os.path.join(client_dir, "res", "scripts.pkg"),
        os.path.join(client_dir, "res", "scripts.zip"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None
