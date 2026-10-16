#!/usr/bin/env python3
"""Check the tables the server mirrors against the client's own sources.

The New Year event is a *rule heavy* feature: the client renders the tree, the
collection, the crafting prices and the collection bonuses from the tables of
``scripts/common/items/new_year_types.py``, while the server has to make the
very same decisions (how many fragments a toy of rank *n* gives, what a craft
costs, which atmosphere level a rank is worth, what a chest level means).  If
the two disagree the interface shows one number and the server acts on another,
which is exactly the class of bug that is impossible to see without a running
client.

So the tables are mirrored in :mod:`server.game.ny_constants` and this tool
compares every mirrored entry with the value the client's own source computes.
The appearance items are a *format heavy* feature: the outfit descriptor the
client sends with ``CMD_VEH_APPLY_OUTFIT`` is a varint packed component tree
(``common/items/customizations.py``) and the server has to read and write exactly
that format.  The same tool therefore also compares the mirrored component
tables (:mod:`server.game.c11n_outfit`, :mod:`server.game.c11n_constants`) with
the client's own classes - the ``customType``, the field names in order and the
default value of every field.

The client files are Python 2, but their tables are literals and comprehensions,
so they can be read with ``ast`` and evaluated table by table without a Python 2
interpreter; ``iteritems()`` of the client's dictionaries is shimmed.

Usage::

    python3 tools/check_client_assets.py --client /path/to/World_of_Tanks_CN
    python3 tools/check_client_assets.py --client res/scripts      # unpacked

The exit status is ``0`` when nothing disagrees, ``1`` otherwise (a missing
client source is reported as a failure so that a run over a half unpacked
installation is never silently green).  ``tests/test_client_assets.py`` runs the
same check when a client is available.
"""

from __future__ import annotations

import argparse
import ast
import collections
import functools
import os
import re
import sys
import zipfile
from itertools import chain, product
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import loader  # noqa: E402
from server.game import c11n_constants as c11nc  # noqa: E402
from server.game import c11n_outfit as c11nout  # noqa: E402
from server.game import ny_constants as nyc  # noqa: E402
from server.game import customization as c11n  # noqa: E402

#: The client's ``new_year_types.py`` and the nation table its setting tables
#: are keyed by.
NEW_YEAR_TYPES = "common/items/new_year_types.py"
NATIONS = "common/nations.py"
C11N_CONSTANTS = "common/items/components/c11n_constants.py"
C11N_COMPONENTS = "common/items/customizations.py"

#: ``server name`` -> ``client name``.  The server spells its "internal"
#: constants without the leading underscore the client uses.
_NAME_MAP = {
    "MAX_TOY_ID": "_MAX_TOY_ID",
    "MAX_ATMOSPHERE_LEVEL": "_MAX_ATMOSPHERE_LEVEL",
    "BONUS_FACTOR_BY_ATMOSPHERE_LEVEL": "_BONUS_FACTOR_BY_ATMOSPHERE_LEVEL",
    "MAX_COLLECTION_LEVEL": "_MAX_COLLECTION_LEVEL",
}

#: Module level tables to compare, in the order they are reported.
_MODULE_TABLES = (
    "NATIONAL_SETTINGS",
    "NATIONAL_SETTINGS_IDS_BY_NAME",
    "NATIONS_BY_SETTING",
    "SETTING_BY_NATION",
    "SETTING_BY_NATION_ID",
    "SETTING_ID_BY_NATION_ID",
    "TOY_TYPES",
    "TOY_TYPES_IDS_BY_NAME",
    "MAX_TOY_RANK",
    "INVALID_TOY_ID",
    "MAX_CHEST_LEVEL",
    "TOY_COLLECTION_BYTES",
    "MAX_TOY_ID",
    "TOY_ATMOSPHERE_BY_RANK",
    "BOUND_ATMOSPHERE_BY_LEVEL",
    "BONUS_FACTOR_BY_ATMOSPHERE_LEVEL",
    "MAX_ATMOSPHERE_LEVEL",
    "TOY_RATING_BY_RANK",
    "BOUND_COLLECTION_RATING_BY_LEVEL",
    "MAX_COLLECTION_LEVEL",
    "COLLECTION_BONUSES_BY_LEVEL",
)

