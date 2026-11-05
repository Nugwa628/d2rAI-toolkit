# World objects: creation, init functions, operate functions, traps, shrines

Objects (chests, urns, barrels, shrines, wells, doors, portals, waypoints, quest props) are server units of type 2
driven by three objects.txt columns: `PopulateFn` (how random ones are placed), `InitFn` (what each rolls or reads
when made) and `OperateFn` (what a click does). This file maps all three in **D2R 3.3 build 93847**, for mods that add
object rows or plugins that change what objects do.

- RVAs are for build 93847 only (image base `0x140000000`); re-find them on any other build
  ([engine-re.md](engine-re.md)).
- Confidence: **high** = matched against the running game (thousands of populations and operates compared draw for
  draw) or named by asserts; **medium** = read in Ghidra and consistent with classic; **static only** = read, never
  run; **unverified** = classic rule assumed.
- Room population order and the `ObjGrp`/`ObjPrb` pass are in [monsters-spawn.md](monsters-spawn.md); waypoint and
  portal travel rules in [engine-server.md](engine-server.md) section 5 and [map-generation.md](map-generation.md);
  message ids in [network-messages.md](network-messages.md).

## 1. The objects record and the seeds

| What | Value (build 93847) | Confidence |
|-|-|-|
| `Selectable0-7` | record +0x85 | high |
| `SizeX` / `FrameCnt0-7` / `FrameDelta0-7` (u16) | +0x90 / +0x98 / +0xb8 | high |
| `CycleAnim0-7` / `HasCollision0-7` / `Start0-7` / `Mode0-7` | +0xc8 / +0xe0 / +0xe9 / +0xfe | high |
| `Parm0-3` | +0x134 / +0x138 / +0x13c / +0x140 | high |
| `InitFn` (byte) / `OperateFn` | +0x15c / +0x15e | high |
| spark flag source | +0xe8 | medium |

Byte-wide columns (`ObjGrp`, `ObjPrb` past 127, `SubClass`, `DENSITY`, `Gore`) are read **unsigned**.

Four random streams touch objects; an extra draw on any of them shifts every later object in the game:

| Stream | Where | Used for |
|-|-|-|
| game seed | game +0x1a8 | one draw per unit made (its unit seed) |
| object control seed | `*(game+0x21d0)` | theme roll, populate functions 1/3/4/6/7, every InitFn roll, PreOperate, every OperateFn roll, trap types 8/9 |
| active room seed | active room +0xa0 | the eight group rolls and member picks, shrines (PopulateFn 2), wells (8), PopulateFn 9 |
| unit seed | unit +0x28 | a chest's re-seed, the dropper seed of its drops, the mode-change jitter (section 3), shrine potion counts |

The object control seed is the second draw of `{mapSeed, 666}` at game creation (the first is the monster seed).

## 2. Populate functions (table `0x239bc10`)

| PopulateFn | RVA | What | Confidence |
|-|-|-|-|
| 1 | `0x50d340` | caskets, urns, sarcophagi, baskets in clusters (class lists `0x239b810`..) | high |
| 2 | `0x50cf10` | shrines: spot by `Orientation` (`0x5119c0`), 50 subtiles apart on either axis, at most 10 and rooms/8. D2R draws the room-seed chance **before** the health-shrine test (classic skipped it) | high |
| 3 | `0x50d1d0` | common objects: `density * (w*h >> 7) >> 8` tries of `0x511760` (five spots, box size + 6 against mask `0x3f11`) | high |
| 4 | `0x50c370` | barrel clusters, one in three (then one in four) exploding; at most 8 | high |
| 6 | `0x50e070` | as 3, then flies on the last one (`% 100 > 70`, `0x5114b0`) | high |
| 7 | `0x50db60` | rogue corpses on stakes, one of four formations (`0x239bcb0`), flies on each | high |
| 8 | `0x50c180` | wells, box `2s + 1`, 100 apart, at most 4 and rooms/8 | high |
| 9 | `0x50e1f0` | as 3 on the room seed, stops at the first object made | high |

- Level `Themes` (table `0x239bcf0`) are rolled (`% 100` against 12, +5 past half the rooms, +5 more past three
  quarters) but the table is bounded by the list length, so Act I never reaches a theme function; only the draws
  remain. A theme function, when one runs, ends the room's object phase (high).
- A group member whose objects row has `Gore` above 2 is skipped (high).
- Late forced health: past 75% of a level's rooms with no health shrine or well yet, a group whose same-numbered
  objects row has a `SubClass` rolls 100. A forced health shrine that comes out as another shrine becomes the class in
  its row's `Parm1` (or the healing well, row 84) with shrine id 2 (high).
