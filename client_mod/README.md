# The client mod

The 0.9.22 client always builds its account connection itself
(`connection_mgr.ConnectionManager` → `BigWorld.connect`) and gets the whole
lobby from what the server sends it.  The mod replaces that one pipe with a
socket to the local server run by `python3 -m server`:

| file (`scripts/client/gui/mods/…`) | what it does |
| --- | --- |
| `__init__.py` | the client's mod loader, widened to accept `mod_*.py` sources next to `mod_*.pyc` |
| `mod_wotny_local.py` | the entry point the loader calls (`init`/`fini`) |
| `wotny/config.py` | host, port, account name, delays (all overridable through the environment) |
| `wotny/log.py` | `wotny.log` next to `python.log` |
| `wotny/proto.py` | framing of the socket (length prefix + `zlib` + pickle protocol 2) |
| `wotny/bridge.py` | the socket itself: a worker thread reads and writes, the game thread applies (`BigWorld.callback`) |
| `wotny/account_proxy.py` | the fake `Account.base` mailbox, plus the diffs, answers, chat actions and streams of the server |
| `wotny/fake_login.py` | the fake login server: `BigWorld.connect`, the predefined host, the client-only `Account` entity |
| `wotny/bootstrap.py` | installs everything and triggers the login |

## Installation

Copy the `res_mods` tree into the game folder:

```
client_mod/res_mods/0.9.22.0.1/scripts/…  ->  <World_of_Tanks>/res_mods/0.9.22.0.1/scripts/…
```

so that `<World_of_Tanks>/res_mods/0.9.22.0.1/scripts/client/gui/mods/mod_wotny_local.py`
exists.  Start `python3 -m server --client <World_of_Tanks>` first, then the game.
The mod logs in by itself after a few seconds and writes `wotny.log` next to
`python.log` (or to `%TEMP%\wotny.log`).

The mod ships as **sources**, because the release client only looks for
`mod_*.pyc`.  That is why `gui/mods/__init__.py` is replaced as well: it is the
stock loader with the file name check widened to `mod_*.py` *and* `mod_*.pyc`.
A user-supplied `mod_*.pyc` still wins over the same mod as source, and every
other package in `gui/mods` keeps working.  If you prefer a compiled package,
run `python3 tools/pack_wotmod.py` (needs Python 2.7 only for the compilation
step) and install the resulting `mods/0.9.22.0.1/*.wotmod`.

## Two conventions worth knowing

* **`ext` payloads are pickles.**  ``Response.ext`` of the server is already
  ``cPickle`` output (`Account.onCmdResponseExt` of the client does
  ``ext = cPickle.loads(ext)`` itself), so ``wotny/account_proxy.py`` passes the
  bytes through unchanged; only a plain object (an older server) is pickled
  there.
* **Streams are raw.**  ``Account.onStreamComplete`` hands the payload to the
  ``SyncController``, which does the ``zlib`` + ``cPickle`` itself, so the
  ``data`` of a ``stream`` frame is forwarded as it arrived.

## Settings (environment variables)

| variable | default | meaning |
| --- | --- | --- |
| `WOTNY_HOST` | `127.0.0.1` | where the local server listens |
| `WOTNY_PORT` | `28422` | its port |
| `WOTNY_ACCOUNT` | `local_account` | the name of the local account |
| `WOTNY_AUTO_CONNECT` | `1` | `0` disables the automatic login (the login screen then stays, and any login is redirected to the local server) |
| `WOTNY_CONNECT_DELAY` | `3.0` | seconds before the automatic login |
| `WOTNY_LOG` | `<game folder>/wotny.log` | log file |
| `WOTNY_NO_HANGAR` | `0` | `1` keeps the stock hangar space instead of the New Year one |
| `WOTNY_VERBOSE` | `0` | `1` also logs the account-base calls the mod ignores on purpose (chat commands, keep-alives) |

## Troubleshooting

* **Nothing happens in the game** — check `wotny.log`: `waiting for the local
  server` means the server is not running or is on another port.
* **The lobby opens without the New Year hangar** — the notification that
  switches the space travels with the *first* account sync
  (`eventsData` → `NOTIFICATIONS`); its path is `spaces/h07_newyear_2015`.  If
  the client's packages do not contain that space, edit
  `server/game/events.py` (`NEW_YEAR_HANGAR_PATH`) to the space the client ships
  (its own `gui/hangars.xml` `default_hangar` is a good hint).
* **A dialog asking for the EULA** — that is the stock client on a fresh
  profile; accept it once.
* **The mod is not loaded at all** — the game version folder must match exactly
  (`0.9.22.0.1`), and `res_mods/<version>/scripts/client/gui/mods/mod_wotny_local.py`
  has to be in place.

`docs/testing.md` is the full checklist for a session in the game: what to try
in which order, what is expected to be empty (the statistics, the battles), and
which five things make a failure report actionable.
