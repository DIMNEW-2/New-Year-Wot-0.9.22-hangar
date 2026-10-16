# Testing in the real client

Everything in `tests/` runs without the game, and `python3 -m server verify`
runs a whole session over the wire against the server's own client stub.  What
neither of them can prove is how the **game** reacts, because a World of Tanks
installation only runs on Windows with the retail client.  This page is the
checklist for the one test that does: starting the game against the local
server, feature by feature, and reporting what happened.

## 1. Before the game

```bash
python3 -m server verify --client "C:/Games/World_of_Tanks"
```

It reads the installation, starts the server on a free port, runs a session
(handshake, account diff, streamed price cache, a loot box, a garage command,
a refusal) and prints what it found.  Two warnings are expected and harmless:

* `the New Year hangar space (spaces/h07_newyear_2015) was not found` - the
  space is inside the client's packages, not in the loose scripts; the client
  itself will tell you if it is missing (see *The lobby opens with the stock
  hangar* below),
* `the client mod is not installed yet` - before step 2.

## 2. Install the mod

Copy the `res_mods` tree into the game folder so that

```
<World_of_Tanks>/res_mods/0.9.22.0.1/scripts/client/gui/mods/mod_wotny_local.py
```

exists (see `client_mod/README.md` for the details and for what the mod
replaces).

## 3. Start the server, then the game

```bash
python3 -m server serve --client "C:/Games/World_of_Tanks" -v
```

`-v` prints every command the client sends and every answer it gets, which is
what makes a report readable.  Leave it running, start the game and wait a few
seconds: the mod logs in by itself.

Files worth keeping from a run:

| file | what it is |
| --- | --- |
| `<game folder>/wotny.log` | the mod's own log (login, every request, every diff, every error) |
| `<game folder>/python.log` | the client's log; **its tracebacks are the important part** - the mod cannot see everything the client code does with the data it gets |
| the server's console | what the server answered (with `-v`) |

## 4. The checklist

Go through this in one session and note for every line: **works** / **fails**
(with what the client showed) / **not reached**.

**Login and lobby**

1. The game reaches the lobby without a "невозможно войти" and without a
   black screen.
2. The hangar is the **New Year** one (the event's own space, snow and
   decorations), not the stock hangar.
3. The hangar shows the New Year **notification** (the event window's entry
   point in the bottom right).
4. The credits, gold and free experience in the header are the account's
   (10 000 000 credits / 25 000 gold / 50 000 free XP on a fresh account).
5. The garage holds the vehicles the server sent (386 by default) with their
   crew, and the vehicle carousel scrolls through them.

**The New Year event (the event window)**

6. The event window opens; the tree/collection shows the decorations of the
   account's starting state and the atmosphere level.
7. **Toys**: dragging a toy into a slot is accepted and survives closing and
   reopening the window.
8. **Crafting**: crafting a toy works and pays what the window showed.
9. **Boxes**: opening a **loot box** plays the animation, grants what the
   window reports (credits, gold, decorations, premium days, a tank or a
   tankwoman) and the box disappears from the account.
10. **Chests**: opening a chest of a level the account has works and asks for
    the decoration the window offers.
11. **Discounts**: a variadic discount can be picked and shows up in the shop.
    The offer of a level is a percent off one tank of that level (5 % on level
    2 up to 50 % on level 10), the shop dialogue shows that very price, and
    buying the tank pays it.  Every atmosphere level the tree reaches also
    writes a line into the **service channel** (the message list in the
    bottom left); `--no-announce` silences the whole service channel.
12. **Tankwomen**: a box that grants a tankwoman asks for the nation/vehicle
    choice and the crew member ends up in the barracks.

**Shop, garage and crew**

13. The shop prices vehicles, modules, shells, consumables and optional
    devices; buying and selling works and the wallet follows.  The wallet
    tooltip in the header shows the gold/credits breakdown the server reports
    for the balance request (gold and credits, the earned half of them).
14. Buying a garage slot and barracks berths works.
15. Buying premium account for gold works (the header shows the premium).
16. Exchanging gold for credits and converting vehicle experience into free
    experience (including the free trade-in discount) works.
    **Trade-in**: the dialog trades an owned premium tank for another premium
    one - the new tank is charged minus half of the gold price of the old one,
    the crew of the old one moves into the barracks (one free berth per crew
    member is needed) and everything mounted on it returns to the depot.
