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
| [saves.md](saves.md) | `.d2s` v105 essentials, checksum, D2RLoader `.d2rl` sidecar, safe and unsafe hand edits |
| [engine-re.md](engine-re.md) | Finding and hooking D2R code: memory dump, Ghidra headless, query scripts, hook rules, logs |
| [d2r-93847-addresses.md](d2r-93847-addresses.md) | Address registry for build 93847: functions, tables, record and unit offsets, entry bytes |
| [debugging.md](debugging.md) | Symptom -> cause -> fix table from real bugs, and where every log lives |
| [crash-triage.md](crash-triage.md) | From a player's crash report to a fix: RVA from RIP, stack return addresses, Ghidra, which logs to ask for, a worked example |
| [d2rloader.md](d2rloader.md) | D2RLoader facts and traps: default mod, the cache-building first start, other plugins, version checks, its files |
| [porting-classic-mods.md](porting-classic-mods.md) | Porting a classic 1.13c-era mod: inventory, table-by-table conversion, ids that must not move, function numbers, events, art order |
| [modkit-ops.md](modkit-ops.md) | The modkit's patch operations (JSON ops applied by `modkit build`) |

## Rules that apply to every file

- **RVAs are for build 93847 only** (D2R image under D2RLoader 1.3.1, PE timestamp `0x6ab3782c`). Before using an RVA
  on any other build, **verify it in Ghidra** against a fresh dump (the function's shape, its callers, its entry
  bytes). A plugin must check the image timestamp at load and install nothing on a mismatch.
- Every hook and table write is byte-checked (`InstallInlineHook` / `PatchWriteU64` with the expected bytes); a
  mismatch must refuse, never patch blind.
- Data before code: many "new" behaviours are an existing D2R function with different table values.
- Facts marked "observed" or "unverified" were seen once in one port and not proven in general.
