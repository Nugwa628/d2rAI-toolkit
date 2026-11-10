# Items in play: death drops, item creation, inventory requests, vendors

How D2R 3.3 (build 93847) decides what a dead monster drops and where it lands, how items get ids and seeds, the
level requirement, the finer inventory rules (main weapon, refresh order, controller requests) and the vendor
details beyond the basics. Read [engine-server.md](engine-server.md) sections 3-5 first: the treasure class executor,
quality roll, item creation, the mover and the vendor stock/price/gamble basics are there and not repeated. The item
record is in [item-format.md](item-format.md), the save in [saves.md](saves.md), chests in [objects.md](objects.md).

Confidence: **high** = a test plugin ran the game's own function thousands of times and an independent model matched
every field; **observed** = seen in captures; **static only** = read in code, not exercised.

## 1. The death drop: which treasure class

`StartDeath` (`0x444f50`) runs the drop first: `0x4479a0(game, modeChange)` (dying unit at `+0x08`, killer at
`+0x10`) -> `0x447b80(game, monster, ignoreNoDrop, modeChange)`. Only `0x446e80` (a forced drop) passes
`ignoreNoDrop = 1`.

| What | Value (build 93847) | Confidence |
|-|-|-|
| No treasure | unit flag `0x20000` at `+0x124` drops nothing (revives, summons); class 344 (bone wall) asserts | high |
| Corpse on a wall | classic dropped nothing on a `0x801` cell; D2R still tests (`0x366250`) but ignores the result | high |
| Base TC | picked inside D2RCore (thunk `0x3e2b45a(game, monster, difficulty, desecrated)`); the rule below matched 16,830 of 16,830 drops | high |
| Superunique (type flag `0x02`, row at monster data `+0x2a`) | superuniques `TC` / `TC(N)` / `TC(H)`; no row -> the monster's unique column | high |
| Champion (`0x04`) | `TreasureClassChamp` (wins over unique) | high |
| Unique (`0x08`) | `TreasureClassUnique` | high |
| Anything else, minions (`0x10`) included | `TreasureClass` | high |
| Desecrated (state `0xbd`, `0x3af200`) | the `Desecrated` variant of the column when filled; herald column not traced | static only |
| monstats record | eight u16 TC indexes per difficulty at `+0xc0 + 0x10 * difficulty`: normal, champion, unique, quest, three desecrated, herald | high |
| Quest TC | replaces the base TC when the monster has `TCQuestId` (`+0xf0`) and a quest TC (`+0xc6`), there is a killer (a non-player killer is replaced by its owner, `0x4a53c0`), it is a player, and its quest word for that difficulty (player data `+0x40 + 8 * difficulty`) has none of bits 15, 1, 0 or `TCQuestCP` (`+0xf1`) | high |
| Level upgrade (`0x441300` -> `0x397290`) | uses the monster's stat 12 only for an expansion monster in Nightmare/Hell that is neither `noRatio` nor `boss`, or a desecrated one; else no upgrade. Type flags `0x600` add a terror-zone bonus (`0x542ea0`) | high |
| Drop level (`0x43e8a0`) | `max(1, stat 12)` for a monster; used for the quality roll and every item | high |
| Difficulty context (`0x48ff40`) | game difficulty `+0x104`, ladder byte `+0x101`, bank `+0x106`, `+0x108`; a terror-zone monster (`0x5439c0(unit, 4)`) can override | static only |

Act I: Andariel (class 156) is the only quest-TC monster (`Andarielq`, which is also her normal TC in 93847). Her
gems come from her quest kill handler (`0x5e1f00`), not the TC. Blood Raven (`0x5de890`) and the Countess (`0x5e10c0`
-> `0x5e3010`) handlers make no items; the Countess's runes are entries of her superunique TC.

**Quality level.** The executor's sixth argument is the level the quality roll compares against the item level
(-1 = the item level). A monster passes its own level; a chest passes its TC index 0-2, which is why chest loot is
mostly low quality ([objects.md](objects.md)). A negative `level - item level` raises every threshold: unique, set,
rare, magic and superior get rarer, low quality more common. The division truncates toward zero.

## 2. Death drop inputs