#: Tables the client computes from the nation indices it imports.
_NEEDS_NATIONS = ("SETTING_BY_NATION_ID", "SETTING_ID_BY_NATION_ID")

#: Class attributes to compare, ``class name`` -> attribute names.
_CLASS_TABLES = {
    "CRAFT": (
        "FRAGMENTS_BY_RANK",
        "COST_BY_RANK",
        "RANDOM_TOY_COST",
        "NATIONAL_SETTING_COST_FACTOR",
        "TYPE_COST_FACTOR",
        "PROBABILITY_FOR_RANK",
        "PROBABILITY_FOR_SETTING",
        "PROBABILITY_FOR_SETTING_ID",
        "PROBABILITY_FOR_TYPE",
        "PROBABILITY_FOR_TYPE_ID",
    ),
    "NY_STATE": ("NOT_STARTED", "IN_PROGRESS", "SUSPENDED", "FINISHED", "ENABLED", "ALL"),
}


class Py2Dict(dict):
    """A ``dict`` that also answers the Python 2 iterations of the client."""

    def iteritems(self):
        return iter(self.items())

    def iterkeys(self):
        return iter(self.keys())

    def itervalues(self):
        return iter(self.values())

    def __iter__(self):
        return iter(self.keys())


def _wrap(value):
    """Wrap dictionaries so that ``.iteritems()`` of a Python 2 file works."""
    return Py2Dict(value) if isinstance(value, dict) else value


def read_scripts_text(layout: loader.ClientLayout, rel_path: str):
    """Read ``rel_path`` from an unpacked ``scripts`` folder or ``scripts.pkg``."""
    scripts = layout.scripts
    if scripts and os.path.isdir(scripts):
        candidate = os.path.join(scripts, *rel_path.split("/"))
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        return None
    if scripts and os.path.isfile(scripts):
        suffix = "/" + rel_path.lower()
        try:
            with zipfile.ZipFile(scripts) as archive:
                for info in archive.infolist():
                    name = info.filename.replace("\\", "/").lower().lstrip("/")
                    if name.endswith(suffix) or name == rel_path.lower():
                        return archive.read(info.filename).decode("utf-8", "replace")
        except (zipfile.BadZipFile, OSError, RuntimeError):
            return None
    return None


#: Long integer literals (``123L``) are valid Python 2 but not Python 3, and the
#: client files use them for the hash constants of the customization components.
_LONG_LITERAL = re.compile(r"\b(\d+)L\b")


def py2_source(source: str) -> str:
    """The client's source as something Python 3 can parse."""
    return _LONG_LITERAL.sub(r"\1", source)


def evaluate_client_module(source: str, module_name: str = "<client>", extra=None):
    """Evaluate the literal tables of a Python 2 module.

    Only assignments whose right hand side is a literal expression (tuples,
    dictionaries, comprehensions over the names defined before them) are
    evaluated; everything else - functions, classes with methods, descriptors -
    is ignored.  A table that cannot be evaluated is simply absent from the
    result, and the caller reports it.  ``extra`` prepends names the module
    expects from its imports (the nation table of ``common/nations.py``).
    """
    tree = ast.parse(py2_source(source))
    namespace = {
        "__builtins__": {},
        "chain": chain,
        "product": product,
        "enumerate": enumerate,
        "dict": dict,
        "tuple": tuple,
        "list": list,
        "len": len,
        "min": min,
        "max": max,
        "namedtuple": collections.namedtuple,
        "SimpleNamespace": SimpleNamespace,
        # ``new_year_types`` imports ``nations`` and reads its indices; a table
        # without a nation key still evaluates with an empty namespace.
        "nations": SimpleNamespace(NAMES=(), INDICES={}),
        "random": SimpleNamespace(),
        "ResMgr": SimpleNamespace(),
    }
    for name, value in (extra or {}).items():
        namespace[name] = value
    tables = {}

    def evaluate(node, scope=None):
        scope = namespace if scope is None else scope
        expression = ast.Expression(body=node)
        ast.fix_missing_locations(expression)
        code = compile(expression, module_name, "eval")
        return eval(code, dict(scope), scope)  # noqa: S307 - client data only

    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            try:
                value = evaluate(statement.value)
            except Exception:  # noqa: BLE001 - unevaluable tables are reported by their absence
                continue
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    namespace[target.id] = _wrap(value)
                    tables[target.id] = value
            continue
        if isinstance(statement, ast.ClassDef):
            # A class body reads its *own* earlier attributes as plain names
            # (``SeasonType.ALL = WINTER | SUMMER | ...``), so the body is
            # evaluated in a scope of its own that starts as a copy of the
            # module one - and the class itself is published as an object, which
            # is how ``customArrayField(PaintComponent.customType)`` of the
            # outfit resolves its field type.
            scope = dict(namespace)
            attributes = {}
            for inner in statement.body:
                if not isinstance(inner, ast.Assign):
                    continue
                try:
                    value = evaluate(inner.value, scope)
                except Exception:  # noqa: BLE001
                    continue
                for target in inner.targets:
                    if isinstance(target, ast.Name):
                        scope[target.id] = _wrap(value)
                        attributes[target.id] = value
            namespace[statement.name] = SimpleNamespace(
                **{name: _wrap(value) for name, value in attributes.items()})
            tables[statement.name] = attributes
    return tables


