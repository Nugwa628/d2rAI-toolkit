# Quests and acts: the quest machinery and each act's quest packets

This file covers how the engine runs quests across the five acts: the per-quest state and flags, the shared quest
machinery (status cycler, display status, record syncs, sequence filters and the intro rule), what each quest sends
and when, Tyrael's trip to Harrogath and the classic end of game, and the quest message layouts. The character
record's bit table, the event list, quest timers, kill dispatch and the D2R 93847 helper RVAs are in
[engine-server.md](engine-server.md) section 6 and are not repeated here. Act change and travel are in
[map-generation.md](map-generation.md) section 7; quest objects and their `InitFn`s in [objects.md](objects.md)
section 3; message ids and sizes in [network-messages.md](network-messages.md); the save's quest blocks in
[saves.md](saves.md).

- Most of this was read in the classic **1.14d** Game.exe (the quest code is the same code D2R carries); those
  addresses are tagged **1.14d** and are **not** D2R RVAs. Addresses tagged **93847** are D2R RVAs.
- Confidence labels as in [engine-server.md](engine-server.md): **high** = named by strings/asserts or checked in
  game; **medium** = inferred from the call graph or several reads; **static only** = read in Ghidra, never observed
  running; **unverified** = from D2MOO or a classic rule assumed to hold.

## 1. Flag letters, record words and quest ids

Flag letters for the 16 bits of a quest word (bit meanings in [engine-server.md](engine-server.md) section 6):

| Letter | Bit | Letter | Bit |
|-|-|-|-|
| G granted | 0 | LU log update | 12 |
| P reward pending | 1 | D primary goal done | 13 |
| S started | 2 | N done in this game while not eligible | 14 |
| LT left town | 3 | B completed in an earlier game | 15 |
| EA entered the area | 4 | | |

"Global" flags are the game's own quest record (the one sent as `0x29`), not a player's.

**Record words vs quest ids** (medium). The record is indexed by word; the quest objects, `0x5d` and `0x50` use the
quest-data id. The act-done words (7, 15, 23, 28) have no quest object, so the id is the word minus the act index:

| Act | Record words | Quest ids in `0x5d` | Act-done word |
|-|-|-|-|
| I | 0 intro, 1 Den of Evil .. 6 Andariel | 0..6 (same as the word) | 7 |
| II | 8 intro, 9 Radament, 10 Horadric Staff, 11 Tainted Sun, 12 the sanctuary (Horazon), 13 Summoner, 14 Seven Tombs | 8..13 | 15 |
| III | 16 intro, 17 Lam Esen, 18 Khalim's Will, 19 Gidbinn, 20 Golden Bird, 21 Blackened Temple, 22 Guardian | 15..20 | 23 |
| IV | 24 intro, 25 Fallen Angel, 26 Terror's End, 27 Hell's Forge | 22..24 | 28 |
| V | 35 Siege, 36 Rescue on Mount Arreat, 37 Prison of Ice, 38 Betrayal, 39 Rite of Passage, 40 Eve of Destruction (no intro; 34 unused) | 31..36 | - |

- **Act access** (high): Act N + 1 needs G on the act-done word (7, 15, 23, 28; classic `A1COMPLETED` ..
  `A4COMPLETED`). Warriv's trip sets word 7 to `0x2001` (G and D; captured in the trip's `0x28`). Meshif sets 15,
  the Guardian 23, Harrogath's portal or Tyrael's trip 28.
- The Act IV quest log shows Hell's Forge before Terror's End.

## 2. Quest messages

