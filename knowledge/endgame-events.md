# Endgame events: Pandemonium (Uber Tristram) and the Uber Ancients

The two Hell-only cube events of D2R: the Pandemonium event (three keys, three mini levels with their organs, Uber
Tristram and the Hellfire Torch) and the Uber Ancients on the Colossal Summit (level 137). This page says what the
server does at each step, which parts are only table data a mod can change, and which are code a plugin would have to
replace.

- RVAs are for **build 93847 only** (image base `0x140000000`); re-find them on any other build
  ([engine-re.md](engine-re.md)). "1.14d" marks classic `Game.exe` addresses.
- Confidence: **static only** = read in the 93847 routine, not run in the real game. Almost everything here is static
  only: the rules were read in the dump and then reproduced and tested in our server reimplementation notes, not
  measured in a real D2R game. Where a value comes from 1.14d and was not found in D2R, it says so.
- Related: the classic Rite of Passage in [quests-acts.md](quests-acts.md) section 8; the classic Ancients' and
  statues' AI in [monster-ai-routines.md](monster-ai-routines.md); the scored target pick and Uber Diablo's choice in
  [monster-ai.md](monster-ai.md) section 11; the Evil Hole AI (row 76) in
  [monster-ai-routines.md](monster-ai-routines.md); object InitFn and OperateFn tables in [objects.md](objects.md);
  drops in [items-inventory.md](items-inventory.md).

## 1. The tables involved

