---
name: d2r-debug
description: Diagnose a Diablo II Resurrected mod that crashes, asserts, hangs, shows the wrong thing, or loses characters - read D2RLoader/D2R logs and crash dumps, map symptoms to causes, and bisect. Use whenever the user reports the mod "crashed", "doesn't load", "character disappeared", "looks wrong" or a skill/item/UI misbehaves in game.
---

# Debug a D2R mod

The user tests; you diagnose from files. Don't ask them to describe a crash: read the logs.

A crash report with an address (`D2R.exe+0x...`, a last-resort report): use the `d2r-crash` skill and
knowledge/crash-triage.md. Loader-side surprises (another `default_mod`, the cache-building first start, other
plugins, stored key bindings): knowledge/d2rloader.md.

## Where to look (newest first)
- `<game>/d2rloader/logs/d2rloader.log`: load steps and **asserts after "startup complete"** (data errors name
  the table and row).
- `<game>/d2rloader/crashes/*.log`: crash reports with the faulting RVA. Map the RVA to a function in Ghidra
  (`Decompile.java <rva>`), then to a table column it reads.
- `%LOCALAPPDATA%\CrashDumps\D2RLoader.exe.<pid>.dmp` (or D2R.exe): crashes without a report.
- `<game>/blz-log.txt`: D2R's own session log (missing files like `[default_unit]`, string and asset warnings).
- `<game>/mods/<Mod>/d2rloader/logs/<plugin>.log`: your plugin's lines.
- Hangs: dump the busy thread (MiniDumpWriteDump) a few times; repeated instruction pointers mark the loop.

## Symptom -> usual cause
See knowledge/debugging.md for the full table. Most common:
- **Skill plays the knockback/get-hit animation**: `anim` = `GH` (get hit) instead of `SQ`; or plrmode Code changed.
- **Crash the moment a sequence skill is used**: anim `SQ` but the mode lookup failed (plrmode/monmode codes must
  stay D2R's: SQ, KB).
- **Skill animates in classic but not HD**: no HD state for its skill ID (anim SQ) - `hd_sequence` op.
- **Missile invisible in HD**: no missiles.json entry - `missile_hd` op.
- **No HD aura/overlay effect**: overlay.txt `Character` empty (it names the HD overlay folder).
- **Item picture missing / vendor crash**: base item code missing from data/hd/items/items.json - `item_hd` op.
- **Character missing from character select**: the save no longer matches (checksum, .d2rl hashes, or more class
  skills than the save format holds). Restore the backup; edit saves only with `modkit save-set`.
- **Load assert about states or level types**: engine limits (knowledge/engine-limits.md); needs a plugin or fewer rows.
- **Strings crash on a message**: a key D2R formats itself got different `%s/%d` placeholders.
- **Crash or hang on picking up / wearing one item type** (assert ItemMode.cpp:676): itemtypes ids shifted; D2R's
  rows must keep D2R's numbers (`warl` 67).
- **Crash when a monster comes into view / all new sounds silent**: sound id -1, or the first start after a
  D2RLoader/D2R update loaded D2R's sounds table. Restart; plugin sound guard.
- **Write to address 0 at D2R.exe+0xF3D028 in a busy fight**: the Renderer Pool is full (heap fallback plugin).
- **Fix works on one PC, not another**: `default_mod`, a stray plugin copy, or other plugins. Read the plugin log's
  first lines.
- **Monsters can't be hovered / overlays gone after a plugin change**: the plugin patched something D2RLoader
  already handles (state sync). Turn plugin features off one by one with the off-switch file.

## Bisect
1. Plugin: list feature words in `<plugin>-off.txt` (half at a time) until the problem goes.
2. Data: move patches out of `patches/` (or comment ops out by splitting files), rebuild, reinstall; halve until
   one op remains. Then compare that row with the vanilla row (`modkit show`).
3. Fix, rebuild, add a TESTING.md row for the regression.
