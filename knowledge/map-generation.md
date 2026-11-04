# Map generation (DRLG), room streaming and travel

How D2R 3.3 builds its levels, what drives each step from the tables, and what a mod that adds or changes areas
runs into. Observed in **D2R 3.3 build 93847** (server and client halves of the same image). Every address is an
RVA for **build 93847** only: verify it on any other build ([engine-re.md](engine-re.md)). Hard limits on levels
(36 level types for the automap, `Layer` < 100, automap cels, `Position`, same-act portals) are already in
[engine-limits.md](engine-limits.md) "Levels and areas"; the automap and level-entry addresses are in
[d2r-93847-addresses.md](d2r-93847-addresses.md) "Levels and automap". This file does not repeat them.

Confidence labels: **high** = read in D2R's code and seen in game; **medium** = one of the two, or the classic
structure with a partial D2R check; **low** = carried over from classic without a D2R check. Untagged facts are
high.

## 1. The big picture

- **Both sides generate the map.** The server rolls a map seed per game and sends it to the client in `0x03`
  (NEWACT). Server and client each build the act's level layout, rooms, preset maps, mazes, outdoor areas and tiles
  from that seed and the tables, and they must agree tile for tile. A server and a client with different tables
  (a mod mismatch) get different maps from the same seed, with asserts or rubber-banding as the result.
- **Units are server-only.** Objects (waypoints, shrines, chests, doors), monsters, warp tiles and items exist on
  the server and are sent as units. Only the layout is generated twice.
- **Levels and rooms are built lazily.** The act alloc places every level of the act; a level's rooms are built
  when it is first needed (server: a player approaches; client: the first `0x07` NEWROOM for it arrives). A room's
  tile grid is built when the room is activated.
- **Generation is order-independent** (medium-high). Every level draws only from its own seed and its rooms'
  seeds, and its seed is reset when it is initialised, so it does not matter which level is built first. (Tile
  picks on shared room borders are the exception, section 6.)

## 2. The RNG and the seed chain

The RNG is classic D2's 64-bit multiply-with-carry, unchanged in D2R:

- state `{u32 lo, u32 hi}`; step `v = lo * 0x6ac690c5 + hi`, `lo = low32(v)`, `hi = high32(v)`; the output is the
  new `lo`. The step is inlined everywhere (over a thousand copies of the multiplier in `.text`).
- `roll(n)`: no step and result 0 when `n <= 0` (signed); otherwise one step and `lo & (n-1)` for a power of two,
  `lo % n` (unsigned) otherwise. Both branches step exactly once. Bare masks (`& 1`, `& 3`, `& 7`) and constant
  moduli (`% 7`, `% 100`, `% 1000`) are written straight into the callers.
- Seed init: `0x367100` sets `{1, 666}`, `0x367110` sets `{x, 666}` (build 93847).
- `0x367120` (build 93847), the "fresh random value" function, **changed in D2R**: it ignores its argument and
  returns a high-resolution clock value masked to 31 bits. Only fresh seeds come from it; nothing that must match
  between server and client does.

| Stream | Seeded from | Lives at (build 93847) | Drives |
|-|-|-|-|
| Game seed | fresh value, stepped once (or the fixed seed) | `game+0x1a8` | object seed, unit seeds, most game rules (server) |
| Map seed | fresh 31-bit value, the fixed seed, or (single player) the save's | `game+0x12c` | the whole DRLG; sent in `0x03` |
| Object seed | the object control's allocator | `game+0x130` | object and shrine choices; also sent in `0x03` |
| Act seed | `{mapSeed, 666}`, stepped once at act alloc | DRLG `+0x000` | `startSeed`, Act II tombs, Act III jungle link, level connections |
| `startSeed` | the `lo` of that first step | DRLG `+0x860` | base of every level seed |
| Level seed | `{startSeed + levelId, 666}`, at level alloc **and again** at level init | level `+0x1e4` (level id `+0x1f8`) | preset file pick, maze graph, outdoor grid, one draw per room alloc |
| Room seed | `{step(levelSeed), 666}` at room alloc; `initSeed = step(roomSeed)` | room `+0x30`, `initSeed` room `+0x08` | tile rarity picks, substitutions, preset unit choices |
| Room grid reset | `{initSeed, 666}` each time a room's grids are built | room `+0x30` | makes tile picks independent of build order |
| Active room seed | from the room seed at activation | active room | monster and object population (server) |
| Unit / item seeds | a game-seed step per unit; the item's own 32-bit seed | `unit+0x28`; item data | AI, skills, drops; affixes (saved with the item) |

