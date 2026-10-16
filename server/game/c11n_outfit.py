"""The outfit descriptor: what the client puts on a tank, in its own binary form.

An *outfit* is everything a player has mounted on one vehicle - the paint of each
region, a camouflage with its pattern size and palette, decals, a modification
and, for a style, the id of the style.  The client serializes it with a small
protobuf-like format (``common/items/customizations.py``) and sends it as a
*string* (``Shop.buyAndEquipOutfit`` -> ``CMD_VEH_APPLY_OUTFIT``), so the local
server has to speak exactly that format both ways:

* :func:`parse_comp_descr` reads the descriptor of a request (which items the
  outfit needs, so they can be charged for and taken out of the depot),
* :func:`make_comp_descr` writes the descriptor of a style, which is what the
  ``OUTFITS`` section of the inventory publishes to the client.

The format
==========

Every component is ``varint(customType)`` followed by ``varint(hasValue)`` and
the values of the fields whose bit is set in ``hasValue`` (bit 0 is the first
field, the field order never changes).  Only non-default fields travel, arrays
are ``varint(len)`` plus their items, and an item of an array is *not* prefixed
with its type - the field itself already knows it::

    outfit   := varint(4) varint(flags) [body…]      flags: 1 modifications, 2 paints,
                                                            4 camouflages, 8 decals,
                                                            16 styleId
    paint    := varint(flags) [id] [appliedTo]       customType 1
    camo     := varint(flags) [id] [patternSize] [appliedTo] [palette]   customType 2
    decal    := varint(flags) [id] [appliedTo]       customType 3

The defaults matter as much as the fields (a paint covers the paintable regions
unless it says otherwise, a camouflage covers hull, turret and gun), because a
value equal to the default is *not* written - which is why the reader has to
start every component from the same defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import c11n_constants as cc
from . import items as items_module

#: ``serialization customType`` of every component of an outfit.
CUSTOM_TYPE_PAINT = 1
CUSTOM_TYPE_CAMOUFLAGE = 2
CUSTOM_TYPE_DECAL = 3
CUSTOM_TYPE_OUTFIT = 4

#: ``FieldTypes`` of the client's serializer.
FIELD_VARINT = 2
FIELD_CUSTOM_OFFSET = 128
FIELD_TYPED_ARRAY = 64


class OutfitError(Exception):
    """A descriptor that cannot be read (or that is not an outfit at all)."""


# ---------------------------------------------------------------------------
# varints
# ---------------------------------------------------------------------------
def encode_varint(value: int) -> bytes:
    """``varint.encode`` - little endian, seven bits per byte, high bit = more."""
    value = int(value)
    if value < 0:
        raise OutfitError("varint cannot be negative: %r" % (value,))
    out = bytearray()
    while True:
        chunk = value & 127
        value >>= 7
        if value:
            out.append(chunk | 128)
        else:
            out.append(chunk)
            break
    return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> Tuple[int, int]:
    """``varint.decode_stream``: returns ``(value, offset after it)``."""
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise OutfitError("unexpected end of the descriptor while reading a varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 127) << shift
        shift += 7
        if not byte & 128:
            return result, offset
        if shift > 70:
            raise OutfitError("varint is too long")


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------
@dataclass
class Paint:
    """One paint on the regions of ``applied_to``."""

    id: int = 0
    applied_to: int = cc.ApplyArea.USER_PAINT_ALLOWED_REGIONS_VALUE


@dataclass
class Camouflage:
    """One camouflage, with the pattern size and palette the player chose."""

    id: int = 0
    pattern_size: int = 1
    applied_to: int = cc.ApplyArea.CAMOUFLAGE_REGIONS_VALUE
    palette: int = 0


@dataclass
class Decal:
    """One emblem or inscription (``id`` is an id of the shared decal type)."""

    id: int = 0
    applied_to: int = cc.ApplyArea.NONE


@dataclass
class Outfit:
    """The complete appearance of one vehicle."""

    modifications: List[int] = field(default_factory=list)
    paints: List[Paint] = field(default_factory=list)
    camouflages: List[Camouflage] = field(default_factory=list)
    decals: List[Decal] = field(default_factory=list)
    #: ``styleId``: the style this outfit is the outfit *of*, ``0`` for a
    #: hand-made outfit.
    style_id: int = 0

    def __bool__(self) -> bool:
        return bool(self.modifications or self.paints or self.camouflages
                    or self.decals or self.style_id)

    def is_empty(self) -> bool:
        return not self

    def as_dict(self) -> dict:
        """The outfit as plain data (used by the account file and the tests)."""
        return {
            "styleId": int(self.style_id),
            "modifications": [int(value) for value in self.modifications],
            "paints": [{"id": int(p.id), "appliedTo": int(p.applied_to)}
                       for p in self.paints],
            "camouflages": [{"id": int(c.id), "patternSize": int(c.pattern_size),
                             "appliedTo": int(c.applied_to), "palette": int(c.palette)}
                            for c in self.camouflages],
            "decals": [{"id": int(d.id), "appliedTo": int(d.applied_to)}
                       for d in self.decals],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Outfit":
        payload = payload or {}
        return cls(
            modifications=[int(value) for value in payload.get("modifications") or ()],
            paints=[Paint(id=int(entry.get("id", 0)),
                          applied_to=int(entry.get("appliedTo", cc.ApplyArea.USER_PAINT_ALLOWED_REGIONS_VALUE)))
                    for entry in payload.get("paints") or ()],
            camouflages=[Camouflage(id=int(entry.get("id", 0)),
                                    pattern_size=int(entry.get("patternSize", 1)),
                                    applied_to=int(entry.get("appliedTo", cc.ApplyArea.CAMOUFLAGE_REGIONS_VALUE)),
                                    palette=int(entry.get("palette", 0)))
                         for entry in payload.get("camouflages") or ()],
            decals=[Decal(id=int(entry.get("id", 0)),
                          applied_to=int(entry.get("appliedTo", cc.ApplyArea.NONE)))
                    for entry in payload.get("decals") or ()],
            style_id=int(payload.get("styleId", 0) or 0),
        )


@dataclass(frozen=True)
class FieldSpec:
    """One field of a component: its name, its kind and its default value."""

    name: str
    attr: str
    kind: int
    default: object
    #: For an array field: the type of its items (varint or a custom type).
    item_kind: int = 0


@dataclass(frozen=True)
class ComponentSpec:
    """One serializable component: its ``customType`` and its fields, in order."""

    custom_type: int
    cls: type
    fields: Tuple[FieldSpec, ...]


_PAINT_SPEC = ComponentSpec(
    CUSTOM_TYPE_PAINT,
    Paint,
    (
        FieldSpec("id", "id", FIELD_VARINT, 0),
        FieldSpec("appliedTo", "applied_to", FIELD_VARINT,
                  cc.ApplyArea.USER_PAINT_ALLOWED_REGIONS_VALUE),
    ),
)
_CAMOUFLAGE_SPEC = ComponentSpec(
    CUSTOM_TYPE_CAMOUFLAGE,
    Camouflage,
    (
        FieldSpec("id", "id", FIELD_VARINT, 0),
        FieldSpec("patternSize", "pattern_size", FIELD_VARINT, 1),
        FieldSpec("appliedTo", "applied_to", FIELD_VARINT,
                  cc.ApplyArea.CAMOUFLAGE_REGIONS_VALUE),
        FieldSpec("palette", "palette", FIELD_VARINT, 0),
    ),
)
_DECAL_SPEC = ComponentSpec(
    CUSTOM_TYPE_DECAL,
    Decal,
    (
        FieldSpec("id", "id", FIELD_VARINT, 0),
        FieldSpec("appliedTo", "applied_to", FIELD_VARINT, cc.ApplyArea.NONE),
    ),
)
_OUTFIT_SPEC = ComponentSpec(
    CUSTOM_TYPE_OUTFIT,
    Outfit,
    (
        FieldSpec("modifications", "modifications", FIELD_TYPED_ARRAY | FIELD_VARINT, [],
                  item_kind=FIELD_VARINT),
        FieldSpec("paints", "paints", FIELD_TYPED_ARRAY | FIELD_CUSTOM_OFFSET * CUSTOM_TYPE_PAINT,
                  [], item_kind=FIELD_CUSTOM_OFFSET * CUSTOM_TYPE_PAINT),
        FieldSpec("camouflages", "camouflages",
                  FIELD_TYPED_ARRAY | FIELD_CUSTOM_OFFSET * CUSTOM_TYPE_CAMOUFLAGE, [],
                  item_kind=FIELD_CUSTOM_OFFSET * CUSTOM_TYPE_CAMOUFLAGE),
        FieldSpec("decals", "decals", FIELD_TYPED_ARRAY | FIELD_CUSTOM_OFFSET * CUSTOM_TYPE_DECAL,
                  [], item_kind=FIELD_CUSTOM_OFFSET * CUSTOM_TYPE_DECAL),
        FieldSpec("styleId", "style_id", FIELD_VARINT, 0),
    ),
)

#: ``_CUSTOMIZATION_CLASSES`` of the client, keyed by ``customType``.
SPECS: Dict[int, ComponentSpec] = {
    spec.custom_type: spec for spec in (_PAINT_SPEC, _CAMOUFLAGE_SPEC, _DECAL_SPEC, _OUTFIT_SPEC)
}


# ---------------------------------------------------------------------------
# writing a descriptor
# ---------------------------------------------------------------------------
def _spec_for(item_kind: int) -> ComponentSpec:
    custom_type = int(item_kind) // FIELD_CUSTOM_OFFSET
    spec = SPECS.get(custom_type)
    if spec is None:
        raise OutfitError("unknown component type %r" % (custom_type,))
    return spec


def _serialize_component(spec: ComponentSpec, component) -> bytes:
    """``ComponentBinSerializer.__serializeCustomType`` - without the type prefix."""
    has_value = 0
    offset = 1
    parts: List[bytes] = []
    for field_spec in spec.fields:
        value = getattr(component, field_spec.attr)
        if value != field_spec.default:
            has_value |= offset
            parts.append(_serialize_value(value, field_spec))
        offset <<= 1
    return encode_varint(has_value) + b"".join(parts)


def _serialize_value(value, field_spec: FieldSpec) -> bytes:
    kind = field_spec.kind
    if kind == FIELD_VARINT:
        return encode_varint(int(value))
    if kind & FIELD_TYPED_ARRAY:
        items = [_serialize_item(item, field_spec.item_kind) for item in value]
        return encode_varint(len(items)) + b"".join(items)
    if kind >= FIELD_CUSTOM_OFFSET:
        return _serialize_component(_spec_for(kind), value)
    raise OutfitError("unsupported field type %r" % (kind,))


def _serialize_item(item, item_kind: int) -> bytes:
    if item_kind == FIELD_VARINT:
        return encode_varint(int(item))
    return _serialize_component(_spec_for(item_kind), item)


def make_comp_descr(outfit: Outfit) -> bytes:
    """The client's ``CustomizationOutfit.makeCompDescr`` for an outfit."""
    if outfit is None:
        raise OutfitError("no outfit")
    return encode_varint(CUSTOM_TYPE_OUTFIT) + _serialize_component(_OUTFIT_SPEC, outfit)


