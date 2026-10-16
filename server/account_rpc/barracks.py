"""The barracks: dismissing, restoring, retraining and the crew's own paperwork.

Every command here is sent by the client's own helpers, so the argument order is
theirs (``client/account_helpers/Inventory.py`` / ``client_recycle_bin.py``):

=================================  ===============================================
``CMD_DISMISS_TMAN`` (306)         ``Int3 (tmanInvID, 0, 0)``
``CMD_RETURN_CREW`` (157)          ``Int3 (vehInvID, 0, 0)`` - the whole crew of a
                                   vehicle goes back into the barracks
``CMD_TMAN_ADD_SKILL`` (151)       ``Int3 (tmanInvID, skillIdx, 0)``
``CMD_TMAN_DROP_SKILLS`` (152)     ``Int3 (shopRev, tmanInvID, dropSkillsCostIdx)``
``CMD_TMAN_RESPEC`` (153)          ``Int4 (shopRev, tmanInvID, tmanCostTypeIdx,
                                   vehTypeCompDescr)``
``CMD_TMAN_PASSPORT`` (154)        ``IntArr [shopRev, tmanInvID, isPremium,
                                   isFemale, fnGroupID, firstNameID, lnGroupID,
                                   lastNameID, iGroupID, iconID]`` - ``-1`` in a
                                   name field means "keep what is there"
``CMD_TRAINING_TMAN`` (155)        ``Int3 (shopRev, tmanInvID, gold)`` - the gold
                                   the convert window computed for the crew XP
``CMD_TMAN_MULTI_RESPEC`` (156)    ``IntArr [shopRev, vehTypeCompDescr, tmanInvID,
                                   tmanCostTypeIdx, …]``
``CMD_TMAN_CHANGE_ROLE`` (158)     ``Int4 (shopRev, tmanInvID, roleIdx,
                                   vehTypeCompDescr)`` - the ``ext`` of the answer
                                   is an ``EQUIP_TMAN_CODE``
``CMD_TMAN_RESTORE`` (10015)       ``Int3 (tmanInvID, 0, 0)``
``CMD_ADD_TMAN_XP`` (10003)        ``Int3 (tmanInvID, xp, 0)``
=================================  ===============================================

Crew members *are* descriptors, so every operation ends by rewriting
``inventory[8]['compDescr'][invID]`` (``server.game.tankmen.TankmanDescr`` does
the arithmetic the client would do) and by publishing the new bytes in the diff
of the answer.  A dismissed crew member leaves the inventory for the recycle bin
(``recycleBin.tankmen.buffer[invID] = (compDescr, dismissedAt)``), which is where
``ItemsRequester`` looks for the negative inventory ids the barracks window
shows; restoring puts it back.

The refusals are named after the client's own ``#system_messages`` keys
(``dismiss_tankman/…``, ``retraining_tankman/…``, …), so a refused operation
shows the message the live game would show instead of a generic one.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..game import tankmen as tankmen_module
from . import commands as cmd
from .purchases import PurchaseError, Shopkeeper

#: ``constants.EQUIP_TMAN_CODE`` - the ``ext`` of a role change, which tells the
#: client why the crew member did or did not end up in the vehicle.
EQUIP_TMAN_OK = 0
EQUIP_TMAN_NO_VEHICLE = 1
EQUIP_TMAN_VEHICLE_LOCKED = 2
EQUIP_TMAN_NO_FREE_SLOT = 3

#: The refusals, spelled as the keys of the client's ``#system_messages`` table.
ERROR_SERVER = "server_error"
ERROR_INVALID_VEHICLE = "invalid_vehicle"
ERROR_INVALID_OPERATION = "invalid_operation"
ERROR_INVALID_ROLE = "invalid_role"
ERROR_VEHICLE_LOCKED = "vehicle_locked"
ERROR_NOT_ENOUGH_SPACE = "not_enough_space"
ERROR_NOT_ENOUGH_MONEY = "not_enough_money"
ERROR_NOT_ENOUGH_GOLD = "not_enough_gold"
ERROR_FORBIDDEN = "FORBIDDEN"

#: ``CMD_TMAN_ADD_SKILL`` sends the skill index of ``skills_constants`` - the
#: position in the client's padded ``SKILL_NAMES`` table.
SKILL_NAMES_BY_INDEX = {
    index: name
    for index, name in enumerate(tankmen_module.SKILL_NAMES)
    if not name.startswith("reserved")
}


class BarracksError(PurchaseError):
    """A refused barracks operation: a code plus the client's own error key."""


