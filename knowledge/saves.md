# Saves (.d2s v105) and the D2RLoader sidecar

Edit test characters only while the game is closed, and **back up first** (copy the `.d2s` and its `.d2rl` to a
folder of your own, e.g. `Saved Games/Diablo II Resurrected/<mod>-manual-backups/<name>-before-<change>/`).

Save folder for a mod with `"savepath": "<Mod>/"` in its modinfo.json: `%USERPROFILE%\Saved Games\Diablo II
Resurrected\mods\<Mod>\` (kept apart from retail saves). D2RLoader also writes its own backups to
`Saved Games\Diablo II Resurrected\D2RLoader Backups\`.

## Two formats: retail and D2RLoader extended

A `.d2s` written by D2R 3.3 is in one of two layouts. Check which one before decoding anything past the header.

| | Retail 3.3 (v105) | D2RLoader extended format |
|-|-|-|
| Version field (0x04) | u16 `0x69`, bytes 0x06-0x07 zero | bytes 0x06-0x07 read `RD` (u32 `0x44520069`); likely the extended marker (observed, not documented) |
| `gf` stat id / end | 9 bits / `0x1ff` | 16 bits / `0xffff` |
| `if` class skills | one byte per class skill, count in header byte 0x1a (30 for every retail class) | can be more than 30 per class |
| Item flag bit 19 (`0x80000`) | clear | set on every item seen |
| Byte after the item flags | none | 8 bits, `0x05` on every item seen |
| Item stat id / end | 9 bits / `0x1ff` | 16 bits / `0xffff` |
| Magic, rare and auto affixes | 11 bits | wider (16 seen for the auto affix; the rest not verified) |
| Set / unique id | 12 bits | not verified |
| Item code | Huffman | Huffman (a starter item failed to decode with the retail tree, so maybe not always) |

The extended item fields match the wide form D2R uses for item records on the network (flag bit 19, a format
byte 5, 16-bit stat ids, `0xffff` end; [item-format.md](item-format.md)). That is an inference from the matching
fields, not a documented fact. A tool that reads retail saves should **refuse** the `RD` marker rather than guess.

The rest of this file is the retail layout unless a row says "extended".

## Layout (retail v105)

All multibyte integers are little endian. Sections in file order:

| Offset | Size | Section |
|-|-|-|
| 0x000 | 403 | header (below) |
| 0x193 | 298 | quests: `Woo!`, u32 6, u16 0x12a, then 3 difficulties x 48 u16 quest words |
| 0x2bd | 80 | waypoints: `WS`, u32 1, u16 0x50, then 3 x {u16 `0x0102`, 14 bytes = 112 waypoint bits, 8 zero bytes} |
| 0x30d | 52 | NPC flags: u16 `0x7701`, u16 0x34, 3 x u64 "introduced", 3 x u64 "congratulated" |
| 0x341 | var | `gf` character stats, padded to a byte |
| next | 2 + n | `if`, then n class-skill bytes (n = header byte 0x1a) |
| next | var | `JM`, u16 root item count, the items |
| next | var | `JM`, u16 corpse count; per corpse 12 bytes (u32 0, u32 x, u32 y) and a `JM` item list |
| next | var | `jf`, then a `JM` item list only when a mercenary exists |
| next | 3+ | `kf`, a presence byte (0/1), then the iron golem's item if present |
| next | 6+ | `lf` section: u16 1, `lf`, presence byte, zero byte; if present a 0x5c-byte record whose first u16 is the length of the data after it. Written only for game type 3 (header 0xf8) |

Notes on the sections:

- **Quests**: 48 words per difficulty, indexed by quest state id (0 Act I intro, 1-6 the Act I quests, 7 Act I
  done, 8 Act II intro, ...). Each word is that quest's progress bits.
- **Waypoints**: 112 bits per difficulty in waypoint order (classic D2 used 39). Waypoint n is bit `n % 16` of the
  u16 at `+2 + 2 * (n / 16)` inside the 24-byte difficulty block. The first waypoint of Act I is set on every load.
- **Root count**: socketed items (gems, runes, jewels at location 6) follow their parent and do not count as roots.
- **Mercenary present** when its experience, seed or name field in the header is nonzero (observed in the loader
  logic); only then is `jf` followed by an item list.
- **Corpse**: after a death the save holds one corpse (count must be 0 or 1: more fails the load) with the items it
  carries. On join the corpse is placed next to the act's town start; once emptied, the next save has count 0
  (corpse rules in [monster-ai.md](monster-ai.md)).

Addresses, build 93847 (static reading in Ghidra): save path and its 16 KiB buffer `0x4135d0`; save assembly
`0x52f090` (32 KiB item scratch buffer); header writer `0x534340`; `if` reader `0x52e910`; mercenary rebuild
`0x530d70`; `lf` reader/writer `0x531510` / `0x533ae0`. D2R never writes a save above 16 KiB (classic: 8 KiB).

## Header

The header writer zero-fills 403 bytes and sets these; every other byte is zero.

| Offset | Size | Field |
|-|-|-|
| 0x00 | u32 | signature `0xaa55aa55` |
| 0x04 | u16 | version `0x69` (105) in 3.3; `RD` at 0x06 = extended format (above) |
| 0x08 | u32 | file size |
| 0x0c | u32 | checksum |
| 0x10 | u32 | **weapon set**: bit 0 set = the second (switched) set is active. Per character |
| 0x14 | u32 | character flags (hardcore, died, expansion, ladder ...) |
| 0x18 | u8 | class |
| 0x19 | u8 | constant 16 |
| 0x1a | u8 | number of class skills in `if` (30 retail) |
| 0x1b | u8 | level (display) |
| 0x1c, 0x20 | u32, u32 | created, last played; 0x24 u32 `0xffffffff` |
| 0x28 | 16 x {u16, u16} | skill hotkeys F1-F16: skill id (`0xffff` none, `| 0x8000` bound to the left button), item |
| 0x68 | 4 x {u16, u16} | mouse skills: left, right, switched left, switched right (skill, item) |
| 0x78 | 32 | appearance (component graphics and tints) |
| 0x98 | 3 x u8 | town per difficulty: act in the low bits, bit 7 = current difficulty (written and read: "When the game writes a save" below) |
| 0x9b | u32 | map seed |
| 0x9f | 16 | mercenary: u32 flags (`0x10000` dead), u32 seed, u16 name, u16 type, u32 experience |
| 0xaf | | six u64 ladder times, then six u32 ladder experience values at 0xdf |
| 0xf7 | u8 | client byte (inferred label) |
| 0xf8 | u8 | game type (3 = the newest edition's table set; see the `lf` section) |
| 0xfb | 4 x 12 | menu items for character select (right hand, left hand, torso, head): u32 code, tint, quality, graphic, flags (tint/graphic labels inferred) |
| 0x12b | char[] | character name (up to 0x3c bytes in a 104-byte field, NUL terminated) |

**Weapon swap**: the active set lives at 0x10. D2R moves items physically between body slots 4/5 (right/left
hand) and 11/12 (the alternate hands) when you swap, so the active weapons are always in 4/5; the flag only says
which set is out. The two "switched" mouse skills at 0x68 belong to the other set.

**Checksum**: set the size field, zero the checksum field, then over every byte: `c = rotl32(c, 1) + byte`
(32-bit). Write `c` at 0x0c.

```python
def fix_checksum(d: bytearray) -> bytearray:
    d[8:12] = len(d).to_bytes(4, "little"); d[12:16] = b"\0\0\0\0"
    c = 0
    for b in d:
        c = (((c << 1) | (c >> 31)) + b) & 0xffffffff
    d[12:16] = c.to_bytes(4, "little"); return d
