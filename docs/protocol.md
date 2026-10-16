# The bridge protocol (client mod ⇄ local server)

The client mod opens one TCP connection to the local server (`127.0.0.1:28422`
by default).  Every message is

```
struct('!I', len(payload)) || payload
payload = zlib.compress(pickle(obj, protocol=2))
```

The pickle is written by Python 3 (`server/proto/py2pickle.py`) in a way Python
2.7 loads back unchanged: protocol 2, `str` as `BINUNICODE`, `bytes` through
`_codecs.encode(data, 'latin1')` so the client sees `str` where it expects
bytes (vehicle descriptors, `toyCollection`, stream payloads).  The mod writes
plain Python 2 protocol-2 pickles.

## Client → server

| message | fields | meaning |
| --- | --- | --- |
| `hello` | `protocol`, `client` | first message after connecting; the server answers with `welcome` |
| `cmd` | `requestID`, `code`, `args` | one `Account._doCmd*` call, `args` exactly as the client passed them |
| `ping` | — | keep-alive (the server answers `pong`) |
| `bye` | — | the client is going away |

`code` is the client's own `AccountCommands` value (`100` sync, `151`–`158` and
`306`/`10015` the barracks, `116`–`119` the appearance window, `503`–`508` and
`600` the profile, `800` the badges, `10016` trade-in, `10017` the WGMBalance
rows, `20161`–`20169` the New Year commands, `300` shop sync, …); `args` is a
tuple because the mod sends `len(args)` values of the matching `doCmd*` helper.

The shop and wallet helpers of `client/account_helpers`:

| code | `args` | answer |
| --- | --- | --- |
| `CMD_VEHICLE_TRADE_IN` (10016) | `IntArr [rev, vehInvID, vehCD, flags, tmanCost]` | the traded-off tank is the *old* one and `vehCD` the new one (`Shop.tradeInVehicle`); the answer charges the new price minus `ceil(0.5 * <gold price of the old one>)` and carries `ext['vehInvID']` of the new tank.  Refusals: `vehicle_not_found_in_inventory`, `invalid_vehicle`, `vehicle_cannot_trade_in`, `vehicle_cannot_trade_off`, `not_enough_space`; fewer than three arguments are `RES_WRONG_ARGS` |
| `CMD_QUERY_BALANCE_INFO` (10017) | `Str ''` (`doCmdStr('')`) | `ext` *is* the row list `WGMBalanceInfoRequester` walks: four rows of `{'currency_code': 'gold'|'credits', 'class': {'is_paid': bool}, 'amount': int}`, the earned pair being the wallet and the purchased pair zero |

The barracks commands come from `client/account_helpers/Inventory.py`
(`dismissTankman`, `addTankmanSkill`, …) and `client_recycle_bin.py`
(`restoreTankman`); the arguments are theirs and the answer carries the rewritten
`inventory[8]['compDescr'][invID]` in its diff:

| code | `args` | answer |
| --- | --- | --- |
| `CMD_DISMISS_TMAN` (306) | `Int3 (tmanInvID, 0, 0)` | the crew member leaves `inventory[8]`, and `recycleBin.tankmen.buffer[invID]` carries `(compDescr, dismissedAt)` |
| `CMD_TMAN_RESTORE` (10015) | `Int3 (tmanInvID, 0, 0)` | the reverse; the bin entry becomes `None` |
| `CMD_DISMISS_TMAN`/`CMD_RETURN_CREW` (157) | `Int3 (vehInvID, 0, 0)` for the crew | the seats travel as `inventory[1]['crew']` |
| `CMD_TMAN_ADD_SKILL` (151) | `Int3 (tmanInvID, skillIdx, 0)` | |
| `CMD_TMAN_DROP_SKILLS` (152) | `Int3 (shopRev, tmanInvID, costIdx)` | |
| `CMD_TMAN_RESPEC` (153) | `Int4 (shopRev, tmanInvID, costIdx, vehTypeCompDescr)` | |
| `CMD_TMAN_PASSPORT` (154) | `IntArr [shopRev, tmanInvID, isPremium, isFemale, fnGroup, first, lnGroup, last, iGroup, icon]` | |
| `CMD_TRAINING_TMAN` (155) | `Int3 (shopRev, tmanInvID, gold)` | the gold the convert window computed |
| `CMD_TMAN_MULTI_RESPEC` (156) | `IntArr [shopRev, vehTypeCompDescr, invID, costIdx, …]` | |
| `CMD_TMAN_CHANGE_ROLE` (158) | `Int4 (shopRev, tmanInvID, roleIdx, vehTypeCompDescr)` | `ext` is an `EQUIP_TMAN_CODE` |
| `CMD_ADD_TMAN_XP` (10003) | `Int3 (tmanInvID, xp, 0)` | |

The profile requests come from `client/Account.py` (`requestPlayerInfo`,
`requestAccountDossier`, …) and `account_helpers/DossierCache.py`
(`CMD_SYNC_DOSSIERS`); the dossiers they answer with are compact descriptors the
client parses with its own `dossiers2`, and an *empty* descriptor is a fresh
dossier of the current version, so the local server never mirrors the client's
block layout:

