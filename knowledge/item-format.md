# The bit-packed item record (saves and network)

D2R 3.3 writes one item record format in two contexts: the **save** copy (`.d2s` item lists, [saves.md](saves.md))
and the **network** copy (the tail of the S->C item messages `0x9c` and `0x9d`, see
[network-messages.md](network-messages.md)). Same field order, different widths and omissions. This file is the
retail layout, as observed in D2R 3.3 build 93847. D2RLoader's extended save format is described in
[saves.md](saves.md#two-formats-retail-and-d2rloader-extended).

Confidence: the save layout round-trips every game-written save that was tested (starter characters and a
character with spawned gear: runeword, ethereal, socketed, stacked, unique). The network layout round-trips 257
captured records (136 distinct). Rows marked **static** were read in code only; no saved or captured item
exercised them (identified magic/rare/set/unique on the wire, tempered, charms, ears, personalized, gamble
listings).

## Bit order

Bits are packed **LSB first** within each byte. Every item ends at a byte boundary (padding bits zero), and each
socketed child is its own byte-aligned record following its parent.

## Head (every item)

| Field | Save | Network | Notes |
|-|-|-|-|
| flags | 32 | 32 | below |
| format byte | none | 8, value 5 | present when flag bit 19 (`0x80000`) is set; set on every network record |
| version | 3 | 10 (101) | save: one bit "above 99", then 2 bits |
| location | 3 | 3 | 0 stored, 1 body, 2 belt, 3 ground, 4 cursor, 5 dropping, 6 socketed |
| x, y | 16, 16 | 16, 16 | instead of the four fields below, for location 3 or 5 |
| body, x, y, page | 4, 4, 4, 3 | same | page is the inventory page + 1 (0 = none; 1 inventory, 4 cube, 5 stash) |
| ear | class 3, level 7, name 8-bit chars to a 0 | same | flag `0x10000`; replaces the code |
| code | Huffman, 4 symbols | four 8-bit chars | space padded (`"hp1 "`); in saves the padding spaces are encoded too |

Body slots: 1 head, 2 neck, 3 torso, 4 right hand, 5 left hand, 6 right ring, 7 left ring, 8 belt, 9 feet,
10 gloves, 11 and 12 the switch-set hands.

**Flags** (32 bits): `0x10` identified, `0x800` socketed, `0x10000` ear, `0x20000` starter, `0x80000` wide record
(bit 19, network only in retail), `0x200000` compact, `0x400000` ethereal, `0x800000` always set by the writer,
`0x1000000` personalized, `0x2000000` gamble listing (network only), `0x4000000` runeword, `0x10000000` and
`0x20000000` auxiliary data (below). The writer sets `0x200000` from the base's `compactsave` column, clears
socketed on an unidentified item, and for a gamble listing clears ethereal.

**Huffman code** (save only): four symbols through D2R's fixed item-code tree (nodes table at `0x1d03330`, 8 bytes
per node; emitters `0x376490` / `0x376500`, build 93847). Only the item-code alphabet is in the tree.

## Compact items (flag `0x200000`)

Potions, scrolls, gems, runes, keys and other `compactsave` bases. After the code:

| Field | Bits | Condition |
|-|-|-|
| gold | 1 + 12 or 32, then 1 | gold-type base: width bit (set exactly when the amount is above `0xfff`), amount, a unit flag bit |
| quest difficulty | stat 356 `Save Bits` | base has `quest` and `questdiffcheck`; value + `Save Add` |
| realm id | 1 (+ 128) | save only: presence bit, four 32-bit words |
| stack byte | 1 (+ 8) | presence = base's `AdvancedStashStackable` column; the stack count in the advanced stash |

## Full items, in order

| Field | Save | Network | Condition |
|-|-|-|-|
| (gamble listing) | - | record ends after the code | flag `0x2000000`; code is the base's `normcode` |
| filled sockets | 3 | 3 | number of socket children (max 7) |
| seed / fingerprint | 32 | absent | |
| item level | 7 | 7 | network: 1 unless the base has `ShowLevel` (no vanilla base does) |
| quality | 4 | 4 | 1 inferior, 2 normal, 3 superior, 4 magic, 5 set, 6 rare, 7 unique, 8 crafted, 9 tempered |
| picture | 1 (+ 3) | same | presence bit = the item type's `VarInvGfx` |
| auto affix | 1 (+ 11) | 1 (+ 16) | presence bit, then the id when nonzero. Save width 11 is **static only** |
| inferior / superior | 3 | 3 | quality 1 or 3 |
| magic | 11 + 11 | 16 + 16 | prefix, suffix |
| set / unique | 12 | 16 | set or unique index (sent even when unidentified) |
| rare / crafted | 8 + 8 names, then 6 x (1 + 11) | 17 + 17, then 6 x (1 + 16) | three prefix/suffix pairs, each a presence bit and an id |
| tempered | not verified | 17 + 17 | quality 9 (static) |
| charm affix | not verified | 1 + 16 | normal-quality charm: a "prefix" bit, then the prefix or the suffix (static) |
| body part | not verified | 10 | normal quality, `body` type that is not `play` (static) |
| scroll / tome spell | 5 | 5 | normal quality, `scro` or `book` type |
| runeword | 16 (12-bit id + 4 bits) | 16 | flag `0x4000000`; runes.txt id |
| personalized name | 8-bit chars to a 0 | same | flag `0x1000000` |
| realm id | 1 (+ 128) | absent | |
| defense | stat 31 `Save Bits` | same | `armo` types; stored = value + `Save Add` |
| max durability, durability | stat 73, stat 72 | same | `armo`/`weap`; durability only when max durability > 0 |
| gold | 1 + 12/32 + 1 | same | gold type that is neither armor nor weapon |
| quantity | 1 (+ 9) | same | presence bit = base's `stackable` column (a reader should reject a mismatch); stat 70, max `0x1ff` |
| total sockets | stat 194 `Save Bits` (4 retail) | same | flag `0x800` (and identified on the network) |
| set mask | 5 | 5 | set quality: which bonus lists follow |
| stat lists | below | below | own list, then one list per set-mask bit, then the runeword list |
| auxiliary data | 16, 32, 4, n x 64 | same | flag `0x10000000`: tag, then (unless `0x20000000`) a value and a count, then the 64-bit entries; with `0x20000000` eight entries (semantics unknown) |
| stack byte | 1 (+ 8) | same | as for compact items |