**Fixing the seed for tests.** Game create (`0x4049d0`, build 93847) reads a fixed-seed global at `0x238ab80`
(build 93847; `-1` = off). When set, `mapSeed = S`, `gameSeed = {S, 666}` and `game+0x134` = 1. No writer exists
in the game's own code; a test plugin that writes it before the game is created gets deterministic maps (observed).
In single player the save loaders replace the map seed with the save's own when the character's act byte has bit
`0x80` and no fixed seed is set (classic behaviour; the gate on the `-resetofflinemaps` option is low confidence).
A networked (TCP/IP) host is expected to roll a fresh seed per game: not confirmed by capture.

## 3. Act and level layout

### 3.1 The act (act alloc `0x326ba0`, build 93847)

`DUNGEON_AllocAct` (`0x2ef1c0`, build 93847) takes `(bank, act, mapSeed, isClient, ...)` and calls the act alloc,
which in order:

1. steps the act seed once: that `lo` is `startSeed`;
2. Act II: the staff tomb = 66 + step % 7, then the boss tomb = 66 + step % 7, redrawn until they differ (the
   redraw rule is from classic; the observed seeds do not separate it from redrawing the boss tomb only);
3. Act III: the jungle link = step & 1;
4. lays out the act's level connections (where each level sits, the outdoor links between neighbours); position
   matches `0x3f0130` (build 93847);
5. on the server, builds the town level. A client act (`isClient` = 1) skips the town at alloc and sets the DRLG's
   "on client" flag, which adds the automap reveal flags; the draws are otherwise identical.

The first byte argument of every DRLG and table getter is the **data-bank byte** (`game+0x106` on the server, the
`dataFlag` of [d2r-93847-addresses.md](d2r-93847-addresses.md)); `0x300a90(byte)` (build 93847) returns the table
set for it. Generating with a different bank than the client uses gives a different map (medium on what the bank
means beyond the expansion/table-set selector).

### 3.2 Levels (alloc `0x3267c0`, init `0x3271c0`, build 93847)

A level's seed is set to `{startSeed + id, 666}` at alloc; a preset level draws its file there. Init resets the
seed to the same value before generating, so every generator starts from a fresh level seed. `levels.txt`
`DrlgType` selects the generator:

| DrlgType | Kind | Generator (build 93847) | Driven by |
|-|-|-|-|
| 1 | maze (caves, dungeons, crypts, sewers, temples) | `0x3e69e0`, with a post-pass switch by level type | lvlmaze `Rooms`/`Rooms(N)`/`Rooms(H)`, `SizeX`/`SizeY`, `Merge`; lvlprest rows of the level type |
| 2 | preset (towns, fixed maps) | `0x3dd3c0` | lvlprest row of the level (`0x32c410` row getter): `Files` + `File1-6`; the file is `roll(Files)` on the level seed |
| 3 | outdoor / wilderness | `0x3ee320` (outdoor info alloc `0x3ee6e0`) | the level's rect and its outdoor neighbours, then a hard-coded per-act pass |

Under D2RLoader the outdoor init entry and some table-getter call sites are routed through the loader's own code
(observed); for vanilla data they behave like the originals. Before hooking a DRLG function, check where its
callers actually jump ([engine-re.md](engine-re.md) section 5).

### 3.3 Rooms (alloc `0x361140`, build 93847)

Every DRLG room ("RoomEx") alloc draws one step from the level seed (the room seed), and one step of that is the
room's init seed. Rooms are prepended to the level's list. A room that is allocated and freed again (trial
placements in mazes) keeps its draws drawn, so every trial costs one level-seed step. D2R also stamps a
process-wide serial counter on rooms and preset maps; it changes nothing in the layout.

### 3.4 Maze levels (DrlgType 1)

1. A first chamber of lvlmaze `SizeX` x `SizeY` is centred in the level rect.
2. **Growth**: until the room count reaches lvlmaze `Rooms` for the difficulty (x3 in the Act II staff tomb, x2 in
   the boss tomb), pick a chamber (`roll(count)`), then a direction from that chamber's own seed (`& 3`); a new
   chamber that overlaps is freed.
3. **Merging**: each chamber touching the new one side by side draws once from its own seed; `step % 1000` below
   lvlmaze `Merge` (per mille) links them.
