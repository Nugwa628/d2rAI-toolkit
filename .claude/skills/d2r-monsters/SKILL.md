---
name: d2r-monsters
description: Create or change Diablo II Resurrected monsters, bosses, superuniques, minions, monster skills and AI, monster levels and density, and areas (levels.txt spawns, area levels, waypoints) in a modkit mod. Use for any request about enemies, difficulty, bosses or where monsters appear.
---

# Monsters and areas

Start from a vanilla monster: `python -m modkit show monstats zombie1`, `show monstats2 zombie1`,
`show levels "Blood Moor"`. Read knowledge/tables.md (monster section) and knowledge/engine-limits.md.

## A new monster = rows in three tables
1. **monstats.txt** (`clone_row` from a monster with the right behaviour): `Id` (key), `NameStr` (string key),
   `MonStatsEx` (monstats2 row), `MonType`, `AI` (monai.txt name: the behaviour, cannot be invented in data),
   `Code` (graphics token), `Level`/`Level(N)`/`Level(H)`, life `minHP`/`maxHP` (+ `(N)`, `(H)`), `AC`, `Exp`,
   attacks `A1MinD`/`A1MaxD`/`A1TH`, resistances `ResDm`/`ResMa`/`ResFi`/`ResLi`/`ResCo`/`ResPo`, speed
   `Velocity`/`Run`, `aip1-8` (AI parameters, meaning per AI), `Skill1-8` + `Sk1mode` (monmode code or a monseq
   sequence) + `Sk1lvl`, `MissA1`.. (missiles per mode), treasure classes, `minion1/2`, `PartyMin/Max`,
   `MinGrp/MaxGrp`.
2. **monstats2.txt**: graphics: which body parts and modes exist (`HD`..`S8`, `mDT`..`mRN` flags), sizes, light,
   selection box, `Height`. Clone the row of the monster whose look you reuse.
3. **Strings**: `string` op for `NameStr` (file `monsters.json`).

HD look: `data/hd/character/monsters.json` maps the monstats `Id` to an HD model; the classic look and the timing
come from `Code`. Map a new monster's `Id` to the HD model of the monster you borrow from, or it has no HD model.
New models at new paths also need index entries (knowledge/hd-graphics.md).
Colour variations: monstats2 / `Utrans`-style palette shifts only affect classic mode.

## Bosses and uniques
- superuniques.txt: `Superunique` (key), `Name` (string), `Class` (monstats Id), `Mod1-3` (monumod.txt
  modifiers), `MinGrp/MaxGrp`, `TC`/`TC(N)`/`TC(H)`; placed through level presets or quests.
- monumod.txt: the random champion/unique modifiers (new ones need engine code beyond existing functions).
- Act bosses and quest monsters are tied to quest code: change their stats and drops, not their role.

## Monster skills
Monsters use skills.txt rows (often monster-only skills with no class): give the monster `Skill1` = skill name,
`Sk1mode` = the mode that casts it (`S1`, `A2`, `SC`, or a monseq sequence name), `Sk1lvl`. The AI decides when to
use skill slots (see the AI's aip meanings); pick an AI whose vanilla users cast skills the same way.

## Where monsters spawn (levels.txt)
`mon1-25` (normal), `nmon1-25` (nightmare/hell), `umon1-25` (unique candidates), `MonLvl`/`MonLvl(N)`/`MonLvl(H)`
(+ `MonLvlEx*` for expansion), `MonDen*` density, `MonUMin/MonUMax*` unique packs, `NumMon` count of mon slots used.
New areas: levels.txt rows need a level type (lvltypes, D2R has a fixed list of 36: no new level types without a
plugin) and presets/maps (lvlprest, lvlmaze, lvlsub); reuse existing layouts with new monsters/levels first.

New maze levels: leave `Position` empty unless the maze's presets have entry rooms (else the game crashes on
entry); keep `Layer` below 100 and automap cels at or below 1498 (knowledge/engine-limits.md). Cast-on-death
properties need a plugin: D2R never raises a mod's own events. A busy scene (many traps and effects per attack) can
fill the renderer's 12 MB pool.

## Difficulty
monlvl.txt (per-level stat scaling, separate L columns per difficulty), difficultylevels.txt (resist penalties,
mercenary/pet rules, drop rates), levels `MonLvl*`.

## Build and test
Build, validate, install with the game closed. Test list: walk into the area, see the monster in HD and classic,
its name, life/damage at the expected level, its skills firing, drops, and that it dies normally (no stuck AI).
