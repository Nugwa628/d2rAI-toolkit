# Game messages between client and server (D2R 3.3 build 93847)

What a plugin author needs to know about the messages D2R's game client and game server exchange: how they are
framed, which ids matter, in what order they arrive, and where the server dispatches them. Observed in D2R 3.3
**build 93847** under D2RLoader 1.3.1 in TCP/IP mode (a hosted game plus one joining client), from static reading
of the image and message captures. All addresses are **RVAs of D2R.exe, build 93847** (image base `0x140000000`);
re-find them on any other build ([engine-re.md](engine-re.md)).

The same code runs for the host's own client in single player and hosted games. The host's own client is
connection 0 and never touches TCP: its messages go through a loopback queue, so a packet sniffer sees only the
joiners. To watch the host, tap the send slot and the handler table from a plugin (below).

Confidence labels as in the rest of the knowledge base: **observed** = seen in a capture and consistent with the
code; **static only** = read in the code, never captured; **unverified** = inferred.

## Framing and sizes

- **No outer framing.** In TCP/IP there is no compression, no encryption and no classic Huffman "bundle" header in
  either direction. Each direction is a plain concatenation of messages; a message's length comes from its id (fixed
  size) or from a length field inside it. (Observed: whole captures split with no leftover bytes.)
- **S->C sizes** come from the client's message table at `0x22bbc40` (build 93847): `0xaf` entries of 24 bytes,
  `{u64 handler, u32 size, u32 pad, u64 unitHandler}`, for ids `0x00`-`0xae`. Size `0xffffffff` = variable, 0 =
  unused. A nonzero `unitHandler` means a unit-level handler does the work. Ids `0xaf`-`0xb4` are system messages
  outside the table.
- **C->S sizes** are checked by the server handler itself (`len != N` at its top) or by a shared size function.
- A loader may exchange messages of its own outside D2R's id ranges. Leave ids D2R does not use alone.

Variable S->C length rules (observed unless marked):

| id | what | total length |
|-|-|-|
| `0x5b` | new client (player list entry) | `u16@1` |
| `0x67` | monster walk to a point | 22 + 4 x `u8@20` (path points) |
| `0x68`, `0x71` | monster walk / skill to a unit | 23 + 4 x `u8@21` (static only for `0x71`) |
| `0x72` | skill with path to a unit | 22 + 4 x `u8@20` (static only) |
| `0x73` | create missile | 21 + 67 x `u8@20` |
| `0x94` | all skills | 6 + 3 x `u8@1` |
| `0x9c`, `0x9d` | item messages | `u8@2` (max 252) |
| `0xa8` | state on with values | `u8@2` (1.14d had it at @6) |
| `0xaa` | all states | `u8@6` |
| `0xac` | new monster | `u8@12` |
| `0xb3` | save file chunk (system) | 3 + `u8@1` |

`u16@2` means a little-endian 16-bit field at byte offset 2, counting the id byte as offset 0.

Size facts worth knowing:

| Limit | Value |
|-|-|
| Item messages `0x9c`/`0x9d` | total <= 252 bytes (asserted); the item record buffer is 0xf4 bytes. A record that does not fit is sent **empty** (header only) |
| Walk/run/path-skill C->S | 210 bytes to a point, 214 to a unit: a 14-byte header plus up to 50 four-byte path points |
| Monster path messages | up to 15 path points |
| Save upload chunks (C->S `0x6c`, S->C `0xb3`) | `{u8 id, u8 len, u8 newFile, data[len]}`, at most 255 data bytes per chunk, at most 1 MiB per file buffer; a zero-length chunk ends the upload |
| System message buffer | 0x210 bytes per popped message |
| Chat S->C `0x26` | speaker <= 60 characters, text <= 255 |
| Transport timeout | a connection silent for 15 s is dropped; the client's 5 s heartbeat (`0x6d`) keeps it alive |

## Changes from classic (1.13c / 1.14d) that bite

- **Do not reuse a 1.14d C->S table.** About 45 C->S ids carry a different message than in 1.14d, mostly from `0x37`
  up, and 11 are new. Movement, skill and the early item ids (`0x01`-`0x2a`) mostly keep their meaning.
