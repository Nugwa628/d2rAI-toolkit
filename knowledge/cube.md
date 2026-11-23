# The Horadric Cube: transmute, recipe match, outputs, portals

What happens between the Transmute button and the new item: the request, how cubemain rows are matched against the
cube's items, what each output and modifier does, and the special outputs (the Cow Portal and the D2R-only Red
Portal). For writing recipes use the `d2r-cube` skill; the table's columns are in [tables.md](tables.md), the record
offsets and the recipe search in [d2r-93847-addresses.md](d2r-93847-addresses.md#cube), the condition limits in
[engine-limits.md](engine-limits.md#items-inventory-sets-runes-cube).

Confidence: the match and the commit follow the 1.14d routines (`CUBE_MatchAndExecuteRecipe` `0x5665f0`,
`CUBE_GenerateOutput` `0x565ab0`) and were compared with the D2R recipe search `0x529090`, the input matcher
`0x529d50` and the cube executor `0x5269c0` (D2R addresses are build 93847 RVAs, **static only**). Where D2R differs
from 1.14d it is said. Nothing on this page was checked live on 93847 unless marked.

## 1. The request

- The Transmute button sends C->S `0x20` (client `0x2cd720`, server `0x4c1590` -> `0x4f2490`). 1.14d had no message
  of its own (a button click). Length 13 + 5 bytes per entry, the entry count at byte 7; the entries' layout was not
  captured, and the server does not use them: it matches what the cube page holds, never what the client lists.
- The player needs a cube in reach, as for any move to or from the cube page: on the cursor or in the inventory, or in
  the stash while standing near the stash. Without one the request is ignored.
- **No match sends nothing.** A refused special output also keeps every item (section 5).

## 2. How a row is read

Input and output cells are split on `,` and `=`, quotes trimmed (1.14d `0x668600` inputs, `0x668a90` outputs; D2R
output parser `0x3e5e60`).

| Part | Accepted |
|-|-|
| Input item | `any`, an item type code, an item code, or a unique / set item's name (uniqueitems first, then setitems): the item must have that base, quality and row |
| Input modifiers | `qty=N`; a quality (`low nor hiq mag set rar uni crf tmp`); `nos`, `sock`, `noe`, `eth`, `nru`; `bas` / `exc` / `eli` (the item is its normal, exceptional or elite code, from weapons/armor `normcode` / `ubercode` / `ultracode`); `upg` (the input's code is the item's own tier or the tier below) |
| Output item | an item code; an item type (a random class of that type, 1.14d `0x565930`); a unique or set name (its base, quality, row and level); `usetype` (a new item of the first input's class); `useitem` (the first input itself); a special output (section 5) |
| Output modifiers | `qty`, `lvl`, `pre`, `suf`, `sock`, `eth`, `mod`, `uns`, `reg`, `exc`, `eli`, `rep`, `rch`, `tmp`, `rem`, and the five mod columns (properties code, chance, param, min, max) |

`lvl` has two meanings: on a normal output it is the item level; on `Red Portal` the D2R parser stores it in the
output's byte `+0xc` as the destination level (section 5.2).

## 3. The match

Rows are tried **in file order and the first that passes wins**. A recipe with an unsupported part still matches and
blocks every later row, so put special rows above general ones.

1. **Row gates:** enabled; a `version` of 100 or more needs an expansion game; the ladder seasons (below); `min diff`;
   `class` when set; `numinputs` equal to the number of item units in the cube (a stack counts once).
2. **Inputs:** each input in order takes every unused cube item it matches (kind, quality, sockets, ethereal and
   runeword tests, 1.14d `0x565010`). With a stackable item among them the count must reach `qty`, otherwise equal it
   (no `qty` means 1).
3. **Op 28** (the only op D2R's own rows use): an input that is a quest item with items.txt `questdiffcheck` counts
   only in a game of its difficulty, read from the item's stat 356. D2R `0x529d50` compares with `==`; 1.14d also took
   a lower game difficulty. So a Normal Horadric Staff no longer works in a Nightmare game. Other ops:
   [engine-limits.md](engine-limits.md#items-inventory-sets-runes-cube).
4. **Quest items and `mod`:** when the first output has `mod`, a quest item (items.txt `quest`, with or without
   `questdiffcheck`) never counts for **input 1** (D2R `0x529d50`; 1.14d has no such test). Later inputs are not
   tested. This is what stops the socket and upgrade recipes from taking a quest weapon as their base.

### The order of the cube's items

The cube walks its items in its inventory list's order (1.14d `0x63b2c0` / `0x63dfa0`). Placing an item in a grid
unlinks it and appends it at the tail (`0x63afd0`), so the list runs in the order of **each item's last placement**:
taking an item out and putting it back in the same cell moves it to the end. That order decides which item is input
1's "first" and "last": the item that `useitem`, `usetype` and `mod` keep, and the item level the output level reads.
It is not the item id order. A save writes the list as it walks it and a load places items in file order, so the order
survives a save and a rejoin (1.14d `0x5317b0`, `0x5335e0`). For a mod: a recipe with two inputs of the same kind
(two axes, two rings) acts on whichever the player put in first.

### Ladder rows

`firstLadderSeason` / `lastLadderSeason` are tested by `0x403fe0`, the same test the item generator (uniques
`0x443910`, sets `0x58c2f0`, TCs `0x444920`) and runeword forming (`0x46eb00`) use. A row fails only when **all** hold:

- game `+0x101` (the host game type, set at create from CREATEGAME `0x67` @16: 0 a Battle.net host, 1 open, 2 direct
  TCP/IP, 3 single player) is 0;
- `first` is not 0 and `last` >= 0;
- the ladder byte `+0x108` is 0, or `first` > 0.

So single player, open and TCP/IP games pass every ladder row, ladder game or not. A Battle.net-type host passes a row
with `first > 0` only when `last < 0`, and a row with `first < 0` only in a ladder game. The same rule decides whether
a ladder-only runeword forms (with runes.txt `disallowCraftingInLadder` / `...NonLadder` on top). 1.14d refused a
`ladder` row only when both type fields read as a Battle.net host and single player. Static only.

## 4. The commit

In `CUBE_GenerateOutput` order:

1. **Each output is made first, at the output level** (below):
   - `useitem` **without `mod`** keeps the first input as it is (its socketed items go with it unless `uns`);
     durability unchanged.
   - Any other output is a new item: the class of the code, of the type, or of the first input (its ubercode /
     ultracode for `exc` / `eli` when that tier exists and the game allows its version); the row's quality (normal when
     none; `crf` is the crafted roll, 1.14d `0x5c21d0`); the forced unique / set row; forced `pre` / `suf`; ethereal for
     `eth`. With `reg` it keeps the first input's quality, row and item level.
   - **No random sockets** on a new output (create flag 8), except `sock` with no count, which is always socketed (flag
     `0x10`); a counted `sock=N` adds its sockets afterwards (D2R `0x5269c0` at `0x5275ee`-`0x527678`).
   - **With `mod`**, the first input is kept and given its new class (`exc` / `eli`); its base stats are rolled again
     (1.14d `0x557ab0`: full new maximum durability, current = half plus a draw below half) and an ethereal item takes the
     ethereal bonus once; quality and affixes stay. The new base's durability is **not** halved for ethereal, so an
     upgraded ethereal item has the full maximum of its new base.
   - **Ethereal outputs** (`eth`, D2R `0x442d60`): maximum durability becomes `max / 2 + 1` (37 -> 19) after the quality's
     own durability changes, current set to it. Only for an item that has durability (weapon or armor with
     `durability`, no `nodurability`, maximum not 0, not indestructible, not low quality or set, not a quest item); an
     indestructible unique comes out **not ethereal**.
   - `uns` removes the runeword and the socketed items. Each mod line whose `chance` passes on the item's seed is
     applied as a property on the item's own list. 1.14d applied the ethereal bonus again after each line (an ethereal
     item's defence rose by half per socket recipe); D2R does not.
   - `rep` mends a broken item or raises durability to the maximum and sets a stack to `qty` (D2R `0x46e7a0`). It does
     not test ethereal or indestructible: an ethereal item goes to its halved maximum. `rch` refills every charged
     skill. `sock=N` on an item with no sockets: N, capped by the type's maximum and by quality (3 magic, 1 set, rare,
     unique and crafted). Without `sock` a stack is set to `qty`.
   - The output is identified, then it must fit (grid, no second quest item of a code, no second `carry1` unique) on
     a copy of the inventory with every cube item gone. 1.14d took the inputs first and freed an output that did not
     fit, and its placement only tested the grid (so a second Horadric Staff could be held); a server that refuses the
     whole transmute instead keeps the inputs.
2. **Every item in the cube goes**, not only the inputs (1.14d `0x564f30`): 0x9d 5 with item flag `0x20` ("gone for good").
   An extra item left in the cube is destroyed, which is why recipes match `numinputs` exactly.
3. 0x2c sound event 4 to the owner.
4. Each output goes to its cell (0x9d 4).
5. Quest hooks see the transmute once an output is in the cube: the Horadric Staff (`hst`, 1.14d `0x59e5c0`) and
   Khalim's Will (`qf2`, `0x5b86e0`).

**Output level:** `lvl` when set, else `plvl`% of the player's level + `ilvl`% of the item level of the **last** item
input 1 took, clamped 1..99.

Open: a `mod` output's new unit seed (one game-seed draw is assumed); the restored flag 1.14d set on a mended item
(D2R `0x46e7a0` only clears broken); the exact maximum a broken item is mended to (D2R reads it with `0x2f4b60`).

## 5. Special outputs

D2R's output step `0x5269c0` calls the table `0x1d3bfc0` by the row's special number (output `+0xd`, recipe `+0x67`)
with the game, the cuber and the output block (recipe `+0x5a`). A handler answer of 0 refuses the transmute: **the cube
keeps everything and plays no cube sound 4**; only the handler's own refusal sound plays. The Pandemonium portals
(D2R `0x5da590`, `0x5da430`) are not covered here.

### 5.1 Cow Portal

1.14d `0x594140`. Refused, in this order, when:

1. the game's cow bit is set (quest word 4 bit 11 in the game's quest record: one cow portal per game, by anyone,
   never cleared);
2. the cuber's own bit 10 of quest word 4 is set on this difficulty (the cuber was in the farm when the Cow King died);
3. the cuber has not completed the last quest of the difficulty (Terror's End, word 26, in a classic game; Eve of
   Destruction, word 40, in an expansion game; bit 0);
4. the cuber is not in the Rogue Encampment (level 1);
5. no free spot near the cuber (size 3, mask `0x400`, 100 rings), or the portal is not made.

A refusal plays sound event `0x14` on the cuber. Allowed: a permanent portal (objects.txt 60) to the Moo Moo Farm
(39), its far end at the farm's special tile 11; the game's bit 11 is set. Only the cuber's record is read: a party
member who killed the King does not block someone else from opening it.

**The Cow King's kill** (1.14d `0x593e70`, in quest 4's handler; the King is superunique 39, monstats 735; a pet's or
merc's kill counts for its owner):

1. a killer who already has bit 10, or has not completed the last quest, ends it: nobody gets the bit;
2. else the killer gets bit 10, wherever they stand;
3. then every player standing in the farm gets bit 10, completed or not;
4. then 8 stamina potions (`vps`) drop at him on top of his TC (D2R `0x5e29d0` sets the drop code at `0x5e2aae` and
   calls the reward drop `0x43fbf0` eight times).

The bit is saved per difficulty, so that character's next cow portal there is refused. (The toolkit's
[quests-acts.md](quests-acts.md) lists this kill under "Cain" because the bits live in the Search for Cain word.)

### 5.2 Red Portal (D2R only)

Shipped row: the five Uber Ancient materials `ua1`-`ua5`, op 28, version 100, `min diff` 2, output
`Red Portal,lvl=137,qty=1`. Normal and Nightmare simply match nothing. The parser reads `Red Portal` as special 4,
`qty` into output byte `+0xb`, `lvl` into `+0xc`; entry 4 (thunk `0x52aa00`) calls `0x5da800(key, lvl, qty)`. In
order:

1. The cuber's room must be in a town (`0x327850`: 1, 40, 75, 103, 109); else refused with sound `0x15`.
2. When `qty` > 0, the per-game count for the key (hash map at game `+0x1aac8`, `0x5d88b0`, not saved) must be below
   `qty`; at or above it the transmute is refused **silently** (no sound). `qty=0` sets no limit.
3. A free spot near the cuber (`0x517f90`, size 3, mask `0x400`, 100 rings); none: refused with the sound.
4. The pair through `0x432ce0`: object 60, the cuber as owner, destination `lvl`, last argument 1 (skips the check
   other cube portals pass). `0x432ce0` **refuses when the destination's act (levels.txt `Act`) is not the act of the
   cuber's level**; the far end goes at the destination's special tile 11, as for the Pandemonium portals. No pair:
   refused with the sound. D2R leaves a near end standing when only the far end fails (1.14d deleted it).
5. Made: destination byte set (`0x34e9d0`), count + 1, then every cube item goes and sound 4 plays. Nothing closes the
   portal.

**For a mod** (static only, not tried in game): this is the data-only way to make a recipe open a red portal to a
level other than the Cow Level. Copy the row with your own inputs and `lvl=<level id>`, and open it from the town of
the destination's act. Each row counts on its own if the key is per row (the key is a dword the table loader writes at
the output's `+0`; which value it is was not traced). The destination must have a special tile 11 to take the far end.

## 6. Pitfalls

- An extra item in the cube is destroyed with the inputs. A refused special output keeps everything.
- First match wins in file order, including rows the engine cannot carry out.
- Equal inputs are taken in placement order, not id order; the "first" input can change by moving items in the cube.
- Op 28 is exact in D2R: a quest item from a lower difficulty no longer counts.
- An ethereal `mod` upgrade gets the new base's full durability; a new `eth` output gets half + 1; an indestructible
  base cannot come out ethereal.
- Ladder seasons only bite in a Battle.net-type game; single player and TCP/IP pass every ladder row.
