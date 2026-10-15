"""New Year 2018 ("Новогоднее наступление") rules that the client hard-codes.

Every table in this module is a byte-for-byte mirror of the values found in the
game client's ``res/scripts/common/items/new_year_types.py`` of the supported
build (Chinese HD ``0.9.22.0.1 #1513``).  The client uses those values to render
the event (atmosphere level, collection level, progress bars, prices, bonus
percentages), so the server must use exactly the same numbers or the interface
would disagree with the stored state.

Keeping them in one dedicated module makes the mirror auditable: tests compare
this table with the client file when a client directory is available
(``tools/check_client_assets.py``).
"""

from __future__ import annotations

from collections import namedtuple

# --------------------------------------------------------------------------
# National settings ("стили") of the event.
# --------------------------------------------------------------------------
NATIONAL_SETTINGS = ("soviet", "traditionalWestern", "modernWestern", "asian")
NATIONAL_SETTINGS_IDS_BY_NAME = {name: index for index, name in enumerate(NATIONAL_SETTINGS)}
NATIONAL_SETTINGS_BY_ID = {index: name for index, name in enumerate(NATIONAL_SETTINGS)}

# ``nations.NAMES`` of the supported client, indexes are the nation identifiers
# the client itself uses for the event (see ``new_year_types``).
NATIONS = (
    "ussr",
    "germany",
    "usa",
    "china",
    "france",
    "uk",
    "japan",
    "czech",
    "sweden",
    "poland",
)
NATION_INDICES = {name: index for index, name in enumerate(NATIONS)}

NATIONS_BY_SETTING = {
    "soviet": ("ussr",),
    "traditionalWestern": ("germany", "czech", "france", "poland"),
    "modernWestern": ("usa", "uk", "sweden", "japan"),
    "asian": ("china",),
}
SETTING_BY_NATION = {
    nation: setting for setting, nation_list in NATIONS_BY_SETTING.items() for nation in nation_list
}
SETTING_BY_NATION_ID = {
    NATION_INDICES[nation]: setting for nation, setting in SETTING_BY_NATION.items()
}
SETTING_ID_BY_NATION_ID = {
    nation_id: NATIONAL_SETTINGS_IDS_BY_NAME[setting]
    for nation_id, setting in SETTING_BY_NATION_ID.items()
}

# --------------------------------------------------------------------------
# Toys / slots.
# --------------------------------------------------------------------------
TOY_TYPES = (
    "top",
    "hanging",
    "garland",
    "gift",
    "snowman",
    "house_decoration",
    "house_lamp",
    "street_garland",
)
TOY_TYPES_IDS_BY_NAME = {name: index for index, name in enumerate(TOY_TYPES)}
TOY_TYPES_BY_ID = {index: name for index, name in enumerate(TOY_TYPES)}

MAX_TOY_RANK = 5
INVALID_TOY_ID = -1
MAX_CHEST_LEVEL = 10

# A collected-toy bit set is 40 bytes wide on both sides of the wire.
TOY_COLLECTION_BYTES = 40
MAX_TOY_ID = TOY_COLLECTION_BYTES * 8 - 1  # 319


class CRAFT:
    """Crafting ("ёлочная мастерская") costs and random-roll probabilities."""

    FRAGMENTS_BY_RANK = (10, 20, 30, 70, 250)
    COST_BY_RANK = (20, 40, 60, 140, 480)
    RANDOM_TOY_COST = 100
    NATIONAL_SETTING_COST_FACTOR = 1.0
    TYPE_COST_FACTOR = 1.0

    PROBABILITY_FOR_RANK = (
        (1, 0.5344),
        (2, 0.27),
        (3, 0.12),
        (4, 0.065),
        (5, 0.0106),
    )
    PROBABILITY_FOR_SETTING = (
        ("soviet", 0.25),
        ("traditionalWestern", 0.25),
        ("modernWestern", 0.25),
        ("asian", 0.25),
    )
    PROBABILITY_FOR_SETTING_ID = tuple(
        (NATIONAL_SETTINGS_IDS_BY_NAME[name], probability)
        for name, probability in PROBABILITY_FOR_SETTING
    )
    PROBABILITY_FOR_TYPE = (
        ("top", 0.08),
        ("hanging", 0.3),
        ("garland", 0.11),
        ("gift", 0.11),
        ("snowman", 0.1),
        ("house_decoration", 0.1),
        ("house_lamp", 0.1),
        ("street_garland", 0.1),
    )
    PROBABILITY_FOR_TYPE_ID = tuple(
        (TOY_TYPES_IDS_BY_NAME[name], probability) for name, probability in PROBABILITY_FOR_TYPE
    )


class NY_STATE:
    """Event state, mirrored from the client enum."""

    NOT_STARTED = 0
    IN_PROGRESS = 1
    SUSPENDED = 2
    FINISHED = 3

    ENABLED = (IN_PROGRESS, SUSPENDED)
    ALL = (NOT_STARTED, IN_PROGRESS, SUSPENDED, FINISHED)


# --------------------------------------------------------------------------
# Atmosphere (tree level) and collections.
# --------------------------------------------------------------------------
TOY_ATMOSPHERE_BY_RANK = (1, 2, 4, 7, 25)
TOY_RATING_BY_RANK = (1, 1, 1, 1, 1)

BOUND_ATMOSPHERE_BY_LEVEL = (0, 8, 22, 41, 64, 91, 137, 186, 249, 325)
BONUS_FACTOR_BY_ATMOSPHERE_LEVEL = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 12.0)
MAX_ATMOSPHERE_LEVEL = 10

BOUND_COLLECTION_RATING_BY_LEVEL = (0, 8, 19, 37, 55)
MAX_COLLECTION_LEVEL = 5

CollectionBonus = namedtuple("CollectionBonus", ("xp", "tmenXp", "freeXp", "credits"))
COLLECTION_BONUSES_BY_LEVEL = (
    CollectionBonus(0.0, 0.0, 0.0, 0.0),
    CollectionBonus(0.01, 0.01, 0.0, 0.01),
    CollectionBonus(0.02, 0.02, 0.0, 0.02),
    CollectionBonus(0.03, 0.03, 0.0, 0.03),
    CollectionBonus(0.04, 0.04, 0.0, 0.04),
)

assert len(BOUND_ATMOSPHERE_BY_LEVEL) == MAX_ATMOSPHERE_LEVEL
assert len(BONUS_FACTOR_BY_ATMOSPHERE_LEVEL) == MAX_ATMOSPHERE_LEVEL
assert len(BOUND_COLLECTION_RATING_BY_LEVEL) == MAX_COLLECTION_LEVEL
assert len(COLLECTION_BONUSES_BY_LEVEL) == MAX_COLLECTION_LEVEL
for _params in (TOY_ATMOSPHERE_BY_RANK, TOY_RATING_BY_RANK, CRAFT.FRAGMENTS_BY_RANK, CRAFT.COST_BY_RANK):
    assert len(_params) == MAX_TOY_RANK