4. **Special chambers** (entrances, exits, boss rooms) per level type: replace a plain chamber or attach a new one.
5. **Themed chambers**: about a fifth of the plain chambers get a themed lvlprest def (def + 15).
6. Each chamber becomes a preset map (file roll on the level seed), its rooms are built, and the chamber is freed.

Hard-coded by level or level type (code, not data): the maze post-pass switch runs for level types 3, 4, 7, 8,
10, 13-15, 17-19, 22-25, 28 and 32-35, and **any other type asserts** (medium on the exact list); the Barracks are
moved against the Outer Cloister and the River of Flame against the Chaos Sanctuary; the Act II
summoner's sanctuary (level 74) with its four arms of 15 chambers; the Act II tomb range 66-72; the Act V lava presets. A new maze level must reuse one of those
level types.

### 3.5 Outdoor levels (DrlgType 3)

The level is a grid of 8x8-tile cells (D2R widened the cells to 8 bytes; only 32 bits are used). In order:

1. **Vertex ring** around the level box, split where a neighbour level's edge starts or ends; link vertices are
   flagged (and flagged again when the neighbour is a preset level).
2. **The act pass** (build 93847): Act I `0x3fac10`, Act II `0x3fbd10` (also level 134), Act III `0x3fcbe0`,
   Act IV `0x3fdc00`, Act V `0x3fe2e0`. They place borders and cliffs, rivers, cave and tomb exits, town
   transitions, waypoints, shrines, special presets and dirt paths, each with a fixed draw order **keyed by level
   id**. Act III jungles and Kurast, Travincal, the Chaos Sanctuary's 5x5 layout and the Act V siege strips,
   barricades, ravine and prisons are all special cases in code.
3. **Secondary borders and substitutions** from lvlsub rows (by `Type`, `BordType`, `Prob0-4`, `Trials0-4`,
   `Max0-4`).
4. **The room loop**, row-major: a cell with a picked file and a nonzero def becomes a preset map; any other
   non-blank cell becomes an 8x8 outdoor room.

D2R's outdoor code matches classic draw for draw (no step, placement, file or flag differs in build 93847; only
inlining and wider cells). A preset that a required placement (an exit, the Hell portal, a waypoint) cannot fit
is **fatal** on the server; fewer Act V prisons than three only logs a warning.

### 3.6 Preset maps

The preset build allocates the map's rooms in 8x8 chunks row by row (or one room) and then scans the `.ds1` for
its units. `.ds1` files are cached by file name (`0x3dcf10`, parse `0x3dfae0`, build 93847) and shared by every map
that uses them.

## 4. Tables and files that drive generation

| Table / file | Columns that change maps | Notes |
|-|-|-|
| levels.txt | `DrlgType`, `LevelType`, `SubType`/`SubTheme`/`SubWaypoint`/`SubShrine`, `SizeX`/`SizeY` and the `(N)`/`(H)` sizes, `OffsetX`/`OffsetY`, `Depend`, `Vis0-7`, `Warp0-7`, `Position`, `Layer`, `WarpDist`, `Waypoint` | sizes are per difficulty: test all three. D2R-only columns (`CompletionTotalRoomsOverride`, `LevelGroup`, `PreventTownPortal`, `rangedspawn`, `*IsInside`) showed no effect on the layout |
| lvlprest.txt | `Def`, `LevelId`, `Populate`, `Logicals`, `Outdoors`, `Animate`, `KillEdge`, `FillBlanks`, `SizeX`/`SizeY`, `AutoMap`, `Scan`, `Pops`, `PopPad`, `Files`, `File1-6`, `Dt1Mask` | **the row index is the def id** mazes and outdoor code ask for by number: never reorder or insert. 3.3 appends two rows after the classic ones; defs 0-1089 keep classic numbering |
| lvltypes.txt | `File 1..32`: the `.dt1` set of a level type | `Dt1Mask` bits in lvlprest/lvlsub pick from these |
| lvlmaze.txt | `Level`, `Rooms`/`Rooms(N)`/`Rooms(H)`, `SizeX`/`SizeY`, `Merge` | `Merge` is per mille |
| lvlwarp.txt | `Id`, `SelectX/Y/DX/DY`, `ExitWalkX/Y`, `OffsetX/Y`, `LitVersion`, `Tiles`, `Direction`; D2R adds `NoInteract`, `UniqueId` | warp tile placement, click area and the walk-out point |
| lvlsub.txt | `Type`, `File`, `CheckAll`, `BordType`, `GridSize`, `Dt1Mask`, `Prob0-4`, `Trials0-4`, `Max0-4` | outdoor substitutions; rows of one `Type` must be contiguous |
| objpreset.txt, monpreset.txt | resolve `.ds1` preset unit numbers per act | server units |
| `.ds1` / `.dt1` under `data/global/tiles` | preset grids and units; tile records (style, sequence, orientation, **rarity**, flags, subtile collision) | the classic tile files are D2R's logic data even in HD; HD art is client-only |

