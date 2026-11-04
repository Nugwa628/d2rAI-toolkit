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
- **Corpse**: after a death the save holds one corpse (count 0 or 1 in practice, at most 16) with the items it
  carries. D2R picks the corpse up when the character next enters a level; the next save has count 0.

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
| 0x98 | 3 x u8 | town per difficulty: act, bit 7 = current difficulty |
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
|---|---|---|
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

Save pages are the engine page + 1: inventory 1, cube 4, stash 5 (location 0 = grid). Location 1 is the body, 2
the belt (slot in x), 4 the cursor, 6 inside a socket.

## What is safe to hand-edit

| Edit | Safe? |
|---|---|
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