- **S->C:** of 181 ids, 84 keep their 1.14d size, 32 changed fixed size, 19 are variable, 15 are new and 4 were
  dropped. Many grew because fields widened:
  - rooms `0x07`/`0x08`: level id is u16 (7 bytes);
  - stat messages `0x1d`-`0x20`: u16 stat id plus a u16 layer;
  - states `0xa7`/`0xa9`: u16 state at @2, guid at @4;
  - skill and unit-change messages `0x4c`/`0x4d`/`0x99`/`0x9a` carry the caster's current x/y (+4 bytes);
  - monster enchant `0x57` is 31 bytes.
- **Interaction ids moved.** NPCs are talked to with C->S `0x2f`, objects (waypoints included) are operated with
  `0x41`, level exits with `0x40`. Classic `0x13` is only for player corpses now (static only).
- **Item action codes from `0x0d` up are one lower than classic** (classic action `0x0d` "swap in container" is
  gone). See the table below. Put-in-container is now a `0x9d` (with an owner), not a `0x9c`.
- **Item replies go out at once** from inside the C->S handler; classic queued them. Combat events are the other
  way round (queued on the unit, see below).
- **Failed requests get no reply.** A refused item move, stat point, skill point or swap changes nothing and sends
  nothing; the server only writes a telemetry line.
- **Life/mana/stamina** (`0x18`, `0x95`, `0x96`) were observed byte-aligned in captures, not in the classic
  bit-packed form (observed for `0x95`/`0x96`, unverified for `0x18`).

## Server -> client ids worth knowing

Not every id; the ones a plugin change commonly touches or that show up first when debugging.

| id | size | meaning |
|-|-|-|
| `0x00`, `0x02`, `0x04` | 1 | game init done, act init done, start act |
| `0x01` | 8 | new game: difficulty @1, **game edition @6** (1 Classic, 2 Expansion, 3 Garden = the Reign of the Warlock content) |
| `0x03` | 12 | new act: act, map seed, town level (u16), object seed |
| `0x06` | 1 | end game |
| `0x07` / `0x08` | 7 | add / remove room (x, y, u16 level) |
| `0x0a` | 6 | remove unit {type, guid} |
| `0x0b` | 6 | "you control this unit" |
| `0x0c`-`0x10` | 9-16 | unit mode/position changes (players: `0x0d` with mode and life) |
| `0x15` | 11 | warp unit to x/y |
| `0x19`-`0x1c` | 2-5 | gold / experience deltas and absolute experience |
| `0x1d` / `0x1e` / `0x1f` | 6 / 7 / 9 | own player stat (value < 255 / < 65535 / larger); field order differs per size |
| `0x20` | 13 | another player's stat |
| `0x21` | 12 | set skill level |
| `0x22` | 12 | skill item refs (tome/scroll charge counts) |
| `0x23` | 13 | set hand skill (left/right) |
| `0x26` | var | chat / overhead text |
| `0x27`, `0x28`, `0x29` | 40, 103, 97 | NPC dialog list, interaction/quest record, game quest record |
| `0x2a` | 15 | NPC transaction result {type, result, 0, guid or -1, gold}; result 0 = success |
| `0x2b` | 206 | correct path (only when client and server paths differ) |
| `0x2c` | 8 | play sound |
| `0x2d` | 5 | echo of the client loop counter `0x6f`; also sent before other messages when the counter moved |
| `0x42` | 6 | destroy hand (cursor) item |
| `0x47` / `0x48` | 11 / 11 | item modifiers / requirements-met refresh; follow every request that ran the inventory refresh |
| `0x4c`, `0x4d` | 20, 21 | skill on unit / at x/y |
| `0x51` | 15 | new object |
| `0x59` | 71 | new player unit (guid, class, name) |
| `0x5a` | 34 | system message (no names: the client looks them up by guid) |
| `0x63` / `0x64` | 17 / 4 | waypoint bit record / waypoint visit (static only) |
| `0x67`-`0x6d` | var / 10-16 | monster moves, attacks, death (`0x69` action 8, then 9), neutral |
| `0x73`, `0x85`, `0x86` | var, 10, 5 | missile create, hit, remove |
| `0x8f` | 33 | heartbeat reply (all zero) |
| `0x94` | var | all skills |
| `0x95` / `0x96` | 23 / 11 | life+mana / stamina sync |
| `0x97` | 1 | weapon set flipped |
| `0x9c` | var | item, world/cursor/belt side: `{id, action, len, component, u32 guid}`, item record from byte 8 |
| `0x9d` | var | item with an owner: as `0x9c` plus `{u8 ownerType, u32 ownerGuid}`, record from byte 13 |
| `0xa7`-`0xaa` | 8 / var / 8 / var | state on / on with values / off / all states |
| `0xab` | 7 | health update (queued when 0..128 life moves by more than 4) |
| `0xac` | var | new monster |
| `0xad` | 12 | attack result {attacker, defender, hit, damage x 255 / max life, flags}; queued on the **attacker** |
| `0xb3` | var | save file chunk (system path) |

