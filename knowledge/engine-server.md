# The server half of D2R: game loop, tables, items, town, quests, death

D2R runs the game logic (the "server" half, classic D2Game) inside the same `D2R.exe` that draws the client, also in
single player and when a client hosts a TCP/IP game. A plugin that changes drops, vendors, quests, deaths or
anything "the game decides" hooks this half. This file is a map of it for mod and plugin authors, as observed in
**D2R 3.3 build 93847** (image timestamp `0x6ab3782c`).

- **Every address here is an RVA for build 93847 only** (image base `0x140000000`). Re-find it on any other build
  ([engine-re.md](engine-re.md)); a plugin checks the image timestamp and the entry bytes before it hooks.
- Confidence labels: **high** = named by strings/asserts or matches the classic function line for line, or checked
  in game; **medium** = inferred from the call graph; **static only** = read in Ghidra, never observed running;
  **unverified** = a classic rule assumed to hold.
- Messages (ids, sizes, reply order) are in [network-messages.md](network-messages.md). Combat, regeneration,
  experience and formulas are in [combat-formulas.md](combat-formulas.md). Functions already listed in
  [d2r-93847-addresses.md](d2r-93847-addresses.md) are not repeated; the cube is in
  [engine-limits.md](engine-limits.md); world objects (chests, shrines, wells, doors, traps) are in
  [objects.md](objects.md).

## 1. Game loop and tick

| What | Value (build 93847) | Confidence |
|-|-|-|
| Message pump `SrvProcessMsgs` | `0x406920`: drains the system queue (create, join, leave, ping) and the game queue (per-client messages) once per loop | high |
| All games tick | `0x406400(nowMs)`: every **40 ms (25 frames/s)**; returns 1 when it ticked, the loop calls it again to catch up | high |
| One game frame | `0x406550(game)`: increments the frame counter `game+0x170` first, then environment per act, clients, units, quests | high |
| Client join state machine | `0x408ea0(game)`, called every frame from the frame | high |
| Empty-game sweep | `0x406e40`: destroys a game with no clients for **300 s**, or at once when `game+0x1a5d8` (closing) is set | high |
| Destroy game | `0x4057c0(gameId)` | high |
| Client list teardown inside destroy | `0x486be0`: a good hook to reset per-game plugin state | high (hooked in a working plugin) |

The server is single-threaded per process: messages are drained, then games tick, all on one loop. Plugin code
running in a game hook does not need locks against other game code, but must be quick: a slow hook stalls every
game.

Frame order inside `0x406550` (high for order, the role of some callees is low):

1. `++game+0x170` (frame counter).
2. Environment per act (day/night), with an S->C environment message to every in-game client.
3. Clients: `0x408ea0` (join states, act start).
4. Unit and room updates (the bulk of the simulation).
5. Every 20 frames: quest timers (`0x516e40`, medium). Every 12 frames and every 11 frames: two other periodic
   jobs (roles not identified).

