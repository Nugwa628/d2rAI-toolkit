# Porting a classic (1.13c-era) mod to D2R 3.x

How to move a classic Diablo II mod (1.10 / 1.13c, with or without its own engine DLL) onto Diablo II: Resurrected
3.3 with D2RLoader. **Only port a mod whose creators allow it** (see the first rule below). Learned from a large classic-to-D2R overhaul port. Every example here is vanilla; the
numbers are for D2R 3.3.0 build 93847 unless marked otherwise.

Read with: [tables.md](tables.md) (how D2R reads tables), [engine-limits.md](engine-limits.md) (what D2R hard-codes),
[strings.md](strings.md), [hd-graphics.md](hd-graphics.md), [engine-re.md](engine-re.md) (finding code),
[d2rloader.md](d2rloader.md), [crash-triage.md](crash-triage.md).

## 0. Policy (decide these first)

| Rule | Why |
|---|---|
| **Follow the original mod, not D2R.** Where the two disagree on a rule, the original mod wins; note every place where you could not follow it | Players of the original know its rules; "D2R does it differently" is a bug report waiting to happen |
| **Port only with the mod creators' permission.** Ask before any work starts, keep their answer, and follow their terms (credit, whether and where the port may be published, what may be changed). No answer or a no means no port | The mod is their work; a port without permission takes it from them |
| **Never ship Blizzard's files or the original mod's archives** in your repo | Players own D2R; the original mod's files come from its own release |
| **HD first.** Make every feature work in HD graphics; classic graphics (the G key) come later | Almost every D2R player plays in HD, and both modes load different files |
| **Stand-in art first, custom art last.** Give every new monster, missile and item the closest vanilla look until the rules work | Art is the biggest single job; gameplay bugs are found faster with a playable build |
| **Keep an unported list** (section 10) | So "missing" never gets mistaken for "broken" |
| First milestone: **a new character in Act 1** as early as possible | From then on, work from what breaks in game |

## 1. Inventory the mod

Before converting anything, write down:

| Question | Where to look | Why it matters |
|---|---|---|
| Game version (1.10, 1.13c, 1.14d) | The mod's readme, its launcher, the version of `Game.exe`/`D2Game.dll` it ships or patches | Table layouts and function numbers differ per version |
| Does it ship an **engine DLL** or patch the game's DLLs? | Extra `.dll` files, a plugin loader, `.dll` hashes that differ from vanilla | Everything that DLL does must be re-done in a D2RLoader plugin or dropped |
| Which **extra tables** the DLL reads | Files in the mod's `data/global/excel` that vanilla does not have; strings the DLL opens | They hold rules D2R will never read |
| Which **function numbers** it adds | Values in `srvdofunc`, `cltdofunc`, `pSrvHitFunc`, `pSpell`, `func1`, cube `op`, `descfunc` above vanilla 1.13c's range | Each one is engine code to port ([engine-limits.md](engine-limits.md), "function numbers") |
| Which **events** it raises | Rows in `events.txt` after vanilla's | D2R raises only its own events (section 6) |
| Its row counts | Every table | D2R has a 32,768-row limit per table |
| Its art formats | `.dc6`, `.dcc`, `.cof`, `.dt1`, `.ds1` | Classic mode can use them; HD needs `.sprite` and models |
| Saves | Does it change the save format (more skills, wider stats, extra stash pages)? | D2RLoader's extended save format may be needed |

Triage the engine DLL by its source files: its assert strings usually name them (`..\SkillFoo.cpp`). Sort each into
(a) data only, (b) a D2RLoader service, (c) a native hook, (d) a UI rebuild, (e) cut, and keep the list.

## 2. The data pipeline

1. Extract the mod's archives (MPQ) and D2R's files (CASC; `python -m modkit fetch`) into folders that git ignores.
2. Decode the mod's tables to text if they are `.bin`; decode its formulas.
3. Merge them into D2R's layout table by table (section 3), into a build folder.
4. Validate every field against D2R's own data guide (`data/global/dataguide/`). Aim for 0 errors.
5. Install into `<game>/mods/<Mod>/` with the game closed; start through D2RLoader; read `d2rloader.log`.

Rebuild from scratch every time; never hand-edit the build output.

## 3. Table by table

### How rows merge

