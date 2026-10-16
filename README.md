# Local server for World of Tanks 0.9.22 — New Year hangar, loot boxes, New Year offensive

Client version: `World_of_Tanks_0.09.22.00.01_CH_1513_HD` (Chinese HD, 0.9.22.0.1 #1513).

The project serves the **account protocol of that client** from a local Python 3
process, so the game runs offline with a working lobby:

* a **New Year hangar** — the event space is switched on by the server's own
  `cmd_change_hangar` notification (`spaces/h07_newyear_2015`),
* **loot boxes and chests** — opening them hands out the official rewards and
  reports them through the client's `tokenQuests` system message,
* the **New Year offensive** — decorations (toys), slots, crafting, the
  collection levels, boxes, chests, variadic discounts and tankwomen, driven by
  the event's own quests and actions in `eventsData`,
* a normal garage (vehicles with their modules, crew, equipment, credits/gold)
  so the lobby and the carousel work as usual.

Game data is **never** bundled: the server reads the player's own installation
(`res/scripts/item_defs`, or the same tree inside `res/scripts.pkg` when the
build ships packed scripts), so it always matches the client exactly.

If the server cannot find the game data, ask it what it sees:

```bash
python3 -m server inspect --client /path/to/World_of_Tanks
```

it prints the layout it found (or every place it looked) instead of a traceback.

## Requirements

* the game itself (`World_of_Tanks.exe`, `res/scripts/item_defs`),
* Python 3.8+ for the server (no third-party packages),
* Windows for the game; the server runs anywhere.

## Quick start

0. Ask the server to check the installation first — one command, a readable
   report (game data, New Year definitions, crew lists, the New Year hangar
   space in the client's packages, the mod, and a whole session over the real
   wire protocol):

   ```bash
   python3 -m server verify --client /path/to/World_of_Tanks
   ```

   Everything the local server depends on shows up as `[ ok ]`; the two things
   that come from the game itself (its packages and the mod) are warnings with
   the exact command or path to fix them.

1. Start the server (it listens on `127.0.0.1:28422` by default):

   ```bash
   python3 -m server --client /path/to/World_of_Tanks -v
   ```

   The first start reads the whole `item_defs` tree and builds the account
   (`server/data/account.json`); later starts reuse the saved account.

2. Install the client mod — copy the `res_mods` tree of `client_mod/` into the
   game folder (see [client_mod/README.md](client_mod/README.md)):

   ```
   client_mod/res_mods/0.9.22.0.1/  ->  <World_of_Tanks>/res_mods/0.9.22.0.1/
   ```

3. Start the game. The mod logs in to the local server by itself, the lobby
   opens with the New Year hangar, and boxes, chests and the event screens work
   in the client's own UI.

## How it works

```
  World of Tanks 0.9.22 (Python 2.7)            Python 3
 ┌──────────────────────────────────┐        ┌───────────────────────────────┐
 │ client mod  gui/mods/wotny       │        │ server/account_rpc/server.py  │
 │  • fake login (BigWorld.connect) │  TCP   │  • sessions, diffs, pushes    │
 │  • account bridge (base mailbox) │◄──────►│  • CMD_SYNC_DATA, …           │
 │  • applies diffs to Account      │  28422 │ server/game/new_year.py       │
 └──────────────────────────────────┘        │  • toys, slots, boxes, chests │
                                             │ server/game/events.py         │
                                             │  • quests, actions, hangar    │
                                             └───────────────────────────────┘
```

* The account protocol is the client's own: commands (`AccountCommands`),
  answers (`onCmdResponse`/`onCmdResponseExt`/`onStreamComplete`), account diffs
  (`Account._update`), chat actions (`Account.onChatAction`) and the events data
  (`eventsData` blobs of `zlib(pickle)`).
* Messages between the mod and the server are length-prefixed, zlib-compressed
  Python-2-readable pickles (`server/proto/py2pickle.py` and the mod's own copy
  of the framing).

## Repository layout

| path | what it is |
| --- | --- |
| `server/game/` | game data (`item_defs` catalog), New Year event logic, quests/actions/notifications, reward payouts |
| `server/account_rpc/` | account state, command dispatch, protocol constants, socket server, persistence |
| `server/proto/` | Python-2-compatible pickles and framing |
| `server/loader.py`, `server/__main__.py` | finding the installation (unpacked or packed, nested folders), CLI (`serve`, `info`, `extract`, `inspect`) |
| `client_mod/` | the in-client mod (bridge + fake login + account proxy) |
| `tests/` | plain-Python test suite (`python3 tests/run_all.py --client …`) |
| `docs/` | protocol notes and the event reference |

## Tests

```bash
python3 tests/run_all.py --client /path/to/World_of_Tanks
```

The suite covers the catalogue, the New Year state machine, the account diffs
their client-side readers expect, the wire protocol and the socket server (the
same bridge the mod uses).

## Status

Implemented: catalogue and garage, account sync, the appearance window (the
customization depot, the outfits and the four commands 116–119), the barracks
(skills, retraining, roles, passports, crew experience, dismiss/restore through
the recycle bin), New Year event (toys, slots, crafting, boxes, chests,
discounts, tankwomen), event quests/actions, the New Year hangar notification,
the socket bridge and the client mod.

Known gaps are tracked in `docs/status.md` — most notably the shop stream
(prices), battles (out of scope) and the parts of the mod that can only be
verified inside a real client.