| Message | Layout and rule | Confidence |
|-|-|-|
| S->C `0x5d` quest alert | `{u8 quest id, u8 flags, u8 status, count}`. Flags 0 = a status cycle (the status byte is the display status); 2 = reward alert (`QUESTS_SendLogUpdate` 1.14d `0x5458e0`, status 0); 0x10 = Tainted Sun darkness, and Anya's reward with the item's drop sound in `count`; 0x20 = Den "few left" with the count, and thawed Anya taken away; 1 = Malah's potion, and the classic end-of-game warp. Status 12 = "completed now" (`QUESTS_SendLogUpdateEx` 1.14d `0x545920`, flags 0, count 0) | static only |
| S->C `0x28` type 6 | the player's own record (`QUESTS_UpdatePlayerFlags` 1.14d `0x5455b0`, 93847 sync `0x518620`). Its GUID field is **0**, not the player's guid | static only, guid 0 captured on Warriv's leg |
| S->C `0x29` | the game's record (global flags) | high |
| S->C `0x5e` | one byte per quest object: its not-intro byte | static only |
| S->C `0x52` quest status | one byte per quest: 0 when the quest's last status is 0, else the player's display status. **Only sent on the client's quest-log request** (1.14d C->S `0x40`, handler `0x53d840` -> `QUESTS_StatusCallback` `0x546040`; the D2R client sends `0x3e` when the log opens). No quest script sends it | static only |
| S->C `0x50` | 15 bytes: id, u16 quest, then u16 counts. Den's remaining count at `+3`, Rescue's caged soldiers at `+7`. Sent by `0x546040` whenever the player's Den status is not 0 (not only while "few left" shows), and while Rescue's status is set | static only |
| S->C `0x89` | quest FX event. `QUESTS_TriggerFX` (1.14d `0x545760`, iterator `0x5456f0`) sends **each player its `0x28` first, then the `0x89`**, player by player | static only |
| S->C `0x27` + `0x29` | NPC menu rebuild: `QUESTS_NPCActivateSpeeches` (1.14d `0x545780`) always follows the `0x27` with the game record `0x29` | static only |
| S->C `0x8a` | NPC hail (the "!"); see section 3.6 | observed |
| S->C `0x61` | cinematic: 2 = Act II intro (first Warriv trip only), 5 = Act IV end | observed / static only |
| C->S `0x4c` | "completion seen": sets the record's LU, and only once the reward is granted | medium |

**Order of the quest log answer** (1.14d `0x546040`): `0x28`, then `0x50`, then `0x52`.

**Join block** (1.14d `QUEST_OnPlayerJoinGame` / sequence cycler `0x546270`): `0x5e`, `0x28` type 6 with the
player's own record, `0x29`, and `0x89` when needed. A join that sends empty records to a saved character makes the
client believe the player has no quest bits until the next record arrives (a likely cause of a missing Quest Log
button). The D2R client asserts `sgbInit` (`Quest\Quests.cpp:92`) soon after a join without these three messages
(observed).

## 3. The shared machinery (1.14d, static only)

### 3.1 Status cycle and display status

- **Cycle** (93847 `0x517b40`; 1.14d `0x544300` with per-quest notify functions, e.g. `0x590830`, `0x5920d0`,
  `0x594830`): sets the quest's last status, then sends `0x5d` to every eligible player in a room of a level of the
  quest's act (`QUESTS_StatusCyclerEx` 1.14d `0x544190` skips players in other acts). Eligible: (no G and no B) or D
  or N. The Forgotten Tower's notify skips only a player with G and neither D nor N (B plays no part). The status in
  the alert is the **display status**, not the raw one. `0x544300` runs the notify for every player whatever unit it
  was called with; the unit is only the callback's argument.
- **Silent set**: the same call with bExecute 0 changes only the last status and sends nothing. Most reward dialogs
  use it (the reward line sends no `0x5d`).
- **Display status** (`QUEST_GetQuestLogDisplayState` 1.14d `0x543f90`) uses the quest's nInitNo (4 for the Act I
  quests, 6 for Cain): below nInitNo, N gives 12, otherwise the last status (Cain also gives 12 at status 6 without
  D); from nInitNo on, D gives the last status, otherwise 12. A quest may install its own status filter; Tools of
  the Trade's (1.14d `0x591d30`) checks in order: P -> 10; a Malus held by the player or a party member -> 2 (0 with
  G); intro game -> 0; D -> 13, N -> 12; below state 5 the last status; otherwise with global D 12 at level 8+, 4
  below, and 0 without global D.