Useful constants: max **8 players** per game (`game+0x105` holds the creator's limit); up to **1024 games**, ids
1..0x400 rotating; the transport drops a connection silent for **15 s**; a 30 s per-game client check.

### Message dispatch and the "dead or busy" gate

C->S game messages go through one dispatcher `0x4f2fa0(game, player, packet, len)` and a table of 0x67 handler
pointers at `0x1d2a790` (8 bytes each, no flag column; a null entry asserts). Before the handler runs, a per-id
gate (byte table `0x4f30f0`) applies:

| Gate | Runs when | Ids |
|-|-|-|
| 0 | player not dead | 0x01-0x12 (moves, skills), 0x42 |
| 1 | always | 0x15 (chat), 0x3c (select skill) |
| 2 | player in the dead mode (17) | 0x3f (respawn) |
| 3 | not dead **and** not in state 0x36 (`uninterruptable`) | everything else |

A blocked message is logged and dropped, never answered. A handler may return 4 = "not now": the message is queued
and replayed next frame (`0x519f10`). Hooking one handler entry in the table is the cheapest way to see or veto one
kind of client request.

### Per-game fields worth knowing

| Offset | Meaning |
|-|-|
| game +0x104 | difficulty (0/1/2) |
| game +0x105 | max players |
| game +0x106 | **game edition** = the dataFlag (section 2) |
| game +0x12c | game seed (random, or fixed when a debug global is set) |
| game +0x138 / +0x140 | first client (next at `client+0x628`) / client count |
| game +0x170 | frame counter |
| game +0x1a8 | game seed used by item creation (two draws per item) |
| game +0x21d8 | quest-control block (section 6) |
| game +0x5650 | NPC-control seed (vendor stock, gamble, mercenary roster) |
| game +0x5658 | game flags (`0x800` hardcore) |

From a player to its client record: `*(PlayerData(player) + 0xf0)`, `PlayerData` = `0x34b240(player)`.

## 2. Data tables and the game edition (dataFlag)

The byte every table getter takes first (`game+0x106`, and `unit+0x1bd` read by `0x34a0e0(unit)`) is the **game
edition** (`gameVersion` in the engine's asserts), not a client/server switch:

| Value | Enum name | Table set |
|-|-|-|
| 0 | Invalid | none |
| 1 | Classic | set A |
| 2 | Expansion | **the same set as Classic** (the loader points slot 2 at slot 1) |
| 3 | Garden | set B: the Reign of the Warlock (RotW) content |

- `0x300a90(version)` returns the table set (four 16-byte shared-pointer slots at `0x2a9a580`, asserts version <=
  3). The loader `0x2ff490` fills slot 1, aliases slot 2 to it and fills slot 3; the per-table loader `0x2ffcd0`
  loops over versions 2 and 3.
- The game's edition comes from the create request and is copied into every unit at creation, so code that only
  has a unit finds the same tables.
- **Mapping to the two banks** (inferred from the record counts, medium): Classic/Expansion games read
  `data/global/excel/base/`, RotW ("Garden") games read `data/global/excel/`. This is why a mod writes **both**
  banks ([tables.md](tables.md)): which one a character uses depends on the edition of the game it plays in, and a
  character converted from Expansion to RotW keeps its row numbers.
- A few getters also take "is not Classic" as a second argument (the hireling getter `0x3965d0(version, version !=
  1, ...)`). Classic games refuse expansion-only things such as Act V waypoints and portals and mercenary
  resurrection.
- `game+0x128` holds 2 for a Classic game and 0x65 otherwise; vendors compare item `version` against it (section 5).
- Rows whose first cell is `Expansion` are separators; every index skips them.
- Lookups by table position: levels, lvlprest and lvltypes are found by row position, not by an id column; lvlprest
  by level is a first-match scan.

## 3. Items and drops

### Random numbers

One generator for every seed: `{u32 lo, u32 hi}`, step `v = lo * 0x6ac690c5 + hi` (64-bit), `lo = low32(v)`,
`hi = high32(v)`.

| RVA | Function |
|-|-|
| `0x153b00` | `rand(seed, n)`: `n < 1` returns 0 with **no step**; else one step, `lo & (n-1)` for a power of two, else `lo % n` (`n == 1` still steps) |
| `0x1b9a60` / `0x3671c0` | `range(a, b)` = `a + rand(b - a + 1)`, no step when `b <= a` |
| `0x3d5860` | property value: returns `a` with no step when `a == b` |

A drop uses three seeds: the **dropper's unit seed** (treasure class picks, quality roll), the **game seed**
(`game+0x1a8`, two draws per created item: its unit seed and start seed) and each **item's seed** (affixes,
properties, sockets, ethereal). Base stats (durability, defence, quantity, gold) come from the item's unit seed.

### Treasure class executor (`0x4404f0` -> `0x444920`)

- Up to **6 items per call** (the caller's buffer).
- `Picks < 0`: entries are taken in order, each as many times as its weight, with no draw. Otherwise each pick
  draws `rand(total + NoDrop)`; below NoDrop drops nothing.
- **NoDrop with players**: with `n = party + (players - party) / 2` above 1, NoDrop becomes `(1 - r) * total / r`
  where `r = 1 - (nd / (nd + total))^n`. For a monster dropper `n` is capped by the dropper's stat 100 (at least 1);
  chests have no cap.
- A sub-TC inherits the parent's quality ratios where its own are zero, else takes the larger.
- Gold `mul=` scales by `mul/256`. Gold find scales by `(100 + killer's stat 79 + owner's stat 79) / 100` (the owner
  is the player behind a mercenary or summon), applied only while the 6-item buffer is not full.
- Auto classes (`weap3`, `armo3`, ...): records 1-160 are generated per item type with `TreasureClass` set, in steps
  of 3 levels. An item joins when spawnable, rarity > 0 and level in `(3k-3, 3k]`; its weight is the **item type's**
  `Rarity`.
- `0x397290`: `tc_level` upgrade inside a TC group (highest record of the group whose level is <= the given level).
- `0x441300`: a TC drop entry that plugins have called directly to give quest or bonus rewards (high; used in a
  working plugin).
- Conditions: a row with a ladder season drops only with ladder content on (`0x403fe0`). `QuestFlag` (Classic) /
  `QuestFlagEx` (other editions) are checked against the **game's** quest record, not the killer's; a quest id of
  42 or more always passes, otherwise the quest needs bit 0, 13 or 15. `ConditionCalc` / `DropConditionCalc` use a
  small expression language with `cond('Difficulty', ...)`, `cond('Desecrated')` and similar.
- Keep TC graphs acyclic and weights small; no engine-side guard against a cyclic chain was found (unverified).

### Quality roll (`0x4421b0`, dropper seed)

1. Fixed: normal-only item types give normal; `unique`-column items give unique; a magic-only quest type gives
   unique.
2. itemratio.txt row: picked by "uber" (exceptional/elite weapon or armour) and class-specific, highest `Version`
   <= 100.
3. Chance `c = (base - (ilvl - item level) / divisor) * 128`, then magic find: `c * 100 / (100 + e)` with `e` = MF for
   magic, and diminishing `MF * k / (MF + k)` for unique (k 250), set (500) and rare (600). Floored at the row's
   minimum, then the TC's ratio for that quality takes off `ratio / 1024`.
4. Draws in order unique, set, rare (rare-capable types only), magic: each `rand(c) < 128`. Then two draws split the
   rest into superior / normal / low.

### Item creation

| RVA | Role |
|-|-|
| `0x444680` -> `0x43cd50` -> `0x4905a0` | create an item from a parameter block (`+0x18` ilvl, `+0x40` quality, `+0x50` forced row, `+0x54` create flags) |
| `0x43ef10` | base stats: gold `ilvl + rand(ilvl*5)`, durability `rand(d/2) + d/2` (max 255), defence in `[minac, maxac]` |
| `0x442d60` | quality dispatcher (handlers below) |
| `0x815400` | affix picker (also automagic, with the group as filter) |
| `0x816270` | rare names: uniform pick among rows whose itypes match and etypes don't |
| `0x3d4db0` | `propertygroups.txt` expansion (D2R addition) |
| `0x517530` | quest item creation (game, player, code, ilvl, quality, allowGround) |
| `0x43fbf0` | drop the unit's "drop item code" at a unit |

Quality handlers and their fallbacks (each fallback resets the item seed and runs again):

| Quality | Handler | Falls back to |
|-|-|-|
| 1 low | `0x58a610` | normal |
| 2 normal | `0x443670` | |
| 3 superior | `0x58b9e0` | normal |
| 4 magic | `0x442c60` | superior, normal |
| 5 set | `0x58c2f0` | magic, superior, normal (durability x2 first) |
| 6 rare | `0x58bba0` (3-6 affixes) | magic, superior, normal |
| 7 unique | `0x443910` | rare, magic, superior, normal (durability x3 first) |

Rules mods trip over:

- A new item is created with flags `0x82010` (identified, new, dropped).
- Sets and uniques (not quest or `unique`-column items) are registered with a per-game drop tracker; that sets item
  flag `0x10000000` (the tracker is per game, at `game+0x3638`).
- **Ethereal**: one `rand(100)` on the item seed after the quality handler, ethereal on a draw <= 4 (about 5%). Only
  version-100 weapons/armour with durability, not low quality, not set, not quest, and not indestructible (stat
  152). Ethereal = damage or defence x3/2, max durability halved (+1). An `ethereal` property placed before
  `indestruct` in a unique's property list still makes it ethereal.
- **Sockets**: for low/normal/superior items only, capped by item level against the type's MaxSockets thresholds,
  by the inventory size and at 6.
- **Affix picker**: a candidate needs spawnable, `version` <= 100, `level` <= affix level, `maxlevel` (when set),
  `rare` for rare items, an itype match and no etype match, and no other class's class-specific affix. **Groups
  are exclusive across prefixes and suffixes.** One draw `% (sum of frequencies + 1)`: an empty pool still draws.
- Affix/rare type lists end at the first blank cell; an unknown type code matches nothing (several vanilla rows
  name the nonexistent `staff`; codes longer than 4 characters are cut to 4).
- **Property functions**: table `0x2386ab0`, filled for 1-25 and 36; an empty slot stops the property line
  ([engine-limits.md](engine-limits.md)). Func 25 picks one random stat of a named properties row. A
  `propertygroups.txt` group either applies every line (mode 0) or picks `count` weighted lines (modes 1 and 2,
  with and without replacement); group parameters are plain numbers, so a skill **name** there reads as 0.
- Quest items with `questdiffcheck` get stat 356 = the difficulty.
- Staff mods with expansion data group the class's skills by required level (1, 6, 12, 18, 24, 30).
- Crafted (8) and tempered (9) qualities were not traced.

## 4. Inventory rules

Item locations are a 16-byte struct `{u32 mode, u32 page, u16 x, u16 y, u32 bodyLoc}`; modes 0 stored (grid), 1
body, 2 belt, 3 ground, 4 cursor. Grid pages are 0 inventory, 3 cube, 4 stash (the save stores page + 1). Body
locations are 1-12; 11/12 are the swap weapons.

Every grid, belt, body and cursor request ends in one transactional **mover** `0x471e90` with a list of `{item,
from, to}` moves; it works on a copy and commits only if the whole result is legal. A refused request changes
nothing and **sends nothing** (the server only logs). Checks, in order:

1. **Source**: the client must name the exact location of each item (a grid's top-left cell, the body slot, the
   belt slot). The stash is reachable only near the stash object (or for a forced move); nothing but cursor and body
   while dead.
2. **Target**: the slot is free; belt only for beltable types; a grid cell range fully inside the page and free (the
   cube page refuses the cube); a body slot only for an identified item of the right class, on one of its type's two
   body locations (11/12 count as 4/5); no second quest item of one code and no second `carry1` unique.
3. **Whole layout** (`0x46e050`): the two hands and the two swap hands must pair (launcher + its ammo; quiver with
   nothing else; no two-hander except a one-or-two-hander for a Barbarian; two weapons only for a Barbarian or two
   claws for an Assassin; else one weapon + one non-weapon; the exact D2R rule was not traced, the classic rule is
   assumed); belt contents fit the worn belt's `belts.txt` row (row 2 with no belt); the cube page cannot change
   without a cube.
4. **Requirements** (`0x36bc50`) for items newly on body slots 1-10: strength/dexterity (stat 91 item_req_percent,
   -10 for ethereal), level, identified, class.
5. **Commit**, then the inventory refresh `0x34dbe0`.

Differences from classic worth knowing:

- A belt cannot come off while potions sit above the new capacity (classic moved them).
- Stash gold limit is a flat **2,500,000** (`0x34b310`); carried gold is `10000 x level` (`0x34b320`).
- The cube is recognised by its item code, not by `hasinv` (which marks socketable bases).
- Automatic pick-up (`0x471950`): scroll into its tome, stack onto an equal stack, a free body slot (requirements
  first), the belt (a column already holding that potion kind, else the bottom row for autobelt items), then the
  inventory (one-row items searched from the bottom right, taller ones from the top left).
- Pick-up range is 4 (`UNITS_GetDistanceToOtherUnit` distance).
- Stacks merge only with the same base and quality row, low/normal/superior quality, same ethereal flag, same damage
  stats, no sockets; limit = `maxstack` + stat 254, at most 511.
- Active item stats: body slots 1-10 and charms in the inventory grid, not broken, requirements met; ammunition only
  while the other hand holds its launcher (itemtypes `Quiver`). The refresh turns off failing items, then repeats
  activation passes, so removing a +strength ring can switch off armour that needed it.
- A dual wielder's second weapon adds its stats without its damage.
- Use (C->S 0x26): an item is **not** used up when it is a tome or its `pSpell` is 0, 7 (cube), 14 or 17 (the
  20-entry item spell table `0x236fe30`). pSpell 1 identify, 2 town portal, 3/5/6/9 potions, 7 open cube.
- Potion class bonus comes from charstats `HealthPotionPercent` / `ManaPotionPercent` in D2R (classic hard-coded it).
- Socketing (`0x4f9f40`): the filler must be type `sock`; it becomes a child of the target and its properties count
  for the parent. Completing a runeword sets the runeword flag and triggers a save.
- Weapon swap is a physical move between body slots 4/5 and 11/12; see [network-messages.md](network-messages.md)
  for message 0x50 and its replies.

## 5. Town, NPCs and vendors

### NPC service table

`0x237ea60`: **43 rows of 0x1c bytes**, getter `0x3971a0`. Byte-patching a row changes what an NPC offers, with no
code (high; done in a working plugin):

| Offset | Meaning |
|-|-|
| +0x00 s16 | monstats class of the NPC |
| +0x02 / +0x03 / +0x04 / +0x05 / +0x06 / +0x07 / +0x08 / +0x09 | repair / identify / resurrect merc / respec / heal on talk / hire / trade / gamble |
| +0x0a | act (0-based) |
| +0x0b | special service: 1 imbue, 2 sockets, 3 personalize |
| +0x0c s32 | travel destination level (0 = none) |
| +0x10 | gate: quest index + 1 (0 = none) |
| +0x11 | hire level (hire allowed from this player level even without the quest) |

Gate semantics: respec and imbue need the gated quest's "reward pending" bit; hire and travel need "reward granted"
(section 6).

Interaction (static only unless noted): the server owns the player-NPC association (`playerData+0xf8` NPC GUID,
`+0xfc` class, `+0x100` mode: 2 trade, 3 gamble, 4 hire). Opening an NPC needs no cursor item, the NPC on the
player's level and within 7. Every later request re-checks the NPC, the distance, the service and the association.
Several players can talk to one NPC; it keeps a speaker count, which is also what stops it walking. The NPC menu is
built by the client (`0x1147a0`), the server answer by `0x4b0470` (C->S 0x38: kind 1 trade, 2 gamble, 3 hire).

Town NPCs do not wander at random: their AI (`0x5c1e80`, monai NPC entry) follows the preset action list of the map
with the NPC's own seed, and stops while someone talks to them.

### Vendor stock (`0x540850` -> `0x53c9f0`)

- Built **once per game per NPC** (runtime record flags `+0x34` initialised, `+0x35` restock), not on every menu
  open. The classic refresh (town empty, every 240 s) is **unverified** in D2R.
- Item level `I = 0x53cfa0(game, npc, playerLevel + 5)`; in **Normal only** it is capped by actinfo
  `maxnpcitemlevel` (vanilla 12/20/28/36/45 per act). Nightmare and Hell have no cap. Hooking `0x53cfa0` changes the
  cap per difficulty.
- Seed: the NPC-control seed `game+0x5650`.
- For each row of the NPC's list (from the per-NPC `<Npc>Min`/`Max`/`MagicMin`/`MagicMax`/`MagicLvl` columns of
  weapons/armor/misc): skip bases above `I`; normal copies `range(Min, Max)` only while `I < 25`; quality per copy
  from `rand(100)`: below I 5 low on >90; I 5-9 superior on >85; I 10+ superior on >74.
- Magic copies when `I >= MagicLvl`: `range(MagicMin, MagicMax + bonus)` with bonus 1, or 2-3 when `I > 24`. So
  past item level 25 a vendor can show up to 2 more magic copies than the table says (the compare is at
  `0x53cd2b`, `cmp ..., 0x19`). `MagicLvl` is an item-level floor: a high value hides all magic stock below it.
- A permanent list (one normal copy each, potions, scrolls, keys) follows.
- Per-item creation `0x540ea0`: more than 32 failures stop the loop.

### Prices (`0x36f0c0(player, item, difficulty, questRecord, npcClass, kind)`)

Kind 0 buy, 1 sell, 2 gamble, 3 repair (callers: buy `0x53d850`, sell `0x53f1a0`, repair `0x53fe00`). No RNG. Base
cost plus quality/affix `CostMult`/`CostAdd`, quantity, defence, durability, charges; identified decides whether
affixes count. NPC multipliers come from a cost row (`0x397160`, `/1024`); up to three quest discounts apply when the
quest has granted or pending; sell is capped by the NPC's max-buy per difficulty; reduced-price stat 87 (capped at
99) applies to buy, repair and gamble but **not** sell. Minimum price 1. Gold comes from carried gold first, then the
personal stash, never the shared stash. The server recomputes every price; the client's quote must match exactly.

