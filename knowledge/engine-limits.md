# Engine limits and fixed behaviour (D2R 3.3.0 build 93847)

Each entry: the rule, what happens when a mod breaks it, and the fix. "Plugin" means a D2RLoader native plugin
(see [engine-re.md](engine-re.md)); RVAs are build 93847 only ([d2r-93847-addresses.md](d2r-93847-addresses.md)).

## Table sizes and IDs

| Rule | Symptom when broken | Fix |
|---|---|---|
| **32,768 rows per table** (D2R limit). A large overhaul port stayed well under it | Load failure / assert | Stay under it; split content |
| **Indexed tables are addressed by row number** (skills, states, itemstatcost, missiles, monstats, monstats2, overlay, properties, levels, objects, monai, pettype, monprop, ...) | Reordering or inserting rows silently changes what every reference, save and engine constant means | Never reorder; keep D2R's rows at their IDs, **append** new rows after D2R's last row. D2R's own code depends on its added rows (e.g. states `cooldown`/`sharedstash`, skills `Interact`/`Loot`/`TownPortal`, itemstatcost `skill_cooldown`) |
| **D2R tests some row numbers in its code**: item types by number (`0x373890(item, type)` with 58 jewl, 68 h2h, 73 pelt, 75 rune, 77 hpot, 81 apot, 82 wpot, 83 scha, 84 mcha, 96 gem4 written into the code). D2R 3.x has `warl` at itemtypes row 67 that classic tables lack | A merge that appends D2R's new rows shifts every later id: D2R's rune test hits circlets (assert `UnitGetType(hUnit) == UNIT_ITEM` at ItemMode.cpp:676 on picking one up, socketed runes apply no mods) | Keep D2R-only rows at D2R's numbers ([porting-classic-mods.md](porting-classic-mods.md) section 4); self-check the ids at build and at plugin load |
| D2R reused classic "unused" slots (itemstatcost 183-213 now hold D2R stats such as `lasthitreactframe`, pierce immunities, passive masteries) | A mod that also used those slots collides with D2R stats | Put mod stats after D2R's last stat (368 rows in 93847) |
| **Blank-key rows are records**; only rows whose key is `Expansion` are skipped (monsounds, montype and the affix tables start with a blank record) | Every ID after the blank row shifts by one (wrong monster sounds, wrong affixes) | Count rows exactly as D2R: every row except `Expansion` |
| **Key length limits** (dataguide `dataLength`): 31 for `missiles.Missile` and `objects.Name`, 39 for `lvlwarp.Name`, 47 for most keys (skills, states, overlay, monstats Id, itemstatcost Stat, properties code, sets index, levels Name), 63 objects Class / runes Name, 64 overlay Filename | Load assert / truncated name, broken references | Validate every key against the dataguide (`data/global/dataguide/data/files/*.js`) |
| **Field widths** (dataguide `memSize`: 8, 16, 32 bits). E.g. cubemain `op` 8, missiles `pSrvHitFunc` 16, misc `pSpell` 32, itemstatcost `descfunc` 8 | Value wraps silently | Check `memSize` before using large values |
| Signed fields: monstats `coldeffect` (-20 = 20% slower), `spawnx/spawny`, misc `spellicon` (-1 = no icon) are signed bytes; `-1` in 32-bit fields must be written `-1`, not `4294967295` | A chill speeds monsters up by 236% (coldeffect 236), wrong offsets, a spell cursor where none was meant | Write signed values as signed; only truly unsigned fields (experience) may exceed 2^31 |

## Skills

