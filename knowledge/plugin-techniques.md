# Plugin techniques: belt refill, buff HUD, weapon swap, waypoint UI, many clients, Wine, sprite-card facing

How to build common quality-of-life plugin features on D2R 3.3 build 93847 with D2RLoader 1.3.1 (PluginSDK 0.3.0)
without fighting the engine. Techniques only; the hooks and offsets they use are in
[d2r-93847-addresses.md](d2r-93847-addresses.md), threads and SDK services in [d2rloader.md](d2rloader.md). Rule of
thumb for all of them: ask the game's own routine for the answer, and move items through the game's own requests.

## 1. Belt refill

| What | Value (build 93847) | Confidence |
|-|-|-|
| Belt slot index | `row * 4 + col`, 4 columns; row 0 is the bottom row, the one the hotkeys 1-4 use | high |
| Rows | 1-4 by the worn belt's `belts.txt` row (no belt = one row) | high |
| Column rule | a column takes only the consumable kind of its bottom slot (healing, mana, rejuvenation, scroll ...); an empty column takes any beltable item | high |
| Shift-click to belt | asynchronous: the server moves the item and the client copy leaves the inventory a few frames later | observed |

How to refill:

1. Find the candidates in the inventory (beltable item types only).
2. For each candidate, ask the game's own free-belt-slot routine where that item would go (automatic pick-up
   follows the same rule, see [engine-server.md](engine-server.md)). Do not compute the slot yourself: the column rule,
   the belt's row count and partly filled columns are already in it, and a slot you computed that the server
   disagrees with is refused silently ([network-messages.md](network-messages.md)).
3. Send **one** move (the same request a shift-click sends), then wait until the item has left the inventory and sits
   in the belt (poll each frame, give up after about a second).
4. Only then ask for the next slot. Two moves in flight both get the same free slot; the second is refused or lands
   in the inventory again.

Skip the refill while an item is on the cursor, a trade or a vendor panel is open, or the character is dead.

## 2. Buff HUD (state icons and timers)

Build the catalogue at run time, not as a list compiled into the plugin, so it follows the mod's tables:

| Source | Used for |
|-|-|
| `states.txt` (through the loader's data-table service, after `DataTablesLoaded`) | which states exist; skip states that are never sent to clients (`nosend`) and pure bookkeeping states |
| `skills.txt` | which skill sets a state (`aurastate`, `passivestate`, `auratargetstate`, ...): the icon and the display name come from the skill |
| `itemstatcost.txt` | the stat behind a resource-style entry |
| the localization (string) lookup | display names in the player's language; never hard-code English |
| a small whitelist text table shipped with the mod | overrides: which states to show, their order, group (buff / debuff), name key, and resource entries |

- **Timers are frames.** The server clock is 25 frames per second (`game+0x170`); a state's end is a frame number.
  Convert to seconds only for display, and count down from the game clock, not wall time (a paused or lagging game
  must not run the timer down).
- The end frame lives on the **server** unit (the state's stat list, [d2r-93847-addresses.md](d2r-93847-addresses.md)).
  Only single player and the host of a TCP/IP game can read it; a remote client shows the icon without a time
  unless the plugin sends the end frames itself ([engine-limits.md](engine-limits.md), States).
- Whether a state is on: ask the client's copy of the local player (has-state on the local player unit), each frame
  or every few frames.
- **Resource-style entries** (charges, stacks, a meter): show `current / max` of two stats instead of a timer.
- Drawing: the SDK overlay draws lines, rectangles and text only ([ui-layouts.md](ui-layouts.md)), so icons are
  either rectangles built from the art at load or layout widgets. Size and place everything in units of the window
  height so the bar keeps its place at every resolution.
- Hover tooltips: read the cursor (do not move it) and keep the box on screen.

## 3. Weapon swap

The items move physically between body slots 4/5 (active) and 11/12 (inactive); the active set is a per-character
flag in the save header. Code that reads the equipped weapon from 4/5 is right for either set. A plugin that moves
weapons itself must keep the client's view of the four slots in step, or the next swap request is refused without a
reply. Details, the request layout and the reply order: [network-messages.md](network-messages.md) (Weapon swap) and
[saves.md](saves.md).

## 4. Waypoint UI

| What | Value (build 93847) | Confidence |
|-|-|-|
| Slots per tab | 9 | high |
| Tabs and their level ranges | one per `actinfo.txt` row, read at panel load | high |
| Waypoint record | 112 numbers per difficulty | high |

Take act ranges from the actinfo rows the game loaded, never from a hard-coded table of D2R's five acts: a mod that
adds levels or acts then still lists the right waypoints per tab. More than 9 waypoints in one act needs the button
table change in [ui-layouts.md](ui-layouts.md) (Waypoint panel).

## 5. Many clients on one machine

| What | Value | Confidence |
|-|-|-|
| `Settings.json` | one file per save folder: every client instance on the machine using that mod's save folder reads and writes the same file (window size, graphics, Quick Cast) | observed |
| Single-instance check | none in a running D2RLoader (the string exists in the game, no object with that name does); a second loader from another folder starts | observed |
| RAM per game | about 6.6-7.2 GB | observed |
| VRAM per game | about 4.1-4.5 GB | observed |
| CPU per game | about 1.5 cores on average, about 6.6 cores at the start-up peak (table compile) | observed |

- **System RAM, not VRAM, is the bottleneck**: three games fitted a 16 GB card, but took about 15-20 GB of system RAM.
- A setting changed in one instance is what every other instance reads at its next start. Give an instance its own
  `savepath` (modinfo.json) when it needs its own settings and characters.
- Find "your" game by process id or by the folder its program file lives in, never by window title or program name
  alone: a tool that closes the first `Diablo II: Resurrected` window or kills every loader process ends the other
  instances. A renamed copy of the loader and a tagged window title make instances easy to tell apart.
- Stagger starts by a few seconds; the start-up table compile is the CPU peak.
- How the toolkit runs several test games: [../docs/testing.md](../docs/testing.md).

## 6. Plugins under Wine / Proton

Players and test machines can run D2R under Proton on Linux. A plugin that works on Windows can still
fail there. What was found (D2R 3.4, Proton Experimental 11; details and code in [docs/linux.md](../docs/linux.md)):

- **Inline hooks on `win32u` syscall stubs:** Wine's stubs share the Windows head (`4c 8b d1 b8 <ssn> f6 04 25 08 03 fe
  7f 01 75 03 0f 05 c3`) but end in `eb 01 c3 ff 14 25 00 10 fe 7f c3` (a call into Wine's dispatcher) instead of
  `cd 2e c3`. A byte check that knows only the Windows tail refuses every stub. Accept both shapes, nothing else, and
  fall back to the exe's `user32` imports when a stub cannot be hooked.
- **Loader lock:** under Wine, `GetModuleHandleEx` and `GetModuleFileName` take the loader lock. Never call them while
  holding your own lock that a DLL-loading thread may also wait for: the game deadlocks at startup.
- **Missing Windows files:** a Proton prefix has few of Windows' fonts (no Segoe UI) and none of many other system
  files. An ImGui overlay that loads `%WINDIR%\Fonts\*.ttf` must check each file and fall back to the built-in font:
  Dear ImGui 1.91's error recovery opens a tooltip window when a font file is missing, and outside a frame that crashes
  the first `Present`.
- **Focus and the foreground window:** D2R under Proton asks none of `GetForegroundWindow`, `GetFocus` or
  `GetActiveWindow`; activation comes through window messages. Code that fakes focus by subclassing the window can hang
  startup on a display where the game holds the real focus. Keep such tricks opt-in.
- **Memory probes:** read game structures only where `VirtualQuery` says the memory is committed and readable. A probe
  that relies on SEH to catch a bad read still makes D2R's last-resort handler write a crash report for the handled
  first-chance exception, on Windows and under Wine alike.

## 7. Sprite-card facing

An HD monster drawn as an animated card of its classic frames ([hd-monsters.md](hd-monsters.md)) shows one direction
whatever way it faces: no data route works. A plugin can make it turn without writing into game objects, by changing
only which clip the engine picks. Built and run in game on D2R 3.3 build 93847 (8 facings, every mode, 0 asserts, no fps
cost); RVAs and entry bytes in [d2r-93847-addresses.md](d2r-93847-addresses.md#hd-unit-animation).

**The data side.** The card unit binds one clip per facing under the first 8 weapon-class keys of each state's
`animationBindings`, in the engine's own order: `hth` 0, `bow` 1, `1hs` 2, `1ht` 3, `stf` 4, `2hs` 5, `2ht` 6, `xbw` 7.
Key k shows the k-th facing clockwise on screen from "down". A monster's own weapon class is `hth`, so without the plugin
every state shows "down". Put the card units' state machines under a folder of your own (for example
`data/hd/character/<mod>/flip/`): the hooks act only on machines whose asset path holds that marker and leave everything
else at once.

**Facing key.** A path's direction is 0-63 (direction 0 looks down the screen, higher ones turn clockwise). Eight keys
as both the classic and the D2R engine count them: `key = ((direction & 63) + 4) >> 3 & 7` (60-63 and 0-3 -> 0, 4-11 ->
1, ... 52-59 -> 7). Read it with the engine's own getters: unit -> path, path -> direction.

**Three hooks.**

| Hook | What it does |
|-|-|
| state transition `(machine, entity, &model, &state, weaponClass, speed, start, force)` | for a card machine only, replace the weapon class argument with the unit's facing key |
| unit mode -> state (called per monster) | tell the transitions inside it which unit they belong to; afterwards, on the registry's own thread and only while the registry may be changed, check every watched card unit (at most 64) and, when its facing left the key its machine plays, call the engine's "same state, other weapon class" function, which keeps the clip's time |
| unit translation layer update (once a frame, on the registry's thread) | the same check. Needed because a live monster gets its own mode call only when its mode changes: a lone card that walks or stands would never turn |

Rules that made it safe:

- Turn only on the registry's own thread while the world says the registry may be changed (world +0xa0 thread id,
  +0x110 "mutable" byte; the registry getter asserts otherwise). Anything else (a stale unit, a changed unit id, a dead
  entity, another thread, an unreadable name) ends the hook's own work and the game's function runs unchanged.
- Compare the entry bytes of every function the part calls or wraps before installing anything; on any difference the
  part installs nothing and logs that it is off.
- Read watched units inside SEH, and only memory that `VirtualQuery` says is readable.

**The assert it fixed.** `BC_ASSERT outAnimationTimeElapsed >= 0.0f (animation_controller.cpp:456)` fired on card units
spawned after the first. A transition hands a start fraction (the monster's phase); the engine turns it into seconds while
the new clip's loop count is still 0, which Granny reads as "for ever" (fraction x 1.9e38 s). A clip that loops for ever
wraps that away; a card clip with counted loops stands at its end and the elapsed time goes negative. Fix: start card
states at 0 and set `fraction x clip length` as the clip time after the call.

**Routes ruled out.** Writing direction bones after the pose (per-frame writes into pose memory on the render path:
larger, riskier); swapping the model or texture per direction (one model set per unit type, a reload per turn); a hook on
the unit position read (fires on several threads, and turns still had to wait for the registry's thread).

**Test harness verb `facing`.** A harness command `facing <monster id>` that prints the client monster's id, class, mode,
path direction (0-63), facing key and position lets a game check shoot each facing on purpose (put the character where
the spawned unit faces the key you want) and label every frame. The in-game sign-off (`python -m hdkit.signoff`,
[hd-viewer.md](hd-viewer.md#the-in-game-sign-off)) relies on it.