```

## D2RLoader sidecar `<name>.d2rl`

| Offset | Size | Field |
|-|-|-|
| 0x00 | char[4] | `D2RL` |
| 0x04 | u16 | version 2 |
| 0x06 | u16 | header size `0x2c` |
| 0x08 | u32 | body length |
| 0x0c | 32 | SHA-256 of the body (file offset 0x2c to the end) |
| 0x2c | | body |
| 0x34 | u32 | copy of the `.d2s` checksum |
| 0x38 | u32 | copy of the `.d2s` file size |
| 0x3c | 32 | SHA-256 of the whole `.d2s` |

All offsets are file offsets (verified on real sidecars: version 2, header 0x2c, body 529 bytes, file 573 bytes).
**After any `.d2s` edit, update in this order**: `.d2s` size and checksum, sidecar
checksum copy (0x34) and size copy (0x38), sidecar `.d2s` hash (0x3c), then the body hash (0x0c). A mismatch makes
the character **silently disappear** from character select (a sidecar-only mismatch greys it out, per the D2RLoader
guide's "Environment badges").

## Character stats ("gf")

After the marker `gf`: a bit-packed list (LSB first) of `stat id`, then the parameter (itemstatcost `CSvParam`
bits, if any), then the value in itemstatcost **`CSvBits`** bits. Pad to a byte at the end.

- **Retail**: 9-bit stat id, list ends with id `0x1ff` (511).
- **Extended**: 16-bit stat id, list ends with `0xffff`. Extended mode reads the width from `CSvBits` and does not
  narrow it; a value above the field's maximum is **saturated** on Save and Exit (with `CSvBits` 11, 2327 unspent
  stat points came back as 2047). Size the columns for what a character can earn.

Values keep their stored form: life/mana/stamina are stored `<< 8` (fractional bits), not as displayed.

Useful rules: experience.txt row L is the experience at which level L+1 starts; charstats
`LifePerLevel`/`ManaPerLevel`/`StaminaPerLevel` are quarter points per level; `StatPerLevel` and
`SkillsPerLevel` give unspent points.

## Class skills ("if")

After `if`: **one byte per class skill** (hard points), in the order of that class's skills by skill ID. The count
is header byte 0x1a. Retail: 30 bytes (30 class skills). With D2RLoader's extended format a class can have more (an
overhaul ran several times 30). Setting a byte works with the checksum and sidecar updates.

## Items ("JM" + u16 count)

Bit-packed (LSB first), each item padded to a byte. The full retail record (every quality, the stat list, gold,
ears, sockets) is in [item-format.md](item-format.md). Short form:

| Field | Retail bits | Extended (observed) |
|-|-|-|
| flags | 32 | 32, bit 19 `0x80000` set (and `0x800000`) |
| format byte | none | 8, value `0x05` |
| version | 3 | 3 |
| location, body slot, x, y, page | 3, 4, 4, 4, 3 | same |
| code | Huffman, 4 symbols | Huffman |
| compact items | gold / quest difficulty, realm-id bit (+128), stack byte bit (+8) | same order |
| filled sockets, seed, level, quality | 3, 32, 7, 4 | same |
| picture flag (+3), auto affix flag (+id) | 1 + 3, 1 + 11 | 1 + 3, 1 + **16** |
| quality fields | magic 11 + 11, set/unique 12, rare 8 + 8 names + 6 x (1 + 11) | widths not verified |
| stats | 9-bit id, `Save Param Bits`, `Save Bits` (value + `Save Add`), end `0x1ff` | 16-bit id, end `0xffff` |

The gold record's trailing bit is an owner bit: set when the pile carries owner info (unit flag `0x400`) with owner
type 0, a player (writer `0x37fc80`).

Save pages are the engine page + 1: inventory 1, cube 4, stash 5 (location 0 = grid). Location 1 is the body, 2
the belt (slot in x), 4 the cursor, 6 inside a socket.

Item stat fields hold `(value >> ValShift) + Save Add`, so a load and save drops the low shifted bits; `gf` is never
shifted. The exact writer and reader rules are in [item-format.md](item-format.md#stat-values-and-valshift).

## What the game does on load

Build 93847, from the save processor `0x52e910` (static reading unless noted):

| Step | What happens | Confidence |
|-|-|-|
| Header (`0x531e70`) | allocates the player unit (`0x490410`), then `0x531180` -> `0x490b00`: **one** game-seed step, low word -> unit `+0x30`, seed `+0x28` = `{low, 666}`; player in mode 1 | static only |
| Items | the item section goes through a D2RCore thunk (`0x3e2b6ca`) back into the exe (`0x41f0b0`), which picks the reader by version (`0x421d20` for 105). Each root item and each socket child is made through the unit allocator: **two game-seed steps and the next item id each**, in save order; an item it skips is still made, then freed | high (seed and id order matched the join) |
| Then | corpses (`0x531af0`, a corpse body is seeded from the game seed), the hireling (`0x530d70`), the other sections; each shared-stash tab holder unit (`0x52f8d0`) is seeded the same way | static only |
| Stat rules (`0x5311f0`) | carried gold (14) below 0 or above `10000 x level` -> **0**; stash gold (15) below 0 or above 2,500,000 -> 0; stamina (10) -> max; life (6) 0 -> max life, else capped at max (items included); mana (8) -> max if the saved life was 0, else capped. Also sets stats 67, 68, 69 to 100 and stat 30 from class and level | static only |

So an edited gold value above the limit loads as **zero**, not as the limit. Classic wrote life and mana back
unchanged; the cap and refill are D2R's.

Other load facts:

- More than one corpse: the save does not load (`0x531af0` takes 0 or 1).
- Items with the same 128-bit realm id: only the first loads, the rest are dropped (observed). Give every
  hand-made item its own id.
- The quest record gets a load reset (see [engine-server.md](engine-server.md#6-quests)).
- New character names: 2-15 ASCII letters with at most one `-` or `_`, never first or last.
- Join: right after the unit control message the owner gets every item once in save order: equipped 0x9d action 6,
  stored (inventory, cube, stash) 0x9d 4, belt 0x9c 0x0d, socket children 0x9d 0x12 right after their parent.
  Cursor, ground and dropping items are not sent. Another client only gets the equipped items (and their sockets)
  when the player's unit reaches it (observed for the owner; other clients static only).

## When the game writes a save

Build 93847, static reading of the host side; classic names and flow from 1.10/1.14d for comparison. Confidence is
high for the timer and the forced save (each step read), medium where marked.

- **Periodic timer** (head of `0x408ea0`, the per-frame client update). `game+0x5800` holds the time of the last
  save on the host's millisecond clock (`0x120d720`). A zero field is stamped and nothing is saved (the game's first
  frame). After that, nothing happens until `now - field > 30000` (signed, strict: the 751st frame at 25 frames a
  second). Then it walks the clients (`game+0x138`) for one that has a player; with none it saves nothing **and does
  not restamp**, so the save comes on the first frame a player is loaded. With one it calls the save and restamps.
  Classic used `frame % 8192` instead.
- **The save** (`0x41e0d0(game, leaver)`, leaver null for the timer and forced saves) writes **every** client that
  has a player: no dirty check, no skip for a dead or hardcore-dead player. Per client it logs
  "PLAYERSAVEWrite(sPlayerSaveToStream) - IsPlayerExit", has D2RCore write the `.d2s` into a buffer and sends it
  through the same chunk sink the upload uses (on a hosted game that is the `0xb3` stream every 30 s; medium for the
  sink emitting `0xb3`). With the shared stash on, each tab section follows as its own file, then an optional side
  file and one empty chunk that ends the stream. Classic's realm path skipped a save that differed only in checksum,
  last-played time and map seed; D2R's realm path was not read.
- **Forced save** (`0x406e10(game)`): calls `0x41e0d0(game, 0)` and stamps `game+0x5800`, so every player is saved
  at once and the 30 s timer restarts. Callers: the Dead mode enter (`0x42a5e0`, `0x42e1bd`) for a hardcore
  character only, tested on the character's own flags (`0x486900(client, 4)`), not the game setting; the NPC
  buy `0x53d850`, sell `0x53f1a0` and Cain's identify `0x53c2b0` when they went through; Akara deciphering the
  Scroll of Inifuss (`0x5e0830`, `a1q4.cpp`); and unidentified sites in `PlrMsg.cpp` (`0x4f2490`, `0x4fa27e`,
  `0x4fd28f`), `a3q3.cpp` / `a4q3.cpp` / `a5q3.cpp` (`0x5ef040`, `0x5e5210`, `0x54d610`) and `0x4f461d`.
- **Leave** (`0x405f1c`): the host calls `0x41e0d0(game, leaver)`, so every player in the game is saved and pushed,
  not only the leaver, then restamps `game+0x5800`.
- **Death**: the save's Dead flag (`0x8` in the character flags at 0x14) is set on every death; a hardcore
  character (`0x4`) also forces a save of everyone (above).
- **Game end**: the 300 s empty-game rule is in [engine-limits.md](engine-limits.md).

**The header at a write** (classic writer PlrSave2; D2R's header writer `0x534340` lives behind D2RCore and its
stamping was not read, so this is the classic rule): `town[difficulty] = act | 0x80` for the game's difficulty, and
the other two difficulties' bytes lose bit 7. The map seed, the weapon set bit, the mouse skills and the last-played
time are written from the live character.

**The act at a load.** The town byte for the game's difficulty names the act in its low bits. Classic's load
(PlrSave2) falls back to Act I for a byte of 5 or more, and for Act V in a non-expansion game; it does not check the
act against the quest record. An act opens through the quest words 7, 15, 23 and 28 (Act I to IV completed, bit 0);
Warriv's trip to Act II writes word 7 as `0x2001` (bits 0 and 13), as the trip's captured `0x28` shows. A hand-edited
town byte for an act whose previous act is not completed is untested on the D2R host (medium).

**Where a joining player and saved corpses land** (1.14d, matched by D2R captures where two players joined, respawned
and left corpses on the same subtiles):

- The join (CLIENTS_SpawnPlayer `0x5394a0`) uses DUNGEON_FindActSpawnLocationEx (`0x61b060`): the act's start tile
  + 3, through a free-spot search with collision mask `0x1c09` (wall, no-player, object, door, no-path). A player's
  footprint is `0x80` (COLLIDE_PLAYER), which that mask leaves out, so a second player joins **on top of** the first.
- A respawn (`0x3f`) in the same act warps to the same point with the same mask. Only an act change
  (LEVEL_ChangeAct) searches with `0x1c89`, which adds `0x80`.
- Each saved corpse is placed by PLAYER_PlaceCorpse with mask `0x9c19` (includes COLLIDE_CORPSE `0x8000`) from that
  same point. The body is allocated with no room, and the collision writer writes nothing for a unit whose old room
  is null, so one placed corpse does not push the next away: several players' saved corpses share a subtile.

## What is safe to hand-edit

| Edit | Safe? |
|-|-|
| Level (header + stat 12), experience, stat points, skill points, base stats | Yes (fix checksum, size, sidecar) |
| Class skill bytes ("if") | Yes |
| Gold, waypoints, quests | Likely (not tested in game) |
| Retail items, with a codec that round-trips the file byte for byte first | Workable; a library written this way re-read every game-written save byte for byte, but a changed save has not been loaded by the client yet (unverified) |
| **Items under the extended format** | **No.** The extended format stores wider item/stat/affix/skill IDs whose exact widths are not documented; two hand-built items made the characters vanish even with correct checksums and hashes |
| Item widths after changing itemstatcost `Save Bits`/`CSvBits` | Existing saves break: decide widths before release |
| Quest words, for test characters | Yes, closed save. Reset the quests a test needs: D2R runs a quest's kill handler only while the quest is open in that difficulty, so a test that passed once fails on the same character later |

Get test items with D2RLoader's debug-mode **Item Spawner** instead of writing items.

## Notes

- Retail starter items may not decode with the retail Huffman table under the extended format (a Paladin starter
  item did not), another sign the extended item layout differs.
- **Item stats must fit their save widths.** A property with function 28 (stat layer = param) on a stat that has
  no `Save Param Bits` makes D2R's item writer assert (`Items.cpp` `ptItemStats`) and write nothing: the item packet
  is an empty header and the client crashes (`BC_ASSERT: hUnit`, then a null read in the item packet handler) the
  moment such an item drops, is gambled or comes out of the cube. 1.13c's writer skipped the layer instead. Fix the
  data: clear the parameter, or give the stat param bits before players have saves.
- A plugin page registered through the PluginSDK inventory service is saved with the character (observed: as an
  extra item page). A character with items on that page **refuses to load** when no plugin provides the page; the
  save is left unchanged, so nothing is lost. Only one such page per game.
- Files next to `<name>.d2s`: `<name>.d2rl` (D2RLoader sidecar), `<name>.key` (key bindings), `<name>.map` and
  `.ma0`-`.ma3` (explored automap per level `Layer`; a cel above the atlas's 1,498 or a `Layer` of 100+ corrupts it,
  [engine-limits.md](engine-limits.md)). The mod's save folder also holds its own `Settings.json` (Quick Cast and
  the other options are per mod).
- The shared stash is a separate file per mod save folder (`SharedStashSoftCoreV2.d2i`); it must stay valid (an
  empty valid stash is fine). Its tabs belong to the account, not the character. The folder also holds
  `Settings.json` and loot-filter files (`*.fltr`). The `.d2i` layout is not documented here.
