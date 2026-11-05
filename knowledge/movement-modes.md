# Movement, collision and unit modes

How a D2R 3.3 unit moves and what it is doing: the collision words, the path finders and the per-frame step, line of
sight, speed, the unit modes with their animation timing, the per-game event queue, and the messages that tell the
client. Observed in **D2R 3.3 build 93847**; every address is an RVA for **build 93847** only (verify on any other
build, [engine-re.md](engine-re.md)). How rooms and their tile collision are built is in
[map-generation.md](map-generation.md) section 5; the server's movement checks on a client walk are in its section 7;
monster AI and the monster mode table are in [monsters-spawn.md](monsters-spawn.md); the rate stats in combat are in
[combat-formulas.md](combat-formulas.md).

Confidence labels: **high** = read in the code and matched against the running game (thousands of compared cases);
**medium** = read in the code with a partial in-game check; **static only** = read in the code, not seen in game;
**observed** = seen in game, the code not located; **unverified** = classic behaviour assumed. Untagged facts are
high.

## 1. Coordinates and directions

- Positions are subtiles, 5 per tile. A unit's precise position is 16.16 fixed point; a unit at rest sits on the
  subtile centre (`+0x8000`).
- Directions are 0..63. **D2R computes the heading in floats** (`0x380bd0`): classic used a 128-entry tangent table.
  The direction vector is the 16.16 delta normalised with `1/sqrtf`, times 4096, truncated; the direction is an
  `atan2f` of the delta rotated by 3/4 pi, folded to `[-pi, pi)`, scaled by `32/pi`, rounded and masked with 63.
  Straight `+x` is 56 and `+y` is 8 (classic gave 7 for `+y`). Code that reproduces paths must use single precision
  without fused multiply-add.
- Facing: the turn table (`0x1d00150`: 1, 4, 8, -8, -4, -1 by angle) either snaps to the new direction or turns by
  that step each frame. D2R keeps three float facings on the path (`+0x80`/`+0x84`/`+0x88`; static only for the turn
  timing).

## 2. Collision words

### 2.1 Bits

One u16 per subtile per room (the tile part is built at room activation, [map-generation.md](map-generation.md) 5.4).