3.3 has 139 levels.txt rows, ids 0-137: level 137 is new (Act V, preset). Code and plugins must take the level
count from the table, not assume 137 levels.

## 5. Tiles and rooms

### 5.1 Activating a room (build 93847)

The room activator is `0x3289a0` (by position: `0x328900`, which finds the room with `0x327400`). Three steps, each
guarded by a room flag at room `+0x50`:

| Step | Function | Flag | What |
|-|-|-|-|
| DT1 load | `0x3f3970` | `0x1000000` | fills the room's 32 tile-library slots (room `+0xb0`): the lvltypes file for each set bit of the room's DT1 mask (room `+0x80`), then three fixed libraries (blank, invisible wall, warp) |
| preset units | `0x3de0e0` | `0x2000000` | preset rooms only: loads the map's `.ds1` on first use and adds its units, drawing from the activating room's seed |
| ensure room | `0x328fd0` | `0x100000` | builds the near list (`0x3608a0`) if missing, the cell grids (`0x3f38d0`), the tile records (`0x3f3930`), then the active room and its collision (`0x363a90`) |

The grid build (`0x3f38d0`) resets the room seed to `{initSeed, 666}`; outdoor grid rooms use `0x3f95e0` / records
`0x3f98c0`, preset rooms `0x3de420` / `0x3de6e0` (all build 93847).

### 5.2 Layers and the tile pick

- A room has floor, wall, shadow and roof records. Cell words come from the `.ds1` layers (D2R widens them to 64
  bits on load and remaps orientations of `.ds1` versions below 7). Cell bits: 0 wall, 1 floor, 2 border/LOS,
  3 enclosed, 7 layer above, 8-15 sequence, 16 fill LOS, 17 unwalkable, 18-19 layer, 20-25 style, 26 reveal hidden,
  27 shadow, 28 linkage, 29 object wall, 31 hidden.
- **The pick** (`0x3f3c60`, build 93847): candidates for (type, style, sequence) come from the room's libraries in
  slot order, up to 40; **within one library the last tile in the file comes first**. The pick draws
  `roll(sum of rarities) + 1` from the room seed (no draw when the sum is 0) and walks the list. A missing tile
  logs `FindHTile: Tile not found` and falls back to the first `(10, 0, 0)` tile, without a draw. So adding or
  reordering tiles in a `.dt1` changes which variant every room picks, and a rarity of 0 on every candidate always
  gives the first.
- **Doors and warps from tiles**: door and gate tiles on 37 hard-coded levels make door objects (a room-seed draw
  decides, so doors consume tile draws); hidden exit tiles with style below 8 add a warp unit and warp tiles at the
  lvlwarp offsets.
- **Outdoor grid rooms**: one room-seed draw per lvlsub row of the level's `SubType` keeps the row when
  `draw % 100 < Prob` for the theme and ORs its `Dt1Mask` into the room's mask. Then the waypoint, shrine and
  theme substitutions run (Trials `-1` means "shuffle every place, take the first that fits"). Act I also draws
  dirt paths.

### 5.3 Shared border tiles and build order

A cell on the border between two rooms is shared: the second room to be built finds the first one's record,
**steps its own seed once (new in D2R)**, may remap the wall type and re-picks the tile from the neighbour's seed.
So a border tile's look depends on which room was built first, which follows the player's route. Observed: about
30 % of preset rooms and a few outdoor rooms pick different border tiles when built in walking order instead of
level order, but **collision was identical in every room**. Expect cosmetic edge differences between two clients,
never walkability differences.

### 5.4 Collision

