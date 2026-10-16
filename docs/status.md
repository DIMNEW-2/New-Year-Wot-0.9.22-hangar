# Status

What works today, and what is still missing or unverified.

## Working (covered by `tests/`)

* **Self check** — `python3 -m server verify --client <game folder>` reads the
  installation (game data, New Year definitions, crew name lists), looks for the
  New Year hangar space inside the client's packages and for the installed mod,
  starts the server on a free port and then runs a whole session over the wire
  (handshake, account diff, streamed price cache, opening a loot box, a garage
  command, and an unsupported command that has to be refused).  It is the one
  command a user runs before starting the game, and `tests/test_verify.py` keeps
  it honest.
* **Mirrored New Year tables** — the rules the client renders the event with
  (craft prices, atmosphere levels, collection bonuses) are mirrored in
  `server/game/ny_constants.py`; `tools/check_client_assets.py` evaluates the
  client's own `common/items/new_year_types.py` (Python 2 literals, read with
  `ast`) and compares every table, so a broken mirror is a test failure instead
  of a number the player sees wrong.
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
* **The shop** — the price cache the client synchronizes (vehicles, modules,
  shells, equipment, optional devices, all from the installation's own
  `item_defs`), buying and selling vehicles and items, garage slots and barracks
  berths.  Prices, refunds and refusals match what the client's own dialogs
  show (`Shop.getSellPrice`, `ShopCommonStats`).
* **The garage** — mounting modules (`CMD_EQUIP`, a turret carries its gun),
  optional devices (`CMD_EQUIP_OPTDEV`, including the paid removal of a
  `<removable>false</removable>` device and the `incompatibleTags` filters),
  ammunition (`CMD_EQUIP_SHELLS`, `CMD_SET_AND_FILL_LAYOUTS`, which buys what a
  layout is short of and answers with what was bought), consumables
  (`CMD_EQUIP_EQS`, `CMD_SET_AND_FILL_LAYOUTS`), crew (`CMD_EQUIP_TMAN`,
  `CMD_BUY_AND_EQUIP_TMAN`), repair (`CMD_REPAIR`), the per-vehicle settings
  (`CMD_VEH_SETTINGS`) and buying-and-mounting in one step
  (`CMD_BUY_AND_EQUIP_ITEM`).  Everything the client mounts leaves the depot and
  everything it takes off goes back into it, so the counts its panels price
  against are the counts the server charges for.
* **The wallet** — gold for credits (`CMD_EXCHANGE`, at the shop's
  `exchangeRate`), premium account packets (`CMD_PREMIUM`, at `premiumCost`,
  publishing `account.attrs`/`premiumExpiryTime`), vehicle experience into free
  experience (`CMD_FREE_XP_CONV`, at `freeXPConversion`, including the free
  conversion limit) and the research commands (`CMD_UNLOCK`, `CMD_UNLOCK_ALL`).
  `stats.vehTypeXP` is a real, saved table.
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
* **Depot diffs** — the `inventory` sections of shells, equipment, optional
  devices and modules only carry what changed plus an explicit `0` for what left
  the depot (`InventoryRequester` merges key by key, so a missing key would keep
  a stale count in the client).
* **Loot reports** — opening a box or a chest pushes a `tokenQuests` system
  message (chat action 21) with the rewards, exactly what
  `_NewYearBoxStorage.__handleSysMsg` expects.  What the reward grants the
  account (a premium with its crew, a recruited tankwoman, the camouflages of a
  completed collection) is booked and published in the diff of the same answer,
  because the client only learns about it from a diff.
* **The appearance window** — the depot (`inventory['customization']['items']`,
  keyed by vehicle type for the `vehicleBound` items), the mounted outfits
  (`['outfits']`, the `SeasonType.ALL` entry being the style) and the four
  commands: buy (118, unbound or bound to the named vehicle, `notInShop`
  refused), sell (117, refunded at `ceil(0.5 * (credits + gold * 400))`), mount
  an outfit (119, the missing items bought, the replaced ones returned to the
  depot) and apply/rent/remove a style (116, `rentCount` battles as the bound
  count, the style published as the `ALL` season).
