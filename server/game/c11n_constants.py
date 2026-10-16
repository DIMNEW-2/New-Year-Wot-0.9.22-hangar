"""The constants of the client's customization 2.0 (``c11n_constants.py``).

Mirror of ``common/items/components/c11n_constants.py`` of the supported build:
the item types, the seasons (a bit per season), the *apply areas* (which part of
a tank an item covers) and the small limits.  Everything the local server says
about appearance items - what a depot entry means, which part an item is applied
to, how much of it an outfit needs - is expressed with these numbers, so they are
kept in one place instead of being spelled out again in every module.
"""

from __future__ import annotations

#: ``RENT_DEFAULT_BATTLES``: what a rentable style grants when it names no
#: ``<rentCount>`` of its own.
RENT_DEFAULT_BATTLES = 50

#: ``MAX_OUTFIT_LENGTH`` - the longest outfit descriptor the client accepts.
MAX_OUTFIT_LENGTH = 1024

#: ``MAX_CAMOUFLAGE_PATTERN_SIZE`` - how many pattern sizes a camouflage has.
MAX_CAMOUFLAGE_PATTERN_SIZE = 5

#: ``MAX_ITEMS_FOR_BUY_OPERATION`` - the client refuses a bigger shopping list.
MAX_ITEMS_FOR_BUY_OPERATION = 100

#: ``C11N_MAX_REGION_NUM`` / ``C11N_MASK_REGION`` (``common/constants.py``).
C11N_MAX_REGION_NUM = 3
C11N_MASK_REGION = 2


class CustomizationType(object):
    """``CustomizationType``: one number per kind of appearance item."""

    PAINT = 1
    CAMOUFLAGE = 2
    DECAL = 3
    STYLE = 4
    MODIFICATION = 5
    ITEM_GROUP = 6
    RANGE = (PAINT, CAMOUFLAGE, DECAL, STYLE, MODIFICATION, ITEM_GROUP)
    #: The types that live in the depot and are applied to a *region* of a tank,
    #: as opposed to a style, which is an outfit of its own.
    _SIMPLE_TYPES = (PAINT, CAMOUFLAGE, DECAL)


#: ``CustomizationTypeNames``: the name of every type in the client's language.
TYPE_NAMES = {
    CustomizationType.PAINT: "paint",
    CustomizationType.CAMOUFLAGE: "camouflage",
    CustomizationType.DECAL: "decal",
    CustomizationType.STYLE: "style",
    CustomizationType.MODIFICATION: "modification",
    CustomizationType.ITEM_GROUP: "itemGroup",
}


class ItemTags(object):
    """``ItemTags``: the tags that change how an item behaves."""

    VEHICLE_BOUND = "vehicleBound"
    PREMIUM_IGR = "premiumIGR"
    IGR = "IGR"
    RARE = "rare"