| Bit | Meaning | Bit | Meaning |
|-|-|-|-|
| `0x1` | wall | `0x100` | monster |
| `0x2` | blocks sight | `0x200` | item |
| `0x4` | missile barrier | `0x400` | object |
| `0x8` | no player | `0x800` | door |
| `0x10` | preset | `0x1000` | no path (a unit's centre) |
| `0x20` | blank | `0x2000` | pet centre |
| `0x40` | missile | `0x8000` | corpse |
| `0x80` | player | | |

A subtile no loaded room holds answers with the asked bits of `0x27` (the four tile bits; **D2R masks them**,
`0x366250`, classic returned every asked bit). A plus check whose centre has no room still answers every asked bit.

### 2.2 Move tests per mover

| Mover | Own bit | Move test | Default path type | Max steps |
|-|-|-|-|-|
| player | `0x80` | `0x1c09` (wall, no player, object, door, no path) | 7 straight | 73 |
| monster | `0x100` | `0x3c01`; `0x3401` with the open-doors flag | 2 toward | 14 |
| flying monster | `0x100` | `0x1804` (barrier, door, no path) | 2 | 14 |
| wraith-type (pattern 5) | `0x100` | `0x0804` | 2 | 14 |
| missile | none | none | 4 | - |

- Monsters do not test `0x100` against each other; they only avoid another unit's centre (`0x1000`).
- The step widens `0x3401` back to `0x3c01`: a door-opening monster's path goes through a closed door, but its walk
  still stops at it.
- Other masks in use: `0x3e01` (town portal field end, size 3), `0x1c89` (act-change arrival), `0x805` (AI approach
  line), `0x804` (teleport and reach lines), `0x4` (missile barrier only).

### 2.3 Footprints

| Unit size | Pattern | Marks |
|-|-|-|
| 0 | none | nothing |
| 1-2 | plus | the subtile and its four neighbours; `0x1000` on the centre |
| 3 | 3x3 box | the box; `0x1000` on its inner plus |
| pattern 5 (wraiths) | plus only | no centre bit |

- A monster that may stand in town and has no interact flag uses the pet forms (centre bit `0x2000`).
- Objects mark a `SizeX` x `SizeY` box; other units a box of their size. A box of size `s` starts at `centre - s/2`
  (unsigned halving).
- Dead units keep their mark; on death the game swaps in a 3x3 box of `0x8000` (corpse).

### 2.4 Room lookups and checks

- The room of a subtile is looked up from an origin room, its near list only (`0x38dfb0`). A plus check finds its
  centre from the origin and the outer four from the centre's room (`0x366d40`); a box check finds its first corner
  from the origin, the rest from that room; a trace walks room to room; the step moves the unit's room after each
  frame. The near list of an active room only holds neighbours created so far, so answers at room edges depend on
  what is loaded.
- Pattern dispatcher `0x364c70`: 0 one word, 1/3/5 the plus (`0x366d40`), 2/4 the box (`0x366050`); any other
  pattern "collides".
- Move test `0x363500`: lift the footprint, test the destination, put the footprint where the unit ends up. The
  missile form `0x363450` only refuses on wall or barrier bits.
- Free-spot searches: `0x362f90` (size 2, warps and portals), `0x3632f0` (size 3, portal field end), `0x363370`
  (act-change arrival, size and mask passed in).

## 3. Line of sight and distances

- **Ray trace** (`0x363f20`): Bresenham whose error starts at 0 and moves the minor axis when it reaches the major
  delta (x is major unless `|dy| > |dx|`). Both ends are tested; it stops at the first masked subtile or one with no
  room.
- **Unit-to-unit line** (`0x351720`, used by the AI approach and by reach tests through `0x350a20`): both ends first
  move toward each other by each unit's size (at most 2) on the major axis (both on an exact diagonal); units whose
  `dx + dy` is below the sum of their sizes never block.
- **The finders' line walk** (`0x364700`): tests with the unit's pattern and move test and returns the last free
  subtile. **D2R change**: a minor-axis step that leaves no error is not tested, so the last free subtile stays one
  minor step behind where classic took the step.
- **Path estimate** (`0x3c5280`): `2 * major + minor` from 8 subtiles on, a small table below (the three nearest on
  an axis count 0).
- **Unit distance** (`0x325200`): the same table, minus one when either unit has size 3, plus one when either is
  below size 2; far apart, each axis first shrinks by `sizeA/2 + sizeB/2`. The AI's own distance (`0x596720`) takes
  monstats2 `SizeX` off each axis (floor 0), then larger axis plus half the smaller.

## 4. Paths

### 4.1 The compute (`0x340e70`)

1. A missile path (flag `0x40000`) of type 4 gets one point, its target.
2. A unit target at x or y 0 fails. The target becomes the unit's position: players and monsters lead by the path's
   speed along their facing; a door target is moved two subtiles to this side; the slack is 1 for units, 2 for
   objects and items.
3. Start and target need a room (type 17 excepted), the target within 100 subtiles on both axes and not the start.
   A monster that may not enter town fails a town target (town test `0x327850`).
4. The mover's footprint is lifted (also the target's, for types with flag `0x800`).
5. With flag `0x1000` a blocked target is pulled toward the start (`0x343920`): three walkers try fixed direction
   triples (`0x1d0bc70`) until one lands free. **D2R's table differs from classic in one entry**: on the down-right
   diagonal it tries east before south. Players then push a near target straight out.
6. The finder of the type runs (table `0x1d000c0`, type flags `0x1d002c0`, circle offsets `0x1d00308`); footprints go
   back.
7. Velocity and direction aim at the first point (`0x380fd0`); points under the unit are skipped; an all-under path
   stops the unit.

### 4.2 Finder types

| Type | RVA | What it does |
|-|-|-|
| 1 A* | `0x3f6930` | subtiles, 8 neighbours (cost 2 straight, 3 diagonal), at most 200 nodes; returns the best node by heuristic as turn points. With a unit target it first needs one free subtile at distance 2 around the target |
| 2 toward, 13 toward-finish, 3 | `0x3aacf0`, `0x3aa830` | line walk; if short by more than the slack, step by step with three preferred directions per octant, a point at every turn, never reversing, at most max steps |
| 5 / 6 circle | `0x3aacf0` with offset +-2 | the same with the preferred directions turned by 2 |
| 7 straight (players) | `0x3abc40` | toward; when short and the target is within 18 (`dx^2 + dy^2 < 325`), A*; toward again if A* fails |
| 8 knockback | `0x3aa870` | `dist/2 + 1` subtiles straight away, cut by the line walk |
| 9 leap, 11 client knockback | `0x3abd10`, `0x3aa840` | one point by the line walk |
| 12 back up | `0x3aa7a0` | circle walk with offset -4, then -2 (or +2) |
| 15 wall follow | `0x3f8660` | Bresenham line (max steps, 40 with a unit target); at the first block two walkers follow the obstacle until one rejoins the line ahead |
| 0 / 16 IDA*, 4/10/14 missiles, 17 analog | | not documented here |