### Gamble (`0x541880` fill, `0x541ab0` quality, `0x541d20` place)

- **14 offers** (loop bound at `0x541a7e`); offers 0 and 1 are forced to ring and amulet after the base draws.
- Per offer, NPC-control seed: ilvl = `clamp(playerLevel + rand(10) - 5, 5, 99)`; a base from the eligible gamble
  list (Classic games reject expansion bases); then exceptional and elite upgrades from itemtypes/armor/weapons
  `GambleUber` / `GambleUltra`; then quality from difficultylevels' unique/set/rare chances with one
  `rand(100000)`. **Magic find does not apply.**
- Buying replaces only that offer. The offer list lives in player data `+0x108`.

### Identify, hire, resurrect, heal, respec

- Identify (Cain, `0x53c2b0`): unidentified items in the inventory and the cube (not equipped); free once quest 4
  ("Search for Cain") is granted or pending, else 100 gold each.
- Hire (`0x53d350`): roster built once per game per NPC from the NPC-control seed (`0x541400`, ten picks); the quote
  (`0x3c17b0`) uses a local seed per entry so re-quoting changes nothing; gold is taken only after the spawn works.
  Mercenary level and stats on hire/level-up: `0x544f60` (hook point for class bonuses). Formulas:
  [combat-formulas.md](combat-formulas.md).
