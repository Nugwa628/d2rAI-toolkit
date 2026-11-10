# Hirelings: hire, death, resurrect, items, save and messages (server side)

This file covers the mercenary as the game server half of D2R handles it outside combat: the hireling.txt row in
memory, the hire list and its quote, the hire and resurrect requests, what happens at the merc's death and at its
owner's death or leave, its items, how it is saved and loaded, and every message it sends. The stat and experience
formulas are in [combat-formulas.md](combat-formulas.md#10-monsters-and-hirelings); the AI (Hireable think, Select,
buffs) and C->S 0x51 are in [skills-missiles.md](skills-missiles.md#9-mercenaries-in-the-world); owner-less mercs and
the follow distances are in [monster-ai.md](monster-ai.md#7-hirelings-and-summons). Every RVA is for **D2R 3.3 build
93847** (image base `0x140000000`) unless marked **1.14d**, which means classic 1.14d Game.exe (D2MOO names), cited
where the D2R function was not read or to explain a captured order.

Confidence labels as in [engine-server.md](engine-server.md): **high** = matched against the game's own function
on generated cases (an oracle of 227,128 cases covered the row lookups, the list, the quote, the hire, stats by
level, experience and the price), or seen on the wire of a hosted game; **medium** = inferred from the call graph;
**static only** = read in Ghidra, never observed running; **unverified** = a classic rule assumed to hold.

## 1. The hireling.txt row in memory (stride 0x150)

| Offset | Field | Notes |
|-|-|-|
| +0x00 u16 | Version | 0 classic rows, 100 expansion rows; matched against "the game is not Classic" |
| +0x04 | Id | one per type and difficulty (Act I: 0/1 Normal, 2/3 Nightmare, 4/5 Hell) |
| +0x08 / +0x14 | Class / Seller | monstats rows (Act I: roguehire 271, Kashya 150) |
| +0x0c / +0x10 | Act / Difficulty | 1-based |
| +0x18 / +0x1c / +0x20 | Gold / Level / Exp/Lvl | |
| +0x24..+0x54 | HP, Def, Str, Dex, AR, Dmg-Min, Dmg-Max and their per-level columns | Str, Dex and Dmg per level in eighths |
| +0x58..+0x74 | fire, cold, lightning, poison resists and per level | per level in quarters |
| +0x78 | DefaultChance | the AI's Select starts its sum here |
| +0x7c | HiringMaxLevelDifference | the quote's level range **and** offset |
| +0x80 / +0x84 / +0x88 | resurrect multiplier / divisor / max | |
| +0x8c i8 | equivalentcharclass | a player class id 0..7; 8 = none (`0x3473d0`) |
| +0x90 / +0xa8 / +0xc0 | Skill1-6 ids / Chance / ChancePerLvl | |
| +0xd8 / +0xde / +0xe4 | Mode / Level / LvlPerLvl (i8 each) | |
| +0x14c / +0x14e | NameFirst / NameLast | string ids resolved from the row's keys at load (`mercenaries.json` in 93847) |

Lookups (**high**), each a first-match scan in file order:
- `0x3965d0(bank, notClassic, id, level)`: refuses ids >= 0x100; returns the last row of the id at or below the
  level, else the id's first row. It checks version and id only, **not the difficulty**.
- `0x396690(.., act, difficulty, prev)`: an iterator; the first call gives the first row of the act and difficulty,
  later calls the next row with the **same Level** as `prev`, so one row per type at its lowest level.
- `0x396800(bank, notClassic, npc, difficulty)`: the first row with that Seller. `0x396730(.., class)`: the first
  row with that Class.

The hire list offers every name id from NameFirst to NameLast, so a mod's names for one row must be one consecutive
run of string ids.

## 2. The hire list and the quote

**List** (`0x541400(game, npc class, npc record)`; record from `0x502b70`, list at +0x18, count at +0x20; **high**):
1. Nothing happens if the list exists (count != 0).
2. The name range comes from the seller's row at the game's difficulty.
3. The list is resized to a **fixed 0x45 entries**; entries past the name range stay zero. A row with more than 0x45
   names writes past the vector.
4. Per name, in order, the game's NPC seed (game +0x5650, `0x502c80`) steps once; its new low word is the entry's
   seed. Entry: +0 u16 name id, +4 seed, +8 hired, +0xc available.
5. Up to ten picks: `roll(n)` gives a start, then a forward search (wrapping) for an entry neither available nor
   hired marks it available; a full wrap ends the draw. With n names the seed moves `n + min(10, n)` steps, plus one
   roll when n < 10.

The list belongs to the game and the NPC and is shared by all players. The hire menu (`0x540640`) builds it lazily and
sends 0x4f (clear) then one 0x4e per available, unhired entry. After each hire (and Blood Raven's free Rogue,
`0x53d030`), `0x5415e0` counts the available, unhired entries; at zero it clears the count and draws a new list from
the current NPC seed. Nothing else refreshes it (not the player's level, not closing the menu).

**Quote** (`0x3c17b0(notClassic, player, seed, act index, difficulty, out[12])`; **high**): the panel and the hire
both call it. A local seed `{entry seed, 666}` keeps quoting from moving the NPC seed. `roll(types)` picks a type
from the 0x396690 rows, then `level = max(2, playerLevel - range + roll(range))` (range +0x7c). Every value comes
from that type's lowest-level row; the formulas are in [combat-formulas.md](combat-formulas.md#10-monsters-and-hirelings)
(life floor 40 here). The quote divides with a shift (floor) where the stat setup truncates toward zero, so the two
differ when `level < row.Level`.

## 3. The hire (C->S 0x36)

`0x4a9520` decodes 0x36 into `0x53d350(game, player, npc, @5 name id, @9 cost, @13 current merc)` (**high**):
1. The player is alive and not in state 0x36.
2. `@13` equals the current merc's GUID, or -1 with none. The lookup (`0x4ff1a0(game, player, 7, 1)`) includes a
   **dead** merc, so with a dead merc `@13` is its GUID.
3. The NPC row allows hiring and the quote gate passes.
4. `@5` is a **name id**, not an index: inside `[NameFirst, NameLast]`, `@5 - NameFirst` below the list count, the
   entry's +0 equal to it, available and not hired.
5. The quote succeeds, equals `@9`, and is not 0.
6. Gold (stat 14) and stash gold (15) are both >= 0, their sum does not overflow and covers the cost.
7. The merc spawns next to the NPC, else next to the player; both failing answers result 15 and takes no gold.
8. Payment `0x5416d0`: carried first, then stash; a pool that would go negative or above its cap is set to 0.
9. The entry is marked hired, the merc bound (`0x544ac0`), 0x4f + 0x4e sent again, and 0x2a result 5 with the merc's
   GUID. Then the exhausted check.
10. Refusals: result 9 (invalid), 12 (gold), 15 (no spawn), GUID -1. Every 0x2a carries the carried gold read just
    before the send (after payment on success).

**Bind** (`0x544ac0`): removes the old merc (0x9b `{0xffff, 0}`), writes the pet record (+8 seed, +0xc name id,
+0x10 hireling id), sets the quote's experience, runs the stat setup at the quote's level and queues the stat
refresh. The old merc is freed **with its items**: the new merc starts bare and the next save holds none of the old
items (1.14d MERC_HireAndReplaceMerc 0x573270; **static only**).

**On the wire** (hosted game, the owner's view; **high**): a first hire sends 0x81 `{pet type 7, class, owner, merc,
name id}`, 0x2a result 5, then a 0x7f about the merc; no 0x7a and no 0x9b. Another player's hire reaches a client as
0x81 only. A re-hire first removes the old merc: 0x7a (with pet type, class and owner), its 0x0a where seen, and the
0x9b clear, before the hire list and the 0x2a (**medium**: order from the code, not captured).

## 4. Stats, levels and the stat refresh

The stat setup `0x544f60` and the experience rules are in
[combat-formulas.md](combat-formulas.md#10-monsters-and-hirelings). Details it does not give (**high** unless noted):
- Stat 30 (nextexp) = `req(level + 1)` while level < 98, else 0. Experience is raised to `req(level)` with a
  **signed** compare. `req` is `0x3c1ac0`, 32-bit.
- The hireling.txt damage goes into stats 0xb9 / 0xba (185 / 186), not 21-24. A hosted level-2 Rogue showed 185/186
  = 1 / 3 with 21 to 24 at 0. 1.14d kept the row damage in 23/24.
- The skill setter `0x433910` **sets** a level; a skill that comes out at 0 keeps its previous level.
- Level-up (`0x44f480`): the stat setup for the new level (life refilled, experience raised to the new row's
  minimum), then the stat refresh `0x545530`, unit event 0x62 (sent as 0x2c event 0x62 on the merc to the owner) and
  event 0xc. 1.14d also sends hover text 0xd7c to the owner (**unverified** in D2R).
- **Refresh** `0x545530` (1.14d SendMercPackets 0x5726c0): stat sets in this order: level, str, dex, max life, life,
  defense, experience, nextexp, 0x17, 0x15, 0xb9, 0x18, 0x16, 0xba, resists 39 / 41 / 43 / 45, then each skill. 1.14d
  queues it on the unit: a client that gets the merc's unit gets it right after the 0xac, and the frame's end sends
  it to every client that has the unit (a client that got the unit that frame gets it twice).
- **Monster skills**: 1.14d's monster init gives every monster, a merc included, its monstats Skill1..8 with a level
  above 0, at `Sk*lvl + MonsterSkillBonus` of the difficulty; the hireling row's skills come on top. Without them a
  merc's AI has no attack skill (**static only**, 1.14d).
- **Missile damage**: a Rogue without a bow has stat 21 = 0, so its arrow does the missile's own 1 damage plus
  Dexterity as a percent; 1.14d's MonUMod 19 (given to every merc at hire) then replaces it with the Act I Rogue's row
  damage (MONSTER_ScaleMissileDamageByOwner), and adds the row to the other mercs' 21/22 (UMOD_AddBaseDamageBonus,
  which may count a one-handed weapon twice). In D2R the physical damage reads 185/186 instead (**medium**).
- Spawn stats: the merc also gets the monster spawn stats (`0x543e90`, the Normal columns), which give its velocity
  75, attack rate 100 and other anim rate 100; without them its walk falls to the 25% floor (**medium**).
- A merc's unit is good-aligned (stat 172 = 2, as for every pet), so its death is not a monster-region kill.

## 5. Items

C->S 0x51 is in [skills-missiles.md](skills-missiles.md#93-giving-items-c-s-0x51-handler-0x4c0e20). Additions:
- `0x4c0e20` tests the requirements (identified, class item, ethereal, strength, dexterity, level; merc event 0x5d)
  **before** the placement (0x5c). The item refresh tests the requirements alone. Strength and dexterity are the
  merc's totals with the items in use, less the item taken off.
- The take-out runs the main-weapon removal rule, then the remover clears a weapon guid (inventory +0x38) that still
  names the item (`0x4747f0` -> `0x389f00`, then `0x389820` -> `0x38ba30`). Classic only ran the remover.
- A merc's item messages go to the **owner alone**: 0x9d Equip (6) and Unequip (8) with owner type 1 and the merc's
  GUID. No other client sees a merc's items, and the unit's init (0xac) carries none: the owner's client makes the
  merc's inventory with the unit, so a 0x9d Equip per worn item must follow each 0xac of the merc that reaches the
  owner (join, coming into view, act change, resurrect). **medium** (1.14d reading, matches the 0x51 sends).
- An off-hand weapon's stat list is dynamic: its damage does not count. A two-handed weapon reads 23/24; a monster is
  no Barbarian, so a one-or-two-handed sword counts as two-handed. An item whose requirements the merc stops meeting
  comes off (no-equip) and returns once met.
- A dying merc drops nothing; the items stay worn and the save keeps them with the dead flag. 1.14d revives the same
  unit, so the owner's client keeps them.

## 6. Death and resurrection

- **Death** (`0x4ffb90`): the pet record gets flag 1 (dead), and the owner gets 0x9b `{name id, price}`. The merc stays
  in its owner's pet list, dead. Every player then gets a 0x7a pet removal holding **only the merc's id** (add 0, pet
  type, class and owner 0). That removal takes the portrait off the owner's screen; without it the portrait stays.
  For a merc killed in a fight 1.14d sends the 0x9b, then the 0x7a (SERVERUNIT_RemovePetFromUnit 0x5751a0, from
  SERVER_KillMonster 0x57ccb0; **static only**). Once the unit reaches its dead mode it is removed from the world
  (1.14d PETS_HandleDeadPet 0x5752b0); no corpse stays.
- **The owner's death kills the merc** at any life, with the owner as the attacker and no experience (1.14d
  PLRMODE_StartXY_Dead 0x57fca0 makes the body, then D2GAME_KillPlayerPets 0x575bc0 empties each pet list via
  0x574570; other pet types die and are dropped, the merc keeps its record). **high** on the wire: one batch after
  the body's 0x8e and its items: 0x7a `{0, merc id}`, 0x9b `{name id, price}`, the merc's 0x0a, its 0x69 action 8.
  No 0x7f and no 0x81. The respawn (0x3f) sends nothing about the pet; Kashya's Resurrect row comes from the 0x9b.
- **Price** `0x3c1ad0(merc, notClassic, id)`: from the row at the merc's **current** level; formula and defaults in
  [combat-formulas.md](combat-formulas.md#10-monsters-and-hirelings). A level-1 merc costs 0 and cannot be
  resurrected.
- **Resurrect** (C->S 0x52 `{npc, name id, price}`, `0x4a9600` -> `0x53eef0`; **high**):
  1. Not a Classic game; the NPC row allows it.
  2. No living merc, and a dead one.
  3. **@5 is the pet record's name id** (the one 0x9b sent), not a GUID.
  4. The cost is in 1..0x7fffffff and equals the price; gold and stash cover it; payment as in the hire.
  5. Success: dead flag 0x10000 cleared, life to max, the merc placed by the player with the **same GUID**, 0x9b
     `{0xffff, 0}`, 0x2a result 5 with the GUID. Refusals: 9 invalid, 12 short gold, GUID -1. Each 0x2a carries the
     carried gold.
  6. Captured order: 0x81 to every player, the merc's 0xac, 0x9b `{0xffff, 0}`, 0x2a 5. No 0x7a. 1.14d
     (SUNITNPC_ResurrectHireling 0x579aa0) sends a 0x7a add only for a pet with no seed and no name id.

## 7. The owner's leave and travel

- **Leave** (1.14d CLIENTS_RemoveClientFromGame 0x539da0; **high** for the 0x7a form on the wire): 0x5c, leave the
  party, then the pets are freed (0x5746d0 -> 0x574570, nothing kept). A merc with a unit sends every player the
  removal with only its id (0x574410 -> 0x53cb30): `7a 00 00 0000 00000000 <merc>`. Its room delete (0x61a270) is
  queued **before** its owner's; a room's delete list is last in, first out, and the client update (0x5380d0 ->
  0x53a770) walks each watcher's rooms in view order. So in one room the owner's 0x0a goes before the merc's; across
  rooms the watcher's view order decides. A dead merc (no unit) sends nothing. Another client in no party sees
  `5a 5c 7a 0a 0a`.
- **Travel**: pet type 7 (hireable) is a warp type. On a relocation 1.14d's PETS_ManagePetsByType (0x5754b0) brings
  every warp pet to its owner's point (PETS_TeleportToRoom 0x574cc0), skipping a dead merc. The owner's client
  normally keeps the merc and gets one 0x15 reassign (an 0xac if it did not have it); a client in the old level gets
  its 0x0a, one in the new level its 0xac. An act change moves the merc with its owner. (**static only**; the
  relative order of the pet move and the room changes is not settled.)
- The pet type list's order puts the merc (row 7) between the revive pets and the Decoy.

## 8. Save and load

The header's 16-byte record at save offset 0x9f ([saves.md](saves.md)) is written by `0x534340` from the living or
dead merc: flags 0x10000 when dead, seed = pet +8, name = pet +0xc **minus the row's NameFirst** (row at the merc's
level), type = hireling id, experience = stat 13. **high**

Load (`0x421240` for v72+, then `0x530d70`, which also reserves the merc's monster id at the join):
1. Skipped unless the type has a row.
2. Name = NameFirst + the stored index; past NameLast it becomes NameFirst.
3. Spawned with the dead flag passed, the pet record written, experience raised to the stored value (unsigned).
4. The level climbs from 1 while `req(L + 1)` by the row **at L** is <= the experience, up to 99.
5. The stat setup runs; a dead merc gets flag 0x10000 and mode 12.

A dead merc joins as its 0x9b (name and price) with no unit. The merc's items follow the `jf` marker.

## 9. Messages

| id | dir | Builder | Fields |
|-|-|-|-|
| 0x36 | C->S | `0x4a9520` | hire: npc, @5 name id, @9 cost, @13 current merc GUID (-1 none) |
| 0x52 | C->S | `0x4a9600` | resurrect: npc, @5 name id (not a GUID), @9 price |
| 0x4f / 0x4e | S->C | `0x477320` / `0x479bc0` | hire list clear; one entry `{u16 name id, u32 seed}` |
| 0x81 | S->C | `0x480df0` (from `0x4fff00`) | pet: @1 pet type 7, @2 class, @4 owner, @8 merc, @12 hireling id (pet +0x10), @16 seed (pet +8), @20 name id (pet +0xc) |
| 0x7a | S->C | `0x480ca0` | pet add/remove, pet type, class, owner, merc; death and leave removals carry only the merc |
| 0x9b | S->C | `0x478a50` | dead merc `{u16 name id, u32 price}`; `{0xffff, 0}` clears it |
| 0x7f | S->C | `0x480ad0` (from `0x5000f0`) | isPlayer 0, life percent, merc GUID, level id; pettype `partysend`; repeats suppressed (`0x4818f0`) |
| 0x9e / 0x9f / 0xa0 | S->C | `0x47be00` | merc stat set; the size picked by value < 0xff, < 0xffff |
| 0xa1 / 0xa2 / 0xa0 | S->C | `0x47bed0` | merc stat delta (to - from); the 0xa0 fallback carries **from** (classic quirk) |
| 0x2a | S->C | | hire and resurrect answer: result 5 ok, 9, 12, 15; the merc GUID or -1; carried gold |

The 0x7f life percent is whole points, `(life >> 8) * 100 / (max >> 8)` (`0x34a220`), sent as 0xffff from 0xffff up
(see [units-stats.md](units-stats.md#6-party-and-relation-records)).

## 10. Deltas from classic

- Weapon types come from monstats arm types, not a class switch; a monster may dual-wield when both arm types allow
  it (D2RCore `0x3761b0`); class items follow equivalentcharclass (classic: only Act V with Barbarian items).
- The hire names the entry by name id with a cost and current-merc check (classic 0x36 was `{npc, u16 id}`).
- Per-element resist columns and resurrect price columns in the row; HiringMaxLevelDifference 1 for expansion rows.
- The merc's damage lives in 185/186 (classic 23/24).
- The 0x51 take-out clears the weapon guid itself.

## 11. Not verified

- A merc killed by monsters while its owner lives (the 0x9b-then-0x7a order) and another client's view of a merc's
  death were not captured.
- Whether the hire's bind (`0x544ac0`) sends the old merc's removal in the full or the id-only form.
- The exact frame the dead merc's unit is deleted, and whether D2R also revives the same unit (1.14d does).
- Item-granted auras and skills on a merc (Insight's Meditation, stat 151 / 97) were not traced.
- The skill levels in the stat refresh, and the 1.14d one-handed damage double count in D2R.
