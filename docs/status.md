# Status

What works today, and what is still missing or unverified.

## Working (covered by `tests/`)

* **Game data** — the catalogue is read from the player's own `item_defs`
  (vehicles, modules with their prices, the New Year definitions) and validated
  (`python3 -m server info`).
* **Account** — a local account with a garage (386 researchable vehicles by
  default, `--garage premium|all`), crew (real names and icons from the client's
  own tankmen configs), credits/gold/free XP, slots and berths.
* **Account protocol** — `CMD_SYNC_DATA` (full and incremental diffs, `rev`/
  `prevRev`), `CMD_SYNC_SHOP` (streamed price cache), tunnelled
  `CMD_ADD_INT_USER_SETTINGS`, `CMD_REQ_SERVER_STATS`, persistence of the
  account between runs.
* **Diffs the client reads** — `inventory` (vehicles with their descriptors,
  crew, equipment, shells, settings, locks; tankmen), `stats`, `account`,
  `economics`, `tokens`, `newYear`, `('goodies','_r')`,
  `('serverSettings','_r')`, `('eventsData','_r')`.
* **New Year event** — toys (fill/break/see/craft), slots, levels, the
  collection and its levels, boxes, chests, variadic discounts and tankwomen,
  all with the client's exact answer codes and payloads (checked against
  `ClientNewYear`, `_NewYearBoxStorage`, `_NewYearChestStorage`).
* **Quests, actions and notifications** — `eventsData` carries the event
  quests (including one hidden quest per chest, which is where a chest's loot
  travels), the event action, the in-game event state and the notifications.
  The notification `cmd_change_hangar` switches the client's hangar space to
  `spaces/h07_newyear_2015` (`gui/ClientHangarSpace.py`).
* **Loot reports** — opening a box or a chest pushes a `tokenQuests` system
  message (chat action 21) with the rewards, exactly what
  `_NewYearBoxStorage.__handleSysMsg` expects.
* **Wire protocol** — length-prefixed, zlib-compressed pickles that Python 2.7
  reads; framed tests of a whole session (hello → welcome → commands → diffs →
  system messages → stream) run against a real socket.
* **Client mod** — fake login, account bridge, diffs, answers, chat actions and
  streams applied to the client's own `Account` (see `client_mod/`).

## Known gaps

* **The mod can only be verified inside the client.** The bridge, the fake login
  and the account proxy follow the decompiled client's own code paths, but this
  repository has no Windows client to run them against.  Things to watch on the
  first run (`wotny.log`): `BigWorld.createEntity('Account', …)` returning an
  entity, `onBecomePlayer` reaching `showGUI`, and the lobby's own sync chain
  (`ItemsCache`, `EventsCache`, `SettingsCache`, `AccountValidator`).
* **The New Year space itself** (`spaces/h07_newyear_2015`) has to exist in the
  client's packages.  If a build ships another space name, change
  `server/game/events.py::NEW_YEAR_HANGAR_PATH` (the client's own
  `gui/hangars.xml` `default_hangar` is a good hint).
* **Battles** are out of scope: no `Avatar`, no arena, no battle results.
* **Shop prices** cover vehicles and their modules; shells, consumables,
  equipment and optional devices are not priced yet (the client shows 0 for
  them), and there are no personal discounts/boosters beyond the New Year
  goodies.
* **Goodies definitions** (`scripts/server_xml/goodies.xml`) are server-side
  data of the live game and are not in the client: the event's own variadic
  discounts work, the shop's personal discount list is empty.
* **Notifications** other than the hangar ones (missions, promotions) are not
  implemented.
* **`--garage all`** (540 vehicles) is available but the default stays
  `researchable` (386), which matches what the lobby can display without
  premium account data.