| Rule | Symptom when broken | Fix |
|---|---|---|
| **Exactly 30 class skills per class** in retail (ama sor nec pal bar dru ass war: 30 each in skills.txt, both banks). The save's class-skill section ("if") stores one byte per class skill | More than 30 class skills: retail saves cannot store them | Enable D2RLoader's extended save format (`[d2rcore.save_format] mode = "extended"`): an overhaul ran several times 30 class skills per class with it. Without it stay at 30 |
| **srvdofunc table has 191 slots** (RVA `0x238ea00`, dispatcher `0x43acb0` rejects > 0xbe). D2R's data uses up to **171**; **172-190 are free** | A value with no function does nothing; a value D2R already uses runs D2R's function (classic mods' 153-171 collide with D2R 3.x skills) | New server do functions go in 172-190, written by a plugin into the table (expected value 0) |
| **srvstfunc**: D2R data max 70 (table `0x238e710`, size not confirmed) | Unknown values: no start behaviour | Reuse an existing start function or hook |
| **cltdofunc table: 130 slots** (`0x235f7d0`, bound at `0x235f7c4`, dispatcher `0x216d20`). D2R data max **109** (3.x uses 96-109 for its own skills); **110-129 free** | Classic mods' 96-99 run D2R's Volcano / Sigil / Ring of Fire client code | Move client functions to 110-129 via plugin |
| cltstfunc: D2R data max 61 (RotW bank), 55 (base bank) | | Reuse or plugin |
| Classic numbers D2R reassigned: srvdofunc **109** is D2R's itemless Town Portal (1.13c's monster frenzy 109 merged into Frenzy's 9); srvstfunc **64** is Bind Demon's (1.13c frenzy start; use 67); cltstfunc **82** is past the end of D2R's client start table | A monster frenzy skill opens a town portal | Audit every row's function columns against D2R's tables at build time |
| Raven-AI pets: D2R takes the pet's attack count from the summon skill's **`calc3`** (1.13c: `Param1 + (lvl-1)*Param2`) | Empty calc3: the pet is killed on its first think, about 2 s after the cast | Put the count formula in `calc3` |
| D2R's skill picker honours `leftskill` / `rightskill`; a `leftskill`-only skill with `TargetableOnly 1` turns a ground click into a walk, and Quick Cast does not cast a targeted melee skill (`range h2h`) without a target | Innate and class-less skills can't be put on a button or never cast | `rightskill 1` on every active skill, including charstats' innate `Skill 1-10` / `StartSkill` rows |
| **Stop functions** (`srvstopfunc` / `cltstopfunc`): only value **1** exists, used by Whirlwind (srvdo 76 / cltdo 45) and Talic's Whirlwind (srvdo 168) | A new row on the Whirlwind do functions without stop 1 never ends its spin/aurastate cleanly on release or interrupt | Copy `srvstopfunc 1` / `cltstopfunc 1` to every row using a do function D2R pairs with a stop function |
| **Sequence skills** need `anim`/`seqtrans` = **SQ** and a `seqnum` (monseq group) | See mode codes below | |
| **Mode codes**: plrmode/monmode `Token` for Sequence and Knockback is `GH` (same as Get Hit), but the `Code` column the engine resolves is **SQ** (sequence) and **KB** (knockback). Skills reference the Code | `anim GH` plays get-hit/knockback frames and the server never starts the skill; if plrmode/monmode `Code` is changed to GH, SQ resolves to mode -1 and the animation lookup (`0x3b6700`) crashes on first use | Always keep D2R's plrmode/monmode `Code` column; use SQ/KB in skills |
| Skill tooltip line types (skilldesc `descline`/`dsc2line`/`dsc3line`): D2R uses its own types up to 74-79. The line renderer (`0x287a50`, D2Client UI/SkillDesc.cpp) asserts `stringIndex1 != strdummy` when a type's text A/B is missing | Assert when hovering a skill | Give every line the texts its type needs; don't invent type numbers |
| Skill tree: 6 rows x 3 columns per tab (`skillRow`/`skillColumn` in skillstreepanelhd.json); D2R's panel fills tab titles for 3 tabs (`textTab0-2`) | Extra tabs have no title text | More than 3 tabs works in HD (8 TabWidgets with `ActivateTab:0..7` confirmed); titles must be drawn in art or a label of your own ([ui-layouts.md](ui-layouts.md)) |
| Skill icons: one HD strip per class, max 16,384 px wide; at D2R's 130 px icon (132 stride) that is **62 icons** (normal + pressed frames) | Bigger strip fails to load or icons shrink | Keep <= 62 icons per class strip or use smaller icons ([hd-graphics.md](hd-graphics.md)) |

## Calc codes and formulas

| Rule | Symptom when broken | Fix |
|---|---|---|
| **skillcalc.txt / misscalc.txt order is fixed**: the engine evaluates a code by row number in a switch (`0x33b3b0` skills, `0x3b9dc0` missiles). D2R has 127 skillcalc rows (RotW; 121 base) and 47 misscalc rows | Reordered rows: every formula reads the wrong variable | Keep D2R's rows; append new codes after them |
| A new calc code compiles (formulas parse) but the switch answers unknown rows with **0** | Tooltips/values show 0 | Plugin: hook the switch, answer your codes, pass the rest on. Keep code numbers < 256 (the port's hook masks the code to a byte; unverified whether the engine allows more) |
| **Formula text limits**: 255 characters per formula, 32 bytes per quoted name (`skill('Name'.blvl)`), **no bare negative literal** (write `(0-5)`), ternary branches in brackets (`(a)?(b):(c)`) | Load-time assert, formula evaluates to 0 | Shorten names referenced from formulas; generate formulas through a checker |
| Formula functions are fixed: `min max rand skill miss stat sklvl sksrc cond`. `cond()` conditions: IsType, IsClass, Desecrated, Difficulty, MonsterTestElite, ItemIsType, ItemIsModType, MonsterHasMod. **No "has state"** test | Can't branch on a state in data | Plugin: e.g. a calc code answered by the plugin, called through `skill('<proxy skill>'.<code>)` on a hidden proxy skill whose Params hold the arguments |
| Scopes: skill formulas use skillcalc identifiers, missile formulas misscalc, monster moncalc; item and treasure-class formulas take **no identifiers** | Parse error | Use `stat()`/`skill()` functions instead |
| missiles: D2R merged classic `LevRange` into `Range` (an expression) | Range ignores level | `Range` = `base+LevRange*lvl` |

## States

| Rule | Symptom when broken | Fix |
|---|---|---|
| D2R's data loader asserts above **511 states** (`MAX_STATES`, `size <= 0x1ff` in `0x3074d0`), then continues with `(count+31)>>5` blocks | Startup asserts in `d2rloader.log` (harmless) | Accept the assert |
| Each unit keeps state bits inline in its extended stat list: current states at **+0xaf0**, "changed" flags at +0xaf0 + blocks*4, room for 16 + 16 dwords. Retail network sync sends **512 states per unit** | With > 512 states, retail sync drops states above 511 | **D2RLoader 1.3.1 already handles wide states**: D2RCore replaces D2R's senders (`0x53a210` changed, `0x539890` full) with `SendChangedWideStateStats` / `SendAllWideStateStats` (client `ReceiveWideStateStatPacket`), reading the arrays at D2R's own offsets with the live block count. Up to 32 blocks (1,024 states) the "changed" flags overlap +0xb70..+0xbf0, which D2R never reads (only copied from a zeroed template) |
| **Never patch the state arrays yourself.** Moving them (enlarging the stat-list pool, redirecting accessors) desynchronises D2RLoader's wide-state sync | No state reaches the client: monsters can't be hovered or attacked (alignment state missing), no Whirlwind spin overlay, buffs invisible. Patching the stat-list copy routine `0x2f0d80` also gave new units random states and hung games | Leave the layout to D2RLoader. Above 1,024 states: unexplored |

## Levels and areas

| Rule | Symptom when broken | Fix |
|---|---|---|
| **Level types are fixed at 36** for the automap. LvlTypes.txt itself loads into a vector (more rows load and their tiles work), but AutoMap.txt rows are matched against 36 built-in names (`0x32cae0`, table `0x1cfe110`): unmatched rows assert `ii < NUM_LEVEL_TYPES` and become type 0. The automap tile lookup `0x32bdf0` indexes a 36-entry array unchecked | Startup asserts; no automap (or reads past the array) in areas of type >= 36 | Plugin: hook `0x32bdf0`, answer -1 for types >= 36 or search the extra AutoMap rows yourself (they stay in the loader's row vector `0x2a9a8c8` in file order). Dataguide: "level types are static defined values that cannot be added" |
| Portal destination level: object data stores 16 bits (+0x08 low, +0x78 high; getter `0x34ad40`), but the common byte setter `0x34e9d0` clears the high byte | Portals to levels > 255 lead to the wrong level | Write the high byte after the setter (plugin); keep levels <= 255 where possible. Client packets carrying a byte level are unverified |
| **Automap cels: 1,499** in D2R's atlas (its own rows end at 1498). The per-character map memory loader rejects a stored cel >= 1499 by writing the node's own offset into its `next` field | The next save walks that self-loop for ever: the client hangs after a few area changes (log repeats `Reading From: <char>.ma0`) | Clamp automap.txt cels above 1498 to -1 |
| The per-character map memory (`<char>.map`, `.ma0`-`.ma3`) is a **100-bucket table indexed by levels.txt `Layer`** directly | `Layer` >= 100: out-of-bounds read/write on the stack | Keep `Layer` < 100, or a plugin that keeps its own map memory for higher layers |
| levels.txt **`Position 1`** makes the level-entry finder pick a random entry room from the maze's preset list | A maze level with `Position 1` and no entry rooms (a new maze D2R doesn't have): null read entering the level, the game closes without a report | Clear `Position` on new maze levels (`DrlgType` 1); arrival then comes from the warp tile |
| D2R's portal routines link levels **within the same act** only (`0x3e2b5c8` level -> act) | Cross-act portal refused | Design portals per act |
| Classic map tiles (`.dt1`/`.ds1` under `data/global/tiles`) are loaded even in HD | Black screen / crash on entering an area whose tile file is missing | Ship every tile a level type references; `.ds1` reference objects by number (renumbering objects breaks them) |

## Items, inventory, sets, runes, cube

| Rule | Symptom when broken | Fix |
|---|---|---|
| **inventory.txt has two rows per page** (one per screen-resolution layout: `Amazon` and `Amazon2`, `Big Bank Page 1` and `Big Bank Page2`, ...), plus Monster, Trade Page 1/2, Bank Page 1 (classic stash), Transmogrify Box (cube), Big Bank (expansion stash), Hireling, and a Warlock row | Grid size changes on one resolution only | Set `gridX`/`gridY` on **every** matching row; then the HD layout's `cellCount` to the same size ([ui-layouts.md](ui-layouts.md)) |
| Retail sizes: inventory 10 x 4, cube 3 x 4, expansion stash 10 x 10 | | |
| **Set items per set: 6** (validator limit used by the port) | More than 6 items in one set: unsupported | Split into two sets or plugin |
| **Runewords: 7 property slots** (runes.txt `T1Code1-7`) | Extra properties can't be expressed | Fewer properties, a hidden auto-mod, or plugin |
| Set item `aprop1a..5b` (5 partial bonus pairs), sets `PCode2a..5b` + `FCode1-8` | | |
| **Item spells (misc `pSpell`)**: D2R's table `0x236fe30` has **20 entries** {check, use, flag}; D2R data uses -1..15; **16-19 are empty**; **20+ does nothing** | Right-click on the item does nothing (e.g. a classic mod's own spell number above 19) | Map to a D2R spell with the same behaviour (timed potion = 3), or a plugin handler in 16-19 (hook dispatchers `0x5817f0` check / `0x581680` use). The entry's flag 1 = item is **not** used up; client and server must agree (`0x308e80`) or the server refuses the use |
| **Cube recipe conditions** (cubemain `op`/`param`/`value`): the recipe search `0x529090` checks **player** condition ops **1-14** only (1/2 date, 3-14 player stat compares); any other op passes. The input matcher `0x529d50` checks **item** ops **15+** on each input. D2R's own data uses only op 28 | A new op is silently ignored (recipe always allowed) | Plugin: hook `0x529090`/`0x529d50`; records are 0x16c bytes at data tables +0x1570 (count +0x1578), getter `0x3e3f60` |
| Timed potions (`pSpell` 3, handler `0x57ecb0`) treat a stat's calc as the **total over the duration** for hitpoints, mana, manarecovery and hpregen (scaled `<< 8`); every other stat is stored as calc / len | A classic potion of "+100 mana recovery for 7 s" gives 0 | Multiply those stats' calc by the row's `len` formula |
| Cube output "portal" (output byte 1, cube executor `0x5269c0`) opens **only the Cow Level** | Every portal recipe opens the Cow Level | Plugin hook on the output handler |
| **Property function 28 (stat layer = param) needs `Save Param Bits`** on the stat. 1.13c's item writer ignored a layer it had no bits for; D2R's asserts (`Items.cpp` `ptItemStats`) and writes nothing | The item packet is an empty header; the client's item packet handler reads null (`BC_ASSERT: hUnit`) and crashes the moment such an item drops | Clear the parameter of function-28 properties whose stat has no Save Param Bits (when only the value is read), or give the stat param bits ([saves.md](saves.md)) |
| HD item pictures: every item code, unique and set item needs an entry in `data/hd/items/items.json` / `uniques.json` / `sets.json` | Vendors/Item Spawner spam `File not found "data/hd/global/ui/items/weapon/[default_unit]"`, then crash (access violation at D2R+0x29538e) | Generate entries for every row; point at a real sprite ([hd-graphics.md](hd-graphics.md)) |

## Missiles, stats, properties (function numbers)

| Function column | D2R data range (93847) | Table / bound | Free slots |
|---|---|---|---|
| missiles `pSrvHitFunc` | 0-60 | `0x2391030`, bound 71 (`0x2391404`), dispatcher `0x464ac0` | **61-70** |
| missiles `pCltHitFunc` | 0-65 (6, 7 and others unused) | `0x2358710`, bound 81 (`0x2358a64`) | 66-80, plus unused low slots (6 and 7 confirmed empty) |
| missiles `pSrvDoFunc` | 1-45 | `0x2390e80`, **no bound check** (dispatcher `0x466ce0`) | **46-54** empty (table end not confirmed) |
| missiles `pCltDoFunc` | 1-77 | `0x2358430` (port notes say 77 slots, but D2R's `heraldspawner` uses 77: verify the size); called from `0x1ba040` | 14 unused by any D2R missile |
| itemstatcost `descfunc` | 5-29 in use | not located | Values outside D2R's set print nothing useful: plugin |
| properties `func1` | 1-25, 36 | funcs at `0x2386ab0` | plugin |
| misc `pSpell` | -1..15 | `0x236fe30`, 20 entries | 16-19 |
| cubemain `op` | 28 | switch in `0x529090` / `0x529d50` | anything not 1-28 needs a plugin |
| skills `srvdofunc` / `cltdofunc` | 171 / 109 | 191 / 130 slots | 172-190 / 110-129 |

## Events and spawns

| Rule | Symptom when broken | Fix |
|---|---|---|
| **D2R raises only its own events.** Rows appended to events.txt load, and properties can listen to them, but nothing raises them (a classic engine DLL raised "the unit dies" from a wrapper of the death function) | "Cast on death" and similar properties never fire | Plugin: hook the unit death function `0x44db20` and raise the event through `0x44d570` on the victim ([porting-classic-mods.md](porting-classic-mods.md) section 6) |
| Missile server hit function **6** spawns the monstats row in **`sHitPar1`** (`0x45ce90` -> spawn `0x50a050`) | After a monstats renumbering, every hit spawns the wrong monster | Remap `sHitPar1` of hit-6 rows through the monstats id map; validate it |

## Strings

| Rule | Symptom when broken | Fix |
|---|---|---|
| **D2R matches string IDs by their low 16 bits** | A new string whose ID shares its low 16 bits with a D2R string answers for it (an NPC greeting showed a skill name) | New IDs below 65,536, from `data/local/lng/next_string_id.txt` (28106 in 93847) upward, skipping any 16-bit value in use ([strings.md](strings.md)) |
| D2R formats some strings itself with printf (e.g. `sysmsg7-9`, the "was slain by" fragments joined to names) | Changing the `%s`/`%d` count or order crashes (death message crash) | Keep each D2R key's placeholder list identical |
| A mod's string file replaces the game's whole file | Missing keys show as key names | Ship complete copies |

## Graphics

| Rule | Symptom when broken | Fix |
|---|---|---|
| overlay.txt `Character` is the HD overlay folder (`data/hd/overlays/<Character>/<overlay>.json`) | Empty: no HD aura, cast effect or Whirlwind spin (classic mode still shows them) | Fill it from D2R's matching row or your own folder |
| HD missiles are looked up by missile name in `data/hd/missiles/missiles.json` | A missile not in the map is invisible in HD | Map every new missile name to a model |
| HD player sequence animations are chosen by **skill ID** in `<class>_state_machine.json` (modeId 18 + skillIndex) | A new sequence skill has no HD body animation (no spin) | Copy the D2R state with the same `seqnum` for the new skill ID |
| HD models and textures at **new paths** are resolved through indexes built at start-up (`data/hd/model_lod_desc.bin`, keyed by an FNV-1a hash of the lower-cased path; `data/hd/texture_desc_cache.json`) | A new model loads but is never drawn ("Failed to find LOD for model"); wrong stream offsets in the index: `Invalid data detected!` and a crash in the preload | Ship both index files with your paths added ([hd-graphics.md](hd-graphics.md)) |
| HD skill icon strips: one sprite per sheet under the 16,384 px texture limit; class-less skills (item charges, oskills) all share **one** sheet | More than 62 shared icons at 130 px: the rest can't be shown | Pick which shared icons to keep (most granted first); a generic icon for the others |
| Classic mode files (COF/DCC/DC6) are still loaded: monster COF files for timing even in HD; missing files crash ("Could not add cof file") | Crash on spawn | Ship classic files or reuse a D2R `Code` ([hd-graphics.md](hd-graphics.md)) |

## Sounds

| Rule | Symptom when broken | Fix |
|---|---|---|
| A sound name that does not resolve becomes id **-1**. D2R's client sound readers (`0xd8c70`, `0xd9180`, `0xd9030`, `0xd9e60`, `0xda360`; row count `0x3b20c0`) check the id against the row count but then **read through a null record** | The client crashes (read of `null + 0xc7`, `+0xb0`, `+0x96`) when a monster with that sound comes into view | Resolve every sound name at build time; a plugin guard returns the empty-table answer for an id outside the table (`plugin-template/src/sound_guard.cpp`) |
| On D2RLoader's cache-building start the game loads **D2R's own** sounds table (11,913 rows in 93847), not the mod's ([d2rloader.md](d2rloader.md)) | Every new sound is silent; with no guard, crashes as above | Check the row count after each table load; tell the player to restart |

## Memory

| Rule | Symptom when broken | Fix |
|---|---|---|
| **Renderer Pool: one fixed 12 MB TLSF arena** (`0xc00000` bytes), made once at renderer start (`0xf6efe0`, names "Renderer Pool" / "TlsfAlloctor"). The allocate wrapper `0xf6f060` (100+ callers) only reports "Failed to allocate from Renderer pool!" on a null answer and **returns null**; callers write through it | A scene much busier than vanilla (many models, missiles and overlays at once, e.g. a monster that spawns several traps per attack) fills the pool: access violation **writing address 0** at `D2R.exe+0xF3D028` (the model instance builder's vector move) | Plugin heap fallback: point the pool object (`0x2810ff0`, `{vptr, parent, base, size, tlsf, lock}`) at a **copy** of its vtable (`0x1d6d770` is shared with six other allocators: never patch it in place) whose allocate / reallocate entries fall back to the process heap on null, and whose free / usable-size / realloc entries send pointers outside `[base, base+size)` to the heap. Count and log fallbacks. Worked example: [crash-triage.md](crash-triage.md) |
| Avoid filling it in the first place | | Fewer simultaneous effects per skill (traps, sub-missiles, overlays); trap monsters that die after their shot |

## Saves

| Rule | Symptom when broken | Fix |
|---|---|---|
| `.d2s` checksum + size, and D2RLoader's `.d2rl` sidecar hashes must match | Character silently disappears from (or greys out in) character select | Update checksum, size, both hashes ([saves.md](saves.md)) |
| Extended save format stores wider IDs; hand-built items are rejected | Character disappears even with correct checksums | Never hand-build items; use D2RLoader's debug Item Spawner |
