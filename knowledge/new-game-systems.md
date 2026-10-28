# Adding a new game system

How to plan a feature D2R has no rows for: a mapping system, a new crafting bench, a currency, a challenge mode, a
new panel. A new skill or item is one table row and a patch. A new **system** changes what the game does, so it is
always part data, part engine plugin, and usually a project of several lanes.

Read [engine-limits.md](engine-limits.md) first. Most designs that fail ignore a limit listed there.

## 1. Write it as what the player sees

Before any table or hook, list the steps the player goes through and what the game must do at each one. Example, for
a mapping system (map items that open a portal to a scaled area):

| Player does | The game must |
|---|---|
| Finds a map item | Drop it (treasure classes), roll its tier and modifiers (affixes), show them (tooltip) |
| Puts it in the cube and transmutes | Accept the recipe, consume the map, open a portal |
| Enters the portal | Lead to the right area, at the area level of the map's tier |
| Fights | Spawn monsters with the map's modifiers |
| Finishes | Drop the rewards; close the portal; let the area be used again |

Each row becomes one or more lanes. Rows that the game can do from tables are **data**. The rest is **plugin**.

## 2. Split data from plugin

Data first: many "new" behaviours are an existing D2R function with different table values. Add only what D2R lacks.

| Usually data (modkit patches) | Usually plugin (D2RLoader, from `plugin-template/`) |
|---|---|
| New item bases and item types (misc, itemtypes) | What a cube **output** does beyond D2R's built-ins (the "portal" output opens only the Cow Level) |
| Affixes and the stats they roll (magicprefix/suffix, itemstatcost, properties) | Reading an item's stats at the moment something happens |
| Areas: levels, lvlprest / lvlmaze, area level (`MonLvl*`), monster lists, density | Changing monsters as they spawn, or anything per game rather than per row |
| Drops (treasureclassex), recipes (cubemain), vendors | New cube conditions (D2R checks player ops 1-14 and item ops 15+ only; unknown ops pass) |
| Strings, item tooltips (itemstatcost `descfunc`), HD art, layouts | A new skill function (`srvdofunc` slots 172-190), a new calc code, a new event |
| Existing D2R functions reused with new values | State that lives longer than one action: timers, counters, "this portal belongs to that map" |

A good rule: if the answer to "how does the game know?" is "a column says so", it is data. If it is "something
must decide at run time", it is plugin.

## 3. Find the engine code

Start from a vanilla feature that already does something close, and follow its code:

| You need | Vanilla feature to start from | Where to look |
|---|---|---|
| A portal to a special area | The Cow Level recipe, the Pandemonium portals | Cube output "Red Portal" and the portal maker in [the registry](d2r-93847-addresses.md) (*Portals and objects*), `D2Game\src\Items\ItemMode.cpp` |
| Something when a skill is used | Any skill's `srvdofunc` | The server skill dispatcher; `plugin-template/src/skill_functions.cpp` |
| Monsters changed as they appear | Champions and uniques (they get extra mods at spawn) | The spawn-monster function in the registry; `Refs.java` on it lists the callers. Confirm which path the level populator uses |
| Something when a unit dies | "Cast on death" properties | The death function (engine-limits.md, *Events and spawns*) |
| A cube rule D2R can't express | A recipe with `op` conditions | The recipe search and input matcher (engine-limits.md, *Items, inventory, sets, runes, cube*) |
| A new panel | The stash or cube panel | Its layout json ([ui-layouts.md](ui-layouts.md)), then the client code that opens it |

Method: [engine-re.md](engine-re.md). Dump the image (`tools/dump_image.py`), analyse it once
(`tools/ghidra_analyze.ps1`), then query it: `StringXrefs.java` on an assert source path, `Refs.java` for callers,
`Decompile.java` / `Disasm.java` for the shape and the entry bytes. Check that D2RLoader doesn't already hook the
function (`FF 25` at the entry). Every confirmed address goes into the plugin's `d2r.h` with its signature, and into
[d2r-93847-addresses.md](d2r-93847-addresses.md) for the next person.