| Input | Value | Confidence |
|-|-|-|
| Dropper seed | the dying unit's seed (written back after the drop) | high |
| Item seeds | the game seed `game+0x1a8`, two draws per item made | high |
| Magic find | killer's stat 80 + its owner's (hireling or summon -> player via `0x4a53c0`); only for a player or monster killer, 0 without a killer | high |
| Gold find | killer's stat 79 + owner's stat 79 | high |
| Players for NoDrop | `n = party + (players - party) / 2`, capped by the monster's stat 100 | high |
| `players` (`0x4251e0`) | players in the game; for game `+0x101` = 1, 2 or 3 (offline and hosted) raised to the `/players` setting (`0x2aa6a30`) | high |
| `party` (`0x506d10`) | living members of the killer's party in the killer's level, itself included (1 without a party), capped 1..8 | static only |
| Quest conditions | the game's quest record `game+0x21d8` (TC `QuestFlag` rows) | static only |

Gold: `ilvl + rand(ilvl * 5)` on the item's unit seed, then `mul=` and gold find as in
[engine-server.md](engine-server.md#3-items-and-drops). Potions, keys and arrows have no extra rule here.

## 3. Where each dropped item lands

`0x444680` runs once per item, after its quality roll and before the item is made:

1. **Start**: corpse `(x + 2, y + 3)` when a room holds that point (`0x38dfb0` from the corpse's room), else the
   corpse cell.
2. **Search**: D2RCore `0x3e2b2ec(room, &start, &corpse, size, 0x3e01, 0x801, any = 1)`. For every walkable corpse it
   equals the exe's `0x364e90` (classic free-coordinates search, distance 50).
3. **No spot**: the item is **not made**, its pick is spent and the game seed is not drawn.
4. **Made**: `0x43cd50` makes it on the spot, and the cell gets collision `0x200` before the next item's search, so
   items of one drop never stack on a cell.

| What | Value (build 93847) | Confidence |
|-|-|-|
| Unit size | 3 when game `+0x124` is set, else 1. `+0x124` is set once at creation (`0x4049d0`) from create-game flag bit `0x400000`; 0 in every game seen | high (size 1) |
| Size shape (`0x364d50`) | 0 or 1 one cell, 2 a plus, 3 a 3x3 box | static only |
| Start accepted | its room exists, no `0x3e01` bits under the size, clear view back to the corpse | high |
| Rings | rings 1..49 round the start: left and right columns top to bottom, then top and bottom rows; each cell's room looked up from the last room that held a cell (that room or a near room); smallest Manhattan distance to the start wins, first found on a tie; stops at the first ring with a winner | high |
| Nothing free | the start is used anyway when its room exists (`any = 1`) | high |
| Point in no room | `0x366250` reads collision `0x27` | high |
| View back (`0x3e33b0`) | tests the cell against `0x801`, then steps toward the corpse; each step's direction from `data/global/expfield.d2` at the offset from the corpse; the corpse cell itself is not tested | high |
| expfield.d2 | u16 version, u32 width, u32 height, then one byte per cell: direction 0..7 (N, NE, E, SE, S, SW, W, NW), 8 at the centre. Loader `0x3e3150`; the walk uses a fixed row stride of 256, so only a 256-wide file works (vanilla is 256x256) | high |
| Step offsets | classic tables at `0x2389cf0` / `0x2389d18` | high |
| Corpse on a blocked cell | D2RCore's search then differs from `0x364e90` (positions only; items and seeds still match) | observed |

The same search places gold piles at spawn ([monsters-spawn.md](monsters-spawn.md)).

**Drop at a unit** (quest objects, `D2GAME_DropItemAtUnit` pattern): the spot comes first, unit position + (2, 3);
with no spot nothing is made and no seed is drawn. Item level = the area's monster level (1 when unset), two
game-seed draws, then 0x9c action 0 to the room.

**Quest item for a player** (`0x517530`): made at the given level (the player's level for 0) with the "never
ethereal" create flag and full durability, put in the first free inventory cell (0x9d 4), else on the ground near the
player when allowed, else lost (its seed draws stay spent). An unidentified result is identified at once (static
only).

## 4. Item ids and seeds

| What | Value (build 93847) | Confidence |
|-|-|-|
| Item id | next value of the game's item counter (`game+0x144 + 4*4`), runs 1, 2, ... and wraps to 1 after `0xfffffffe` | high |
| Unit allocator (`0x4905a0`) | for any non-player unit steps the game seed once: low word -> `unit+0x30`, seed `unit+0x28` = `{low, 666}`; for an item a second step for the item start seed; then the counter moves on | high |
| Loaded items | every item and socket child read from a save goes through `0x43d900` -> `0x490410(type 4, ...)` -> `0x4905a0`: **two game-seed steps and one id each**, in save order (character, corpse, hireling, golem) | high |
| Join | on a fresh game the join burst shows ids 1, 2, 3, ... in save order | observed |
| New-item flags | `0x82010` ([engine-server.md](engine-server.md)) | high |

A server that rebuilds items itself must take exactly one pair of seed draws per item: a loaded item already took
its pair in the allocator, a generated item takes it in the generator.

## 5. Level requirement

D2R keeps classic `ITEMS_GetRequiredLevel` and adds one step. The result is the largest of:

- magic: prefix, suffix and automagic requirements (a negative value is skipped); rare: all six affixes and the
  automagic; crafted: the largest affix + 10 + 3 per affix, capped at 98;
- unique and set: the row's `lvl req` when not negative;
- the base's `levelreq`; each socket child's requirement;
- stat 107 (single skill): the skill's `reqlevel`; stat 97 (off-class skill): `reqlevel + 6` unless the skill is the
  holder's class. An affix's `classlevelreq` applies to its own class only, so the result depends on the holder.

Then stat 92 is added, floored at 0, and D2R applies `req += req * pct / 100` with `pct` from the holder's stat 209
(`passive_mastery_item_level_req_percent`, D2RCore lookup, kind 7). Each stat 209 layer: low 14 bits an item type (0 =
any), bits 14-15 a tag compared with a holder-derived value; the **largest** match counts, not the sum. Socket
children get the same percent. Confidence: static only.

## 6. Inventory: main weapon, component, refresh

Basics (the mover, checks, auto pick-up, stacks) are in [engine-server.md](engine-server.md#4-inventory-rules).

**Main weapon** (`0x387690`): the arm weapon whose id equals the inventory's weapon guid (`+0x38`), left arm checked
first. The guid follows classic's rules:

- insert (`0x389b60`), a weapon entering arm 4 or 5: an unequippable one gives the guid up if it held it. Otherwise it
  takes the guid unless the current main weapon stays: it stays when it is not a throwing potion (`tpot`) or the new
  one is one too, and the new one enters the left arm or is a throwing potion beside a non-potion;
- removal (`0x389f00`): the leaving weapon gives the guid up and a usable item of any type in the other arm takes it.
  A non-weapon leaving does not run this rule, but the inventory remover (`0x38ba30`, classic `INVENTORY_RemoveItem`)
  clears a guid that names the item it takes out, whatever its type, so a shield that took the guid gives it up when
  it goes. The engine removes through it for the merc (`0x51`) and on every player removal, so the guid is always
  none or an item in an arm.

The mover's commit (`0x471e90`) runs removal for each item leaving an arm, insert for each entering, then (unless
dead) insert again for the new right then left arm items. A loaded character starts with no guid; the first refresh
sets it, so the main weapon is the first equippable arm weapon in save order. Confidence: high (replayed).

**Component** (`0x34ba80`): the base's `component` (record `+0x124`) wherever the item lies, except 6 (left hand)
for an item at body location 4 or 5 when all hold: both arms hold equippable items (`0x36ad20`: not broken `0x100`,
not no-equip `0x4000`); neither is two-handed (record `+0x12b`); the class passes `0x348510` (Barbarian 4 or
Assassin 6); the item is not the main weapon. So an Amazon's shield beside a javelin keeps its own 7, a bow's quiver
keeps its slot. The sender (`0x535f60`) describes an item at the location the message is about: an item leaving a
grid is described where it was, an item a swap takes off at the arm it left. Hirelings: see
[skills-missiles.md](skills-missiles.md). Confidence: high (replayed).

**Item refresh order** (`0x470ca0`):

1. items that left the active places come off;
2. charms: on while usable;
3. every body item 1-10 comes off and is flagged no-equip (`0x475020`: weapon-guid removal, detach), except one
   already off and broken or flagged;
4. activation rounds in body-location order turn each usable item on (`0x475090`: flag cleared, insert rule, attach);
   while the main weapon found at the start stands, the other arm's weapon waits; rounds repeat until none turns on.

Nothing turns off again in the same refresh. The refresh (`0x34dbe0`) only flags the unit; the unit update sends the
stat messages (0x47/0x48). A gold pick-up runs no refresh.

**Stash reach**: there is no stash-open flag. Every move recomputes (`0x46c690`, `0x325200`) whether a stash object
(type 2, class `0x10b`) in a near room is within `0x33` of the player. So a player who never operated the stash can
bank gold (C->S `0x27`, `0x4ba580`) standing next to it, and one who walked away cannot. An accepted `0x27` sends the
stash gold as stat 15 (`0x1d`-`0x1f`); the carried gold goes out with the vitals pass. Opening the stash is in
[objects.md](objects.md) section 4.8.

## 7. Inventory requests (C->S, controller paths included)

| Id | Move | Rule | Confidence |
|-|-|-|-|
| `0x1e` (`0x4b8590`) | cursor item onto an arm, arm item to cursor, other arm item to inventory | refused when the cursor item would instead be used on the arm item (`0x4f2a80`: scroll into tome, stack onto stack, filler into socket, item into cube); the other item's cell must be the current free position | high |
| `0x57` (`0x4afc30`) | grid item to an empty arm, other arm item to the same page | named arm must be the one `0x36e360` picks ("allow swap": identified, usable, requirements; the type's two body locations; `seco` items take the second); other item's cell must be where `0x3877e0` puts it (its own cell if only the item lies under it, else the free-cell search `0x38c010` without the item) | high |
| `0x5a` (`0x4bc7b0`) | grid item to an arm, both arm items to the same page | as 0x57, plus the `0x4f2a80` refusal; if the first arm item takes the item's cell and the second finds no room, the first is searched again | high |
| `0x5d` (`0x4ac160`) | up to 16 inventory items to the belt ("hold to populate belt") | `@1` count 1..16, 16 entries of 6 bytes (u32 item, u8 cell x low / y high nibble, u8 belt slot); only `count` entries are read (the rest is stale). Item at the stated cell, type beltable (itemtypes `+0x17`), slot = the one `0x3873e0` picks: a column whose bottom holds the same kind (`0x36a8d0`), or an empty column whose belts.txt `defaultItemCodeColN` / `defaultItemTypeColN` takes it (`0x36aa00`), then the lowest free slot. One mover call | high |
| `0x5e` | belt to a grid page, or the ground for page >= 5 | the page's free position; column shifts as 0x24; to the ground sends no 0x42 | high |
| `0x4e` (`0x4ae440`) | sort a page | `@1` page (< 5), `@2` owner, `@6` n, n + 1 entries `{u8 cell, u32 item}`; the list names every item once, largest first; each item's cell must be what the free-cell search gives on an empty page in list order; one mover call | high |
| `0x22` (`0x4aaec0`), `0x60` (`0x4aafa0`) | variable-length displacement (0x60 touches body location 8: a belt change that displaces potions) | both run the large worker `0x4f6490`; lengths 17 + 7n and 32 + 7n | static only |

The server never refills a belt by itself: using a belt potion sends only 0x26. Without belts.txt defaults the
columns are health, mana, rejuvenation, town portal. A used-up potion's record goes out with flag `0x20`
(`0x4f40c0`). Message layouts: [network-messages.md](network-messages.md).

## 8. Vendor stock and gamble details

| What | Value (build 93847) | Confidence |
|-|-|-|
| Seeds | counts, qualities, upgrades and gamble picks on the NPC-control seed `game+0x5650`; every item on the game seed | high |
| Normal count draw | `0x3671c0` over vendor `Min..Max` (item level < 25). The model that matched every stock roll drew `a + rand(b - a)` with no draw for `b <= a`; [engine-server.md](engine-server.md) gives `rand(b - a + 1)` for the same RVA, so check before relying on the top value | high (stock), unverified (exact bound) |
| Upgrades | Nightmare/Hell past player level 25: one NPC-seed draw may turn the row into its exceptional or elite form; `NightmareUpgrade` / `HellUpgrade` codes replace the row | high |
| Cracked | a low-quality Cracked roll is re-rolled up to 5 times | high |
| Make whole (`0x53bb50`) | throwables to a full stack, charges refilled, durability to max; stock is marked identified | high |
| Placement | on the item type's `StorePage` (storepage.txt); one-high items from the right, others from the left, column-major; a weapon that does not fit page 1 tries page 2. Store page is item data `+0x55` | high |
| Quivers | stocked with a full stack | high |
| Store size | 4 pages of 10x10 (400); a slot freed by a buy is reused | static only |
| Gamble grid | per (player, NPC), freed when the player's mode leaves trade/gamble (`0x502f60`) | high |
| Gamble upgrade | exceptional with chance `(ilvl - uberLevel) * GambleUber + 1` in 10000, else elite the same way with `GambleUltra` | high |
| Gamble quality | one draw in 100000 against difficultylevels rare/set/unique (clamped 0..100000); else magic. A unique already made in this game (tracker `game+0x3638`, kept across fills) becomes rare | high |
| gamble.txt order | sorted by item level with the CRT `qsort`, so equal levels keep that algorithm's order; `gambleLimit[level]` counts usable entries | high |
| Gamble buy | the item is identified; a ring or amulet is replaced from the same code, anything else by a random entry sharing a normal/exceptional/elite code, at an item level no lower than that entry's | static only |
| Magic stock allowed | item record `+0xe4` bit 0 (`0x3742f0`) | static only |

**Restock.** The game keeps a store list at `game+0x3838` (count `+0x3840`, entries 0x78 bytes): filled `+0x34`,
mark `+0x35`, 64-bit stamp `+0x38` (clock `0x120d720`, ms). Subscribe (`0x540850`) fills an unfilled store for the
subscriber's level and stamps it; it refills a filled store only on a **trade** (not gamble) subscribe with at most
one player at the NPC (`0x5448b0`) and the mark set. A first gamble subscribe fills the grid, then the trade store.
The mark comes only from the town-leave rule `0x503290`, run when a player leaves a town (1, 40, 75, 103, 109) for
that act's trader stores:

- players still on the town level: a store with `stamp + 240000 < now` (signed 64-bit) is marked and restamped;
- nobody left: each store is cleared (`0x545800`, then `0x540aa0`; filled 0, stamp 0) unless its NPC is missing or
  busy, then only marked; remaining players' interactions with that NPC end (0x92);
- callers: act change `0x488180` (before the move, so the traveller counts itself: never a clear), level change
  `0x486970` (after the move), leaving the game `0x502e50` (leaver subtracted; nothing from level 109 or with game
  `+0x101` = 3).

So the classic "4 minutes and an empty town" refresh holds, in two steps. Confidence: high (480 replayed steps).

## 9. Prices and transactions

Price function and its inputs: [engine-server.md](engine-server.md#5-town-npcs-and-vendors).
Extra rules (high unless noted):

- Stat costs come from itemstatcost `Encode`, `Multiply` / `Add`, with op-13 percent stats applied to their targets;
  charges per skill (skills `cost mult` / `cost add`, `CostPerCharge`).
- Unidentified items price defence from the base stat, not the stat list total; stacks price per unit times
  quantity; durability scales sell and repair.
- A quest discount replaces the NPC multiplier once its quest word has bit 0 or bit 1 (the bit masks at `0x1d996d0`
  are 32-bit entries).
- Stat 87 is summed over the active items (body slots without the inactive swap pair, charms in the grid), clamped
  0..99.
- Gamble price: the normal form's `gamble cost`, scaled by the player's level.
- Repair price needs an identified, repairable item, else 0. **Repairable** (`0x36ab40`): not ethereal, and charges
  below full, or the type's `Repair` column with a replenishable throwable or real durability (`nodurability` clear,
  durability stats nonzero, not indestructible stat 152). Unlike 1.14d the identified test is not here, so
  unidentified stock is made whole but has repair price 0.

Each handler answers 0x2a `{0, result, guid, carried gold}` (static only):

| Handler | Refusal -> result | Success |
|-|-|-|
| buy `0x53d850` | id not in this NPC's store (or this player's gamble grid), or quote != price or 0 -> 7; gold + stash too low or negative -> 12; no backpack room -> 10 | 0: pay (carried gold first, then stash, `0x5416d0`), item to backpack |
| sell `0x53f1a0` | the player holds no item of that id -> 11 (1.14d `AlreadyHoldingItem`; the store sees the grid, the body and the cursor, so a belt, cube or stash item counts as not held); held but not on the grid or the cursor (equipped), or a quest item (flag `0x1000`, `quest` column, type `ques`) -> 9; the message's location kind is not where the item is (0 backpack grid, 4 cursor; 1.14d `SUNITNPC_SellItem` compares the item's mode) -> 9; quote != sell price or 0 -> 9 | 1: credit at most `level * 10000 - carried` (the rest is lost) |
| repair `0x53fe00`, all (item -1) | quote != sum of body repair prices -> 9; too little gold -> 12 | 2 |
| repair, one item | quote 0 -> 12; not the player's, not repairable, nothing to repair -> 9 | 2; a partial repair when quote = carried gold, the item is not broken and gold > 0 |

Partial repair: `ratio = ((u64)price << 10) / (max - cur)` (classic did this in u32 and wrapped past 2^22);
`points = (carried << 10) / ratio`; when nonzero all carried gold is paid and durability becomes
`min(cur + points, max)`.

**What a sale relists** (`0x38c8d0`): the sold unit itself, same id, via 0x9c 0x0b on this NPC's store page;
identified, full durability and quantity. Destroyed instead: Cracked low quality; broken, personalized or ethereal;
type `play`; filled sockets; a `carry1` unique; outside Normal `hp4 hp5 mp4 mp5`; this NPC's permanent store item;
no room. The seller gets the item's leave first (0x22 for a scroll or tome, then 0x9d 5 with item flag `0x20`, or
0x42 for a cursor item; then 0x47/0x48), then 0x2a, then the 0x9c 0x0b and the gold (1.14d D2GAME_STORES_SellItem
`0x579510`; the order after 0x2a is inferred). Without flag `0x20` the client takes 0x9d 5 for a pick-up and keeps the
sold item on its cursor (observed). Buy, sale and identify each force a save when they go through
([saves.md](saves.md#when-the-game-writes-a-save)). The handler also checks the client's page and cell against
the spot `0x38c8d0` picks (static only).

**Store listing**: one 0x9c 0x0b (add to shop) per item, record at its store cell with page = store page + 1;
0x9c 0x0c removes an item from the shop.

**Repair messages** (`0x53bb50`): each fix is a stat set through `0x43eb30` that sends 0x3e to the owner: quantity
(stat 70) for throwables, each charged skill (stat 204 = `max << 8 | current`, via `0x46f310`), durability (stat 72)
to max with the stat 75 percent (`0x2f4b60`). A broken item goes through `0x46e7a0`: flag `0x100` cleared, back
into use (`0x475090`, `0x470ca0`), then 0x7d `{owner type, owner guid, item guid, 0x200, 1}` to the owner and to
viewers when worn, then 0x47/0x48. No 0x9d 0x14 for a repair (static only).

**Identify** (`0x46e8c0`): sets flag `0x10`, clears no-equip `0x4000`, the sender sends 0x9d 0x14, the unit is
flagged for refresh, sound 6 (`0x491960`). The sender clears the transient item flags `0x2e9` on each item it sends,
then sets the one it was given (1 identify, `0x200` repaired). Cain's identify-all (`0x53c2b0`) takes only stored
items (mode 0) on page 0, and page 3 when the message's cube id is the player's cube (-1 without one); body, belt
and stash items are left alone. Count `0x385f10` / `0x386160` (with cube), 100 gold each. A set or unique with the
drop-tracker flag is registered at identify (`0x424970` / `0x4247d0`).
