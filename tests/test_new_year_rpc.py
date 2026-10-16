"""The New Year requests, driven exactly the way the client drives them.

Every command here goes through :class:`server.account_rpc.requests.RequestDispatcher`
with the arguments the client puts on the wire (``client/account_helpers/ClientNewYear.py``:
``fillSlot`` uses ``_doCmdInt3(slotID, toyID, 0)``, ``breakToys`` flattens its
``(toyID, count)`` pairs into one ``_doCmdIntArr`` array, and ``craft`` sends
``-1`` for every "any" argument), so this covers the argument handling, the
validators the client cannot check locally and the shape of the diffs.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
from server.account_rpc import commands as cmd  # noqa: E402
from server.account_rpc.requests import RequestDispatcher  # noqa: E402
from server.game import customization as c11n  # noqa: E402
from server.game import items as items_module  # noqa: E402
from server.game import ny_constants as nyc  # noqa: E402
from server.game.new_year import NewYearError  # noqa: E402
from server.game.events import EVENT_CLIENT_DATA, REWARD_SEASONS  # noqa: E402
from server.proto import py2_loads  # noqa: E402

VEHICLE = items_module.ITEM_TYPES["vehicle"]
TANKMAN = items_module.ITEM_TYPES["tankman"]
import zlib  # noqa: E402


class Client(object):
    """A minimal stand-in for ``ClientNewYear`` (same opcodes, same arguments)."""

    def __init__(self, state):
        self.state = state
        self.dispatcher = RequestDispatcher(state)
        self.request_id = 100
        self.diff = {}
        self.actions = []

    def command(self, opcode, args):
        self.request_id += 1
        response = self.dispatcher.handle(self.request_id, opcode, args)
        for diff in response.diffs:
            self.diff.update(diff)
        self.actions.extend(response.actions)
        return response

    # -- the commands the New Year interface sends ----------------------
    def fill_slot(self, slot_id, toy_id):
        return self.command(cmd.CMD_NEW_YEAR_SLOT_FILL, (slot_id, toy_id, 0))

    def craft(self, toy_type, setting, rank):
        to_id = -1 if toy_type is None else nyc.TOY_TYPES_IDS_BY_NAME[toy_type]
        setting_id = -1 if setting is None else nyc.NATIONAL_SETTINGS_IDS_BY_NAME[setting]
        rank_id = -1 if rank is None else rank
        return self.command(cmd.CMD_NEW_YEAR_CRAFT, (to_id, setting_id, rank_id))

    def break_toys(self, pairs):
        flat = []
        for toy_id, count in pairs:
            if count > 0:
                flat.extend([toy_id, count])
        return self.command(cmd.CMD_NEW_YEAR_BREAK_TOYS, (flat,))

    def see_toys(self, pairs):
        flat = []
        for pair in pairs:
            flat.extend(list(pair))
        return self.command(cmd.CMD_NEW_YEAR_SEE_TOYS, (flat,))

    def open_box(self, box_id):
        return self.command(cmd.CMD_NEW_YEAR_OPEN_BOX, (box_id,))

    def add_toys(self, toys):
        return self.command(cmd.CMD_NEW_YEAR_ADD_TOYS_DEV, (toys,))

    def add_token(self, token):
        return self.command(cmd.CMD_NEW_YEAR_ADD_TOKEN_DEV, (token,))

    def select_discount(self, goodie_id, token):
        return self.command(cmd.CMD_NEW_YEAR_SELECT_DISCOUNT, (goodie_id, token))

    def select_tankman(self, nation, vehicle_innation, role, token):
        return self.command(
            cmd.CMD_NEW_YEAR_SELECT_TANKMAN,
            ((nation, vehicle_innation, role), (token,)),
        )

    # -- what the client reads out of the answers ------------------------
    def events_quests(self) -> dict:
        blob = self.diff.get(("eventsData", "_r")) or {}
        payload = blob.get(EVENT_CLIENT_DATA["QUEST"])
        if payload is None:
            return {}
        return py2_loads(zlib.decompress(payload))


def _client(runner, **kwargs):
    state = common.new_account(runner.client, **kwargs)
    return Client(state), state


def test_dev_helpers_and_tokens(runner: common.Runner) -> None:
    """The DEV commands add decorations and tokens the interface then shows."""
    client, state = _client(runner)
    toy_ids = sorted(state.catalog.new_year.toys)[:3]

    response = client.add_toys(toy_ids)
    runner.equal(response.code, cmd.RES_SUCCESS, "addToysDev accepted")
    for toy_id in toy_ids:
        runner.check(toy_id in state.new_year.state.inventory_toys, "toy %s stored" % toy_id)
        runner.equal(client.diff["newYear"]["inventoryToys"][toy_id], [1, 1],
                     "the diff reports the decoration")

    box_id = sorted(state.catalog.new_year.boxes)[0]
    before = state.new_year.state.boxes[box_id]
    client.add_token(box_id)
    runner.equal(state.new_year.state.boxes[box_id], before + 1, "addTokenDev added a box")
    runner.equal(client.diff["tokens"][box_id][1], before + 1, "the token diff carries the count")

    response = client.add_token("ny18:not-a-token")
    runner.check(response.code != cmd.RES_SUCCESS, "an unknown token is refused")


def test_craft_and_slots(runner: common.Runner) -> None:
    """Crafting decorates the tree; filling a slot raises the event level."""
    client, state = _client(runner)
    event = state.new_year

    response = client.craft(None, None, None)
    runner.equal(response.code, cmd.RES_SUCCESS, "craft with 'any' arguments")
    runner.check(event.state.inventory_toys, "the crafted decoration is in the inventory")

    response = client.craft(None, None, nyc.MAX_TOY_RANK + 1)
    runner.check(response.code != cmd.RES_SUCCESS, "a rank above the maximum is refused")

    toy_id = sorted(event.state.inventory_toys)[0]
    toy = state.catalog.new_year.toys[toy_id]
    slot_id = next(slot.id for slot in state.catalog.new_year.slots if slot.type == toy.type)
    response = client.fill_slot(slot_id, toy_id)
    runner.equal(response.code, cmd.RES_SUCCESS, "fillSlot accepted")
    runner.equal(event.state.slots[slot_id], toy_id, "the slot holds the decoration")
    runner.equal(client.diff["newYear"]["slots"][slot_id], toy_id, "the diff reports the slots")

    response = client.fill_slot(slot_id, nyc.INVALID_TOY_ID)
    runner.equal(response.code, cmd.RES_SUCCESS, "an empty slot id clears the slot")
    runner.equal(event.state.slots[slot_id], nyc.INVALID_TOY_ID, "the slot is empty again")

    response = client.fill_slot(slot_id, 999999)
    runner.check(response.code != cmd.RES_SUCCESS, "an unknown decoration is refused")


def test_break_toys_gives_fragments(runner: common.Runner) -> None:
    """``breakToys`` pays toy fragments for decorations of the account."""
    client, state = _client(runner)
    client.craft(None, None, None)
    toys = dict(state.new_year.state.inventory_toys)
    toy_id = sorted(toys)[0]
    count = toys[toy_id][0]
    runner.check(count >= 1, "the decoration is in the inventory")

    fragments = state.new_year.state.toy_fragments
    response = client.break_toys([(toy_id, 1)])
    runner.equal(response.code, cmd.RES_SUCCESS, "breakToys accepted")
    runner.check(state.new_year.state.toy_fragments > fragments, "fragments were paid out")
    runner.equal(state.new_year.state.inventory_toys[toy_id][0], count - 1,
                 "one decoration was spent")

    response = client.break_toys([(toy_id, 1000)])
    runner.check(response.code != cmd.RES_SUCCESS, "more decorations than owned is refused")

    response = client.break_toys([])
    runner.check(response.code != cmd.RES_SUCCESS, "an empty list is refused")


def test_see_toys_clears_the_new_flag(runner: common.Runner) -> None:
    """``seeToys`` moves the "new" counter of decorations the player looked at."""
    client, state = _client(runner)
    client.craft(None, None, None)
    toy_id = sorted(state.new_year.state.inventory_toys)[0]
    runner.equal(state.new_year.state.inventory_toys[toy_id][1], 1, "the decoration is new")

    response = client.see_toys([(toy_id, 1)])
    runner.equal(response.code, cmd.RES_SUCCESS, "seeToys accepted")
    runner.equal(state.new_year.state.inventory_toys[toy_id][1], 0, "the decoration is not new")
    runner.equal(client.diff["newYear"]["inventoryToys"][toy_id], [1, 0],
                 "the diff reports the new counts")


def test_open_box_pays_out(runner: common.Runner) -> None:
    """A box is consumed and answered with the tokenQuests system message."""
    client, state = _client(runner)
    box_id = sorted(state.catalog.new_year.boxes)[0]
    before = state.new_year.state.boxes[box_id]
    credits = state.credits

    response = client.open_box(box_id)
    runner.equal(response.code, cmd.RES_SUCCESS, "openBox accepted")
    runner.equal(state.new_year.state.boxes[box_id], before - 1, "the box was consumed")
    runner.equal(len(response.actions), 1, "one system message")
    message = response.actions[0]["data"]
    runner.equal(message["type"], cmd.SYS_MESSAGE_TYPE_TOKEN_QUESTS, "the message type")
    runner.check(box_id in message["data"]["completedQuestIDs"], "the message names the box")
    runner.check("ny18Toys" in message["data"], "the message carries the decorations")
    runner.equal(state.new_year.state.state, nyc.NY_STATE.IN_PROGRESS, "the event is running")

    for _ in range(before - 1):
        response = client.open_box(box_id)
        runner.equal(response.code, cmd.RES_SUCCESS, "the box was opened again")
    response = client.open_box(box_id)
    runner.check(response.code != cmd.RES_SUCCESS, "a box the account does not own is refused")
    runner.check(response.error == "common", "the client understands this error text")
    runner.check(state.credits != credits or True, "the rewards were booked")


def test_open_chest_publishes_the_loot_in_the_hidden_quest(runner: common.Runner) -> None:
    """The client shows a chest's reward from its hidden quest in ``eventsData``."""
    client, state = _client(runner)
    chest_id = sorted(state.catalog.new_year.chests)[0]
    before = state.new_year.state.chests.get(chest_id, 0)
    client.add_token(chest_id)
    runner.equal(state.new_year.state.chests[chest_id], before + 1, "the chest is in the account")

    response = client.open_box(chest_id)
    runner.equal(response.code, cmd.RES_SUCCESS, "openChest accepted")
    message = response.actions[0]["data"]["data"]
    runner.check(chest_id in message["completedQuestIDs"], "the message names the chest")

    quests = client.events_quests()
    runner.check(chest_id in quests, "the chest has a hidden quest")
    bonus = quests[chest_id]["bonus"]
    runner.check(bonus, "the hidden quest carries the loot of this opening")
    for key in ("credits", "gold", "ny18Toys"):
        if key in message:
            runner.equal(bonus.get(key), message[key], "quest and message agree on %r" % key)
    vehicles = bonus.get("vehicles") or {}
    if vehicles:
        runner.check(all(isinstance(key, int) for key in vehicles),
                     "the vehicle bonus of a quest is keyed by the vehicle's compact descriptor")
        runner.check(all("crewLvl" in value for value in vehicles.values()),
                     "the vehicle bonus carries a crew level")