| Table | Rows |
|-|-|
| levels.txt | 133 Matron's Den, 134 Forgotten Sands, 135 Furnace of Pain, 136 Uber Tristram, 137 Colossal Summit (all Act V) |
| monstats.txt | 704 Uber Mephisto, 705 Uber Diablo, 706 Uber Izual, 707 Lilith (`uberandariel`), 708 Uber Duriel, 709 Uber Baal; Uber minions 725-733; demonhole 711, pit lord 712; Uber Talic / Madawc / Korlic 745-747; their spirits 748-750 |
| misc.txt | keys `pk1` `pk2` `pk3`; organs `dhn` `bey` `mbr`; `cm2` (Hellfire Torch base); `std` (Standard of Heroes); `ua1`-`ua5` (the summit's materials) |
| objects.txt | 60 permanent (red) portal; 26 gibbet, 268 Wirt's body, 269 gold placeholder (Tristram); 397 sparkly chest; 402 desert waypoint; 583 summit altar; 584-586 summit statues |
| cubemain.txt | the three portal recipes (section 4) |
| treasureclassex.txt | the key, organ and Uber Ancient classes (sections 3, 6, 10) |

## 2. Game state

All of both events' state sits in the game structure. It is zeroed when the game is built and never saved, so it lives
exactly as long as the game. Static only.

| Field | Meaning |
|-|-|
| game `+0x1a5e0` / `+0x1a5e4` / `+0x1a5e8` | a Matron's Den / Forgotten Sands / Furnace of Pain portal was opened |
| game `+0x1a5ec` | the Uber Tristram portal was opened |
| game `+0x1a5f0` | the Forgotten Sands object counter (section 5) |
| game `+0x1a5f4` / `+0x1a5f8` / `+0x1a5fc` | Uber Baal / Uber Diablo / Uber Mephisto died |
| game `+0x1a600` .. `+0x1a690` | the Uber Ancients block (section 10.2) |
| game `+0x1aac8` | per-game hash map of Red Portal counts (accessor `0x5d88b0`) |
| game `+0x1a5d4` | Diablo Clone state (0 idle, 1 armed by the realm, 2 spawned); a separate event, not covered here |

1.14d keeps the three death flags but has no portal flags.

## 3. The keys: Hell only, by table data

- The Key of Terror (`pk1`) is an entry of the class `Countess Item (H)`, the Key of Hate (`pk2`) of `Summoner (H)`,
  the Key of Destruction (`pk3`) of `Nihlathak (H)`; prob 1 each. The Hell desecrated (Terror Zone) variants list them
  too. No Normal or Nightmare class does, and **that is the only gate**.
- No code checks a key: no `pk1`-`pk3` code compare, no once-per-game rule, no quest flag, no realm or single-player
  switch. The keys are `ques` items, not spawnable, but the TC executor (`0x4404f0`) makes any item an entry names;
  `spawnable` only filters random picks. Static only.
- The Countess's key comes through her picks `-2` class (`Countess (H)` -> `Countess Item (H)`), with the usual
  superunique TC choice (`0x447b80`).
- At one player the rates come out near 7.1 % (Countess), 8.6 % (Summoner) and 7.9 % (Nihlathak) per Hell kill
  (from the shipped classes; reproduced over 20000 kills in our notes, not in the real game).
- The classes `Pandemonium Key A/B/C` exist but nothing uses them: no monster, superunique or code names them.

**For a mod:** to move or add a key drop, edit treasure classes. Nothing else needs to change.

## 4. The cube portals

The cube output parser (`0x3e5e60`) turns four output names into a special-output number (recipe `+0x67`). The output
step `0x5269c0` calls the handler table at `0x1d3bfc0` with (game, cuber, output block at recipe `+0x5a`). A handler
that returns 0 refuses the transmute and the cube keeps every input.

| # | cubemain output | Handler (thunk -> body) | 1.14d |
|-|-|-|-|
| 1 | `Cow Portal` | `0x52a9d0` -> `0x5da2a0` | `0x594140` |
| 2 | `Pandemonium Portal` | `0x52a9f0` -> `0x5da590` | stub that returns 0 |
| 3 | `Pandemonium Finale Portal` | `0x52a9e0` -> `0x5da430` | stub that returns 0 |
| 4 | `Red Portal` (D2R only, takes `lvl=N` and `qty=N`) | `0x52aa00` -> `0x5da800` | none |

**Conflict with other pages.** [engine-limits.md](engine-limits.md), [debugging.md](debugging.md),
[porting-classic-mods.md](porting-classic-mods.md) and [new-game-systems.md](new-game-systems.md) say the cube's
"portal" output opens only the Cow Level. That is true of output 1. Per the 93847 parser and handler table read here,
output 4 (`Red Portal,lvl=N`) opens a red portal to level N, with the limits in 4.3. Neither reading was checked live.

### 4.1 Pandemonium Portal (`0x5da590`): the three mini levels

Vanilla recipe: `pk1` + `pk2` + `pk3`, op 28, version 100. In order:

1. Refused when all three mini levels were already opened in this game.
2. **One draw on the cuber's unit seed** (`0x34a1e0`), mod 3: 0 Matron's Den, 1 Forgotten Sands, 2 Furnace of Pain.
   If that level is already open it falls back with no second draw: Den -> Sands, else Furnace; Sands -> Furnace,
   else Den; Furnace -> Den, else Sands. The draw comes **before** the town and difficulty checks, so a refused
   transmute still spends a draw of the player's seed.
3. Refused unless the cuber stands in Harrogath (level 109) and the game is Hell (`game+0x104 == 2`).
4. A free spot from the cuber (`0x517f90`: size 3, mask `0x400`, 100 rings), as for the Cow Portal. None: refused.
5. The pair through `0x432ce0`: object 60, destination the chosen level. The town end's destination byte is set
   (`0x34e9d0`) and the level marked opened.
6. Any refusal queues sound event `0x15` on the cuber (`0x491960`).

So **each mini level opens at most once per game**; a fourth key set is refused and kept.

### 4.2 Pandemonium Finale Portal (`0x5da430`): Uber Tristram

Recipe: `dhn` + `bey` + `mbr`. The same rules as 4.1 (Harrogath, Hell, free spot, object 60, sound `0x15`), fixed
destination 136, once per game through `+0x1a5ec`. No draw. It does **not** check that the mini Ubers died or that
their portals were opened: the organs in the cube are the whole check.

### 4.3 Red Portal (`0x5da800`): the Colossal Summit

Vanilla recipe: `ua1`-`ua5`, op 28, version 100, min difficulty 2 (Hell), output `Red Portal,lvl=137,qty=1`. The
difficulty is the recipe table's gate, not the handler's: on Normal or Nightmare no row matches. In order:

1. The cuber must be in a town (levels 1, 40, 75, 103, 109; `0x327850`). Else refused with sound `0x15`.
2. With `qty` above 0, the game's count for this recipe (map at `game+0x1aac8`) must be below `qty`. At the limit the
   transmute is refused **silently** (no sound) and the inputs stay. `qty=0` sets no limit.
3. A free spot (size 3, mask `0x400`, 100 rings). None: sound `0x15`.
4. The pair through `0x432ce0` with object 60 and the line's level. Its last argument 1 skips the check that limits
   other cube portals from a town to level 39 or 133-137. `0x432ce0` refuses when the destination's act (levels.txt
   `Act`) differs from the cuber's, so level 137 opens only from Harrogath; from another town: sound `0x15`.
5. Made: the count goes up by one, the inputs are used up, cube sound 4 plays.