| Kind of table | Examples | Rule |
|---|---|---|
| **Indexed** (read by row number) | skills, states, itemstatcost, missiles, monstats, monstats2, overlay, properties, levels, objects, pettype, monai, itemtypes | Keep **D2R's own added rows at their numbers**; the mod's rows at its numbers where they don't clash; the mod's extra rows **after D2R's last row**. Every reference to a moved row must be remapped (section 4) |
| **Replace** (the mod's rows only) | weapons, armor, misc, treasureclassex, magicprefix/suffix, uniqueitems, setitems, sets, runes, cubemain | Merging D2R's vanilla rows back lets vanilla items drop through the automatic treasure classes |
| **D2R wins** | sounds, skillcalc, misscalc, playerclass, plrtype, storepage | D2R's rows first; the mod's extra codes appended |
| **Keyed** (the rest) | | The mod's rows, then D2R's rows whose key the mod lacks |

Anything that has no D2R column goes to an overflow report. Nothing is dropped silently.

### Column differences (1.13c -> D2R 3.x)

| Table | Classic | D2R | What to do |
|---|---|---|---|
| plrmode / monmode | Sequence mode spelled `GH` | `Code` is `SQ` (sequence), `KB` (knockback) | Skills' `anim`/`seqtrans` `GH` -> `SQ`; never change D2R's `Code` column |
| overlay | `Character` empty | `Character` = HD overlay folder | Fill it from D2R's matching row |
| missiles | `Range` + `LevRange` | `Range` is an expression | `Range` = `base+LevRange*lvl` |
| inventory | one row per page | two rows per page (two screen layouts) plus new pages | Set grid sizes on every matching row |
| skills | srvdofunc up to ~152 | 153-171 are D2R 3.x functions | Move the mod's 153+ functions to 172-190 (section 5) |
| skills | `leftskill` / `rightskill` often ignored by the mod's DLL | D2R honours them | Section 7 |
| misc | `pSpell` up to whatever the DLL handles | D2R's table has 20 entries | Map to D2R's spell or a plugin slot 16-19 |
| itemtypes | no Warlock rows | D2R 3.x added rows (`warl` at 67) | Keep D2R's rows at D2R's numbers (section 4) |
| All tables | `-1` sometimes stored as `4294967295` or `255` | signed | Compare every numeric column with D2R on shared rows to find decode slips (`coldeffect` 236 instead of -20) |
| Banks | one `excel/` | `excel/` (RotW) and `excel/base/` (classic/LoD) | Write both banks until you know which one the mod's characters load |

### Row limits

A big overhaul can have several times D2R's row counts in skills, missiles, monstats and cubemain and still stay
far under the 32,768-row limit. More than 511 states load with an assert and work under D2RLoader 1.3.1's wide-state
sync (see [engine-limits.md](engine-limits.md)). Key lengths are the real limit: see the dataguide `dataLength`
values in [engine-limits.md](engine-limits.md).

## 4. IDs: what may move and what may not

**D2R-only rows must stay at D2R's own numbers.** D2R.exe tests many IDs with numbers written into its code. If a
merge puts the mod's rows first and appends D2R's new rows at the end, every ID after the first D2R-only row shifts,
and the engine's built-in checks hit the wrong rows.

Worked example, itemtypes: D2R 3.x has the Warlock type `warl` at row 67. A classic mod has no row 67 of that kind,
so a naive merge numbers `h2h` as 67 instead of 68, `rune` as 74 instead of 75, and so on. D2R's code asks "is this
item of type 75 (rune)" in about ten places. With the shift, its rune test hits circlets: picking up or wearing any
circlet runs the socket-filler code and asserts `UnitGetType(hUnit) == UNIT_ITEM` (ItemMode.cpp:676) and the game
hangs or dies; socketed runes apply none of their gems.txt mods.

Item type ids the engine tests by number (build 93847, `0x373890(item, type)` call sites with an immediate >= 67):

| Id | Code | Id | Code |
|---|---|---|---|
| 68 | h2h | 81 | apot |
| 73 | pelt | 82 | wpot |
| 75 | rune | 83 | scha |
| 77 | hpot | 84 | mcha |
| 96 | gem4 | 58 | jewl (below 67, also tested) |

Rules:

- Count rows the way D2R does: every row except those whose key is `Expansion` (a blank-key row is a record).
- Put each D2R-only row at its D2R number; give the mod's rows the numbers around it. Then remap.
- **Remap every reference** after rows move: skill and monster parameters in item, affix, recipe and monprop
  properties (oskills, procs, charges); `reqskill`, `srcmissile`, sub-missiles; skill-desc rows; level ids in
  portals; monster ids in missiles (below). A mismatch gives the wrong skill, not an error.
- Missiles whose **server hit function is 6** spawn the monster row in `sHitPar1`. That parameter is a monstats ID
  and must go through the monstats remap too. If it doesn't, every hit spawns some other monster next to the
  player (in one port, an invisible dummy whose broken sound crashed the client).
- Add a self-check: at build time compare your ids against D2R's for every D2R row; at run time a plugin can check
  the same (the template's `item_type_check.cpp`).

## 5. Function numbers

D2R 3.x reused classic "free" slots for its own skills. Values that meant the mod's functions now run D2R's code.

| Column | D2R 3.3 uses | Free | Classic trap |
|---|---|---|---|
| skills `srvdofunc` | 1-171 | **172-190** | A classic DLL's 153-171 are D2R 3.x skills now. Put new server functions at **172+** |
| skills `srvdofunc` 109 | D2R's itemless Town Portal | | 1.13c's 109 (monster frenzy) was merged into 9 (Frenzy) |
| skills `srvstfunc` 64 | D2R's Bind Demon | | 1.13c's 64 (frenzy start) -> use 67 |
| skills `cltstfunc` | max 61 | | Classic 82 is past the end of D2R's table: use none |
| skills `cltdofunc` | 1-109 (96-109 are D2R 3.x skills) | **110-129** | Classic 96-99 run D2R's Volcano / Sigil / Ring of Fire client code |
| missiles `pSrvHitFunc` | 0-60 | 61-70 | |
| misc `pSpell` | -1..15 | 16-19 | 20+ does nothing |

Before writing code, look for a D2R function that does the same with other table values: an itemless town portal
is D2R's 109; a timed potion is `pSpell` 3; resurrect is Revive's function; a storm of missiles is Armageddon's.
Audit every row against D2R's function tables and fail the build on a classic number that D2R reassigned.

## 6. Events and behaviour D2R never raises

The mod's DLL may raise events that vanilla 1.13c also had in its table but never sent, or add its own rows to
`events.txt`. D2R raises only its own. A property listening to such an event is silently dead.

Worked example: **cast on death.** A classic engine DLL wrapped the unit death function and raised a new event
("the unit dies", a row it added to `events.txt`) on the victim before every death. Monster properties used it for
death novas and exploding summons. In D2R the rows load fine and nothing ever fires.

Port: hook D2R's unit death function (`0x44db20` in 93847, `(game, victim, killer or null, flag)`), and before the
original runs, raise the event through D2R's unit event dispatcher (`0x44d570`, `(game, event, unit, other, 0)`) on
the victim with the killer as the other unit. Read the event id from the stat's itemstatcost record so a renumbered
events.txt keeps working. Skip units already dying or dead (the death function itself returns at once for a
monster in mode 0 / 12 or a player in mode 0 / 17), or you raise it twice.

Others seen:

| Classic behaviour | D2R | Port |
|---|---|---|
| **Ranged pets**: the mod's summon code read a flag in one of the summon skill's `Param` columns and switched the pet's AI to its ranged branch | D2R's summon function has no such step: the pet melees | Plugin: after the pet is made, set its AI control field (observed in 93847: monster data `+0x30` -> `+0x44 = 1`, `+0x48 = 0` picks the NecroPet ranged branch) |
| Raven-AI pets: attack count = `Param1 + (lvl-1)*Param2` (a negative Param1 made a pet permanent) | D2R takes the count from the skill's **`calc3`** | Write the count formula into `calc3` (e.g. `ln12`), or the pet dies on its first think |
| The mod's skill picker ignored `leftskill` / `rightskill` | D2R honours them | Section 7 |
| Cube output "open a portal" for any level | D2R's cube executor (`0x5269c0`) opens only the Cow Level | Plugin hook on the output handler |
| A classic timed potion added its stat as is | D2R's timed potion (`pSpell` 3, `0x57ecb0`) treats a stat's calc as the **total over the duration**, except hitpoints, mana, manarecovery and hpregen; every other stat is stored as calc / len | Multiply the calc by the `len` formula for those stats |
| Quest kill handlers | D2R runs a quest's kill handler only while the quest is still open in that difficulty | Test with a character whose quest is open |

## 7. Skills that can't be selected

D2R's skill picker uses `leftskill` / `rightskill`; many classic DLLs did not.

- **Every active skill needs `rightskill 1`**, including the class-less **innate skills** that `charstats` gives
  every character (`Skill 1-10`, `StartSkill`). A `leftskill`-only skill with `TargetableOnly 1` turns a click on the
  ground into a walk, and Quick Cast does nothing without a monster under the cursor (as vanilla Leap Attack).
- D2R does not quick-cast a targeted melee skill (`range h2h`, `TargetableOnly 1`) with no target. That is vanilla,
  not a bug.
- More than 30 class skills per class needs D2RLoader's extended save format.

## 8. Strings, sounds, icons

**Strings.** Use a D2RLoader string namespace for the mod's own keys; put new text for keys D2R already has into
one override file. Never append new strings with numeric ids: D2R matches ids by their low 16 bits and they collide
(an NPC greets with a skill name). Keep D2R's printf placeholders per key (`sysmsg7-9` "was slain by" crashed the
client on death when the classic text had a different `%s` count). Details: [strings.md](strings.md).

**Sounds.** New sound rows are appended to `sounds.txt` and referenced by name. A name that does not resolve becomes
id -1, and several D2R client sound readers crash on an id outside the table instead of skipping it. Also, on the
start where D2RLoader builds its table cache, the game loads D2R's own sounds table, not the mod's
([d2rloader.md](d2rloader.md)). A plugin sound guard covers both (the template's `sound_guard.cpp`).

**Skill icons.** Classic class sheets store two frames per icon (normal, pressed). A **shared** icon sheet (attack,
throw, item skills) may store **one frame per icon**: read each sheet's real layout, don't assume pairs. HD icon
strips are capped at 16,384 px (62 icons at 130 px with pressed frames), see [engine-limits.md](engine-limits.md).

## 9. Graphics

- **HD first.** Item pictures need entries in `data/hd/items/items.json` / `uniques.json` / `sets.json` (a missing
  one crashed vendors); missiles need `missiles.json`; sequence skills need an HD state; overlays need `Character`.
  See [hd-graphics.md](hd-graphics.md).
- **Stand-ins**: reuse the closest vanilla HD model for each new monster and missile (a new cold skeleton mage gets
  the vanilla cold skeleton mage, scaled), the closest vanilla item picture of the same type, and recolour later.
- New HD models and textures at **new paths** need entries in the model and texture indexes
  (`data/hd/model_lod_desc.bin`, `data/hd/texture_desc_cache.json`), or they load but are never drawn.
- **Classic mode** still reads `.cof`/`.dcc`/`.dc6` and the `.dt1`/`.ds1` tiles even in HD (monster COFs for timing,
  tiles for areas). Ship every classic file the tables reference, or the game crashes on spawn or area entry.
- Upscaling classic DC6 bilinearly is an acceptable first HD picture for items and panels.

## 10. The unported list

Keep a table of what the original mod has and the port does not, with the reason. Example rows:

| Feature | Why not ported | What players see |
|---|---|---|
| A portal from a scroll to a level above 255 | D2R's portal setter clears the high byte | Plugin writes the high byte; until then the scroll does nothing |
| Item skill icons beyond 62 shared icons | HD strip limit | Those skills show a generic icon |
| Cross-act portals | D2R links portals within one act | Portal opens only in its own act |
| Classic-mode panel art | HD first | Classic panels are vanilla |
| A rule the original engine DLL implemented and no plugin does yet | Not ported yet | Name the rule and its workaround |

## 11. Testing a port

- Keep a testing table (change, how to test, expected result). The player or tester runs batches.
- Write in-game checks for what you fix, where the toolkit or your harness allows.
- Test with mortal characters too: a god-mode character skips the damage path where many crashes live.
- Reset the test character's quests between runs; finished quests skip their handlers.
- When a crash comes from a player, go to [crash-triage.md](crash-triage.md).