# ---------------------------------------------------------------------------
# reading a descriptor
# ---------------------------------------------------------------------------
def parse_comp_descr(data) -> Outfit:
    """``parseOutfitDescr``: the descriptor the client sent, as an object.

    The client sends the descriptor over a socket as a ``str`` (bytes), so a
    ``str`` argument is read as latin-1 - that is how the mod passes it through
    and how it survives a JSON round trip in the account file.
    """
    if isinstance(data, str):
        data = data.encode("latin-1")
    if data is None:
        raise OutfitError("no outfit descriptor")
    data = bytes(data)
    if len(data) > cc.MAX_OUTFIT_LENGTH * 8:
        raise OutfitError("outfit descriptor is too long (%d bytes)" % len(data))
    custom_type, offset = decode_varint(data, 0)
    if custom_type != CUSTOM_TYPE_OUTFIT:
        raise OutfitError("not an outfit descriptor: customType %r" % (custom_type,))
    outfit, offset = _parse_component(_OUTFIT_SPEC, data, offset)
    return outfit


def parse_outfit_descr(data) -> Outfit:
    """An empty descriptor means "no outfit" (``parseOutfitDescr``)."""
    if not data:
        return Outfit()
    return parse_comp_descr(data)


def _parse_component(spec: ComponentSpec, data: bytes, offset: int):
    has_value, offset = decode_varint(data, offset)
    component = spec.cls()
    bit = 1
    for field_spec in spec.fields:
        if has_value & bit:
            value, offset = _parse_value(field_spec, data, offset)
            setattr(component, field_spec.attr, value)
        bit <<= 1
    return component, offset


