# Monsters: spawning, packs and AI (server side)

How the D2R 3.3 server fills a room with monsters and objects, builds champion and unique packs, picks monster levels,
and runs monster AI. Observed in **D2R 3.3 build 93847** (image timestamp `0x6ab3782c`); every address below is an RVA
for **build 93847 only** and must be re-checked in Ghidra on any other build ([engine-re.md](engine-re.md)). Most of
it was read statically and checked against recorded spawns of Act I (every room of levels 1-39, all three
difficulties); the AI notes are **static only** unless marked otherwise. D2R's population and AI are classic 1.10
almost line for line; the differences that matter to a mod are called out.

Related: limits in [engine-limits.md](engine-limits.md) ("Events and spawns"), the unit layout and the generic spawn
function `0x492cf0` in [d2r-93847-addresses.md](d2r-93847-addresses.md), table columns in [tables.md](tables.md),
drops on death in [engine-server.md](engine-server.md). The full AI framework, every common AI's behaviour, the
mods' start handlers, the new-monster message and player corpses are in [monster-ai.md](monster-ai.md).

## 1. Structures worth knowing (build 93847)

| Where | Offset | Meaning |
|-|-|-|
| game | +0x104 | difficulty (0 normal, 1 nightmare, 2 hell) |
| game | +0x106 | data bank; the expansion rules apply when it is not 1 |
| game | +0x12c | map seed |
| game | +0x170 | frame counter (25 frames per second) |
| game | +0x1a8 | game seed `{lo, hi}` |
| game | +0x1c8 | monster seed (first draw of the game seed) |
| game | +0x1d0 | monster regions, one pointer per level id |
| game | +0x1d8 | superunique "already spawned" flags |
| active room (0xb8 bytes) | +0x54 | flags; bit 0 = populated |
| active room | +0xa0 | room seed |
| DRLG room | +0x50 | flags; `0x800000` = never populated |
| DRLG room | +0x98 | preset units (from the DS1) |
| monster region (0x9f0 bytes) | +0x04 / +0x08 / +0x0c | rooms seen / rooms that spawned / populatable rooms (counted on first use) |
| monster region | +0x14 | up to 13 monster entries (class, rarity, component variants) |
| monster region | +0x2b8, +0x2bc, +0x2bd, +0x2c0, +0x2c8 | MonDen, MonUMin, MonUMax, area level, uniques spawned so far |
| monster region | +0x2e8 | D2R-only Terror Zone block (`+0x2e8` = level is desecrated) |
| monster data (unit +0x10) | +0x1a | u16 type flags: 1 boss, 2 superunique, 4 champion, 8 counted as a unique of its level, 0x10 minion, 0x20 possessed, 0x40 ghostly, 0x80 multishot (more in [monster-ai.md](monster-ai.md)) |
| monster data | +0x20 | up to nine monumod ids, zero-terminated |
| monster data | +0x2a | u16 superuniques row (-1 none) |
| monster data | +0x30 | pointer to the AI control block (section 6) |
| monster data | +0x68 | last AI mode ("was hit" reads it) |

## 2. When a room is populated

- Rooms become active by player proximity: the room a client stands in and its near rooms are created. Joining a game
  creates the act start room first, then its near list.
- `CreateRoom` (`0x326480`) steps the DRLG room seed once **in place** (rooms built later re-pick their shared border
  tiles from the stepped value) and makes the active room with seed `{that value, 666}`; a DRLG room whose tile build
  made no wall or floor records gets no active room. Its
  collision grid is built from the near rooms that exist at that moment and is not refreshed when later neighbours
  appear; only units placed later write into it.
- Population happens **on the next game frame**, in the pending-rooms loop (`0x408670`, called from the frame function
  `0x406550`). It walks each act's active rooms **newest first**. Every pass rolls the rare wandering-monster check
  (one room-seed draw; a hit only on 1 in 32768). An unpopulated room then runs, in order:
  1. preset units `0x48f190` (non-monster presets first, then monster presets),
  2. restore inactive units `0x503790` (units that were saved when the room went cold),
  3. random objects `0x510f00`,
  4. random monsters `0x50acf0`,
  5. mark populated `0x2f04e0`.