class Barracks(object):
    """The crew of one account, with the money the operations cost."""

    def __init__(self, state, shopkeeper: Optional[Shopkeeper] = None):
        self.state = state
        self.shop = shopkeeper if shopkeeper is not None else Shopkeeper(state)

    # -- helpers --------------------------------------------------------
    def _shop_payload(self) -> dict:
        return self.state.shop_builder.payload()

    def tankman(self, inv_id):
        """The barracks entry of ``inv_id``; a dismissed one is not crew."""
        entry = self.state.tankmen.get(_as_int(inv_id))
        if entry is None:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_VEHICLE)
        return entry

    def descr(self, entry):
        """The mutable descriptor of one crew member."""
        return self.state.tankman_descr(entry)

    def vehicle(self, inv_id: int):
        """``(entry, vehicle type)`` of a garage vehicle."""
        entry = self.state.vehicles.get(_as_int(inv_id))
        vehicle = self.state.vehicle_type(entry) if entry is not None else None
        if entry is None or vehicle is None:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_VEHICLE)
        return entry, vehicle

    def _charge(self, credits: int = 0, gold: int = 0,
                error: str = ERROR_NOT_ENOUGH_MONEY) -> None:
        if credits <= 0 and gold <= 0:
            return
        try:
            self.shop.charge(credits=credits, gold=gold)
        except PurchaseError:
            raise BarracksError(cmd.RES_FAILURE, error)

    def _save(self, entry, descr, economics: bool = False) -> dict:
        """Store the rewritten descriptor and answer with its diff.

        Only the ``compDescr`` of that one crew member travels: the client
        replaces the item of ``inventory[8]`` from it, so the rest of the
        section would be noise (and a diff of a few hundred crew members is a
        packet the client has to unpickle for nothing).
        """
        self.state.set_tankman_descr(entry, descr)
        sections = {"inventory": self.state.partial_inventory_diff(
            tankman_inv_ids=[entry.inv_id])}
        if economics:
            sections["economics"] = self.state.economics_diff()
        return {"code": cmd.RES_SUCCESS, "ext": None,
                "diff": self.state.incremental_diff(extra=sections)}

    # -- CMD_DISMISS_TMAN (306) -----------------------------------------
    def dismiss(self, inv_id) -> dict:
        """Send a crew member to the recycle bin of the barracks."""
        entry = self.tankman(inv_id)
        vehicle_inv_id = entry.vehicle_inv_id
        vehicle_inv_ids: List[int] = []
        if vehicle_inv_id not in (-1, None, 0):
            vehicle_entry = self.state.vehicles.get(vehicle_inv_id)
            if vehicle_entry is not None and entry.inv_id in vehicle_entry.crew:
                vehicle_entry.crew[vehicle_entry.crew.index(entry.inv_id)] = None
            vehicle_inv_ids.append(int(vehicle_inv_id))
        self.state.dismiss_tankman(entry)
        diff = self.state.incremental_diff(extra={
            "inventory": self.state.partial_inventory_diff(
                vehicle_inv_ids=vehicle_inv_ids, tankman_inv_ids=[entry.inv_id]),
            "recycleBin": self.state.recycle_bin_diff([entry.inv_id]),
        })
        return {"code": cmd.RES_SUCCESS, "ext": None, "diff": diff}

    # -- CMD_TMAN_RESTORE (10015) ---------------------------------------
    def restore(self, inv_id) -> dict:
        """Bring a dismissed crew member back into the barracks."""
        inv_id = abs(_as_int(inv_id))
        if not self.state.has_dismissed_tankman(inv_id):
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_SERVER)
        if self.state.free_berths() < 1:
            raise BarracksError(cmd.RES_FAILURE, ERROR_NOT_ENOUGH_SPACE)
        credits, gold = self.restore_price(inv_id)
        self._charge(credits=credits, gold=gold,
                     error=ERROR_NOT_ENOUGH_GOLD if gold else ERROR_NOT_ENOUGH_MONEY)
        entry = self.state.restore_tankman(inv_id)
        sections = {
            "inventory": self.state.partial_inventory_diff(tankman_inv_ids=[entry.inv_id]),
            "recycleBin": self.state.recycle_bin_diff([inv_id]),
        }
        if credits or gold:
            sections["economics"] = self.state.economics_diff()
        return {"code": cmd.RES_SUCCESS, "ext": None,
                "diff": self.state.incremental_diff(extra=sections)}

    # -- CMD_RETURN_CREW (157) ------------------------------------------
    def return_crew(self, veh_inv_id) -> dict:
        """Unload the whole crew of a vehicle into the barracks."""
        entry, vehicle = self.vehicle(veh_inv_id)
        seated = [inv_id for inv_id in entry.crew if inv_id not in (-1, None)]
        if not seated:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_SERVER)
        if self.state.free_berths() < len(seated):
            raise BarracksError(cmd.RES_FAILURE, ERROR_NOT_ENOUGH_SPACE)
        for slot, inv_id in enumerate(entry.crew):
            if inv_id in (-1, None):
                continue
            crew_entry = self.state.tankmen.get(inv_id)
            if crew_entry is not None:
                crew_entry.vehicle_inv_id = -1
            entry.crew[slot] = None
        diff = self.state.incremental_diff(extra={
            "inventory": self.state.partial_inventory_diff(
                vehicle_inv_ids=[entry.inv_id], tankman_inv_ids=seated),
        })
        return {"code": cmd.RES_SUCCESS, "ext": None, "diff": diff}

    # -- CMD_TMAN_ADD_SKILL (151) ---------------------------------------
    def add_skill(self, inv_id, skill_index) -> dict:
        """Let a crew member learn a skill its role may hold."""
        entry = self.tankman(inv_id)
        name = SKILL_NAMES_BY_INDEX.get(_as_int(skill_index))
        if name is None:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        descr = self.descr(entry)
        if not descr.can_add_skill(name):
            raise BarracksError(cmd.RES_FAILURE, ERROR_INVALID_OPERATION)
        descr.add_skill(name)
        return self._save(entry, descr)

    # -- CMD_TMAN_DROP_SKILLS (152) -------------------------------------
    def drop_skills(self, inv_id, cost_index) -> dict:
        """Forget every skill; the chosen price decides how much XP comes back."""
        entry = self.tankman(inv_id)
        descr = self.descr(entry)
        if not descr.skills:
            raise BarracksError(cmd.RES_FAILURE, ERROR_INVALID_OPERATION)
        cost_index = _as_int(cost_index)
        cost = self._drop_skills_cost(cost_index)
        if descr.is_free_drop_skills() and cost_index != 0:
            raise BarracksError(cmd.RES_FAILURE, ERROR_INVALID_OPERATION)
        credits = int(cost.get("credits", 0) or 0)
        gold = int(cost.get("gold", 0) or 0)
        self._charge(credits=credits, gold=gold,
                     error=ERROR_NOT_ENOUGH_GOLD if gold else ERROR_NOT_ENOUGH_MONEY)
        descr.drop_skills(float(cost.get("xpReuseFraction", 0.0) or 0.0))
        return self._save(entry, descr, economics=bool(credits or gold))

    def _drop_skills_cost(self, index: int) -> dict:
        table = self._shop_payload().get("dropSkillsCost") or {}
        cost = table.get(index)
        if cost is None:
            cost = table.get(str(index))
        if cost is None:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        return cost

    # -- CMD_TMAN_RESPEC (153) / CMD_TMAN_MULTI_RESPEC (156) ------------
    def respec(self, inv_id, cost_index, veh_type_comp_descr) -> dict:
        """Retrain one crew member onto another vehicle."""
        return self._respec_many([(inv_id, cost_index)], veh_type_comp_descr)

    def multi_respec(self, pairs: Sequence[Tuple[int, int]], veh_type_comp_descr) -> dict:
        """Retrain a whole crew onto another vehicle at one price."""
        return self._respec_many(pairs, veh_type_comp_descr)

    def _respec_many(self, pairs, veh_type_comp_descr) -> dict:
        if not pairs:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        new_vehicle = self.state.catalog.vehicles.get(_as_int(veh_type_comp_descr))
        if new_vehicle is None:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_VEHICLE)
        costs = {index: cost for index, cost in
                 enumerate(self._shop_payload().get("tankmanCost") or ())}
        planned: List[Tuple[object, object, dict]] = []
        for inv_id, cost_index in pairs:
            entry = self.tankman(inv_id)
            if entry.nation_id != new_vehicle.nation_id:
                raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_VEHICLE)
            cost = costs.get(_as_int(cost_index))
            descr = self.descr(entry)
            if cost is None or slot_for_role(new_vehicle, descr.role) is None:
                # The client's ``VehicleRoleValidator`` only offers vehicles
                # whose crew has the role, so this is a disagreeing client.
                raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
            planned.append((entry, descr, cost))
        total_credits = sum(int(cost.get("credits", 0) or 0) for _, _, cost in planned)
        total_gold = sum(int(cost.get("gold", 0) or 0) for _, _, cost in planned)
        self._charge(credits=total_credits, gold=total_gold,
                     error=ERROR_NOT_ENOUGH_GOLD if total_gold else ERROR_NOT_ENOUGH_MONEY)
        for entry, descr, cost in planned:
            old_vehicle = self.state.vehicle_type_of(entry)
            descr.respecialize(
                new_vehicle.innation_id,
                vehicle_tags=old_vehicle.tags if old_vehicle is not None else (),
                new_vehicle_tags=new_vehicle.tags,
                min_new_role_level=int(cost.get("roleLevel", tankmen_module.MIN_ROLE_LEVEL)),
                vehicle_change_role_level_loss=float(cost.get("baseRoleLoss", 0.0) or 0.0),
                class_change_role_level_loss=float(cost.get("classChangeRoleLoss", 0.0) or 0.0),
            )
            self.state.set_tankman_descr(entry, descr)
        diff = self.state.incremental_diff(extra={
            "inventory": self.state.partial_inventory_diff(
                tankman_inv_ids=[entry.inv_id for entry, _, _ in planned]),
            "economics": self.state.economics_diff(),
        })
        return {"code": cmd.RES_SUCCESS, "ext": None, "diff": diff}

    # -- CMD_TMAN_CHANGE_ROLE (158) --------------------------------------
    def change_role(self, inv_id, role_index, veh_type_comp_descr) -> dict:
        """Give a crew member another role, seating it in a vehicle if it can."""
        entry = self.tankman(inv_id)
        index = _as_int(role_index)
        name = tankmen_module.SKILL_NAMES[index] \
            if 0 <= index < len(tankmen_module.SKILL_NAMES) else None
        if name not in tankmen_module.ROLES or name == entry.role:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_ROLE)
        gold = int(self._shop_payload().get("changeRoleCost", 0) or 0)
        self._charge(gold=gold, error=ERROR_NOT_ENOUGH_GOLD)
        descr = self.descr(entry)
        try:
            descr.change_role(name)
        except tankmen_module.TankmenError:
            raise BarracksError(cmd.RES_FAILURE, ERROR_INVALID_ROLE)
        self.state.set_tankman_descr(entry, descr)
        code = self._seat_if_possible(entry, veh_type_comp_descr)
        vehicles: List[int] = []
        if entry.vehicle_inv_id not in (-1, None, 0):
            vehicles.append(int(entry.vehicle_inv_id))
        diff = self.state.incremental_diff(extra={
            "inventory": self.state.partial_inventory_diff(
                vehicle_inv_ids=vehicles, tankman_inv_ids=[entry.inv_id]),
            "economics": self.state.economics_diff(),
        })
        return {"code": cmd.RES_SUCCESS, "ext": code, "diff": diff}

    def _seat_if_possible(self, entry, veh_type_comp_descr) -> int:
        """``EQUIP_TMAN_CODE``: seat the crew member in the vehicle it was sent to."""
        compact = _as_int(veh_type_comp_descr)
        if not compact:
            return EQUIP_TMAN_NO_VEHICLE
        for veh_inv_id, vehicle_entry in self.state.vehicles.items():
            vehicle = self.state.vehicle_type(vehicle_entry)
            if vehicle is None or vehicle.compact_descr != compact:
                continue
            slot = slot_for_role(vehicle, entry.role)
            if slot is None:
                return EQUIP_TMAN_NO_FREE_SLOT
            while len(vehicle_entry.crew) < len(vehicle.crew_roles):
                vehicle_entry.crew.append(None)
            previous = vehicle_entry.crew[slot]
            if previous not in (-1, None) and previous != entry.inv_id:
                # ``change_tankman_role/slot_is_taken`` - the seat is taken.
                return EQUIP_TMAN_NO_FREE_SLOT
            vehicle_entry.crew[slot] = entry.inv_id
            entry.vehicle_inv_id = int(veh_inv_id)
            return EQUIP_TMAN_OK
        return EQUIP_TMAN_NO_VEHICLE

    # -- CMD_TMAN_PASSPORT (154) ----------------------------------------
    def change_passport(self, inv_id, args: Sequence) -> dict:
        """Rename a crew member inside the groups of its nation."""
        entry = self.tankman(inv_id)
        is_premium, is_female, fn_group, first_name, ln_group, last_name, i_group, icon = (
            _as_int(value, -1) for value in args)
        descr = self.descr(entry)
        if is_premium not in (-1, 0, 1) or (is_premium != -1 and
                                            bool(is_premium) != bool(descr.is_premium)):
            # The client only ever sends the flag the crew member already has;
            # the group tables differ, so a changed flag cannot be validated.
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        config = self.state.tankman_config(entry.nation_id)
        if config is None:
            raise BarracksError(cmd.RES_FAILURE, ERROR_SERVER)
        female = None if is_female == -1 else bool(is_female)
        groups = (config.premium_groups or config.groups) if descr.is_premium \
            else config.groups
        if not groups:
            raise BarracksError(cmd.RES_FAILURE, ERROR_SERVER)
        ok, _error, ctx = descr.validate_passport(
            bool(descr.is_premium), female,
            fn_group, None if first_name == -1 else first_name,
            ln_group, None if last_name == -1 else last_name,
            i_group, None if icon == -1 else icon, groups)
        if not ok:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        gold = self._passport_price(bool(descr.is_female) if female is None else female)
        self._charge(gold=gold, error=ERROR_NOT_ENOUGH_MONEY)
        descr.replace_passport(ctx)
        return self._save(entry, descr, economics=bool(gold))

    def _passport_price(self, is_female: bool) -> int:
        """``passportFemaleChangeCost``/``passportChangeCost``, as the client reads it."""
        payload = self._shop_payload()
        key = "femalePassportChangeCost" if is_female else "passportChangeCost"
        price = payload.get(key)
        if price is None:
            price = payload.get("passportChangeCost", 0)
        return int(price or 0)

    # -- CMD_TRAINING_TMAN (155) / CMD_ADD_TMAN_XP (10003) ---------------
    def free_xp_to_tankman(self, inv_id, gold) -> dict:
        """Give a crew member crew experience for gold.

        The convert window of the client computes the amount itself out of
        ``shop.freeXPToTManXPRate`` (crew XP per gold) and shows it as the price,
        so that is exactly what the server charges and pays out.
        """
        entry = self.tankman(inv_id)
        gold = _as_int(gold)
        if gold <= 0:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        rate = float(self._shop_payload().get("freeXPToTManXPRate", 0) or 0)
        if rate <= 0:
            raise BarracksError(cmd.RES_FAILURE, ERROR_INVALID_OPERATION)
        self._charge(gold=gold, error=ERROR_NOT_ENOUGH_GOLD)
        descr = self.descr(entry)
        descr.add_xp(int(round(gold * rate)))
        return self._save(entry, descr, economics=True)

    def add_xp(self, inv_id, xp) -> dict:
        """``CMD_ADD_TMAN_XP`` - experience straight into the descriptor."""
        entry = self.tankman(inv_id)
        amount = _as_int(xp)
        if amount <= 0:
            raise BarracksError(cmd.RES_WRONG_ARGS, ERROR_INVALID_OPERATION)
        descr = self.descr(entry)
        descr.add_xp(amount)
        return self._save(entry, descr)

    # -- the recycle bin ------------------------------------------------
    def restore_price(self, inv_id: int) -> Tuple[int, int]:
        """``(credits, gold)`` a restore costs; ``(0, 0)`` while it is free."""
        dismissed_at = self.state.dismissed_at(inv_id)
        if dismissed_at is None:
            return (0, 0)
        config = (self._shop_payload().get("restore_config") or {}).get("tankmen", {})
        free = float(config.get("freeDuration", 0) or 0)
        if _now() - dismissed_at < free:
            return (0, 0)
        return (int(config.get("creditsCost", 0) or 0),
                int(config.get("goldCost", 0) or 0))


def slot_for_role(vehicle, role: str) -> Optional[int]:
    """The first crew slot of a vehicle that can hold ``role``."""
    for slot, roles in enumerate(vehicle.crew_roles):
        if role in roles:
            return slot
    return None


def _now() -> float:
    import time

    return time.time()


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "Barracks",
    "BarracksError",
    "EQUIP_TMAN_NO_FREE_SLOT",
    "EQUIP_TMAN_NO_VEHICLE",
    "EQUIP_TMAN_OK",
    "EQUIP_TMAN_VEHICLE_LOCKED",
    "SKILL_NAMES_BY_INDEX",
    "slot_for_role",
]