* **Research is inert.**  The local garage grants every researchable vehicle
  (`researchable`), so the client's own `UnlockItemValidator` refuses to send
  `CMD_UNLOCK` (`already_unlocked`); the handler answers the requests that still
  arrive (an item that is already unlocked) and logs the rest.  `CMD_UNLOCK_ALL`
  is a real command: it grants the researchable garage to an account that was
  started with premium tanks only.  Battles are out of scope, so vehicle
  experience only comes from the New Year bonuses.
* **Garage validation** is the client's own: the local server trusts the module
  and device ids the client sends, checks that they belong to the vehicle and
  that they are in the depot, but it does not re-derive research trees, crew
  role requirements of a module or gun/turret compatibility beyond
  `mayInstallComponent`.  Fitting commands never charge for the *mount* itself -
  only a purchase or a paid removal costs money.
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
* **Battles** are out of scope: no `Avatar`, no arena, no battle results.  The
  client's battle commands (`CMD_ENQUEUE_*`, the platoon/`CMD_PRB_*` family, the
  queue info requests) are answered with ``unknown_command``, so the buttons
  that lead into a battle report a server error instead of doing nothing.
* **The appearance UI (customization) is not verified in the client.**  Both
  halves are implemented: the catalogue (`server/game/customization.py` - every
  paint, camouflage, decal, modification and style with its price group, season
  mask, `notInShop` flag and nation/level/tag filter, checked against the
  client's own numbering by `tools/check_client_assets.py`) and the account side
  (`server/account_rpc/customization.py` - the `ITEMS`/`OUTFITS`/`UNLOCKS`
  sections of `inventory['customization']`, the depot, the mounted outfits and
  the four commands 116-119, driven by `tests/test_customization.py` through the
  same dispatcher the socket uses).  What is *not* proven is how the client's own
  window renders it: the outfit descriptor codec
  (`server/game/c11n_outfit.py`) is a re-implementation of
  `common/items/customizations.py`, and the one thing that cannot be derived from
  the client is which items the *live* server hands out (the event camouflages of
  the New Year rewards are taken from the installation's own
  `item_defs/customization` instead - see below).
* **The New Year reward tables are the installation's own.**  The live game
  decides what a box, a chest or a completed collection hands out in server-side
  data that is not part of the client.  Where the client does not care about the
  exact item (money, decorations, premium days) the local server rolls its own
  table (`server/game/new_year.py::BOX_LOOT`/`CHEST_LOOT`); a collection reward
  names *real* camouflage ids, so it takes the event camouflages of the
  installation (`server/game/events.py::collection_reward_customizations`, the
  `vehicleBound` and `notInShop` ones of the setting's nation) - an id the
  client's items cache does not know would break its awards window.
* **The barracks commands are not implemented yet** (`CMD_DISMISS_TMAN`,
  `CMD_TMAN_ADD_SKILL`, `CMD_TMAN_CHANGE_ROLE`, `CMD_TMAN_RESPEC`,
  `CMD_TMAN_DROP_SKILLS`, `CMD_TMAN_MULTI_RESPEC`, `CMD_TMAN_RESTORE`,
  `CMD_TMAN_PASSPORT`, `CMD_TRAINING_TMAN`, `CMD_RETURN_CREW`).  Crew can be
  moved between vehicles and seats (`CMD_EQUIP_TMAN`, `CMD_BUY_AND_EQUIP_TMAN`)
  and dismissed through the client only.
* **Dossiers and profile** (`CMD_REQ_ACCOUNT_DOSSIER`, `CMD_REQ_VEHICLE_DOSSIER`,
  `CMD_SET_DOSSIER_FIELD`, `CMD_SELECT_BADGES`, the global rating requests) are
  not answered: the statistics and profile screens stay empty.  Also
  unimplemented: `CMD_VEHICLE_TRADE_IN` and `CMD_QUERY_BALANCE_INFO`.
* **The shop** prices what `item_defs` prices; personal discounts and boosters
  (`goodies`) beyond the New Year ones are not implemented.
* **Goodies definitions** (`scripts/server_xml/goodies.xml`) are server-side
  data of the live game and are not in the client: the event's own variadic
  discounts work, the shop's personal discount list is empty.
* **Notifications** other than the hangar ones (missions, promotions) are not
  implemented.
* **`--garage all`** (540 vehicles) is available but the default stays
  `researchable` (386), which matches what the lobby can display without
  premium account data.