def _comparable(value):
    """Tuples of the client's own named tuples compare as tuples."""
    if isinstance(value, tuple):
        if hasattr(value, "_fields"):  # namedtuple
            return tuple(_comparable(item) for item in value)
        return tuple(_comparable(item) for item in value)
    if isinstance(value, dict):
        return {key: _comparable(item) for key, item in value.items()}
    return value


def _note_for_missing(name: str, nations) -> str:
    """Explain a table the client file has but that could not be evaluated."""
    if name in _NEEDS_NATIONS and not nations:
        return "needs common/nations.py, which this installation does not ship"
    return "the client file has no %s" % name


def compare_tables(client: dict, report, nations=None) -> int:
    """Compare the mirrored tables; returns the number of disagreements."""
    problems = 0
    for name in _MODULE_TABLES:
        client_name = _NAME_MAP.get(name, name)
        if client_name not in client:
            report("MISS", name, _note_for_missing(name, nations))
            problems += 1
            continue
        theirs = _comparable(client[client_name])
        ours = _comparable(getattr(nyc, name))
        if theirs != ours:
            report("FAIL", name, "server %r != client %r" % (ours, theirs))
            problems += 1
            continue
        report("ok", name, "")
    for class_name, attributes in _CLASS_TABLES.items():
        client_class = client.get(class_name)
        if not isinstance(client_class, dict):
            report("MISS", class_name, "the client file has no class %s" % class_name)
            problems += 1
            continue
        server_class = getattr(nyc, class_name)
        for attribute in attributes:
            if attribute not in client_class:
                report("MISS", "%s.%s" % (class_name, attribute), "not in the client file")
                problems += 1
                continue
            theirs = _comparable(client_class[attribute])
            ours = _comparable(getattr(server_class, attribute))
            if theirs != ours:
                report("FAIL", "%s.%s" % (class_name, attribute),
                       "server %r != client %r" % (ours, theirs))
                problems += 1
                continue
            report("ok", "%s.%s" % (class_name, attribute), "")
    # The named tuple of the collection bonuses (field names matter to the
    # client's ``CollectionBonus`` readers).
    client_fields = getattr(client.get("CollectionBonus"), "_fields", None)
    if client_fields is not None and tuple(nyc.CollectionBonus._fields) != tuple(client_fields):
        report("FAIL", "CollectionBonus", "server fields %r != client %r"
               % (tuple(nyc.CollectionBonus._fields), tuple(client_fields)))
        problems += 1
    elif client_fields is not None:
        report("ok", "CollectionBonus", "")
    return problems


def compare_nations(client: dict, report) -> int:
    """The event keys its settings by the client's nation indices."""
    names = client.get("NAMES")
    if names is None or "INDICES" not in client:
        report("MISS", "nations.NAMES", "the client's nations module has no NAMES/INDICES")
        return 1
    names = tuple(names)
    if names != tuple(nyc.NATIONS):
        report("FAIL", "nations.NAMES",
               "server %r != client %r" % (tuple(nyc.NATIONS), names))
        return 1
    indices = dict(client.get("INDICES") or {})
    if indices and indices != dict(nyc.NATION_INDICES):
        report("FAIL", "nations.INDICES",
               "server %r != client %r" % (dict(nyc.NATION_INDICES), indices))
        return 1
    report("ok", "nations.NAMES", "")
    if indices:
        report("ok", "nations.INDICES", "")
    return 0