## Stat lists

Each list is a run of entries ended by the id `0x1ff` (retail save):

1. **id** in 9 bits (network: 16);
2. **parameter** in the stat's itemstatcost `Save Param Bits`, if it has any (network: 32 bits);
3. **value** in `Save Bits`, stored as `(value >> ValShift) + Save Add`, capped at the field's maximum. The cap is
   an unsigned compare, so a negative sum also becomes the maximum.

Grouped stats: after a leader's value, the followers' values come **without their own id**:
17 -> 18 (enhanced damage max/min), 48 -> 49, 50 -> 51, 52 -> 53 (fire, lightning, magic min/max),
54 -> 55, 56 and 57 -> 58, 59 (cold and poison min, max, length).

What the writer leaves out (so a reader must not expect it): stats with `Save Bits` 0, stats whose value is 0
after the shift, and a follower whose value is already what its leader wrote. Entries are sorted by (id,
parameter). A stat with a parameter but no `Save Param Bits` makes the writer fail the whole record: an empty item
packet and a client crash ([saves.md](saves.md#notes)).

### Stat values and ValShift

Under D2RLoader 1.3.1 the narrow (save) item stat writer and reader live in D2RCore: writer core `0x3cb1c0` (called
from `0x830ff0`), reader core `0x3c9f40`, one ordinary stat core `0x3cbe70` (core RVAs = offsets in D2RLoader's
D2RCore module, not D2R.exe; static only). The unit's stat list holds 32-bit values; the field holds
`(value >> ValShift) + Save Add` (arithmetic shift). The game keeps the list sorted by (id, parameter); the setter
(core `0x3d95c0`) overwrites an existing entry and removes one set to 0.

| Rule | Writer | Reader |
|-|-|-|
| Ordinary stat | skipped when Save Bits is 0 or `value >> ValShift` is 0 (tested **before** Save Add: with shift 4, values 1-15 vanish and -1 comes back as -16); sum written unsigned, capped at `2^Save Bits - 1`, so a negative sum becomes the maximum | `(field - Save Add) << ValShift` as int32; the only path that reads ValShift |
| Refused | ValShift above 31; a parameter on a grouped leader; a parameter on a stat without Save Param Bits | ValShift above 31 |
| Leader (17, 48, 50, 52, 54, 57) | written normally; then each follower's list value + Save Add, capped, **no shift**; a follower missing from the list is written as 0 | leader and followers come back `field - Save Add`, unshifted |
| Follower already equal to its leader's value | not written a second time | |
| Stats 0-3 | | unshifted, and read **without** a parameter |
| Stat 326 | only its id is written | |
| Parameter | | the narrow reader keeps 16 bits |
| Leader 57 | | loading it also sets stat 326 to 1 |

In vanilla tables the item stats with a ValShift are 7, 9, 11 (max life, mana, stamina) and 216, 217 (per-level);
6, 8, 10 have ValShift 8 but no Save Bits, and the followers have none, so the asymmetries only show with edited
tables. A save read and written back loses the low ValShift bits, exactly once. The character stat section `gf` is
**not** shifted (writer core `0x3cb650` from `0x830030`, reader core `0x3cb920`). The network record (wide form)
follows the same rules.

A tool that re-encodes an item must also refuse a value whose shift wraps in 32 bits: the game keeps the wrapped
value, but encoding it would not give the field back.

## Save copy vs network copy

| | Save | Network |
|-|-|-|
| Widths | narrow: 9-bit stat ids, 11-bit affixes, 8-bit rare names, 12-bit set/unique, `0x1ff` end | wide: format byte 5, 16-bit ids and affixes, 17-bit names, 32-bit parameters, `0xffff` end |
| Version / code | 3 bits / Huffman | 10 bits / ASCII |
| Seed, realm id | present | absent |
| Unidentified item | everything | head and base fields only: no affixes, rare names (six zero bits), socket count, set mask or stat lists |
| Socket children | follow the parent | each sent as its own `0x9d` (action `0x12`, item in socket) |
| Size limit | 16 KiB save, 32 KiB item buffer | record at most 0xf4 bytes; in a `0x9d` (13-byte header) at most 0xef. A longer record is sent **empty** |

The wide network form was observed under D2RLoader 1.3.1; a game without the loader was not checked.

Addresses, build 93847: item serializer `0x37ffd0` (flags), compact writer `0x37ca20` / reader `0x3780d0`, full
writer `0x37d140` / reader `0x378860`, version writer `0x3801d0`, gold writer/reader `0x37fc80` / `0x37bcc0`,
stack byte `0x37c970`, auxiliary data `0x37fe60`, realm id `0x37fd60`. Network message builders: `0x9c`
`0x479cd0`, `0x9d` `0x479ea0`; client item handler `0x1c6b60`.