- Resurrect price `0x3c1ad0` = `min(max, trunc(level^2 / max(1, div)) * mul)` from the hireling row (defaults
  50000 / 2 / 15). Expansion games only. Hooking it replaces the price. The hire and resurrect requests, the merc's
  death, its messages and its save record are in [hirelings.md](hirelings.md).
- Heal on talk (`0x53fc50`): life, mana, stamina to max, removes listed states, heals pets.
- Respec (`0x4b2530`, D2R respec `0x580f20`): needs the NPC respec byte and quest 41 "pending"; spending it sets
  quest 41 granted. The reset runs in this order (static only): skill refund `0x4360f0` (each owned class skill's
  base level goes to 0 but the entry stays, passives refresh, S->C 0x21 per skill with the remaining bonus level,
  then stat 5 `newskills` gains the total), then the stat reset `0x52ddf0` in the order strength `0x52e020`, energy
  `0x52df20`, dexterity `0x52ded0`, vitality `0x52e070`. Each moves `charstats start - current base` between the
  attribute and stat 4 `statpts`; vitality and energy move max life/stamina/mana by `per-point byte * delta * 0x40`
  (32-bit, wraps), the current value only when the amount is positive, then cut to the new max. A save whose
  attributes sit below the start values makes the pool go negative. Sound 2 plays at the end.
