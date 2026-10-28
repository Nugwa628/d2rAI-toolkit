# Debugging: symptom -> cause -> fix

From real bugs in an overhaul port (D2R 3.3.0 build 93847, D2RLoader 1.3.1). Where a fix needs a plugin, see
[engine-re.md](engine-re.md); addresses in [d2r-93847-addresses.md](d2r-93847-addresses.md).

## Where to look first

| Source | Path | What it tells you |
|---|---|---|
| D2RLoader log | `<game>\d2rloader\logs\d2rloader.log` | Plugin load/install lines, table load asserts (after "startup complete"), `BC_VERIFY` checks |
| D2RLoader crash reports | `<game>\d2rloader\crashes\*.log` | Exception, faulting RVA, stack values (stack values can be stale: don't over-read them) |
| Plugin log | `<game>\mods\<Mod>\d2rloader\logs\` | Your own `LogInfo` lines |
| D2R log | `blz-log.txt` (game's log location) | `File not found "..."` (missing assets), `Failed to find an animation state` (mostly harmless noise, also for D2R's own NPCs) |
| Crash dumps | `%LOCALAPPDATA%\CrashDumps\D2RLoader.exe.<pid>.dmp` | Faulting RIP and memory; parse the minidump's exception and memory-list streams |
| Plugin console command | in-game console | Version, image timestamp match, each feature's install result |

Bisect anything that a plugin may cause with the diagnosis switch file (parts off) before reading code. For a
player's crash report, follow [crash-triage.md](crash-triage.md). For loader-side surprises (default mod, first
start, stray plugins, stored key bindings), see [d2rloader.md](d2rloader.md).

## First questions for a player's report

| Question | Why |
|---|---|
| Does your plugin's log show the version you just released, from `mods\<Mod>`? | `default_mod` naming another or old mod folder, or a stray copy of the plugin in `<game>\d2rloader\plugins`, runs another build |
| Was it the first start after installing or updating D2RLoader or D2R? | That start builds the table cache and loads D2R's own sounds table: silent mod sounds, possible sound crashes. Restart |
| Which other plugins are loaded? | Other gameplay plugins change tooltips, item spells, vendors. Ask them to remove them and retry |
| Steam or Battle.net, which D2R version? | Same image means the same code; the plugin's image check is the real answer |

## Symptom table

| Symptom | Cause | Fix |
|---|---|---|
| A sequence skill (Whirlwind, multi-strike, volley) plays **knockback / get-hit frames** and never really starts | skills `anim`/`seqtrans` = `GH` (classic plrmode spelled Sequence's token GH); D2R's Code is `SQ` | Write `SQ` in skills `anim`/`seqtrans`; keep plrmode/monmode `Code` as D2R's (`SQ`, `KB`) |
| **Crash on first use of a sequence skill**, animation lookup (`0x3b6700`) got mode **-1** | plrmode/monmode `Code` column changed to GH: `SQ` resolves to no mode | Restore D2R's `Code` column |
| Sequence skill works but the **HD character doesn't animate** (no spin) | No state for the skill ID in `<class>_state_machine.json` | Copy the D2R state with the same `seqnum` for the skill ID (modeId 18, skillIndex) |
| **No HD overlay** (no aura glow, no cast effect, no Whirlwind spin effect), classic mode shows it | overlay.txt `Character` empty | Fill `Character` (HD folder) from D2R's matching row |
| Whirlwind keeps spinning / aurastate stays after release | New row on Whirlwind's do functions without `srvstopfunc`/`cltstopfunc` 1 | Copy D2R's stop functions |
| **Monsters can't be hovered or attacked**, states/overlays don't reach the client | A plugin moved the unit state arrays (to allow > 512 states); D2RLoader's wide-state sync reads them at D2R's offsets (+0xaf0) | Remove the patch; D2RLoader 1.3.1 already syncs wide states. Never patch state arrays |
| **Every new unit starts with random states**; game hangs loading a character; can't talk to vendors / move items / use potions; `hUnit` assert (Units.cpp:275) | Patched the stat-list copy routine (`0x2f0d80`), which copies from a stack template: redirected reads took other stack data as states | Don't patch the copy routine (and don't move state arrays at all) |
| `BC_VERIFY: proxy && StatsGetState(proxy, UNIT_STATE_SHAREDSTASH)` (D2Client SCmd.cpp:4448) | Stash proxy missing state 186 `sharedstash` (state sync broken, or first-join packet order) | Fix state sync; a single occurrence at first join was harmless |
| **Character vanishes from character select** after a save edit | `.d2s` checksum/size or the `.d2rl` sidecar hashes not updated; or a hand-built item in the extended format | Update checksum, size, sidecar copies and both SHA-256 ([saves.md](saves.md)); restore the backup; never hand-build items |
| Character greyed out in character select | `.d2rl` mismatch only | Recompute the sidecar |
| **Invisible missiles in HD** (classic mode shows them) | Missile name not in `data/hd/missiles/missiles.json` | Map the name to a D2R model |
| **Crash at a vendor / Item Spawner**, blz-log floods `File not found "data/hd/global/ui/items/<table>/[default_unit]"`; access violation at D2R+0x29538e | Item code (or unique/set name) without an HD picture entry, or an entry naming a removed item | Entries in `items.json` / `uniques.json` / `sets.json` for every item, pointing at existing sprites |
| **Items overflow / overlap the grid** after enlarging it | `cellSize` shrunk; items still draw at 98 px | Keep `cellSize` 98 x 92, scale the grid `rect`; slots native size + same scale |
| Grid larger on one resolution only / items land outside | Only one of the two inventory.txt rows per page changed | Change every row of that page; match layout `cellCount` |
| **Crash on death** | Overridden string with a different `%s` count (`sysmsg7-9` "was slain by" fragments are sprintf'ed by D2R) | Keep D2R's placeholders per key |
| **Wrong text** (an NPC greets with a skill name) | New string IDs collide with D2R IDs in the low 16 bits | New IDs from `next_string_id.txt`, < 65,536, skipping used 16-bit values |
| Tooltip numbers missing ("Durability: of") | Overridden tooltip strings lost their placeholders | Keep D2R's text where placeholders differ |
| Skill tooltip asserts `stringIndex1 != strdummy` (`0x287a50`) | A skilldesc line type whose text a/b is empty, or a made-up line type colliding with D2R's 74-79 | Give each line its texts; use D2R's line types only |
| Can't put points in skills (cost 999) | `skpoints` formula uses a calc code D2R doesn't know (evaluates 0 / huge) | Express caps in D2R's codes or implement the code in a plugin |
| Skills can't be bound to the mouse | `rightskill` flag unset | Set `rightskill` 1 |
| Skill with a new srvdofunc does nothing | Function number not in D2R's table, or it collides with a D2R 3.x function (153-171) | Use a D2R function that does the same, or a plugin function in 172-190 |
| Client effect of a skill looks like a D2R skill (Volcano, Ring of Fire) | cltdofunc 96-109 belong to D2R 3.x skills | Plugin client functions in 110-129 |
| Item right-click does nothing (potion, scroll) | misc `pSpell` >= 20 (ignored) or an unknown number | Map to D2R's spell (timed potion = 3) or plugin handler in 16-19 |
| Item used up when it shouldn't be (or server refuses) | Item spell table flag; client and server disagree | Flag 1 on that slot (not used up) |
| Item procs / oskills grant the wrong skill | Skill IDs in property parameters not remapped after rows moved | Remap every skill/monster parameter in item, affix, recipe and monprop tables |
| Chilled monsters run faster | Signed `coldeffect` written unsigned (236 instead of -20) | Write signed bytes signed |
| Monster stands still where it should cast (raise, revive, charge) | monstats `Sk*mode` holds a monseq name that doesn't exist (or a placeholder) | Use a monmode code or a real monseq sequence name |
| Load asserts: formula errors | > 255 chars, quoted name > 32 bytes, bare negative literal, unbracketed ternary branch | Shorten / rewrite formulas ([engine-limits.md](engine-limits.md)) |
| Load asserts: `ii < NUM_LEVEL_TYPES` | AutoMap.txt rows for level types beyond D2R's 36 | Harmless if a plugin handles the lookup; else drop those rows |
| Load asserts about state count (`size <= 0x1ff`) | More than 511 states | Harmless with D2RLoader 1.3.1 (wide states) |
| Empty automap in new areas | Level type >= 36 | Plugin hook on `0x32bdf0` |
| Black screen / crash entering an area | Missing `.dt1`/`.ds1` tile file of the level type | Ship the tile files |
| Crash when a monster spawns (`Could not add cof file`) | monstats `Code` has no classic COF | Ship COF/DCC or reuse a D2R Code |
| Crash in classic mode when an effect spawns | Missing classic `.dcc` for a missile/overlay | Ship the file or point at an existing one |
| Portal leads to the wrong level (level ID > 255) | Byte setter `0x34e9d0` clears the high byte | Plugin writes the high byte (object data +0x78) |
| Vanilla items appear in drops of a total-conversion | Auto treasure classes (`weapN`/`armoN`) pull every spawnable base | Remove or `spawnable 0` vanilla bases |
| Monster sounds / affixes shifted by one row | Blank-key first row skipped when counting | Count every row except `Expansion` |
| Game hangs on load after turning one plugin part off | Feature dependency (formulas reference calc codes the off part provides) | Know dependencies; turn related parts off together |
| **Crash or hang picking up / wearing an item of one type** (e.g. every circlet), assert `UnitGetType(hUnit) == UNIT_ITEM` (ItemMode.cpp:676); socketed runes add nothing | itemtypes ids shifted: D2R's code tests type numbers (75 rune) and hits another type | D2R-only rows at D2R's numbers (`warl` 67) ([engine-limits.md](engine-limits.md)) |
| **Client crash right after a kill**, `BC_ASSERT: hUnit`, item serializer asserts (`Items.cpp` `ptItemStats`) in d2rloader.log | A dropped item has a function-28 property layer on a stat with no `Save Param Bits`: the server writes an empty item packet | Clear that parameter, or give the stat param bits ([saves.md](saves.md)) |
| **Crash when a monster comes into view** (read of `null + 0xc7` in a sound reader) | Its sound id is -1: a name that did not resolve, or D2RLoader's cache-building start loaded D2R's sounds table | Resolve names at build time; plugin sound guard; restart |
| All new sounds silent | Same cache-building start | Restart the game |
| **Every hit of a missile spawns some monster** next to the target | Server hit 6 spawns `sHitPar1` as a monstats row; it was not remapped | Remap hit-6 `sHitPar1` through the monstats id map |
| "Cast on death" and other event-driven properties never fire | The event is a row D2R never raises | Plugin hook on the unit death function ([porting-classic-mods.md](porting-classic-mods.md)) |
| A summoned pet dies about 2 s after the cast, untouched | Raven-AI pet with an empty `calc3` (D2R's attack count) | Put the count formula in `calc3` |
| A ranged pet only melees | The classic engine set a ranged-AI flag from a summon skill parameter; D2R does not | Plugin sets the pet's AI control field |
| A skill can't be put on the right button, or a ground click walks instead of casting | `leftskill`-only skill (often an innate charstats skill) | `rightskill 1` |
| A monster skill opens a town portal | Classic srvdofunc 109 (monster frenzy) is D2R's itemless Town Portal | Remap 109 -> 9 for those rows |
| Every portal recipe opens the Cow Level | D2R's cube portal output knows only the Cow Level | Plugin hook on the cube output handler |
| A classic timed potion of a regeneration stat gives almost nothing | D2R's timed potion divides non-life/mana stats by the duration | Multiply the calc by `len` |
| **Client hangs after a few area changes**, log repeats `Reading From: <char>.ma0` | automap cel >= 1499 stored in the map memory (self-loop), or levels `Layer` >= 100 | Clamp cels to <= 1498; keep `Layer` < 100 |
| Game closes without a report entering a new maze level | levels `Position 1` with no entry rooms | Clear `Position` |
| **Write to address 0 at `D2R.exe+0xF3D028`** in a busy fight | The 12 MB Renderer Pool is full; the allocator returned null | Plugin heap fallback ([crash-triage.md](crash-triage.md)) |
| A changed default hotkey never reaches players who ran an older build | D2RLoader stored the old key in `input-bindings.toml` on first registration | Installer drops the stale entry |
| A check passes once, then fails on the same test character | The quest finished: D2R runs a quest's kill handler only while it is open | Reset the quest words in the test save |
| A fix "doesn't work" on one PC only | Another mod folder in `default_mod`, an old plugin in the game-wide folder, or other plugins | Your environment guard's warning; compare file hashes |
| Recipe ignores its condition | cubemain `op` outside 1-28 (search handles player ops 1-14, matcher item ops 15+; unknown passes) | Plugin hook on the recipe search / matcher |

## Hangs

Dump the hung process (MiniDumpWriteDump), sample the busy thread's RIP a few times, map RIPs to functions with
`.pdata`. A loop through item getters (`0x36f000`, `0x373610`, `0x3731f0`, `0x349860`, `0x300a90` seen in one
hang) with D2RCore trampolines meant per-unit state corruption; bisect with the diagnosis switch.

## Testing workflow that worked

- Install only with the game closed; restart D2R fully after each install.
- Keep a `testing.md` table (what changed, how to test, expected result) and let testers run batches; move rows to
  "confirmed" or into a bug list.
- Back up saves before any change that touches items, grids or stats.