| code | `args` | answer |
| --- | --- | --- |
| `CMD_REQ_PLAYER_INFO` (503) | `Int3 (databaseID, 0, 0)` | `ext.databaseID`/`dossier`/`clanDBID`/`clanInfo`/`globalRating`/`eSportSeasons`/`ranked` |
| `CMD_REQ_ACCOUNT_DOSSIER` (504) | `Int3 (accountID, 0, 0)` | `ext['dossier']` (empty: the client builds a new dossier) |
| `CMD_REQ_VEHICLE_DOSSIER` (505) | `Int3 (databaseID, vehCD, 0)` | the same for one vehicle |
| `CMD_REQ_PLAYER_CLAN_INFO` (506) | `Int3 (databaseID, 0, 0)` | `ext.clanDBID` 0, `clanInfo` `None` |
| `CMD_REQ_PLAYER_GLOBAL_RATING` (507) | `Int3 (accountID, 0, 0)` | `ext['globalRating']` |
| `CMD_REQ_PLAYERS_GLOBAL_RATING` (508) | `IntArrStrArr (ids, [])` | `ext` *is* `{databaseID: rating}` |
| `CMD_SYNC_DOSSIERS` (600) | `Int3 (version, maxChangeTime, 0)` | a `RES_STREAM` response whose stream is `(version, [(ownerID, changeTime, dossier)])` |
| `CMD_SELECT_BADGES` (800) | `IntArr (badgeIDs)` | `diff['badges']`, validated against `item_defs/badges.xml` |
| `CMD_SET_DOSSIER_FIELD` (2000) | `IntStr (value, path)` | `diff['cache']['SPA'][path]` |

## Server → client

| message | fields | client side |
| --- | --- | --- |
| `welcome` | `protocol`, `account` (`name`, `databaseID`), `diff` | the full account snapshot; the mod keeps it for `showGUI` and lets the client's own `CMD_SYNC_DATA` do the sync |
| `diff` | `diff` | `Account._update(True, diff)` — a full sync when `prevRev` is missing |
| `sysMessage` | `action` | `Account.onChatAction(action)`: a chat action (21 = `sysMessage`, 22 = `personalSysMessage`), e.g. the `tokenQuests` (29) message that carries a loot box reward |
| `response` | `requestID`, `code`, `error`, `ext` | `Account.onCmdResponseExt(requestID, code, error, pickle(ext))` when `ext` is set, else `Account.onCmdResponse(…)`; `code` is `RES_SUCCESS` (0), `RES_STREAM` (1), `RES_FAILURE` (-1), … |
| `stream` | `requestID`, `desc`, `data` | `Account.onStreamComplete(requestID, desc, data)`; `desc` is `(isCorrupted, origPacketLen, packetLen, origCrc32, crc32)` and `data` is `zlib(pickle(obj))`.  Sent *after* the `RES_STREAM` response, because the client subscribes to the id when it sees the code |
| `pong` | `time` | answer to `ping` |

Order matters within one answered request: the diffs and chat actions first,
then the response (the client's callback reads the freshly synced caches), then
the stream, if any.

## Sync contract

* `CMD_SYNC_DATA (100)` is sent by `AccountSyncData` with `(revision, crc, 0)`.
  A diff without `prevRev` is a full sync (`isFullSync = True`, the client
  clears its caches first); an incremental diff must echo the revision the
  client sent in `prevRev`, otherwise it resynchronizes.
* Sections keyed by a tuple `(name, '_r')` replace the client's cache
  (`__synchronizeCacheDict`), which is how `eventsData`, `goodies` and
  `serverSettings` travel; every other key must be a dictionary of dictionaries.
* `eventsData` values are `zlib(pickle(…))` blobs keyed by
  `EVENT_CLIENT_DATA` (`QUEST` 3, `ACTION` 1, `INGAME_EVENTS` 7,
  `NOTIFICATIONS` 9, …).  The notification list is only sent with a full sync:
  the client reports every entry it receives as freshly added, and a repeated
  `cmd_change_hangar` reloads the hangar space.  A notification *without* a
  `cmd_`/`promo_` prefix becomes a message of the service channel
  (`SystemMessagesInterface` → `ActionNotificationFormatter`, which reads
  `message['type']` as its icon and wants `message['state']`), so the event
  announces itself and every atmosphere level that way - the level-up rides in
  the plain `NOTIFICATIONS` key of that very diff (the client diffs the list it
  knows against the one it receives), a `promo_` notification is never sent
  because the client would open a browser window for it.
* `goodies` travels twice: the *definitions* sit in the shop cache
  (`goodies`/`prices`/`notInShop` and again inside `defaults`) and the offers an
  account *holds* in `('goodies', '_r')` as
  `{'goodies': {goodieID: (state, finishTime, count)}}`, which is the section
  `ClientGoodies` replaces its whole cache with.
* New Year tokens are `(expiry, count)` pairs; the client moves the event ones
  into its own box/chest/discount caches and strips them from the token diff.
* `cache` carries the flags the helpers keep there: `mayConsumeWalletResources`
  (`Stats.synchronize`; a missing one leaves a gold purchase refused as
  `WALLET_NOT_AVAILABLE`), `vehsLock` (`Inventory.synchronize` — the lock of
  every locked vehicle, keyed by inventory id) and `SPA` (the dossier paths
  written by `CMD_SET_DOSSIER_FIELD`).
* The shop cache travels as a `RES_STREAM` response and a `stream` frame of its
  own (`CMD_SYNC_SHOP`): `items` (every price plus the tables a requester
  indexes), `goodies` (the personal offers of the event: definitions, their
  prices and the not-in-shop list), `achievementsReward` (the crystal reward of
  every medal, keyed by the record ids of the client's own
  `common/dossiers2/custom/records.py`) and `defaults`, which repeats the tables
  a dialog falls back to (`DefaultShopRequester` reads `defaults['items']`).
* A dossier answer is a compact descriptor built by
  `server/game/dossier.py`: the header, then one size per block in layout order
  and the block payloads.  `CMD_SYNC_DOSSIERS` (600) streams them (a `stream`
  frame *after* its `RES_STREAM` response), every other request puts the
  descriptor in `ext`; `CMD_SELECT_BADGES` (800) answers only `(resultID,
  errorCode)` and publishes the selection in `diff['badges']`.