- A room is populated **once**. What it gets depends on what was populated before it in the same game: the game seed
  is shared, the region counters (rooms seen, uniques spawned) move, and units already placed block border spots.
  Do not expect two walks through the same map seed to give the same monsters.
- Global switch: the byte at `0x238d5b8` gates random monsters (the "monsters on" flag; 0 = no random spawns).

### Random streams

| Stream | Seeded from | Used for |
|-|-|-|
| game seed (`game+0x1a8`) | `{map seed, 666}` | one draw per unit created (its own seed), item seeds, the density rolls |
| monster seed (`game+0x1c8`) | first game-seed draw | per-level monster lists at game start |
| room seed (`room+0xa0`) | `CreateRoom` | wandering check, which monster, pack or group, every spawn-spot search, object groups |
| unit seed (`unit+0x28`) | its game-seed draw | minion counts, champion roll, mods, all AI decisions |

The step is `lo' = lo * 0x6ac690c5 + hi`, `hi'` = the carry ([d2r-93847-addresses.md](d2r-93847-addresses.md), Units).

## 3. Which monsters a level can have

At game creation `0x5098b0` takes the monster seed and builds every level's **monster region** (`0x508750`):

- levels.txt `MonDen`, `MonUMin`, `MonUMax` for the difficulty; area level from `MonLvl` (classic games) or
  `MonLvlEx` (expansion games).
- `min(NumMon, 13, list length)` monsters picked at random (without repeats) from `mon1-25` on Normal or `nmon1-25` on
  Nightmare and Hell. With levels `rangedspawn` set, the first pick is re-rolled up to 20 times until it is a monstats
  `rangedtype` monster.
- Only monsters with monstats `isSpawn` enter the region, each with its `Rarity`. **13 is a hard cap**: extra `mon`
  columns only widen the pool the 13 are drawn from.
- Three component variants (looks) per entry, capped by how many looks the monster has.
- A composite monster that the region does not list (a summon, a minion type) is appended at spawn time while there
  are fewer than 13 entries.

## 4. Random monsters in a room (`0x50acf0`)

Skipped when the level has `MonDen` 0, has no populatable rooms, or is the Chaos Sanctum (hard-coded). Otherwise:

1. For every **populate box** of the room (the walkable regions the map generator marks; rooms flagged `0x800000`,
   and boxes on certain hidden floor styles, never populate), roll `((w)/3) * ((h)/3)` times (box size in subtiles):
   a hit is `game seed % 100000 <= MonDen` (MonDen is capped at 10000). So **MonDen is "hits per 100,000 per 3x3
   subtile cell"**, and density scales with floor area.
2. On a hit, `0x419e80` picks a region entry weighted by `Rarity` (room seed). A monstats `placespawn` monster becomes
   its `spawn` row 80% of the time. If no monster can be chosen the room's population stops.
3. `0x419d20` decides pack or group (room seed):
   - a **unique/champion pack** when uniques so far < `MonUMin` and a roll under `100 * rooms seen / populatable rooms`
     passes (so a level catches up to MonUMin as it is explored), or when uniques < `MonUMax` and a 6% roll passes;
   - otherwise a **normal group**.
4. A group spawns `MinGrp..MaxGrp` monsters (forced to 1/1 for the fallen1 and scarab1 base classes), with an extra
   `sparsePopulate` roll; the leader goes to a random spot (`0x509cc0`), the rest around it.
5. A pack's class comes from `umon1-25` on Normal (else the normal choice) and is built as in section 5.

### Spawn spots

- `0x509cc0` (FindSpawnLocation): up to 20 random spots in the box. With warp avoidance it refuses spots closer than
  levels `WarpDist` to a level warp or to the level's spawn-rule location (the first waypoint, else a room with a warp
  out, else the centre room).
- `0x493cc0` (spawn core): tests the spot, then rings of radius 3, 6, ... around it. The collision mask comes from
  monstats2 `spawnCol` (0 ground, 1 water, 2 another mask, 3 no test); the footprint from monstats2 `SizeX` (0-1 a
  point, 2 a plus, 3 a 3x3 box). Too-large `SizeX` or a strict `spawnCol` silently makes a monster rare or absent in
  tight levels.
- Five monster base classes count as spawners and keep an exit clear around them.

### Objects in the same pass (`0x510f00`)