# ---------------------------------------------------------------------------
# the customization format
# ---------------------------------------------------------------------------
#: ``cType -> server spec`` compared field by field with the client's classes.
_COMPONENT_SPECS = (
    ("PaintComponent", c11nout.CUSTOM_TYPE_PAINT),
    ("CamouflageComponent", c11nout.CUSTOM_TYPE_CAMOUFLAGE),
    ("DecalComponent", c11nout.CUSTOM_TYPE_DECAL),
    ("CustomizationOutfit", c11nout.CUSTOM_TYPE_OUTFIT),
)

#: ``CustomizationType`` / ``ApplyArea`` / ``SeasonType`` members the server
#: mirrors.  The names are the client's, the values have to be identical.
_CONSTANT_CLASSES = (
    ("CustomizationType", c11nc.CustomizationType,
     ("PAINT", "CAMOUFLAGE", "DECAL", "STYLE", "MODIFICATION", "ITEM_GROUP",
      "RANGE", "_SIMPLE_TYPES")),
    ("ApplyArea", c11nc.ApplyArea,
     ("NONE", "CHASSIS", "CHASSIS_1", "CHASSIS_2", "CHASSIS_3", "HULL", "HULL_1",
      "HULL_2", "HULL_3", "TURRET", "TURRET_1", "TURRET_2", "TURRET_3", "GUN",
      "GUN_1", "GUN_2", "GUN_3", "ALL",
      "DECAL_REGIONS_VALUE", "CAMOUFLAGE_REGIONS_VALUE", "EMBLEM_REGIONS_VALUE",
      "INSCRIPTION_REGIONS_VALUE", "USER_PAINT_ALLOWED_REGIONS_VALUE",
      "RANGE", "USER_PAINT_ALLOWED_REGIONS", "DECAL_REGIONS", "CAMOUFLAGE_REGIONS",
      "EMBLEM_REGIONS", "INSCRIPTION_REGIONS", "PAINT_REGIONS")),
    ("SeasonType", c11nc.SeasonType,
     ("UNDEFINED", "WINTER", "SUMMER", "DESERT", "EVENT", "ALL", "RANGE",
      "SEASONS", "COMMON_SEASONS")),
    ("DecalType", c11nc.DecalType, ("EMBLEM", "INSCRIPTION")),
    ("ModificationType", c11nc.ModificationType,
     ("UNDEFINED", "PAINT_AGE", "CAMOUFLAGE_AGE", "PAINT_FADING", "GLOSS", "METALLIC")),
    ("ItemTags", c11nc.ItemTags, ("VEHICLE_BOUND", "PREMIUM_IGR", "IGR", "RARE")),
)

#: ``server name -> client name`` of the module level constants.
_C11N_MODULE_CONSTANTS = {
    "RENT_DEFAULT_BATTLES": "RENT_DEFAULT_BATTLES",
    "MAX_OUTFIT_LENGTH": "MAX_OUTFIT_LENGTH",
    "MAX_CAMOUFLAGE_PATTERN_SIZE": "MAX_CAMOUFLAGE_PATTERN_SIZE",
    "MAX_ITEMS_FOR_BUY_OPERATION": "MAX_ITEMS_FOR_BUY_OPERATION",
}


