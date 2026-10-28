# Saves (.d2s v105) and the D2RLoader sidecar

Edit test characters only while the game is closed, and **back up first** (copy the `.d2s` and its `.d2rl` to a
folder of your own, e.g. `Saved Games/Diablo II Resurrected/<mod>-manual-backups/<name>-before-<change>/`).

Save folder for a mod with `"savepath": "<Mod>/"` in its modinfo.json: `%USERPROFILE%\Saved Games\Diablo II
Resurrected\mods\<Mod>\` (kept apart from retail saves). D2RLoader also writes its own backups to
`Saved Games\Diablo II Resurrected\D2RLoader Backups\`.

## Header

| Offset | Size | Field |
|---|---|---|
| 0x00 | u32 | signature `0xaa55aa55` |
| 0x04 | u16 | version `0x69` (105) in 3.3. In D2RLoader extended-format saves bytes 0x06-0x07 read `RD` (u32 `0x44520069`); likely the extended marker (observed, not documented) |
| 0x08 | u32 | file size |
| 0x0c | u32 | checksum |
| 0x18 | u8 | class |
| 0x1b | u8 | level (display) |
| 0x28 | u32[16] | skill hotkeys F1-F16: skill id, `0xffff` = none, `| 0x8000` = bound to the left button (observed) |
| 0x12b | char[] | character name |

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
bits, if any), then the value in itemstatcost **`CSvBits`** bits. Under D2RLoader's **extended** save format the
stat id is **16 bits** and the list ends with `0xffff` (retail D2: 9-bit ids, end `0x1ff`). Life/mana/stamina are
stored `<< 8`. Pad to a byte at the end.

Useful rules: experience.txt row L is the experience at which level L+1 starts; charstats
`LifePerLevel`/`ManaPerLevel`/`StaminaPerLevel` are quarter points per level; `StatPerLevel` and
`SkillsPerLevel` give unspent points.

## Class skills ("if")

After `if`: **one byte per class skill** (hard points), in the order of that class's skills by skill ID. Retail:
30 bytes (30 class skills). With D2RLoader's extended format a class can have more (an overhaul ran several times 30).
Setting a byte works with the checksum and sidecar updates.

## Items ("JM" + u16 count)

Bit-packed (LSB first), each item padded to a byte. Retail 3.3 layout, as decoded:

| Field | Bits | Notes |
|---|---|---|
| flags | 32 | 0x10 identified, 0x800 socketed, 0x10000 ear, 0x20000 starter, 0x200000 simple, 0x400000 ethereal, 0x1000000 personalized, 0x4000000 runeword |
| (3.3) | 8 | 0x05 on every item seen |
| version | 3 | 5 |
| location, equip slot, x, y, page | 3, 4, 4, 4, 3 | |
| code | Huffman, 4 chars | D2R's item-code tree |
| simple items | 1 + 128, then 1 | unique-id flag + 128-bit id, socket bit; done |
| sockets filled | 3 | |
| item id | 32 | |
| item level, quality | 7, 4 | |
| picture flag, class-specific flag | +3, +16 | auto-affix id is 16 bits in 3.3 (11 in 2.x) |
| quality fields | | set/unique id, affixes, rare names (3.3 widths not verified) |
| defense / durability | itemstatcost `Save Bits` / `Save Add` | |
| stats | 16-bit id, `Save Param Bits`, `Save Bits` (value + `Save Add`) | end `0xffff` |

## What is safe to hand-edit

| Edit | Safe? |
|---|---|
| Level (header + stat 12), experience, stat points, skill points, base stats | Yes (fix checksum, size, sidecar) |
| Class skill bytes ("if") | Yes |
| Gold, waypoints, quests | Likely (not tested) |
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
- Files next to `<name>.d2s`: `<name>.d2rl` (D2RLoader sidecar), `<name>.key` (key bindings), `<name>.map` and
  `.ma0`-`.ma3` (explored automap per level `Layer`; a cel above the atlas's 1,498 or a `Layer` of 100+ corrupts it,
  [engine-limits.md](engine-limits.md)). The mod's save folder also holds its own `Settings.json` (Quick Cast and
  the other options are per mod).
- The shared stash is a separate file per mod save folder (`SharedStashSoftCoreV2.d2i`); it must stay valid (an
  empty valid stash is fine). The folder also holds `Settings.json` and loot-filter files (`*.fltr`).