Towns, rooms with DRLG flags `0x30000`/`0x800000` and dirt-path outdoor rooms get no random objects. For each of the
eight levels `ObjGrp`/`ObjPrb` pairs: a `% 100` roll against `ObjPrb`, then a member of the objgroup row by its
`PROB` column, run through the member's populate function with its `DENSITY`. Shrines stay at least 50 subtiles apart
and wells 100; a level stops at 10 shrines or 4 wells (or rooms/8). Objects use their own object seed (made at game
creation), not the room seed. Chests: trapped on `% 100 < MonLvl/8 + 5`, locked (if `Lockable`) on
`% 100 < MonLvl/2 + 8`, with the **Normal** `MonLvl` on every difficulty.

## 5. Champions, uniques, minions and superuniques

### Packs (`0x499880`)

1. The leader is placed with warp avoidance (5 rings without a box if the first spot fails), counted as a unique of
   its level once (type flag 8) and marked as a boss (flag 1).
2. Mod count = `difficulty + 1` (from the game descriptor `0x48ff40`). A leader-seed roll `% 100` below **monumod row
   0's `constants` (20 in vanilla)** makes a **champion**: flag 4 and one weighted champion mod (`cpick`).
   Otherwise it is a **unique** with `min..max` mods, each a weighted pick (`upick`) over the mods the monster may
   take: `enabled`, `version`, the `exclude` monster types, `fPick`, and not already owned.
3. A unique gets `3 + rand(4)` minions of monstats `minion1` (else its own class), with flag 0x10 and the leader's
   transferable mods.
4. A champion gets `seed % 3 + 1` companions, each promoted to a champion with mod 16.
5. The mods' start handlers run (table at `0x2395fe0`, by mod id; what each does is in [monster-ai.md](monster-ai.md)
   section 8). Mod 36 (ghostly) sets type flag 0x40 and mod 38 (possessed) 0x20 in their handlers, not when the mod is
   assigned. Minions run the leader's mod list.

Mod rule for monumod: the champion chance lives in **row 0's `constants` column**, not in a column of its own;
adding or reordering monumod rows moves the mod ids the hard-coded start handlers and the fixed boss mods expect.

### Minion parties (any monster)

After a monster is made, monstats `PartyMin..PartyMax` minions of `minion1`/`minion2` (alternating) spawn within 4
rings, unless the spawn flags suppress it. Fixed mods by base class come from `0x4947d0`; boss mods by base class (act
bosses and quest uniques get mod 22 and others) from `0x494a60`. Monsters with an on-init monequip row get their item
here.

### Superuniques (`0x499ba0`)

- Spawned from preset units (DS1 or monplace placeholders): a preset whose class index is past the monstats rows is a
  superunique row; past the superuniques, a monplace placeholder (`0x50a050` dispatches).
- **Once per game** (flag at `game+0x1d8`) unless superuniques `Stacks` is set; 5 placement tries; `AutoPos`.
- Mods: the row's `Mod1-3` (one hard-coded exception is skipped), plus `difficulty` more random rolls; minions
  `MinGrp..MaxGrp` plus the difficulty; a quest mod (22) appended.
- A superunique uses its base monstats row's AI. One superunique is switched to a special AI state by the spawn code
  (section 6), and the hcIdx (superuniques row) drives a few hard-coded cases inside `0x499ba0`.
- Superuniques do carry type flag 8: the end of `0x499ba0` runs the start handlers (one leader draw, mod 1, inside
  the minion spawn), then counts the unit as a unique and appends mod 22.

### Monster level (`0x543120` -> `0x543b50`)

| Case | Level |
|-|-|
| Normal difficulty, or monstats `NoRatio` / `boss` set | monstats `Level` for the difficulty |
| Nightmare and Hell otherwise | the area level (`MonLvl`, or `MonLvlEx` in expansion games) |
| champion (and its companions) | +2 |
| unique, a unique's minion, the minion party of a unique or champion | +3 |

So in Nightmare and Hell, levels.txt `MonLvlEx` decides monster level for almost everything; the monstats `Level`
columns only matter on Normal and for `NoRatio`/`boss` rows.

### Counting

`0x509520` registers a spawned monster; type flag 8 or monstats `neverCount` means it does not count toward the
level's monster total. monstats `neverCount` is also how critters and quest monsters stay out of pack logic.

