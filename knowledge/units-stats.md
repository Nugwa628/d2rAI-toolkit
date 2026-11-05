# Units, stat lists, states and party records (D2R 3.3 build 93847)

How the server makes, numbers, seeds and frees units, how a unit's stats and states are stored, and the per-player
party and relation records. Static reading of build 93847 (image base `0x140000000`, RVAs for this build only) with
classic 1.10-1.14d code as the guide; confidence per row. The stat ops (derived stats) are in
[combat-formulas.md](combat-formulas.md), the stat and state messages in [network-messages.md](network-messages.md),
the column side of itemstatcost and states in [tables.md](tables.md), other addresses in
[d2r-93847-addresses.md](d2r-93847-addresses.md).

## 1. The unit header

| What | Value (build 93847) | Confidence |
|-|-|-|
| `unit+0x00` | u32 type: 0 player, 1 monster, 2 object, 3 missile, 4 item, 5 tile (room tile / warp) | high |
| `unit+0x04` | u32 class (charstats / monstats / ... row), as the allocator stores it | high |
| `unit+0x08` | the unit id (guid). The address registry's older row reads a class id here; see the note there | medium |
| `unit+0x28` / `+0x30` | unit seed pair / the low word the game seed gave the unit ("init seed") | high |
| `unit+0xc0` / `+0xc4` | zeroed at allocation; probably owner type / owner id | medium |
| `unit+0x158` | link in the game's id hash chain | high |
| Flags | `0x10` "init seed set", set by the allocator (`0x34e500`) | high |
| Extended flags | `0x04000000` on every server unit; `0x100` deleted; `0x20000` remember the last attacker (read back by `0x48fd20`) | high |
| Path | players, monsters, missiles: dynamic path; objects, items, tiles: static path | high |

## 2. Allocation and ids

The allocator `0x4905a0` takes a parameter block `{+0x00 u32 type, +0x04 u32 class, +0x08 i32 x, +0x0c i32 y, +0x10
room, +0x18 u16 flags, +0x20 u32 guid}`. Flag 1 = link into the game lists; flag 2 = a monster keeps the guid at
`+0x20` and its type's counter does not move. Players are allocated through `0x490410`.

1. **Refusals**: a player class above 7; a monster whose monstats row is not `enabled`.
2. **Seed** (section 3).
3. **Id**: one counter per type at `game+0x144 + type*4`, zeroed by game create, so each type's first id is 1. Next
   = old + 1; after `0xfffffffe` it wraps to 1, `0xffffffff` is never given. Ids are not reused within a game.
4. **Per-type setup** callback (players: `0x48cdb0` case 0 -> `0x4252c0`: mode, path, inventory, stats, skills), then
   flags `|= 0x10`, list node 11 (`0x34f2c0`), extended flags `|= 0x04000000`, `+0xc0`/`+0xc4` = 0.
5. **Link** (flag 1): tiles go on one list at `game+0x3630`; every other type on one of 128 chains picked by
   `0x4925b0(type, game, id)` from `id & 0x7f`. Each chain is sorted by id, highest first; a duplicate id asserts.
   Lookup `0x48fe80(game, type, id)` walks the chain.

**Removal** `0x48faa0` (same order as classic): unlink, free the type data, events and messages, free the unit last
(`0x349250`, which starts in the stat-list code `0x2f40d0`). It frees at once and checks nothing: a remove callback
that frees another unit whose stat list is being detached reads freed memory. Vanilla callbacks only toggle states
and refresh passives, and units removed during a frame are marked deleted (`0x100`) instead. A plugin that frees
units from a stat-list callback must defer the free to the end of the frame. (high for the order, medium for the hazard)

## 3. Seeds and draw order

The game seed is `{lo, hi}` at `game+0x1a8`/`+0x1ac`; one step is `lo * 0x6ac690c5 + hi` (64-bit, new lo = low half,
new hi = high half; [map-generation.md](map-generation.md)).

- **Every non-player type** in the allocator: InitSeed `0x367100` (`{1, 666}`), one game-seed step, the new lo to
  `unit+0x30`, unit seed `{lo, 666}` (`0x367110`). **Items** take a second step for the item start seed `{start, 666}`.