With the shipped row: one summit portal per Hell game. Nothing closes it. Where the destination has no arrival tile
(special tile 11), D2R's `0x432ce0` refuses but leaves the near end standing (1.14d deleted it).

**For a mod:** `Red Portal,lvl=N,qty=K` is a data-only way to add a portal to a level of the same act as the town,
with a per-game limit. The Pandemonium handlers have their levels, town and difficulty fixed in code.

## 5. The mini levels and their bosses

The bosses are not monster presets. The room preset pass (`0x48f190`) swaps one **object** preset for the boss and
spawns it through the normal preset spawn (`0x4926b0` -> `0x48dc50`: the spot, then 4 rings), in neutral mode (1),
after the room's monster pass. Static only.

| Level | Rule | Boss |
|-|-|-|
| 133 Matron's Den | the sparkly chest (object 397) is not made; the boss stands on its spot | 707 Lilith |
| 135 Furnace of Pain | the same, object 397 | 706 Uber Izual |
| 134 Forgotten Sands | every object preset except waypoint 402 counts up `game+0x1a5f0`; the one that brings it to `(map seed mod 3) + 3` is not made and the boss replaces it; the waypoint is never made | 708 Uber Duriel |

- The map seed is `game+0x12c`, so Uber Duriel takes the 3rd, 4th or 5th counted object, in room population order.
- **Uber Duriel stands at the room's origin**, not at the object's spot: the code reads the object's x and y but
  writes 0 for both into the spawn record, and the preset spawn adds the room's origin. Lilith and Uber Izual get the
  chest's spot.
- The populate pass has no difficulty test and no once-per-game flag; the levels are reached only through the Hell
  portals.
- No minions or packs: the monstats rows have no minion columns and the preset path places none. All three are
  level 110 on every difficulty and carry the `boss` flag.
- Mods given at creation (`0x494a60`, switched on the base class): Lilith 23, 6, 29; Uber Duriel 6, 18 (plus base
  Duriel's normal mods 11 and 22 from `0x4947d0`); Uber Izual 6, 18. All count as unique mods except 11. These are
  monumod row numbers. 1.14d's `0x5b1cf0` has the same rows.

## 6. The organs: plain death drops

Lilith, Uber Duriel and Uber Izual drop through the ordinary death drop (`0x4479a0` -> `0x447b80`). Their only class
is in the Hell column: `Uber Andariel` -> `dhn` (Diablo's Horn), `Uber Duriel` -> `bey` (Baal's Eye), `Uber Izual` ->
`mbr` (Mephisto's Brain). One pick, prob 1, NoDrop 0, so every Hell kill drops the organ and nothing else.

- No code forces or gates the organ. The special-death dispatcher (`0x4a1480`) has no case for 706-708; Lilith is
  not class 156, so Andariel's death effects never run for her.
- No quest is attached to 706-708, so no quest TC replaces their class.
- The `boss` flag stops the TC level upgrade.

## 7. Uber Tristram (level 136)

The same preset pass (`0x48f190`), level 136 branch. Static only.

- **Not made:** Wirt's body (268), the gold placeholder (269) and Cain's gibbet (26). The gibbet's x and y are kept;
  a preset of class 26 of any unit type counts, and with several the last one's spot wins.
- **No monster presets:** level 136 jumps past the room's monster-preset pass, so the four monster presets in the
  shipped map are never made. levels.txt gives 136 no monster list and no density, so no ordinary monsters either.
- **The three bosses**, when a gibbet was seen, in this order on the gibbet's spot: 704 Uber Mephisto, 709 Uber Baal,
  705 Uber Diablo, all in mode 1. Mephisto takes the spot; Baal and Diablo find it taken and use the 4-ring retry.
- **Mods** (`0x494a60`, all unique mods): Uber Mephisto 22, 30, 17, 8, 6; Uber Diablo 22, 8, 6; Uber Baal 22, 18, 8,
  6. Mod 30 is aura enchanted, which always gives Uber Mephisto Conviction ([monster-ai.md](monster-ai.md)).
- **No packs or starting minions:** no minion columns, no pack data, and AI rows 145-147 have no init. Every Uber
  minion comes from a think (section 8).
- **Normal Tristram is untouched.** Act I's Tristram code (Cain, the Tristram portal, the gibbet's quest init) is
  keyed to level 38 or to the gibbet object, which 136 never makes.
- 704-709 never take the prevent-heal state (1.14d `0x57d810`; D2R's twin near `0x44aa5f` also special-cases 333 and
  705, exactly what it exempts is not traced).

**For a mod:** moving the Ubers means moving the gibbet in the level's map file; adding monsters to 136 by preset does
nothing (the pass is skipped), only a levels.txt monster list would add any.

## 8. The Uber AIs

AI table rows (table `0x2396e90`):

| Class | monai row | Think (alt) | 1.14d |
|-|-|-|-|
| 707 Lilith | 34 Andariel | `0x5aba70` | Andariel's think |
| 708 Uber Duriel | 44 Duriel | `0x5adfc0` | Duriel's think |
| 706 Uber Izual | 144 UberIzual | `0x5b60c0` | real code (`0x5f8c80`) |
| 709 Uber Baal | 145 UberBaal | `0x5a8fe0` (alt `0x5a8f00`) | stub |
| 704 Uber Mephisto | 146 UberMephisto | `0x5b6540` | stub |
| 705 Uber Diablo | 147 UberDiablo | `0x5cfce0` (alt `0x5a8f00`) | stub |

The 1.14d single-player build has stubs for three of the Tristram AIs and no room population for the Uber levels.

- **Lilith and Uber Duriel** run Andariel's and Duriel's thinks unchanged; neither reads the class. Duriel's charge is
  tied to class 211 and Uber Duriel's aip5 is 0, so he never charges.
- **Uber Izual** is Izual's think (`0x5b0cb0`) without the wake (no quest call) and with two steps in front: if his
  Skill2 (Chilling Armor) aurastate is missing he casts it; else, if a sight or barrier cell (mask 6, `0x3502a0`) lies
  between him and the target, he casts Skill3 (MonTeleport) at the target's spot. Then Izual's usual moves.
- **Uber Mephisto** is Mephisto's think (`0x5b1ca0`) instruction for instruction on his own aips (copied from
  Mephisto), with a minion step first on every think (below). His Conviction comes from mod 30, not the think.