### 3.2 Records and syncs

- Log update (93847 `0x5178b0`; 1.14d `0x545920`): `0x5d {quest, 0, 12, 0}`. Its act test passes any level from act 0
  on, so a player in Act I gets an Act II-V "completed now" alert too.
- Quest scripts send the `0x5d` and, at some points, the player's `0x28`. Sending a `0x28` together with every
  `0x5d` is wrong: most cycles, Cain's lines and the reward lines send the alert alone or nothing.
- A Book of Skill read and the Golden Bird's potion (`ITEMMODE_UseStoredItemAtCoordinates` 1.14d `0x55e170`) send
  `0x5d {quest, 2, 0, 0}` and no record.

### 3.3 Leaving, town exits and inactive quests

- **Player left** (event 10): each quest drops the player's guid from its sets. Den: rewarded and contributors;
  Burial: rewarded; Tools: speakers; Cain: two speech sets; Tower: its three sets; Andariel: three speech sets, and
  rewarded when the player has G and P. 1.14d: Den `0x5901f0`, Burial `0x590c10`, Tools `0x591a60`, Cain
  `0x592cf0`, Tower `0x594bb0`, Andariel `0x5961c0`.
- **Leaving town** (1.14d `0x545310`, at the top of the Den, Burial and Cain level handlers) drops the player from
  the quest's rewarded-this-game set; Cain also from speech set `+0xe0`.