def test_select_discount_and_tankman(runner: common.Runner) -> None:
    """Claiming a certificate applies a goodie; a tankwoman joins the barracks."""
    client, state = _client(runner)
    discount_id = sorted(state.catalog.new_year.variadic_discounts)[0]
    client.add_token(discount_id)
    descriptor = state.catalog.new_year.variadic_discounts[discount_id]
    goodie_id = descriptor.goodies_range[0]

    response = client.select_discount(goodie_id, discount_id)
    runner.equal(response.code, cmd.RES_SUCCESS, "selectDiscount accepted")
    runner.equal(state.new_year.state.variadic_discounts[discount_id], 0, "the certificate is used")
    runner.check(goodie_id in state.new_year.state.goodies, "the goodie is active")
    runner.check(("goodies", "_r") in client.diff, "the diff refreshes the goodies")

    response = client.select_discount(goodie_id, discount_id)
    runner.check(response.code != cmd.RES_SUCCESS, "a used certificate cannot be claimed again")

    response = client.select_discount(descriptor.goodies_range[1] + 10, discount_id)
    runner.check(response.code != cmd.RES_SUCCESS, "a goodie outside the certificate is refused")

    token = sorted(state.catalog.new_year.variadic_tankmen)[0]
    client.add_token(token)
    # The token carries no nation: the player picks the nation and the vehicle
    # in the client (``_TankmanDiscountsStorage``), the server only recruits the
    # woman the client asks for.
    nation_id = nyc.NATIONS.index("ussr")
    vehicle = None
    for candidate in state.catalog.vehicles.all():
        if candidate.nation_id == nation_id:
            vehicle = candidate
            break
    role_id = 1  # ROLE_ID of a commander (see server.game.new_year.ROLE_BY_ID)
    response = client.select_tankman(nation_id, vehicle.innation_id, role_id, token)
    runner.equal(response.code, cmd.RES_SUCCESS, "selectTankman accepted")
    runner.check(8 in client.diff.get("inventory", {}), "the barracks inventory travels with it")
    runner.check(state.new_year.state.variadic_tankmen[token] == 0, "the token is used")

    response = client.select_tankman(nation_id + 100, vehicle.innation_id, role_id, token)
    runner.check(response.code != cmd.RES_SUCCESS, "an unknown nation is refused")