The item record inside `0x9c`/`0x9d` is not the save record (code as four plain bytes, 10-bit version, no seed,
unidentified items hide their affixes, socketed children are sent as their own `0x9d`). Its bit layout is in
[item-format.md](item-format.md).

## Client -> server ids worth knowing

Server handler RVAs from the handler table `0x1d2a790` (build 93847).

| id | size | meaning | handler |
|-|-|-|-|
| `0x01`-`0x04` | 210 / 214 | walk / run to a point / to a unit | `0x4ac050`, `0x4ace20`, `0x4ace40`, `0x4ace60` |
| `0x05`-`0x12` | 5, 9 or path | left/right skill at x/y or on a unit (shift and hold variants) | `0x4ace80`-`0x4ad4f0` |
| `0x13` | 9 | loot a player corpse | `0x4b2be0` |
| `0x15` | var | chat: 3 + message + whisper target + 9 | check for an existing loader hook before patching |
| `0x16` | 17 | pick up from the ground | `0x4b9df0` |
| `0x17` | 5 | drop the cursor item | `0x4bd8f0` |
| `0x18` / `0x19` | 17 | cursor -> grid / grid -> cursor | `0x4bff30` / `0x4aa100` |
| `0x1a` / `0x1c` | 9 | equip / unequip | `0x4aa450` / `0x4aaba0` |
| `0x20` | var | cube transmute | `0x4c1590` |
| `0x26` | 36 | use item (potion, scroll, cube) | `0x4c13b0` |
| `0x28` | 21 | insert into socket | `0x4c0b20` |
| `0x2f` / `0x30` | 5 | start / end NPC dialog (NPC within 7) | `0x4c2410` / `0x4c2330` |
| `0x32` / `0x33` | 24 | buy / sell | `0x4c4650` / `0x4c5380` |
| `0x36` | 17 | hire mercenary | `0x4a9520` |
| `0x37` | 9 | NPC travel (caravan) | `0x4a9880` |
| `0x38` | 9 | open NPC menu: 1 trade, 2 gamble, 3 hire | `0x4b0470` |
| `0x3a` / `0x3b` | 5 | spend stat / skill point {u16 id, u16 count-1} | `0x4b3c70` / `0x4b3ee0` |
| `0x3c` | 9 | select hand skill (never gated) | `0x4b4250` |
| `0x3f` | 1 | respawn (only while dead; hardcore refuses) | `0x4b6160` |
| `0x40` | 5 | use a level exit (within 5) | `0x4adbb0` |
| `0x41` | 13 | operate object (waypoints, chests, shrines) | `0x4b0f80` |
| `0x4b` | 5 | travel to a waypoint {i32 level}, 10 s guard | `0x4b6f10` |
| `0x50` | 30 | weapon swap with the client's view (below) | `0x4bea50` |
| `0x53`-`0x5f` | 21-98 | direct grid / body / belt / stash moves (new in D2R) | `0x4c70b0` ... `0x4bb600` |

System messages (before and outside a game) go to `0x51a430` instead: `0x67` create game (401 bytes), `0x68` join
(85), `0x69` leave (1), `0x6c` save upload chunk, `0x6d` heartbeat (13; answered with `0x8f`), `0x6f` client loop
counter (5; echoed as `0x2d`), and `0x70`, which the network service posts itself when a connection is gone.

## Where the server handles them (hook points, build 93847)

