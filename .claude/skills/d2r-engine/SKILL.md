---
name: d2r-engine
description: Change Diablo II Resurrected engine behaviour that game data cannot express - new skill functions, new missile/item functions, cube conditions, calc codes, raising hard-coded limits, or hooking any game function - with a D2RLoader plugin built from plugin-template, and reverse engineering D2R with Ghidra to find the code. Use when a mod feature needs code, or when asked to find/understand a D2R function.
---

# Engine work: D2RLoader plugins and reverse engineering

Data first: check knowledge/engine-limits.md and the vanilla function catalogs (`python -m modkit functions`)
before writing code. When code is needed, read knowledge/engine-re.md and knowledge/d2r-93847-addresses.md, and
knowledge/d2rloader.md for what the loader does on players' machines. Porting a classic engine DLL: the `d2r-port`
skill first. A whole new game system (a mapping system, a new crafting bench, a new panel): plan it with
knowledge/new-game-systems.md first (player steps, data vs plugin split, an RE spike for every hook, where state
lives), then build it part by part with this skill.

## The plugin
1. Copy `plugin-template/` into the mod project (e.g. `<project>/plugin/`), build with
   `powershell -ExecutionPolicy Bypass -File plugin\build.ps1 -Name <mod>-core` (point `-Sdk` at this repo's
   `third_party/PluginSDK`). Add the DLL path to mod.json `plugins`; `modkit install` copies it to
   `<game>/mods/<Mod>/d2rloader/plugins/`. Players run the mod through D2RLoader.
2. The plugin only hooks the exact game image it was written for (PE timestamp check, 0x6ab3782c = D2R 3.3.0
   build 93847 under D2RLoader 1.3.1). After a game or loader update: re-dump, re-find every address, update.
3. Keep the template's guards on: build guard (image stamp, off on mismatch), environment guard (other plugins,
   stray copies, `default_mod`), sound guard (bad sound ids, the cache-building start), item type id self-check.
   Each part has a status line in the `<plugin>` console command and one log line at load (`log.h`).
4. Every feature has a word in the off-switch file `<plugin>-off.txt` (next to the DLL): list words to turn
   features off when bisecting a crash. Keep this for every feature you add.

## Common jobs
- **New skill function**: write `int64_t Fn(Game*, Unit*, int32_t skillId, int32_t level) noexcept` in
  `skill_functions.cpp`, register it in a free srvdofunc slot (172-190; D2R uses up to 171), set the skill's
  `srvdofunc` to that number. The template's slot-172 missile fan shows target lookup, calc evaluation and missile
  creation.
- **Hook a function**: `InstallInlineHook(rva, entryBytes, size, Hook, &original)`. Entry bytes must be whole
  instructions covering at least 14 bytes (REX prefixes like 40/48 included), copied from the disassembly
  (`Disasm.java`); the SDK refuses on mismatch. Hook function pointer types must be `noexcept`.
- **Before patching anything**, check the function entry for `FF 25` (jmp [rip+x]): D2RLoader already hooks it
  (e.g. its wide state sync). Work with D2RLoader's exports instead of fighting them; patching state arrays yourself
  broke monster hover and overlays in a past port.
- **Table slots** (do-function tables, missile/item function tables): `PatchWriteU64(rva, expected, size, value)`
  with the slot's current bytes as expected (zero for empty slots).

## Finding code (Ghidra)
1. With the game running through D2RLoader: `python tools/dump_image.py` (D2R's code is encrypted on disk; the
   dump is the in-memory image, RVA = address - image base). Analyse once: `tools/ghidra_analyze.ps1` (hours).
2. Query read-only: `powershell -ExecutionPolicy Bypass -File tools/ghidra_query.ps1 <Script>.java args`:
   `StringXrefs <text>`, `NearRefs`, `Refs <rva>`, `Decompile <rva>`, `Disasm <rva>`, `FindDisp <offset>`
   (users of a struct field), `FindSwitch <bound>` (switches with N cases), `FindPtrTables` (dispatch tables).
3. Fastest lead: D2R keeps its source paths in asserts (`D2Game\src\Player\PlrTrade.cpp`,
   `D2Common\src\DataTbls\HoradricCube.cpp`): StringXrefs the file name -> NearRefs the descriptor -> Refs the
   tiny assert stub -> the functions of that source file. Then decompile and match by constants, switch shapes and
   record offsets (FindDisp on a data-table offset lists every reader of a table).
4. Record every confirmed address in knowledge/d2r-93847-addresses.md (or your build's file) with its signature
   and entry bytes.

## Debug
Logs and crash dumps: d2r-debug skill. Add one log line per feature at install (installed / skipped and why).