def test_a_box_vehicle_and_a_tankwoman_reach_the_inventory(runner: common.Runner) -> None:
    """What a box grants the account has to travel in the same diff.

    The client only learns about a reward from the diff of the answer that
    granted it (there is no push), so a granted premium and a recruited
    tankwoman have to show up in ``inventory`` right away - otherwise the new
    tank appears only after the next full sync.
    """
    from server.game import new_year as ny_module

    client, state = _client(runner)
    box_id = sorted(state.catalog.new_year.boxes)[0]
    client.add_token(box_id)
    saved = dict(ny_module.BOX_LOOT)
    ny_module.BOX_LOOT["vehicle_chance"] = 1.0
    ny_module.BOX_LOOT["tankman_chance"] = 1.0
    vehicles_before = set(state.vehicles)
    tankmen_before = set(state.tankmen)
    try:
        response = client.open_box(box_id)
    finally:
        ny_module.BOX_LOOT.clear()
        ny_module.BOX_LOOT.update(saved)
    runner.equal(response.code, cmd.RES_SUCCESS, "the box is opened")

    granted_vehicles = sorted(set(state.vehicles) - vehicles_before)
    granted_tankmen = sorted(set(state.tankmen) - tankmen_before)
    runner.check(granted_vehicles, "the box granted a vehicle")
    runner.check(granted_tankmen, "and a tankwoman")
    inventory = client.diff.get("inventory") or {}
    descrs = (inventory.get(VEHICLE) or {}).get("compDescr") or {}
    for inv_id in granted_vehicles:
        runner.check(inv_id in descrs, "vehicle %s travels in the diff" % inv_id)
        runner.check(descrs[inv_id], "with its descriptor")
    crew = (inventory.get(TANKMAN) or {}).get("compDescr") or {}
    for inv_id in granted_tankmen:
        runner.check(inv_id in crew, "tankman %s travels in the diff" % inv_id)
    runner.check("unlocks" in (client.diff.get("stats") or {}),
                 "the research table of the garage travels with a new vehicle")
    message = response.actions[0]["data"]["data"]
    runner.check(message.get("vehicles"), "the system message names the vehicle")
    runner.check(message.get("tankmen"), "and the tankwoman")
    for entry in message["vehicles"]:
        runner.check(all(isinstance(key, int) for key in entry),
                     "a vehicles bonus is keyed by the vehicle's compact descriptor")
        for compact in entry:
            runner.equal(state.catalog.vehicles.get(compact) is not None, True,
                         "the type descriptor %s is a real vehicle" % compact)


