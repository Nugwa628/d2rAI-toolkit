---
name: d2r-skill
description: Create a new Diablo II Resurrected skill, or rework an existing one, from a plain description ("a frost whirlwind for the barbarian", "a summon that throws fire", "an aura that ..."). Use for any request to add, change, balance or re-theme a D2R skill in a modkit mod project, including its tooltip, icon, skill tree slot, missiles, states and HD animation.
---

# Make a D2R skill

You turn a skill idea into a `skill` op in a modkit project, build it, check it, and hand the user a test list.
Read `knowledge/engine-limits.md` once per session if you haven't; it lists what D2R hard-codes.

## 1. Pin the design down
Ask only what changes the build: class (or item/monster-only), which existing skill it replaces (if a class skill:
see step 3), and anything unusual (multiple hits, summons, an aura). Pick sensible numbers yourself.

## 2. Find the vanilla skill whose behaviour is closest
A skill's behaviour is its *functions* (skills.txt srvstfunc / srvdofunc / cltstfunc / cltdofunc / srvstopfunc /
cltstopfunc) plus columns those functions read. You cannot invent behaviour in data; you choose a function.
- `python -m modkit functions --column srvdofunc` lists every server function number with the vanilla skills that use it
  (also `--column cltdofunc`, `srvstfunc`, `cltstfunc`).
- `python -m modkit show skills "Whirlwind"` prints a skill's non-empty columns. Read 2-3 candidates; the one whose
  functions match the idea is the `clone`. Its columns tell you which ones the function reads (Param1-8, calc1-4,
  aurastate, srvmissile*, sumskill*, ...).
- Behaviour no vanilla function has (e.g. a fan of missiles with a twist) needs a plugin function: use the
  d2r-engine skill (plugin-template/src/skill_functions.cpp shows one in free slot 172), then set srvdofunc to
  that slot. Prefer data whenever a vanilla function is close enough.

## 3. Class skills: replace, don't add
Every class has exactly 30 skills and saves store one byte per class skill. Adding a 31st needs mod.json
`"extendedSaves": true` (D2RLoader extended save format; existing characters of the mod must be made under it).
The normal path is `"replace": "<vanilla skill>"`: the row keeps its ID, tree slot and save byte. Pick a skill the
user doesn't need, in the right tree tab. Item/monster-only skills (`"class": ""`) can be added freely.

## 4. Write the op
Add it to `<project>/patches/NNN-<topic>.json`:

```json
{"ops": [{
  "op": "skill", "name": "Frost Cyclone", "replace": "Double Throw", "clone": "Whirlwind",
  "skills": {"EType": "cold", "EMin": 4, "EMax": 8, "EMinLev1": 3, "EMaxLev1": 6, "ELen": 75},
  "skilldesc": {"descline1": "", "dsc2line1": ""},
  "tree": {"page": 1, "row": 3, "column": 2},
  "text": {"name": "Frost Cyclone", "short": "Frost Cyclone", "long": "Spin through enemies, chilling them", "alt": "Frost Cyclone"},
  "icon": "assets/frost_cyclone.png",
  "missiles": [{"name": "frostcyclonebolt", "clone": "icebolt", "set": {"Vel": 25}, "hdLike": "icebolt"}],
  "states": [{"name": "frostcycloned", "clone": "freeze", "set": {}}],
  "hdSequenceLike": "Whirlwind"
}]}
```
All fields are documented at the top of `modkit/skill.py`. Rules that bite:
- **Damage**: `EType` / `EMin` / `EMax` / `EMinLev1-5` / `EMaxLev1-5` (elemental), `MinDam`/`MaxDam` +
  `MinLevDam1-5` (physical), all scaled by `HitShift` (8 = whole points, lower = fractions of a point; keep the
  clone's), `ELen` + `ELevLen1-3` in frames (25 per second) for cold/poison/burn lengths, `SrcDam` = share of weapon
  damage (128 = 100%).
  Synergies: `EDmgSymPerCalc` / `DmgSymPerCalc` formulas like `(skill('Ice Bolt'.blvl)+skill('Ice Blast'.blvl))*15`.
- **Formulas**: max 255 characters, quoted names max 32 bytes, no bare negatives (modkit rewrites them),
  ternaries bracketed. Calc codes are listed in skillcalc.txt (`python -m modkit show skillcalc <code>`).
- **Animation**: `anim` must be a plrmode code (`SC` cast, `A1`/`A2` attack, `S1`-`S4` special, `TH` throw, `KK` kick,
  `SQ` sequence). `GH` is *get hit*, never a skill animation. `SQ` needs `seqnum`/`seqtrans` from a vanilla sequence
  skill and an HD state (the op copies one: `hdSequenceLike` must be a skill of the same class that has one, e.g.
  Whirlwind, Zeal, Fend, Strafe, Double Swing). Skills built on Whirlwind's functions keep its stop functions
  (srvstopfunc/cltstopfunc 1); without them the spin never ends.
- **Missiles** need an HD model (`hdLike`) or they are invisible in HD. Overlays and states that should show an
  effect need overlay.txt `Character` set (the HD overlay folder) - clone a vanilla one.
- **Tooltip**: skilldesc descline/dsc2line/dsc3line rows use description function numbers; clone a skill with a
  similar tooltip and change texts/calcs rather than inventing line types.
- **Mana**: `mana`, `lvlmana`, `minmana`, `manashift` (mana = (mana + lvlmana*(lvl-1)) << manashift >> 8).
- **Requirements**: `reqlevel`, `reqskill1-3` (names), `maxlvl`; `skpoints` formula.
- **Icon**: a PNG (any size, square looks best) becomes the HD icon (130 px) and the classic one; or
  `{"from": "<skill>"}` to reuse another skill's icon of the same class.

## 5. Build, read the result, fix
`python -m modkit build <project>`: it prints the skill's ID and icon frame, warnings, and the validator summary
(`<project>/out/validation.md`). Fix every error. Read the warnings: a renamed skill must also be renamed where
other tables name it (`python -m modkit find monstats Skill1=<old>`, charstats StartSkill, item properties).

## Traps from ports
- `rightskill 1` on every active skill, innate charstats skills too, or it can't go on the right button.
- Raven-AI pets read their attack count from `calc3`; empty = the pet dies at once.
- New server do functions at 172-190; classic 153-171 and 109 are D2R's now (knowledge/engine-limits.md).
- Hit-6 missiles spawn the monstats row in `sHitPar1`.
- Class-less skills share one 62-icon HD sheet (knowledge/hd-graphics.md).

## 6. Hand over
Install only with the game closed (`python -m modkit install <project>`), then give the user a short test list in
the project's TESTING.md: get the skill (`save-set ... --skill SLOT=1` or skill points), cast it in town and on
monsters, check tooltip numbers at levels 1 and 2, HD and classic (G) graphics, the icon, and hold-to-repeat.
Then keep working; the user tests in batches.
