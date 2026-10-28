# How D2R reads its tables

Tables live in `data/global/excel/` as tab-separated `.txt` (Latin-1 safe, CRLF) with compiled `.bin` twins. A mod
ships its tables inside its folder MPQ (`<game>/mods/<Mod>/<Mod>.mpq/data/global/excel/...`). The port compiled
them with D2RLoader (`d2rl compile <Mod>` in the developer console). D2RLoader 1.3.1 compiles a mod's `.txt` tables
itself at start-up ("Compiled N tables from TXT"), so `-txt` is not needed; shipping any `.bin` makes D2RLoader treat
the mod as a "custom BIN set" that must be complete. On its cache-building first start it loads D2R's own sounds
table, not the mod's ([d2rloader.md](d2rloader.md)).

## Two banks: `excel/` and `excel/base/`

Since 3.0 (dataguide `docs/game-mode-split.html`):

| Folder | Game mode | 93847 |
|---|---|---|
| `data/global/excel/base/` | Classic and Lord of Destruction | 177 tables |
| `data/global/excel/` | Reign of the Warlock (RotW) | 184 tables |

- No inheritance between the two: each mode reads only its own folder.
- Exceptions shared by all modes (always read from `excel/`): `sounds.txt`, `soundenviron.txt`.
- **Indices must match across modes** (a character upgraded from LoD to RotW keeps its row numbers).
- An overhaul should write **both banks** with identical row IDs until it knows which mode its characters use.
- Bank differences in 93847 (records): skills 429 (RotW) vs 427 (base); missiles 742 vs 736; skilldesc 263 vs
  232; levels 138 vs 137; overlay 334 vs 333; skillcalc 127 vs 121 rows; srvdofunc max 171 vs 152; cltdofunc max
  109 vs 97. Both banks have the Warlock class (30 skills) in skills.txt.

## The schema source: the dataguide

`data/global/dataguide/` (shipped in CASC) is Blizzard's own documentation: `data/files/<table>.js` per table
(`files["skills"] = {...}` JSON after the `= `). Per field: `name`, `description`, `type`:

| type | meaning |
|---|---|
| `text` | key or free text; `dataLength` = maximum characters |
| `reference` | points at `file`.`field` (validate the target exists) |
| `string` | a string key (data/local/lng/strings) |
| `int` / `boolean` | `memSize` = bit width (8, 16, 32): values beyond it wrap |
| `parse` | BBE formula (scope by table) or property code/parameter |
| `comment` | ignored by the game |

Plus `enums.js` (enumerations), `docs/bbe-calc.html` (formula syntax, functions, `cond()` conditions),
`docs/game-mode-split.html`, `docs/damage.html`, `docs/genericpetai.html`, `docs/itemratio-calc.html`. Build a
validator from these files: it types every cell, checks references, key lengths and enum values.

## Indexed vs keyed tables

| Kind | Tables | Identity |
|---|---|---|
| **Indexed** (the engine and saves use the row number) | skills, states, itemstatcost, missiles, monstats, monstats2, overlay, properties, levels, objects, monai, pettype, monprop, skilldesc (by key only, see below), superuniques, ... | Row number = ID. Reordering changes every reference |
| **Keyed / content** (referenced by key or code) | weapons, armor, misc (by `code`), itemtypes (`Code`), uniqueitems/setitems/sets (`index`), runes, magicprefix/magicsuffix/automagic, treasureclassex, cubemain, gamble, monsounds, ... | Key; row order mostly irrelevant, but **uniqueitems/setitems/runes row indexes are stored in saves**: keep existing rows in place. **itemtypes is keyed but D2R's code tests type numbers** (75 rune, 77 hpot, 68 h2h, ...): keep D2R's rows at D2R's numbers ([engine-limits.md](engine-limits.md)) |

- Rows counted: **every row except rows whose first cell is `Expansion`**. Blank-key rows are real records
  (monsounds and montype start with one). D2R's own `*Id`/`*ID`/`*hcIdx` comment columns confirm the count.
- `skilldesc` was re-sorted alphabetically by D2R and is referenced by name only.
- Columns starting with `*` are comments (`*ID`, `*eol`, `*ItemName`...). Keep them but the game ignores them.
- `*eol` ends some tables: keep it last.

## Rules for adding rows safely

1. **Never reorder or delete D2R rows** in indexed tables; D2R code refers to its own additions by number (states
   `cooldown`, `sharedstash` 186, `hidedead`; skills `Interact`, `Loot`, `TownPortal`; itemstatcost
   `skill_cooldown`, `skill_missile_damage_scale`, `psychicward`; missiles `recklessattacksmissile`, ...).