- Imbue (Charsi, C->S 0x2e, exactly 21 bytes: +1 service, +5 NPC guid, +9 item guid, +0xd "has a location", +0x11
  location; static only). `0x4fc230` runs **twice**: the handler with commit off, then a task on the game's task list
  with commit on. Both passes check NPC reach (`0x53b8f0`, 0x32), the item (exists, identified, location matches,
  `0x3865b0`), the service on the NPC row, the quest gate pending (quest 3 for Charsi), service = imbue (sockets and
  personalize exist only on Act V rows) and the item test `0x36aed0`: an items.txt row, not gold, no flag 0x1000
  (unsellable), `bitfield1` bit 0 set (clear for misc), no throwable type in a Classic game, not a quest item except
  Wirt's leg, nothing socketed and no flag 0x800, quality low/normal/superior. Both passes work on a 0x4888-byte
  inventory copy (`0x472590`, applied by `0x471500`). The new item (`0x43cd50`): same class, rare, ilvl = player level +
  4 (D2R adds 4 at every level; classic only above level 5), capped at 255, ethereal kept, identified and repaired,
  personalization kept. The spend `0x5da1c0` deletes every Horadric Malus carried and sets quest 3 granted. Reply S->C
  0x58 (7 bytes: item guid, u16 6). Quirk: when the first pass's inventory step fails the reward is already spent.
- Payment `0x5416d0`: carried, then stash. A pool that would end below 0 or above its cap is set to 0.

### Waypoints, town portals, travel

- Town test `0x327850`: levels 1, 40, 75, 103, 109 are towns.
- Waypoint record per difficulty: `*(playerData + 0x58 + 8*difficulty)`, 112 bits; waypoint `n` is bit `n%16` of
  word `1 + n/16` (word 0 is `0x0102`). Level -> waypoint number comes from levels.txt (row `+0xec`, `0x3d6670`;
  0xff = none); test `0x3d6930`, set `0x3d6890`. **Entering a level does not activate its waypoint**; touching the
  object (OperateFn 23, `0x58fe00`) does. The waypoint panel side is in [ui-layouts.md](ui-layouts.md).
- Waypoint travel (C->S 0x4b, `0x4b6f10`, travel `0x5933f0`): the target bit must be set, a waypoint object within 50
  whose own level's bit is also set, no cursor item, no Act V in a Classic game, and **10 s since the player last
  declared hostility** (playerData `+0x1e8`, read by `0x507bd0`, written only by `0x507f30` from the declare-hostile
  command; warps do not set it, so back-to-back waypoint trips are allowed) (high). A request for the level the player
  is already on is accepted and does nothing. You arrive at the target's waypoint object (entry kind 13): a free-spot
  search from the waypoint - (1, 1), then a walk to that spot + (3, 3); the level warp is `0x488180`.
- Town portal `0x46fa70` (skill 411 = srvdofunc 109, or the item spell table): refused in towns and where the level
  forbids it (`level+0x184`); one blue pair per player (a new cast retires the old); the field end starts its free-spot
  search at the caster - (2, 0), the town end at entry kind 11. Generic portal creator `0x432ce0`. Portal use
  (OperateFn 15, `0x58f680`): mode 2; a player in hostility (`0x424a60`, computed at each use) may take only its own
  portals; **5 s since the last hostility declaration** (the same `+0x1e8` as waypoints, not the last warp); a non-owner
  must be in the owner's party (a missing owner is let through); state 0x66 blocks reuse for 75 frames; the player is
  put on a free spot at the twin and walks to that spot + (5, 5); the owner going town -> field uses the pair up,
  field -> town does not. The red portals have no owner and are never used up.
- Objects (C->S 0x13 / 0x41, `0x4b0f80` -> `0x593630`): the object row must allow operating in its current mode
  (`Mode0-7`), unit distance at most 50; only OperateFn 25 takes a cursor item. OperateFn numbers: 6 tome, 9 stones,
  10 cage, 12 tree, 15 portal, 21 rack, 23 waypoint, 32 stash. Every check, the init and operate tables, chests,
  traps, shrines, wells and doors: [objects.md](objects.md).
- Warp hook used by plugins for entry rules: `0x491ff0`.

## 6. Quests

**Character record** (high): one 0x60-byte block per difficulty at `*(playerData + 0x40 + 8*difficulty)`; quest `q`
is the little-endian u16 at byte `2*q`. Bit helpers: test `0x325c50`, set `0x325c00`, clear `0x325990`, clear the
intermediate bits `0x325930`.

| Bit | Meaning |
|-|-|
| 0 | granted (reward taken) |
| 1 | pending (reward can be collected) |
| 2 | started |
| 3 / 4 | left town while active / entered the quest area |
| 5-11 | quest-specific |
| 12 | log update |
| 13 | primary goal done |
| 14 | done in this game while not eligible |
| 15 | completed in an earlier game |