Toward and wall-follow first walk the path the client sent with its walk message (path `+0x2b8`, count `+0x380`);
only players have one.

### 4.3 Repath (`0x380ee0`) and the step (`0x3812d0`)

- A server path counts its `distance` (`+0xc0`) down by the points walked; a monster gives up at 0 and is re-sent.
  Toward, toward-finish and wall-follow paths retry as toward or toward-finish, then wall-follow.
- The server steps a walking or running unit once a frame with a **float scale of 1.0** (`0x491ac0`; classic passed
  the integer 1024):
  1. a path without flag `0x20`, points or velocity stops the unit on its subtile centre;
  2. a point path that ran out short repaths; a unit target within the step count stops (`0x382c20`); a target that
     moved more than 5 subtiles from where the path aimed repaths as toward-finish;
  3. speed (`0x3822b0`): every fifth frame the velocity grows by the acceleration up to the maximum; per axis the move
     is `((int)(float(velocity << 10) * scale) >> 6) * direction >> 12`;
  4. move (`0x381c60`, walk `0x3827a0`): landing exactly on a point within one step; crossing subtiles one at a time
     through the move test, halving the step to fit; a collision stops the unit on the last free centre (players and
     flag-`0x10` units repath); at most 10 subtiles a frame;
  5. **D2R change**: a server monster with more than 15 points asks for a new walk message when its index reaches 8,
     23, 38, ... (15-point segments);
  6. **D2R change**: a player walking to a unit stops once in reach (`0x34c490`) when the target is a monster with
     the interact flag (town NPCs) or a player and both are in town.

### 4.4 Path record (offsets)

| What | Offset | What | Offset |
|-|-|-|-|
| precise x / y | `+0x00` / `+0x04` | velocity / previous | `+0xa0` / `+0xa4` |
| target / aimed target / final target | `+0x10` / `+0x14` / `+0x18` | maximum / acceleration | `+0xa8` / `+0xac` |
| room | `+0x20` | message segment | `+0xb8` |
| point index / count | `+0x30` / `+0x34` | dist / dist max / step num | `+0xbc` / `+0xbd` / `+0xbf` |
| unit / flags | `+0x40` / `+0x48` | distance | `+0xc0` |
| type / previous type | `+0x50` / `+0x54` | points (78 slots) | `+0xc8` |
| size / pattern / own bit / move test | `+0x58` / `+0x5c` / `+0x60` / `+0x64` | analog direction, magnitude | `+0x22c`, `+0x230` / `+0x234` |
| target unit | `+0x70` | client path | `+0x2b4`..`+0x381` |

## 5. Speed

- **Path velocity** (`0x350b40`): `max(percent, 25) * base / 100`, `percent` = stat 67 (velocity%, 100 at rest) plus
  the effective FRW. `base` is always the **walk** column `<< 8` (charstats `WalkVelocity` `+0x3c`, monstats
  `Velocity` `+0x68`), running or not. Running is faster only through stat 67: a running player carries +50
  (`100 * RunVelocity / WalkVelocity - 100`), a monster's AI sets its own bonus.
- **Analog moves** (static only): when a player moves by direction (path type `0x11`, set by the player walk/run start
  `0x429860` via `0x342750`), the percent is first multiplied by the magnitude at path `+0x234`. Nothing else writes
  that field.
- Monster velocity percent: 75 walking; the hostile AIs' velocity call (`0x4a7270`) adds +100 for most (175 seen),
  +50 for others, champion/unique mods more. Read the stat total, never hard-code 175.
- Seen in game: Act I monsters with `Velocity` 4 and 5 move at 768/960 walking and 1792/2240 running; a Barbarian
  town-walking at FRW 0/10/50/100/200 had velocity 1536/1674/2104/2457/2841.