17. Mounting and dismounting modules, optional devices (including the **paid**
    removal of a non-removable one), ammunition and consumables works, and
    what is taken off comes back to the depot.
18. The equipment/ammunition **layouts** save and buy what they are short of.
19. Repairing a damaged vehicle works.
20. The vehicle's own settings (the ones the client allows) work.
21. **Crew**: moving a crew member between seats and vehicles works; the
    personal file opens.
22. The personal file's operations: learning a skill, forgetting skills (the
    free and the paid ones), retraining to another vehicle or role, changing
    the passport, buying crew experience for gold.
23. Dismissing a crew member puts it into the recycle bin (**barracks** view),
    restoring it works, and "return the crew" empties a vehicle.

**Appearance window (customization)**

24. Paints, camouflages, decals, modifications and styles are listed with
    prices; buying one puts it into the depot.
25. Mounting an item on a vehicle, removing it and applying a style works; the
    vehicle looks different in the hangar.

**Profile**

26. The profile window opens and its **statistics** are the empty ones (see
    *What is expected to be empty* below), not an error dialog.
27. The **awards** tab lists the achievements and medals of the client; a
    medal's tooltip shows the crystals (bonds) it pays per tank tier.
28. The badges page opens, shows the badges of the installation (a local
    account is awarded every badge the installation describes, so the page has
    something to show) and keeps the ones that are picked.
29. Closing and reopening any of these screens does not break the hangar.

**Persistence**

30. Close the game, stop the server (Ctrl+C), start it again and log in: the
    garage, the crew, the wallet, the event state and the boxes are the same as
    before.

## 5. What is expected to be empty

These are not bugs, they are what the server is:

* **Statistics stay at zero.**  The dossiers are empty because there are no
  battles: no battles, no wins, no damage, no achievements and no medals.  The
  screens themselves work.  Badges are the one exception and deliberately so:
  they come from the installation's own `badges.xml` and are awarded as earned,
  so the badges page has data to show.
* **Battles do not start.**  There is no battle server, so "В бой!" reports a
  server error instead of queueing.  Nothing in the New Year event needs a
  battle.
* **The crystal (bond) rewards of the medals are the schedule of the build.**
  The awards window shows what `server/game/achievements_reward.py` publishes:
  the medal-bond table of 9.20.1 times one crystal, per tank tier, keyed by the
  installation's own record ids.  That is the *published* schedule, not an
  account's earnings - the local server awards no bonds in a battle, so the
  counter itself stays at zero.
* **Missions, promotions, friends, clans, platoons** - not implemented, and
  there is no live mission/promotion feed to show: the notification mechanism
  itself works (the event announces its start and every atmosphere level in the
  service channel), but the missions and promotions of a live server are
  decided by that server's own event data, which is not part of the client.

## 6. Common things that look like bugs

* **A dialog asking for the EULA or the "не отключать" warning** - the stock
  client on a fresh profile; accept it once.
* **The lobby opens with the stock hangar** - the notification that switches
  the space travels with the first account sync and names
  `spaces/h07_newyear_2015` (`server/game/events.py::NEW_YEAR_HANGAR_PATH`).
  If the installation ships another space name, the client's packages say
  which one - it is also in the client's own `gui/hangars.xml`
  (`default_hangar`).
* **"waiting for the local server" in `wotny.log`** - the server is not running,
  or it is on another port than the mod's (`WOTNY_PORT`, default 28422).
* **The mod is not loaded at all** - the version folder must match exactly
  (`0.9.22.0.1`) and the stock `gui/mods/__init__.py` may have to be replaced
  with the one from `client_mod/` (it is the only file the mod needs from the
  stock scripts).
* **Something is wrong with the account and a reset is fine** - stop the server,
  delete `server/data/account.json` (or pass `--save` to keep several) and start
  it again: the next run builds a fresh account.

## 7. Reporting a failure

For every failing line of the checklist, the useful report is:

1. what was done (the client's window and the button, in order),
2. what the client showed (a screenshot is the shortest way to say it),
3. the tail of `wotny.log` around that moment,
4. the tail of `python.log` - especially a traceback (`File "scripts/…"`),
5. the server's console output for the same moment (with `-v`).

With those five things a wrong diff, a wrong price or a missing command is
usually a one-line fix; without `python.log` the client-side half of the failure
is invisible to the server.