- **Players** leave the allocator with `{0, 0}`; the creation code seeds them right after with the unit InitSeed
  `0x490b00(unit, &game+0x1a8)`, the same single step. Nothing draws between the allocator and that call. The seed
  depends only on how many draws came before (not on guid, act, time or character).
- **Six callers**, all making player-type units: the player (`0x41ff50` from `0x420070` new / `0x420c50` from a save;
  `0x531180` from `0x5355b0`, `0x531e70`, `0x532630`), corpses read from the save (`0x41fb20`, `0x531af0`), one body
  per shared-stash tab (`0x52f8d0`, from the stash loader `0x52fdb0` and `0x530590`; stat `0xb8` = tab number, stat
  `0xf` = its gold; padded with empty bodies up to the tab count `0x388c70`), and a dead body (`0x42c110`, mode
  `0x11`, state `0xba`). Player creation itself is: client into the player data (`0x4911a0`), the name (`0x34eba0`),
  InitSeed, anim mode 1 (`0x34e5f0`).
- **Order of draws in a fresh game** (checked against 236 spawn dumps): 4 at game create (monster seed `0x5098b0`,
  `0x5125a0`, the NPC seed `0x502780` at `game+0x5650`, `0x513ee0`), then per player one draw for the player, one per
  stash-tab body (six on a fresh character) and two per item. A level-1 host with 8 items: 27 draws before the first
  room population.
- A rejoin makes a new player unit with the next id and a fresh draw. The act change (`0x487d20`) never reseeds a
  player.

## 4. Stat lists

| What | Value (build 93847) | Confidence |
|-|-|-|
| Key | `stat << 16 \| layer` (layer = the param, for example a skill id); entries sorted by key | high |
| Fixed point | life, mana, stamina and their maxima (stats 6-11) are 8.8 (`ValShift` 8): `40 << 8` = 40 points | high |
| Hard-coded ids | 0-15 (strength .. goldbank); 6/8/10 current, 7/9/11 maxima | high |
| Unit's own list | extended (flag `0x80000000`): base stats, full stats, the keys changed since the last send, state and change bits, the chain of attached lists | high |
| List flags | `0x1` basic, `0x2` timed (new length), `0x4` temp only, `0x8` buff, `0x20` curse, `0x40` magic, `0x80` overlay, `0x200` toggle, `0x800` convert, `0x2000` parked (set), `0x200000` item ex, `0x20000000` permanent | high (classic values, kept) |
| Store / update / propagate | `0x2f9db0` / `0x2fa430` / `0x2fa200` (up the parent chain) | high |
| Change record | `0x2f9bb0`: marks the key for the next send; skips stats 6, 8, 10, 13, 14 and stats without `Saved` | high |
| fMin clamp | `0x2f9b10`: on an extended list of a player or monster, a total below `MinAccr` is stored as `MinAccr << ValShift` | high |

Attaching a list to a parent (classic PostStatToStatList): expired lists are dropped first; a list already in the
chain is not added again; a temp-only list becomes a timed list; a parked list adds nothing; with "reset" all its
stats reach the parent, without it the list turns dynamic and its damage-related stats stay out. Detaching (classic
ExpireStatList) takes the stats off the parent and runs the list's remove callback. Timed lists expire when the
frame reaches their expire frame.

- `keepzero`: a 0 value stays an entry; without it the entry is removed.
- `maxstat` is not enforced by the list: capping current life at max life is the caller's job.
- **Item op stats after a level change** (op 4/5, for example defense per level) are recomputed only when the item's
  list is posted again; classic's level-up ends by re-posting the player's items. A plugin that changes a level must
  do the same or item per-level stats stay stale. (medium)

## 5. States on a unit

| What | Value (build 93847) | Confidence |
|-|-|-|
| Storage | bits in the unit's extended list, `(count + 31) / 32` blocks, at most 16 (asserted at `0x3352b0`) | high |
| Max id | 511 rows (ids 0-510): `0x1ff` ends the all-states stream | high |
| Has state | `0x3351b0` answers only for players, monsters and missiles; other types can hold the bit but read as off | high |
| A state's list | found by `0x2f5940`: first among attached lists, then parked ones | high |
| Change bits | a real toggle sets the state's change bit; the senders read and clear them each frame | high |

