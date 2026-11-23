# Player to player: trade, hostility, deaths and shared rewards (server side)

This file covers what happens between two players in one game: D2R's new trade, how hostility resolves for mercs,
summons and missiles, what a player's death tells the others, a hardcore corpse looted by another player, and how
gold, experience and drops are shared. The party screen messages, relation records and the party sync are in
[network-messages.md](network-messages.md#party-roster-and-chat) and
[units-stats.md](units-stats.md#6-party-and-relation-records); the corpse itself, the death penalty and the waypoint
and portal timers are in [engine-server.md](engine-server.md#7-death-corpse-party).

Every RVA is for **D2R 3.3 build 93847** (image timestamp `0x6ab3782c`) unless marked **1.14d** (classic Game.exe).
Confidence: **checked live on 93847** = seen on the wire of a D2R host or client; **static only** = read from the
93847 functions, never seen running; **from the 1.14d routine** = read in classic code, assumed to hold in D2R.

## 1. Trade

### 1.1 The model

D2R threw away the classic trade (C->S `0x4f` trade buttons, S->C `0x77` trade actions, a shared trade window built
from copies of both inventories) and made trade client-driven:

- Each client builds its own offer and sends the **whole offer** every time it changes (C->S `0x2b` TRADEOFFER).
- The server keeps an offer as a list of item guids plus gold. It **never moves an item** into a trade container.
  The partner sees the offered items as "put in container" item messages that point at the real items.
- Each client accepts or cancels with C->S `0x2c` TRADESETTING. An accept carries the client's echo of the partner's
  offer, the cell in its own inventory where each incoming item goes, and any moves of its own items to make room.
- When both sides have accepted the same revision, the server checks both echoes, moves the items and the gold in
  one pass with rollback, and resyncs both inventories.

Only the `0x2b` and `0x2c` handlers touch the trade state. There is no timeout, and nothing on the server ends a
trade when a player walks away, changes level, dies, turns hostile or leaves the game (1.9).

Confidence for this whole section: **static only**, except where a line says otherwise. Checked live on 93847: a
click on another player sends a 16-byte `0x2b` with no items, its Cancel sends a 13-byte `0x2c`, and a 6-byte
`2e 04 <trade id>` answer closes the client's "Asking other player to trade" prompt. No real host's trade answer has
been captured.

### 1.2 Messages

`0x2b` and `0x2c` are `{u8 id, u16 len @1, bitstream @3}`; `len` counts the bitstream only, so the message is
`3 + len` bytes. Bits are read LSB first in each byte (bit reader `0xa1b6c0`, init `0xa1b620`). After the last field
fewer than 8 bits may be left, all zero; anything else fails the decode, the handler returns 2 and nothing is sent.

**C->S `0x2b` TRADEOFFER** (handler `0x4c2320` -> `0x528330`, decoder `0x52ce60`). The dispatcher gate (sender alive,
not in state `0x36`) runs first.

| bits | field | notes |
|-|-|-|
| 32 | trade id | chosen by the client; an answer to a request echoes the asker's id |
| 32 | gold offered | |
| 32 | partner guid | the other player's unit guid |
| 6 | item count | 0..63 |
| 47 each | offer entry | 1.4 |

With no items that is 102 bits: 13 bytes of bitstream, 16 bytes in all, which matches the captured request. The
largest is about 383 bytes.

**C->S `0x2c` TRADESETTING** (handler `0x4c2310` -> `0x5288e0`, decoder `0x52d080`).

| bits | field | notes |
|-|-|-|
| 32 | trade id | |
| 32 | revision | must equal the sender's current step for an accept or unaccept to count |
| 1 | accept | 1 accept this revision, 0 withdraw the accept |
| 1 | refuse | the "refuse trade requests" setting |
| 1 | cancel | |
| 6 | accept-list count | 0..63 |
| 6 | move-list count | 0..63 |
| var each | accept-list entry, then move-list entry | 1.4 |

At least 79 bits (13 bytes in all). The captured Cancel, `{u32 trade id, u32 0, u16 4}`, is this layout with only
the cancel bit set; the older reading of it as a `u16` field 4 is the same bytes.

The three flags are handled in this order:

1. **cancel**: if the trade id is the sender's, or the sender has no trade (the id is then taken for the cancel),
   the trade is cancelled with reason 0. A cancel naming some other trade is ignored and handling goes on.
2. **refuse differs from the stored setting**: refuse 1 cancels any trade (reason 0), stores 1 and sends `2e 05 1`
   to the sender. Refuse 0 with a trade open changes nothing; without one it stores 0 and sends `2e 05 0`. While
   refuse is 1, a request to this player is turned down (the asker gets event 2).
3. **accept or unaccept**: needs a trade, the same id and revision = step, else it is dropped silently. The side's
   accepted step becomes the step (or 0), the accept and move lists are replaced, a partner who is gone cancels
   with reason 0, and when both sides now accept the same step the exchange runs (1.7); otherwise both get event 1.

**S->C `0x2e` TRADEEVENT** (client handler `0x12db30`). Events 2..5 are 6 bytes `{u8 0x2e, u8 event, u32 value}`.
Event 1 is **36 bytes**: its builder `0x52ae60` hands `0x24` bytes to the send path (`0x477310` -> `0x4817f0`). The
client's size table marks the id variable.

| event | size | value | meaning |
|-|-|-|-|
| 1 | 36 | below | update: a request, the partner's offer, both accept flags |
| 2 | 6 | trade id | cancelled (cancel reason 0); the client ignores an id that is not its trade's |
| 3 | 6 | trade id | done |
| 4 | 6 | trade id | aborted: the server failed the trade (cancel reason 1) |
| 5 | 6 | 0 or 1 | echo of the refuse setting; the client only logs it |

The cancel function sends event `(reason + 1) * 2`, the same event to both sides. Live, a 6-byte event 4 as the
answer to a request closed the asker's prompt (checked live on 93847, against a custom server).

Event 1, built for one receiver about its partner (offsets count the id byte as @0):

| off | type | field |
|-|-|-|
| 1 | u8 | 1 |
| 2 | u32 | trade id: the receiver's, or the partner's when the receiver has none yet |
| 6 | u32 | the receiver's step (0 without a trade); its client sends it back as the `0x2c` revision |
| 10 | u32 | the receiver's offered gold |
| 14 | u32 / u8 | the receiver's guid, unit type 0 at @18 |
| 19 | u32 | the receiver accepted: 1 when its accepted step is nonzero and equals the step |
| 23 | u32 | the partner's offered gold |
| 27 | u32 / u8 | the partner's guid, unit type 0 at @31 |
| 32 | u32 | the partner accepted, same rule |

A client with no trade logs "Had new trade request on id %u" and opens the request prompt; one whose trade panel
(panel `0x16`) is open for another trade logs "We were busy reject request".

### 1.3 Who gets what

| trigger | sender gets | partner gets |
|-|-|-|
| first `0x2b` (a request), partner idle and in reach | nothing | event 1 (step 0) and the item views |
| first `0x2b`, partner busy, refusing or out of reach | event 2 with its own id; its state is reset | nothing |
| a `0x2b` that answers a request, or any later `0x2b` | event 1 and item views | event 1 and item views |
| `0x2c` accept or unaccept, not both accepted | event 1 and item views | event 1 and item views |
| `0x2c`, both accepted, exchange done | event 3, then the resync | event 3, then the resync |
| `0x2c`, both accepted, a check or the exchange fails | event 4, then the resync | event 4, then the resync |
| `0x2c` cancel, or a `0x2b` to another player, or a failed range check | event 2, then the resync | event 2, then the resync |
| `0x2c` refuse toggle | event 5 | nothing (event 2 if a trade was cancelled) |

The partner declines a request with a `0x2c` cancel carrying the asker's trade id.

**Item views** (`0x52c170`, from the event 1 builder). The server diffs the partner's current offer against the offer
last shown to this receiver:

- an added item: `0x9d` action 4 (put in container) to the receiver only, the receiver's player as the owner, item
  flags = the entry's flags | 1, page byte 2, x/y from the entry. Page 2 is taken to be the client's trade view
  (unverified);
- a removed item: `0x9d` action 5 (remove from container) to the receiver only, item flag `0x20`.

Every item message goes through `0x535f60`, which sets the item's location fields for the send and restores them, so
the real item never moves. An item that no longer exists fails the view and cancels the trade.

**Resync** (`0x52c100`, after a done trade and after every cancel), for each trader: `0x92` CLEARINVENTORY
`{u8 unit type, u32 guid}` to every player whose client has the trader's room near (the trader included), then every
item the trader owns again with item flag 1 at its real place (grid `0x9d` 4, body `0x9d` 6, belt `0x9c` `0x0d`, the
cursor item), then a stat refresh (`0x4f4020`; which stats is not read). Order on success: event 3 to A, A reset,
event 3 to B, B reset, resync A, resync B.

### 1.4 Item entries

**Offer entry** (`0x2b`), 47 bits:

| bits | field | notes |
|-|-|-|
| 32 | item guid | |
| 2 | tier | 0 normal, 1 exceptional, 2 elite, 3 invalid; must name the item's real tier (`0x52a920`) |
| 5 | flags | b0 personalized (`0x01000000`), b1 broken (`0x100`), b2 identified (`0x10`), b3 socketed (`0x800`), b4 runeword (`0x04000000`) |
| 4 / 4 | x / y | the cell in the offerer's trade grid |

**Accept-list entry** (`0x2c`): the same 47 bits, the acceptor's echo of one partner item. When the socketed bit is
set, 3 more bits follow (a count) and, if the count is not 0, 32 more; both are read and thrown away. Here x/y is the
**destination cell in the acceptor's own inventory** (page 0), not the partner's grid cell.

**Move-list entry** (`0x2c`), 42 bits: u32 guid of one of the acceptor's own items, 2 bits kind (0 grid, 1 belt,
2 body, 3 fails the decode), then 8 bits: grid x(4) y(4) on page 0, a belt slot below 16, or a body location 1..12.
The moves take effect at the exchange so that the incoming items fit.

### 1.5 Request rules and the range check

`0x528330` in order:

1. Decode; find the partner: a player unit by guid with a client, not the sender, not a corpse (state 7), not a
   stash-tab body (state `0xba`). Every listed item guid must exist, else cancel (reason 0).
2. A sender already in a trade with another id or partner, or failing the range check, has that trade cancelled and
   goes on idle.
3. **Answer**: the sender has no trade and the partner's state has this id with the sender as partner: the sender's
   state is set (step 1) and the update rule follows.
4. **Update**: the ids match both ways: step + 1 on both sides (any accept is now void, as the accepted step no
   longer equals the step), the gold and the offer are replaced and checked (1.6), then both get event 1 and views.
5. **Request**: the partner is idle (no trade, refuse 0) and the range check passes: the sender's state is set
   (step 1) and only the partner gets event 1 and views.
6. Anything else: `2e 02 <sender's id>` to the sender, and its state is reset.

**Range check** (`0x52ac80`): both units exist and differ, neither is dead (`0x34c2c0`), both are a client's own
player (`0x490ba0`), both targetable (`0x3500e0`), neither in state `0x36`, distance (`0x325140`) at most **50**, a
clear line with mask `0x804` (`0x350550`), **both in town** (`0x2f0750`), the same room or the partner's room near
the sender's, and **both cursors empty** (`0x388a70`). It runs only on `0x2b`. `0x2c` never repeats it, so an open
trade can be accepted from anywhere.

### 1.6 What may be offered

The server checks little (`0x52cd60`):

- the item exists and the offerer's inventory owns it (`0x38ac50` against `0x34a360`);
- its class equals the class the entry's tier gives;
- when the item is identified, `item flags & 0x15004b10` equals the entry's flags. The mask holds three bits the 5-bit
  wire field cannot carry (`0x200`, `0x4000`, `0x10000000`), so an identified item with any of them can never be
  offered; their meaning is open.

The page is **not** checked: as far as the server cares, worn items, belt items, the cube and the stash can all be
offered. The client limits what the player may pick. No quest-item, cube-content, mercenary-item or ethereal rule was
found on the D2R side.

### 1.7 The exchange (`0x52c5d0`)

Preconditions (`0x5288e0`): the same trade id on both sides, both accepted at the same step, each accept list holds
exactly the other side's offer (same count; per entry the same guid, class and `flags & 0x15004b10`, `0x52be30`),
and both offers pass the item checks again (`0x5261a0`). Any failure cancels with reason 1 (event 4 to both).

1. Per side a remove list (its moved items and its offered items) and a place list (its moved items at their new
   places, and the partner's items at the accept-list cells on page 0).
2. Gold: both offers at most `0x7fffffff`, both carried golds (stat 14) at least 0 and within the carry cap
   (`0x34b320`, character level x 10000); `offerA <= goldA` and `goldA - offerA + offerB <= capA`, the same for B.
   Any failure: no trade, nothing changes. Only carried gold (stat 14) is traded, never the stash.
3. Remove (`0x472590`, called twice per side; it reads as a dry run then the real removal, unverified), then place
   (`0x471500`), side A then side B. The placement is the fit check: a taken cell or an item that does not fit rolls
   the whole trade back.
4. The item requirement check (`0x36bc50`) runs on body slots whose item changed (`0x526800`).
5. Gold moves with `0x5281b0(unit, 14, delta)`, clamped to the cap.
6. On failure the removed items go back and the placements are undone (`0x5260e0`).

So the **client decides where each received item lands**; the server only checks that it fits.

### 1.8 Server state

One trade state per client, at `client + 0x6b8` (accessor `0x4851f0`); scratch for decoded lists at `client + 0x748`
(`0x4851e0`).

| off | field |
|-|-|
| +0x00 | trade id (0 none) |
| +0x04 | step: 1 when a trade starts, +1 on both sides at every offer change |
| +0x08 | partner guid (-1 none) |
| +0x0c | gold offered |
| +0x10 | the step this side accepted at (0 none) |
| +0x14 | refuse setting |
| +0x18 | offer (16-byte entries, count at +0x20) |
| +0x30 | partner offer last shown to this client (count at +0x38) |
| +0x48 | accept list (20-byte entries, count at +0x50) |
| +0x60 | move list (12-byte entries, count at +0x68) |

The reset (`0x484510`) clears the id, step, partner, accepted step **and the refuse setting** (one 8-byte store), so
a done or cancelled trade turns refuse off on the server while the client still shows it on. Whether the client sends
it again is open.

### 1.9 What does not end a trade

The only callers of the reset and the accessor are the `0x2b` and `0x2c` paths. No timeout, nothing on walking away,
a level change, a death, a hostility change or a player leaving. The client is expected to send the cancel. When the
partner has left, the next `0x2c` finds no partner and cancels. A stale partner guid can be reused by a new unit, so
a custom server should end the trade itself when a player leaves (event 2 to the other side and a resync).

### 1.10 Classic 1.14d rules kept and dropped

| 1.14d rule (from the 1.14d routine) | D2R |
|-|-|
| a player target within 50; within 8 the player asks, farther he walks first (1.14d `0x548b00`) | 50 and a clear line, checked on every `0x2b`; the walk is the client's (`0x02`/`0x04` to unit type 0, then the `0x2b`) |
| both in town, both alive | kept |
| a busy target: `0x5a` type 8 with the name | event 2 to the asker |
| 5 s cooldown after a decline or accept (`0x5a` type 9) | dropped |
| a completed classic game (quest 27) blocks trade | not found |
| a quest item the other player already has, a cube holding items (`0x5a` type 10), monster-flagged items, merc items | not found |
| fit check of the incoming items | kept, as a real placement at the client's cells with rollback |
| gold over the cap dropped on the ground | refused (event 4) |
| trade ends on leave (`0x77` `0x0c` to the partner) | dropped |
| `0x77` actions 0 ask, 1 asked, 5 accept, 6 open, 12 close, 13 done | replaced by `0x2e` events |
| hardcore restriction | none in either version |

1.14d had no `0x2b` handler: its dispatcher returns 3 for the id and the client is dropped. The classic trade code is
still in the D2R image (`0x5269c0`, reached through a pointer table at `0x238c610`, and `0x529090`, which is also the
cube recipe search), but nothing in the new `0x2b`/`0x2c` flow reaches the trade part.

### 1.11 Notes for plugins, proxies and custom servers

- Frame `0x2b` and `0x2c` by `3 + u16@1`. A splitter with no size for them closes or desyncs the connection the
  moment a player clicks another player (checked live on 93847).
- Event 1 is 36 bytes, the others 6. A server that only refuses can answer `0x2b` with `2e 04 <id>` and `0x2c` with
  `2e 02 <id>`, to the asker only (checked live on 93847).
- The server, not the client, must keep a trade honest: the echo compare, the tier and flag compare, and the fit
  check are the whole defence. The page is not limited.
- An offered item can change after the offer (a gem socketed into it, a repair, durability, a move). D2R does not
  notice: it compares guids, class and flags only. A server that wants the 1.14d behaviour (any change resets both
  accepts) has to watch the offered items itself.
- A crash between event 3 and the next save loses or copies items unless both traders are saved as one unit.

### 1.12 Open

Who picks the trade id and whether it must be unique; the meaning of item flags `0x200`, `0x4000`, `0x10000000` in
the mask; whether the client cancels by itself when it closes the panel, leaves range or changes level; which pages
the client lets a player offer; what the dispatcher does with handler return codes 1 and 2.

## 2. Hostility between players

The hostile flag, the 60 s declare delay and the party rules are in
[units-stats.md](units-stats.md#6-party-and-relation-records). What it does elsewhere:

- **Owners first** (static only). The hostility test (`0x48e060` at the damage sites, `0x48e460` -> `0x492790` for
  AI, skills and auras) first replaces a monster attacker by its owner (`0x4a53c0`), a missile by its owner unit,
  and walks a monster defender up its owner chain. So between two hostile players out of town, each one's merc,
  summons and pets are hostile to the other player and his minions, both ways; a merc outside town whose owner
  stands in town is not. Every other pair keeps the alignment rule.
- **Friends are a separate test**, not "not hostile" (1.14d SKILL_CanUnitTargetAlly `0x554de0`, from the 1.14d
  routine): each monster is replaced by its owner once; a unit is its own side; **two players are on one side only
  in the same party**; any other pair is one side when both are evil or both good, and a neutral monster is on none.
  Auras with the FINDALLY filter, the warcry hit (Battle Orders and the rest) and Enchant ask this test, so they
  reach the party and its minions and never a player outside the party, hostile or not. Holy Bolt's heal uses the
  skill's own flags instead: TargetPet = a monster the caster owns; TargetAlly = a partied player caster and a
  player, or a player's monster, of that party (1.14d `0x554d20`).
- **In hostility** (`0x424a60`, callback `0x425a80`): the player has the hostile flag about **any** other player. It
  is worked out at each use. The town portal (OperateFn 15, `0x58f680`) lets such a player take only his own portals.
- **The declare stamp**: only a declaration that passes the 60 s delay stamps the declarer (`0x507f30` writes
  playerData `+0x1e8`); the same step removes his town portal (`0x424c20`). Waypoints then refuse him for 10 s and
  portals for 5 s ([engine-server.md](engine-server.md#waypoints-town-portals-travel)).
- **Portal party**: the portal's "non-owner must be in the owner's party" rule reads the unit's party id
  (`0x48e480`), so it follows party joins and leaves at once. A player's own town portal is remembered at playerData
  `+0xa0` even after the pair is used up.

## 3. A player's death, as the others see it

### 3.1 The slain line: `0x5a` type 6

D2R's death handler (`0x429d10`) calls `0x4f2cc0` only when the death has a killer; it sends one 34-byte `0x5a` to
every client in the game (through `0x4040c0`). The client looks the names up itself and formats them with the
`sysmsg7-9` strings (keep their placeholders: [engine-limits.md](engine-limits.md)). Static only; a captured host
death has a `0x5a` at that point, the fields are not matched yet.

| off | type | field |
|-|-|-|
| 1 | u8 | 6 |
| 2 | u8 | color 4 |
| 3 | u32 | killer class: a player's character class, a monster's monstats row |
| 7 | u8 | killer unit type |
| 8 | u64 | victim guid |
| 16 | u64 | a player killer's guid; for a monster the kind: 0 plain, 1 superunique, 2 terror-zone herald |
| 24 | u64 | 0 |
| 32 | u16 | the superunique row (kind 1) or the herald tier (kind 2, its stat 367) |

A monster with a player owner (a merc, a summon) is named as its owner. A player who killed himself is named as the
player killer. A death with no killer sends no type 6. 1.14d (PLAYERMSG_BroadcastPlayerSlain `0x54d8a0`) sent the
names instead.

**The ear** (from the 1.14d routine: PLRMODES_Death `0x580ec0` -> `0x55a200`): just before the line, the victim's ear
drops when the killer is another player, or a monster owned by a player other than the victim. It is one normal item
of the items.txt `ear` row with the victim's name, class and level (also the item level), the hardcore item flag
`0x8000` in a hardcore game, dropped where an item drop lands (from x + 2, y + 3) with `0x9c` 2. The item record is in
[item-format.md](item-format.md).

### 3.2 A partied death

Checked live on 93847 (a host capture of a partied joiner's death matched message for message): at the first
30-frame party sync after the death, every member gets a `0x7f` about the dead player with **life 0**, and the dead
player gets his own `0x7f` at life 0 (the D2R own-`0x7f` delta in units-stats.md). The corpse unit's `0x75` carries
the party of the corpse's owner, so a partied player's corpse shows his party id, not `0xffff`.

### 3.3 A hardcore corpse looted by another player

Rules (`0x4b2be0`, static only): a hardcore game, and the owner's **loot** flag about the looter (C->S `0x4f` action 0,
which only a hardcore client can set). The `0x13` still names the corpse and its owner; a wrong owner, or a looter
without the flag, is refused silently. A successful loot moves the items onto the looter's body and inventory with
the owner's pickup rules ([monster-ai.md](monster-ai.md#10-player-corpses)); the corpse's stored experience goes
back only when the requester **is** the owner, so a looter gains none.

The messages follow the pickup's order: the item messages, then `0x8e {0, requester, corpse}` to every client, the
corpse's `0x0a`, the refresh (`0x47`/`0x48`), then sound `0x2c` 100 to the looter. The `0x8e` carries the
**requester's** guid, which is the owner's except at a hardcore loot (1.14d `0x57fb70` hands the requester to the
`0x8e` builder). The emptied corpse leaves the owner's corpse list, the world and the roster.

## 4. Shared gold, experience and drops

### 4.1 Gold piles

Which split runs (D2R `0x475c90`, the only caller of the gold share `0x5074b0`; 1.14d PickingGold `0x55c850`): the
pile's owner is looked up by id. **A player owner still in the game: the picker takes the pile alone.** No owner, a
non-player owner or an owner who left: the party split when the picker is in a party, else the picker alone. The
part above the carry limit drops again as a new pile with no owner. Static only.

Owned piles are the ones a player drops: the death penalty's pile and the C->S `0x47` gold drop (`0x4bc3d0` ->
`0x424d90`, which stores the dropping player as the owner, `0x34ef50`). Monster and chest gold has no owner. So gold
one player drops for another is never split among a party.

The split itself is in [units-stats.md](units-stats.md#6-party-and-relation-records). Its floor quirk can hand out
more gold than the pile held: with dead members paid but not counted, the picker's share goes negative and his gold
is floored at 0; with a picker at 0 gold, a 1,000 pile can pay out 1,500.

### 4.2 Worked numbers for 1, 2 and 8 players

Computed from the formulas in [combat-formulas.md](combat-formulas.md) and
[engine-server.md](engine-server.md#treasure-class-executor-0x4404f0---0x444920) (static only; the formulas
themselves are marked there). A level 5 zombie (evil, so it scales) worth 105 experience, killed by level 10 players
who are all partied and in range:

| players | life bonus | monster exp | party pool | each member gets |
|-|-|-|-|-|
| 1 | +0% | 105 | (alone) | 105 |
| 2 | +50% | 157 | 157 + 89 x 1 x 157 / 256 = 211 | 105 |
| 8 | +350% | 472 | 472 + 89 x 7 x 472 / 256 = 1620 | 202 |

A good monster (alignment 1) keeps its life and experience, and its stat 100 (spawn player count) is 1.

NoDrop weight a pick rolls against, `n = party + (players - party) / 2` capped by the dropper's stat 100, then
`total * r^n / (1 - r^n)` with `r = NoDrop / (NoDrop + total)`, truncated:

| players in game | in the killer's party near the kill | n | NoDrop 100 vs 60 | NoDrop 19 vs 1 |
|-|-|-|-|-|
| 1 | 1 | 1 | 100 | 19 |
| 2 | 1 | 1 | 100 | 19 |
| 2 | 2 | 2 | 38 | 9 |
| 8 | 1 | 4 | 10 | 4 |
| 8 | 2 or 3 | 5 | 6 | 3 |
| 8 | 8 | 8 | 1 | 1 |
| 8 | 8, dropper spawned with 3 players | 3 | 19 | 6 |
| 8 | 8, a chest (no cap) | 8 | 1 | 1 |

A dropper whose stat 100 is below 1 counts as 1. The game's player count (`0x4251e0`) is the living players; a
dedicated or hosted game has no `/players` setting to raise it, so it goes 1..8 as players join and down as they
leave or die.

## 5. Addresses (build 93847)

| RVA | What |
|-|-|
| `0x4c2320` -> `0x528330` / `0x52ce60` | C->S `0x2b` handler / offer rules / decoder |
| `0x4c2310` -> `0x5288e0` / `0x52d080` | C->S `0x2c` handler / setting rules / decoder |
| `0x52ae60` | S->C `0x2e` event 1 builder (36 bytes) and item views (`0x52c170`) |
| `0x52ac80` | range check (town, 50, line, cursors) |
| `0x52cd60` / `0x5261a0` / `0x52be30` | offer check / recheck / echo compare |
| `0x52a920` | tier -> item class |
| `0x52c5d0` / `0x52b280` | exchange / remove and place lists |
| `0x52b030` | cancel (game, unit, client, state, reason) |
| `0x52c100` | post-trade resync (`0x92`, item resend) |
| `0x484510` / `0x4851f0` | trade state reset / accessor (`client + 0x6b8`) |
| `0x4f2cc0` | `0x5a` type 6 slain line |
| `0x48e060` / `0x48e460` | hostility with owners resolved (damage / AI, skills, auras) |
| `0x424a60` | player is hostile with anyone (callback `0x425a80`) |
| `0x475c90` | gold pickup: owner test, then the party share `0x5074b0` |
| `0x424d90` | drop gold from a player's inventory (stores the owner) |
