"""The barracks: the crew of the account and the paperwork the player can do.

Every command is driven through the real dispatcher (the same one the socket
server calls), and every assertion is about what the *client* would read:
``inventory[8]['compDescr'][invID]`` is the descriptor the client parses into a
crew member (``common/items/tankmen.TankmanDescr``), the diff of an answer is
what updates its caches and the recycle bin is where a dismissed crew member
waits to be restored.  The arithmetic of a skill, a retraining or a rank is
mirrored from the client's own ``TankmanDescr``, so the numbers here are the
numbers the game would compute.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server.account_rpc import barracks as barracks_module  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc.requests import RequestDispatcher  # noqa: E402
from server.game import tankmen as tankmen_module  # noqa: E402

TANKMAN_SECTION = 8
SKILL_REPAIR = tankmen_module.SKILL_INDICES["repair"]
SKILL_BROTHERHOOD = tankmen_module.SKILL_INDICES["brotherhood"]


def _dispatcher(runner, **kwargs):
    state = common.new_account(runner.client, **kwargs)
    return RequestDispatcher(state), state


def _seated(state, index=0):
    """``(vehicle entry, crew member)`` of a crewed vehicle of the account."""
    for inv_id in sorted(state.vehicles):
        entry = state.vehicles[inv_id]
        for tman in entry.crew:
            if tman not in (-1, None):
                return entry, state.tankmen[tman]
    raise AssertionError("the account has no crew at all")


def _free_seat(state):
    """``(vehicle entry, slot)`` of a seat nothing occupies yet."""
    for inv_id, entry in sorted(state.vehicles.items()):
        roles = state.crew_roles_of(entry)
        for slot, role in enumerate(roles):
            if slot >= len(entry.crew) or entry.crew[slot] in (-1, None):
                return entry, slot, role
    raise AssertionError("no vehicle has a free seat")


def _diff_tankman(diff, inv_id):
    section = (diff.get("inventory") or {}).get(TANKMAN_SECTION) or {}
    return (section.get("compDescr") or {}).get(inv_id)


# ---------------------------------------------------------------------------
# Reading a crew member the way the client does
# ---------------------------------------------------------------------------
def test_the_crew_descriptors_are_readable(runner: common.Runner) -> None:
    """Every crew member of the account parses into the client's descriptor."""
    dispatcher, state = _dispatcher(runner)
    empty = []
    for entry in state.tankmen.values():
        try:
            descr = tankmen_module.TankmanDescr(entry.compact_descr)
        except tankmen_module.TankmenError as error:
            empty.append("%s: %s" % (entry.inv_id, error))
            continue
        runner.equal(descr.role, entry.role, "crew %s keeps its role" % entry.inv_id)
        runner.equal(descr.role_level, entry.role_level, "and its role level")
        runner.equal(descr.nation_id, entry.nation_id, "and its nation")
        runner.equal(descr.pack(), entry.compact_descr,
                     "crew %s survives a round trip" % entry.inv_id)
    runner.equal(empty, [], "no descriptor of the account is broken")