- Footprint (`0x48ee10` -> `0x3489a0`): the `SizeX * SizeY` box when `HasCollisionN` of the mode is set. Bits
  (`0x349890`): `0x400`; `0x404` with `BlockMissile`; `0x8000` for `SubClass & 4`; doors `0x806` with `BlocksVis`, else
  `0x804` with `BlockMissile` (high). The box test is `0x364b20`; a box reaching outside every known room counts as
  blocked.

## 3. Creation, init and the mode change

Create `0x4905a0` -> `0x48cdb0` -> init dispatcher `0x510ba0`:

1. one game-seed draw (the unit seed);
2. spark flag reset (`0x34db10`), interact byte 0 (`0x34e9d0`);
3. asserts mode < 8 and `InitFn` < **0x51**, calls entry `InitFn` of the table `0x239b980` (81 pointers, 4 null);
4. operator slot cleared to -1 (`0x34e970`), targetable flag from `SelectableN` of the current mode;
5. **PreOperate**: when the row has it, `objectSeed % 14 == 0` opens the object (mode 2) (high).

The init function gets a 0x30-byte block: +0x00 game, +0x08 object, +0x10 room, +0x18 object control seed, +0x20
objects record, +0x28/+0x2c x, y (high).

**Mode change jitter** (`0x34e5f0` -> animation setup `0x34c880`): every mode change after creation (init,
PreOperate, operate) draws once from the object's **unit seed**, `roll(FrameDelta[mode] >> 3)`, when that is above 0
and the row is not `Sync`. It also drops the footprint when the new mode has no collision and sets the targetable bit
(unit flags bit 1, read by `0x3500e0`). A chest's open-mode jitter therefore comes before its drop rolls (high).

### InitFn table (`0x239b980`)

| InitFn | RVA | What it does | Confidence |
|-|-|-|-|
| 1 | `0x50e450` | shrine type: `Parm0` 0 = up to 8 tries of `roll(rows - 1) + 1` until the area level reaches `LevelMin`; else effect class 2/3/4 (class 1 on `% 10 == 0`) via `0x512050`; then ids 4 -> 2, 5 -> 3, 16 -> 18. The id is the interact byte | high |
| 2 | `0x50e6d0` | urn trap: `% 100 < MonLvl/8 + 5`, type `(& 7) + 1` | high |
| 3, 57 | `0x50e920`, `0x50eac0` | chest: same trap roll; locked (`| 0x80`) when `Lockable` and `% 100 < MonLvl/2 + 8`; unit seed becomes `{draw % 0xfffe + 1, 666}`. 57 also marks it sparkly | high |
| 4 | `0x5dc520` | tower tome: mode 3 before the Countess quest's intro; asserts without quest data | medium |
| 5, 31-33, 56, 70 | `0x78d20` | empty | high |
| 6 | `0x5dc3e0` | Cairn stone: mode from the Cain quest; stone Alpha (class 17) starts the open-portal quest timer | medium |
| 7 / 9 / 15 | `0x516140` / `0x5dc360` / `0x516290` | gibbet / Inifuss tree / Malus: take the quest's stored mode and record the unit in the quest | medium |
| 8 / 14, 26 / 22 | `0x50ee40` / `0x50ee50` / `0x50e660` | torch mode 2 / brazier mode 1 / fire mode 2 when `Mode2` and not `Mode0` | high |
| 11 | `0x50ed70` | town portal: interact byte = the act's town (1, 40, 75, 103, 109) | medium |
| 12 | `0x50eae0` | permanent portal: opens, interact byte = the other end of a hard-coded level pair (section 3.1) | medium |
| 13 | `0x5dc4c0` | invisible sound object: links the unit to quest 4 | medium |
| 16 | `0x50e600` | well: charges = `2 * Parm2` | high |
| 17 | `0x512a50` | waypoint: mode 2 in a town (also for the waypoint a player arrived by; that list is at object region +0x80130) | high for the town case |
| 18, 19, 30 | `0x5161b0`, `0x5161f0`, `0x5a04f0` | Lut Gholein: Jerhyn at placeholder 1, the palace guard (class 0x14b) and Jerhyn by the palace, the harem blocker (object 318) | medium |
| 24 | `0x50e620` | floor spike trap: event 3 at frame + 25 | static only |
| 27 | `0x50e790` | goo pile: interact 3 on `% 1000 < 333` | high |
| 28 | `0x50e7f0` | gold placeholder: mode 2, `roll(9) + 1` gold piles near it | high |
| 47 | `0x5dc000` | Countess chest: registers up to 8 chests with quest 5; after her death fires missile 0x14c at each from a monster 0x146 | medium |
| 54 | `0x5dbf70` | Cain's town start marker (object 385): records it, spawns town Cain when the quest says so | medium |
| 61 | `0x5dbf20` | Cain's portal: mode 1, quest event at frame + 25 | medium |
| 80 | `0x50f510` | D2R addition: classes 0x248-0x24a (the colossal statues) record their id at game +0x1a600/+0x1a604/+0x1a608 and take their mode from game +0x1a618/+0x1a61c/+0x1a620 | static only |

