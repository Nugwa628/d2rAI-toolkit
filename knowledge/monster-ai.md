# Monster AI, monster init and corpses (server side)

How the D2R 3.3 server runs a monster's think, what each common AI does with its `aip` values, what a new monster gets
from its unique mods right after it is placed, how the new-monster message describes it, and how player corpses live
and die. Observed in **D2R 3.3 build 93847**; every address is an RVA for **build 93847 only** (image base
`0x140000000`) and must be re-checked in Ghidra on any other build ([engine-re.md](engine-re.md)). The AI notes were
read in the disassembly next to classic 1.10 and replayed against live think logs of Act I monsters (several hundred
thousand thinks matched); the corpse notes were read statically and partly checked on the wire. The act boss notes
(section 11) come from our own disassembly notes of the same build: read in the binary next to classic, not replayed
against live logs unless a line says so.

What every AI routine does and what each of its aip values means is in
[monster-ai-routines.md](monster-ai-routines.md); this page is the framework around the routines.

Related: room population, packs, superuniques, the AI table and the first aip table in
[monsters-spawn.md](monsters-spawn.md); mode changes, animation speed and animdata in
[movement-modes.md](movement-modes.md); per-attack elemental damage (`0x588d50`) and monster stats in
[combat-formulas.md](combat-formulas.md); death penalty and respawn in [engine-server.md](engine-server.md); message
sizes in [network-messages.md](network-messages.md); the save's corpse section in [saves.md](saves.md).

## 1. The tick in detail

The AI think is event type 2 (slot 2 of the monster event table `0x1d1a7a0` is the tick `0x4a2a00`). Order inside the
tick:

1. **Debug gate** (D2R only): the `freeze` console value at `0x2aa6204` (0 in retail) idles every unit, or every
   non-good unit, for 15 frames.
2. Build the tick record (control block, monstats and monstats2 rows, difficulty) and pick the **pet prelude**: when
   control `+0x14` is 0 or 1 a wrapper from `0x2396e70` runs around the think (summons only).
3. **Pre-checks** `0x4a3a20`: stunned (state 21) idles 3 frames; a monstats `opendoors` monster on a door path looks for
   a closed door within 9 and operates it (it idles 5 frames whether or not the door opened); the minion leash.
4. **Target search** by the entry's kind (section 3).
5. **Pre-actions** `0x4a37f0` (only with a target): boss sighting, heal-teleport, blocked-approach retarget.
6. **Deferred state queue** (D2R only): AI state changes asked while the unit was uninterruptable are applied now, in
   order.
7. The think itself (through the prelude when there is one).

A tick that ends in step 3, 4 or 5 never reaches the think, so a stunned, leashed or newly sighted monster makes none
of its think's seed draws on that tick. The tick is also called directly when an animation ends; that caller has two
more debug gates (D2R only, both off in retail): the `monfreeze` value at `0x2aaea9c` skips the tick, and the `monai`
skip mode (per unit at control `+0x18`, or the global at `0x2aaeaa0`) replaces it with a think event 25 frames later.

| What | Value (build 93847) | Confidence |
|-|-|-|
| No target, kinds 1 and 3 | a monster that was hit, or stands on a blocked spot, and has a walk mode wanders 5 (4 draws); else it idles 25 when the nearest player is 35 or more away, 10 under 25, distance - 10 between | high |
| No target, kind 5 | on a blocked spot with a walk mode wanders 5; else the next think in 20 frames, no mode change | high |
| Minion leash | owner farther than 20: velocity bonus 40, wander to within 19 of it; owner a player and within 1: step away. Owner dead or missing, or the minion has no walk mode: leash cleared | high |
| Boss sighting | a unique-flagged monster, a boss, or the Summoner class, with a player target under 20, once: message 0x11, then idle 20 frames | high |
| Heal-teleport (AI flag 0x20) | 40% roll, then only when life < 30% or (a ranged monster and target under 10), then 15%: teleport (skill 184, MonTeleport, mode A1) to a spawn spot not in town; when life < 30% a 25% roll also heals `level << 8` (never above max life, and not under state 52 preventheal). Base classes 10 (bighead1), 345 (councilmember1) and 557 (baalhighpriest) count as melee here whatever monstats says. Up to three draws before the think | high |
| Retarget | melee monster with a walk mode, levels `MonSpcWalk` > 0, target farther than that with no clear approach: take another reachable target, else wander 4 | high |
| Uninterruptable | a state change asked in state 54 is queued, not dropped (classic dropped it with a warning) | high |

### AI control block, the rest of it (monster data +0x30)

The fields already listed in [monsters-spawn.md](monsters-spawn.md) section 6, plus:

| Offset | Meaning | Confidence |
|-|-|-|
| +0x10 | set-state phase: -1 switching, 0 init running, 1 normal, 2 the entry's `alt` think installed | high |
| +0x14 | prelude index (0 or 1 = a pet wrapper, other = none) | medium |
| +0x18 | per-unit skip mode of the `monai` debug command | medium |
| +0x1c / +0x20 / +0x28 | deferred queue busy flag / entries (0x30 bytes, state at +0) / count | high |
| +0x38 | AI flags, two more bits: 0x01 and 0x02 mark a group leader and a leader whose death hands the lead on (monstats `SetBoss` / `BossXfer`) | medium (named from classic) |
| +0x44 / +0x48 / +0x4c | the three scratch params are the think's memory between ticks: Baal's throne keeps its wave number, flags and wait frame there, Diablo the action to resume, a hireling its miss counter | high |
| +0x50 | current AI command (section 5) | high |
| +0x80 | the class a spawner think makes (read by the Ancient statue's spawner branch) | low |
| +0x78 | map-AI path from a preset: `{i32 count; ...; +8 entries of 12 bytes {action, x, y}}` (the Countess's firewall points, town NPC routes) | medium |

Monster data `+0x68` is the AI's "last mode": 3 after a get-hit, **19** after a hit that played no GH animation (soft
hit, block without a block mode, hit recovery skipped). "Was hit" (`0x597450`) is last mode 3 or 19; many thinks branch
on it. Flag helpers: `0x597440(ctl, mask)` tests the AI flags, `0x5972a0(ctl, mask, on)` sets or clears them;
`0x5437b0(unit)` reads the last mode.

### Set-state `0x4a2690`

States 18 and up, or a non-monster, are ignored. If the unit is uninterruptable (or the queue is not empty) the change
is queued. Otherwise: if the current entry has an `alt` think the unit switches to it; else the three scratch params
are zeroed, the AI commands freed, the new entry's `init` runs, and its `think` (or Idle `0x4a3680`) is installed.
A class past the monstats rows always gets state 1.

The `alt` think is how an AI cleans up before another state takes over, and it takes two ticks: the first set-state
only stores the new state and installs the alt; the alt, on the next tick, calls set-state again, which now does the
real switch. Two alts exist in the stock table. `0x5af320` (hireling, Nihlathak) clears the inferno state 12 and idles
one frame. `0x5a8f00` (Diablo, Uber Diablo, Baal and his clone) also cuts the inferno off, and puts the home command
(section 5) back after the switch freed the commands. An owner-less hireling reaches special state 5 or 6 this way
(section 7).

The getter `0x4a36c0(unit, state)` picks the entry: state 0 reads the AI table by the monstats `AI` number, states
1-17 the special table, and states 10-12 fall back to state 0 for a unit that may not switch AI (curse states below).
Both tables have the same entry layout: confirmed on the base table by reading the boss rows (section 11).

Special state table `0x23981f0` (entries `{kind, init, think, alt}` of 0x20 bytes):

| State | Think | Who |
|-|-|-|
| 1 | `0x4a3680` Idle | debug spawns, classes past the monstats rows |
| 3 | init `0x5c2d70`, think `0x5c2a30` | formation |
| 4 | `0x5beac0` (alt `0x5af320`) | hireling |
| 5 | `0x5be000` (same as AI 60 GoodNpcRanged) | an owner-less ranged merc |
| 6 | `0x5bdde0` | an owner-less melee merc |
| 7 | `0x5c1400` (necro pet, melee) | revived monsters |
| 8 | `0x5ccfe0` | town rogue |
| 10 / 17 | `0x5c94b0` (kind 1 / kind 2) | Dim Vision |
| 11 | init `0x5cd2f0`, think `0x5cd060` | Terror |
| 12 | `0x5c6b00` | Taunt |
| 13 | `0x5bbef0` | the Countess (set by the superunique code) |
| 15 | `0x5c4000` | set by a monster skill (`0x585141`) |
| 16 | init `0x5c4570`, think `0x5c4120` | mounted imp |
| 2 / 9 / 14 | `0x4a3c70` / `0x5c7c90` / `0x5bbcb0` | users not traced; 2 and 9 end by setting state 0 |

Who sets a state (callers of set-state, read in the binary): the summon code passes the state from its parameter
block; the superunique spawn passes 13; Dim Vision 10, Terror 11; a monster skill 15; the mounted imp 16; a hireling
with no player owner 5 or 6; a loaded pet or hireling, monster AI setup and the end of states 2, 3, 9, 11, 12 and 16
pass 0. A monster that comes back from the inactive list gets its saved state again.

## 2. When the next think runs

`SetEvent` (`0x48b720`; delete `0x48b890`, get frame `0x48b230`): a frame at or before now becomes now + 1; for a
monster in state 54 the uninterruptable state is cleared first, and **a dead unit gets no think event at all** (D2R
only).

| Scheduler | Frames | Confidence |
|-|-|-|
| `IdleInNeutralMode(n)` `0x4a6d10` | n (0 means 1), sets NU | high |
| `Idle(n)` `0x4a6e50` | n, no mode change | high |
| FindTargetOrIdle with no target | 20 | high |
| UseSkill refused | idle 10 | high |
| NU start (end of any mode) | stunned 45, else monstats `aidel` for the difficulty (0 means 15) | high |
| S4 start | 15 | high |
| Knockback end with no GH mode | 1 (the sand leaper: 15, or 45 when stunned) | high |
| Boss sighting | 20 | high |
| End of an animation with the `monai` skip on | 25 (debug only) | high |
| Freeze applied / removed | its length + 1 / the aidel rule | high |
| Summon | first think 25 frames after the summon | high |
| `UpdateAiCallbackEvent` `0x542440` | deletes the pending think, then: NU, base class 110, 118, 136 or 247, or a mode with no animation events (D2R only): 2 frames; a dying or dead unit: none | high |

The `aidel` rule in full: the NU start only schedules when the pending think is not in the future, so a think an AI
already queued (an idle of 50, say) is kept. `aidel` is one byte per difficulty in the monstats row. A think is a
single pending event per unit: every scheduler above deletes the old one before it sets the new one.

An attack or cast does not reschedule the think: the mode plays out, the unit returns to NU, and NU schedules the next
think at `aidel`. So a monster's attack rate is roughly animation length + `aidel`.

**Dead test** (`0x34c2c0`): unit flag `0x10000` (+0x124) means dead for any type; else a player is dead in mode 0 or 17,
a monster in mode 0 or 12, other types when `+0x128` has `0x4000000`. A test that only checks modes 0 and 12 takes a
kicking player (mode 12) for dead.

## 3. Target search

Kinds (from the AI table entry): 0 none (the AI searches itself), 1 find or idle in NU, 2 find (null allowed), 3 find or
idle without a mode change (one user), 5 find or idle 20, 6 find (null allowed). In the stock table 27 entries use kind
0, 103 kind 1, 22 kind 2; kind 3 is the Vulture's, kind 5 the SandMaggot's, kind 6 the FrogDemon's. Classic kinds 4 and
5 are D2R 5 and 6, and kind 3 is new. With kinds 2 and 6 the think runs with no target; with kind 0 the think (pets,
NPCs, Baal, the Ancient statues) searches for itself.

Whether the search tests line of sight per candidate: AI flag 0x40 forces the test once and is cleared; otherwise it is
made only by a monster that has not seen a target yet (flag 0x08 clear) in a room without the line-of-sight draw. A
found target sets flag 0x08 unless the searcher is good-aligned. An explicit target override (`0x598010`) is asked
first.

**Evil monsters** (`0x595750`): walk the eight player node slots at `game+0x21e0`; a player must be in the same act,
not in town, alive and closer than 55; the range cap is monstats `aidist` (0 means 35). A player that passes is followed
**along its node chain** (its summons and hireling, linked by `0x58d950` when they are created); those compete on
distance and collision only. This is why monsters often attack a hireling first: in a recorded Bighead fight the
hireling was the target of 5,000 of 5,152 thinks. Units a plugin creates as pets must be linked the same way or no
monster will ever target them.

Why the hireling wins: the act, town, death and under-55 tests are made on the player alone. A chained unit skips all
of them and only has to be nearer than `aidist` and (when the test is on) in line of sight, and the nearest unit of all
chains is the target. A merc or pet that stands between its owner and the monster is nearer, so it is picked. The link
call `0x58d950(game, pet, 0, owner's node index)` refuses a unit already in a node, a node index of 8 or more, and a
unit that is not a player or monster.

The bosses do not use this search. Diablo and the Ancients pick by score (`0x5caed0`, section 11), Baal with his own
scan, Mephisto with a room scan.

**Good and neutral units** use scan callback 4: players or monsters, alive, attackable, hostile, within 35. Players
count as threat 14, monsters use monstats `threat`; threat above 1 competes for the best target, 1 or less only as an
alternative, taken when there is no best or it is within 5 and the path to the best fails. The alternative is never
taken when the searcher is a player (a search a pet AI makes around its owner). Pets and hirelings that pick their own
target use `0x595f80` instead: the override, then callback 5 (range 48, line of sight required), with the same
best-and-alternative rule; the Hireable think and the pet thinks call it.

Callbacks 5 and 6 first run the shared test `0x597710`: both units are players or monsters and alive, the target is
not in a town room, has the attackable flag and is hostile. The evil search above does not run it.

| What | Value (build 93847) | Confidence |
|-|-|-|
| Scan with callback | `0x596340(game, unit, arg, fallback, id)`, table `0x1d3f910` of 11 `{scanMode, fn}` rows | high |
| Callback ids | 0-2 no stock callback (the caller's own function runs; ids 0 and 1 scan the unit's room and its near rooms), 3 friendly/dead monster, 4 hostile (35), 5 eligible hostile (48, collision), 6 high threat, 7 closed door, 8 dead minions, 9 high threat with clear approach, 10 living monsters of a class | high |
| Classic port | classic ids 5-9 are D2R 4-8, classic 11-12 are D2R 9-10, classic 10 is gone: map by name, not by offset | high |
| Invisible target (state 146) | part of `0x597710`, so only callbacks 5 and 6 and the Raven's think apply it; the evil search never does. A player target: 80% reject unless in melee, rolled on the **target's** seed (classic: the searcher's); a monster target: rejected in melee. No stock Act I monster or skill makes a player invisible | high |
| Alignment | `0x2f4190`: 0 evil, 1 neutral, 2 good; monstats `Align` (1 good, 2 neutral) is honoured for monsters, so an owner-less good merc fights monsters and ignores players | high |
| Draws | the search itself never draws from the searcher's seed; the only draw near it is the invisible-player roll, on another unit's seed | high |
| Boss scans | id 1 with the caller's own callback: Mephisto's target scan, the Overseer's and Nihlathak's minion scan, Diablo's count of monsters by class. Baal's throne count of living non-hostile monsters within 64 uses id 3 | high |

Distances: `0x596720` takes both sizes off and stops each axis at 0 (a target inside your footprint is distance 0, not
the gap); `0x5968a0` ignores size.

## 4. Movement tactics and their seed draws

All AI randomness comes from the **unit seed** (`unit+0x28`). A plugin that adds draws changes what that monster does
later, but clients never see it.

| Tactic | RVA | Draws | Note |
|-|-|-|-|
| WalkCloseToUnit(n) | `0x4a8320` | 4 (3 when n is 0) | wander around itself: axis coin, `rand(n)`, two sign coins |
| WanderAround a point | `0x4a7650` | 4 | same pattern around a point; walk or run |
| MoveToTarget | `0x4a8a10` | 0, or on a failed path with flag 2: 1 + 4 | flags: 1 force a collision check next time, 2 on failure 70% wander 4 else idle 10, 4 delete the think event on failure; a run under Decrepify becomes a walk |
| Random path type walk | `0x4a7f70` | 1 (first, always) | path type 5 or 6 |
| Escape / move away | `0x4a7df0` / `0x4a7540` | 0 | an axis where both positions are equal moves `-n` (classic moved 0) |
| WalkInRadius | `0x4a86a0` -> `0x4a6ec0` | 0 | straight steps toward the target |
| UseSkill | `0x4a7bc0` | 0 | `(mode, skill, target, x, y, flag)`; a refused skill idles 10 |
| SetVelocity | `0x4a7270` | 0 | `(unit, pathType, velocity bonus, distance)`; path type 1 is stored as 7; the values apply to the next mode change |
| WalkToOwner(n) | `0x4a8090` | 4 (3 when n is 0) | wanders around the owner's position; with no owner it wanders 2 around itself (the leash, pets) |
| WanderToTarget | `0x4a84e0` | as WalkCloseToUnit | ignores its target and wanders around the unit itself |
| UseSkillInRange | `0x4a7990` | 2 | a skill at a random point: x then y, each `rand(2 * range) - range` (the Overseer, Nihlathak) |
| Scaled walk | `0x4a8170` | 0 | walks to the point `scale` away from the target on the line through the unit (circling) |
| Walk to a point | `0x4a8880` | 0 | on failure the pending think is deleted, so the caller must schedule one |
| Run at a unit | `0x4a7510` | 0 | the run always ends with a step count of 1, whatever was set before |

Draw helpers the thinks use: a percent roll is one draw `% 100`; `rand(n)` draws nothing when n is 0 or less, and uses
a mask instead of a modulo when n is a power of two; a coin is the low bit of one draw. So a think's draw count depends
on its parameters: an aip of 0 passed as a wander distance saves a draw.

Other draw sites: the mode-change elemental damage before every mode start but GH (see
[combat-formulas.md](combat-formulas.md)), and the death start. Ordinary mode changes draw nothing else.

## 5. AI commands (leader orders, home points)

Each control block holds a list of AI commands (current at `+0x50`; record `+0x10` type, `+0x14` p1, `+0x18` p2).
Get current `0x4a4800`, free current `0x4a4460`, add `0x4a4a80`, give one to every minion `0x4a48a0`, find by type
`0x4a4710`.

| Type | Meaning | Confidence |
|-|-|-|
| 1 | "attack now", given by a Fallen leader's war cry or a shaman to its minions; other leader AIs use it as "charge" for themselves and their minions | high |
| 3 | Cain's portal job: p1, p2 the point, p3 the stage, p4 walk tries | high |
| 4 | walk to a point (town NPCs): p1, p2 the point, p3 a count, p4 idle frames | high |
| 5 | play a mode at a point (town NPCs; classic 7): p1 the mode (8-11 only), p2, p3 the point, p4 tries | high |
| 7 | home point `{x, y}` (Blood Raven, the Countess, Diablo, Baal; classic 10) | high |
| 10 | "come to unit {type, id}", a fetish shaman to its minions (classic 14) | high |
| 14 | read by the sand maggot's alt think (come up or dig in) | medium |

D2R makes a new command the current one (newest first) and puts no limit on the list. A record has four parameters.
Classic type 5 (the wander loop) is gone, so classic ids above 4 do not carry over: map them by meaning.

**Leaders.** A think tells a leader from a minion by its owner field (`0x4a53c0`): a pack leader is its own owner, a
minion's owner is its leader. A leader gives an order with `0x4a48a0`, which adds the command to every minion; each
minion reads it on its own next think and frees it when done, so an order takes up to one think delay to show. Commands
are freed on every AI state change (the home command survives only through the alt `0x5a8f00`, section 1).

**Map-AI paths.** A preset can carry a path of `{action, x, y}` points (control `+0x78`). Town NPCs walk it and play
the action's mode at each point; the Countess casts at its points. The town NPC walk uses one counter shared by the
whole process (`0x32ef858`) to pick a different path type on every fourth step: **observed** in the code, effect on
play cosmetic.

## 6. What the common AIs do

The full per-AI reference (all AIs, every aip value) is [monster-ai-routines.md](monster-ai-routines.md); the table
below is the short list for the Act I roster.

aipN is the i16 at monstats `0x90 + 6*(N-1) + 2*difficulty`; rolls are `% 100` compared signed. "Stall" = idle frames.
Zombie, Fallen, Fallen Shaman, Andariel and the generic pet are in [monsters-spawn.md](monsters-spawn.md); behaviour
notes for those are below the table. All confidence **high** (read in the binary, matched in replays) unless marked.

| AI (monai row) | Think | aip meanings | Behaviour worth knowing |
|-|-|-|-|
| 1 Idle | `0x4a3680` | none | idles 200 frames forever (critters, rogue1) |
| 2 Skeleton | `0x5b43a0` | 1 approach %, 2 stall, 3 attack %, 4 A1 vs A2 % | the plain melee pattern |
| 4 Bighead | `0x5ac6e0` | 1 healthy life %, 2 circle %, 3 spit % while healthy, 4 spit % while hurt | hurt: backs off under 3, closes in beyond 15; A2 is the spit |
| 5 BloodHawk | `0x5aca20` | 1 charge %, 2 wander-away %, 3 attack %, 4 back-off velocity, 5 charge velocity | a landed charge attacks without a roll |
| 7 Brute | `0x5acc20` | 3 attack % (reused as circle %), 4 A1 vs A2 % | aip1 and aip2 are never read; walks faster the more hurt (bonus up to 60) |
| 9 Wraith | `0x5b9bb0` | 1 approach %, 2 stall, 3 attack % | |
| 10 CorruptRogue | `0x5ad430` | 1 approach %, 2 stall, 3 attack %, 4 run velocity bonus, 5 run % | beyond 20/17/14 (by difficulty) always runs at the target |
| 12 Goatman (also 19) | `0x5b0490` | 1 approach %, 2 stall, 3 attack % | |
| 14 QuillRat | `0x5b2d50` | 1 activate range, 2 shoot %, 4 walk-away distance (min 3) | beyond aip1 it only wanders |
| 26 Arach | `0x5b4980` | 1 attack %, 2 circle %, 3 engage %, 4 run-away range, 5 hurt life % | hurt: lays (Skill1) then retreats until life > 75% |
| 28 Vampire | `0x5b7590` | 1 melee %, 2 cast %, 3 active distance, 4 upgrade-cast %, 5 spell flags (1 slot 0/3, 2 slot 1, 4 slot 2) | flees under 33% life until 75%; slot 1/2 casts have an 11-think cooldown |
| 30 Fetish | `0x5aec30` | 1 attack %, 2 stall, 3 attacks before running off, 4 target life % | runs off only while the target's life is above aip4 |
| 35 CorruptArcher | `0x5ad600` | 1 approach %, 2 shoot %, 3 stall, 4 run-away %, 5 always-run distance, 6/7 skill1/2 %, 8 walk-toward distance | runs away when the target is under 6 |
| 36 CorruptLancer | `0x5ad9e0` | 1 approach %, 2 attack %, 3 stall, 4 run %, 5 always-run distance, 6-8 skill0-2 % | charges with monstats2 `MeleeRng` steps; attacks unrolled after a charge |
| 37 SkeletonBow | `0x5b44f0` | 1 shoot %, 2 stall, 3 approach %, 4 walk steps, 5 desired distance | shoots only under 20 |
| 43 FoulCrowNest | init `0x5b28e0`, think `0x5add80` | 1 spawn interval (frames), 3 spawn count | dies itself (mode 0) after aip3 spawns; idles 25 when the target is beyond 20 |
| 59 BloodRaven | init `0x5bbb30`, think `0x5bb360` | none | raises zombies (chance +3% per think, cap `2*difficulty + 8` raises), leashed to a home point (45/50) |
| 63 GargoyleTrap | `0x5b00e0` | 1 shoot distance, 2 shoot %, 3 recovery, 4 stall | faces one of four directions; shoots only when nearly aligned (dx or dy < 6) |
| 64 SkeletonMage | `0x5b46b0` | 1 shoot %, 2 approach distance, 3 approach %, 4 too-close, 5 walk-away %, 6 fire distance, 7 circle %, 8 stall | |
| 78 / 79 trap arrows | `0x5b5f60` / `0x5b5b40` | 1 min distance, 2 max distance, 3 re-fire delay, 4 second delay | in levels 40+ a per-level random mode (one draw, cached in the region) can pick the skill instead of A1 |
| 90 Griswold | `0x5be220` | none | 80% attack in melee, else 50% walk in |
| 98 Smith | `0x5c3590` | none | no draws; walks faster as he loses life |
| 60 GoodNpcRanged | `0x5be000` | (merc) 2 skill slot, 3 mode | shoots within 20 outside town |
| 61 Hireable | `0x5beac0` | 1 range type (1 ranged, 2 melee, 3 hybrid), 2 skill slot as basic attack, 3 basic mode | section 7 |
| 109 Totem | `0x5cf810` | 1 walk-away %, 2 ignore-target %, 3 teleport-to-owner distance, 4 follow distance | |
| 110 Vines | `0x5d0630` | 1 skill interval, 2 target range, 3 stall, 4 walk-away from poisoned targets, 5 warp distance | |
| 111 CycleOfLife | `0x5c83c0` | 1 skill interval, 2 corpse range, 3 stall, 4 walk-away, 5 warp distance | |
| 112 DruidBear | `0x5c95d0` | 1 stall after A1, 2 run %, 3 skill % | |
| special 13 Countess | `0x5bbef0` | 1 run %, 2 stall, 3 attack % (+10) | stays in her home area; casts her firewall at the preset map-AI points |

Notes on the AIs in the other file:

- **Zombie** (3): in the Burial Grounds (level 17) every zombie always runs at its target, hit or not.
- **Fallen** (6): a Fallen that sees a corpse still in its death mode within 15 (from the rooms' last-four-dead lists)
  flees 12 away; the next combat think then attacks unrolled. A leader (its own owner) within 15 rolls aip1 to give
  its minions command 1 and plays S2 (the war cry).
- **Fallen Shaman** (13): the resurrect skill's `Sk1mode` is a monseq name, which the monstats linker `0x3a9230` turns
  into mode SQ (14); a mode token longer than three characters that is not a monmode code always becomes SQ. The
  command roll (aip1) is drawn every think whether or not a corpse was found. A good-aligned shaman under the D2R
  bind-demon state 207 searches only within 20.
- **Andariel** (34): her quest runs from quest code on death; the AI has none. The same holds for Blood Raven, Griswold
  and the Smith.

### Curse states

- Dim Vision (10/17): attacks in melee if it can, else 20% wander 3, otherwise idle 10.
- Terror (11): flees 30 (or control +0x44) at run speed; leaves the state on its own when state 56 is gone.
- Taunt (12): walks at the taunter and attacks it in melee; leaves when the taunter is gone or in town.
- The getter refuses 10-12 for a monster with no walk mode, monstats `boss`, no `switchai`, state 54, or no attackable
  flag. It does not refuse uniques (classic refused them when the curse was applied; the D2R apply side is not traced).

## 7. Hirelings and summons

| What | Value (build 93847) | Confidence |
|-|-|-|
| Owner-less merc | aip1 = 1 (ranged): special state 5, else special state 6; both attack monsters near them | high |
| Follow distances | default max 24 / min 16 / 5 steps; a control +0x44 of 17-19 gives `2*p`, `p`, `p >> 1` | high |
| Teleport to owner | farther than 100 | high |
| Combat | only outside town, target under 25; a stuck merc wanders 5 with a 6/128 (melee) or 12/128 chance | high |
| Different area from the owner | runs to the owner (the test compares the area id at each unit's point, so two areas of one level differ) | high |
| Merc `aidel` | 1 for every stock merc | high |
| Merc while walking or running | the think returns at once: a move always finishes first | high |
| Follow by distance | over the max: run after the owner through the owner's last 20 positions; between min and max while the owner moves: walk (owner walking) or run (owner running) to a point 8 beyond the owner's path target, trying 8 directions (classic 7) | high |
| Idle merc | owner near and still: one roll per think, 5% wander near the owner, else idle 5 | high |
| Merc attack roll | melee (aip1 = 2): 98%; others `min(p0 + 40 + 2 * level, 95)`, where p0 grows by 10 on each miss and resets on a hit. p0 is the same field as the follow distance override | high |
| Ranged merc crowded | target within 3: 50% to step back toward the owner or away from the target before it shoots | high |
| Leaving state 5 / 6 | nothing in these thinks puts an owner-less merc back into state 4 | high |

The Hireable think (AI 61 and special state 4, `0x5beac0`), its Combat `0x5bf6e0`, skill choice `0x5bf070`, buff
target `0x5bf5a0` and follow helper `0x5c1460` are described step by step in
[skills-missiles.md](skills-missiles.md#9-mercenaries-in-the-world). D2R replaced classic's class-id tests with aip1-3,
so a new hireling class only needs the right aip values: aip1 range type, aip2 the skill slot used as the basic attack
(0 = none), aip3 the mode of the basic attack when aip2 is 0. The merc thinks were replayed against live think logs
(several thousand thinks matched) for the owner-following part; Combat and the skill choice were read, not captured.

**Summon** `0x4350e0` reads a 0x30-byte block: `+0x00` flags (1 use the given x/y, 2 replace the tracked summon, 4 fail
rather than retry in mode 4, 8 not a revive), `+0x08` owner, `+0x10` u16 monster id, `+0x14` AI special state, `+0x18`
mode, `+0x1c`/`+0x20` x/y, `+0x24` pettype, `+0x28` petmax. It spawns through `0x492cf0`, registers the pet, writes the
owner into control `+0x60/+0x68/+0x6c`, runs set-state with the given special state and schedules the first think 25
frames later. D2R has no null-owner path. Pets are not leashed by the core tick (the core leash reads another owner
field); each pet AI follows its owner itself, and the pet prelude removes a pet whose owner is gone.

More on the summon call (read in the binary): the spot is the given x/y with flag 1, else the owner's target unit, else
the first point of the owner's path. The spawn uses flags 0x42 (no normal mods, no minion party). Unless flag 8 is set
the pet gets the revive unit flag; every pet gets the no-treasure-class flag. The skill code then sets the level, the
passives and aura, and links the pet behind its owner (`0x58d950`, section 3). The pet prelude runs only while the pet
is in state 0 and its set-state phase is 1: owner missing or dead removes the pet without running its think
(confidence low beyond that).

### Summons and pets in classic 1.14d

Read in the classic 1.14d Game.exe (**addresses are 1.14d's, not 93847 RVAs**; not re-checked in D2R).

- **Creation** (SKILL_CreateSummon `0x56d940`): the room at the point is found from the owner's room, the spawn uses
  flags 0x42 (no normal mods, no minion party), first at the spot alone, then within 4 rings (skipped for summon
  flag 4). The pet starts in skills.txt `summode` (via `0x56e620`: S2 Hydra, S1 golems and Raise Skeleton, NU
  Valkyrie): CreateUnit `0x555230` sets the mode before the monster fill `0x574250`, whose InitAnimFrame `0x5533d0`
  loads the animation at frame 0 with no mode-change or end event, so the pet holds that mode until its first think
  starts another one. Then UNITFLAG_NOTC, the owner link (`0x58f030`) and the first think 25 frames on. monstats
  inTown is set on Valkyrie, the golems and the skeletons.
- **Summon buffs** (SKILLNEC_ApplySummonBuffs `0x5c4470`), in order: passive and aura stats, aurastate, the calc1 life
  bonus and the sumskills (a Hydra head gets HydraMissile); item events of each passive stat and nonzero aura stat
  (itemstatcost itemevent1/2, skipped when the pet already has that key; D2SetAuraEvent `0x56e740`); the skill's
  auraevent1..3 keyed by aurastate, old ones of that key removed first; sumumod 1..42 (InitUmods); sumoverlay; the
  summon items (SKILLASS_EquipSummonItems `0x5d6b60`) at item level min(owner level, max(1, 3 x skill level)) when the
  caller passes 0, as all but Valkyrie (`0x5dc1e0`, its calc2) and the corpse-position summon do.
- **AI skill use** (SERVER_UNIT_CastSkill `0x5dead0`) passes bResetSkillOnSameMode 0 to the mode start (`0x5a7c20`),
  so a monster keeps the used skill through the mode start when it has the skill; the skill's target is the path's.
- **Hydra (think 86, `0x5e9e60`)**: same branches as D2R's. The expiry test is signed (the head dies once the frame
  is past p[0], which the Hydra skill writes through `0x5b0d70`); the death is a skill command at position 0, 0 with
  mode 0 (`0x5ddfc0`); the roll is the unit seed's new low word `% 100`; the last step is PlanNextAiMove (`0x5de080`,
  idle 10, going neutral first). hydra1-3 have monstats Align 1, so the heads are on the players' side.

| A pet leaves its owner's list when | 1.14d path |
|-|-|
| the owner leaves | FreePets `0x5746d0` from `0x5349d6`: PETS_FreeOrKillPetList `0x574570` pettype by pettype, each pet through KillHandlerPet `0x574450` (NOXP, then killed if monstats killable, else a death with the owner as attacker) |
| the owner dies | after the body: every pet type from row 1 through KillHandlerPet (no attacker, no experience, the 0x7a pet removal to every player), the hireling row 7 with its own kill |
| the pet is killed | SERVER_KillMonster `0x57ccb0` -> `0x5751a0` (`0x5750e0`, removal message, no second kill) |
| the pet died another way | player event 0xb (`0x580be0`, ally info `0x575630`) runs PETS_RemoveDeadPets `0x575590`; first set 250 frames after placement (`0x534ad0`), then every 30 |
| the pet's unit goes | `0x5431f0` -> PETS_HandleDeadPet `0x5752b0` |
| the owner changes area | PETS_ManagePetsByType `0x5754b0` (from relocation `0x555057` and act warp `0x53aea6`): a pettype warp pet comes to the owner (`0x574cc0`, footprint put on the owner's point with no free-spot search, `0x650910`); a range pet more than 0x640 squared subtiles away dies (`0x575380`); any other dies (`0x574c60`). The hireling (row 7) is handled between the revive row and the dopplezon row. A resurrect (`0x54c0e0`) relocates through `0x53aec0` the same way |

**Monster skills** (classic monster init, SetSkill / SetSkillMode): every monster gets its monstats Skill1..8 that
have a level above 0, at `Sk<n>lvl` plus the difficulty's MonsterSkillBonus; a hireling gets these before its
hireling.txt skills. Without the entry the AI's cast has no used skill and its swing does nothing.

## 8. Monster init: what a new monster gets

After the spawn core places a monster, `0x543e90` makes its base stats (see [combat-formulas.md](combat-formulas.md)):
the component pick `0x5429d0` comes first, then **one HP draw** on the unit seed, then the monprop rolls. The name seed
stays 0 until mod 1 runs.

### Monster data fields (unit +0x10)

| Offset | Meaning | Confidence |
|-|-|-|
| +0x08 | u8[16] component values | high |
| +0x18 | u16 name seed (only mod 1 sets it) | high |
| +0x1a | u16 type flags: 1 boss (every pack leader, superunique and companion), 2 superunique, 4 champion, 8 counted as a unique of its level, 0x10 minion, 0x20 possessed, 0x40 ghostly, 0x80 multishot, 0x200 aura enchanted also gives an item aura, 0x400 skipped by the resist routine | high (0x200, 0x400 writer unknown) |
| +0x20 | u8[9] mods, zero-terminated | high |
| +0x2a | u16 superuniques row (-1 none) | high |
| +0x70 | u16 spawn flags: 1 and 8 make the update send 0x57 kind 1 / kind 2, 2 do not count as a unique, 4 Terror Zone | medium |

### Start handlers (table `0x2395fe0`, 45 entries, `(unit, mod, isLeader)`)

C(n) is monumod row n's `constants` value, d the difficulty.

| Mod | What it does | Confidence |
|-|-|-|
| 1 rndname | leader: one unit-seed draw, the name seed is its low 16 bits (the only handler that draws) | high |
| 2 hpmultiply | leader: life +C(4+d)% (champion) or +C(7+d)% (unique) and regen stat 74 zeroed; a minion: +C(1+d)% | high |
| 4 leveladd | champion level +2 and experience x3; otherwise +3 and x5 (D2R moved the champion case here) | high |
| 5 strong | damage % and to-hit % from C(12)-C(15), scaled by ChampionDamageBonus | high |
| 6 fast | velocity bonus `clamp(2048 / Velocity - 128, 10, 100)` | high |
| 8, 27, 28 | the resist routine (below) | high |
| 9, 17, 18, 23 | fire, lightning, cold, poison damage `+= monlvl DM * C / 100`; cold adds cold length, poison its length; then the resist routine | high |
| 25 manahit | mana steal into stats 62/63; then the resist routine | high |
| 16 champion | leader: damage and to-hit from C(10)/C(11), velocity +20 | high |
| 26 teleport | leader: skill 184 level 1 and AI flag 0x20 (section 1) | high |
| 30 aura | leader: an aura skill set as its right skill (below) | medium |
| 36 ghostly | flag 0x40, stat 36 = 80, cold damage, adds mod 16 | high |
| 37 fanatic | stat 16 = -70, adds mod 16 | high |
| 38 possessed | flag 0x20, life +100%, adds mod 16 | high |
| 39 berserk | life -75%, damage and to-hit x3 ChampionDamageBonus, no mod 16 | high |
| 41 always_run_ai | queues a MonUMod event (type 7) 75 frames later | high |
| 44 hidedead | state 187 (D2R) | high |

The other mods have no start handler (their effect is on hit, on damage or on death).

**Resist routine** `0x38e430`: skipped for minions and flag 0x400. Mod 28 first doubles defense. If two of fire, light,
cold, poison, damage and magic resist are already 100 or more, nothing more. Else: 8 (resist) +40 to cold, fire and
light where under 100, stopping when a second immunity appears; 9/17/18/23 +75 to their element; 25 +20 magic; 27 +20
to cold, fire and light where under 75 (same stop); 28 +50 damage resist.

**Life change** `0x49bd50`: setting max life (stat 7) runs the server stat callback, which rescales life and, for a
monstats `DamageRegen` row, resets regen stat 74 to `(DamageRegen * (newMax >> 8)) >> 4`. A unique's minions keep a
regen that matches their raised life; only the leader loses it (mod 2).

**Aura enchanted.** Under D2RLoader the table's mod-30 slot points into D2RCore (a thunk at `0x3e2b67c`); D2R's own
version is `0x495cd0` (one weighted draw on a private seed made from the name seed). The pick: Uber Mephisto always
Conviction 20; one superunique row always Fanaticism; otherwise one of seven auras (six below monster level 20) with
level `clamp(level / divisor, 1, 99)`. Aura and pick details inside D2RCore are D2RCore's, not the game's: **observed**.

### Pack calls and draw order

| Call | RVA | What |
|-|-|-|
| count as a unique | `0x49eed0` | sets flag 8 and bumps the level's count (not for spawn flag 2, an already counted unit, or alignment 2) |
| assign a mod | `0x4995e0` | append the mod, run its handler for the leader |
| run start handlers | `0x49e780` + `0x49e860` | the fixed list 1, 2, 3, 4 (`0x1d1df80`), then the leader's mods: each on the leader, then on every minion (callback `0x49e960`) |
| promote a companion | `0x49ab40` | counted, flags \|= 5, mod 16 appended, handlers on it alone |
| finish a superunique | end of `0x499ba0` | handlers, the aura handler again if the row lists mod 30, then counted (flag 8) with mod 22 appended |

Minions run the **leader's** list, not their own: they get 2 (minion life), 4, 5, the elemental mods with the minion
constants, 6; the leader-only handlers do nothing on them and the resist routine skips them. Superuniques do carry flag
8. Leader draw order: unique pack: champion roll, count roll, one per rolled mod, minion count, mod 1; champion:
champion roll, champion mod pick, mod 1, companion count; superunique: one pick per difficulty level, minion count,
mod 1; companion: mod 1.

In recorded expansion games (not ladder) monster life and the elemental mod damage matched only with the monlvl `L-`
columns; compare the game-type rule in [combat-formulas.md](combat-formulas.md): **observed**.

## 9. The new-monster message (`0xac`, builder `0x47c570`)

Header: id, u32 unit id, u16 class, u16 x, u16 y, u8 life (`0x80` full), u8 total size. Then an LSB-first bit tail:

1. mode, 4 bits: the unit's mode when it is 0, 8, 9 or 12, else 1;
2. one bit "has components", then each component value in 1 bit when its look has fewer than three choices, else
   `bit_width(choices - 1)` bits (a minion party member whose values are all 0 goes without them);
3. when the monster has mods (or unit +0x124 bit 9): a 1, seven flag bits (champion, unique, superunique, minion,
   ghostly, 0x200, 0x400), the superunique row in 16 bits when flagged, each mod in 8 bits up to the first 0 or nine,
   an 8-bit 0, the name seed in 16 bits, an owner bit (plus a 32-bit id for a player's pet); otherwise one 0 bit;
4. a Terror Zone bit (with spawn flag 4, plus 9 more bits of the zone record);
5. a bit for unit flag 0x400 (plus 31 bits of unit +0xec);
6. D2RCore's stat tail: every stat with itemstatcost `Send Other` and `Send Bits` > 0 (id 16 bits, the param when it
   has send-param bits, the value), ended with 0xffff; one 0 bit when there is none;
7. zero padding; the whole message stays under 0xf4 bytes.

The game builds it again (`0x538690`) each time a client starts seeing the unit, so mode, position and life are those
of that moment. **`0x57`** (`0x4778b0`, 31 bytes, from the per-frame monster update `0x59d400`) only goes out with spawn
flag 1 or 8; random Act I bosses never get one, their mods travel in `0xac`. Confidence: high (all 1,616 captured
`0xac` of nine games rebuilt byte for byte).

### What else an AI puts on the wire

The AI itself sends nothing; its side effects reach the clients through the ordinary unit update.

| Effect | How the clients learn it (build 93847) | Confidence |
|-|-|-|
| A state set or cleared by a think | the per-client monster update `0x59d527` calls the state sender `0x539880` when the unit has changed states; `0x53a210` then sends one message per changed, sendable state, after that update's mode message: `0xa9` cleared, `0xa8` when the state's list holds stats (16-bit id, param and value at their send widths, ended with 0xffff), `0xa7` otherwise | high (static) |
| A unit that comes into view | `0x538690` sends the unit's state list right after `0xac`; hosted-game captures show a monster's states as messages of their own after its `0xac` | high (static), observed |
| A class change (Baal leaving the throne) | the unit keeps its id; state 142 (changeclass) with a stat list holding stat 355 = the new class goes out as `0xa8`, and the clients redraw the unit from it. A client that sees him later gets `0xac` with the new class | high (static) |
| A unit freed in the think that set a state | no update runs for it again, so the clients only get its removal (Baal's state 146 at the portal) | high (static) |
| Boss sighting, taunts, laughs | unit message 0x11 through `0x491960` (classic sound 16) | high |

Message layouts are in [network-messages.md](network-messages.md).

A unit's stat list (unit +0x88) holds sorted entries of **16 bytes** `{u32 layer, u32 stat, i32 value, u32 0}`; the
classic-looking helpers that step 8 bytes are not the ones D2R runs: **observed** (measured, not read in code).

## 10. Player corpses

| Step | What happens (build 93847) | Confidence |
|-|-|-|
| Death | the corpse creator `0x42c110` runs at the Dead mode enter `0x42a5e0`, after the death penalty | high |
| What moves | the cursor item and body locations 1-12 in order (belt and both swap arms included) | high |
| Belt | potions in the boxes an empty belt has (the bottom row) stay on the player; the others go to free inventory cells, else to the ground | high |
| Corpse unit | only while the owner has fewer than **16**: a player-type unit in mode 17 with state 7 (playerbody), one game-seed draw, stat 13 = 75% of the experience loss; then 0x8e {1, owner, corpse} to everyone | high |
| 16 corpses | no unit: every listed item goes to the ground (freed when there is no spot) | high |
| Gold | carried gold set to 0; any left becomes a pile at the corpse (the death penalty's own pile goes out earlier) | high |
| Pickup | C->S 0x13 -> `0x4b2be0` -> `0x42b220`: all 13 locations come off the corpse, experience back to the owner once, then three passes: own body location (or the paired one: arms 4/5, rings 6/7, swap 11/12), automatic body or belt, anything. What does not fit stays on the corpse (classic was all or nothing) | high |
| Emptied | the corpse leaves the list and its room, 0x8e {0, owner, corpse}, unit freed, sound 100 (items left: sound 0x18) | high |
| Respawn, waypoint, portal, act change | **nothing**: the corpse stays where its owner died | high (wire) |
| Join | `0x485350` moves every corpse of the joining player next to the town start of its act and sends 0x8e {1}. Its only other callers are debug chat commands | high |
| Leave | every corpse unit is freed; the save keeps the items | high |
| Load | `0x531af0`: a count that **must be 0 or 1** (else load error 0x68, the character does not load); the three u32 per corpse are skipped; a loaded corpse returns no experience | high |
| Save | `0x52f090`: at most one corpse, the one with items whose summed item value is highest (the later one on a tie); a corpse without items is never saved (classic wrote the first with items) | high |

So a character with several corpses at leave keeps only the most valuable one's items in its save; the other corpses'
items are lost.

## 11. Act bosses

Read in the build 93847 disassembly next to classic 1.10; draws are on the boss's own seed. Not replayed against live
think logs. AI table rows (`{kind, init, think, alt}`):

| Row | Who | Kind | Think | Alt |
|-|-|-|-|-|
| 50 Mephisto | class 242 | 1 | `0x5b1ca0` | none |
| 85 HighPriest | the Council 345-347, baalhighpriest 557 | 1 | `0x5be300` | none |
| 51 Diablo | 243 and the clone 333 | 1 | `0x5c8d40` | `0x5a8f00` |
| 55 Izual | 256 | 1 | `0x5b0cb0` | none |
| 120 Overseer | Shenk and the overseers | 1 | `0x5c2290` | none |
| 128 Nihlathak | 526 | 2 | `0x5cc240` | `0x5af320` |
| 132 AncientStatue | 537-539 | 0 | `0x5c69d0` | none |
| 133 Ancient | 540 Talic, 541 Madawc, 542 Korlic | 2 | `0x5c5f60` | none |
| 134 BaalThrone | 543 | 2 | `0x5c7070` | none |
| 135 / 140 BaalCrab / clone | 544 / 570 | 0 | `0x5a8b80` / `0x5a8cf0` | `0x5a8f00` |
| 136 BaalTaunt | 545 | 1 | `0x5c6cf0` | none |
| 138 BaalToStairs | 559 | 1 | `0x5c7580` | none |
| 139 BaalTentacle | 562-566 | 1 | `0x5c6eb0` | none |
| 141 BaalMinion | 571-573 | 1 | `0x5c7750` | none |
| 144 / 146 / 147 | Uber Izual / Uber Mephisto / Uber Diablo | 1 | `0x5b60c0` / not noted / `0x5cfce0` | 147: `0x5a8f00` |

No think here branches on the superunique or boss flags; a boss behaves as its class's row says.

### The scored target pick (`0x5caed0`)

Diablo and the Ancients choose their own target. The pick walks the eight player node lists (section 3). A cull decides
per list head: Diablo's (`0x5d0ce0`) wants a player in his act within 1020; the Ancients' (`0x5d0be0`) a player on the
same level. A refused head is skipped with its whole chain. A head in mode 0 or 17 scores 0; every other head and every
unit chained behind it is scored. Only a strictly higher score replaces the pick, so a tie keeps the earlier unit.
Nothing is drawn. Classic 1.10 culled at 55; 1.14d and D2R use 1020.

| Score term | Value |
|-|-|
| range | 100 in melee range, else 75 with a clear line, else 0 |
| resists | (cold, in melee only, + 4 x (damage + 2 x magic) + lightning + fire) / 15 |
| damage | (poison max >> 8 + cold, magic, lightning and fire max + max damage) / 2 |
| chill | 200 with state 11 |
| low life | 300 under 20% |
| skills | (attack rank x level of the right skill + the same for the left) / 4, levels with bonuses |
| sum | (5 x range + low life + 2 x (chill + skills + damage) + resists) / 22, at least 1; 0 for a monster with threat 1 or less |

The score function is `0x5cba10`; D2RCore adds a threat bonus for a living player from stat 84 of a state-231 list that
names the monster (confidence medium; no stock path that adds that state was found). After the eight lists D2R also
scores a ninth list and keeps the best of a tenth as an alternative, taken when that unit is within 5, the pick is out
of melee range and no path to the pick exists; what the game links into those two lists is not traced. Baal uses his
own scan `0x5a9a10` with cull `0x5aa710` (players within 1020 and their chains); his score call leaves the readable
code and was not read.

### Weighted choosers (Diablo, Baal)

Both fill a weight table from the situation (in melee, blocked, clear line, far from home, target's skills) and make
**one** draw to pick an action; with every weight 0 nothing is drawn.

- **Diablo** (`0x5ca750`, 17 weights, start weights at `0x1d40e90..0x1d40ec0`): walk at the target, attack 1, attack
  2, the fire stomp (S4), the inferno breath (skill slot 0; a scratch param makes the next think go on with it), skill
  slots 1-6 (slot 6 is the bone prison), a random-path walk, walk home, a bone prison on the target's town portal (in
  the Chaos Sanctuary), a wander when stuck, else an idle of 12 / 8 / 4 frames by difficulty. His home is AI command 7,
  set at his first think. Slot 7 is an aura he recasts whenever its state is gone. A target whose right or left skill
  is Blizzard, Meteor, Fire Wall above level 3 or Immolation Arrow above level 7 changes the weights (right skill
  tested first, levels with bonuses).
- **Uber Diablo** (`0x5cb260`) is the same code with other weights; he measures "far" to himself, not to a home point,
  and never walks home. Before the choice, on every think, he may open a hole (class 711) at a player when neither other
  Uber boss is near: a 33% roll with
  fewer than 2 holes and fewer than 10 pit lords near, else 15% with no hole and fewer than 4 pit lords. The hole (AI 76,
  `0x5ae410`) spawns up to aip1 monsters, one every aip2 frames, flagged no experience and no treasure class, then
  dies.
- **Baal in the chamber** (rows 135 and 140, case roll `0x5a95e0` / `0x5aa200`, weight tables
  `0x1d406a0..0x1d40790`): 16 cases: wait, four kinds of walk (in radius, random path, around himself, home), two
  drains, tentacles, attack 2, nova, inferno, cold missiles, the teleport away and the clone (`0x5aae60`). The weights
  match classic, except that a target under 33% life sets the case-10 weight to 200 directly. The clone runs the same
  chooser with cases 7, 9, 14 and 15 turned into case 2, and dies with its leader or stored owner. A drain case with no
  target runs case 0. A dead tentacle is taken off the map.

### Baal's throne (row 134) and the walk-off (row 138)

The throne keeps three values in the scratch params: the next wave (0-5), two flag bits (taunt played, wave called) and
a frame before which it only waits. Each think past that frame counts the living non-hostile monsters within 64.

1. Some alive: skill slot 1 at the target on an aip1 roll, else a wait of 10.
2. None alive, taunt not played: skill 285 on himself (it uses up every monster corpse within 40 and deals no damage:
   it only clears the bodies), the laugh, wait 250 frames.
3. None alive after the taunt, waves left: the spawn skill 286 at (x, y + 13) for superunique row 61 + wave number
   (five rows, wave table `0x1d40e10`), wait 100 frames, taunt bit cleared.
4. None alive after the fifth wave's taunt: he changes class to 559 (baalcrabstairs) in mode NU through the monster
   re-init `0x5426c0`, keeping his unit id; set-state 0 makes row 138 his think; state 142 with stat 355 = 559 tells
   the clients (section 9); wait 5.

Row 138 then looks for the portal object 563 within 25 on each think: none, wait 25; farther than aip1, walk to it;
closer, the quest call that opens the Worldstone Chamber (`0x546dd0`), state 146, path stopped, think events deleted,
and the unit leaves the world. If the portal is still in its operating mode it gets its end-of-animation event then.
There is no timer or teleport fallback: if he cannot reach the portal the walk is retried on every think and the
chamber stays shut. In the stock tables the throne's aip1 is 25 and the stairs form's aip1 is 4 on every difficulty.
A mod that changes class 559's speed, the portal's place or the throne room's collision can therefore lock the fight.

### Mephisto, the Council, Nihlathak, the Ancients

- **Mephisto** scans the adjacent rooms himself (hostile, attackable, living units within 32) and keeps the unit with
  the fewest hit points and the closest player. aip1 is the chance a unit in melee range gets a spell volley instead of
  a melee answer, aip2 the chance the volley aims at the lowest-life unit, aip3 at the closest player. In classic the
  closest-player test looked at Mephisto's own unit type and never matched; D2R tests the scanned unit, so the aip3
  branch is live. Uber Mephisto runs the same think after a minion step.
- **The Council** (row 85): the heal skips a monster at 0% life; the lightning branch is skipped with no draw when the
  class has no missile on its S1 mode.
- **Nihlathak**: every think first calls quest code (`0x548e90`), which sends the quest status once. His corpse skill
  needs a corpse found by `0x437b50` around his **target**: radius 10, monsters in mode DD only (a dying one or a
  player's body does not count), passing the corpse rule Corpse Explosion uses (`0x33a4f0`). The first match of the room
  walk wins, not the nearest; a room's unit list runs newest first. The search draws nothing. He whips a minion within
  25 (the Overseer: heals one under half life within 24, whips a non-unique one within 20; minions are base class 453
  without state 141).
- **The Ancients** (row 133 dispatches by class: Talic `0x5c6600`, Madawc `0x5c6220`, Korlic `0x5c6000`) use the
  scored pick above; Madawc and Korlic on every think, Talic only when the tick has no target. D2R changes: Madawc
  walks toward his target only from aip1 away and idles 10 when closer (classic walked with a negative step count); his
  shout needs skill slot 0 set; Talic's whirlwind is a direct mode change. The statues (row 132) act only once the
  quest code says the rite may start.

### Calls from a think into quest code

| Think | Call | What it does |
|-|-|-|
| Nihlathak | `0x548e90` | first sight: quest status to the players |
| Shenk (Overseer) | `0x549760` | he joined the fight |
| Izual | `0x5e4fe0` | he woke |
| BaalToStairs | `0x546dd0` | opens the Worldstone Chamber |
| Ancients, statues | `0x549e20`, `0x547200`, `0x547280` | may the rite start, portal count |

A plugin that replaces one of these thinks must keep the call, or the quest never moves on. Quest details:
[quests-acts.md](quests-acts.md).

## 12. Differences from classic 1.10

The D2R AI is classic's almost line for line. What changed, as read in build 93847:

| Area | D2R | Classic |
|-|-|-|
| Tick | debug gates, deferred state queue, pet prelude | none of the three; a state change while uninterruptable was dropped |
| Target-search kinds | 5 and 6; kind 3 new | 4 and 5 |
| Scan callback ids | 11 ids: 4-8, 9-10 | 13 ids: 5-9, 11-12 |
| AI command ids | home 7, mode at a point 5, follow unit 10 | 10, 7, 14; type 5 was a wander loop |
| Sound and message ids | boss sighting 0x11, Fallen flee 18, NPC greet 0x13; quest sounds moved further (seen: 87, 88, 90, 20) | 16, 17, 18; 80, 81, 83, 19 |
| Invisible-player roll | on the target's seed | on the searcher's seed |
| Move away | an equal axis moves `-n` | 0 (in the classic source reconstruction) |
| Dead units | never get a think event | could get one |
| Curse AI gate | also refuses uninterruptable and non-attackable units; uniques pass the getter | uniques refused when the curse was applied |
| Hireling | aip1-3 instead of class tests; buff and target-state skill filters; 8 follow directions; act5hire2 swings A2 | class ids; 7 directions |
| Fallen Shaman | a bound-demon branch before the corpse search | none |
| FoulCrowNest | the exit test uses the real y | tested x twice |
| Boss target cull | 1020 (Diablo, Baal) | 55 |
| New AIs | 148 GenericPet, the Uber rows | none |
| animdata with no event frame | `min(frames, 144)` | 0 |

Ids are not shifted by one constant: map every classic id by its name or meaning. A classic think ported as a plugin
must translate its scan ids, command ids, search kind and sound ids.

## 13. Not traced

- The pet prelude wrappers (`0x5d7e10`, `0x5d7b60`) beyond "owner gone: remove the pet", who writes control `+0x14`
  and `+0x18`, and their default values.
- The writer of the minion leash owner (control +0x3c) and of type flags 0x200 / 0x400.
- The target override path (`0x598010`, `0x598130`, which has one more draw) and what the game links into node lists 8
  and 9 (used by the evil search and by the scored pick).
- The first think after a spawn or a room activation: the ten callers of `0x542440` are listed, not mapped (classic:
  2 frames after a client enters the room).
- The D2R get-hit and knockback "last mode" rule (3 / 13 / 19) is classic's; the D2R code was not re-read.
- The order of think events of different units inside one frame.
- Draws inside the spawn-spot search of the heal-teleport, and which units besides mod 26 carry AI flag 0x20.
- Special states 2, 9 and 14: who sets them. How the curse states are entered and left by the skill code, and whether
  scan id 3 is classic's 3 or 4.
- Hurt branches of the Act I AIs were read but not seen live (the test scenes kept monsters at full life). No boss
  think of section 11 was replayed against live logs. The owned hireling's Combat and skill choice were not captured.
- Baal's target score and the D2RCore threat bonus (state 231): who adds that state.
- The Ancient statue's spawner class (control +0x80), Talic's whirlwind mode-change byte, Uber Mephisto's think address.
- Weapon class of monsters with several (it picks the animdata record), sequence (monseq) frame counts, and the
  per-mode animation speed branches: these decide the frame of the next think.
- The D2RCore aura path behind its own flag (likely Terror Zones), and what `0x4ff3b0` returns for a pet's owner.