| RVA | What |
|-|-|
| `0x406920` | message pump: pops system then game messages from the network service each loop |
| `0x51a0e0` | per-client game message: finds game, client and player, runs the dispatcher, defers on return 4. The host's own client does not pass through it in this build |
| `0x4f2fa0` | dispatcher `(game, player, packet, len)`: checks the id, applies the state gate, calls `handler[id]` |
| `0x1d2a790` | C->S handler table: `0x67` entries of 8-byte function pointers (no flag column; classic had `{fn, flag}`) |
| `0x4f30f0` | gate byte table for ids `0x01`-`0x42` (cases below) |
| `0x51a430` | system message handler |
| `0x519f10` | deferred-message replay, once per frame per client |
| `0x408ea0` | per-frame client state machine (joined -> loading -> loaded -> in act) |
| `0x483b20` | add player to game: parses the uploaded save and builds the player unit (the one place a client's character becomes a server player) |
| `0x1d46bd8` | network service vtable; slot `+0x28` = Send(connId, span) `0x641d20`, `+0x30` Disconnect, `+0x38` 15 s timeout sweep, `+0x50`/`+0x58` pop system / game message |
| `0x476d70`-`0x481ca0` | the S->C builder module: 113 small functions, one send per message. Generic sender `0x4817f0(client, buf, len)` |
| `0x479cd0` / `0x479ea0` | builders of `0x9c` / `0x9d` |
| `0x535f60` | item broadcast: sends an item change to every client that can see the owner |
| `0x481400` | builder of `0x97` (weapon set flipped) |

**State gate** (`0x4f30f0`, jump table `0x4f30e0`):

| case | runs when | ids |
|-|-|-|
| 0 | player not dead | `0x01`-`0x12`, `0x42` |
| 1 | always | `0x15`, `0x3c` |
| 2 | player mode is dead | `0x3f` |
| 3 | not dead **and** not in state `0x36` | every other id, and all ids above `0x42` |

A gated message is logged and dropped: not an error, no reply.

**Deferral.** A handler that returns 4 means "not now": the message is queued on the client and replayed next frame
(with a retry limit). A tap on the handler table sees a deferred message twice.

**Tapping without code patches.** Both the service's Send slot and the handler table entries are data pointers. A
plugin can swap them for recorders that call the original, after checking each pointer holds its expected value
(build check first, as for any hook). That captures the host's own client too. Do not hook the per-client function
for this: the host client's messages skip it.

## The join, in order

Observed on a TCP/IP join to a hosted game (times from the TCP connect):

1. **Connect.** The server speaks first: 2-byte hello `AF 00` as soon as it accepts.
2. **C->S** `0x68` join (85 bytes, no character name), `0x6d` heartbeat, then the client's own character as `0x6c`
   chunks: the `.d2s`, then the shared stash sections, then a zero-length chunk. The client does not wait between
   them.
3. **S->C** `0x01` new game, `0x00`, `0x02`, `0x8f` (heartbeat reply). Join accepted.
4. **Next server frame** after the upload ended: the load burst (about 190 messages):
   - `0x59` the player, `0xa8` states on the player, `0x94` all skills;
   - the shared stash tabs, each as its own player-like unit (`0x59`, stats, states, `0x83` stash link);
   - skill state `0x22`, `0x21`, `0x23`;
   - quest records `0x5e`, `0x28`, `0x29`;
   - `0x0b` (you control this unit), `0x5f` portal flags;
   - stats, then the player's items as `0x9d` (action 4 inventory, 6 equipped) and `0x9c` (action `0x0d` belt);
   - `0x03` new act, town rooms `0x07` interleaved with NPCs (`0xac`, `0xa8`, `0x6d`) and objects (`0x51`);
   - other players already in the game;
   - `0x15` warp to the start position, `0x7e`, `0x53` time of day.
5. **S->C** `0x04` start act (about 0.66 s after the connect), then `0x5b` per player, party (`0x8d`, `0x8b`,
   `0x8c`) and `0x5a` system messages.
6. **In game:** `0x6d` every ~5 s -> `0x8f`; `0x6f` -> `0x2d` echo; monster moves.

Periodic server traffic while idle (observed): `0x8a` NPC hail every 0.8 s while an NPC has something unheard,
`0x53` time of day every 2176 frames (87 s), and a push of the character files back to their client as `0xb3`
chunks every ~30 s (autosave) and on leave.

**Leave:** C->S `0x69`; S->C the save push, `0x5a` (player left), roster `0x75` and party updates, `0x0a` for the
player's stash-tab units, `0x23`, `0x06` end game; then the host closes the connection.

A plugin that builds per-player state should key it on the player unit after `0x04` (state 4); before that the
player may not exist yet. There is no engine "player joined" callback a plugin gets for free.

## Flows that help when reading a capture

**A monster kill** (observed): `0xad` attack result -> `0x95` vitals -> `0x1a` experience -> `0x9c` action 0 (the
drop) -> `0x69` action 8 (death) -> later action 9 -> `0x0a` when the corpse is freed. **The drop comes before the
death message.**

**Events queued on the unit.** `0xad`, `0xab`, `0xa5`, `0x99`/`0x9a`, `0xa3`, `0xa4` are queued on the unit and sent
with its next per-frame update, not at once. Per frame and per client that sees a monster: removal or `0x47`/`0x48`,
`0x15` after a teleport, the mode message, the queued events, states (`0xaa`), `0x0c`, `0x57`.

**Vitals.** `0x4f31a0` runs each frame, forced at least every 20 frames. It sends nothing for a life change under
10% with mana above half; otherwise potion change -> `0x18`, life/mana -> `0x95`, stamina -> `0x96`. Gold uses
`0x19` for a delta of 1..254, else absolute `0x1d`-`0x1f` on stat 14. Experience uses `0x1a` / `0x1b` / `0x1c` by
size.

**Level-up**, about one frame after the experience: `0x1d`-`0x1f` for the level stats and points, `0x95` refill,
the experience message, `0x2c` sound to the player, `0x75` to all, `0x20` to the others.

**Respawn** (`0x3f`): life/mana/stamina `0x1e`, the town rooms and hand skills, removal of the old level's rooms and
units, `0x15` warp, state `0x36` off (`0xa9`), `0x95`, then the town objects and NPCs.

## Item messages and action codes

Action byte at offset 1 of `0x9c` / `0x9d`. D2R numbering (observed for the codes the captures hit, static for the
rest):

| action | msg | meaning | classic number |
|-|-|-|-|
| 0 | `0x9c` | add to ground (drop appears) | 0 |
| 1 | `0x9c` | ground -> cursor | 1 |
| 2 | `0x9c` | drop to ground | 2 |
| 3 | `0x9c` | on ground (already there) | 3 |
| 4 | `0x9d` | put in container | 4 (was a `0x9c`) |
| 5 | `0x9d` | remove from container | 5 |
| 6 | `0x9d` | equip | 6 |
| 7 | `0x9d` | indirectly swap body item | 7 |
| 8 | `0x9d` | unequip | 8 |
| 9 | `0x9d` | swap body item | 9 |
| `0x0a` | `0x9c` | add quantity | `0x0a` |
| `0x0b` / `0x0c` | `0x9c` | add to / remove from shop | `0x0b` / `0x0c` |
| `0x0d` | `0x9c` | put in belt | `0x0e` |
| `0x0e` | `0x9c` | remove from belt | `0x0f` |
| `0x0f` | `0x9c` | swap in belt | `0x10` |
| `0x10` | `0x9d` | auto unequip | `0x11` |
| `0x11` | `0x9c` | to cursor | `0x12` |
| `0x12` | `0x9d` | item in socket (one per socketed child, owner type 4 = the parent) | `0x13` |
| `0x14` | `0x9d` | update stats / refresh | `0x15` |
| `0x16` | `0x9d` | weapon switch | `0x17` |
| `0x17`-`0x19` | `0x9d` | new in D2R (owners in state `0xd1`; static only) | - |

Classic code `0x0d` (swap in container) is gone, which is why everything from `0x0d` up moved down by one.

`ownerType` in `0x9d`: 0 player, 1 monster (mercenary), 4 item (socket parent). The `component` byte is the item's
display slot (`0x10` none, 5 right hand, 6 left hand; two one-handers on a Barbarian/Assassin are swapped).

**Request -> reply** (observed unless marked):

| C->S | reply in order |
|-|-|
| `0x19` grid -> cursor | `0x22` (if a tome/scroll count changed), `0x9d` 5, `0x9c` `0x11` |
| `0x18` cursor -> grid | `0x22`?, `0x9d` 4 |
| `0x1a` equip | `0x9d` 6, `0x47`, `0x48` |
| `0x1c` unequip | `0x9d` 8, `0x47`, `0x48`, `0x2c` sound |
| `0x17` drop | `0x42`, `0x9c` 2 |
| `0x26` belt potion | `0x9c` `0x0e`, `0x47`, `0x48`, later `0xa9` |
| `0x16` pick up | `0x0a {4, guid}` to watchers, then `0x9c` 1 / `0x9d` 4 / `0x9d` 6 / `0x9c` `0x0d` by destination; gold sends no item message, only `0x19` (static for the order) |
| `0x28` socket | `0x42`, `0x9d` `0x14` (parent), `0x9d` `0x12` per child |

After any `0x9c`/`0x9d` about a socketed item, each child follows as `0x9d` action `0x12`. Equip-type actions go to
everyone who sees the player, ground actions to the room's watchers, the rest to the owner only. Sounds (`0x2c`) are
queued and leave after the request's other messages.

## Weapon swap

- **Physical move.** D2R does not keep two "active set" pointers: a swap moves the items between body slots
  **4/5** (right/left arm) and **11/12** (swap right/left). The active weapons are **always in slots 4/5**. Code that
  reads the equipped weapon from slots 4/5 is correct for either set; code that reads 11/12 sees the inactive set.
  When placing items, slots 11/12 count as 4/5 for the item type's body locations.
- **The set flag** (0 or 1) is per character and saved in the `.d2s` header (u32 at 0x10, bit 0 = switched; see
  [saves.md](saves.md)). The server reads its own copy (`0x485200`) and flips it in `0x486740`.
- **C->S `0x50`, 30 bytes**, carries the client's view of the swap:

| offset | field |
|-|-|
| 1 | u32 guid the client thinks is in slot 5, `0xffffffff` = empty |
| 5 | u32 slot 4 |
| 9 | u32 slot 12 |
| 13 | u32 slot 11 |
| 17 / 21 | u32 right / left hand skill reference (probably the owner item guid; unverified) |
| 25 / 27 | u16 right / left hand skill |
| 29 | u8 current set (0/1), must equal the server's |

  The server rejects the swap on any mismatch (telemetry only, no reply), so a desynced client cannot swap. A hand
  skill mismatch runs a resync instead (static only). 1.14d's swap was a 1-byte message at another id.
- **Reply order** (observed twice): `0x9d` action `0x16` per moved item (left first), `0x97`, `0x23` left, `0x23`
  right, `0x47`, `0x48` (unit item refresh), `0x23` left, `0x23` right.
- A plugin that moves weapons itself (or changes what slots 4/5/11/12 hold) must keep the client's view in step, or
  the next `0x50` is silently refused and the W key "does nothing".

## What to expect when debugging a plugin

- **"Nothing happens" on an action** usually means the server refused it: item moves, stat/skill points and swaps
  are answered with silence, not an error. Check the gate (dead, or state `0x36`), distance rules (NPC 7, exit 5,
  pick-up 4) and, for `0x50`, the client's slot view.
- **Client crash right after an item appears** (dropped, gambled, cubed, bought): an item record that does not fit
  the 252-byte message goes out empty and the client asserts in its item handler. Usually a stat that cannot be
  serialized at its save widths; see [saves.md](saves.md) notes.
- **A hook on a C->S handler that never fires** for the host: check you hooked the handler table entry, not the
  per-client function, and that the gate does not drop the id in that state.
- **Duplicate events in a tap**: deferred messages (return 4) are replayed and seen again.
- **Drops before deaths**: on the wire `0x9c` (drop) precedes the death message; client-side code reacting to the
  death sees the item already there.
- **Combat numbers arrive late**: `0xad`, `0xab` and friends ride on the unit's next update, not on the hit.
- **Stats**: own-player stats go out as `0x1d`-`0x1f` (u16 stat id + u16 layer) each frame; another player's as
  `0x20`. A stat a plugin sets on the server reaches the client only if it is on the sent list (itemstatcost
  `Send Other` / `Send Bits` columns; [tables.md](tables.md)).
- **Do not imitate 1.13c/1.14d layouts** for anything above `0x36` C->S or for the item and stat messages.

Open questions (not captured): NPC dialog and shop messages (`0x2f`, `0x27`, `0x28`, `0x31`, `0x38`, `0x32`,
`0x33`, `0x2a`), waypoint menu and travel (`0x41`, `0x64`, `0x63`, `0x4b`), town portal (`0x60`, `0x82`: the builder
writes 80 bytes but the client table says 81), party and trade with two players.