Save offsets for these blocks: [saves.md](saves.md). Granted, pending and done are separate; reward dialogs need
pending. Which bits D2R clears on load is not settled (classic: for quests 0-40, clear 13 and 14 and set 15 where 1
is set). Quest 41 is outside that reset (respec bookkeeping, see above).

**Game side** (`*(game+0x21d8)`): a list of quest objects (`0x518220(game, q)`) with a game state (`+0x14`), the shown
status (`+0x13`), a "not intro" byte (`+0x11`, progress needs it), a "rewarded in this game" GUID set (`+0x1e8`) and
event callbacks at `+0x270 + 8*event`. The quest-control seed is at `+0x24` (`0x518270`).

Events: 0 NPC opened (greeting), 2 NPC closed, 3 level changed, 4 item picked up, 5 item dropped, 8 monster killed,
9 player dropped a quest item, 10 player left, 11 dialog choice, 13 player started the game, 14 player joined.
Dispatch `0x518ec0(event, all)`; wrappers: NPC open `0x515980`, close `0x5158c0`, level `0x515c30`, item `0x515400`
/ `0x5154e0`, **kill `0x515640`**, dialog `0x516940`. Event 13 goes to the player who made the game, 14 to one who
joined it.

**Quest timers** (`0x518680` makes one, the quest update `0x516e40` runs them every 20 frames; medium): they count
**quest ticks** (one per update), not frames. A new timer goes to the **head** of the list, due at `tick + n`;
starting one that already runs adds a second. Each update first increments the tick, then walks the list newest first
and runs every timer whose due tick is below the new tick, so `n` = 0 fires at the next update and `n` runs `n + 1`
updates later. The callback returns 1 to drop the timer, else it stays and re-arms `n` ticks after the tick that ran
it. D2R asserts when a timer is made during the walk; there is no cancel other than the callback. At tick
`0xffffffff` every due tick is inverted.

Rules that matter to a mod:

- **Kills reach only linked quests.** `0x515640` returns at once when the victim has no quest link; credit goes to
  the killer, or to the killer's owner if that is a player. A few superuniques and monster classes go to every
  quest. A test that kills a boss twice on one character fails the second time because the quest is already
  granted ([saves.md](saves.md)).
- Credit goes by personal bits, location and party membership, never "the whole party completes". A dialog never pays
  twice.
- Greetings follow a fixed per-quest priority list (pending -> hand-in chain first), not "highest state wins".
  Dialog chain entries are `{u16 npc, u16 string, u32 menu}`.
- Helpers worth calling: set the game state `0x517ab0`, cycle the shown status `0x517b40`, send the player's quest
  flags `0x518620`, log update `0x5178b0`, refresh NPC speech icons `0x518d00`, delete a quest item `0x518720`,
  find a quest item `0x441980`, act access `0x486490(client, 1, difficulty)`.
- Act I callbacks (good hook points for quest reward changes): Den of Evil kill `0x5dde20`; Burial Grounds kill
  `0x5de890`; Search for Cain dialog `0x5e0830`; Forgotten Tower kill `0x5e10c0`; Andariel kill `0x5e1f00`, greeting
  `0x5e1d30`. Inits `0x5daef0` .. `0x5db6c0` fill the slots.
- The Den of Evil reward also sets quest 41 granted and pending (the respec). None of the Act I quests gives stat
  points.
- Add experience: `0x44f2e0` (used to award quest experience from a plugin).
- Quest props set themselves up from quest state in their objects.txt `InitFn` (Cairn stones, tree, gibbet, Malus,
  tower tome, Countess chest, Cain's start marker, the Lut Gholein Jerhyn and harem blocker): see
  [objects.md](objects.md) section 3. A quest's extra fields follow the common ones (`+0x11` not intro, `+0x13`
  shown status, `+0x14` state) at fixed per-quest offsets; when a quest's data is missing most props go to mode 2.

**Cain's rescue (quest 4, static only).** Operating the cage (`0x5da980`) schedules object event 7 at frame + 17
(`0x5d92e0`), which runs twice. First run: players in Tristram without granted/pending get started + pending, the
others a log update, the status cycles to 6, sound 15, and the event re-arms after the cage animation (unit +0x64
length, +0x68 speed). Second run: cage mode 3, walking Cain (monster 146) at cage + (2, 2) or a free spot within 3
and sound 55 on the rescuer; if he cannot be made the first player in Tristram gets a town portal at cage + 5 and the
town spawn becomes pending. The walking Cain's AI (`0x5c4c90` first think, `0x5c0db0` later) walks a few steps, opens
his portal (object 189, `0x5d9be0`) and vanishes (`0x5dbc80`, which also makes the in-town portal and the town Cain
when pending). Town Cain is monster 265, spawned by `0x5dcaa0` at a free spot within 2 of the start marker (object
385), up to 20 diagonal retries, flags `| 0x3000000`; entering level 1 (`0x5df990`) or starting the game with quest 4
granted (`0x5e0360`) also triggers it. No writer was found for the town Cain's walk target (quest +0xd0).

**Andariel's poison spray** (srvdo 88, `0x578df0`, static only): the aim point is the start function's stored
target (or the skill target when 0); the 64-step direction is reduced to 8 facings; for each whole animation frame
4..12 in the window since the last update, the running point advances by the facing's step (frame 8 has none) and one
missile is fired there. Step tables: origin `0x1d3e688`, per-frame `0x1d3e63c`, offsets X `0x1d09360` / Y `0x1d09380`.

## 7. Death, corpse, party

- Death mode enter `0x429d10`; Dead mode enter (after the death animation) `0x42a5e0`; another death handler
  `0x42de40`. Clearing the hardcore bits before these handlers turns a hardcore death into a softcore one (done in a
  working plugin).