class ApplyArea(object):
    """``ApplyArea``: the parts of a tank an appearance item can cover.

    The three ones of a part (``HULL_1`` ...) are the individual decal and paint
    slots of that part, so an item's ``appliedTo`` is a bit mask of these.
    """

    NONE = 0
    CHASSIS = 1
    CHASSIS_1 = 2
    CHASSIS_2 = 4
    CHASSIS_3 = 8
    HULL = 16
    HULL_1 = 32
    HULL_2 = 64
    HULL_3 = 128
    TURRET = 256
    TURRET_1 = 512
    TURRET_2 = 1024
    TURRET_3 = 2048
    GUN = 4096
    GUN_1 = 8192
    GUN_2 = 16384
    GUN_3 = 32768
    ALL = 65535

    CHASSIS_PAINT_REGIONS = (CHASSIS, CHASSIS_1, CHASSIS_2)
    HULL_PAINT_REGIONS = (HULL, HULL_1, HULL_2)
    HULL_CAMOUFLAGE_REGIONS = (HULL,)
    HULL_EMBLEM_REGIONS = (HULL, HULL_1)
    HULL_INSCRIPTION_REGIONS = (HULL_2, HULL_3)
    TURRET_PAINT_REGIONS = (TURRET, TURRET_1, TURRET_2)
    TURRET_CAMOUFLAGE_REGIONS = (TURRET,)
    TURRET_EMBLEM_REGIONS = (TURRET, TURRET_1)
    TURRET_INSCRIPTION_REGIONS = (TURRET_2, TURRET_3)
    GUN_PAINT_REGIONS = (GUN, GUN_1, GUN_2)
    GUN_CAMOUFLAGE_REGIONS = (GUN,)
    GUN_EMBLEM_REGIONS = (GUN, GUN_1)
    GUN_INSCRIPTION_REGIONS = (GUN_2, GUN_3)

    #: Every single region bit (the composite values are not part of it).
    RANGE = (CHASSIS, CHASSIS_1, CHASSIS_2, CHASSIS_3,
             HULL, HULL_1, HULL_2, HULL_3,
             TURRET, TURRET_1, TURRET_2, TURRET_3,
             GUN, GUN_1, GUN_2, GUN_3)

    DECAL_REGIONS = (HULL_EMBLEM_REGIONS + HULL_INSCRIPTION_REGIONS
                     + TURRET_EMBLEM_REGIONS + TURRET_INSCRIPTION_REGIONS)
    CAMOUFLAGE_REGIONS = HULL_CAMOUFLAGE_REGIONS + TURRET_CAMOUFLAGE_REGIONS + GUN_CAMOUFLAGE_REGIONS
    PAINT_REGIONS = (CHASSIS_PAINT_REGIONS + HULL_PAINT_REGIONS
                     + TURRET_PAINT_REGIONS + GUN_PAINT_REGIONS)
    EMBLEM_REGIONS = HULL_EMBLEM_REGIONS + TURRET_EMBLEM_REGIONS + GUN_EMBLEM_REGIONS
    INSCRIPTION_REGIONS = HULL_INSCRIPTION_REGIONS + TURRET_INSCRIPTION_REGIONS + GUN_INSCRIPTION_REGIONS

    #: ``ApplyArea.USER_PAINT_ALLOWED_REGIONS``: which parts a paint may cover
    #: (the side skirts and the third gun slot are not paintable by hand).
    USER_PAINT_ALLOWED_REGIONS = (CHASSIS, HULL, TURRET, GUN, GUN_2)

    #: The region tuples above folded into one bit mask each - these are the
    #: values the client's ``ApplyArea`` computes with ``reduce(int.__or__)``.
    USER_PAINT_ALLOWED_REGIONS_VALUE = 1 | 16 | 256 | 4096 | 16384  # 20753
    DECAL_REGIONS_VALUE = 16 | 32 | 64 | 128 | 256 | 512 | 1024 | 2048  # 4080
    CAMOUFLAGE_REGIONS_VALUE = 16 | 256 | 4096  # 4368
    EMBLEM_REGIONS_VALUE = 16 | 32 | 256 | 512 | 4096 | 8192  # 13104
    INSCRIPTION_REGIONS_VALUE = 64 | 128 | 1024 | 2048 | 16384 | 32768  # 52416


class SeasonType(object):
    """``SeasonType``: a bit per season, ``ALL`` covers every one of them."""

    UNDEFINED = 0
    WINTER = 1
    SUMMER = 2
    DESERT = 4
    EVENT = 8
    ALL = WINTER | SUMMER | DESERT | EVENT
    RANGE = (WINTER, SUMMER, DESERT, EVENT, ALL)
    SEASONS = (WINTER, SUMMER, DESERT, EVENT)
    COMMON_SEASONS = (WINTER, SUMMER, DESERT)

    @staticmethod
    def fromArenaKind(arena_kind: int) -> int:
        """``arenaKind`` of a battle (``vehicles.CAMOUFLAGE_KINDS``) -> season."""
        if arena_kind == 0:
            return SeasonType.WINTER
        if arena_kind == 1:
            return SeasonType.SUMMER
        if arena_kind == 2:
            return SeasonType.DESERT
        raise ValueError("unknown arenaKind %r" % (arena_kind,))


#: The names the XML files use for the seasons.
SEASON_IDS = {
    "winter": SeasonType.WINTER,
    "summer": SeasonType.SUMMER,
    "desert": SeasonType.DESERT,
    "event": SeasonType.EVENT,
    "all": SeasonType.ALL,
}


class ModificationType(object):
    """``ModificationType``: the effects a modification can apply."""

    UNDEFINED = 0
    PAINT_AGE = 1
    CAMOUFLAGE_AGE = 2
    PAINT_FADING = 3
    GLOSS = 4
    METALLIC = 5


class DecalType(object):
    """``DecalType``: emblems and inscriptions share the decal item type."""

    EMBLEM = 1
    INSCRIPTION = 2


__all__ = [
    "ApplyArea",
    "C11N_MASK_REGION",
    "C11N_MAX_REGION_NUM",
    "CustomizationType",
    "DecalType",
    "ItemTags",
    "MAX_CAMOUFLAGE_PATTERN_SIZE",
    "MAX_ITEMS_FOR_BUY_OPERATION",
    "MAX_OUTFIT_LENGTH",
    "ModificationType",
    "RENT_DEFAULT_BATTLES",
    "SEASON_IDS",
    "SeasonType",
    "TYPE_NAMES",
]