def _parse_value(field_spec: FieldSpec, data: bytes, offset: int):
    kind = field_spec.kind
    if kind == FIELD_VARINT:
        return decode_varint(data, offset)
    if kind & FIELD_TYPED_ARRAY:
        count, offset = decode_varint(data, offset)
        items = []
        for _index in range(count):
            value, offset = _parse_item(field_spec.item_kind, data, offset)
            items.append(value)
        return items, offset
    if kind >= FIELD_CUSTOM_OFFSET:
        return _parse_component(_spec_for(kind), data, offset)
    raise OutfitError("unsupported field type %r" % (kind,))


def _parse_item(item_kind: int, data: bytes, offset: int):
    if item_kind == FIELD_VARINT:
        return decode_varint(data, offset)
    return _parse_component(_spec_for(item_kind), data, offset)


# ---------------------------------------------------------------------------
# what an outfit needs
# ---------------------------------------------------------------------------
def count_areas(mask: int) -> int:
    """How many regions a bit mask covers (``getAllItemsFromOutfit`` counts them)."""
    return sum(1 for area in cc.ApplyArea.RANGE if int(mask) & area)


def paint_amount(applied_to: int, usage_costs=None) -> int:
    """``PaintItem.getAmount``: the paint a mask needs, region by region.

    Every region of a paint has its own cost (``<usages>`` of the item) and a
    paint covering three regions is three times the price of one; a paint that
    does not know a region cannot be applied there at all, which is what the
    client's ``getAmount`` reports as ``None``.
    """
    applied_to = int(applied_to)
    if not usage_costs:
        return count_areas(applied_to)
    total = 0
    for area in cc.ApplyArea.RANGE:
        if not applied_to & area:
            continue
        cost = usage_costs.get(area)
        if cost is None:
            return 0
        total += int(cost)
    return total