def test_the_rank_of_a_crew_member_follows_its_level(runner: common.Runner) -> None:
    """A fresh 100% crew member is not a private: the rank is derived."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    config = state.tankman_config(entry.nation_id)
    runner.check(config is not None, "the nation config is known")
    ranks = config.role_rank_ids(entry.role)
    runner.check(len(ranks) > 1, "the nation has a rank table")
    descr = state.tankman_descr(entry)
    runner.check(descr.rank_index in ranks,
                 "the rank %s of the descriptor is a rank of the nation" % descr.rank_index)
    runner.check(descr.rank_index > 0,
                 "a 100%% %s is above the lowest rank" % entry.role)

    # Learning and levelling a skill promotes the crew member, as in the game.
    rank_before = descr.rank_index
    descr.add_skill("repair")
    descr.add_xp(descr.total_xp())
    runner.check(descr.rank_index > rank_before, "a levelled skill promotes the crew member")


# ---------------------------------------------------------------------------
# CMD_TMAN_ADD_SKILL (151)
# ---------------------------------------------------------------------------
def test_learning_a_skill_publishes_the_descriptor(runner: common.Runner) -> None:
    """``addTankmanSkill`` - the new skill travels in the diff of the answer."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    before = entry.compact_descr

    response = dispatcher.handle(1, cmd.CMD_TMAN_ADD_SKILL, (entry.inv_id, SKILL_REPAIR, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the skill is accepted")
    descr = state.tankman_descr(entry)
    runner.equal(descr.skills, ["repair"], "the crew member learned it")
    runner.equal(descr.last_skill_level, 0, "and starts from zero percent")
    runner.check(entry.compact_descr != before, "the descriptor changed")
    runner.equal(_diff_tankman(response.diffs[0], entry.inv_id), entry.compact_descr,
                 "the diff carries the new descriptor")

    response = dispatcher.handle(2, cmd.CMD_TMAN_ADD_SKILL, (entry.inv_id, SKILL_REPAIR, 0))
    runner.equal(response.code, cmd.RES_FAILURE, "learning it twice is refused")
    runner.equal(response.error, barracks_module.ERROR_INVALID_OPERATION, "with the client's own key")

    # A skill of another role cannot be learned either.
    response = dispatcher.handle(3, cmd.CMD_TMAN_ADD_SKILL, (entry.inv_id, 9999, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an unknown skill id is a bad request")
    response = dispatcher.handle(4, cmd.CMD_TMAN_ADD_SKILL, (4242, SKILL_REPAIR, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "so is an unknown crew member")


# ---------------------------------------------------------------------------
# CMD_TMAN_DROP_SKILLS (152)
# ---------------------------------------------------------------------------
def test_dropping_the_skills_charges_and_gives_experience_back(runner: common.Runner) -> None:
    """``dropTankmanSkills`` - the price decides how much experience returns."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    descr = state.tankman_descr(entry)
    descr.add_skill("repair")
    descr.add_xp(120000)
    state.set_tankman_descr(entry, descr)
    trained = state.tankman_descr(entry)
    runner.check(trained.last_skill_level > 0, "the skill has a level to lose")
    runner.check(not trained.is_free_drop_skills(), "the skills are not free to drop any more")

    gold_before = state.gold
    response = dispatcher.handle(1, cmd.CMD_TMAN_DROP_SKILLS, (1, entry.inv_id, 2))
    runner.equal(response.code, cmd.RES_SUCCESS, "the gold option is accepted")
    cost = state.shop_payload()["dropSkillsCost"][2]
    runner.equal(state.gold, gold_before - cost["gold"], "the gold price was taken")
    runner.equal(state.tankman_descr(entry).skills, [], "the crew member has no skills left")
    runner.check(state.tankman_descr(entry).free_xp > 0,
                 "and keeps the experience the price promised to give back")
    runner.equal(response.diffs[0]["economics"]["gold"], state.gold,
                 "the diff publishes the wallet")

    response = dispatcher.handle(2, cmd.CMD_TMAN_DROP_SKILLS, (1, entry.inv_id, 0))
    runner.equal(response.code, cmd.RES_FAILURE, "dropping nothing is refused")
    runner.equal(response.error, barracks_module.ERROR_INVALID_OPERATION, "with the client's key")


def test_the_free_drop_only_accepts_the_free_price(runner: common.Runner) -> None:
    """A crew member that learned nothing yet is dropped for free only."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    descr = state.tankman_descr(entry)
    descr.add_skill("repair")
    state.set_tankman_descr(entry, descr)
    runner.check(state.tankman_descr(entry).is_free_drop_skills(), "the skill is fresh")

    credits_before, gold_before = state.credits, state.gold
    response = dispatcher.handle(1, cmd.CMD_TMAN_DROP_SKILLS, (1, entry.inv_id, 1))
    runner.equal(response.code, cmd.RES_FAILURE, "paying for a fresh skill is refused")
    runner.equal(state.credits, credits_before, "and nothing was charged")
    runner.equal(state.gold, gold_before, "in either currency")

    response = dispatcher.handle(2, cmd.CMD_TMAN_DROP_SKILLS, (1, entry.inv_id, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the free option works")
    runner.equal(state.tankman_descr(entry).skills, [], "the skill is gone")


# ---------------------------------------------------------------------------
# CMD_TMAN_RESPEC (153) / CMD_TMAN_MULTI_RESPEC (156)
# ---------------------------------------------------------------------------
def test_retraining_moves_the_crew_member_to_another_vehicle(runner: common.Runner) -> None:
    """``respecTankman`` - the price decides how much of the level survives."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    descr = state.tankman_descr(entry)
    old_vehicle_type_id = descr.vehicle_type_id
    target = None
    for vehicle in state.catalog.vehicles.all():
        if vehicle.nation_id != entry.nation_id or vehicle.innation_id == old_vehicle_type_id:
            continue
        if any(entry.role in slot for slot in vehicle.crew_roles):
            target = vehicle
            break
    runner.check(target is not None, "the installation has another vehicle for the role")

    response = dispatcher.handle(1, cmd.CMD_TMAN_RESPEC,
                                 (1, entry.inv_id, 0, target.compact_descr))
    runner.equal(response.code, cmd.RES_SUCCESS, "the free retraining is accepted")
    trained = state.tankman_descr(entry)
    runner.equal(trained.vehicle_type_id, target.innation_id,
                 "the crew member now trains on the new vehicle")
    loss = state.shop_payload()["tankmanCost"][0]
    expected = max(int(loss["roleLevel"]), int(round(100 * (1.0 - loss["baseRoleLoss"]
                                                           - loss["classChangeRoleLoss"]))))
    runner.equal(trained.role_level, expected, "the level dropped by the free option's loss")
    runner.equal(_diff_tankman(response.diffs[0], entry.inv_id), entry.compact_descr,
                 "the new descriptor travels in the diff")

    gold_before = state.gold
    response = dispatcher.handle(2, cmd.CMD_TMAN_RESPEC,
                                 (1, entry.inv_id, 2, target.compact_descr))
    runner.equal(response.code, cmd.RES_SUCCESS, "the gold retraining is accepted")
    runner.equal(state.gold, gold_before - state.shop_payload()["tankmanCost"][2]["gold"],
                 "the gold price was taken")
    runner.equal(state.tankman_descr(entry).role_level, 100,
                 "the gold option keeps the whole role level")

    response = dispatcher.handle(3, cmd.CMD_TMAN_RESPEC,
                                 (1, entry.inv_id, 0, 0xFFFFFF))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an unknown vehicle is a bad request")


def test_retraining_a_whole_crew_costs_once(runner: common.Runner) -> None:
    """``multiRespecTankman`` - one price for every crew member of the request."""
    dispatcher, state = _dispatcher(runner)
    vehicle, entry = _seated(state)
    pair = None
    for slot, role in enumerate(state.crew_roles_of(vehicle)):
        if slot < len(vehicle.crew) and vehicle.crew[slot] not in (-1, None):
            pair = (slot, role)
            break
    runner.check(pair is not None, "the vehicle has a crew to retrain")
    slot, role = pair
    crew = [vehicle.crew[slot]]
    travelling = state.tankmen[crew[0]]
    target = None
    for candidate in state.catalog.vehicles.all():
        if candidate.nation_id != travelling.nation_id:
            continue
        if candidate.compact_descr == state.vehicle_type(vehicle).compact_descr:
            continue
        if any(travelling.role in roles_of_slot for roles_of_slot in candidate.crew_roles):
            target = candidate
            break
    runner.check(target is not None, "another vehicle for the role exists")

    credits_before, gold_before = state.credits, state.gold
    flat = [state.shop_payload()["rev"], target.compact_descr]
    for inv_id in crew:
        flat.extend([inv_id, 1])
    response = dispatcher.handle(1, cmd.CMD_TMAN_MULTI_RESPEC, (flat,))
    runner.equal(response.code, cmd.RES_SUCCESS, "the whole-crew retraining is accepted")
    cost = state.shop_payload()["tankmanCost"][1]
    runner.equal(state.credits, credits_before - cost["credits"] * len(crew),
                 "the credits price was taken once per crew member")
    runner.equal(state.gold, gold_before, "the gold was left alone")
    for inv_id in crew:
        runner.equal(state.tankman_descr(state.tankmen[inv_id]).vehicle_type_id,
                     target.innation_id, "crew %s was retrained" % inv_id)


# ---------------------------------------------------------------------------
# CMD_TMAN_CHANGE_ROLE (158)
# ---------------------------------------------------------------------------
def test_changing_the_role_costs_gold_and_seats_the_crew_member(runner: common.Runner) -> None:
    """``changeTankmanRole`` - the role is new, the seat is the answer's ``ext``."""
    dispatcher, state = _dispatcher(runner)
    vehicle, entry = _seated(state)
    roles = state.crew_roles_of(vehicle)
    slot = vehicle.crew.index(entry.inv_id)
    other_slot = None
    for index, role in enumerate(roles):
        if index != slot:
            other_slot = index
            break
    runner.check(other_slot is not None, "the vehicle has a second crew slot")
    other_role = roles[other_slot]
    # Empty the seat the crew member is about to take.
    occupant = vehicle.crew[other_slot]
    if occupant not in (-1, None):
        dispatcher.handle(90, cmd.CMD_DISMISS_TMAN, (occupant, 0, 0))
    runner.check(vehicle.crew[other_slot] in (-1, None), "the other seat is free")

    roles_index = tankmen_module.ROLE_INDICES[other_role]
    gold_before = state.gold
    response = dispatcher.handle(1, cmd.CMD_TMAN_CHANGE_ROLE,
                                 (1, entry.inv_id, roles_index,
                                  state._vehicle_type(vehicle).compact_descr))
    runner.equal(response.code, cmd.RES_SUCCESS, "the role change is accepted")
    runner.equal(state.gold,
                 gold_before - state.shop_payload()["changeRoleCost"],
                 "the role change took its gold")
    runner.equal(state.tankman_descr(entry).role, other_role, "the crew member has the new role")
    runner.equal(response.ext, barracks_module.EQUIP_TMAN_OK, "and was seated")
    runner.equal(vehicle.crew[state.crew_roles_of(vehicle).index(other_role)], entry.inv_id,
                 "in the seat of the new role")

    response = dispatcher.handle(2, cmd.CMD_TMAN_CHANGE_ROLE,
                                 (1, entry.inv_id, tankmen_module.ROLE_INDICES[other_role],
                                  state._vehicle_type(vehicle).compact_descr))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "the same role again is a bad request")


# ---------------------------------------------------------------------------
# CMD_TMAN_PASSPORT (154)
# ---------------------------------------------------------------------------
def test_changing_the_passport_stays_inside_the_nation_groups(runner: common.Runner) -> None:
    """``replacePassport`` - the name has to exist in the client's own table."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    descr = state.tankman_descr(entry)
    config = state.tankman_config(entry.nation_id)
    groups = config.premium_groups if descr.is_premium else config.groups
    group = None
    for index, candidate in enumerate(groups):
        if (candidate.is_female == descr.is_female and candidate.first_names
                and candidate.last_names and candidate.icons):
            group = (index, candidate)
            break
    runner.check(group is not None, "the nation has a group for the crew member's sex")
    index, group = group
    first = sorted(group.first_names)[0]
    last = sorted(group.last_names)[0]
    icon = sorted(group.icons)[0]

    gold_before = state.gold
    args = [1, entry.inv_id, int(descr.is_premium), 1 if descr.is_female else 0, index,
            first, index, last, index, icon]
    response = dispatcher.handle(1, cmd.CMD_TMAN_PASSPORT, (args,))
    runner.equal(response.code, cmd.RES_SUCCESS, "the passport is accepted")
    runner.equal(state.gold, gold_before - state.shop_payload()["passportChangeCost"],
                 "the rename took its gold")
    renamed = state.tankman_descr(entry)
    runner.equal((renamed.first_name_id, renamed.last_name_id, renamed.icon_id),
                 (first, last, icon), "the descriptor carries the new name")
    runner.equal(state.tankmen[entry.inv_id].passport.first_name_id, first,
                 "and the entry caches it")

    unknown = list(args)
    unknown[5] = 999999
    response = dispatcher.handle(2, cmd.CMD_TMAN_PASSPORT, (unknown,))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "a name outside the group is refused")


# ---------------------------------------------------------------------------
# CMD_TRAINING_TMAN (155) / CMD_ADD_TMAN_XP (10003)
# ---------------------------------------------------------------------------
def test_buying_crew_experience_with_gold(runner: common.Runner) -> None:
    """``freeXPToTankman`` - the convert window's gold pays for crew experience."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    descr = state.tankman_descr(entry)
    descr.add_skill("repair")
    state.set_tankman_descr(entry, descr)
    rate = state.shop_payload()["freeXPToTManXPRate"]
    runner.check(rate > 0, "the shop publishes the convert rate")

    before_total = state.tankman_descr(entry).total_xp()
    gold = 100
    gold_before = state.gold
    response = dispatcher.handle(1, cmd.CMD_TRAINING_TMAN, (1, entry.inv_id, gold))
    runner.equal(response.code, cmd.RES_SUCCESS, "the convert is accepted")
    runner.equal(state.gold, gold_before - gold, "the gold was taken")
    trained = state.tankman_descr(entry)
    runner.equal(trained.total_xp(), before_total + gold * rate,
                 "the bought experience is in the descriptor")
    runner.check(trained.last_skill_level > 0, "the skill levels up from the bought experience")

    response = dispatcher.handle(2, cmd.CMD_TRAINING_TMAN, (1, entry.inv_id, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "zero gold is a bad request")


def test_experience_goes_straight_into_the_descriptor(runner: common.Runner) -> None:
    """``CMD_ADD_TMAN_XP`` - the experience the account hands a crew member."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    descr = state.tankman_descr(entry)
    descr.add_skill("repair")
    state.set_tankman_descr(entry, descr)
    before = state.tankman_descr(entry).total_xp()

    response = dispatcher.handle(1, cmd.CMD_ADD_TMAN_XP, (entry.inv_id, 5000, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the experience is accepted")
    runner.check(state.tankman_descr(entry).total_xp() > before, "the descriptor grew")
    runner.equal(_diff_tankman(response.diffs[0], entry.inv_id), entry.compact_descr,
                 "the diff carries it")
    response = dispatcher.handle(2, cmd.CMD_ADD_TMAN_XP, (entry.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "zero experience is a bad request")


# ---------------------------------------------------------------------------
# CMD_DISMISS_TMAN (306) / CMD_TMAN_RESTORE (10015) / CMD_RETURN_CREW (157)
# ---------------------------------------------------------------------------
def test_a_dismissed_crew_member_waits_in_the_recycle_bin(runner: common.Runner) -> None:
    """``dismissTankman`` - out of the inventory, into the bin, and back again."""
    dispatcher, state = _dispatcher(runner)
    vehicle, entry = _seated(state)
    slot = vehicle.crew.index(entry.inv_id)
    descriptor = entry.compact_descr

    response = dispatcher.handle(1, cmd.CMD_DISMISS_TMAN, (entry.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the dismissal is accepted")
    runner.check(entry.inv_id not in state.tankmen, "the crew member left the barracks")
    runner.check(state.has_dismissed_tankman(entry.inv_id), "and is in the recycle bin")
    diff = response.diffs[0]
    runner.equal(_diff_tankman(diff, entry.inv_id), None,
                 "the client is told to drop it from the inventory")
    buffer = diff["recycleBin"]["tankmen"]["buffer"]
    runner.equal(buffer[entry.inv_id][0], descriptor,
                 "the bin carries the descriptor it was dismissed with")
    runner.equal(vehicle.crew[slot], None, "the seat is empty")
    runner.check(isinstance(buffer[entry.inv_id][1], float), "and a dismissal time")

    response = dispatcher.handle(2, cmd.CMD_TMAN_RESTORE, (entry.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the restore is accepted")
    runner.check(entry.inv_id in state.tankmen, "the crew member is back")
    runner.check(not state.has_dismissed_tankman(entry.inv_id), "and out of the bin")
    runner.equal(state.tankmen[entry.inv_id].compact_descr, descriptor,
                 "with the descriptor it had")
    runner.equal(response.diffs[0]["recycleBin"]["tankmen"]["buffer"][entry.inv_id], None,
                 "the client clears the entry of the bin")
    runner.equal(_diff_tankman(response.diffs[0], entry.inv_id), descriptor,
                 "and gets the crew member back")


def test_restoring_is_bounded_by_the_berths(runner: common.Runner) -> None:
    """A full barracks refuses the restore with the client's own message."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    response = dispatcher.handle(1, cmd.CMD_DISMISS_TMAN, (entry.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the dismissal is accepted")

    # Fill the barracks: every berth taken by a crew member without a vehicle.
    for index in range(state.berths):
        vehicle, slot, role = None, None, None
        try:
            vehicle, slot, role = _free_seat(state)
        except AssertionError:
            break
        crew_inv_id = vehicle.crew[slot] if slot < len(vehicle.crew) else None
        if crew_inv_id in (-1, None):
            # take a crew member of another seat and put it into the barracks
            for other in state.vehicles.values():
                for seat, tman in enumerate(other.crew):
                    if tman not in (-1, None):
                        crew = state.tankmen[tman]
                        crew.vehicle_inv_id = -1
                        other.crew[seat] = None
                        break
                else:
                    continue
                break
    runner.equal(state.free_berths(), 0, "the barracks is full")
    response = dispatcher.handle(2, cmd.CMD_TMAN_RESTORE, (entry.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_FAILURE, "the restore is refused")
    runner.equal(response.error, barracks_module.ERROR_NOT_ENOUGH_SPACE,
                 "with the message the client shows for it")


def test_returning_the_crew_of_a_vehicle(runner: common.Runner) -> None:
    """``returnCrew`` - the whole crew of a vehicle goes back into the barracks."""
    dispatcher, state = _dispatcher(runner)
    vehicle, entry = _seated(state)
    seated = [inv_id for inv_id in vehicle.crew if inv_id not in (-1, None)]
    runner.check(seated, "the vehicle has a crew")
    runner.check(state.free_berths() >= len(seated), "and there is room in the barracks")

    response = dispatcher.handle(1, cmd.CMD_RETURN_CREW, (vehicle.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_SUCCESS, "the crew returns")
    runner.equal([inv_id for inv_id in vehicle.crew if inv_id not in (-1, None)], [],
                 "the seats are empty")
    for inv_id in seated:
        runner.equal(state.tankmen[inv_id].vehicle_inv_id, -1,
                     "crew %s is in the barracks" % inv_id)
        section = response.diffs[0]["inventory"][1]
        runner.equal(section["crew"][vehicle.inv_id].count(None), len(seated),
                     "the client's crew list has the empty seats")
    response = dispatcher.handle(2, cmd.CMD_RETURN_CREW, (vehicle.inv_id, 0, 0))
    runner.equal(response.code, cmd.RES_WRONG_ARGS, "an empty vehicle is a bad request")


# ---------------------------------------------------------------------------
# The account keeps what happened
# ---------------------------------------------------------------------------
def test_the_barracks_survives_a_restart(runner: common.Runner) -> None:
    """The dismissals and the rewritten descriptors are in the save file."""
    dispatcher, state = _dispatcher(runner)
    _vehicle, entry = _seated(state)
    dispatcher.handle(1, cmd.CMD_TMAN_ADD_SKILL, (entry.inv_id, SKILL_BROTHERHOOD, 0))
    _vehicle2, other = None, None
    for candidate in state.tankmen.values():
        if candidate.inv_id != entry.inv_id and candidate.role == "commander":
            other = candidate
            break
    runner.check(other is not None, "the account has another commander to dismiss")
    other_vehicle = state.vehicles[other.vehicle_inv_id]
    dispatcher.handle(2, cmd.CMD_DISMISS_TMAN, (other.inv_id, 0, 0))
    dismissed = other.inv_id
    skills = state.tankman_descr(entry).skills

    reloaded = type(state).from_json(state.to_json(), state.catalog,
                                     tankman_factory=state.tankman_factory)
    runner.equal(reloaded.tankman_descr(reloaded.tankmen[entry.inv_id]).skills, skills,
                 "the learned skill is saved")
    runner.check(reloaded.has_dismissed_tankman(dismissed), "the dismissals are saved")
    runner.equal(reloaded.dismissed_tankmen[dismissed][0], other.compact_descr,
                 "with the descriptor they were dismissed with")
    diff = reloaded.full_diff()
    runner.equal(diff["recycleBin"]["tankmen"]["buffer"][dismissed][0], other.compact_descr,
                 "and a full sync publishes them")
    runner.equal(other_vehicle.crew.count(dismissed), 0, "the seat of a dismissal is free")


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