- **Uber Diablo** is Diablo's think with the evil-hole step and other weights; see [monster-ai.md](monster-ai.md)
  section 11 and the Evil Hole in [monster-ai-routines.md](monster-ai-routines.md). Each hole (711) makes three pit
  lords (712, aip1 3, one every aip2 25 frames), no experience, no treasure, state 184, then dies. His slot-7 skill
  Diablogeddon (349) is recast whenever its state is gone. Class 705 uses the cast rows of the mode chart for S3 and
  S4 (`0x448400`), as 243 and 333 do.
- **Uber Baal** reads no engine target. In order, each draw on his seed: the crab's target scan (`0x5a9a10`); the
  minion step; Chilling Armor (Skill8, level 30) if its state is gone, which ends the think; home command 7 and his own
  case roll (`0x5a9d20`, the crab's shape with other weights, "far" measured to himself); no target: nothing more, not
  even an idle; a sight or barrier cell to the target: Skill5 (Baal Teleport) at the target's spot; else the crab's
  action handler, whose teleport case sends 709 straight to the target's spot.

### 8.1 Shared helpers

| Helper | RVA | What it does |
|-|-|-|
| Nearby count | `0x596210` | over the centre's room and its near rooms only: counts 704, 705 and 709 each, 711 and 712, 731-733 together, and the rest |
| Minion spawn | `0x5ab940` | one draw mod 8 picks 725, 725, 726, 726, 727, 728, 729 or 730; spawned at the given unit (else the boss) in mode 8 (S1), 4 rings; gets no experience (unit flag bit 26) and state 184 (uberminion). Only Uber Mephisto calls it |
| Minion spot | `0x5ab740` | picks the player a minion or hole appears at: each player on the boss's level, alive and within 1020, takes one draw; `draw % 20 + weight - 1` keeps it, weights by class 35, 40, 30, 15, 0, 20, 10, 25 (Amazon .. Druid, then the eighth class). No weight is negative, so in practice the last such player is kept |

**The minion steps.** Each Uber counts around his target (himself without one) and, when that count saw neither of
the other two Tristram bosses, again around himself to look for them.

| Boss | Neither other boss near | One near |
|-|-|-|
| Uber Mephisto | one minion when the "others" count is under 30 | under 15 |
| Uber Baal (`0x5a9440`) | a roll under 45 with fewer than 15 of 731-733 spawns one; else (or roll 45+) falls through | fewer than 7 of 731-733 and a roll under 30 |
| Uber Diablo | a roll under 33 with fewer than 2 holes and 10 pit lords opens a hole | else no hole, fewer than 4 pit lords and a roll under 15 |

Uber Baal's minion is wraith9 (732, one draw & 1) or vampire9 (731), at the free spot nearest him (size 2), no
experience, state 184. Units outside the near rooms, even on the same level, are not counted: a minion army that
walks away lets the boss make more.

## 9. The Hellfire Torch and the Standards of Heroes

The special-death dispatcher `0x4a1480` sends 704, 705 and 709 to `0x5a8450` once the death check passes (1.14d
`0x5e0070`, reached through umod 22's mode-change slot, which all three carry). Static only.

1. The dying boss sets its own death flag (section 2).
2. When all three flags are set, **whichever boss died last** makes the drops, on top of its own empty death drop
   (704, 705 and 709 have no treasure class):
   - one **unique** on base `cm2` through the reward drop (`0x43fbf0`): the Hellfire Torch, the only unique on that
     base. The item is then recharged (`0x46f310`), so its charged skill starts full;
   - one `std` per **client** in the game (`game+0x140`), asked as normal quality. misc.txt's `unique` column on the
     base makes the generator turn it unique, so the Standard of Heroes comes out unique without a uniqueitems row.
3. The client count includes players still loading and players elsewhere in the game; it is read at the third death.
4. The Torch's class is its first property, `randclassskill` (property func 36), which rolls the class between the
   row's min 0 and max 7 on the item's seed and gives +3 to that class's skills. Read in 1.14d; func 36 was not found in
   the D2R dump (unverified there).
5. The item level: in the 1.14d reward drop it is the dying boss's level (110), not the area level (83 in Hell
   Uber Tristram), which matters because the Torch's `lvl` is 110. Not traced in D2R.

The flags stay with the game: a player who leaves and rejoins between the deaths does not reset them. The handler
tests "all three set" after setting the dying boss's flag and has no "already dropped" flag, so a further copy of
704, 705 or 709 dying later in the same game (one a mod or plugin spawns) would seem to drop another Torch and set of
Standards. That follows from the routine as read; unverified.

`0x476e20` recognises the Standard of Heroes, The Stone of Jordan and a prismatic item and sends S->C `0x4a` with ids
`0x1a`, `0x1b`, `0x17`. It looks like a notice or achievement hook, not part of the event (unverified).

No message is specific to Pandemonium: the portals are ordinary object 60 units, a refusal is sound event `0x15` in the
next unit update, and the drops are ordinary drops.

## 10. The Uber Ancients (Colossal Summit, level 137)

D2R only; 1.14d has none of it. D2R's asserts put the AI init, the special death and the shared state in
`D2Game/src/Ai/AiColossal.cpp` and the altar and statue callbacks in `Objects/ObjMode.cpp`. Static only.

### 10.1 What stands on the summit

| Unit | Row | Role |
|-|-|-|
| altar | object 583 | OperateFn 74 (`0x590b00`); selectable in mode 0 only; modes 0-2, mode-1 animation 30 frames |
| statues | objects 584, 585, 586 | InitFn 80 (`0x50f510`); block in modes 0 and 3, not 4; mode-3 animation 32 frames |
| Uber Talic | monstats 745, monai 149 ColossalWhirlwind | his spirit 748 colbarbwhirl (monai 152) |
| Uber Madawc | monstats 746, monai 150 ColossalThrow | his spirit 749 colbarbthrow (monai 153) |
| Uber Korlic | monstats 747, monai 151 ColossalLeap | his spirit 750 colbarbfrenzy (monai 154) |

- Statue 584 + i belongs to Ancient 745 + i. The three Ancient rows share the AI init `0x59aba0`.
- Each Ancient's Hell class is Uber Talic / Uber Madawc / Uber Korlic (one pick, NoDrop 0).
- Their monstats `spawnx` 0, `spawny` 2, `spawnmode` NU and `aip6` 5 drive the spirits.
- The summit's map places the altar and statues as Act V objpreset ids 150-153. D2R's map parse (`0x3dfae0`) looks
  those ids up in the act's objpreset table; 1.14d read any id from 150 on as "object class minus 150". A ported map
  or a tool that uses the 1.14d rule gets the wrong objects.

### 10.2 The shared state (game `+0x1a600` .. `+0x1a690`)

| Field | What |
|-|-|
| `+0x1a600` (3 ids) | each statue's unit id, written by InitFn 80 |
| `+0x1a60c` (3 ids) | the Ancient each statue let out |
| `+0x1a618` (3) | each statue's mode record: 0 idle, 3 waking, 4 spent |
| `+0x1a624` | the altar's state, 2 once used |
| `+0x1a628` | the altar's unit id |
| `+0x1a630` (vector) | the living list: Ancients alive, in the order they came out |
| `+0x1a648` (3) | each Ancient's heal step (0, 1, 2) |
| `+0x1a658` (vector) | the spirit list |
| `+0x1a670` | the "one Ancient left" flag |
| `+0x1a678` (map by class) | spirits summoned so far per Ancient class |

The rest of the block is telemetry and debug fields.

### 10.3 Waking them

- **The altar** (`0x590b00`) keeps its id, then for each statue in turn: a missing statue stops the operate there
  (the statues before it keep their change, the altar stays idle); a found one goes to mode 3 (record 3) and gets its
  wake event. With all three found the altar goes to mode 1, then to mode 2 at the end of its animation, state 2.
- **A statue made** (InitFn 80), as when its room loads, keeps its id and takes its mode from the record, so a
  reloaded room shows the fight's state.
- **The wake** (`0x595000`), at the end of the statue's mode-3 animation: a statue already in mode 4 does nothing.
  Else the Ancient is made on exactly the statue's spot as a plain spawn; on success the statue goes to mode 4 and the
  Ancient's id is kept; on failure the event is set again 10 frames later. The statue's footprint is freed by the
  mode-4 change itself (HasCollision4 is 0), not by the wake.
- **The AI init** (`0x59aba0`): an Ancient not yet in the living list joins it, gets the no-treasure flag
  (UNITFLAG_NOTC) and `ua_escalation` (itemstatcost 210) at 0. One already listed gets no-treasure unless it is the
  only one left. It points the Nest skill entry's per-spawn callback (`+0x38`) at `0x59c890`, which adds each spirit
  the Nest makes to the spirit list.

### 10.4 A death (`0x599bd0`)

The special-death dispatcher sends 745-747 (not the spirits) here when the unit enters its death mode, before the
drop is drawn. In order:

1. **Last Ancient** (the living list holds one): every spirit in the spirit list dies and the list empties.
   **Another one:** the death burst. Count = the class's `aip6` (5), cut to what is left of the class's cap (`aip6`
   of the Hell column, 5) after the spirits it already summoned, never below 0. The Nest runs start, do and end at
   once with that count; the spirits appear 2 subtiles below the body (spawnx 0, spawny 2), mode NU, no experience,
   no treasure, and join the spirit list.
2. The Ancient leaves the living list.
3. **One left:** the flag is set and the survivor's no-treasure flag is cleared, so **only the last Ancient drops**.
4. **None left:** the dying Ancient casts its Skill7 (monstats skill slot 6) at its own spot through `0x56b020`: Fire
   Twisters (412) for Talic, Charged Bolt Disk (426) for Madawc, Cold Fissure (406) for Korlic, at its Sk7 level plus
   the difficulty bonus.

No portal, quest state, client message or statue change follows the last kill.

**Pitfall, an Ancient that leaves without dying.** Only the death erases from the living list (plus the reset and
the game's teardown); nothing prunes ids of units that are gone. If an Ancient is removed in any other way (a plugin
deletes it, a mod's skill removes it), its id stays in the list: the next deaths both burst, the "one left" clear lands
on the missing unit, neither real Ancient drops, the spirits stay, and the last Skill7 never fires.

### 10.5 The thinks (rows 149-151)

Talic `0x59ba50`, Madawc `0x59b200`, Korlic `0x59ad00`. Each wraps the classic Ancient's sub-think (`0x5c6600`,
`0x5c6220`, `0x5c6000`) in the summit's steps. Every draw is on the Ancient's own seed.

**Which aip column is read.** The thinks read aip values at fixed record offsets, not by difficulty: the **Hell
column** for ranges, chances and timers, but aip7's **Normal and Nightmare** columns (66, 33) for the life steps,
aip6's Nightmare column (5) for the survivor's burst and aip2's Nightmare column (50) for Korlic's Bash. A mod that
edits only the Normal column of these rows changes almost nothing.

**The prologue (all three):**

- **Escalation** (`0x59ca10`): a life step (0 above 66 % of max life, 1 above 33 %, else 2), the dead count (3 minus
  the living list) and the current `ua_escalation`; the highest becomes `ua_escalation` (stat 210), so **it never
  falls**, and `ua_defeated` (stat 211) becomes the dead count.
- While the "one left" flag is set, the survivor's burst (below) runs, and ends the think if it acted.
- Target: Talic takes the tick's target first; all three fall back to the Ancients' scored pick (`0x5caed0`, filter
  `0x59c380`, [monster-ai.md](monster-ai.md)). No target: Idle 25.

**Shared steps:**

| Step | RVA | Rule |
|-|-|-|
| Heal | `0x59c400` | Skill5 (UberAncientsHeal) once when the first Ancient dies and once more at the second; the step moves only when the cast starts, so each death heals each survivor once |
| Pierce | `0x599f60` | Skill6, set as his right skill (an aura) whenever its aurastate is missing |
| Nest timer | `0x599940` | AI timer 1: under the class's cap, a timer at 0 is set to now + [aip7(H), aip8(H)) = 75..149 frames (one draw); when due it clears and, with the evilhut's exit free (size-2 cross at x + 2, y + 4), casts the Nest for one spirit |
| Survivor's burst | `0x59c770` | count = aip6 (Nightmare column, 5) cut to the cap; none left clears the flag and idles 25; else the Nest is cast with that count, and when it starts the count is added and the flag cleared |
| Escalation spirit | `0x56a950` | at escalation 1+, with the Nest listed and the class under its cap, one spirit of the class beside him (the spot, then 4 rings, mode SQ), no experience or treasure |

So each class has at most **five spirits** between two resets, from the timer, the bursts and the escalation spirits
together.

**Talic.** Prologue, heal, Pierce, Enchant (Skill4) at the target without its aurastate, Nest timer. Unlike the
classic Talic he has **no Uninterruptable gate**. With the Whirlwind listed, closer than aip1(H) (15) and a roll under
aip2(H) (25): when escalated, the escalation spirit, then the Whirlwind's calc5 (100 at escalation 2) as a chance on
one draw, under which calc7's Colossal Volcano (413) is cast at calc4's level (30) on his spot; he whirls aip4(H) (8)
past the target. Else out of melee a walk and IdleNoMode 10; in melee a roll under aip3(H) (75) swings A1 or A2, else
Idle 25.

**Madawc.** Prologue, Pierce, Lightning Enchant (Skill3), Colossal Thunder Storm (Skill4) at escalation 2, heal, Nest
timer. In melee a roll under aip4(H) (25) escapes aip5(H) (7) steps. The Shout (Skill1) on a roll under aip3(H) (25)
unless he has its state or the target its target state. Hit lately with a tick target: a roll under aip2(H) (50) steps
away. Closer than aip1(H) (15) on a roll under aip2(H): the escalation spirit when escalated, then the throw (MissA1
escalated, else MissA2) after a line test to the target; a blocked line walks round it. From aip1(H) on he walks
`dist - aip1(H)` steps, else Idle 10.

**Korlic.** Prologue, Pierce, Cold Enchant (Skill3). At escalation 2 with Blizzard (Skill4): AI timer 2 is set to
now + [aip4(H), aip5(H)) (125..174 frames) and Blizzard is cast at the target when due. Heal, Nest timer. Leap Attack
(Skill1) closer than aip1(H) (15) on a roll under aip2(H) (50), with the escalation spirit first when escalated. Bash
(Skill8) in melee on a roll under aip2's Nightmare column (50). Else the classic Korlic's walk or swing.

### 10.6 The spirits (rows 152-154)

Kind 2 (the engine's target search first), no init, no alt. They read the Hell aip column at fixed offsets, use the
Ancients' scored pick (only the whirl spirit looks at the tick's target first) and never read the event's state.

| Row | Think | What it does |
|-|-|-|
| 152 ColBarbWhirl | `0x59a7f0` | target within aip1(H), roll under aip2(H): whirls aip4(H) past the target (no Uninterruptable gate); else walk out of melee, roll under aip3(H) swings in melee, else Idle 25 |
| 153 ColBarbThrow | `0x59a610` | in melee a roll under aip3(H) escapes aip4(H) steps; within aip1(H) a roll under aip2(H) throws (A1); from aip1(H) walks `dist - aip1(H)` steps; else Idle 10 |
| 154 ColBarbFrenzy | `0x59a340` | out of melee walk; in melee: slot 0 (Frenzy) on aip2(H); slot 1 (Bash) on aip3(H) while the AI counter `+0x44` is under aip4(H), and nothing lowers it, so **at most aip4(H) bashes per spirit**; else a swing on aip5(H) |

### 10.7 The skill functions only the Ancients use

| srvdo | Skill | RVA | Rule |
|-|-|-|-|
| 168 | Talic's Whirlwind (410) | `0x567740` | calc3 is a chance (100 escalated, else 0), one draw every time; under it calc6's skill (Fire Twisters 412) is cast at calc2's level on his spot; then the plain Whirlwind do. It runs on each action frame of the spin, so an escalated Talic casts on every one |
| 169 | UberAncientsHeal (415) | `0x57d030` | life becomes calc1 % of max life (75 % once `ua_defeated` is 1, else 100 %), at least 1, never lower than it already is. No draw |
| 170 | Korlic's Leap Attack (407) | `0x567650` | the plain Leap Attack do, then calc5's skill (Cold Fissure 406 escalated) at calc6's level on his spot, on every run of the do (take-off, flight, strike) |
| 171 | Charged Bolt Disk (426) | `0x579280` | calc1 missiles (`colossalchargedbolt`) from the caster in a fixed ring pattern (`0x435c90`), missile init kind 6 (seed from the target x and 666, path type 10, the Charged Bolt path) |
| 91 | Nest | `0x57c590` | D2R's Nest entry has a count byte (`+0x34`) and a per-spawn callback (`+0x38`); the k-th monster searches k rings. Only the Uber Ancients set either; every classic Nest user leaves both 0 and makes one monster |

- The escalation values come only from the skills' calcs through `stat('ua_escalation'.accr)` and
  `stat('ua_defeated'.accr)`, so whoever writes stats 210 and 211 drives these skills: a mod can reuse them on another
  monster by writing those stats.
- 168, 170 and the last death's Skill7 cast through `0x56b020` -> `0x589930` (the item-effect cast): it refuses a
  skill whose skills.txt `ItemEffect` is 0, aims at the caster's own spot, runs the do without the held-skill check,
  no charge and no delays, and queues S->C `0x9a`. A skill a mod wants to proc this way needs `ItemEffect` set.
- **Charged Bolt Disk count quirk:** the pattern does not make `calc1` missiles exactly. 32 (the shipped value) makes
  36, 1 makes 1, 2 makes 10, 0 or less makes 8, and the seven rings cap it at 64. D2R asserts `count - 1 < 40` but goes
  on.

### 10.8 The join reset (`0x599720`)

Called on every player join (`0x483b20`). It acts **only while the living list holds an Ancient**:

- every Ancient the statues let out is taken out with no kill and no drop (a corpse goes too); each statue goes back
  to mode 0, its record and heal step to 0;
- every spirit in the spirit list is taken out;
- both lists, the per-class counts and the "one left" flag clear; the altar's state and mode go back to 0, so it can be
  used again.

Once all three are dead the list is empty and nothing resets: statues spent, altar used, **one fight per game**.
Leaving the level, dying or having no player near resets nothing; only a join does, at any moment of the fight. A
statue reset from mode 4 stays walkable until its room is made again (the mode change frees a footprint but never
places one).

## 11. Pitfalls

| Doing this | Breaks this |
|-|-|
| Adding the keys to Normal or Nightmare classes | nothing breaks, but the portals still open only in Hell Harrogath: the handler checks it, not the recipe |
| A second Pandemonium recipe with the output `Pandemonium Portal` | it shares the same three once-per-game flags |
| Moving Uber Duriel's spot in the Sands map | nothing: he stands at the room origin of the replaced object's room |
| Monster presets in level 136 | never made |
| Editing the Normal aip column of monstats 745-750 | almost no effect; the thinks read the Hell column (and a few Nightmare values) |
| Removing an Ancient without killing it | the living list keeps a stale id: no drop, spirits stay, no last-death skill |
| A plugin that replaces a Tristram or summit think | must keep the minion steps and the shared-state calls, or the minion caps and the escalation stop |
| Porting the summit map with the 1.14d objpreset rule | the wrong objects at ids 150-153 |

## 12. Not traced

- How the far end of the Pandemonium portals links back to Harrogath (`0x432ce0`, `0x435dd0`) for levels 133-136.
- Property func 36 (`randclassskill`) in the D2R dump, and the Torch's item level in D2R's reward drop.
- What D2R's damage code near `0x44aa5f` / `0x44ac25` exempts for 333 and 705.
- The Red Portal key at the output's first dword (which loader argument it is); with one shipped row it does not
  matter.
- The statue room flag (`0x2f0530`), the reset's fallback `0x5040d0` when an Ancient cannot be taken out (the dead
  count would then read 3), and the Ancients' equipment helper `0x495110` (four fixed items per class; looks
  cosmetic).
- The ordinary monsters of the three mini levels (assumed to come from levels.txt monster lists, as for the Cow
  Level).