- **Inactive quest** (the quest object's active byte, `+0x10`, just before not-intro): kills and item events are
  not delivered. NPC, level, gossip and leave events still are.
- **Kill dispatch** (1.14d `QUESTS_ParseKill` `0x543a30` -> `QUESTS_StatusCycler` `0x5439a0`): a quest's kill
  callback runs only while the quest is active, or for the forced bosses (the Act V uniques, the Cow King,
  Nihlathak, Mephisto, Diablo, Baal). The Countess is not forced.
- **Level change** reaches the quests once per change (a waypoint, Warriv or a portal included).

### 3.4 Slots

The callback table has the 15 events (list in [engine-server.md](engine-server.md)) and, past them, filters:
**slot 19 is the sequence filter** (93847 Seven Tombs handler 19 `0x5a1d70`, inferred from the 1.14d layout
`Q14_InitData` `0x59d5b0`). 1.14d registers more Act I slots than a first read of D2R suggests: Andariel has slot 2;
Tools has 2, 3, 4, 9, 10, 14; Cain 2, 4, 9, 10, 14. Seven Tombs fills 0, 2, 3, 8, 10, 11, 13, 14 and three filters.

### 3.5 Start states, the intro rule and sequence filters

- **Start states**: `AllocQuestControl` (1.14d `0x545d80`) sets every quest's not-intro byte to 1 before its init.
  Den's init starts it at state 1; Burial, Tools, Cain, Tower and Andariel start at 0. A quest at state 0 offers
  nothing (Kashya's Burial entry needs state 1-3 or a reward; Akara's Cain entry returns at 0).
- **The intro rule**: only `QUEST_ResetQuestDataForReconnect` (1.14d `0x544410`) clears not-intro (and the active
  byte and the previous state). The join (`0x546270`) calls it for a quest only when the player founds the game,
  the character was loaded from a save, `gbQuestReconnectMode` is set (statically 1; its only writer is
  unreachable), and the founder's record for the difficulty has G or B on that quest. It also sets B in the game
  record. Then event 13 runs for every quest, then Den's sequence filter. So a game founded by a character who has
  done a quest is an "intro game" for it: the quest runs with no progress or credit (e.g. the Ancients can be
  fought again for nothing; the Countess's chest effect does not run).
- **Sequence filters** (Den `0x590620`, Burial `0x5910f0`, Cain `0x593d70`, Tools `0x591e40`, Tower `0x595240`,
  Andariel `0x5968e0`, all 1.14d): the chain is Den -> Burial -> Cain -> Tools -> Andariel, with the Tower leading
  into Tools. A quest at state 0 with intro still on opens at state 1 and the chain stops; a done quest (Cain counts
  as done at state 6), or one whose intro was cleared, hands on. Den and the Tower never open themselves (the Tower
  opens through its tome). Filters run at the founder's join, after Akara's Den reward, at Charsi's hand-in, at the
  Tower's end and at two Kashya/Akara sites (`0x590a6f`, `0x592488`). None runs on a kill. The other acts chain the
  same way (the Seven Tombs filter `0x59d450` sets state 1 at 0 with intro on, then calls the Tainted Sun's).
- **Consequence for joiners**: the chain follows the founder only; later players get event 14. A character with Den
  done joining a game whose founder has not done it is offered no Burial until Den finishes in that game.

### 3.6 Hails (`0x8a`)

- `QUESTS_ActiveCycler` sends `0x8a` on **every** NPC think whose scan reaches the player: no cooldown, an NPC that
  stays active hails every think (Warriv every 0.8 s in a D2R capture). The quest ends it.
- Act I's intro keeps Warriv active until word 0 has G: he offers his speech (string 0, the Paladin's 1) while G is
  clear, and the C->S `0x31` naming it sets G (1.14d `0x58fa90`), with no message.
- Act I active filters (medium):

| NPC | hails while |
|-|-|
| Akara | Den open (no G) or its reward pending; Cain open, or at state 4 with the scroll, or rescued (state 6, D, no G), or his reward pending; the player in Andariel's speech set |
| Kashya | Burial open (no G) or its reward pending; the player in Andariel's speech set |
| Charsi | Tools open (no G), or level 8+ holding the Malus (no G) |
| Town Cain | D on Cain and in neither of his speech sets; Andariel open (no G, no P) or the player in her speech set |
| Warriv | the intro; Andariel's reward pending |
| Flavie | neither the game (D) nor the player (D, G, P) has cleared the Den |
| everyone but Warriv and Gheed | the player is in the Tower's pending set |

- Act I's other intro (Akara's hail until her first speech, and the four first speeches 11/12, 24/25, 36/37,
  45/46) needs a saved "heard" flag. D2MOO puts the intro flags in the first of the save's two NPC arrays, the same
  array D2R's `0x91` welcome-back reads as unheard greetings ([map-generation.md](map-generation.md) section 7.3).
  Which reading is right is **unverified**; writing the flag on a guess may corrupt `0x91`.

## 4. Act I (1.14d, static only)

| Quest | Event | What the engine sends |
|-|-|-|
| Den | Akara 64 / close | state 2, the pass, `0x27` + `0x29`; close: flags 0, `0x5d` status 1 |
| Den | leave town at 2 / enter the Den | state 3, LT; `0x5d` status 2, EA |
| Den | kill, few left / last (`0x590260`) | `0x5d` flags 0x20 status 4 with the count; last: D and P for contributors and party, N for the rest, log updates, sound, TriggerFX 0. D2R also syncs the contributors (GUIDUpdate); 1.14d does not |
| Den | timer (`0x590230`) / Akara 76 reward | flags 0 then `0x5d` status 5; reward: silent status 13, `0x27` + `0x29` |
| Burial | Kashya 81 / close | state 2, menu; flags 0, `0x5d` 1, pass |
| Burial | enter level 17 / kill / timer (`0x590bf0`) | state 3, `0x5d` 2; D and P near the boss and in the party; flags 0 then `0x5d` status 3 |
| Burial | Kashya 92 reward | silent status 13, `0x28`, `0x27` + `0x29` |
| Tools | Charsi 146 / close | state 2, menu; close: the Tools pass (`0x591340`: skips G or P; state 2 sets S, 3-4 set LT), flags 0, `0x5d` 1. Close after the hand-in: `0x5d` 13 (10 for a player with P) |
| Tools | leave town at 2 (`0x591810`, one shot) | `0x5d` 1 unless shown, state 3, the pass |
| Tools | Malus pickup (`0x591960`) | first time: Custom1 + 1 and sound 0x24 to the picker; every time flags 0, `0x5d` 2 |
| Tools | Malus lost (event 9, `0x591a20`) / join (event 14, `0x5919d0`) | count - 1, at 0 after the rack gave its Malus `0x5d` 3; count + 1, first Malus: status 2, no alert |
| Tools | Charsi 163 hand-in (`0x591490`, `0x591430`) | the player: D, P, `0x28`, Malus removed; party members without G/P at level 8+: D and P, no sync, no area test; state 4 -> 5 (other states with a Malus brought in run Andariel's filter); `0x27` + `0x29`. A player with G gets nothing |
| Cain | Akara 97 / close / leave town | state 2; `0x5d` 1 and the pass; state 3 for a player without G/P |
| Cain | Akara 112 (scroll translated) | silent status 3 (her close sends it). D2R sends status 3 at once. D2R also forces a save of every player here (93847 `0x406e10`, medium) |
| Cain | Akara 118 reward | `0x5d {4, 2, 0, 0}`, the ring given inline. D2R deletes the scrolls, sends a log update and queues the ring |
| Cain | tree, stones, cage | `0x5d` to the operating player; the stone order is rolled at level load (D2R: at the first touch). Cage and Cain's escape: [engine-server.md](engine-server.md) |
| Cain | enter Lut Gholein before he is out (`0x596de0`, `0x596ca0`, `0x5928c0`) | cage and tree open; Cain due in town; the first caged Cain (monster 146) takes his death mode, unless players are talking to him: their talks end (`0x62` with him, then `0x5d {4, 1, 0, 0}`, `0x592880`) and he stays; state 7; `0x5d` 5 to eligible players in Act I (not the one entering); global D; everyone without G/P gets N and Cain's speech. The act change (`0x597310`) runs the same skip |
| Cain | kill (`0x593e70`) | sets the secret cow level bit |
| Tower | tome operate (`0x594e70`) | `0x7f`; below state 2: state 2; the tome counts as read only when the last status was 0 |
| Tower | tome text 127 (`0x594960`) | read and status 0: `0x5d` 1 to all eligible; read and status 3: `0x5d` 2, state 3; below 2: state 2; the Tower pass if anything changed |
| Tower | enter level 20 / 25 (`0x595010`) | 20 at state 0: state 2, status 3, else (state <= 3 after status 1) status 4; flags 0, `0x5d`, the pass. 25 at state <= 3 with status not 2: state 3, `0x5d` 2, pass |
| Tower | the pass (`0x594890`) | skips G or P; state 2 sets S; state 3 sets bit 3/4/5/6 for status 1/2/3/4 |
| Tower | leave town | state 2 without G: state 3; state 5: leaves the heard set, both sets empty -> quest inactive |
| Tower | Countess kill (`0x595710`) | G skipped; players in level 25 get D and G, P cleared, sound, pending set; others N; party step skips only G; no guid kept -> inactive |
| Tower | news 0x8c-0x91 (Charsi, Kashya, Cain, Warriv, Akara, Gheed only) | with D and the reward open: state 5 and the sequence; leaves pending, joins heard if under 12; no `0x27` |
| Tower | timer (`0x5954c0`) / game start (`0x595860`) | `0x5d` 13; start skips G or B, then tests EA (state 3, status 1), bit 6 (3, 4), bit 5 (2, 3), LT (3, 1), S (2, 1) in that order |
| Andariel | Cain 166 / close | state 2, syncs, menu; `0x5d` 1 |
| Andariel | Catacombs 1-3 / 4 (`0x596010`) | state 3, silent status 1; `0x5d` 2. The pass runs when the state moved or a status was set |
| Andariel | leave town | drops the rewarded set except into Catacombs 1-4 and on the walk into Lut Gholein at state 4 (state 5, returns) |
| Andariel | kill (`0x5965a0`) / timer (`0x596500`) | D, P, Act II, gems, speech sets, N, log updates, sound; timer: portal at count 10, `0x5d` 3 at 12. 1.14d puts the portal at a free cell near the player; D2R places it differently |
| Andariel | Cain 184, Akara 179, Kashya 181 / Warriv 183 | speech set changes, no `0x27`; Warriv: silent 13, state 5, global D, G |

**The Countess's death effect** (1.14d `0x5954f0`, `0x595a50`, retry `0x5449e0`): the kill stores the death spot on
every call but sets "Countess killed" only outside intro, so an intro game or a second kill makes nothing (the first
kill also clears the kill callback). Effect: a trap (monster 326, mode 12) at the death spot, else 5 subtiles
south-east of the chest, and missile 332 (towerchestspawner) at each listed chest (up to 8, InitFn 47) within 100
subtiles, owner the trap, skill 0, level 1, target x = the chest's guid. A chest made later tries at its init and
every 10 frames. Missile 332: srvdo 18 (`0x5af300`) plays sound 0x5c at frame 1; at frame Range - Param1 (150) opens
the chest and sets target y 1; then drops gold every max(4 * Param2, 1) frames ("gld", quality 2, area monster
level, free spot within Param3); srvhit 33 (`0x5abeb0`) turns the room's portal flag back on (cleared when the
missile is made).

Not read: Cain's callback 6 trigger, the Malus pickup sound's D2R id, Andariel's 20-frame `Q06_QuestUpdate`, the
Tristram re-entry sub-flags, `0x5956c0`.

## 5. Act II (1.14d, static only)

Only the cycles (`0x5d {8..13, 0, status, 0}`) and these send anything:

| Event | Sends |
|-|-|
| Radament credit (`0x598e20`, `0x598d40`) / kill completed now (`0x598dc0`) | `0x28` to each credited player / `0x5d {8, 0, 12, 0}` |
| Book of Skill read (`0x55e170`) | `0x5d {8, 2, 0, 0}` after the skill point, no record |
| Horadric Staff piece picked up (`0x599a30`) | `0x5d {9, 0, status, 0}` in Act II |
| Orifice, staff placed (`0x59de06`) / staff cubed (`0x59e5c0`) | TriggerFX after the credit / `0x28`, then Custom7 |
| Tainted Sun darkness (`0x59a100`) / amulet pending (`0x59a5d0`) / altar (`0x59aa66`) | `0x53` then `0x5d {10, 0x10, 0, 0}` / `0x5d {10, 1, 0, 0}` / TriggerFX before the credit, then `0x5d {10, 0, 12, 0}` |
| Summoner kill (`0x59c0f0`) | `0x5d {12, 0, 12, 0}` to the completed-now players, then TriggerFX |
| Duriel's death | TriggerFX |
| Tyrael's portal home (`0x59cbf0`) | `0x5d {13, 0, 12, 0}` to the completed now |
| Meshif's reward (`0x59ce11`) / first sail (`0x5467e0`) | `0x28` after the reward bit / `0x28` |

Nothing at: the orifice per player, Cain's lines, the sanctuary quest's progress bits, guard and reward lines, the
harem entry and the journal; the Summoner's reward line and Started pass; the Seven Tombs' Started pass and
Jerhyn's closing line.

- **Duriel's Lair entrance** (objects row 100, OperateFn 43, 1.14d `0x584d00`, operate table `0x732d18`): players
  only; ORs 5 into its portal flags and sends the player to level 73's tile 0 on a free spot (mask `0x1c09`); no
  quest state, packets or mode change. The lair gate (`0x59db20`) is on the stairs path. Row 377 (Guild Portal)
  shares OperateFn 43 and goes to the level stored in its unit data, in any act.
- **Radament's think** (`0x5f2b10`, ally scan `0x5f2a00`): heals hurt living allies and raises corpses among
  targetable same-alignment undead (mummies by lUndead; Radament by lUndead or hUndead; uniques only for Radament in
  Normal) within aip5 (+10 for Radament) as a squared distance. Quest wake `0x599420`.
- **Duriel's aura** (`0x5f67b0`, `SetSkill` `0x56deb0`): with no right skill, Skill4 (Holy Freeze) is **set** to
  level aip1 for the difficulty and becomes the right skill. AI details: [monster-ai.md](monster-ai.md).
- The true tomb comes from the map (Act II's layout); Tyrael's portal ends on Lut Gholein's tile 12.

## 6. Act III (1.14d, static only)

| Event | Sends |
|-|-|
| cycles (`0x544190`) | `0x5d {15..20, 0, status, count}` in Act III |
| Lam Esen payout (`0x5b74e0`, `0x5b7580`) | `0x28` to each paid player in Act III, then five stat points and `0x5d {15, 2, 0, 0}` |
| Golden Bird turn-in (`0x5ba591`) / potion drunk (`0x55e170`) | the potion, then `0x28` / `0x5d {18, 2, 0, 0}` after the life |
| Khalim's Will: Cain's close (`0x5b8020`, `0x5b7f90`) | `0x5d {16, 0, status, 0}`; the log byte reads 0 until this close sets the last status 1 |
| Khalim's part picked up (`0x5b81c0`) | `0x5d {16, 0, pick-up status, 0}` |
| Compelling Orb smashed (`0x5bbaee`) / Mephisto's death (`0x5bcab0`) | TriggerFX after the bits |
| Council's last death (`0x5bb710`) / Mephisto completed now (`0x5bc810`) | `0x5d {19 or 20, 0, 12, 0}`, no record |
| through the Hellgate (`0x546ac0`) | the act reward, `0x28`, then `0x61` |

Nothing at Ormus's and Asheara's lines, Cain's Khalim lines or the party's orb pass. Natalya (InitFn 52,
`0x5bce80`) keeps her guid in a quest slot and is spawned again when that guid no longer resolves.

## 7. Act IV

Cycles `0x5d {22..24, 0, status, 0}`; Izual's reward (`0x5b3b30`) two skill points then `0x5d {22, 2, 0, 0}`;
Diablo's death per player with the goal (`0x5b49c0`) `0x5d {23, 2, 0, 0}` and the sound; Diablo's death, the seals
and the Hellforge smash use TriggerFX (static only, 1.14d).

**Harrogath's portal** (object 566, `0x5b5880`): a player who killed Diablo (expansion) gets `0x5d {23, 2, 0, 0}`
and `0x61 {5}` only while not busy, then the act change, then `0x28`, then waypoint 30. The first time it marks
word 28 done.

**Tyrael's trip** (class 367's travel entry, not the portal; 1.14d UseMenu `0x579d60` then
`QUEST_ProcessActTransition` `0x5467e0`; static only):

- Expansion only, for a player with word 26 G. The act change to Harrogath's result is not checked.
- First time (no word 28 G; `0x5467e0` tests the expansion again): word 28 gets G and D; `0x28`; then
  `0x5d {23, 2, 0, 0}` and the Act IV end cinematic `0x61 {5}`, both only while the player's cutscene byte
  (player data `+0x4c`, D2MOO `bBusy`) is clear, which it then sets.
- Before the record, the act's NPCs are marked met (`0x544fa0`): Tyrael passes Act II's list (act 1, at
  `0x54686c`), Warriv Act I's, Meshif Act II's.
- Last, the waypoint to Harrogath (30) is enabled.
- D2R's travel handler takes C->S `0x37 {Tyrael, 109}` only from a player talking to Tyrael and only for 109.

**Classic end of game** (`ACT4Q2` end branch 1.14d `0x5b4be0`, per-player iterator `0x5b4a80` called at `0x5b4ccc`,
kill handler `0x5b52e0` with the Diablo branch at `0x5b5360`; static only):

- A Diablo kill with a killer, while no quest timer runs, starts a timer that counts wall-clock ms from the kill. The
  Diablo **spawn** timer is the same timer, so a kill while it runs starts no end of game.
- 75 s: the clients are saved. 90 s: each player once has its interaction closed (`0x5351c0`) and its cutscene byte
  set; a player with word 26 D is warped to the fortress (level 103, tile 0; `0x53aec0` = `LEVEL_WarpUnit`, which
  moves within the act or changes act) and gets `0x5d {23, 1, 0, 0}`; any other player gets `0x50` for quest 23
  (1.14d writes only the id and quest, the other 12 bytes are stack leftovers). 95 s: the game ends.
- The expansion starts the same timer but only saves the clients at 75 s.

## 8. Act V

| Event | Sends | Confidence |
|-|-|-|
| cycles | `0x5d {31..36, 0 or Rescue's flags, status, Rescue's count}` | static only |
| completed-now passes | `0x5d {quest, 0, 12, 0}` | static only |
| soldiers freed (`0x587d11`) | `0x5d {32, 2, 0, 0}` | static only |
| Malah's potion / scroll (`0x589580`) | `0x5d {33, 1, 0, 0}` / `0x5d {33, 2, 0, 0}`, the status, Anya's move, `0x28` last | static only |
| scroll read (`0x55e170`) | `0x5d {33, 2, 0, 0}` | static only |
| Anya's reward (call at `0x5899b0`) | `0x5d {33, 0x10, 0, drop sound}`: `count` is the item's items.txt dropsound as a sounds.txt row, then the flags | static only |
| frozen Anya's AI clear (`0x589070`) | `0x5d {33, 0x20, 0, 0}` | static only |
| thawed Anya taken away (`0x5890b0`) | each player talking to her: interact cleared, `0x62`, `0x5d {33, 32, 0, 0}`; if anyone was talking she stays, else she is removed; town Anya appears either way | unverified (D2MOO) |
| Ancients' reward (`0x58c5c0`) | `0x28`, then the experience | static only |
| Baal's death per player with the goal (`0x58de83`) | `0x5d {36, 2, 0, 0}` (others get the completed-now pass) | static only |
| thaw, Siege and Betrayal kills, the Ancients' fall, Baal's death | TriggerFX | static only |

Anya's reward drop sounds in D2R 3.3 sounds.txt rows: bow 211, staff 245, orb 234, head 222, metal shield 231, helm
223, sword 246 (by the class item drawn; unverified beyond these rows).

**Rite of Passage** (classic and 93847): only characters of level 20 / 40 / 60+ get S, LT or credit; a lower player
can fight but cannot pass the Gateway (G or P required). The fight resets (the Ancients deactivated with no kill,
`0x58c000`) when:

- the last living player on the summit dies, once the altar was used, no portal open, before the defeat
  (`OnPlayerDied` `0x58d560`);
- a town portal end opens on the summit before the defeat (`OnPortalOpened` `0x58cf00`): the portal is counted and
  the altar disarmed. `OnPortalClosed` (`0x58cf50`) decrements an int32 (a close after the defeat goes below 0); at
  0 with the altar armed the fight may start again. Using the altar with a portal open first closes every town
  portal on the summit.
- Leaving by the stairs does nothing. An intro game leaves the summit and Gateway open; the Ancients fight again for
  no credit. Reward experience 1.4M / 20M / 40M, capped at one level.

## 9. Pitfalls

| Doing this | Breaks this |
|-|-|
| Sending `0x52` after a quest event | Nothing in the engine does; the client gets `0x52` only when it asks |
| Sending `0x28` with each `0x5d` | Order and count differ from the engine's; the record is sent at specific points only |
| Putting the player's guid in the `0x28` type 6 sync | The engine sends guid 0 |
| Alerting the raw status | `0x5d` carries the display status (12 for a player not eligible) |
| A quest-script portal or warp that ignores the intro rule | intro games must run quests without credit |
| Sending empty quest records on a saved character's join | the client hides quest progress until the next record |
| Expecting the Countess effect on a second kill or in an intro game | the first kill clears the callback; intro sets nothing |