## 6. Unit modes

### 6.1 Ids

| Player | Id | Monster | Id | Object | Id |
|-|-|-|-|-|-|
| DT death | 0 | DT death | 0 | NU | 0 |
| NU neutral | 1 | NU neutral | 1 | OP operating | 1 |
| WL walk | 2 | WL walk | 2 | ON | 2 |
| RN run | 3 | GH get-hit | 3 | S1-S5 | 3-7 |
| GH get-hit | 4 | A1 / A2 | 4 / 5 | | |
| TN town neutral | 5 | BL block | 6 | | |
| TW town walk | 6 | SC cast | 7 | | |
| A1 / A2 | 7 / 8 | S1-S4 | 8-11 | | |
| BL block | 9 | DD dead | 12 | | |
| SC cast | 10 | KB knockback | 13 | | |
| TH throw | 11 | SQ sequence | 14 | | |
| KK kick | 12 | RN run | 15 | | |
| S1-S4 | 13-16 | | | | |
| DD dead | 17 | | | | |
| SQ sequence | 18 | | | | |
| KB knockback | 19 | | | | |

- Dead = death or dead mode of the unit's type: players 0 and 17, monsters 0 and 12. Mode 12 on a player is a kick
  (alive). The is-dead test `0x34c2c0` also counts unit flag `0x10000`.
- Players in a town use TN/TW, outside NU/WL/RN.

### 6.2 What each mode schedules

- Players: WL, RN, TW and KB run on a recurring ModeChange event (the path steps each frame; running drains stamina;
  no stamina turns a run into a walk); A1, A2, SC, TH, KK, S1-S4 and SQ execute the used skill on animation flags 1
  and 2 (only while unit flag `0x40`, "server function not yet run", is clear). EndAnim: death becomes dead with the
  corpse, knockback becomes get-hit, the rest neutral.
- Monsters (policy record read by `0x448400`, "while moving" check `0x448950`): WL, KB, SQ, RN recurring; DT, NU, GH,
  BL, DD the flagged frames; A1, A2, SC, S1-S4 the flagged frames unless monstats2 allows the mode while moving. NU,
  S3, S4 and DD schedule nothing after the start.
- Monster mode change `0x4475c0`, argument record: `+0x00` mode, `+0x08` unit, `+0x10` target, `+0x18`/`+0x1c` x/y,
  `+0x20` direction, `+0x21` path type, `+0x24` velocity, `+0x28` distance. In order: drop the uninterruptable state
  (stop if dead), update the AI's last mode, set up a path for every mode but GH, run the mode start (DT is the only
  start that draws from the unit seed; S4 schedules a think 15 frames on; GH needs monstats2 mode bit 3, KB bits 3 and
  13), on refusal go neutral (`0x445a80`), then init the animation (`0x498f80`), delete the old ModeChange and
  EndAnim events (`0x48f820`) and schedule the new ones (`0x491240` or recurring `0x491490`).
- A frozen monster ignores every event but stat regen, traps, active states, periodic skills and state removal.

### 6.3 Player start rules (medium: classic rules, partly checked)

- A cursor item blocks every mode but death. Death, neutral, town neutral and dead always start.
- From A1/A2/SC/TH a new mode may start while `now <= EndAnim + 5`, or for get-hit/block (unverified: the `+ 5`
  window was not captured). From DT/GH/BL/DD never. S1 is refused for an Amazon, S3 for a Druid; SQ only when the used
  skill takes sequence input.
- A non-interruptible skill only starts from neutral; Concentration holds the mode; the same skill with re-enter
  (srvdofunc 67 / 76) may restart itself.

## 7. Animation timing

### 7.1 Rates (unit `+0x68`, i16, clamped 0..`0x7fff`)

Effective rate stat `v` with cap `c`: `v * c / (v + c)` (`0x3516c0`, caps at `0x1d00b60`: 120 for IAS, FCR, FHR,
FBR; 150 for FRW). Per mode, of the animdata speed unless stated:

| Mode | Speed |
|-|-|
| cast (SC; SQ through a cast sequence) | `min(100 + FCR', 175)` % |
| block | `(50 + FBR')` %, `(100 + FBR')` % with Holy Shield (unverified), at least 1 |
| get-hit | `(50 + FHR')` % |
| knockback | the base move speed; path velocity 4096 (unverified) |
| walk / run | `max(25, FRW' + stat 67)` % of the base move speed: players 213 walk and town walk, 101 run |
| attack | `IAS' + stat 68 + dual-wield adjustment` (-30 for a player SQ), clamped 15..175 |
| death, dead | the plain animdata speed |
| other | `clamp(stat 69, 15, 175)` % |

Stat ids: 67 velocity%, 68 attack rate, 69 other anim rate, 93 IAS, 96 FRW, 99 FHR, 102 FBR, 105 FCR. Matched in game
for every cast, get-hit, block, attack and walk start captured (several thousand starts over five classes).

**Monster walk/run bases** (monstats loader `0x3a7470`): walk is the WL animdata speed of the `BaseId` row's token in
its default weapon class; run is half that for classic monsters (rows below 410), the RN record's speed for expansion
monsters. A monster that is not its own base scales them by its `Velocity`/`Run` over the base's.

**Frame bonus** (`0x34b760`): players in A1/A2 (and S3/S4 dual wielding) start that many frames in: Amazon and
Sorceress 1 bare-handed, 2 with 1HS/1HT/STF/2HS/2HT, everyone else 0.

### 7.2 Event frames (`0x491240`)

- Position starts at bonus + speed, in frames << 8. Each game frame, every animation index reached is checked: flags
  1 (attack), 2 (missile) and 4 (skill) schedule a ModeChange with the flag as custom id and a running param; flag 3
  (sound) one with param 0. The record has 144 flag bytes.
- With no bonus: index 0 fires at now + 1, index `i` at `now + ceil(i * 256 / speed)`, **EndAnim at
  `now + 1 + max(ceil(frames * 256 / speed) - 1, 1)`**. Speed 0 gives EndAnim at now + 1 and nothing else. The usual
  breakpoint tables list `ceil(frames * 256 / speed) - 1`; the next mode can start in the EndAnim frame. Breakpoints
  therefore follow from section 7.1 without a table.
- Timing of the start: a player's own attack, cast or skill (set while client messages are handled) is scheduled one
  frame before the frame it shows in; modes set inside the event pass (every monster mode, a player's get-hit, block,
  knockback, death) in the same frame.

### 7.3 Sequences (SQ)

- monseq.txt rows are 6-byte `{u16 sequence, mode, frame, direction, event}`; the sequence number is the order in
  which a name first appears (the blank first row is 0).
- A unit in a sequence: first row at unit `+0x40`, precise count `rows << 8` at `+0x48`, position `+0x4c`, previous
  position `+0x50`, speed `+0x54` (a plain animation uses frame count `+0x64` and speed `+0x68`). Row lookup
  `0x3cb7d0`; the event of index `i` is row `i`'s event.
- A **player** in a sequence gets EndAnim one frame later (static only).
- A **monster** SQ is stepped each frame (`0x347960`): position += speed, wrapped at the count; the event
  (`0x351970` -> `0x3cb6c0`) is the last non-zero event of the rows passed, none on a wrap, so a slowed monster fires
  each event row once. It leaves SQ the frame after the step that uses the count up (end test `0x3476b0`). Speed: the
  "other" rate of the token's record, 256 without one.

### 7.4 animdata.d2

256 buckets, each a u32 count then 160-byte records `{char name[8]; u32 frames; u32 speed; u8 flags[144]}`; a name's
bucket is the sum of its upper-cased bytes `& 0xff`. A missing token gets the default record (2048 frames, speed 256;
see [monsters-spawn.md](monsters-spawn.md) "Modes").

## 8. The event queue

| Function | RVA | What |
|-|-|-|
| schedule | `0x48be80` | type < 15; a frame at or before now becomes now + 1; appended to its bucket |
| set event | `0x48b720` | schedule with the unit type's default callback |
| recurring | `0x48bfe0` | frame -1, prepended to the type's recurring list |
| run a frame | `0x48b5c0` | per queue: recurring list, then the frame's bucket |
| next event frame | `0x48b230` | smallest pending frame > 0 of (unit, type), 0 for none |
| delete | `0x48b7a0` | predicate delete; frees through `0x48cc10` or marks a running timer |
| unit's timer list | `0x48fe50` | head at unit `+0x148` |