The server's pattern for a timed state (classic MissMode): make a list (flags 2, expire frame = now + length,
owner type and id), set the state, set a remove callback that turns the state off, post the list with reset, toggle
the state on. Applying the same state again moves the expire frame to the new value, even earlier; cold is the
exception (extends only). `remhit` states are removed when the unit is hit; `hide` is not read by either server
sender (medium).

## 6. Party and relation records

| What | Value (build 93847) | Confidence |
|-|-|-|
| Relation list | per player at player data `+0x88`, newest first: `{guid, u16 flags, u64 hostileUntil, u8 state}`; new record `0x5077c0` | static only |
| Flags | 1 loot, 2 ignore, 4 squelch, 8 hostile | static only |
| State (A about B) | 0 none (A can invite), 1 B is in a party, 2 B invited A, 4 A invited B | static only |
| Party ids | start at 3, skip ids in use, wrap as a u16 back to 3; `0xffff` = no party on the wire | static only |
| Member add / new party | `0x506980` / `0x506b30`: newest first, broadcast `0x8d`, hostility cleared with the members | static only |
| Leave | `0x5070b0`; a party with one member left is disbanded. There is no kick | static only |
| Reset pending invites | `0x5078e0` | static only |
| Player iteration | `0x490d00`: walks the `guid & 0x7f` buckets, skips player-type units in state 7 (corpse) and `0xba` (stash-tab bodies) | static only |

- **Hostile** (`0x5e39a0`): the declarer must be in town and both above level 8. On sets `hostileUntil = now +
  60 000 ms`; on again before that sends sound `0x14` and `0x5a` type `0x10`. Declaring on a party member applies to
  every member above level 8 but stamps only the named record, so the 60 s delay can be dodged by naming another
  member. Declaring on a fellow member also makes the declarer leave the party. The declarer's town portal is removed
  (`0x424c20`).
- **Invite / cancel / accept** need states 0 / 4 / 2. Accept creates a party (the inviter first) when the inviter has
  none, then refreshes every player's relation with both (`0x5e3d20`).
- **Gold share** (`0x5074b0`): n = living members in the picker's area. When n > 1 each other member in the area,
  **dead or alive**, gets `min(amount / n, its room)`; the picker gets the rest up to its room; the excess is dropped.
  Classic capped every share by the picker's room. Quirk: with dead members counted out of n but paid, the picker's
  share can go negative (its gold is floored at 0). Nobody shares while the picker is dead.
- **Experience sharers** (classic ComputePartyExperience): living members in the killer's area within squared
  distance 6400 of the monster. The split is in [combat-formulas.md](combat-formulas.md).
- **Player count** `0x4251e0`: the living players, or a larger virtual count for game types 1..3. For a monster in
  state `0xbd` (terror zone, test `0x3af200`) `0x542f40` uses `max(0x509430(game, level), count)`, where
  `0x509430` reads the game's terror-zone list (`0x489b70`, `game+0x5820`) and a value inside the loader core.
- Distance used by the party sync: `0x324fc0`, per axis `max(0, |d| - size1/2 - size2/2)`, then `max + min/2`
  (players have size 2).

## 7. Timing

| What | Value (build 93847) | Confidence |
|-|-|-|
| Server frame | 40 ms, 25 frames a second; counter `game+0x170` | high |
| Party life/position sync | every 30 frames per player | static only |
| Hostile declaration delay | 60 s per (declarer, target) record | static only |
| Periodic save push | 30 s timer at `game+0x5800`, then `0x41e0d0(game, 0)` when some client has a player | high (timer), medium (save) |
| Empty game | ends 300 s after the timer `game+0x57e8` starts, or at once when closing; the sweep `0x406e40` first runs a per-client output step `0x408ab0` and, for in-game clients, the vitals sender `0x4f31a0` (counter at `client+0x2bc`) | high |
| Time of day message `0x53` | every 2176 frames (87 s) | observed |
| Heartbeat / transport timeout | client every 5 s / 15 s silent drops the connection | observed |