2. **Append** new rows after D2R's last row. 93847 record counts (not counting `Expansion` rows), RotW / base:
   skills 429 (last ID 428) / 427, states 232, itemstatcost 368, properties 284, missiles 742 / 736, monstats 751,
   monstats2 623, overlay 334 / 333, objects 587, levels 138 / 137, skilldesc 263 / 232, superuniques 66,
   lvltypes 36. New rows start after the larger bank's count so both banks share the IDs.
3. To repurpose a D2R row, edit it in place and keep its key unless nothing references the key.
4. Keep both banks' IDs identical.
5. Every reference column must name an existing row (validator). Remap references when IDs move.
6. Write signed values signed (`-1`, not `4294967295`); write booleans as 0/1.
7. Keep each key within its `dataLength` (31 missiles/objects Name, 39 lvlwarp, 47 most keys).
8. Formulas: 255 chars max, 32-byte quoted names, no bare negative literals (`(0-N)`), bracketed ternary
   branches.

## D2R-only columns worth knowing

| Table | Column | Meaning |
|---|---|---|
| overlay | `Character` | HD overlay folder: `data/hd/overlays/<Character>/<overlay>.json` (`common`, `paladin`, `sorceress`, `druid`, `warlock`, `monster`, `assassin`, `amazon`, `necro`, `barbarian`, `object(s)`, `npcs`, `item`). Empty = no HD effect |
| skills | `Param9`-`Param14`, cooldown/behaviour flags, `calc5`-`calc10` (free formula slots a plugin can read) | |
| monstats | `TreasureClass`, `TreasureClassChamp`, `TreasureClassUnique`, `TreasureClassQuest` (+ (N)/(H)), Desecrate / Herald columns | Classic `TreasureClass1..4` renamed |
| levels | `MonLvl`, `MonLvl(N)`, `MonLvl(H)`, `MonLvlEx...` | Classic `MonLvl1..3` renamed |
| itemtypes | `MaxSockets1..3` + `MaxSocketsLevelThreshold1/2`, `UICategory`, `RunewordCategory1/2` | |
| hireling | `ResistFire/Cold/Lightning/Poison` (+ `/Lvl`), `HireDesc` (a string key) | |
| uniqueitems / setitems / runes / treasureclassex | `firstLadderSeason`, `lastLadderSeason`, `DropConditionCalc` / `ConditionCalc`, `disableChronicle`, `diablocloneweight` | |
| armor / weapons / misc | Anya-reward, advanced-stash, quiver columns, `ShowLevel`, `DropConditionCalc` | |
| charstats | `HealthPotionPercent`, `ManaPotionPercent`, `SkillsPerLevel`, `StrSkillTab1-3` | |
| difficultylevels | mercenary / pet / prime-evil damage rules, gamble rates, `ResistFloor` | |
| inventory | Warlock rows; two rows per page | see below |
| experience | `Warlock` column | |

Classic renames to remember: `Hralti*` -> `Hratli*`, `Drehya*` -> `Anya*`, `NoShldBlock` -> `ShieldBlockOverride`,
skills `delay` -> `localdelay`, uniqueitems `enabled` -> `spawnable`, missiles `LevRange` merged into `Range`.

## Per-table cheat sheet for overhaul work

