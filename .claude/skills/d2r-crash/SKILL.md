---
name: d2r-crash
description: Triage a Diablo II Resurrected crash from a player's files - read the D2RLoader crash report (RIP, registers, stack), turn addresses into D2R.exe RVAs and functions with a memory dump and the Ghidra scripts, decide whether the mod, its data, another plugin, the loader setup or the machine caused it, and write a fix with evidence. Use when the user pastes or attaches a crash report, a d2r-lastresort-crash-report log, an access violation address like D2R.exe+0x..., or says a player's game crashed.
---

# Triage a crash

Read `knowledge/crash-triage.md` (the method and a worked example), `knowledge/d2rloader.md` (environment traps)
and the symptom table in `knowledge/debugging.md`.

## 1. Get the files (ask once, same session)
- `<game>\d2rloader\crashes\*` (crash and last-resort reports)
- `<game>\d2rloader\logs\d2rloader.log` (asserts, plugins, cache-building start)
- `<game>\mods\<Mod>\d2rloader\logs\*` (the plugin's own log: version, image, parts, other plugins)
- what was on screen, D2R version and store, other plugins

## 2. Read before guessing
1. Module: `Allocation Base 0x140000000` = D2R.exe; RVA = RIP - 0x140000000. Another base: a DLL (yours,
   D2RCore, a driver).
2. Exception parameter 0 = read (0) / write (1); parameter 1 = address. Small address = null + field offset.
3. Disassemble the RVA (`tools/ghidra_query.ps1 Disasm.java 0x<rva>`): which register was bad? Its value is in the
   report.
4. Map the RIP and every image value on the stack to functions (the script in crash-triage.md, on a dump of the
   same image). Trust a caller chain that makes sense, not every stale slot.
5. Decompile the function and its callers (`Decompile.java`), find the source file from its strings
   (`StringXrefs.java`), find which table field feeds the bad value (`FindDisp.java`).

## 3. Rule out the environment
- Plugin log version and path are the build you released from `mods\<Mod>`? (`default_mod`, stray plugin copies)
- First start after a D2RLoader / D2R install or update? (D2R's sounds table; restart)
- Other plugins loaded? Remove them and retry.
- Noise: a first-chance report in a non-D2R module at game exit; `Present failed` / `DXGI_ERROR_DEVICE_REMOVED`.
- Bisect your plugin with `<plugin>-off.txt` before reading code.

## 4. Fix and record
- Fix the data when the data is wrong (an id out of range, a missing HD entry, a reassigned function number).
- Fix with a plugin guard when D2R itself fails on a case the mod creates (null allocation, bad sound id): byte-
  checked hooks, counted and logged, an off word and a status line.
- Write the cause and evidence (report lines, instruction, register) in the bug list; add a check that would
  catch it again. If you could not reproduce it, say so, and say what the log will show if it returns.
- New engine facts go into `knowledge/` (addresses with the build number).