def _c11n_namespace(extra=None):
    """The names the client's customization modules import from ``items``."""
    namespace = {
        "items": SimpleNamespace(
            makeIntCompactDescrByID=lambda *args: 0,
            CustomizationOutfit=object,
        ),
        "constants": SimpleNamespace(IS_CELLAPP=False, IS_BASEAPP=False),
        "IS_CLIENT": True,
        "IS_CELLAPP": False,
        "IS_BASEAPP": False,
        "IS_WEB": False,
        "OrderedDict": collections.OrderedDict,
        "namedtuple": collections.namedtuple,
        "reduce": functools.reduce,
        "int": int,
        "ApplyArea": c11nc.ApplyArea,
        "SeasonType": c11nc.SeasonType,
        "CustomizationType": c11nc.CustomizationType,
        "DecalType": c11nc.DecalType,
        "ModificationType": c11nc.ModificationType,
        "ItemTags": c11nc.ItemTags,
        # The field constructors of ``common/items/customizations.py``.
        "intField": lambda default=0: c11nout.FieldSpec("", "", c11nout.FIELD_VARINT, default),
        "intArrayField": lambda default=None: c11nout.FieldSpec(
            "", "", c11nout.FIELD_TYPED_ARRAY | c11nout.FIELD_VARINT, default or [],
            item_kind=c11nout.FIELD_VARINT),
        "customArrayField": lambda custom_type, default=None: c11nout.FieldSpec(
            "", "", c11nout.FIELD_TYPED_ARRAY | c11nout.FIELD_CUSTOM_OFFSET * custom_type,
            default or [], item_kind=c11nout.FIELD_CUSTOM_OFFSET * custom_type),
        "customFieldType": lambda custom_type: c11nout.FieldSpec(
            "", "", c11nout.FIELD_CUSTOM_OFFSET * custom_type, None),
        "arrayField": lambda item_type, default=None: c11nout.FieldSpec(
            "", "", c11nout.FIELD_TYPED_ARRAY | item_type, default or []),
        "FieldTypes": SimpleNamespace(VARINT=c11nout.FIELD_VARINT,
                                      CUSTOM_TYPE_OFFSET=c11nout.FIELD_CUSTOM_OFFSET,
                                      TYPED_ARRAY=c11nout.FIELD_TYPED_ARRAY),
    }
    namespace.update(extra or {})
    return namespace


def _fields_of(client_class) -> list:
    """The client's ``fields`` as ``(name, kind, default)`` in declaration order."""
    fields = client_class.get("fields")
    if fields is None:
        return None
    if isinstance(fields, dict):
        entries = list(fields.items())
    else:
        entries = [(entry.name, entry) for entry in fields]
    result = []
    for name, spec in entries:
        if isinstance(spec, c11nout.FieldSpec):
            result.append((name, spec.kind, spec.default, spec.item_kind))
        elif isinstance(spec, (tuple, list)) and len(spec) >= 3:
            # ``FieldType(type, default, deprecated)``
            result.append((name, spec[0], spec[1], 0))
        else:
            return None
    return result


def compare_customization(constants: dict, components: dict, report) -> int:
    """Compare the mirrored customization constants and component fields."""
    problems = 0
    for name, server_class, members in _CONSTANT_CLASSES:
        client_class = constants.get(name)
        if not isinstance(client_class, dict):
            report("MISS", name, "the client file has no class %s" % name)
            problems += 1
            continue
        for member in members:
            if member not in client_class:
                report("MISS", "%s.%s" % (name, member), "not in the client file")
                problems += 1
                continue
            theirs = _comparable(client_class[member])
            ours = getattr(server_class, member)
            if isinstance(ours, (tuple, set)) or isinstance(theirs, (tuple, set)):
                theirs, ours = set(theirs), set(ours)
            if theirs != ours:
                report("FAIL", "%s.%s" % (name, member),
                       "server %r != client %r" % (ours, theirs))
                problems += 1
    if not problems:
        report("ok", "CustomizationType/ApplyArea/SeasonType", "")
    for name, client_name in _C11N_MODULE_CONSTANTS.items():
        if client_name not in constants:
            report("MISS", name, "the client file has no %s" % client_name)
            problems += 1
            continue
        theirs = constants[client_name]
        ours = getattr(c11nc, name)
        if theirs != ours:
            report("FAIL", name, "server %r != client %r" % (ours, theirs))
            problems += 1
    for class_name, custom_type in _COMPONENT_SPECS:
        client_class = components.get(class_name)
        if not isinstance(client_class, dict):
            report("MISS", class_name, "the client file has no class %s" % class_name)
            problems += 1
            continue
        server_spec = c11nout.SPECS[custom_type]
        if client_class.get("customType") != server_spec.custom_type:
            report("FAIL", "%s.customType" % class_name,
                   "server %r != client %r" % (server_spec.custom_type,
                                               client_class.get("customType")))
            problems += 1
            continue
        theirs = _fields_of(client_class)
        if theirs is None:
            report("MISS", "%s.fields" % class_name, "the client's fields are not literal")
            problems += 1
            continue
        ours = [(spec.name, spec.kind, spec.default, spec.item_kind)
                for spec in server_spec.fields]
        if len(theirs) != len(ours):
            report("FAIL", "%s.fields" % class_name,
                   "server has %d fields, client %d" % (len(ours), len(theirs)))
            problems += 1
            continue
        for (theirs_name, theirs_kind, theirs_default, theirs_item), \
                (ours_name, ours_kind, ours_default, ours_item) in zip(theirs, ours):
            if theirs_name != ours_name or theirs_kind != ours_kind:
                report("FAIL", "%s.%s" % (class_name, ours_name),
                       "server %r (kind %r), client %r (kind %r)"
                       % (ours_name, ours_kind, theirs_name, theirs_kind))
                problems += 1
                continue
            if theirs_item != ours_item:
                report("FAIL", "%s.%s item type" % (class_name, ours_name),
                       "server %r != client %r" % (ours_item, theirs_item))
                problems += 1
                continue
            if theirs_default != ours_default:
                report("FAIL", "%s.%s default" % (class_name, ours_name),
                       "server %r != client %r" % (ours_default, theirs_default))
                problems += 1
        report("ok", "%s.fields" % class_name, "")
    # The outfits of a style are read from the XML with the same field names.
    for field_name in ("modifications", "paints", "camouflages", "decals", "styleId"):
        if field_name not in ("id", "appliedTo", "patternSize", "palette") and \
                field_name not in [spec.name for spec in c11nout.SPECS[c11nout.CUSTOM_TYPE_OUTFIT].fields]:
            report("FAIL", "CustomizationOutfit.%s" % field_name, "missing on the server")
            problems += 1
    return problems