- **Gold penalty** `0x425cb0`: `min(level, 20)%` of carried + stash gold. Killed by a monster: the rest of the carried
  gold drops, carried becomes 0, the stash pays a shortfall (a stash result outside its range resets it to 0 in
  D2R). Killed by a player: the penalty drops for the killer. In a single-player game the character keeps
  `500 x level` and loses at most the carried gold. Stat 175 records the loss.
  - On the wire (**high**, captured): the pile goes out as 0x9c action 2 (drop to ground) before the 0x5a, the mode-8
    0x0d and stat 175, then the same record again as 0x9c action 0 with the gold field's last bit set. That bit is
    the pile's owner: 1.14d GOLD_DropGoldFromInventory stores the dropping player as the owner (unit flag 0x400),
    and the gold writer `0x37fc80` ([item-format.md](item-format.md)) sets the bit for flag 0x400 with owner type
    0. Without a free cell there is no pile: a player's kill keeps the amount carried, a monster's kill loses it.
    1.14d's dropper (0x535510) refuses an amount above the carry limit, so a player kill whose penalty is above it
    leaves 0 carried and no pile (**static only**).
- **Experience penalty** (above level 1, not when killed by a player or a player's minion): difficultylevels
  `DeathExpPenalty`% of the current level's span; it never goes below the previous level's threshold (D2R stops
  **at** that threshold; classic kept one point above). The subtraction is unsigned: a character whose experience is
  below its level's threshold (a save editor can make one) can wrap to a huge value. Keep level and experience in step
  when editing saves.
- **Corpse** `0x42c110`: a player-type unit with the owner's name, the equipped items moved onto it, and 75% of the
  lost experience stored for recovery. Picking it up (C->S 0x13, within 8, a clear path) gives the experience back
  once (`0x42b220` returns it between taking the items off the corpse and placing them, so a level-up can happen
  mid-move). Another player may loot it only in hardcore with loot permission. The step-by-step rules (belt, the 16
  limit, the one-corpse save) are in [monster-ai.md](monster-ai.md#10-player-corpses).
- **Corpse on the wire** (**high**, two captured deaths, identical): 0x8e {1, owner, corpse} to everyone, then one
  0x9d action 8 per body item in body order, then the corpse unit, then 0x47/0x48 (sent by the frame's unit update,
  not the creator), then the player's own 0x0d. Each death 0x9d 8 has item flag **0x20** set (1.14d
  `ITEMFLAG_REMOVED`, set on every body item the creator takes off) and describes the item at its slot: mode 1, body
  and x both the slot. A 0x9d 8 for an item taken to the cursor (C->S 0x1c) has no 0x20 and mode 4; the client tells
  them apart by the flag, and without it keeps the item on its cursor (every later ground click becomes a refused
  drop). The corpse unit's 15 messages, each with the corpse's guid:

  | # | Message | Fields |
  |-|-|-|
  | 1 | 0x59 | the owner's class and name (zero-filled to 61 bytes), at the **corpse's own** x and y (71 bytes) |
  | 2 | 0x75 | the owner's party (party of the corpse inventory's owner, `0xffff` none), flags 0, state 0, level 0 |
  | 3-7 | 0x20 | stats 0x43, 0x44, 0x0c, 0x00, 0x02, each 0 |
  | 8 | 0x74 | {assign, owner, corpse}; assign = the corpse's unit flag 0x1000: 1 for the death's own corpse, 0 for a loaded one |
  | 9-10 | 0xa8, 0xa8 | state 7 (size 11, empty list); state 0x69 alignment (size 14) |
  | 11 | 0x0d | action 9 at the corpse |
  | 12 | 0x76 | |
  | 13-15 | 0x0d, 0xa8, 0xa8 | 11, 9 and 10 again, only when the unit is placed (the death, the join) |

  A corpse coming back into view gets messages 1-12 with assign 0, also the one that session's death made. 1.14d
  sends the unit's own coordinates in 0x59 (SERVER_SendInitUnitPacket 0x571f90 -> 0x53e8f0); a corpse with assign 0
  sent at 0, 0 is neither drawn nor clickable.