The other entries are Acts II-V quest props (altars, orifice, tome stands, sewer, Hell Forge, Hratli, Natalya,
Diablo's start, the Act V block 62-79); InitFn 10 (`0x50e3d0`) has code but no vanilla row uses it. A **new object row
can reuse any of these numbers**; a new behaviour needs a hook on the dispatcher or a free table entry (0, 35, 36, 40
are null).

Preset placeholder 580 (`0x510e10`) becomes one of 13 Act I chests by `objectSeed % 13`; 574-582 are the other acts'
placeholders (high for 580).

### 3.1 Permanent portal pairs (InitFn 12)

| Level | Other end |
|-|-|
| 1 / 39 | 39 / 1 (cow level) |
| 4 / 38 | 38 / 4 (Tristram) |
| 46 / 74 | 74 / 46 (Canyon of the Magi and Act II's sanctuary) |
| 109 / 121 | 121 / 109 (Nihlathak) |
| 125, 126, 127 | 111, 112, 117 |
| 111, 112, 117 | 125, 126, 127 unless already set, then event 0xb a frame later |
| 73 | the true tomb (from Act II's layout, `0x2efe90`) and back |

Any other level gets 0. These are hard-coded: a portal object placed in a new level needs a hook here (medium).

### 3.2 Quest free-spot search (`0x517f90`)

Used by quest spawns (Cain, Jerhyn): rings around `(x - size/2, y - size/2)`, ring r covering columns -r..r-1 and rows
r..-r+1, every other subtile, top row down, left to right; a subtile counts when the `(2*size + 1)` box there clears the
mask. The answer is the subtile + size/2 in its room; nothing found leaves the point and returns a null room. Callers
pass 100 rings (high; the spot matched the game exactly on three seeds).

## 4. Operating an object

### 4.1 The checks (C->S 0x41)

Gate first (dead/dying or state 0x36 refused, see [engine-server.md](engine-server.md) section 1), then
`0x4b0f80`, then `0x593630`. Any failure drops the message with no reply (high for order, static only for each test):

1. The guid is an object in the requester's own game with an objects row.
2. The requested mode is below 8 with `SelectableN` set (or class 561), and equals the object's mode.
3. `0x42bfb0` must let the player start the action (OperateFn 5 with skill 1; OperateFns 15, 16, 44, 47, 50, 66, 71
   with mode 2).
4. Interact range `0x34bc90` (distance 0 or the corner ring test `0x34bde0`); classes 152 and 561 are exempt. Out of
   range, the client walks up and asks again.
5. In `0x593630`: the object is in the player's room or its near rooms; unit distance (`0x325140`) at most 50; the
   object targetable; the player not dead and not in state 0x36; a cursor item only for OperateFn 25.
6. Unless class 152: a clear line (`0x350550`, mask `0x804`, walls and closed doors).
7. The param is the cursor item's guid, or `0xffffffff` with none; OperateFn below 0x4b and not null.

Dispatch `0x592c40` builds `{game, object, player, object control seed, class}` and calls entry `OperateFn` of
`0x23a51c0`. Entries 11 (torch), 13 (tiki torch) and 17 (obelisk) are null in D2R. Unless noted each function does
nothing (returns 0) outside mode 0.

### 4.2 Containers

| OperateFn | RVA | Behaviour | Confidence |
|-|-|-|-|
| 4 chest | `0x58e2f0` | locked (interact `& 0x80`): needs an Assassin or a key (`0x4712a0`), else sound 0x17 and stop. **D2R opens first, then drops** (classic dropped first). Drops `(interact >> 7) + 1` times; sparkly chests force magic (rare on `% 100 < 5`) and retry up to 10 times for a magic item; else `% 100` above 24 (or locked, or sparkly) drops. Class 397 has its own `% 10000` table. Then the object's drop code, then the trap | high |
| 1 casket | `0x58edd0` | one chest-TC drop; only if an item was made: mode 1, a `% 10000 >= 8192` trap monster, the trap | high |
| 3 urn, basket, jar | `0x58f1d0` | mode 1, `% 100 < 21` drops, the trap | high |
| 14 corpse | `0x58eed0` | a drop, mode 1, the trap | high |
| 5 barrel | `0x58e890` | the player kicks it (skill 1), mode 1, `% 10000 >= 8192` trap monster, `% 100 < 21` drop | high |
| 7 exploding barrel | `0x58ea20` | explosion `0x593ab0`: damage in range 3 rolled on the **barrel's** unit seed (2-3 draws per unit hit), neighbouring exploding barrels within 2 operated in turn | high |
| 19 armor stand / 20 weapon rack | `0x58f560` / `0x58f5f0` | one armor (`0x43f4f0`) or weapon (`0x4414c0`) drop, mode 2 | high |
| 26 bookshelf | `0x58ff50` | mode 2; scroll on `% 20 < 13` (identify or town portal), else a tome. D2R adds a `roll(10) == 0` pick of 15 extra codes in expansion Nightmare/Hell that are not items in the 3.3 tables: nothing drops, but the draws happen | high |

**Chest treasure class** (`0x5930b0`): the area level against the act's level span (`(|max - min| + 1) / 3`) picks
index 0, 1 or 2 of the per-difficulty, per-act `Act N[ (N)| (H)] Chest A|B|C` records (`0x396430`). The object is the
dropper (its unit seed is the dropper seed). The level the **quality roll** measures against is that index (0-2), not
the area level, while the items themselves take the area level: container drops are graded as from a level 0-2
dropper, so low quality is common and better qualities rarer than a monster of that level gives (high). No NoDrop
player cap applies to chests ([engine-server.md](engine-server.md) section 3).

### 4.3 Traps (`0x594630`, handlers `0x23a5160`)

Trap type = interact `& 0x7f` (1-9). Arming: object event 4 at frame + 35 and sound 0xd on the object.

| Type | Effect when it fires |
|-|-|
| 1 | monster 0x14a at the object |
| 2, 6 | monster 0x146 |
| 3 | monster 0x149 |
| 4 | monster 0x171 |
| 5, 7 | a large fire on the object and a small one a subtile east (objects 162, 160) |
| 8, 9 | `(objectSeed & 1) + 1` of the level's trap monster, at the spot then 1 and 3 rings out |

The level's trap monster (`0x512bf0`, kept at object region +0x1c) is the base of the first monster group the level's
spawn list falls in; with none it is 0xea (flying scimitar), and in Act I types 8/9 stay unarmed then (high).

### 4.4 Shrines (OperateFn 2, `0x58f2a0`)

Once only (D2R asserts the operator slot is -1) and in mode 0: stores the operator, mode 1, the shrine name as hover
text for 300 frames, the effect, and with `reset time in minutes` set a reset to mode 0 at `frame + 1 + minutes * 1200`
(the column is a byte at record +0x10: values above 255 wrap). Effect handler table `0x1d3f1d0` (24 rows of handler,
stat, state, same as classic); a code past the table refills (high).

| Code | Shrine | Effect | State | Confidence |
|-|-|-|-|-|
| 1 / 2 / 3 | refill / health / mana | add (max - current) | | high |
| 4 / 5 | health / mana exchange (unused) | `0x590e30` / `0x590ec0`, 32-bit wrapping arithmetic | | medium |
| 6 | armor | stat 171 = Arg0 | 128 | high |
| 7 | combat | stat 25 = Arg1, stat 19 = attack rating * Arg0 / 100 | 129 | high |
| 8 / 9 / 10 / 11 | fire / cold / lightning / poison resist | stat 39 / 43 / 41 / 45 = Arg0 | 131 / 132 / 130 / 133 | high |
| 12 | skill | the state alone (+2 all skills while it lasts) | 134 | high |
| 13 | mana recharge | stat 27 = Arg0 | 135 | high |
| 14 | stamina | stamina filled, stat 162 = value, stat 10 = 2 * value, stat 28 = 1000; refilled at the end | 136 | high |
| 15 | experience | stat 85 | 137 | high |
| 16-22 | enirhs, portal, gem, fire, monster, exploding, poison | world effects; exploding and poison roll the potion count on the shrine's unit seed | | medium |

Timed shrines add the state's stat list for `Duration in frames` (`0x433d20`); taking the same shrine again restarts
that state. Value (`0x594da0`): stats 11, 27, 39, 41, 43, 45, 85, 171 take Arg0 as is; to-hit takes Arg0 % of the
attack rating (`0x593910`, left weapon); stat 21 takes Arg0 % of min damage (two-handed min damage for a two-hander);
any other stat Arg0 % of its total, all wrapping at 32 bits (medium).

### 4.5 Wells (OperateFn 22, `0x58fd00`)

Mode 0 or 1. With charges left (`0x5949a0`): life when `Parm3 & 2`, mana when `Parm3 & 1`, stamina always, each by
`max * Parm1 / 256`; also removes curses and poison and heals pets. A use costs one charge; at 0 mode 2, at `Parm2`
mode 1; a regeneration event at `frame + 1 + Parm0` gives one charge back up to `2 * Parm2` (high for the use, medium
for the regeneration).

### 4.6 Doors (OperateFn 8, `0x58e090`) and secret doors (18, `0x5900c0`)

D2R keeps **500 ms of wall clock** between door changes: the last change is a 64-bit tick at unit +0x138 (read
`0x34b990`, written `0x34f340`, clock `0x120d720`). Inside the window the function returns 0 and does nothing.
Otherwise (high):

| Mode | Result |
|-|-|
| 0 | open (mode 2), footprint removed |
| 2, box clear of player/monster/corpse bits (`0x8180`) | closed (mode 0), footprint back |
| 2, a corpse bit `0x8000` in the box | mode 4 (blocked open) |
| 2, only a player or monster in the box | no change, but the tick is stamped and it returns 1 |
| 6 (locked) | a key opens it (mode 2); without one sound 0x17, and the tick is **not** stamped |

Classic returned 1 inside the window, used mode 5 and sound 0x16. Secret doors: mode 1 and the end event.

### 4.7 Timers on objects

Event 1, end of animation: mode 1 -> 2 when `Mode2` is set, written directly (no jitter, no message), at
`frame + 1 + FrameCnt1 / 256`. Event 2 well regeneration, 4 trap, 5 shrine reset, 6 hover end, 7 quest event
(dispatcher `0x516370`, switched on the class: Cain's portal, the harem blocker, the cage and others) (high for the
numbering, medium for the quest cases).

### 4.8 Waypoints and portals as objects

Waypoints are OperateFn 23 (`0x58fe00`): touching one sets its bit; town and red portals are OperateFn 15
(`0x58f680`). Their travel rules are in [engine-server.md](engine-server.md) section 5. Taking a portal ors 5 into its
interact flags (medium).

## 5. Messages

| Message | When | Bytes | Confidence |
|-|-|-|-|
| S->C 0x51 (15) | an object enters a client's view | `51 02 guid:u32 class:u16 x:u16 y:u16 mode:u8 interact:u8 extra:u8` (extra = object data +0x78, usually 0) | high |
| S->C 0x0e (12) | an object's mode changes | `0e 02 guid:u32 03 targetable:u8 mode:u32` | high |
| S->C 0x2c | sounds | 0x17 locked, 0xb unlocking, 0xd trap armed | high |

The interact byte is the shrine id, a chest or urn's trap type (`| 0x80` locked), a well's charges or a portal's
destination level. The `targetable` byte of 0x0e is unit flags bit 1 as set from `SelectableN`; sending 0 there makes
the object unclickable on the client.

## 6. Pitfalls

| Trap | Symptom | Fix |
|-|-|-|
| An object put into a mode its row does not have (an operate function reused on a row with `Mode1` blank) | client asserts in its graphics code and closes | give a reused OperateFn a row with every mode it sets |
| An extra object-seed draw in a hook | every later shrine type, chest trap and drop in the game changes | draw from your own seed |
| Chest trap/lock odds | use the Normal `MonLvl` on every difficulty | accept, or hook `0x50e920` / `0x50e6d0` |
| Higher chest quality wanted | the quality roll uses the TC index 0-2, not the area level | hook `0x5930b0` or give chests their own TC with quality ratios |
| Shrine `reset time` above 255 minutes | wraps (byte column) | stay at 255 or below |
| A portal object in a new level | permanent portal has no pair, destination 0 | hook InitFn 12 (`0x50eae0`) |
| Door spam | a second click inside 500 ms is ignored silently | expected; doors are wall-clock timed, not frame-timed |