## 6. Monster AI

### Dispatch

The short version; the tick step by step, target search, think scheduling and per-AI behaviour are in
[monster-ai.md](monster-ai.md).

- The AI think runs from the unit's **AI-think event** (event type 2; monster event table `0x1d1a7a0`, slot 2 =
  `0x4a2a00`). The end-of-animation handler also calls the tick directly.
- Tick `0x4a2a00`: pre-checks `0x4a3a20` (stunned -> idle; door opening for monstats `opendoors`; minion leash: a minion
  farther than 20 from its boss walks back), then a target search by the AI's kind, then pre-actions `0x4a37f0`
  (boss sighting message once within 20; heal-and-teleport when AI flag 0x20 is set; retarget when the approach is
  blocked and levels `MonSpcWalk` > 0), then the AI's own think.
- **AI table: `0x2396e90`**, indexed by the monstats `AI` column (row byte at monstats +0x52; out of range, 0x9b or
  more, falls back to entry 0). Entries are 0x20 bytes `{kind, init, think, alt}` (layout read on the special-state
  table at `0x23981f0`; assumed the same for the base table). Getter: `0x4a36c0(unit, state)`.
- **Special AI states** 1-17 (table `0x23981f0`) override the monai row: 1 idle, 4 hireling, 7 revived monster
  (uses the necromancer pet think), 10 Dim Vision, 11 Terror, 12 Taunt, 13 a superunique's tower AI, 16 mounted imp,
  and others. Set with `0x4a2690(game, unit, ctl, state)`; state 0 = back to the monai row. D2R adds a **deferred
  queue**: a state change asked while the unit is uninterruptable (state 54) is queued and applied later instead of
  dropped.
- Curse AIs (10-12) are refused for units that cannot switch AI: monstats2 has no walk mode, monstats `boss` set,
  monstats `switchai` clear, uninterruptable, or not attackable. Uniques are not refused by the getter.

Target-search kinds (D2R numbering, renumbered from classic): 0 none (the AI searches itself), 1 find or idle in
neutral, 2 find (null allowed), 3 new (one user), 5 and 6 = classic 4 and 5. Monsters only see players in the same
act, not in town, within `aidist` (monstats, per difficulty; 0 means 35), plus the pets and hireling chained behind
each player.

### Hooking one AI from a plugin

- Easiest: replace the `think` pointer of one entry of the table at `0x2396e90` with your function
  (`PatchWriteU64` with the expected old value), call the original from it for the cases you don't change. The tick
  calls `think(game, unit, tickParam)`; `tickParam` +0x00 is the AI control block, +0x10 the target, +0x20 the
  distance, +0x24 "in melee range", +0x28 the monstats row, +0x30 the monstats2 row, +0x38 difficulty.
- Or inline-hook a specific think function (its RVA is per AI; for example the zombie think is `0x5ba780`, fallen
  `0x5ae650`, fallen shaman `0x5b3fd0`; see the RVA column in your own Ghidra notes before relying on others).
- A **new AI number** needs a new table entry, and the table has a fixed size (entries past 0x9a fall back to
  entry 0): give a new monster an existing AI with new `aip` values, or hook an existing entry and branch on the
  monster's class.
- Keep the think's event contract: a think must end with the unit in a mode change or with the next AI event
  scheduled (`IdleInNeutralMode` `0x4a6d10(game, unit, frames)` or `Idle` `0x4a6e50`), or the monster freezes.
- AI decisions draw from the **unit seed**; extra draws in a hook change what the monster does later, but do not
  desync clients (the AI is server-only).

### AI control block (monster data +0x30; medium confidence)