| Table | Key / ID | What to watch |
|---|---|---|
| **skills** | `skill` (47), row = ID | `charclass` (30 per class in retail), `skilldesc`, `srvstfunc`/`srvdofunc`/`srvstopfunc`, `cltstfunc`/`cltdofunc`/`cltstopfunc`, `anim`/`seqtrans` (SQ for sequences), `seqnum`, `aurastate`/`passivestate`/`auratargetstate`, `aurastat1-6` + `aurastatcalc1-6`, `aurarangecalc`, `aurafilter`, `calc1-10` (Raven-AI pets read their attack count from `calc3`), `Param1-8` (+9-14), `srvmissile(a/b/c)`, `cltmissile(a/b/c)`, `summon`/`pettype`/`summode`/`petmax`, `mana`/`lvlmana`/`minmana`/`manashift`, `localdelay`, `reqlevel`, `reqskill1-3`, `skpoints` (cost formula; 999 = cannot learn), `leftskill`/`rightskill` (D2R's picker honours them: `rightskill 1` on every active skill, innate ones too), `InTown`. D2R function maxima: srvdo 171, cltdo 109, srvst 70, cltst 61 |
| **skilldesc** | `skilldesc` | `SkillPage` (tab), `SkillRow`, `SkillColumn`, `ListRow`, `IconCel` (frame in the class icon strip: 2 frames per icon, normal + pressed; class-less skills read one shared sheet, and a classic mod's shared sheet may hold one frame per icon), `str name/short/long/alt`, `descline*`/`dsc2line*`/`dsc3line*` + text a/b + calc a/b. A line type whose text is missing asserts |
| **missiles** | `Missile` (31), row = ID | `pCltDoFunc`/`pCltHitFunc`/`pSrvDoFunc`/`pSrvHitFunc` (free: srv hit 61-70, srv do 46-54), `Param1-5`, `CltParam1-5`, `sHitPar1-3`, `cHitPar1-3`, `SrvCalc1`, `CltCalc1`, `SHitCalc1`, `CHitCalc1`, `DmgCalc1`, `Range` (expression; classic `LevRange` merged in). **Server hit 6 spawns the monstats row in `sHitPar1`**: remap it when monstats rows move, `CelFile` (classic only), `SubMissile1-3`, `HitSubMissile1-4`, `Skill`. HD look comes from `hd/missiles/missiles.json` by name |
| **states** | `state` (47), row = ID | `overlay1-4`, `castoverlay`, `removerlay`, `pgsvoverlay`, `stat`, `setfunc`/`remfunc`, `missile`, `skill`, `colorshift`, `nosend` (not synced to clients). Above 511 states D2R asserts at load; D2RLoader 1.3.1 syncs wide states |
| **overlay** | `overlay` (47), row = ID | `Filename` (classic DCC), **`Character`** (HD folder), `Trans`, `AnimRate`, `Height1-4` |
| **monstats** | `Id` (47), row = ID | `BaseId`/`NextInClass`, `MonStatsEx` (monstats2 row; blank = row 0), `MonType`, `AI`, `Code` (classic animation set, also used in HD for timing), `Skill1-8` + `Sk1mode-Sk8mode` (a **monmode code or a monseq name**), `TreasureClass*`, `coldeffect` (signed), `spawnx/spawny` (signed), per-difficulty columns `(N)`/`(H)` |
| **monstats2** | `Id`, row = ID | visual columns: sizes, `HitClass`, part variants, modes `m*`/directions `d*`, `isSel`/`isAtt` (hover/target), `noGfxHitTest`, `automapCel`, `Light`, `Utrans` |
| **armor / weapons / misc** | `code` (4 chars) | `type`/`type2` (itemtypes), `namestr` (string key), `invfile`/`uniqueinvfile`/`setinvfile`, `invwidth`/`invheight`, `normcode`/`ubercode`/`ultracode` (armor/weapons tiers), `level`/`levelreq`, `spawnable`, vendor `*Min/*Max` columns, misc `pSpell` (0-19), `spellicon` (signed, -1 none), `stat1-3`/`calc1-3` (timed potion `pSpell` 3: calc is the total over `len` for hp / mana / manarecovery / hpregen, a per-frame value for other stats), `state`/`cstate1-2`, `len`. HD picture: `hd/items/items.json` by code |
| **itemtypes** | `Code` | `Equiv1/2` (parent types: the type tree drives affixes, auto TCs, sockets), `BodyLoc1/2`, `MaxSockets1-3` + thresholds, `TreasureClass` (auto `weapN`/`armoN`), `StorePage`, `Class`, `VarInvGfx`/`InvGfx1-6` |
| **uniqueitems** | `index` | `code` (base item), `lvl`, `lvl req`, `rarity`, `spawnable`, `nolimit`, `prop1-12` + `par`/`min`/`max`, `invfile`. HD picture: `hd/items/uniques.json` key = index lowercased, spaces/hyphens `_`, apostrophes dropped |
| **setitems / sets** | `index` | setitems `set` (sets.index), `item`, `prop1-9`, `aprop1a..5b` (partial bonuses); sets `PCode2a..5b` (2-5 items worn), `FCode1-8` (full set). **Max 6 items per set** |
| **runes** | `Name` (63) | `complete`, `itype1-6`/`etype1-3`, `Rune1-6`, `T1Code1-7` (**7 property slots**) |
| **magicprefix / magicsuffix / automagic** | row (affix ID saved in items) | `spawnable`, `rare`, `level`/`maxlevel`/`levelreq`, `classspecific`/`class`, `frequency`, `group` (exclusive group), `mod1-3code/param/min/max`, `itype1-7`, `etype1-5`. automagic = class-specific implicit mods. Affix tables start with a blank record row |
| **properties** | `code` (47), row = ID | `func1-7` (D2R uses 1-25, 36), `stat1-7` (itemstatcost), `set`/`val`. Items reference properties by code |
| **itemstatcost** | `Stat` (47), row = ID | `Signed`, `Send Bits`/`Send Param Bits` (network), `Saved`, `CSvBits`/`CSvParam` (character save width), `Save Bits`/`Save Add`/`Save Param Bits` (item save width), `Encode` (a property with function 28, "layer = param", on a stat with no `Save Param Bits` makes D2R's item writer fail: the client crashes when the item drops), `op`/`op param`/`op base`/`op stat1-3` (derived stats), `descpriority`/`descfunc` (D2R uses 5-29)/`descval`/`descstrpos`/`descstrneg`/`descstr2`, `dgrp*`. **Widths change the save format**: decide them before players have saves |
| **treasureclassex** | `Treasure Class` | `group`, `level`, `Picks`, `Unique/Set/Rare/Magic` ratios, `NoDrop`, `Item1-10` + `Prob1-10` (items by code; quoted entries with modifiers like `"gld,mul=2560"`), `ConditionCalc`. Auto classes `weapN`/`armoN` pull every spawnable base of that level band: remove vanilla bases you don't want dropping |
| **levels** | `Name`, `Id` (row = ID) | `Act`, `LevelType` (lvltypes row, **0-35 only for automap**), `DrlgType`, `SubType`, `Teleport` (0 no, 1 yes, 2 only with a clear line), `Vis0-7`/`Warp0-7`, `MonLvl*`, `MonDen*`, `mon1-25`/`nmon1-25`/`umon*`, `Waypoint`, `Quest`, `Portal`, `Layer` (**< 100**: index into the 100-bucket per-character map memory), `Position` (1 = arrive in a random entry room of the maze preset: **clear it on new maze levels** that have none, or the game crashes on entry). Level portal links are bytes in many places: keep IDs <= 255 |
| **lvlprest / lvltypes / lvlwarp / lvlmaze / lvlsub** | lvlprest `Def`, lvltypes `Name` (36 types + `Expansion` row) | lvlprest `File1-6` (.ds1 maps), lvltypes `File 1-32` (.dt1 tiles). Ship every tile file referenced. .ds1 maps hold object IDs by number |
| **automap** | (`LevelName`, `TileName`, `Style`, `StartSequence`, `EndSequence`, `Cel1-4`) | `LevelName` must be one of the 36 lvltypes names in a reformatted form ("Act 1 - Town" -> "1 Town"); others assert `ii < NUM_LEVEL_TYPES`. `Cel1-4` **<= 1498** (the atlas has 1,499 cels; a higher cel stored in a character's map memory hangs the next save) |
| **cubemain** | none (comment `description`) | `enabled`, `min diff`, `version`, `op`/`param`/`value` (player conditions 1-14 checked, item ops 15+; unknown ops pass), `class`, `numinputs`, `input 1-7`, `output`/`output b`/`output c` + `mod 1-5`. Record 0x16c bytes in memory |
| **charstats** | `class` | base stats, `LifePerLevel`/`ManaPerLevel`/`StaminaPerLevel` (quarter points), `LifePerVitality`, `ManaPerMagic`, `StatPerLevel`, `SkillsPerLevel`, `StartSkill`, `Skill 1-10`, `StrAllSkills`/`StrSkillTab1-3`/`StrClassOnly` (string keys), starting `item1-10` |
| **hireling** | `Hireling` + `Version` + `Difficulty` + `Level` rows | `Class` (monstats row), `Act`, `Skill1-6` + `Mode/Chance/Level`, stat growth, resist columns, `HireDesc` (string key). D2R repeats rows per level: key-merging re-adds vanilla mercenaries |
| **inventory** | `class` (comment), row order fixed | `invLeft/Right/Top/Bottom`, `gridX`/`gridY`, `gridLeft/Right/Top/Bottom`, `gridBoxWidth/Height`, then body slot rectangles. **Two rows per page** (`Amazon` + `Amazon2`, `Big Bank Page 1` + `Big Bank Page2`, ...). Pages: 8 classes, Monster, Trade Page 1/2, Bank Page 1 (classic stash), Transmogrify Box (cube), Big Bank (expansion stash), Hireling |
| **experience** | `Level` | first row `MaxLvl`; row L = experience at which level L+1 starts; one column per class incl. Warlock; `ExpRatio` |
| **difficultylevels** | `Name` (3 rows) | resist penalties, `DeathExpPenalty`, damage-vs rules, gamble rates, `ResistFloor` |
| **plrmode / monmode** | row = mode index | Keep D2R's `Code` column exactly (Sequence `SQ`, Knockback `KB`; the `Token` column is `GH` for both) |
| **skillcalc / misscalc** | row = code number | D2R's rows first in D2R's order; append new codes (they need a plugin to evaluate) |

## Mod folder layout under D2RLoader (as the port installed it)

```
<game>/mods/<Mod>/
  <Mod>.mpq/modinfo.json            {"name": "<Mod>", "savepath": "<Mod>/"}   (own save folder)
  <Mod>.mpq/data/...                folder-style MPQ: global/excel, local/lng, hd/..., global/dataversionbuild.txt
  modinfo.json                      copy (D2RLoader guide layout)
  d2rloader/metadata.json           {"schemaVersion": 1, "metadata": {...}}  (no "namespace": native strings)
  d2rloader/config/d2rloader.toml   [d2rcore.save_format] mode = "extended"   (wider IDs in saves)
  d2rloader/plugins/<plugin>.dll    mod-scoped plugins
```

Install only while the game is closed; restart D2R fully after an install.