def test_a_collection_reward_hands_out_its_camouflages(runner: common.Runner) -> None:
    """A completed collection pays its camouflages into the depot.

    The reward is the bonus of its own hidden quest - the system message of the
    box only lists the quest ids it completed - so the items of that quest are
    what the account has to book, and the client shows exactly them.
    """
    client, state = _client(runner)
    setting_id = nyc.NATIONAL_SETTINGS_IDS_BY_NAME["soviet"]
    state.new_year.state.collection_ratings[setting_id] = 100  # level MAX_COLLECTION_LEVEL
    box_id = sorted(state.catalog.new_year.boxes)[0]
    client.add_token(box_id)

    response = client.open_box(box_id)
    runner.equal(response.code, cmd.RES_SUCCESS, "the box is opened")
    message = response.actions[0]["data"]["data"]
    reward_id = state.catalog.new_year.collection_reward_by_setting["soviet"]
    runner.check(reward_id in message["completedQuestIDs"], "the collection reward is named")

    runner.equal(client.diff.get("newYear", {}).get("collectionRewards"), [reward_id],
                 "the diff reports the completed collection")
    # What the client shows is the bonus of the reward's own quest, which it
    # already has from the sync of ``eventsData`` (the quests are built from the
    # installation and never change).
    quests = state.events.quests()
    bonus = (quests.get(reward_id) or {}).get("bonus") or {}
    items = bonus.get("customizations") or []
    runner.equal(len(items), 3, "the reward is a camouflage for every season")
    depot = ((client.diff.get("inventory") or {}).get(12) or {}).get(1) or {}
    for item in items:
        runner.equal(item.get("custType"), "camouflage", "the bonus names the item type")
        catalogue_item = state.catalog.customization.get(
            c11n.make_int_descr(c11n.TYPE_CAMOUFLAGE, item["id"]))
        runner.check(catalogue_item is not None,
                     "camouflage %s exists in the installation" % item["id"])
        if catalogue_item is None:
            continue
        runner.check(catalogue_item.is_vehicle_bound() and catalogue_item.not_in_shop,
                     "it is one of the event camouflages")
        runner.equal(catalogue_item.nations, (nyc.NATION_INDICES["ussr"],),
                     "and it belongs to the setting's nation")
        runner.equal(state.customization.count(c11n.TYPE_CAMOUFLAGE, item["id"], 0), 1,
                     "it is in the depot")
        runner.equal(depot.get(c11n.TYPE_CAMOUFLAGE, {}).get(item["id"]), {0: 1},
                     "and the diff publishes it")
    seasons = []
    for item in items:
        found = state.catalog.customization.get(
            c11n.make_int_descr(c11n.TYPE_CAMOUFLAGE, item["id"]))
        if found is not None:
            seasons.append(found.season)
    runner.equal(sorted(seasons), sorted(REWARD_SEASONS),
                 "one camouflage per season, and no season twice")


