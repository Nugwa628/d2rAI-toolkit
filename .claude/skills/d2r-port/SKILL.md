---
name: d2r-port
description: Port a classic Diablo II mod (1.10 / 1.13c era, with or without its own engine DLL) to Diablo II Resurrected 3.x with D2RLoader - inventory the mod, convert its tables row by row into D2R's layout, keep D2R's own ids where the engine hard-codes them, move function numbers D2R 3.x took, re-do the engine DLL's rules in a plugin, then strings, sounds and graphics (HD first, stand-in art first). Use when someone wants to bring an existing classic mod, its tables or its DLL features to D2R, or asks why a converted classic table misbehaves.
---

# Port a classic mod to D2R

Read `knowledge/porting-classic-mods.md` first; it has the rules and the worked examples. Then
`knowledge/tables.md`, `knowledge/engine-limits.md` and `knowledge/d2rloader.md`.

## Policy (tell the user, then follow it)
- Follow the original mod, not D2R, when their rules disagree. List every rule you could not follow.
- Get the mod team's permission. Never commit Blizzard's files or the mod's archives.
- HD first; classic graphics later. Stand-in vanilla art first, custom art last.
- Keep an unported list (feature, why, what players see) in the mod project.
- First milestone: a new character in Act 1. Then work from what breaks.

## Steps
1. **Inventory**: game version, engine DLL or patched DLLs, extra tables, function numbers above vanilla's, extra
   events.txt rows, row counts, art formats, save changes. Triage the DLL by its assert source-file names into
   data / loader service / hook / UI rebuild / cut.
2. **Extract** both games into ignored folders (`python -m modkit fetch` for D2R). Decode the mod's tables and
   formulas to text.
3. **Merge table by table** (porting-classic-mods.md section 3): indexed tables keep D2R's added rows at D2R's
   numbers and append the mod's extras; item/affix/TC/recipe tables take the mod's rows only; sounds and calc tables
   keep D2R's rows first. Fix the column differences (GH -> SQ, overlay `Character`, `LevRange`, inventory rows,
   signed values, both banks).
4. **Ids**: D2R-only rows stay at D2R's numbers (itemtypes `warl` 67, or D2R's rune test hits circlets). Remap every
   reference after rows move, including hit-6 missiles' `sHitPar1` (a monstats id). Add a build-time id self-check.
5. **Function numbers**: move the mod's srvdofunc 153+ to 172-190, cltdofunc 96+ to 110-129; fix 109 / 64 / 82;
   map to an existing D2R function wherever one does the same job. Fail the build on a reassigned number.
6. **Behaviour D2R lacks**: events D2R never raises (cast on death: hook the death function), ranged pets
   (a summon `Param` flag), Raven-AI pets (`calc3`), `rightskill 1` on every active and innate skill, timed potions, cube portals.
   Plugin work goes through the `d2r-engine` skill.
7. **Strings** (namespace, no new numeric ids, keep D2R's placeholders), **sounds** (resolve every name; plugin
   sound guard), **icons** (read each sheet's real frame layout).
8. **Validate to 0 errors**, install with the game closed, start twice the first time (D2RLoader's cache-building
   start loads D2R's sounds), read `d2rloader.log`, fix load asserts, get into Act 1.
9. **Graphics** (`knowledge/hd-graphics.md`): HD item/unique/set entries, missiles.json, HD states for sequence
   skills, overlay folders, model/texture indexes for new paths; ship the classic files the tables reference.
10. Keep the bug list and TESTING.md current; crashes from players go through the `d2r-crash` skill.

## Before you say "done" with a table
- Row count under 32,768, keys within their dataguide lengths.
- Every reference names an existing row; ids D2R tests are D2R's.
- Every function number is D2R's own for the same job, or a plugin slot.