- **Pickup on the wire**: 0x9d 6 per placed body item, each with item flag **0x01** (1.14d `ITEMFLAG_RELOAD`, set
  on every item taken from a corpse) and the slot in x, mode 1 (captured), then 0x8e {0}, the corpse's 0x0a, the
  refresh, then sound 0x2c 100 to the picker. The corpse step's own gold pile goes through `0x424d90` (D2R's
  GOLD_DropGoldFromInventory, which in 1.14d makes the player the owner); whether D2R owns the pile for every caller
  (also C->S 0x47's drop) is not read.
- **Where a corpse lives** (1.14d, **static only** in D2R): the corpse stays in the room where its owner died, in
  that act, for the whole game, through respawns, waypoints and act changes. When its room goes inactive the
  inactive-unit saver (SERVER_GAME_UNIT_method 0x5433f0 -> SUNITINACTIVE_SaveInactiveUnit 0x542e10) keeps a
  player-type unit in the corpse state: GUID and position go in the act's inactive list, the unit is unlinked, not
  freed; SUNITINACTIVE_RoomInit (0x542b40) puts it back on the same spot when the room returns. The 0x13 handler
  finds the corpse by GUID game-wide and compares only coordinates (walk-to above 8, refusal above 0x32), and
  nothing compares acts: since the acts' coordinates overlap, a crafted 0x13 could reach a corpse in another act.
  A custom game server should check the act.
- **Join placement**: `0x485350` -> `0x4245c0` puts each saved corpse by the town start of the act the player joins
  in, whatever act it was saved in (1.14d CLIENT_PlacePlayerInRoom 0x5394a0 -> PLAYER_PlaceCorpse 0x5352c0); the
  exact spot comes from `0x2ef8b0` (captured one subtile off the start). 0x8e {1} goes right after the joiner's 0x15.
- **Leave**: the corpses are freed with the player (D2R `0x424f60`; 1.14d PLAYER_Destroy 0x5349d0 queues each
  corpse's room delete after the player's). The room delete list is last in, first out (0x61a270), so in one room a
  watcher gets the corpse's 0x0a, then the player's, then the merc's; across rooms the watcher's view order decides
  (0x5380d0 -> 0x53a770). All of them come after the roster's leave messages.
- **States at death**: see [units-stats.md](units-stats.md#5-states-on-a-unit) (plrstaydeath and friends).
- **The merc dies with its owner** (any life, no experience); see [hirelings.md](hirelings.md#6-death-and-resurrection).
- **Hardcore death** forces a save of every player at once (`0x406e10`, which also restarts the periodic timer).
  1.14d makes every save on the game thread (SaveGameAllGameTypes): an open or realm game hands the bytes to the
  client or the realm; without a realm it writes the file with plain `fopen`/`fwrite`, no flush to disk, no backup.
- **Corpse cap**: with 16 corpses already (`0x3892d0`) no corpse is made; the player still enters Dead mode, the
  loss record stays and the items go to the ground (medium).
- **Respawn** C->S 0x3f (`0x4b6160`): only in the dead mode, refused in hardcore; full life/mana/stamina and a move to
  the act's town.
- **Party**: the party iterator is `0x506ed0` (`0x506f90` is the one kill experience uses); kill experience with a
  level-gap penalty is `0x44fef0`. D2R splits party experience in doubles with a 64-bit pool capped at `0x7fffffff`.
  Hostility and loot flags travel in the party/relation messages ([network-messages.md](network-messages.md)).
- **Party on the wire** (3.3 captures plus 1.14d):
  - **Leave order**: 0x5a type 3 goes **first**, to everyone including the leaver, then 0x5c to the others, the party
    leave, the pet removals, and last the 0x0a's as the frame's room deletes. The 3.3 leave capture gave the leaver its
    0x5a before the party's 0x75/0x8d, and 1.14d's disconnect (0x52caf0) sends it before the removal; only classic
    1.10's game-end path sends it after. The last member of the leaver's party sees `5a 5c 75 75 75 8d 0a`; a client
    in no party `5a 5c 0a` (plus `7a` and a second `0a` for a merc). **high**
  - **Leaving a party** (1.14d PARTY_RemoveUnitFromParty 0x5407a0, matches a 3.3 capture): the refresh (0x75 about
    each member to each, 0x5a5ff0), then **one** 0x8d to the leaver about itself with `0xffff`; on a disband the last
    member gets the same. Nobody gets a 0x8d about another player here; the others learn "no party" from the 0x75.
    The stayer saw: 0x5a type 7 code 9, 0x75 leaver, 0x75 self, 0x75 leaver, 0x8d self. **high**
  - **In view**: a player unit coming into view is 0x59 then 0x75 (1.14d 0x53e8f0 sends both): the 0x75 is the viewer's
    roster entry for him (party 0x554630, his current level, the viewer's relation flags 0x55b350 and party
    state 0x55b3f0, all 1.14d). Sending party `0xffff` there tells the viewer a partied player is in none. The attributes in
    the same block (0x20; 1.14d SendInitUpdateAttributes 0x5489f0) are velocity, attack rate, level, strength and
    dexterity as the unit's **base** stats (no item bonuses); velocity and attack rate were 100 in captures.
  - **Level change**: no party message; the member's new area reaches the others at the next 30-frame sync. 0x90
    depends only on distance, whatever the two levels.
  - Life percent, the sync cache and the D2R own-0x7f delta: [units-stats.md](units-stats.md#6-party-and-relation-records).

## 8. Other hook points (build 93847)

| RVA | What | Note |
|-|-|-|
| `0x406550` | game frame | per-frame plugin work; hooked in working plugins |
| `0x408ea0` | client join/act state machine | |
| `0x483b20` | add a joined client's character to the game (save parsed by `0x41df40`) | medium |
| `0x4f2fa0` / `0x1d2a790` | C->S dispatcher / handler table | |
| `0x48fe80` | unit by id | high |
| `0x34b440` | room of a unit | high |
| `0x4929e0` / `0x492cf0` | spawn a monster | high |
| `0x44db20` | kill a unit | high |
| `0x42d2c0` | player move | high |
| `0x4a36c0` / `0x2396e90` | monster AI getter / AI table (155 rows of 0x20, think at +0x10) | a new AI id past 155 needs a getter hook |
| `0x543e90` | monster player-count scaling record | |
| `0x5269c0` / `0x529090` | cube | see [engine-limits.md](engine-limits.md) |

## 9. Pitfalls

- **A refused request is silent.** If a plugin changes rules so the server refuses something the client thinks is
  legal (a wrong item location, a price that no longer matches the quote), the client simply sees nothing happen.
  Recompute prices with the same function the server uses.
- **Seed order is behaviour.** Vendor stock, gamble offers and the mercenary roster all share the NPC-control seed,
  and the TC executor uses the dropper's seed: an extra `rand` call in a hook changes every later roll in the game.
  `rand(n)` with `n < 1` does not step, `n == 1` does.
- **Six items per TC call.** Drops beyond six from one call are lost; a bigger drop needs several calls.
- **Edition matters.** A Classic game (edition 1) refuses expansion items in gambling, Act V travel and mercenary
  resurrection, and reads the `base` bank.
- **Per-game state dies with the game.** Quest game state, GUID sets, vendor stock and the roster are rebuilt per game;
  only the character's own bits are saved. Plugin state keyed per game should reset in the destroy path (`0x486be0`).
- **Do not block the loop.** All games share one thread at 25 frames/s.