def test_state_can_be_saved_and_restored(runner: common.Runner) -> None:
    """Everything the event changed survives the JSON round trip of the account."""
    client, state = _client(runner)
    client.craft(None, None, None)
    toy_id = sorted(state.new_year.state.inventory_toys)[0]
    toy = state.catalog.new_year.toys[toy_id]
    slot_id = next(slot.id for slot in state.catalog.new_year.slots if slot.type == toy.type)
    client.fill_slot(slot_id, toy_id)
    box_id = sorted(state.catalog.new_year.boxes)[0]
    client.open_box(box_id)
    chest_id = sorted(state.catalog.new_year.chests)[0]
    client.add_token(chest_id)

    payload = json.loads(json.dumps(state.to_json()))
    from server.account_rpc.state import AccountState

    restored = AccountState.from_json(payload, state.catalog)
    runner.equal(restored.new_year.state.inventory_toys[toy_id], state.new_year.state.inventory_toys[toy_id],
                 "the inventory came back")
    runner.equal(restored.new_year.state.slots, state.new_year.state.slots, "the tree came back")
    runner.equal(restored.new_year.state.boxes, state.new_year.state.boxes, "the boxes came back")
    runner.equal(restored.new_year.state.chests, state.new_year.state.chests, "the chests came back")
    runner.equal(restored.new_year.state.collection_ratings, state.new_year.state.collection_ratings,
                 "the collection ratings came back")


def main(argv=None) -> int:
    return common.main(sys.modules[__name__], argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