A room's collision grid is (w x 5) x (h x 5) u16 subtiles. Every floor, wall and roof record ORs in its tile's 25
subtile flags (stored bottom row first) plus flag-derived bits. Units add their own bits (`0x80`, `0x100`, `0x400`,
`0x1000`, `0x2000`: players, monsters, objects). A player is a size-2 footprint (the subtile and its four
neighbours) tested against mask `0x1c09`.

## 6. Room streaming: what the client is sent

| Id | Size | Fields | Use |
|-|-|-|-|
| `0x03` NEWACT | 12 | u8 act, u32 map seed, u16 town level, u32 object seed | client frees the old act and allocates the new one from the seed |
| `0x07` NEWROOM | 7 | u16 tile x, u16 tile y, **u16 level** | client builds that room from its own DRLG (classic sent 6 bytes with an 8-bit level) |
| `0x08` REMOVEROOM | 7 | same | room leaves view |
| `0x09` NEWUNIT | 11 | u8 type, u32 id, u8 class, u16 x, u16 y | warp and level tiles (unit type 5); a missile type is refused |
| `0x0a` REMOVEUNIT | 6 | u8 type, u32 id | unit or tile leaves view |
| `0x51` | 15 | object class, position, mode | preset and random objects |

The full message picture is in [network-messages.md](network-messages.md).

- **Load**: `0x03`, then `0x07` for the start room and for each room of its near list (which holds the start room
  again, so it is sent twice), then the warp tiles. A client exits fatally if `0x15` places the player before the
  start room exists, or if `0x04` STARTACT arrives while the player has no room.
- **Changing room** (one batch): `0x07` for each room of the new near list not in the old one (near-list order);
  for each room leaving view `0x0a` for its units and tiles, then `0x08`; then `0x09` for the new rooms' warp tiles
  (each with a new id). Crossing from one outdoor level into the next is just a room change, followed by the
  player's `0x7f` with the new level.
- **Units of a room** when it comes into view: monsters (`0xac`, then `0xa8` alignment, then `0x6d`), objects
  (`0x51`, and `0x0e` with the mode when its init set one), then warp tiles; newest first within each group.
- The server activates rooms around players and stops AI in rooms nobody has (classic behaviour, observed).
- **Wire level ids are 16-bit** in `0x07`/`0x08`. Other client messages that carry a level as a byte are
  unverified (see the portal row in [engine-limits.md](engine-limits.md)).

## 7. Travel: warps, waypoints, act change

- **Taking a warp tile.** The client walks up and sends C->S `0x40` with the tile's id. The server's warp handler
  (`0x4adbb0`, build 93847) only operates an enabled tile in nearby rooms **within 5 subtiles** of the player's
  server position; it does not schedule a walk. The reply: `0x07` for the destination room (even if known), `0x0d`
  for the player, `0x7f` with the new level, `0x15` at the arrival point (flag 1), then the room change of
  section 6.
- **Arrival point.** A free-coordinates search in the destination room around its warp unit: size-2 footprint,
  mask `0x1c09`, square rings out to 50 subtiles (left and right columns first, then top and bottom rows),
  smallest Manhattan distance wins. The player then walks to arrival + lvlwarp `ExitWalkX`/`ExitWalkY`. Warp
  destinations come from levels.txt `Warp0-7`; the destination lookup (`0x3daad0`, build 93847) first checks an
  act-level override list.
- **Warp tiles are only clickable outside town when the player has an alignment.** The client hovers a warp tile
  only when the player and the tile are not "allies"; a tile has alignment 0, and so does a player whose alignment
  was never set. The server sets it with `0xa8` (state 105 `alignment`, stat 172, value 2 for players) right after
  `0x59` and again after the placing `0x15`. A plugin or tool that builds or replays player units must send it, or
  the player can walk onto a cave mouth and never enter it. monstats `Align` 1 (NPCs) is sent as 2, `Align` 2 as 1,
  monsters get an empty list.
- **Waypoints** are preset objects in the `.ds1` (or placed by the outdoor pass or a waypoint substitution), sent
  as `0x51`. Their init (objects `InitFn` 17) sets mode 2 in a town and 0 elsewhere; torches (`InitFn` 8) are mode
  2, braziers (`InitFn` 14) mode 1.
- **Act change** frees the client's act and allocates the new one from a fresh `0x03` with the same map seed; the
  server's act join (`0x487d20`, build 93847) builds the server act if missing (`0x48aa50`), places the player and
  sends `0x03` (`0x47c220`). Portals link levels of one act only.
