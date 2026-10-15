"""Pure functions of the New Year event, mirroring the client's own maths.

The client evaluates these functions locally to paint the hangar (tree level,
progress bars, collection levels, bonus percentages, crafting price), while the
server evaluates them to decide what actually happens.  Any divergence would be
visible as a mismatch between the money spent and the tree that grows, so the
implementations are kept identical, including the boundary comparisons of
``getAtmosphereLevel``/``getCollectionLevel``.
"""

from __future__ import annotations

import random

from . import ny_constants as nyc


def get_total_atmosphere(toys_descrs, slot_toys) -> int:
    """Sum the atmosphere value of every toy placed in a slot (``-1`` is empty)."""
    atmosphere = 0
    for toy_id in slot_toys:
        if toy_id != nyc.INVALID_TOY_ID:
            atmosphere += toys_descrs[toy_id]["atmosphere"]
    return atmosphere


def get_atmosphere_level(total_atmosphere: int) -> int:
    for level, bound in enumerate(nyc.BOUND_ATMOSPHERE_BY_LEVEL):
        if total_atmosphere < bound:
            return level
    return nyc.MAX_ATMOSPHERE_LEVEL


def get_atmosphere_progress(total_atmosphere: int):
    for level, bound in enumerate(nyc.BOUND_ATMOSPHERE_BY_LEVEL):
        if total_atmosphere < bound:
            prev_bound = nyc.BOUND_ATMOSPHERE_BY_LEVEL[level - 1]
            return total_atmosphere - prev_bound, bound - prev_bound
    final_delta = nyc.BOUND_ATMOSPHERE_BY_LEVEL[-1] - nyc.BOUND_ATMOSPHERE_BY_LEVEL[-2]
    return final_delta, final_delta


def get_collection_level(collection_rating: int) -> int:
    for level, bound in enumerate(nyc.BOUND_COLLECTION_RATING_BY_LEVEL):
        if collection_rating < bound:
            return level
    return nyc.MAX_COLLECTION_LEVEL


def get_bonuses(atmosphere_level: int, collection_level: int):
    """Return ``(xp, tmenXp, freeXp, credits)`` bonus factors of the event."""
    if collection_level <= 0:
        collection = nyc.COLLECTION_BONUSES_BY_LEVEL[0]
    else:
        collection = nyc.COLLECTION_BONUSES_BY_LEVEL[min(collection_level, nyc.MAX_COLLECTION_LEVEL) - 1]
    factor = nyc.BONUS_FACTOR_BY_ATMOSPHERE_LEVEL[max(0, min(atmosphere_level, nyc.MAX_ATMOSPHERE_LEVEL)) - 1]
    return (
        collection.xp * factor,
        collection.tmenXp * factor,
        collection.freeXp * factor,
        collection.credits * factor,
    )


def calculate_craft_cost(is_type_specified: bool, is_setting_specified: bool, rank):
    """Craft price in toy fragments, exactly as ``new_year_types`` computes it."""
    if rank is None or rank == nyc.INVALID_TOY_ID:
        base_cost = nyc.CRAFT.RANDOM_TOY_COST
    elif 1 <= rank <= nyc.MAX_TOY_RANK:
        base_cost = nyc.CRAFT.COST_BY_RANK[rank - 1]
    else:
        return None
    cost = base_cost
    if is_type_specified:
        cost += base_cost * nyc.CRAFT.TYPE_COST_FACTOR
    if is_setting_specified:
        cost += base_cost * nyc.CRAFT.NATIONAL_SETTING_COST_FACTOR
    return int(cost)


def fragments_for_rank(rank: int) -> int:
    return nyc.CRAFT.FRAGMENTS_BY_RANK[rank - 1]


def make_random_choice(choices, rng: random.Random):
    """Same walk as the client's ``makeRandomChoice`` (cumulative probability)."""
    value = rng.random()
    probability = 0.0
    for item, weight in choices:
        probability += weight
        if value <= probability:
            return item
    return choices[0][0]


def get_toy_group_id(type_id: int, setting_id: int, rank: int) -> int:
    return type_id << 16 | setting_id << 8 | rank


def choose_toy_using_craft_rules(toys_by_group, type_id, setting_id, rank, rng: random.Random):
    """Roll a toy for the crafting interface (any of the three axes may be random).

    ``toys_by_group`` maps the group id to a list of toy ids; a group is
    ``(toy type, national setting, rank)``.  The supported client guarantees the
    group table is complete (see ``_buildToyGroups``), which
    :func:`server.game.catalog.NyCatalog.validate` enforces for us as well.
    """
    if type_id == nyc.INVALID_TOY_ID:
        type_id = make_random_choice(nyc.CRAFT.PROBABILITY_FOR_TYPE_ID, rng)
    if setting_id == nyc.INVALID_TOY_ID:
        setting_id = make_random_choice(nyc.CRAFT.PROBABILITY_FOR_SETTING_ID, rng)
    if rank == nyc.INVALID_TOY_ID:
        rank = make_random_choice(nyc.CRAFT.PROBABILITY_FOR_RANK, rng)
    group_of_choice = toys_by_group.get(get_toy_group_id(type_id, setting_id, rank))
    if not group_of_choice:
        return None
    return rng.choice(group_of_choice)


def pack_toy_collection(toy_ids) -> bytes:
    """Pack collected toy ids into the bit set the client decodes (40 bytes).

    The client's ``_unpackBinaryCollection`` walks bit ``byte_pos * 8 + bit_pos``
    of every byte of the delivered string, so this is the exact inverse.
    """
    data = bytearray(nyc.TOY_COLLECTION_BYTES)
    for toy_id in toy_ids:
        toy_id = int(toy_id)
        if 0 <= toy_id <= nyc.MAX_TOY_ID:
            data[toy_id >> 3] |= 1 << (toy_id & 7)
    return bytes(data)


def unpack_toy_collection(data) -> list:
    """Inverse of :func:`pack_toy_collection`; accepts ``bytes`` or ints."""
    result = []
    if isinstance(data, (bytes, bytearray)):
        for byte_pos, byte in enumerate(bytearray(data)):
            for bit_pos in range(8):
                if byte & (1 << bit_pos):
                    result.append(byte_pos * 8 + bit_pos)
    return result