def items_of_outfit(outfit: Outfit, catalogue=None) -> Dict[int, int]:
    """``getAllItemsFromOutfit``: ``compactDescr -> how many`` an outfit needs.

    Paint is charged per covered region (and a region may cost more than one
    paint), a camouflage and a decal per covered region, a modification and a
    style once.  ``catalogue`` (a
    :class:`server.game.customization.CustomizationCatalog`) provides the paint
    usage costs; without it every region costs one paint.
    """
    result: Dict[int, int] = {}

    def add(c_type: int, item_id: int, count: int) -> None:
        if count <= 0:
            return
        compact = items_module.make_int_compact_descr_by_id("customizationItem", c_type, item_id)
        result[compact] = result.get(compact, 0) + int(count)

    for modification_id in outfit.modifications:
        add(cc.CustomizationType.MODIFICATION, int(modification_id), 1)
    for decal in outfit.decals:
        add(cc.CustomizationType.DECAL, int(decal.id), count_areas(decal.applied_to))
    for paint in outfit.paints:
        item = catalogue.get(items_module.make_int_compact_descr_by_id(
            "customizationItem", cc.CustomizationType.PAINT, int(paint.id))) if catalogue else None
        usage = dict(item.usages) if item is not None else None
        add(cc.CustomizationType.PAINT, int(paint.id), paint_amount(paint.applied_to, usage))
    for camouflage in outfit.camouflages:
        add(cc.CustomizationType.CAMOUFLAGE, int(camouflage.id), count_areas(camouflage.applied_to))
    if outfit.style_id:
        add(cc.CustomizationType.STYLE, int(outfit.style_id), 1)
    return result