- **Movement checks** (server, build 93847; move handlers `0x4f3c70`, `0x4f6060`, `0x4fe1f0`, medium): a walk
  target more than 50 subtiles from the server position on either axis is dropped; a client position 46 or more
  away (unit distance `(2 * max + min) / 2`, `0x325370`) drops the move silently; more than 15 forces a resync
  (`0x15` with the server's position, flag 0, then a 15-frame cooldown). Walk messages carry at most 50 path points;
  more logs "bPathLen too long" and the path is dropped.

## 8. Functions worth hooking (build 93847)

| RVA | What | Note |
|-|-|-|
| `0x4049d0` | game create (seed code inside) | map seed `game+0x12c`, object seed `+0x130`, game seed `+0x1a8` |
| `0x238ab80` | fixed map seed global (`-1` off) | write before game create for reproducible maps |
| `0x2ef1c0` | DUNGEON_AllocAct `(bank, act, seed, isClient, ...)` | client copy built from `0x03` |
| `0x326ba0` | act DRLG alloc | `startSeed` at DRLG `+0x860`, jungle link `+0x864` |
| `0x3267c0` / `0x3271c0` | level alloc / level init | after init the level is laid out; good place to inspect or post-process |
| `0x361140` | DRLG room alloc | one level-seed draw each |
| `0x3dd3c0` | preset level generator | lvlprest row via `0x32c410` |
| `0x3e69e0` | maze generator | post-pass by level type |
| `0x3ee320` | outdoor GenerateLevel | act passes `0x3fac10`-`0x3fe2e0` |
| `0x3289a0` | room activator | then `0x328fd0` ensure-room |
| `0x3f38d0` | room grid init | resets the room seed |
| `0x3f3c60` | tile pick | rarity walk |
| `0x363a90` | collision grid build | |
| `0x3daad0` | warp destination | `0x32c980` lvlwarp row getter |
| `0x487d20` | server act join | sends `0x03` via `0x47c220` |
| `0x4adbb0` | C->S `0x40` warp handler | 5-subtile reach |
| `0x129930` / `0x129b80` | client `0x03` / `0x07` handlers | client act at `*0x2a233b0` (server acts: vector at `game+0x190`) |

## 9. Pitfalls for mods that add or change areas

| Change | What happens | Do this |
|-|-|-|
| Inserting or reordering lvlprest rows | defs are row indices that mazes and outdoor passes ask for by number: wrong maps, asserts, missing exits | append new rows only |
| A new maze level with a new level type | the maze post-pass switch asserts on an unknown type | reuse a vanilla maze level type (and keep types below 36 for the automap) |
| A new outdoor level | the outdoor pass is chosen **by level id**; an unknown id gets only the generic steps (or none) | prefer a preset level (DrlgType 2) with your own `.ds1`, or clone a vanilla outdoor level's role; a new outdoor generator needs a plugin |
| Changing `SizeX`/`SizeY` | per-difficulty sizes; required presets that no longer fit are fatal on the server (exits, Hell portal, waypoint) | test every difficulty and several seeds; leave room for the required presets |
| Changing lvlmaze `Rooms` | room count per difficulty; too few rooms can leave no place for the special chambers | test all three difficulties |
| Editing `.dt1` tile order or rarity | every room's variant pick changes (the last tile in the file comes first) | fine for a mod, but server and client must ship the same files |
| A `.ds1` whose substitution group count is wrong | D2R cuts the count to the whole groups in the file (a vanilla file declares 14 and holds 13), so rolls use the real count | keep the header's group count equal to the groups present |
| A missing tile for a referenced style/sequence | `FindHTile: Tile not found`, the fallback tile shows | ship every `.dt1` the level type and masks reference |
| Different tables on server and client | different maps from the same seed: rooms at wrong coordinates, DRLG asserts, position corrections | the mod's tables must be identical on both sides (D2RLoader's mod check) |
| Level count | 3.3 has ids 0-137 | read the count from levels.txt; never hard-code 137 or 136 |
| Debug force-preset globals | the outdoor passes check three debug globals (`0x2a9a5cc`, `0x2a9a5c4`, `0x2a9a5c0`, build 93847) that force presets | leave them at 0 |

Open (not verified in build 93847): which `.ds1` preset units the client creates by itself ("client presets"),
what level 137 and other 3.x additions add as hard-coded cases, how the object seed is used by the per-character
map memory, and the meaning of the bank byte for DRLG beyond the table set.