| Offset | Meaning |
|-|-|
| +0x00 | special state (0 = monai row) |
| +0x08 | current think function |
| +0x38 | u16 AI flags: 0x08 has seen a target, 0x10 boss sighting done, 0x20 heal/teleport allowed, 0x40 force a collision check |
| +0x3c / +0x40 | leash owner id / type (a minion's boss; -1 = none) |
| +0x44 / +0x48 / +0x4c | three scratch params of the current AI, zeroed when the state changes |
| +0x60, +0x68 / +0x6c | game; owner id / type (a minion's boss, a summon's owner); `0x4a53c0(unit)` returns the owner |

### aip1-8 and the other AI columns

- `aipN` are i16 per difficulty at monstats row `0x90 + 6*(N-1) + 2*difficulty`. They are percentages or distances
  compared with a `% 100` roll or the target distance; **their meaning is per AI**, and monai.txt's descriptions
  are sometimes wrong (the fallen "wander %" is really an approach distance).
- `aidel` (monstats, per difficulty; 0 means 15): frames between the end of a mode and the next think. This is the
  main "how aggressive" knob: lower = thinks more often. A stunned unit waits 45.
- `aidist` (0 means 35): how far the monster looks for a target.
- `Skill1-8` with `Sk1mode-Sk8mode`: AIs use skill slots by index (often slot 0 and 1); an empty slot is -1 and is
  skipped.

Common AIs, as observed (n/nm/h values differ per row):

| AI (monai row) | aip1 | aip2 | aip3 | aip4 | aip5 |
|-|-|-|-|-|-|
| 3 zombie | approach % | aware distance | unused | A1 vs A2 % | |
| 6 fallen | leader "command attack" % | approach distance | attack % | A1 vs A2 % | |
| 13 fallen shaman | resurrect and command % | shoot % | melee and circle % | resurrect distance (squared) | shoot distance |
| 34 act 1 boss | spray in melee % | stall % | fire or engage % | spray from range % | |
| 148 generic pet (D2R only) | leash / warp / follow velocity | wander % / swarm cutoff / flags | skill gather / bonus / choose | mode-attack mode / aitype / score | melee min / max / bonus |

AI 148 is new in D2R (used by the Warlock's summons); its three values per aip are three **different** parameters,
not one per difficulty, and aip6 (ranged min/max/bonus) and aip7 (random and progressive score bonus) also apply. Its
skills are scored by monstats skill `aitype`. Only the vanilla aitypes are checked; aitypes 7, 8, 12 and 13 are
**unverified**.

### Modes

0 DT death, 1 NU neutral, 2 WL walk, 3 GH get hit, 4 A1, 5 A2, 6 BL block, 7 SC cast, 8-11 S1-S4, 12 DD dead,
13 KB knockback, 14 SQ sequence, 15 RN run. Mode table `0x1d1a200` (16 entries of 0x20: start, update, end, event
count). At the end of a mode the monster returns to NU and the next think is set at `frame + aidel`. A monster with
no GH mode in monstats2 never plays get-hit (the hit is recorded as a "soft hit" instead).

Animation length comes from `data/global/animdata.d2` (looked up by the token monstats2 `Code` + mode + weapon class,
for example `ZMA1HTH`), **not from the HD assets**; a missing record logs "Failed to retrieve animation file" and uses a
default of 2048 frames at speed 256 (the monster seems to never finish attacking). A record with no event frame uses
`min(frames, 144)` as its action frame in D2R (classic used 0). Sequence modes take their frames from monseq.txt.
New monsters with a new `Code` therefore need an animdata record, not just HD art.

The "faster X" stats go through `0x3516c0` with diminishing returns `cap*v/(cap+v)`: IAS stat 93 cap 120, FHR 99 cap
120, FCR 105 cap 120, FBR 102 cap 120, FRW 96 cap 150 (the formula per mode is classic's; D2R's per-mode branches are
**unverified**).

### Summons and pets

- Summon `0x4350e0` spawns the pet through `0x492cf0`, marks it as a revive unless told not to, registers it with
  `0x4feb00` (pettype and petmax), sets the owner and runs the AI init, then schedules its first think 25 frames
  later. The skill code then calls `0x55fb50` (level), `0x55ef80` (passives/aura) and `0x58d950` (links the pet into
  the owner's target node, so hostile monsters target pets exactly like players).
- Pets are not leashed by the core tick; each pet AI does its own following.
- A revived monster runs special state 7 (the necromancer pet think, melee variant).
- Every monster's init also gives it its monstats `Skill1-8` that have a level above 0, at `Sk*lvl` plus the
  difficulty's `MonsterSkillBonus` (1.14d monster init, SetSkill / SetSkillMode; unverified in D2R). A unit made as
  a monster for a plugin's pet or hireling needs this, or its AI has no skill to cast or swing.
- A pet is good-aligned (stat 172 = 2). Warp-type pets (the hireling among them) are brought to their owner on every
  relocation (1.14d PETS_ManagePetsByType 0x5754b0 -> PETS_TeleportToRoom 0x574cc0). The owner's death kills the
  hireling and frees the other pets; the owner's leave frees all of them, each with a 0x7a pet removal. Hireling
  details: [hirelings.md](hirelings.md).

## 7. Corpses, resurrection and death

- Each room keeps the ids of its **last four** dead monsters (`0x2ef9f0`); AIs that react to corpses (the fallen
  flee on a fresh corpse within 15, the shaman's resurrect search) only see those.
- Player corpses (creation, pickup, the 16-corpse limit, the one-corpse save) are in [monster-ai.md](monster-ai.md)
  section 10.
- A player corpse is never freed when its room goes cold: the inactive-unit saver keeps a player-type unit in the
  corpse state in the act's inactive list (GUID and position) and puts it back on the same spot when the room
  returns (1.14d 0x5433f0 -> 0x542e10, restore 0x542b40; D2R's restore step is `0x503790`, section 2). Wire details
  in [engine-server.md](engine-server.md#7-death-corpse-party).
- Monster resurrect is AI-driven: the fallen shaman looks for a dead (mode DD) fallen or shaman base class within
  aip4 (squared distance), skips unique and champion corpses, and takes the **last** match of the scan. A unique
  shaman searches any such corpse, not only its own minions.
- No AI event is scheduled for a dead unit in D2R (classic could fire one).
- Quest behaviour on a boss's death runs from quest code, not from the AI; drops on death are the treasure class
  logic in [engine-server.md](engine-server.md). Raising "on death" events for your own properties needs a plugin
  hook on `0x44db20` ([engine-limits.md](engine-limits.md)).

## 8. Hard-coded rules a mod hits

| Rule | Effect | Work-around |
|-|-|-|
| A level's region holds at most **13** monster types | More `mon`/`nmon` entries only widen the random pool | Split the level or accept the draw |
| `MonDen` is per 3x3 subtile cell, capped at 10000 | Density scales with floor area, not with room count | Tune per level; small rooms get few rolls |
| Champion chance is monumod row 0 `constants` | Editing row 0 changes champion odds everywhere | Keep row 0; append new mods at the end |
| Uniques per level: `MonUMin`, `MonUMax`, exploration-weighted | Small levels may never reach MonUMax | Raise MonUMin for guaranteed packs |
| Superuniques spawn once per game unless `Stacks` | A second preset of the same row does nothing | Set `Stacks` or use another row |
| Groups of the fallen1 / scarab1 base classes are size 1 | Their `MinGrp/MaxGrp` are ignored | Use minion parties instead |
| Chaos Sanctum never gets random monsters; towns and some rooms never get objects | Spawns missing there | Presets or a plugin |
| Nightmare/Hell monster level = area level for non-`NoRatio`, non-`boss` rows | monstats `Level` (N)/(H) seem ignored | Edit levels `MonLvlEx`, or set `NoRatio` |
| Chest trap/lock odds use the Normal `MonLvl` on every difficulty | Hell chests trap as often as Normal ones | Accept or hook `0x50e920` |
| AI table has a fixed size (0x9b entries) | A new monai row number falls back to entry 0 (idle) | Reuse an AI number; hook its think |
| Population is once per room, order-dependent | Same map seed, different monsters per walk | Don't build tests on exact spawns |
| animdata.d2 decides attack length and hit frame | New `Code` without a record: endless attack | Add the record (classic data) |

## 9. Unverified and not traced

- First think after spawn or room activation (classic: +2 frames when a client enters the room); the D2R callers were
  listed, not mapped.
- The D2R get-hit / knockback "last mode" rule was taken from classic.
- The writer of the minion leash owner (assumed: a group's boss, as in classic).
- Which units other than teleport-enchanted ones (mod 26) carry AI flag 0x20 (heal and teleport when hurt).
- The per-mode animation speed branches in `0x350cb4`, and the monseq loader.
- Terror Zone population (D2R-only step during monster init, active only in a desecrated level): not observed.
- Acts II-V presets, water spawns (`spawnCol` 1) and the wandering-monster branch were not exercised.