def outfit_size(outfit: Outfit) -> int:
    """How many items an outfit holds (paints and so on, one each)."""
    return (len(outfit.modifications) + len(outfit.paints) + len(outfit.camouflages)
            + len(outfit.decals))


# ---------------------------------------------------------------------------
# reading an ``<outfit>`` section of the client's XML (the outfits of a style)
# ---------------------------------------------------------------------------
def read_season(section, default: int = cc.SeasonType.ALL) -> int:
    """``<season>`` - one name or a list of names, ``all`` covers everything."""
    if section is None:
        return int(default)
    tokens = list(section.value_tokens("season"))
    if not tokens:
        tokens = str(section.value("season", "") or "").split()
    season = 0
    for token in tokens:
        season |= cc.SEASON_IDS.get(str(token).strip().lower(), 0)
    return season or int(default)


def _read_int_array(section, name: str) -> List[int]:
    node = section.subsection(name)
    if node is None:
        return []
    values = []
    for child in node.children:
        raw = child.value("value", child.value("id", ""))
        try:
            values.append(int(str(raw).strip()))
        except (TypeError, ValueError):
            continue
    return values


def read_outfit_section(section, style_id: int = 0) -> Outfit:
    """One ``<outfit>`` section as a :class:`Outfit` (with its style id)."""
    outfit = Outfit(style_id=int(style_id or 0))
    outfit.modifications = _read_int_array(section, "modifications")
    paints = section.subsection("paints")
    if paints is not None:
        for entry in paints.children:
            paint = Paint()
            paint.id = entry.value_int("id", 0)
            paint.applied_to = entry.value_int("appliedTo", paint.applied_to)
            outfit.paints.append(paint)
    camouflages = section.subsection("camouflages")
    if camouflages is not None:
        for entry in camouflages.children:
            camouflage = Camouflage()
            camouflage.id = entry.value_int("id", 0)
            camouflage.pattern_size = entry.value_int("patternSize", camouflage.pattern_size)
            camouflage.applied_to = entry.value_int("appliedTo", camouflage.applied_to)
            camouflage.palette = entry.value_int("palette", 0)
            outfit.camouflages.append(camouflage)
    decals = section.subsection("decals")
    if decals is not None:
        for entry in decals.children:
            decal = Decal()
            decal.id = entry.value_int("id", 0)
            decal.applied_to = entry.value_int("appliedTo", decal.applied_to)
            outfit.decals.append(decal)
    return outfit


def read_outfits(section, style_id: int = 0) -> Dict[int, Outfit]:
    """``StyleItem.outfits``: ``season -> outfit`` of one ``<style>`` section.

    The client stores one outfit per *season* (``SeasonType.SEASONS``): a style
    that says ``ALL`` is one outfit for every season, and each outfit carries the
    id of the style it belongs to.
    """
    outfits: Dict[int, Outfit] = {}
    node = section.subsection("outfits")
    if node is None:
        return outfits
    for entry in node.children:
        season = read_season(entry, cc.SeasonType.ALL)
        outfit = read_outfit_section(entry, style_id)
        # ``StyleXmlReader``: ``for s in SeasonType.SEASONS: if s & season``.
        for single in cc.SeasonType.SEASONS:
            if single & season:
                outfits[single] = outfit
    return outfits


__all__ = [
    "CUSTOM_TYPE_CAMOUFLAGE",
    "CUSTOM_TYPE_DECAL",
    "CUSTOM_TYPE_OUTFIT",
    "CUSTOM_TYPE_PAINT",
    "Camouflage",
    "ComponentSpec",
    "Decal",
    "FieldSpec",
    "Outfit",
    "OutfitError",
    "Paint",
    "SPECS",
    "count_areas",
    "decode_varint",
    "encode_varint",
    "items_of_outfit",
    "make_comp_descr",
    "outfit_size",
    "paint_amount",
    "parse_comp_descr",
    "parse_outfit_descr",
    "read_outfit_section",
    "read_outfits",
    "read_season",
]
