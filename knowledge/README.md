# Knowledge base

What a D2R overhaul mod runs into, learned from a large classic-to-D2R overhaul port. Target: **D2R 3.3.0
build 93847** with **D2RLoader 1.3.1** (PluginSDK 0.3.0, ABI 4). Everything here uses only D2R's own files and engine:
the toolkit has no MPQ/DC6/DCC conversion pipeline. Bringing a classic mod over (its tables, its engine DLL's
rules) is covered in [porting-classic-mods.md](porting-classic-mods.md).

| File | What it covers |
|---|---|
| [engine-limits.md](engine-limits.md) | Every hard-coded limit a mod hits: rule, symptom when broken, fix (data or plugin) |
| [new-game-systems.md](new-game-systems.md) | Planning a new game system (for example a mapping system): data vs plugin, finding the hooks, where state lives, multiplayer, UI, lanes |
| [tables.md](tables.md) | How D2R reads its `.txt` tables: banks, indexed vs keyed, IDs, adding rows safely, per-table cheat sheet |
| [hd-graphics.md](hd-graphics.md) | HD assets: `.sprite` format, item/skill/panel art, HD json maps, overlays, player state machines, monsters |
| [ui-layouts.md](ui-layouts.md) | `data/global/ui/layouts/*hd.json`: widget tree, grids, panels, tabs, stash/inventory/cube/skill tree |
| [strings.md](strings.md) | String JSON files, complete-file replacement, safe new IDs, printf placeholder hazard |
| [saves.md](saves.md) | `.d2s` v105 layout (retail and D2RLoader extended), checksum, `.d2rl` sidecar, safe and unsafe hand edits |
| [item-format.md](item-format.md) | The retail bit-packed item record: fields per quality, Huffman code, stat lists, save copy vs network copy |
| [engine-re.md](engine-re.md) | Finding and hooking D2R code: memory dump, Ghidra headless, query scripts, hook rules, logs |
| [engine-server.md](engine-server.md) | The server half: game loop, table sets and the game-edition flag, treasure classes and item creation, inventory rules, NPCs and vendors, quests, death and party |
| [combat-formulas.md](combat-formulas.md) | Hit chance, block, the damage pipeline and resist caps, crushing blow and open wounds, experience, skills and missiles, itemstatcost ops, hirelings |
| [monsters-spawn.md](monsters-spawn.md) | Room population, monster density and packs, superuniques, the AI table and aip parameters, corpses and resurrection |
| [map-generation.md](map-generation.md) | Level generation: seed chain, act and level layout, the tables that drive it, tiles and rooms, room streaming, travel and act change |
| [items-inventory.md](items-inventory.md) | Death drops (TC choice, ground spot search), item ids and seeds, level requirement, main weapon and inventory requests, vendor stock, restock, prices and transactions |
| [skills-missiles.md](skills-missiles.md) | Skill start/do and per-slot behaviour (RVAs, seed draws, D2R changes), monster per-attack stat draws, the missile hit handler, timed states and curses, mercenary AI, stats and item giving |
| [monster-ai.md](monster-ai.md) | The AI tick and target search, think timing, what each common AI does with its aip values, hirelings and summons, unique-mod start handlers at monster init, the 0xac message, player corpses |
| [objects.md](objects.md) | World objects: populate/init/operate tables, PreOperate and mode jitter, chests and their TC level, traps, shrines, wells, doors, portal pairs, object messages |
| [quests-acts.md](quests-acts.md) | Quests across the five acts: quest records and flags, the shared quest machinery (status, syncs, sequence filters, intro rule), per-quest triggers and messages, Tyrael's trip and the end of game |
| [hirelings.md](hirelings.md) | Mercenaries outside combat: the hireling row, hire list and quote, hire and resurrect, death and the owner's death or leave, items, save and load, messages |
| [movement-modes.md](movement-modes.md) | Collision bits and move masks, path finders and the step, line of sight, speed, unit modes, animation timing and breakpoints, the event queue, mode and walk messages |
| [units-stats.md](units-stats.md) | Units, stat lists, states and party records: unit header, allocation and ids, seed draw order, stat list flags, state bits, relation and party rules, server timing |
| [network-messages.md](network-messages.md) | Client/server game messages: framing and sizes, the useful ids, server hook points, the join in order, item action codes, weapon swap |
| [d2r-93847-addresses.md](d2r-93847-addresses.md) | Address registry for build 93847: functions, tables, record and unit offsets, entry bytes |
| [debugging.md](debugging.md) | Symptom -> cause -> fix table from real bugs, and where every log lives |
| [crash-triage.md](crash-triage.md) | From a player's crash report to a fix: RVA from RIP, stack return addresses, Ghidra, which logs to ask for, a worked example |
| [d2rloader.md](d2rloader.md) | D2RLoader facts and traps: default mod, the cache-building first start, other plugins, version checks, its files |
| [porting-classic-mods.md](porting-classic-mods.md) | Porting a classic 1.13c-era mod: inventory, table-by-table conversion, ids that must not move, function numbers, events, art order |
| [plugin-techniques.md](plugin-techniques.md) | Plugin feature recipes: belt refill, buff HUD from tables and the 25 fps clock, weapon swap, waypoint UI from actinfo, running many clients (shared Settings.json, RAM) |
| [sdk-gaps.md](sdk-gaps.md) | For the D2RLoader SDK author: every place plugins still hook or patch the game (build 93847 RVAs), what breaks, and the SDK service that would replace each patch, by priority (checked against PluginSDK 0.4.0) |
| [modkit-ops.md](modkit-ops.md) | The modkit's patch operations (JSON ops applied by `modkit build`) |

## Rules that apply to every file

- **RVAs are for build 93847 only** (D2R image under D2RLoader 1.3.1, PE timestamp `0x6ab3782c`). Before using an RVA
  on any other build, **verify it in Ghidra** against a fresh dump (the function's shape, its callers, its entry
  bytes). A plugin must check the image timestamp at load and install nothing on a mismatch.
- Every hook and table write is byte-checked (`InstallInlineHook` / `PatchWriteU64` with the expected bytes); a
  mismatch must refuse, never patch blind.
- Data before code: many "new" behaviours are an existing D2R function with different table values.
- Facts marked "observed" or "unverified" were seen once in one port and not proven in general.