def check(client_path: str, stream=sys.stdout) -> int:
    """Compare the mirrored tables with the client at ``client_path``."""
    layout = loader.find_client(client_path)
    stream.write("client: %s\n" % layout.root)
    stream.write("scripts: %s\n" % layout.scripts)

    def report(status: str, name: str, note: str) -> None:
        line = "  %-5s %s" % (status, name)
        if note:
            line += " - " + note
        stream.write(line + "\n")

    problems = 0
    nation_source = read_scripts_text(layout, NATIONS)
    nations = None
    if nation_source is None:
        report("MISS", NATIONS, "the installation does not ship the client's sources")
        problems += 1
    else:
        nations = evaluate_client_module(nation_source, NATIONS)
        problems += compare_nations(nations, report)
    # ``new_year_types`` keys its setting tables by the nation indices it
    # imports, so the nation module has to be evaluated first.
    source = read_scripts_text(layout, NEW_YEAR_TYPES)
    if source is None:
        report("MISS", NEW_YEAR_TYPES, "the installation does not ship the client's sources")
        problems += 1
    else:
        extra = {}
        if nations:
            extra["nations"] = SimpleNamespace(
                NAMES=_wrap(nations.get("NAMES", ())),
                INDICES=_wrap(nations.get("INDICES", {})),
            )
        tables = evaluate_client_module(source, NEW_YEAR_TYPES, extra=extra)
        problems += compare_tables(tables, report, nations=nations)
    # The customization constants and the outfit descriptor format.
    constants_source = read_scripts_text(layout, C11N_CONSTANTS)
    if constants_source is None:
        report("MISS", C11N_CONSTANTS, "the installation does not ship the client's sources")
        problems += 1
        constants_tables = {}
    else:
        constants_tables = evaluate_client_module(constants_source, C11N_CONSTANTS,
                                                  extra=_c11n_namespace())
    components_source = read_scripts_text(layout, C11N_COMPONENTS)
    if components_source is None:
        report("MISS", C11N_COMPONENTS, "the installation does not ship the client's sources")
        problems += 1
        component_tables = {}
    else:
        component_tables = evaluate_client_module(components_source, C11N_COMPONENTS,
                                                  extra=_c11n_namespace())
    if constants_tables or component_tables:
        problems += compare_customization(constants_tables, component_tables, report)
    if problems:
        stream.write("check_client_assets: %d disagreement(s)\n" % problems)
    else:
        stream.write("check_client_assets: all mirrored tables agree with the client\n")
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", default=None,
                        help="the game folder, its res/scripts folder or an item_defs tree")
    arguments = parser.parse_args(argv)
    client = arguments.client or loader.guess_client()
    if not client:
        parser.error("no client given and none found; pass --client <path> to the game folder")
    try:
        return check(client)
    except loader.ClientNotFound as error:
        print("check_client_assets: %s" % error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