**Do a research spike first.** One lane answers "where would each hook go, and is it hookable?" before anyone writes
data. A system whose key hook turns out to be impossible is cheaper to redesign on day one.

## 4. Decide where the state lives

| Where | Lasts | Good for | Watch out |
|---|---|---|---|
| Item stats (itemstatcost rows on the item) | As long as the item; saved with the character | A map's tier and modifiers, charges, counters on gear | New stats need `Save Bits`/`Save Add` that fit the values; the extended save format needs D2RLoader ([saves.md](saves.md)) |
| Table rows | The whole mod | Anything fixed: tiers, reward tables | Indexed tables: append only, never reorder ([tables.md](tables.md)) |
| Plugin memory, per game | Until the game closes | Which area a portal belongs to, which modifiers are active, timers | Lost on exit. Fine for a map run, wrong for progress |
| Quest flags, waypoints | Saved with the character | Unlocks ("map device opened") | Few free bits; decide them once |
| A plugin's own file | Whatever you design | Account-wide progress | Your format, your bugs. Keep it next to the save, version it, never corrupt a `.d2s` |

Never move or resize D2R's save structures or unit state arrays (see plugin-template's *Rules*).

## 5. Server, client and multiplayer

- Game rules run on the **server** side of the game, even in single player. Spawns, drops, portals and skill effects
  are server work. Hook server functions for them.
- The client sees the results through D2R's normal sync: units, stats it already sends, states, items. Something only
  your plugin knows on the server is invisible to the client unless it maps onto those.
- Mark the plugin `Shared` (the template does) and bump its version with every release: host and client must run the
  same one in TCP/IP games.

## 6. UI

Try these in order. Each one costs much less than the next.

1. **No new UI**: a cube recipe, an item, a tooltip line, an NPC's store.
2. **A changed panel**: an existing layout with new art or grids ([ui-layouts.md](ui-layouts.md), the `layout` and
   `panel` ops).
3. **A new panel**: a layout json plus client code that opens it and fills it. That is real RE work on the client
   side. Plan it as a separate lane, after the system works through the cube.

## 7. Check the limits

The ones new systems hit most (all in [engine-limits.md](engine-limits.md)):

- Level ids: portal links store a byte in many places, so keep new levels at id 255 or below. That leaves few free
  ids: count them before you design "one area per tier".
- levels.txt `Layer` below 100; clear `Position` on new maze levels; ship every tile file a level type uses.
- Portals link levels **within one act** only.
- The cube "portal" output opens only the Cow Level; unknown cube `op` values are ignored.
- Indexed tables: append only. Item type numbers D2R tests in code stay where they are.
- 30 skills per class; new events are never raised by D2R itself.

## 8. Split it into lanes

A typical order, with one PR each ([docs/ai-workflow.md](../docs/ai-workflow.md)):

1. **RE spike** (strong model): find and confirm every hook, write them into `d2r.h` and the registry.
2. **Data** (routine): items, affixes, stats, levels, recipes, drops, strings, art stand-ins. It builds and validates
   on its own and can be tested in game with no plugin (the items drop, the recipe is accepted).
3. **Plugin parts** (strong model), one part and one off-switch word each: for example `mapportal`, `mapmods`,
   `maplevel`.
4. **Tests**: one named check per row of the table in step 1 ([docs/testing.md](../docs/testing.md)).
5. **Art and polish**: real HD art, tooltips, a panel if wanted.
6. **Release** ([docs/release.md](../docs/release.md), [docs/distribution.md](../docs/distribution.md)).

## 9. Make it debuggable from day one

- One part word per hook in `g_parts`, so `<name>-off.txt` can turn each one off.
- A log line per part at load (`mapportal on`, or why it is off), and the part's counters in the status command.
- Refuse, don't guess: a hook whose entry bytes differ is not installed, and the system says which part is missing.