- Queue object at game `+0x188`; unit type -> queue by the byte table `0x1d1d578` (player 0, monster 1, missile 2,
  object 3, item 4, tiles none). 64 buckets per queue (heads `+0x08`, tails `+0xa08`, index `queue * 64 + frame % 64`),
  recurring list at `+0x1408 + queue * 8`.
- **Order per frame**: queues missile, player, monster, object, item; within a queue the recurring list (newest
  first) then the bucket in insertion order. A recurring event added during the run waits for the next frame.
- Timer record: `+0x00` type (u8), `+0x04` frame, `+0x08` unit, `+0x18` custom id, `+0x1c` param, `+0x28`/`+0x30`
  bucket links, `+0x38` next on the unit, `+0x48` callback.
- Types: 0 ModeChange, 1 EndAnim, 2 AiThink, 3 StatRegen, 4 Trap, 5 ActiveState, 6 FreeHover, 7 MonUMod, 8
  PeriodicSkills, 9 PeriodicStats, 10 AiReset, 11 DelayedPortal, 12 RemoveState, 13 UpdateTrade, 14
  RemoveSkillCooldown.
- **D2R change** (static only): scheduling an AiThink for a monster in the uninterruptable state (`0x36`) clears the
  state (`0x48e820`), and a monster that is then dead gets no think.
- A plugin that frees a unit must remove its events, recurring ones included; a stale recurring walk event moves
  whatever reuses the id.

## 9. Messages

| Id | Size | Fields / use |
|-|-|-|
| `0x67` | 22 + 4n | monster walk to a point: guid, action, target, current position, path type (7, or 1), velocity byte (stat 67 clamped), dist max at byte 19/20, point count, points (the start left out, the end included, at most 15: the current segment) |
| `0x68` | 23 + 4n | monster walk to a unit |
| `0x69` | 12 | DT / DD: guid, action 8 / 9, x, y, mode data, hit class |
| `0x6b` | 16 | mode at a point: guid, action, target x/y, mode data, hit class, current x/y |
| `0x6c` | 16 | mode at a unit: guid, action, target type, target guid, mode data, current x/y |
| `0x6d` | 10 | monster neutral / stop: guid, x, y, life |
| `0x0d` | 13 | player stop / get-hit / walk start: unit type, guid, action, x, y, hit class, life |
| `0x0f` / `0x10` | | another player's walk relayed to its viewers, to a point (walk 1, run `0x17`) / to a unit (walk 0, run `0x18`), with its current position; the walker gets no echo (observed) |

- Monster action table `0x1d3fec0`: 16 rows of six u32 `{use target, use path end, send path, send position, action
  to a point, action to a unit}`. WL, KB and RN send the path messages; circle paths go out as type 1 (`0x67`) or 2
  (`0x68`), the server knockback as 11. Builders `0x47baa0` (`0x67`) and `0x47b1e0` (`0x68`).
- Player action table `0x2399e28`: 16-byte rows `{action to a point, action to a unit, builder}` per mode (static
  only).
- A client that first sees a monster mid-walk gets `0xac`, `0xa8`, then a `0x67` with the rest of the walk; one in
  S1/S2 gets its `0x6b` (observed).
- Town NPC walks seen in game take about `ceil(length * 64 / (3 * Velocity))` frames per straight segment (fitted,
  observed).

## 10. Pitfalls

| Change | What happens | Do this |
|-|-|-|
| A plugin moves a unit by writing its position | footprint bits stay at the old subtile, the room is not updated (unverified) | move through the teleport `0x491ca0` |
| Hard-coding a monster's run speed from `Run` | path velocity always uses the walk column; `Run` only changes stat 67 through the AI | change `Velocity` or the AI's velocity bonus |
| New monster `Code` without animdata | default 2048 frames: attacks never end | add the record ([monsters-spawn.md](monsters-spawn.md)) |
| Breakpoint tables copied from classic | same formulas, but one less than the engine's EndAnim offset | compute from section 7 |
| Replaying headings with a classic table | `+y` and other directions differ by one | use D2R's float heading |

Open: IDA* and the missile and analog (17) finders; the unit update order within a frame (players first, then
monsters, is a model that fits most cases); the knockback speed and Holy Shield's block rate; player sequence end
timing in game.
