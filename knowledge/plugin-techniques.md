# Plugin techniques: belt refill, buff HUD, weapon swap, waypoint UI, many clients

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
