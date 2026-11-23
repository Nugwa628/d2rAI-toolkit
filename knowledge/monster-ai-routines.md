# Monster AI routines: what each AI does and what aip1 to aip8 mean

A per-AI reference for picking a monstats `AI` for a monster and setting its `aip1` to `aip8`. It covers the 130 AI
routines of monai rows 1 to 100 and 113 to 142, in monai order, as observed in **D2R 3.3 build 93847**; every address
is an RVA for **build 93847 only** (image base `0x140000000`) and must be re-checked on any other build
([engine-re.md](engine-re.md)). Behaviour and parameter meanings come from a community unit AI compendium that was
re-derived from the 3.3 server code, cross-checked against our own disassembly notes. Each AI carries a confidence
mark that says how far the two agree. The player summons of rows 101 to 112 and the rows from 143 on are not here:
see [monster-ai.md](monster-ai.md) section 6 and [monsters-spawn.md](monsters-spawn.md) section 6.

Related: the tick, target search, movement tactics and AI commands in [monster-ai.md](monster-ai.md); the AI table,
the aip layout and the AI columns in [monsters-spawn.md](monsters-spawn.md); mercenaries in
[hirelings.md](hirelings.md); monster skills and missiles in [skills-missiles.md](skills-missiles.md); the table
columns in [tables.md](tables.md).

## 1. Reading this page

**Confidence marks**

| Mark | Meaning |
|-|-|
| **confirmed** | the compendium and our own disassembly notes agree (the Act I AIs were also replayed against live think logs) |
| **confirmed**, classic notes | they agree, but our notes for this AI were read in the classic 1.14d routine, not in build 93847; a D2R-only change would not show |
| **compendium only** | not in our notes; unverified |
| **differs** | the two sources disagree; both versions are given |

A think RVA is given where our notes name the build 93847 routine. Row numbers are the monai.txt rows (the monstats
`AI` column links to them by name).

**Vocabulary**

- **aipN**: monstats `aip1` to `aip8` for the current difficulty. "Not read" means the routine never looks at it.
- **Chance**: a random number from 0 to 99 is drawn and the roll succeeds when it is below the parameter. 0 never
  succeeds, 100 always does.
- **Distances** are in subtiles. **Frames**: 25 per second.
- **Modes**: A1, A2 (attacks), S1 to S4 (special animations), SC (cast), SQ (sequence), NU (neutral).
- **SK1 to SK8**: monstats `Skill1` to `Skill8`, cast in their `Sk1mode` to `Sk8mode` unless the text names a mode.
- **Melee range**: the engine's own "in melee range of the target" flag of the tick.
- **Approach**: walk to the target with path finding. **Run to**: the same in run mode.
- **Circle walk N**: a short side path of N steps around the target.
- **Wander N**: walk to a random point N subtiles from itself.
- **Move near N**: walk to a random point N subtiles from the target.
- **Straight step N**: walk up to N subtiles in a straight line toward the target, no path finding.
- **Flee N**: walk to a point N subtiles farther from the target on each axis. **Run away N**: the same in run mode.
- **Idle N**: go to neutral mode and think again N frames later. **Delay N**: think again N frames later without
  interrupting the current mode.
- **Speed bonus X%**: a velocity bonus for the next move only.
- **Just hit**: the unit's previous mode was a get-hit (any hit, melee or ranged; see "last mode" in
  [monster-ai.md](monster-ai.md) section 1).
- **Tick**: one run of the AI routine. Some AIs count ticks, not frames; the time between ticks depends on the last
  idle or delay and on the length of the current animation.

**Rule:** an idle issued right after a move order in the same tick cancels that move. A few routines lose a move
this way; the text says so where it matters.

## 2. The AIs

### Idle (monai row 1, think `0x4a3680`)

Confidence: **confirmed**. Does nothing: while it is in a room it idles 200 frames at a time. No parameters.

### Skeleton (monai row 2, think `0x5b43a0`)

Confidence: **confirmed**. Outside melee range it approaches on aip1; in melee range it attacks on aip3 (A1 on aip4,
else A2). Any failed roll idles aip2 frames.

| aip | Meaning |
|-|-|
| 1 | chance to approach (outside melee range) |
| 2 | idle frames after a failed roll |
| 3 | chance to attack (in melee range) |
| 4 | chance of A1 rather than A2 |

### Zombie (monai row 3, think `0x5ba780`)

Confidence: **confirmed**. In melee range it always attacks (A1 on aip4, else A2). Outside it runs at the target
(run mode, speed bonus 100%) when it was just hit, or the target is within aip2 and aip1 succeeds, or it stands in
the Burial Grounds (level 17); otherwise it wanders 3.

| aip | Meaning |
|-|-|
| 1 | chance to run at the target (target within aip2) |
| 2 | awareness distance |
| 3 | not read |
| 4 | chance of A1 rather than A2 |

### Bighead (monai row 4, think `0x5ac6e0`)

Confidence: **confirmed**. A2 is its ranged attack. Outside melee range a bighead that was just hit answers with A2.

- Life at or above aip1 %: A1 in melee range. Outside it shoots A2 on aip3 when a target is found closer than 15,
  else it approaches.
- Life below aip1 %: closer than 3 it flees 5 at speed bonus 50% (A2 when the flee fails). Farther than 15 it walks
  to within 6. In between it shoots A2 on aip4, else circle walks 3 on aip2, else idles 10.

| aip | Meaning |
|-|-|
| 1 | life % at or above which it counts as healthy |
| 2 | chance to circle walk 3 (hurt, aip4 failed) |
| 3 | chance to shoot A2 rather than approach (healthy) |
| 4 | chance to shoot A2 (hurt) |

### BloodHawk (monai row 5, think `0x5aca20`)

Confidence: **confirmed**.

- Outside melee range: on aip1 it charges (approach with a speed bonus of aip5 %). The first melee tick after a
  charge attacks with A1 without a roll.
- In melee range: A1 on aip3. When that fails, or when the charge roll failed with the target 3 or closer, it backs
  off: flee 4 with a speed bonus of aip4 % (A1 when the flee fails).
- Otherwise it wanders 4 at speed -50% on aip2, else wanders 3.

| aip | Meaning |
|-|-|
| 1 | chance to charge (outside melee range) |
| 2 | chance of the slow wander 4 rather than wander 3 |
| 3 | chance to attack (in melee range) |
| 4 | back-off speed bonus (%) |
| 5 | charge speed bonus (%) |

### Fallen (monai row 6, think `0x5ae650`)

Confidence: **confirmed**.

- Scare: when a monster within 15 is still in its death mode, the Fallen flees 12 at speed bonus 50% (1 in 20 also
  plays its scared sound) and is marked scared. Everything below runs only in neutral mode (else idle 10).
- Just hit outside melee range: approaches.
- A pack leader with the target closer than 15 gives its minions the attack order and plays S2 (the war cry) on aip1.
- Outside melee range: approaches when the target is within aip2; else 30% wander 3, 70% idle 10.
- In melee range: attacks on aip3 (a scared Fallen attacks without the roll). A failed roll plays S2 30% of the
  time, else idle 10.
- Under an attack order: approaches; in melee range it attacks on aip3, else idle 5.
- The attack is A1 on aip4, else A2.

| aip | Meaning |
|-|-|
| 1 | chance that a leader gives the attack order |
| 2 | approach distance (the table comment calls it a wander chance; the code compares the distance) |
| 3 | chance to attack (in melee range) |
| 4 | chance of A1 rather than A2 |

### Brute (monai row 7, think `0x5acc20`)

Confidence: **confirmed**. Outside melee range it always approaches, with a speed bonus of 100 minus its life %
(life counted between 40 and 100, so 0% to 60%). In melee range it attacks on aip3 (A1 on aip4, else A2). When the
attack roll fails a second roll against aip3 picks circle walk 4, else idle 15.

| aip | Meaning |
|-|-|
| 1, 2 | not read |
| 3 | chance to attack; second roll: chance to circle walk 4 |
| 4 | chance of A1 rather than A2 |

### SandRaider (monai row 8)

Confidence: **confirmed**, classic notes.

- Charge cycle: a counter goes up every tick. At aip5 the raider plays its charge overlay (150 when aip6 is 1, else
  46), waits `aidel` + 1 frames and is charged: state 90 (aip6 = 1) or state 91. A charged raider in melee range
  uses SK1 on the target, which restarts the cycle. An unused charge runs out after aip5 + max(24 - aip5, 6) ticks,
  then idle 15.
- Below aip1 % life it walks to the nearest ally. Only failed ally searches are counted (7 end it), so a hurt raider
  with any ally around keeps following it and does not attack.
- Farther than 4 and not charged: circle walks on aip2.
- In melee range: attacks on aip3, A2 on aip7, else A1.
- Outside melee range: approaches when charged or on aip4.

| aip | Meaning |
|-|-|
| 1 | life % below which it regroups |
| 2 | chance to circle walk (farther than 4, not charged) |
| 3 | chance to attack (in melee range) |
| 4 | chance to approach (outside melee range, not charged) |
| 5 | ticks until it is charged |
| 6 | charge look: 1 = overlay 150 and state 90 (cold), other = overlay 46 and state 91 (fire) |
| 7 | chance of A2 rather than A1 |

### Wraith (monai row 9, think `0x5b9bb0`)

Confidence: **confirmed**. In melee range A1 on aip3. Outside it takes a straight step of up to 12 toward the target
on aip1. A failed roll idles aip2 frames.

| aip | Meaning |
|-|-|
| 1 | chance to step toward the target |
| 2 | idle frames after a failed roll |
| 3 | chance to attack (in melee range) |

### CorruptRogue (monai row 10, think `0x5ad430`)

Confidence: **confirmed**. With the target farther than 20 / 17 / 14 (Normal / Nightmare / Hell) it always runs at
it with a speed bonus of aip4 %, stopping 3 short. Closer: in melee range A1 on aip3, else idle aip2. Outside melee
range it engages on aip1 (else idle aip2): a run with the aip4 bonus on aip5, else a walk.

| aip | Meaning |
|-|-|
| 1 | chance to approach (outside melee range) |
| 2 | idle frames after a failed roll |
| 3 | chance to attack (in melee range) |
| 4 | run speed bonus (%) |
| 5 | chance to run rather than walk |

### Baboon (monai row 11)

Confidence: **confirmed**, classic notes.

- Normal: outside melee range it always approaches. In melee range it attacks (A1 on aip4, else A2) and marks that it
  just attacked. With the mark set it attacks again only on aip3; else circle walk 3 on aip2, else idle 15. A baboon
  just hit right after attacking circle walks 3 20% of the time.
- Escape: just hit below aip1 % life, 50% of the time it starts an escape of 2 to 6 ticks. During it the baboon gains
  life regeneration of `hpregen x aip5 / 8` and flees 15 at its run speed (bonus `100 x Run / Velocity - 100`, at
  most 120%). While escaping it attacks 33% of the time in melee range, ends the escape above 75% life, and when 24
  or more away and not hit circle walks 4 33% of the time (else idle 20).

| aip | Meaning |
|-|-|
| 1 | life % below which a hit can start the escape |
| 2 | chance to circle walk 3 (aip3 failed) |
| 3 | chance to attack again right after an attack |
| 4 | chance of A1 rather than A2 |
| 5 | regeneration bonus while escaping, in eighths of its `hpregen` |

### Goatman (monai row 12, think `0x5b0490`)

Confidence: **confirmed**. In melee range A1 on aip3; outside it approaches on aip1; a failed roll idles aip2.

| aip | Meaning |
|-|-|
| 1 | chance to approach (outside melee range) |
| 2 | idle frames after a failed roll |
| 3 | chance to attack (in melee range) |

### FallenShaman (monai row 13, think `0x5b3fd0`)

Confidence: **confirmed**. The order roll against aip1 is drawn every tick, corpses or not.

- In melee range: A1 on aip3.
- Raise: on a second aip1 roll, when SK1 can be placed on a corpse within aip4, it uses SK1 on it. A unique shaman
  that is not a champion raises dead Fallen and Fallen Shamans (BaseId 19 and 58) that are neither unique nor
  champion, and takes the last match of the scan. Any other shaman raises the dead minions of its own pack. A
  player-allied shaman (bound demon) searches only within 20.
- SK2 at the target when it is closer than aip5 and aip2 succeeds (the nearest other enemy is tried the same way).
- Otherwise circle walk 3 on aip3, else idle 10.

| aip | Meaning |
|-|-|
| 1 | chance to give the attack order; chance to raise with SK1 |
| 2 | chance to use SK2 |
| 3 | chance to attack (in melee range); chance to circle walk 3 |
| 4 | corpse search distance |
| 5 | SK2 range |

### QuillRat (monai row 14, think `0x5b2d50`)

Confidence: **confirmed**.

- With an attack order from its leader it shoots A2 and clears the order.
- In melee range it always uses A1. Just hit: A2.
- Target closer than aip1: A2 on aip2, else flee aip4 (when the flee fails: A2 if the target is closer than 4, else
  wander).
- Otherwise it wanders max(aip4, 3).

| aip | Meaning |
|-|-|
| 1 | distance inside which it shoots or flees; beyond it only wanders |
| 2 | chance to shoot A2 |
| 3 | not read |
| 4 | flee distance and wander radius (the wander uses at least 3) |

### SandMaggot (monai row 15)

Confidence: **confirmed**, classic notes. The maggot is up, preparing an egg, or burrowed.

- It burrows with SK2 (no target) when no enemy is within 10 and its aip5 timer has run out, or 20% of the time when
  below 25% life with an enemy within 7.
- Burrowed, it comes up with SK1 at a target within 16 once aip5 frames have passed (idle 20 between checks).
- Up: in melee range A1 on aip4. A target closer than 15 is shot with A2 on aip2. Otherwise 20% circle walk 6.
- Eggs: with fewer than aip3 eggs laid and aip1 succeeding, it first circle walks 6 and on the next tick lays an egg
  with SK3 at the target. Otherwise idle 12.

| aip | Meaning |
|-|-|
| 1 | chance to lay an egg |
| 2 | chance to shoot A2 |
| 3 | most eggs it lays |
| 4 | chance to attack with A1 (in melee range) |
| 5 | frames between burrowing and coming up, and back |

### ClawViper (monai row 16)

Confidence: **confirmed**, classic notes.

- In melee range: attacks on aip3 (A1 on aip4, else A2), else idle aip5.
- SK1 (the charge) when the target is within aip2, aip1 succeeds and SK1 can be placed at the target. With aip6 set
  the viper glows while charging: state 91 (red) for 2, state 90 (blue) for any other non-zero value. The glow goes
  on its next tick.
- Otherwise 50% approach, 50% idle aip5.

| aip | Meaning |
|-|-|
| 1 | chance to charge with SK1 |
| 2 | charge range |
| 3 | chance to attack (in melee range) |
| 4 | chance of A1 rather than A2 |
| 5 | idle frames |
| 6 | charge glow: 0 none, 2 red (state 91), other blue (state 90) |

### SandLeaper (monai row 17)

Confidence: **confirmed**, classic notes.

- Leaps with SK1 when the target is closer than 5, aip1 succeeds and the skill can be used.
- In melee range: A2 on aip2 (it never uses A1), else idle 10.
- Outside melee range: farther than 10 it moves near the target (5) at speed bonus 75%. Closer it approaches on aip3,
  else circle walks 4 on aip4, else idle 10.

| aip | Meaning |
|-|-|
| 1 | chance to leap with SK1 |
| 2 | chance to attack with A2 (in melee range) |
| 3 | chance to approach (target within 10) |
| 4 | chance to circle walk 4 (aip3 failed) |

### PantherWoman (monai row 18)

Confidence: **confirmed**, classic notes.

- In melee range: A1 on aip2, else idle aip4.
- Outside: approaches at speed bonus 75% on aip1. Otherwise, when the nearest ally of the same BaseId is farther
  than aip3, she walks to it at 75%. Otherwise 25% circle walk 3, else idle aip4.

| aip | Meaning |
|-|-|
| 1 | chance to approach (outside melee range) |
| 2 | chance to attack (in melee range) |
| 3 | most distance to the nearest ally of her type |
| 4 | idle frames |

### Swarm (monai row 19, think `0x5b0490`)

Confidence: **confirmed**. The Goatman routine (the same think): A1 on aip3 in melee range, approach on aip1 outside
it, idle aip2 on a failed roll.

| aip | Meaning |
|-|-|
| 1 | chance to approach (outside melee range) |
| 2 | idle frames after a failed roll |
| 3 | chance to attack (in melee range) |

### Scarab (monai row 20)

Confidence: **confirmed**, classic notes.

- A pack leader with the target within 20 gives its pack (and itself) a raid order on aip5.
- Under the raid order: in melee range it uses SK1 on the target and drops the order; else it approaches at speed
  bonus 100%.
- In melee range: acts on aip1 (else idle aip3): SK1 on aip4, else A1 on aip2, else A2.
- Outside melee range it alternates circle walks and approaches and never idles.

| aip | Meaning |
|-|-|
| 1 | chance to act (in melee range) |
| 2 | chance of A1 rather than A2 |
| 3 | idle frames (aip1 failed) |
| 4 | chance to use SK1 (in melee range) |
| 5 | chance that a leader gives the raid order |

### Mummy (monai row 21)

Confidence: **confirmed**, classic notes.

- Just hit outside melee range: approaches.
- Target within aip1: outside melee range it always approaches. In melee range it attacks on aip3 (A1 on aip4, else
  A2); when aip3 fails it approaches again and thinks next after aip5 frames.
- Target beyond aip1: wanders 3 on aip2, else idle aip5.

| aip | Meaning |
|-|-|
| 1 | awareness distance |
| 2 | chance to wander 3 (target beyond aip1) |
| 3 | chance to attack (in melee range) |
| 4 | chance of A1 rather than A2 |
| 5 | idle frames |

### GreaterMummy (monai row 22, think `0x5b05a0`)

Confidence: **confirmed**, classic notes.

- In melee range: A1 on aip1. Target within 5: A2 on aip1.
- It looks for undead allies within aip5. In this order: heals a hurt one with SK2 (aip3), raises a dead one with
  SK1 (aip2), casts SK3 at the target (aip4).
- When allies were found it idles 6 or circle walks 3 (50/50). Otherwise it approaches to within 3 at speed bonus
  50%.
- Radament (class 229) has his own ally rules (both undead kinds, uniques only in Normal) and searches aip5 + 10.

| aip | Meaning |
|-|-|
| 1 | chance to attack (A1 in melee range, A2 within 5) |
| 2 | chance to raise a dead ally with SK1 |
| 3 | chance to heal a hurt ally with SK2 |
| 4 | chance to cast SK3 at the target |
| 5 | ally and corpse search distance |

### Vulture (monai row 23)

Confidence: **confirmed**, classic notes. It flies to points around its target. A unit within 11 at or below aip3 %
life is preferred as target.

- In melee range: A1 on aip1, else idle aip2.
- Outside: it repositions on aip5 (always on its first tick): circle walk 6 on aip4, else a straight step of 9
  toward the target, then delay 12. Otherwise idle aip2.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames |
| 3 | life % at or below which a nearby unit is preferred as target |
| 4 | chance to circle walk 6 rather than step toward the target |
| 5 | chance to reposition (outside melee range) |

### Mosquito (monai row 24)

Confidence: **confirmed**, classic notes. It cycles through approach (speed bonus 100%), retreat (flee 10 at 100%)
and wander (wander 4 at 50% for aip5 ticks, then approach again). In melee range the first strike needs no roll;
later strikes need aip3. A strike is SK1 on aip4, else A1. When aip3 fails it idles 15 80% of the time and switches
to the retreat 20% of the time.

| aip | Meaning |
|-|-|
| 1, 2 | not read |
| 3 | chance to strike again (in melee range) |
| 4 | chance of SK1 rather than A1 |
| 5 | wander legs after a retreat |

### WillOWisp (monai row 25)

Confidence: **confirmed**, classic notes.

- While moving, after a few moves it vanishes with S1: always in melee range, else on aip1. Each move is an approach
  on aip3, else wander 6.
- After vanishing: in melee range it attacks with A1 (after vanishing outside melee range this needs aip2); outside
  melee range it casts SK1 (in SC) at the target. Then three more moves (approach on aip3, else wander 4).
- Hidden dance: with a target within 32, each tick has a (difficulty + 3) in 1000 chance to start a formation. It
  runs only when exactly 4 or 5 wisps (BaseId 118) are within 32. They move around the target every 67 frames; if
  the target is still a living player at the end it gets `item_magicbonus` +50 x (difficulty + 1) for 1,728,000
  frames. The formation then has a cooldown of 1800 frames.

| aip | Meaning |
|-|-|
| 1 | chance to vanish (outside melee range) |
| 2 | chance to attack with A1 after reappearing in melee range |
| 3 | chance to approach rather than wander |

### Arach (monai row 26, think `0x5b4980`)

Confidence: **confirmed**.

- Outside melee range: just hit, or already closing in, it approaches. Otherwise the engage roll (aip3) is drawn
  once per 21 idle ticks; without it 20% wander 6, else idle 15.
- In melee range: A1 on aip1. When that fails and life is at or above aip5 %: circle walk 4 on aip2, else idle 15.
  Below aip5 % it turns to flight: SK1 (no target) when it is not in state 22 (spiderlay), else flee 8.
- In flight: above 75% life it stops and approaches (aip3) or circle walks 6. In melee range it attacks on
  aip1 - 25. While the target is closer than aip4 or it is hit it keeps fleeing 4; else it stops and circle walks 12.

| aip | Meaning |
|-|-|
| 1 | chance to attack (aip1 - 25 while in flight) |
| 2 | chance to circle walk 4 (aip1 failed) |
| 3 | chance to engage |
| 4 | distance inside which it keeps fleeing |
| 5 | life % below which it takes flight |

### ThornHulk (monai row 27)

Confidence: **confirmed**, classic notes. Outside melee range it always approaches. In melee range it acts on aip1;
else circle walk 4 on aip3, else idle 15.

- When it acts and owes no plain attacks, aip4 starts a frenzy: SK1 in A2 mode for aip6 attacks, each followed by
  aip5 more frames before the next think. After a frenzy the next 3 attacks are plain ones.
- A plain attack is A2 on aip2, else A1.

| aip | Meaning |
|-|-|
| 1 | chance to act (in melee range) |
| 2 | chance of A2 rather than A1 |
| 3 | chance to circle walk 4 (aip1 failed) |
| 4 | chance to start a frenzy |
| 5 | extra frames after each frenzy attack |
| 6 | frenzy length (attacks) |

### Vampire (monai row 28, think `0x5b7590`)

Confidence: **confirmed**. aip5 is a bit mask of what it may cast: 1 = SK1 or SK4 (50/50, only at a target within
20), 2 = SK2, 4 = SK3. SK2 and SK3 each need aip4 and share a cooldown of 11 ticks.

- Just hit: it engages. In melee range it then uses SK1 or SK4 30% of the time (bit 1), else A1.
- Below 33% life it turns to flight and flees 8 (with its run speed bonus, at most 120%) until it is back at 75%.
- In melee range: A1 on aip1 (30% SK1 or SK4 instead with bit 1 and the target within 20); else 33% circle walk 4,
  else idle 10.
- Outside melee range, at aip3 or farther: approaches if engaged, else idle 15. Closer than aip3: casts on aip2
  (SK2, SK3 or SK1/SK4 as aip5 allows). Otherwise it approaches when farther than 20, flees 8 50% of the time when
  closer than 9, and circle walks 4 or idles 10 (50/50).

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to cast (target within aip3) |
| 3 | active distance |
| 4 | chance to use SK2 or SK3 |
| 5 | cast mask: 1 = SK1/SK4, 2 = SK2, 4 = SK3 |

### BatDemon (monai row 29)

Confidence: **confirmed**, classic notes. It rests hanging (S3, then S4) and gains life regeneration of
`hpregen x aip5 / 8` while resting. After at least two resting ticks it takes off (S2) when the target is in melee
range or within 7, when it was just hit, or when the target is within 14 and it is above 50% life.

- In flight the first strike is A2. Just hit: flee 12 on aip2. In melee range it attacks on aip3 (A2 on aip4, else
  A1), else idle 10. Outside melee range it approaches.
- Below aip1 % life it flees 15 and rests again.

| aip | Meaning |
|-|-|
| 1 | life % below which it flees and rests |
| 2 | chance to flee 12 when just hit |
| 3 | chance to attack (in melee range) |
| 4 | chance of A2 rather than A1 |
| 5 | regeneration bonus while resting, in eighths of its `hpregen` |

### Fetish (monai row 30, think `0x5aec30`)

Confidence: **confirmed**.

- It obeys its leader's orders (attack, or come to a unit): it walks to the named unit at speed bonus 50%.
- Outside melee range it approaches at 50%. In melee range A1 on aip1, else idle aip2.
- After more than aip3 ticks of its attack loop, if the target's life is above aip4 %, it flees 14 at 50%. It keeps
  fleeing while the target is within 12, then 20% circle walk 4 (else idle 10) and back to the fight.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames (aip1 failed) |
| 3 | ticks of the attack loop before it may run off |
| 4 | target life %: it runs off only while the target is above it |

### NpcOutOfTown (monai row 31, think `0x5c0db0`)

Confidence: **confirmed**. Used by Deckard Cain in Tristram and by Anya in the Frozen River (class 527, driven by
the Act V quest code). When its quest event fires it walks to a scripted point (up to 8 steps, 20 or 40 frames
apart), opens its portal and leaves (life set to 0, death mode). No parameters.

### Npc (monai row 32, think `0x5c1e80`)

Confidence: **confirmed**. The town NPC AI. Classes 201, 254, 255, 265 and 512 have their own quest branches
(scripted walks and portals). Every other NPC runs the shared town routines (turning to nearby players, AI commands,
walking its preset path) and idles 8 between them. No parameters.

### HellMeteor (monai row 33)

Confidence: **confirmed**, classic notes. It needs no target. With an SK1, on aip1 it casts SK1 at a random point
within aip3 of itself on each axis; otherwise it idles aip2 frames.

| aip | Meaning |
|-|-|
| 1 | chance to cast SK1 |
| 2 | idle frames (aip1 failed) |
| 3 | distance around itself for the aim point |

### Andariel (monai row 34, think `0x5aba70`)

Confidence: **confirmed**.

- In melee range: SK1 at the target on aip1, else A1.
- Outside: on aip2 she idles 5 frames. Otherwise she casts on aip3 (SK1 on aip4, else SK2) and approaches when aip3
  fails.

| aip | Meaning |
|-|-|
| 1 | chance to use SK1 (in melee range) |
| 2 | chance to idle 5 frames (outside melee range) |
| 3 | chance to cast rather than approach |
| 4 | chance of SK1 rather than SK2 |

### CorruptArcher (monai row 35, think `0x5ad600`)

Confidence: **confirmed**.

- No target: 50% circle walk 3, else idle aip3.
- Just hit outside melee range: A1 at the target.
- Target closer than 6: runs away 12 at speed bonus 100% on aip4.
- aip8 above 0 and the target farther than aip8: walks to within aip8 at speed bonus 10% on aip1.
- Target farther than aip5: runs to within aip5 at 100%.
- Otherwise it shoots on aip2 (else idle aip3): SK2 on aip6, SK3 on aip7, SK1 when it has one, else A1.

| aip | Meaning |
|-|-|
| 1 | chance to approach (target beyond aip8) |
| 2 | chance to shoot |
| 3 | idle frames |
| 4 | chance to run away (target closer than 6) |
| 5 | shooting distance: it runs closer when the target is beyond it |
| 6 | chance to use SK2 |
| 7 | chance to use SK3 |
| 8 | walk-toward distance, 0 = off |

### CorruptLancer (monai row 36, think `0x5ad9e0`)

Confidence: **confirmed**.

- Target farther than aip5: runs at it (speed bonus 100%) with monstats2 `MeleeRng` steps. The first melee tick after
  such a run attacks without a roll.
- Outside melee range: approaches on aip1, running at 100% on aip4, else walking to within 3. Idle aip3 when aip1
  fails.
- In melee range: attacks on aip2, else idle aip3. The attack is SK1 (aip6), SK2 (aip7), SK3 (aip8) or A1.

| aip | Meaning |
|-|-|
| 1 | chance to approach (outside melee range) |
| 2 | chance to attack (in melee range) |
| 3 | idle frames |
| 4 | chance to run rather than walk |
| 5 | distance beyond which it always runs in |
| 6 | chance to use SK1 |
| 7 | chance to use SK2 |
| 8 | chance to use SK3 |

### SkeletonBow (monai row 37, think `0x5b44f0`)

Confidence: **confirmed**.

- Just hit: A1 at the nearest target.
- Target closer than 20: A1 on aip1; else 20% circle walk 3, else idle aip2.
- Target farther: on aip3 a straight step toward it, up to aip4 subtiles and stopping aip5 short. Otherwise idle 20.

| aip | Meaning |
|-|-|
| 1 | chance to shoot (target within 20) |
| 2 | idle frames |
| 3 | chance to step closer (target beyond 20) |
| 4 | step length |
| 5 | distance kept from the target |

### MaggotLarva (monai row 38)

Confidence: **confirmed**, classic notes. In melee range it attacks with A1 on aip1 and never twice in a row: the
tick after an attack only idles aip2. Outside melee range it approaches on aip3, else idle aip4.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames after every attack and when aip1 fails |
| 3 | chance to approach (outside melee range) |
| 4 | idle frames (aip3 failed) |

### PinHead (monai row 39)

Confidence: **confirmed**, classic notes. The first tick in melee range always attacks; after that an attack needs
aip1 (else idle aip2). The attack is SK1 on aip5, SK2 on aip6, else A1. Outside melee range it approaches on aip3,
else idle aip4.

| aip | Meaning |
|-|-|
| 1 | chance to attack again (in melee range) |
| 2 | idle frames (aip1 failed) |
| 3 | chance to approach (outside melee range) |
| 4 | idle frames (aip3 failed) |
| 5 | chance to use SK1 |
| 6 | chance to use SK2 |

### MaggotEgg (monai row 40)

Confidence: **confirmed**, classic notes. The egg hatches once, with SK1 at the target, on aip2. After hatching it
removes itself (the kill goes to its target). It idles aip1 frames between checks.

| aip | Meaning |
|-|-|
| 1 | idle frames between checks |
| 2 | chance to hatch (SK1) |

### Towner (monai row 41, think `0x5c4650`)

Confidence: **confirmed**. Runs the shared town NPC routines (AI commands, preset path) and idles 12 between them.
No parameters.

### Vendor (monai row 42, think `0x5d05a0`)

Confidence: **confirmed**. Plays S1 in place 20% of the time, else idles 30. No range check. No parameters.

### FoulCrowNest (monai row 43, init `0x5b28e0`, think `0x5add80`)

Confidence: **confirmed**. Active only while its target is within 20 (else idle 25). When at least aip1 frames have
passed since its last try and its spawn spot (3 subtiles below it) is free, it uses SK1 at the target and counts
it. After aip3 uses it plays its death animation and stops. Between checks it idles 20 to 29 frames.

| aip | Meaning |
|-|-|
| 1 | frames between uses of SK1 |
| 3 | uses before it dies |

### Duriel (monai row 44, think `0x5adfc0`)

Confidence: **confirmed**, classic notes. He keeps SK4 as an aura at skill level aip1 (the level is set, not added)
and puts it back whenever no aura is active.

- In melee range: SK3 on aip2, SK2 on aip3, else A2 on aip4, else A1.
- Outside: SK1 on aip5, else he approaches.

| aip | Meaning |
|-|-|
| 1 | skill level of the SK4 aura |
| 2 | chance to use SK3 (in melee range) |
| 3 | chance to use SK2 (in melee range) |
| 4 | chance of A2 rather than A1 |
| 5 | chance to use SK1 (outside melee range) |

### Sarcophagus (monai row 45)

Confidence: **confirmed**, classic notes (1.14d init `0x5f6630`, think `0x5f6a10`). Works like the FoulCrowNest with
three differences: its range is 25, its use timer only restarts after a successful spawn, and it dies once its uses
exceed aip3, so it spawns aip3 + 1 times.

- A target past 25: idle 25. More than aip3 spawns (strict test): no treasure, then death mode.
- aip1 frames since the last spawn (either way round) and an SK1: the spot test is a small unit's cross two subtiles
  south of the sarcophagus, in a room and clear of mask 0x3c01. Free: one more spawn, the timer restarts, SK1 (its
  Nest) at the target. Busy: the timer is kept.
- Otherwise one raw draw: idle 20 + draw % 10.
- No levels.txt list names it (monstats 228): the Act II tombs' presets place it.

| aip | Meaning |
|-|-|
| 1 | frames between uses of SK1 |
| 3 | use count (dies after aip3 + 1 uses) |

### ElementalBeast (monai row 46)

Confidence: **compendium only**. It idles aip3 frames until the target comes within aip2 or it is hit; then it plays
S1 at the target. From then on it approaches on aip1, else wanders 8. Once in melee range it plays S1 again and dies
on the next tick, the kill credited to its target. It never uses A1.

| aip | Meaning |
|-|-|
| 1 | chance to approach |
| 2 | awareness distance |
| 3 | idle frames while inactive |

### FlyingScimitar (monai row 47)

Confidence: **confirmed**, classic notes (1.14d think `0x5f6ca0`).

- In melee range: A1 on aip2, else circle walk 2 on aip4, else idle aip3.
- Outside: on aip1 a straight step of up to 8 at the target. Otherwise a second aip1 roll makes it approach
  normally; idle aip3 when that fails too.
- Monstats 234 is what a chest trap of type 8 or 9 releases in a level whose monsters give no other trap class (Act
  II on); no levels.txt list names it.
- Its Charge (skill 107) uses seq_pinheadsmite, whose first row has no event. The attack frame index (1.14d
  `0x5cf8c0`) reads only the sequence's first row: 7 when it has an event, else -1. So the scimitar's charge run loops
  on every frame once Charge's do first runs, where Duriel, the hordes, the siege beasts and the vipers (whose
  sequences start with an event) loop every 7 frames. See [skills-missiles.md](skills-missiles.md) section 10.

| aip | Meaning |
|-|-|
| 1 | chance to dash; second roll: chance to approach |
| 2 | chance to attack (in melee range) |
| 3 | idle frames |
| 4 | chance to circle walk 2 (aip2 failed) |

### ZakarumZealot (monai row 48)

Confidence: **confirmed**, classic notes (the Act III quest rule below is the compendium's alone).

- In Act III, when its target (or the target's owner) is a player with quest record 21 set, the zealot runs away 8
  (wanders 6 if that fails).
- Just hit below aip3 % life: runs away 8 at its run speed. If the run does not start it waits 5 ticks before
  trying again.
- In melee range the first strike needs no roll; later strikes need aip1 (else 80% idle 10, 20% circle walk 4). A
  strike is A2 on aip2, else A1.
- Outside melee range it runs at the target on aip4, else walks.

| aip | Meaning |
|-|-|
| 1 | chance to attack again (in melee range) |
| 2 | chance of A2 rather than A1 |
| 3 | life % below which a hit makes it run away |
| 4 | chance to run rather than walk (outside melee range) |

### ZakarumPriest (monai row 49)

Confidence: **confirmed**, classic notes.

- In melee range or just hit, below 33% life: teleports with SK3 to a point past the target (four times farther when
  in melee range). Cooldown 4 x aip5 frames.
- In melee range: A1 on aip1.
- Heals with SK1 (25% chance) the most hurt other priest or zealot (BaseId 235 or 238) within aip6 that is at or
  below 60% life. It never heals itself.
- With a clear line to the target it casts on aip4: SK4 (aip2, cooldown aip5 frames) or SK2 (aip3, cooldown 20
  frames). Without a clear line only SK4 is tried, needing aip1 and aip2.
- Otherwise 30% circle walk 4, else idle 20.
- The teleport is SK3 = MonTeleport. Its spot test (1.14d `0x5fd470`): a room out of town, nothing of mask 0x3c01
  under the caster's collision pattern (a plus for size 1 or 2, a 3 x 3 box for size 3) and a clear line of mask
  0xc01. A refused spot spends the cooldown and the routine goes on. "Just hit" here is the AI state 3 or 19 (1.14d
  `0x5dd2b0`). The heal takes the **last** matching priest or zealot in scan order (1.14d callback `0x5f7240`).

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range); first roll for SK4 without a clear line |
| 2 | chance to use SK4 |
| 3 | chance to use SK2 |
| 4 | chance to cast (clear line to the target) |
| 5 | SK4 cooldown in frames; the teleport cooldown is 4 x aip5 |
| 6 | heal distance |

### Mephisto (monai row 50, think `0x5b1ca0`)

Confidence: **confirmed** for aip1; aip2 and aip3 **settled** in favour of our notes (they were **differs**). His
desperation is d = (100 - life %) / 5, 0 to 20.

- In melee range: a skill burst starts on aip1 (our notes read the test as roll at or below aip1). Otherwise a roll
  of d + 80 or more gives a running circle walk 3 at speed bonus 50%, a roll of 80 - d or more casts SK3 at the
  target, and lower rolls use A1.
- Target farther than 20: moves near it (6) at 50%. If that fails a straight step toward it, and wander 12 as the
  last resort.
- Otherwise: at 20% life or less with the target closer than 5 he flees 8 at 50% 40% of the time. A roll below
  d + 50 starts a burst. Otherwise 65% circle walk 4 (or a walk to within 4 when close), else idle 10.
- A burst is 3 to 5 casts; its first tick is a running circle walk. The skill is picked by slices of 100 / N, N
  being the number of filled skill slots. In Nightmare and Hell he uses SK6 when the line to the target is blocked
  or it is farther than 30, and SK5 when the target is within 15 and the roll falls in the first slice. Then SK4 for
  the next slice, SK2 for the third and SK1 for the rest. Without skills he wanders 6.

The two sources differed on the parameters. The compendium says only aip1 is read. Our notes (read in build 93847)
say his own target search (`0x5b2370`) also reads aip2 and aip3. Among the hostile, living units within 32 (squared
distance 1024) it keeps the one with the fewest hit points and the closest player; on aip2 the burst aims at the
weakest unit, else on aip3 at the closest player, else at the tick's target.

**Settled** (from the 1.14d routine and the 93847 one): both builds read aip2 and aip3. The 1.14d alternate-target
pick (`0x5f77c0`, 1.14d) rolls under aip2 for the weakest unit of its scan (`0x5f7700`, 1.14d), then under aip3 for the
closest player. In 1.14d the aip3 roll never comes, because the scan tests Mephisto's own type for "player"; D2R tests
the scanned unit, so the aip3 branch is live. The compendium's "only aip1" is wrong for both builds. Not checked in the
game.

| aip | Meaning |
|-|-|
| 1 | chance to start a skill burst (in melee range) |
| 2 | chance that a burst aims at the weakest enemy in reach |
| 3 | chance that it aims at the closest player instead (live in D2R only) |

### Diablo (monai row 51, think `0x5c8d40`, alt `0x5a8f00`)

Confidence: **confirmed** for the chooser, the home distances and the portal rule; the single weights below are the
compendium's and were not compared one by one. No parameters. He picks his own target among the players and keeps a
home point (where he first thought). Every tick a weighted chooser picks one action.

Actions: approach at speed bonus 20%, A1, A2, S4 at the target, SK1 (held over the next ticks), SK3, SK2, SK4, SK7,
SK5, circle walk 4, SK6, walk home at 50%, SK7 on the target's town portal, wander 5 at 20%, or idle 12 / 8 / 4
frames (Normal / Nightmare / Hell). SK8 is a buff on himself, cast whenever its state is missing.

| Situation | Weights |
|-|-|
| No target | wanders when stuck, plays S4 once in 1000 ticks, else idles |
| In melee range | A1 40 (50 when the target is below 20% life), A2 70, SK1 40, SK3 24, SK2 40, SK4 15 |
| Outside, line of sight | SK1 25, SK3 25, SK4 15, SK7 20, SK5 10 (20 beyond 25 subtiles), circle walk 20 |
| Outside, no line of sight | S4 5, SK3 25, SK4 25, SK7 40, circle walk 25 |

- SK3 in melee range becomes 14 when the target's fire resistance is above its lightning resistance and 34 when it
  is below; at range the same test moves it by 10.
- A chilled target (state 11) removes SK2 and SK4 in melee range; no line of sight removes SK1 and SK3 there.
- SK6 only gets a weight (15 to 25) when the target has Meteor or Blizzard, Fire Wall above level 3 or Immolation
  Arrow above level 7 selected on either mouse button.
- More than 105 subtiles from his home point he walks home (weight 60).
- SK7 on a town portal is chosen only when the portal is in the Chaos Sanctuary within 85 of his home and skill 199
  can be placed on it; when skill 199 can be placed on the target itself, the plain SK7 action is dropped.

### FrogDemon (monai row 52)

Confidence: **confirmed**, classic notes. It is submerged, emerging or up.

- First tick: with SK1 and the target farther than 12 it submerges (SK1 at the target); otherwise it emerges with
  SK2 on itself, or just comes up.
- Submerged: emerges with SK2 when the target is closer than aip8, or within 20 after more than 64 hidden ticks.
  Delay 24 between checks.
- Up, in melee range: A2 on aip2, else A1 on aip1, else circle walk 3 on aip3, else delay aip7.
- Up, outside melee range: at aip6 or farther it circle walks 3 on aip4, else walks to within 4. Closer than aip6 it
  shoots A2 on aip5, else circle walks 4 on aip4, else waits aip7 frames.
- Without a target it waits 32. Its event handler submerges it again with SK1 while it is up.

| aip | Meaning |
|-|-|
| 1 | chance to use A1 (in melee range, aip2 failed) |
| 2 | chance to use A2 (in melee range) |
| 3 | chance to circle walk 3 (in melee range, aip1 failed) |
| 4 | chance to circle walk (outside melee range) |
| 5 | chance to shoot A2 (closer than aip6) |
| 6 | shooting distance |
| 7 | wait frames |
| 8 | distance at which it emerges |

### Summoner (monai row 53, think `0x5b4d50`)

Confidence: **confirmed**; aip6 **settled** in favour of the compendium (it was **differs**). On his first tick he
runs a one-time quest update. Then:

- aip1 is the chance to use a skill at all; when it fails he wanders 4.
- SK5 comes first on aip2.
- He compares the target's fire resistance with its cold resistance and prefers the side the target resists less.
  The roll against aip3 keeps that side when it is at or below aip3 (so aip3 + 1 %), else he swaps sides.
- Cold side: SK2 when the target is closer than aip7 and its timer allows (timer aip4 frames), then SK1 on the
  nearest enemy when the target is closer than aip8.
- Then, for both sides: SK4 when its timer allows (timer aip5 frames, no range limit), SK3 on the nearest enemy
  within aip8, SK2 for the fire side (same rules as above), and at last SK5.

The two sources differ on aip6. Both read the same order of steps: with the target closer than 5, aip6 is rolled to
flee 6, and the skill or wander order follows in the same tick.

- Compendium: that later order always replaces the flee, so aip6 has no effect in practice.
- Our notes: aip6 is listed as a working chance to back off; they do not record that the later order cancels it.

**Settled** (from the 1.14d routine, 1.14d `0x5f85c0`): the flee starts (a walk with offset 6, whose move start runs
the mode transition at once) and the think falls through. Every path after it ends in a cast or the wander 4, and
each runs the mode transition again, replacing the walk's mode, path target and step count. What is left of aip6 is
its draw, the walk start's side effects and the velocity overrides it set. So aip6 moves the seed but he never backs
off. The 93847 think was not re-read for this; not checked in the game.

| aip | Meaning |
|-|-|
| 1 | chance to use a skill |
| 2 | chance to use SK5 (aip1 succeeded) |
| 3 | chance to keep the side that matches the target's resistances |
| 4 | SK2 interval (frames) |
| 5 | SK4 interval (frames) |
| 6 | a roll for a flee 6 (target closer than 5) that the same tick cancels; it only spends a draw |
| 7 | SK2 range |
| 8 | SK1 and SK3 range |

### NpcStationary (monai row 54, think `0x5c2140`)

Confidence: **confirmed**. A town NPC that never walks. With the nearest player within 24 a greeting counter runs
down; at 0 it plays its greeting sound and the counter restarts at 60 ticks (idle 20 between ticks). While it or
the player is busy talking it idles 10. Classes 251 (Tyrael in Act II) and 406 (Izual's ghost) check their quest
state when no player is near and leave (death mode) once it is done. No parameters.

### Izual (monai row 55, think `0x5b0cb0`)

Confidence: **confirmed**. On his first tick he runs a quest update. He only uses SK1. After SK1 he pauses aip5
frames, and for the next aip6 ticks he must engage: in melee range he always attacks, outside he always approaches,
and SK1 is not used.

- In melee range: A1 on aip1; else SK1 on aip4, else idle `aidel` frames.
- Outside, target within 10: SK1 on aip3; else approach on aip2, else idle `aidel` frames.
- Target farther than 10: approach on aip2, else a straight step toward the target (6 subtiles, stopping 9 short).

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach |
| 3 | chance to use SK1 (target within 10, outside melee range) |
| 4 | chance to use SK1 (in melee range, aip1 failed) |
| 5 | pause after SK1 (frames) |
| 6 | forced attack ticks after SK1 |

### Tentacle (monai row 56)

Confidence: **compendium only**. SK1 submerges it and SK2 (cast on itself) makes it emerge. It depends on its pack
leader.

- Without a leader it dies. Once its leader is dead it dies 40% of the time each tick, the kill credited to the
  leader's killer.
- Submerged: after aip3 seconds it emerges when the target is in melee range or within aip6. It also emerges 5% of
  the time without a target unless its leader is in sequence mode (SQ). It waits aip5 frames between checks.
- Up: in melee range A1 on aip1, else idle aip5.
- After aip4 seconds up it submerges again when the target is beyond aip6, when the target is outside melee range
  and aip2 succeeds, or 50% of the time while its leader is in sequence mode.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to submerge with SK1 (target outside melee range, after aip4) |
| 3 | seconds submerged before it may emerge |
| 4 | seconds up before it may submerge |
| 5 | wait frames |
| 6 | distance at which it emerges |

### TentacleHead (monai row 57)

Confidence: **compendium only**. The Tentacle cycle without the leader rules and without the random 5% emerge. When
up it attacks the nearest target with A1 on aip1, with no range check; it submerges again when the target leaves
aip6. Otherwise delay aip5.

| aip | Meaning |
|-|-|
| 1 | chance to attack with A1 |
| 2 | chance to submerge with SK1 (target outside melee range, after aip4) |
| 3 | seconds submerged before it may emerge |
| 4 | seconds up before it may submerge |
| 5 | wait frames |
| 6 | distance at which it emerges |

### Navi (monai row 58, think `0x5cc0c0`)

Confidence: **confirmed**. Flavie's AI: a standing NPC that defends herself. With a player within 4 she greets them
2 times out of 3 (the NpcStationary greeting counter). Otherwise she uses A1 on any enemy closer than 25 and idles
50 when there is none. No parameters.

### BloodRaven (monai row 59, init `0x5bbb30`, think `0x5bb360`)

Confidence: **confirmed**. An archer tied to a home point (where she first thought). No parameters.

- She ignores targets farther than 45 (idle 5). When the target is 50 or more from her home, or she is more than 50
  from it, she runs home (speed bonus 100%) and keeps going until she is within 5 of it.
- Target farther than 20: she runs (100%) to a point max(distance / 2, 12) from it.
- SK1: its chance starts at 0 and grows by 3 % every tick. When it succeeds outside melee range she casts SK1 at a
  random point 5 to 19 subtiles from the target and the chance goes back to 0. She can do this 8 + 2 x difficulty
  times in all (8 / 10 / 12).
- Target farther than 5: 5% run to a point 12 from the target. If she was not just hit, 80% of the time she shoots:
  SK2 with a 40 / 50 / 60 % chance by difficulty, else A1. Otherwise a circle walk 4 at speed bonus 50%.
- Closer: 30% she runs away (12 - distance) at 100%, else A1.

### GoodNpcRanged (monai row 60, think `0x5be000`)

Confidence: **confirmed**. Used by the unused rogue scout, and the routine a ranged mercenary without an owner falls
back to (see Hireable). It only acts in neutral mode (else idle 5). In town, or with no target closer than 20, it
wanders 5 (20%) or idles 10. With a target, 30% of the time it attacks, else circle walk 4 (30%) or idle 10. The
attack is A1, except for a unit flagged as a mercenary: it uses skill slot aip2 when aip2 is above 0, and animation
mode aip3 when aip2 is 0.

| aip | Meaning |
|-|-|
| 2 | skill slot (1 to 8) of the attack (mercenaries only) |
| 3 | animation mode of the attack when aip2 is 0 (mercenaries only) |

### Hireable (monai row 61, think `0x5beac0`, alt `0x5af320`)

Confidence: **confirmed**. The mercenary AI; see also [hirelings.md](hirelings.md) and
[monster-ai.md](monster-ai.md) section 7. Most of its choices come from hireling.txt, the rest from aip1 to aip3.

- Without an owning player it switches to the GoodNpcRanged routine when aip1 is 1 and to a built-in melee fallback
  otherwise (in melee range 80% attack, else 30% approach).
- Leash: farther than 100 from its owner it warps to them, farther than 24 it runs back, and between 16 and 24 it
  follows when the owner walks or runs.
- Near its owner: on a blocked spot it wanders 5 (12 in 128 ticks, 6 in 128 when aip1 is 2). Outside town an enemy
  closer than 25 starts combat. Otherwise it steps aside when standing on its owner, moves near the owner (16) 5%
  of the time and idles 5; in another area than its owner it runs to them.
- Combat: each tick it acts with a chance of 98% when aip1 is 2, else 40 + 2 x its level, plus 10 for every failed
  roll (95 at most). With aip1 = 2 it first runs into melee range. With other values it backs off toward its owner
  50% of the time when the target is closer than 4.
- When it acts it picks from hireling.txt: `DefaultChance` weighs the default attack, and `Chance` +
  `ChancePerLvl` x (level - `Level`) / 4 weighs each `Skill1` to `Skill6` it has (cast in the matching `Mode`). The
  list stops at the first empty slot. Skills with aitype 1 (buffs) count only when a unit lacking their aurastate
  is found (owner, itself, party, pets), skills with aitype 2 are skipped when the target already carries their
  auratargetstate, Inferno counts only with the target within skill level / 2 + 4, and aura skills are set as its
  active aura.
- The default attack is skill slot aip2 (aip2 above 0) or animation mode aip3 (aip2 = 0). It is used only when aip1
  is 1 or the target is in melee range; otherwise the mercenary idles 10.

| aip | Meaning |
|-|-|
| 1 | combat style: 1 ranged, 2 melee, other values caster (hybrid) |
| 2 | skill slot (1 to 8) of the default attack |
| 3 | animation mode of the default attack (aip2 = 0) |
| 4 | not used by the AI |

### TownRogue (monai row 62, think `0x5ccfe0`)

Confidence: **confirmed**. It attacks any enemy closer than 25 with A1 and idles 50 otherwise. The game also uses
this routine as a built-in fallback (special state 8). No parameters.

### GargoyleTrap (monai row 63, think `0x5b00e0`)

Confidence: **confirmed**. The trap turns to face its target along the nearest of the four grid directions. It
fires only when the target is nearly in line (less than 6 off on one axis) and closer than aip1: SK1 on aip2, then a
wait of aip3 frames. Otherwise it idles aip4 frames (0 counts as 1).

| aip | Meaning |
|-|-|
| 1 | shooting distance |
| 2 | chance to use SK1 |
| 3 | wait frames after a shot |
| 4 | idle frames when it does not shoot |

### SkeletonMage (monai row 64, think `0x5b46b0`)

Confidence: **confirmed**.

- Target farther than aip2: on aip3 it approaches at speed bonus 10% and stops at aip2.
- Target within aip4: on aip5 it flees 5 at speed bonus 25%. If the flee fails it shoots A1 at once, whatever aip1
  says.
- Target closer than aip6: A1 on aip1.
- Otherwise a second aip3 roll can make it approach (target beyond aip2); then circle walk 4 on aip7, else idle
  aip8.

| aip | Meaning |
|-|-|
| 1 | chance to shoot A1 |
| 2 | distance it approaches to |
| 3 | chance to approach (target beyond aip2) |
| 4 | too-close distance |
| 5 | chance to flee (target within aip4) |
| 6 | shooting distance |
| 7 | chance to circle walk 4 |
| 8 | idle frames (aip7 failed) |

### FetishShaman (monai row 65, think `0x5aefb0`)

Confidence: **confirmed**, classic notes.

- SK1 (the fire breath) is used on the target, with no roll, whenever the target is closer than the shaman's SK1
  skill level in subtiles. Its pack gets an attack order at the same time. On the next tick the channel state
  (state 12) is removed and the rest of the routine runs.
- Raise: it looks for a corpse within aip5. When one is found, aip1 succeeds and SK3 can be used on it, it orders
  its pack to the corpse and either uses SK3 on it (squared distance at or below aip3) or moves near it (10).
- Otherwise circle walk 4 on aip4, else idle 10.
- Which corpses: dead hostile monsters of BaseId 141 (and BaseId 396 unless aip2 is 1). With aip2 = 0 only members
  of its own pack count. With aip2 below 3 champions and uniques are left out. A player-allied shaman treats aip2
  as at least 1.
- It never heals living units.
- From the 1.14d routine (`0x5f9a80`, alt `0x5f9950`, corpse callback `0x5f99a0`): a corpse must still carry the
  targetable flag 0x2 and no state of the undead kind, and the nearest wins. SK3 (Resurrect2) needs its placement
  check to pass: nothing of mask 0x3c01 under the corpse's collision pattern, so an object, a wall or a living unit on
  the corpse stops the raise. The pack gets an Attack order (with the breath) or a follow order to the corpse (with
  the raise). The alt ends a breath still on, makes the asked state change and idles 1. Our notes read the last
  step as a walk at the target on aip4 (else idle 10), not a circle walk; the difference was not settled.
- The raise itself is in [skills-missiles.md](skills-missiles.md) section 10 (srvdo 97).

| aip | Meaning |
|-|-|
| 1 | chance to raise with SK3 |
| 2 | corpse rule: 0 own pack only, 1 BaseId 141 only, 2 BaseId 141 and 396, 3 or more also champions and uniques |
| 3 | raise distance, compared with the squared distance (25 = 5 subtiles) |
| 4 | chance to circle walk 4 |
| 5 | corpse search distance |

### SandMaggotQueen (monai row 66)

Confidence: **compendium only**. The queen plays S1, waits aip2 frames, spawns one minion and then waits
25 x aip2 frames before the next cycle. What she spawns is fixed by her BaseId in the game's spawner table; no skill
is used. After aip1 spawns she gives no more orders and stays where she is (she does not die).

| aip | Meaning |
|-|-|
| 1 | spawn count |
| 2 | wait frames before a spawn; the wait between cycles is 25 times that |

### NecroPet (monai row 67, think `0x5c1400`)

Confidence: **confirmed**. The summon AI, also used by revived monsters. It has a melee and a ranged version, picked
by a flag the summoning skill sets (the ranged one for skeletal mages). The aip values in its monstats rows are not
read.

- Without an owner it warps to its remembered owner, or idles 10.
- Melee version: farther than 50 from the owner it warps to them, farther than 28 it follows them. Closer, it
  targets the nearest enemy within 24 if that one is within 6, else its owner's target within 36. The shared
  follow-the-owner routine is tried first. Then: without a target, or in town, it stays near its owner (4); in
  melee range 80% A1, else idle 10; outside melee range it approaches.
- Ranged version: no leash check of its own. It targets the nearest enemy within 15, or else its owner's target
  within 20 when the line to it is clear. After the follow routine, 80% of the time it uses SK1 at the target, else
  idle 10 (75%) or circle walk 3.

### VileMother (monai row 68)

Confidence: **confirmed**, classic notes.

- Spawning: with fewer than aip1 children spawned, on aip3 she uses SK1 at a point 3 subtiles from herself toward
  the target (8 directions are tried). She skips this when aip2 or more of her own child type are already within
  25. A player-allied mother is limited by aip2 or by her pet limit (20 at most).
- In melee range: A1 on aip4, else idle aip7.
- Outside: once all spawns are used she always approaches. Before that she approaches on aip5, else circle walks 4
  on aip6, else idle 15.
- More from the 1.14d routine (`0x5fa010`): her child class is vilechild1 walked down monstats NextInClass as many
  steps as she sits down her own class chain, so each mother counts and lays the children of her own tier. The spawn
  spot is 3 subtiles out in the eighth toward the target, then the other eighths in turn; the first where vilemother1
  could stand (a room, nothing of mask 0x3c01 under a small unit's cross) gets SK1 (Nest). The spawn branch runs only
  while she is evil.

| aip | Meaning |
|-|-|
| 1 | most children she spawns |
| 2 | most children of her type within 25 |
| 3 | chance to use SK1 |
| 4 | chance to attack (in melee range) |
| 5 | chance to approach (outside melee range, spawns left) |
| 6 | chance to circle walk 4 (aip5 failed) |
| 7 | idle frames (aip4 failed) |

### VileDog (monai row 69)

Confidence: **confirmed**, classic notes. It idles 5 on its first tick. In melee range A1 on aip1, else idle aip2.
Outside melee range it approaches on aip3, else idle 10. A walk the mode system refuses forces a collision check and
draws again: 70% a short random walk, else idle 10. Users: the vile children (vilechild1-5) and the pain worms
(painworm1-5); none stands in a level's monster list.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames (aip1 failed) |
| 3 | chance to approach (outside melee range) |

### FingerMage (monai row 70, think `0x5af7e0`)

Confidence: **confirmed**.

- Just hit: it stops fleeing. In melee range it then always attacks with A1; outside it uses SK1 when the target is
  within aip5.
- Escape: a finger mage without a pack leader starts fleeing (flee 9) when its life drops below aip4 %. While
  fleeing it runs away 14 at its run speed as long as the target is within 14. The escape ends when life rises
  above aip3 %, after more than aip6 fleeing ticks, or at random 25% of the time (idle 15 then).
- In melee range: A1 on aip1; else SK1 on aip2, else circle walk 5.
- Outside, target at aip5 or farther: approaches when the target is within aip7, else idle 15.
- Target within aip5: SK1 on aip2. Otherwise it approaches when the target carries state 84 (fingermagecurse) and
  idles aip8 frames when it does not.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to use SK1 |
| 3 | life % above which the escape ends |
| 4 | life % below which the escape starts (only without a leader) |
| 5 | SK1 range |
| 6 | most ticks of one escape |
| 7 | approach distance |
| 8 | idle frames (aip2 failed) |

### Regurgitator (monai row 71)

Confidence: **confirmed**, classic notes.

- Corpses: the nearest hostile monster corpse within aip6 that its monstats2 flags allow to be eaten. With one in
  range it goes to eat it when the corpse is closer than 3 and aip2 succeeds, or when aip5 succeeds.
- In melee range: A1 on aip1, else idle 15.
- Outside: approaches on aip3; else goes to eat a corpse on aip4, else idle 8.
- Eating: it walks to the corpse and eats it with SK1. If it stops more than 2 subtiles short, its retry walk is
  cancelled by the idle issued right after it, so it eats from where it stands. After eating it flees 8 while the
  target is in melee range, then shoots A2 once and flees 16.
- More from the 1.14d routine (`0x5fa710`, corpse search `0x5fa690`): a corpse qualifies when it is a monster other
  than itself, not a hireling (unit flag 0x200), still flagged targetable (0x2), dead, with no state of the udead
  kind, evil and monstats2 soft. The walk to it gives up after six tries. The eat (SK1, RegurgitatorEat, srvdo 108)
  removes the corpse and heals calc1% of the corpse's full max life, capped at its own max life.
- Flag 0x2 is set when the monster is made (1.14d `0x574266`) and kept through an ordinary death; a state of the hide
  kind that outlives the death (Freeze, for example) clears it (1.14d `0x5a65bb`), so such a corpse is never eaten or
  raised.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to eat a corpse closer than 3 |
| 3 | chance to approach (outside melee range) |
| 4 | chance to eat a corpse (outside melee range, aip3 failed) |
| 5 | chance to eat a corpse (checked first) |
| 6 | corpse search distance |

### DoomKnight (monai row 72)

Confidence: **confirmed**, classic notes. In melee range A1 on aip1, else idle aip2. Outside melee range it
approaches on aip3, else idle aip4. No life check.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames (aip1 failed) |
| 3 | chance to approach (outside melee range) |
| 4 | idle frames (aip3 failed) |

### AbyssKnight (monai row 73)

Confidence: **confirmed**, classic notes.

- Below aip1 % life, when SK2's aurastate is not on it, it uses SK2 (no target) on aip2.
- In melee range: A1 on aip3, else idle aip4.
- Outside: SK1 is cast at the target when its counter is at 0; the counter is then set to aip6 ticks and counts down
  once per tick. With the target closer than aip5 the counter is re-armed whenever it reaches 0, so SK1 is never
  used at close range (an aip6 of 0 switches that off).
- When SK1 is not cast it approaches on aip7, else circle walks 3 when the target is closer than aip8, else idle 15.

| aip | Meaning |
|-|-|
| 1 | life % below which it may use SK2 |
| 2 | chance to use SK2 |
| 3 | chance to attack (in melee range) |
| 4 | idle frames (aip3 failed) |
| 5 | least distance for SK1 |
| 6 | SK1 cooldown (ticks) |
| 7 | chance to approach (SK1 not cast) |
| 8 | circle walk distance |

### OblivionKnight (monai row 74, think `0x5b7120`)

Confidence: **confirmed**. It looks for the nearest doom knight (BaseId 310) within 50.

- Target within aip1: casts SK4 when the target lacks SK4's curse state (the skill timer is set to aip3 frames).
  Otherwise it walks to the doom knight when that one is farther from the target than itself, else flees 10 at
  speed bonus 50%, and uses SK3 at the target when it cannot flee.
- Target within aip2: SK6 when its timer allows and aip4 succeeds (timer aip3 frames). Otherwise, on aip5: SK3 on
  aip6, else SK1.
- Otherwise: moves near the target (6) when it is farther than aip8 and aip7 succeeds; else 70% circle walk 3, else
  idle 10.

SK2 and SK5 are never used.

| aip | Meaning |
|-|-|
| 1 | flee distance |
| 2 | casting distance |
| 3 | frames before the next SK4 or SK6 |
| 4 | chance to use SK6 |
| 5 | chance to use SK1 or SK3 |
| 6 | chance of SK3 rather than SK1 |
| 7 | chance to move near the target (target beyond aip8) |
| 8 | approach distance |

### QuillMother (monai row 75)

Confidence: **compendium only**. When just hit she gives her pack an attack order on the target, then attacks with
A1 (in melee range) or approaches. Otherwise: in melee range A1 on aip1, else idle aip3; outside she approaches on
aip2, else idle aip4.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | idle frames (aip1 failed) |
| 4 | idle frames (aip2 failed) |

### EvilHole (monai row 76, think `0x5ae410`)

Confidence: **confirmed**. A level populator. In neutral mode it waits (idle 5) until the target is within 5; then
it opens (S3, then S4, 20 frames each). While open it spawns a unit every aip2 frames, picked by its BaseId in the
game's spawner table (fallen1 for the evil hole; a hole of class 711 spawns class 712 and gives it state 184). The
spawned units give no experience and no drops. After aip1 spawns, or as soon as it leaves S4, it plays its death
animation. SK1 is not used.

| aip | Meaning |
|-|-|
| 1 | spawn count |
| 2 | frames between spawns |

### Trap-Missile, Trap-Poison, Trap-Nova (monai rows 77, 80, 92)

Confidence: **compendium only**. Traps that objects and maps create.

- Trap-Missile: while the target is within aip1 and it has fired fewer than aip2 shots, it alternates an A1 shot at
  the target with an idle of aip3 frames. Without a target in range, or with its shots used up, it removes itself
  (death mode).
- Trap-Poison and Trap-Nova share one routine: the same with SK1 in place of A1. Without an SK1 it only idles and
  never runs out.

| aip | Meaning |
|-|-|
| 1 | range |
| 2 | number of shots |
| 3 | frames between shots |

### Trap-RightArrow, Trap-LeftArrow (monai rows 78, 79, think `0x5b5f60` and `0x5b5b40`)

Confidence: **confirmed**. The trap fires when the target is between aip1 and aip2 away and within 2 subtiles of the
trap's X coordinate (RightArrow) or Y coordinate (LeftArrow), at most once every aip3 frames. The shot is A1. In
levels with id 40 or higher the game rolls once per level (1 in 3) to make these traps use SK1 instead. It idles 40
when the target is out of range and 30 otherwise.

[monster-ai.md](monster-ai.md) lists aip4 as a second delay. The routine stores aip4 but never reads it back, so
it has no effect.

| aip | Meaning |
|-|-|
| 1 | least distance |
| 2 | greatest distance |
| 3 | frames between shots |
| 4 | second delay (stored, never read by this routine) |

### JarJar (monai row 81)

Confidence: **compendium only**. The guard at the Act II palace entrance. While the palace is closed to the player
he guards a home point: at most every 200 frames he walks back when he was pushed more than 1 subtile away, and
otherwise he runs the shared town NPC routines (idle 20). Once the quest check opens the palace he moves to a spot
3 subtiles from his home point and stays there. No parameters.

### InvisoSpawner (monai row 82)

Confidence: **compendium only**. An unused level populator. While the target is within aip2 and its timer allows,
it spawns a monster of the BaseId 96 line (the variant that fits its room) on a free spot, then waits aip3 frames.
It idles 15 between checks and dies after aip1 spawns.

| aip | Meaning |
|-|-|
| 1 | spawn count |
| 2 | activation distance |
| 3 | frames between spawns |

### MosquitoNest (monai row 83)

Confidence: **compendium only**. An unused level populator. It acts only while the target is within aip2 (else idle
25). Every aip3 frames it uses SK1 at the target when the spot under it is free for its spawn. It dies once its
uses exceed aip1, so it uses SK1 aip1 + 1 times.

| aip | Meaning |
|-|-|
| 1 | use count (dies after aip1 + 1 uses) |
| 2 | activation distance |
| 3 | frames between uses of SK1 |

### BoneWall (monai row 84, init `0x5bbdf0`, think `0x5bbdb0`)

Confidence: **confirmed**. When the wall is created its expiry time is set from `Param2` of its SK1 in skills.txt
(frames). It idles 15 until that time has passed, then plays its death animation. No parameters.

### HighPriest (monai row 85, think `0x5be300`)

Confidence: **confirmed**. The high priest is either holding back or engaged. Its ranged attack is its S1
animation, which fires the `MissS1` missile; it is used only within that missile's range minus 2.

- Holding back, in melee range: on aip1 it engages and attacks with A1, else it flees 6.
- Holding back, outside melee range, when the skill timer allows: SK2 on the most hurt ally within 50 that is at or
  below 75% life (aip2, timer aip3 frames; it can heal itself). Then SK1 near the target, at a random spot up to 5
  off on each axis, when the target is within aip8 (aip4, timer always 100 frames). Then the S1 attack (aip5).
  Otherwise 80% of the time it circle walks 3 (target within aip8) or approaches to 6, and 20% of the time it
  engages.
- Engaged, in melee range: the S1 attack on aip7; else on aip6 it stops engaging and flees 6; else A1 (90%) or idle
  10.
- Engaged, outside melee range: the S1 attack when the target is within 6 and aip7 succeeds; else on aip6 it stops
  engaging (idle 10); else 70% approach, 30% wander 12.

| aip | Meaning |
|-|-|
| 1 | chance to engage (in melee range, holding back) |
| 2 | chance to use SK2 on a hurt ally |
| 3 | SK2 interval (frames); SK1 always uses 100 |
| 4 | chance to use SK1 (target within aip8) |
| 5 | chance to use the S1 attack (holding back) |
| 6 | chance to stop engaging |
| 7 | chance of the S1 attack rather than A1 (engaged) |
| 8 | SK1 and circle walk distance |

### Hydra (monai row 86, think `0x5cbd80`)

Confidence: **confirmed**. It dies when its expiry time (set at creation) has passed. Until then it uses SK1 on a
target within 25 60% of the time, else idles 10. No parameters.

### Trap-Melee (monai row 87)

Confidence: **compendium only**. In melee range it attacks with A1 on aip1, else idles aip2 frames. Outside melee
range it idles 40.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames (aip1 failed) |

### 7TIllusion (monai row 88)

Confidence: **compendium only**. On its first tick it plays A1 toward a point 10 subtiles away in the -X direction,
whatever its target. On the next tick it removes itself (death mode). There is no range check. No parameters.

### Megademon (monai row 89, think `0x5c04f0`)

Confidence: **confirmed**. SK1 reaches as many subtiles as its skill level. SK1 holds a channel state (state 12);
while that state is on, the next tick removes it and skips SK1.

- Outside melee range, within SK1's reach: SK1 when its timer allows and aip1 succeeds (timer aip6 frames).
  Otherwise it approaches on aip4, else idle 10.
- In melee range: SK1 when its timer allows and aip2 succeeds. Otherwise A1 on aip3, else circle walk 3 on aip5,
  else idle 5.

| aip | Meaning |
|-|-|
| 1 | chance to use SK1 (outside melee range, within its reach) |
| 2 | chance to use SK1 (in melee range) |
| 3 | chance to attack (aip2 failed) |
| 4 | chance to approach (outside melee range) |
| 5 | chance to circle walk 3 (aip3 failed) |
| 6 | frames between uses of SK1 |

### Griswold (monai row 90, think `0x5be220`)

Confidence: **confirmed**. In melee range he attacks with A1 80% of the time. Outside he approaches 50% of the
time. He idles 10 when the roll fails. No parameters.

### DarkWanderer (monai row 91)

Confidence: **compendium only**. He idles 10 until his quest script gives him a destination. Then, with a target
within 20, he walks toward that point; after reaching it, or after 3 walks, he dies and starts his quest event.
Spawned through levels.txt he stays idle. No parameters.

### ArcaneTower (monai row 93)

Confidence: **compendium only**. It shoots at its target with no range check, in two alternating rounds:

- SK1 at the target aip1 times, aip2 frames apart, then a pause of aip3 frames.
- A1 at the target aip4 times, aip6 frames apart, then a pause of aip5 frames.

The second round uses A1, not SK2. Without an SK1 only the second round runs.

| aip | Meaning |
|-|-|
| 1 | number of SK1 shots |
| 2 | frames between SK1 shots |
| 3 | pause after the SK1 round (frames) |
| 4 | number of A1 shots |
| 5 | pause after the A1 round (frames) |
| 6 | frames between A1 shots |

### DesertTurret (monai row 94)

Confidence: **compendium only**. On its first tick it fires SK1 the way it faces. After that it fires only while
the target is within aip4 (else idle 15, and its shot counter slowly runs down). Each shot aims at the target's
position plus an offset that cycles through a fixed pattern, multiplied by aip5; with aip5 = 0 every shot goes
straight at the target. A burst is aip2 + 1 shots, aip1 frames apart, followed by a pause of aip3 frames.

| aip | Meaning |
|-|-|
| 1 | frames between shots |
| 2 | burst size (aip2 + 1 shots) |
| 3 | pause after a burst (frames) |
| 4 | firing distance |
| 5 | spread: multiplier of the aim offset |

### PantherJavelin (monai row 95)

Confidence: **confirmed**, classic notes; aip3 **settled** in favour of our notes (it was **differs**).

- Target closer than 8: flees 16 on aip4.
- Target farther than aip6 - 6: moves near it (4) on aip1.
- Target within aip6: throws A1 on aip2.
- No target, or the target beyond aip6: walks to the nearest ally of the same BaseId when that ally is too far
  away (aip3).
- Otherwise idle aip5 frames.

The two sources differ on the unit of aip3:

- Compendium: aip3 is a distance in subtiles.
- Our notes (classic routine): aip3 is compared as it is with the squared distance to the ally, so the real
  distance is the square root of aip3.

**Settled** (from the 1.14d routine `0x5e1080`, re-read branch by branch): the nearest-ally finder (1.14d `0x5b0d00`)
keeps the squared distance of its distance helper (1.14d `0x5b0bd0`), and the compare at 1.14d `0x5e1213` tests it
against aip3 unsquared. So aip3 is a squared distance: aip3 = 25 pulls it back from 5 subtiles. The PantherWoman
(row 18) is different: she squares her aip3 before the same compare, so hers is in subtiles. Other details from the
same read: the distances are those of the routine's own target search (the largest int when it finds none), not the
tick's; the flee 16 spends its roll whether the step starts or not; the move near is a spot up to 4 around the
target. The 93847 think was not re-read; not checked in the game.

| aip | Meaning |
|-|-|
| 1 | chance to move closer (target beyond aip6 - 6) |
| 2 | chance to throw A1 (target within aip6) |
| 3 | greatest distance from the nearest ally of its type, as a squared distance (25 = 5 subtiles) |
| 4 | chance to flee (target closer than 8) |
| 5 | idle frames |
| 6 | throwing distance |

### FetishBlowgun (monai row 96)

Confidence: **confirmed**, classic notes.

- It obeys its pack leader: an attack order makes it shoot A1 at the ordered unit, a follow order makes it walk
  there at speed bonus 50%.
- Target farther than aip1: moves near it (6) at 50%.
- Target in melee range or closer than 6: starts an escape on aip2. While escaping it flees 14 at 50% as long as
  the target is within 12 (A1 when the flee fails); farther away it runs a circle walk 4 20% of the time.
- Otherwise it shoots A1. After 3 to 5 shots it moves with a running circle walk 6 at 50%.

| aip | Meaning |
|-|-|
| 1 | shooting distance |
| 2 | chance to escape (target in melee range or closer than 6) |

### Spirit (monai row 97)

Confidence: **compendium only**. It idles 10 until a target is in melee range, attacks once with A1, and from then
on only idles 50 frames at a time. The routine does not kill the unit. No parameters.

### Smith (monai row 98, think `0x5c3590`)

Confidence: **confirmed**. In melee range he attacks with A1. Outside he always approaches, with a speed bonus of
(100 - life %) / 2, so he gets faster as he is hurt (up to 50%). He never pauses. No parameters.

### TrappedSoul (monai row 99)

Confidence: **compendium only**. It idles 15 until a target comes within 5, then plays S2 once and S1 after that.
In melee range, at most every 35 frames, it attacks: A1 when the target is on one side (target X at or below its
own and target Y at or above its own), A2 when it is on the opposite side. In the other two quarters it plays S1
and checks again 5 frames later. No parameters.

### Buffy (monai row 100, think `0x4a3680`)

Confidence: **confirmed**. Hadriel's AI. It is the Idle routine: it idles 200 frames at a time. No parameters.

### SiegeTower (monai row 113)

Confidence: **confirmed**, classic notes (1.14d think `0x5e1860`). The tower idles aip1 frames at a time. Whenever
it has no living rider and a target exists, it picks a free imp within 20 (class 492, hostile, on its side, alive,
with no mount yet) and makes itself that imp's mount. The imp then walks to the tower and boards it (see Imp).

- The search (1.14d `0x5e1720`) takes the first match of the near-room scan, within squared distance 400.
- It tests the tower's own owner link: a tower whose rider died but is still linked takes no new imp.
- 1.14d idles aip1 twice after a claim; the second replaces the first. No draw.

| aip | Meaning |
|-|-|
| 1 | idle frames |

### ReanimatedHorde (monai row 114)

Confidence: **confirmed**, classic notes, for the routine; the self-resurrect rules are **compendium only**.

- In melee range: A1 on aip1 (never A2), else idle aip2.
- Outside: SK2 at the target when it has an SK2, the target is farther than 5 and closer than aip3, a straight line
  to the target (or to a point 2 to 4 subtiles beside it) is free of walls and doors, and aip4 succeeds. An aip3 of
  6 or less therefore switches SK2 off.
- Otherwise it approaches on aip5, else takes one straight step of up to 4 toward the target on aip6, else idle
  aip7.

Rising again is not part of this routine. It belongs to the hidden monster mod 34, which the game gives to every
monster whose BaseId is reanimatedhorde1, whatever its AI. That mod reads aip1 and aip8 of this row:

- When the death animation starts (hostile monsters only) aip8 is rolled once. On success a rise attempt is set
  1 + 10 x aip1 frames later.
- The attempt goes ahead only if the monster is still dead, is not a unique or superunique, is not allied and has
  risen fewer than 2 times. While something stands on the corpse it waits another 1 + 10 x aip1 frames, without a
  new roll.
- It casts skill 293 in S1. The skill has to be in one of the monster's skill slots (any of the 8).
- The count of 2 starts over whenever the monster's AI enters or leaves a special state such as fear.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range); also the rise delay (1 + 10 x aip1 frames) |
| 2 | idle frames (aip1 failed) |
| 3 | SK2 range (the target must also be farther than 5) |
| 4 | chance to use SK2 |
| 5 | chance to approach (outside melee range) |
| 6 | chance to step up to 4 toward the target (aip5 failed) |
| 7 | idle frames (aip5 and aip6 failed) |
| 8 | chance to rise again (rolled once per death) |

### SiegeBeast (monai row 115)

Confidence: **confirmed**, classic notes.

- A hostile siege beast without a rider picks a free imp (class 492, as for the SiegeTower) within aip1 and makes
  itself that imp's mount.
- In melee range: SK1 (no target) on aip3, else A1 on aip2, else idle aip4.
- Outside: SK1 (no target) when the target is closer than `Param5` of SK1 in skills.txt and aip5 succeeds.
  Otherwise it always steps straight toward the target (12 subtiles). When the way is clear and aip6 succeeds,
  that step gets a speed bonus of aip7 % (127 at most).

| aip | Meaning |
|-|-|
| 1 | rider search distance |
| 2 | chance to attack (in melee range, aip3 failed) |
| 3 | chance to use SK1 (in melee range) |
| 4 | idle frames (aip2 and aip3 failed) |
| 5 | chance to use SK1 (outside melee range) |
| 6 | chance of the speed bonus on its step |
| 7 | speed bonus % |

### Minion (monai row 116, think `0x5c0820`)

Confidence: **confirmed**.

- It obeys a timed attack order from its leader: while the order lasts it always attacks or approaches the ordered
  unit.
- In melee range: it attacks when ordered or on aip1; the attack is A2 on aip2, else A1. When aip1 fails it idles
  aip2 frames, so aip2 is both the A2 chance and this idle.
- Outside: it approaches when ordered or on aip3, else idle aip4.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance of A2 rather than A1; also the idle frames when aip1 fails |
| 3 | chance to approach (outside melee range) |
| 4 | idle frames (aip3 failed) |

### SuicideMinion (monai row 117)

Confidence: **confirmed**, classic notes. Outside melee range it approaches on aip3, else idle aip2. When it
reaches melee range it arms a fuse of aip5 frames. From then on it only idles aip2 frames at a time; once the fuse
has run out it goes into death mode at its target (the explosion).

| aip | Meaning |
|-|-|
| 2 | idle frames between checks |
| 3 | chance to approach (outside melee range) |
| 5 | fuse (frames) |

### Succubus (monai row 118)

Confidence: **confirmed**, classic notes.

- Skills: when the target is not cursed, is within aip4 and aip3 succeeds, she uses the first skill that applies:
  SK1 when the target's life is at least aip7 %, SK2 when her own life is at most aip8 %, SK3 when the target's
  maximum life is above its maximum mana or it is not a player, SK4 when it is a player. With aip7 = 0, SK1 always
  applies first.
- In melee range: A1 on aip1, else idle aip5.
- Outside: with an SK5 and aip8 above 0 she casts SK5 at the nearest enemy on aip8. Otherwise she approaches on
  aip2, else idle aip6.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | chance to use a skill (target within aip4) |
| 4 | skill distance |
| 5 | idle frames (aip1 failed) |
| 6 | idle frames (aip2 failed) |
| 7 | target life % at or above which SK1 applies |
| 8 | own life % at or below which SK2 applies; also the chance to use SK5 |

### SuccubusWitch (monai row 119, think `0x5c36d0`)

Confidence: **confirmed**.

- Skills: the Succubus rule (aip3, aip4, aip7, aip8).
- In melee range: flees aip4 on aip3; else A1 on aip1, else idle aip6.
- Outside, in this order: with an SK5 and aip8 above 0, on aip5 she casts SK5 at the nearest enemy if aip8 also
  succeeds. With the target within aip4 she flees aip4 on aip3. Without an SK5 she uses S2 on the nearest enemy on
  aip5. Then she approaches on aip2; when that fails she runs a circle walk 6 half of the time and idles aip6
  frames the other half.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | chance to use a skill; chance to flee (target within aip4) |
| 4 | skill distance and flee distance |
| 5 | chance to try SK5, or S2 when she has no SK5 |
| 6 | idle frames |
| 7 | target life % at or above which SK1 applies |
| 8 | own life % at or below which SK2 applies; also the chance to use SK5 |

### Overseer (monai row 120, think `0x5c2290`)

Confidence: **confirmed**. Its minions are living monsters of BaseId 453 that are not in state 141. In this order:

- Just hit: SK1 on the unit that hit it, when its timer allows (timer aip1 frames).
- In melee range: attacks on aip6 (A2 on aip7, else A1).
- SK2 on the first minion within 24 that is below 50% life, on aip2.
- SK3 on the first non-unique minion within 20, on aip3 (never by a player-allied overseer).
- With minions it keeps between aip4 - aip5 and aip4 + aip5 from the target: closer, it flees (aip4 - distance);
  inside the band it circle walks 5 or idles 10 (50/50); farther, it approaches.
- Without minions it approaches 60% of the time, else idle 10.

| aip | Meaning |
|-|-|
| 1 | SK1 interval (frames) |
| 2 | chance to use SK2 on a hurt minion |
| 3 | chance to use SK3 on a minion |
| 4 | preferred distance from the target |
| 5 | tolerance around aip4 |
| 6 | chance to attack (in melee range) |
| 7 | chance of A2 rather than A1 |

### MinionSpawner (monai row 121)

Confidence: **compendium only**. While it has spawns left it idles aip2 frames between checks. When the target is
within aip4 and its spawn timer allows, it counts the living hostile monsters of BaseId 453 and 461 around it.
With fewer than aip2 of them it uses SK1 at the target and waits aip3 frames before the next spawn. After aip1
spawns it gives no more orders.

| aip | Meaning |
|-|-|
| 1 | spawn count |
| 2 | most living minions around it; also the idle frames between checks |
| 3 | frames between spawns |
| 4 | activation distance |

### Imp (monai row 122)

Confidence: **confirmed**, classic notes. The imp reads its parameters from four monstats rows at once: aip1 to
aip4 of the rows with hcIdx 492, 493, 494 and 495 (current difficulty). Its own row only supplies its skills.

- Siege rides: a siege tower or siege beast can make itself an imp's mount. The imp then walks to it until it is
  within the boarding distance and boards it with SK1. The link is dropped when the mount is gone, dead, already
  ridden or allied.
- In melee range: below the life threshold it teleports with SK1 to a random point within the teleport distance of
  itself (on each axis). It flees 5 on the flee chance. Then it goes on as below.
- Outside melee range: it teleports the same way on the teleport chance. It flees 5 when the target is within the
  flee distance and the flee chance succeeds. With an SK4 it casts it at the nearest enemy within the first SK4
  distance on the first SK4 chance, or within the second SK4 distance on the second SK4 chance.
- Otherwise: 33% approach to within 4, then 20% wander 8, else idle 10.

| Row (hcIdx) | aip | Meaning |
|-|-|-|
| 492 | 1 | life % below which it teleports in melee range |
| 492 | 2 | teleport distance |
| 492 | 3 | chance to teleport (outside melee range) |
| 493 | 1 | boarding distance |
| 494 | 1 | flee distance |
| 494 | 2 | chance to flee |
| 494 | 3 | first SK4 distance |
| 494 | 4 | first SK4 chance |
| 495 | 3 | second SK4 distance |
| 495 | 4 | second SK4 chance |

### Catapult (monai row 123)

Confidence: **confirmed**, classic notes (1.14d think `0x5e34c0`). It plays A1 (no target) on aip1, else idles 15:
one draw a think. The attack's missile goes where the catapult aims. The bombardment near players is cast by the
CatapultSpotter. Catapults, spotters, the siege tower and the evil hut are on no levels.txt list: the Act V presets
place them.

| aip | Meaning |
|-|-|
| 1 | chance to play A1 |

### FrozenHorror (monai row 124)

Confidence: **confirmed**, classic notes. SK1 reaches as many subtiles as its skill level. SK1 holds a channel state
(state 12); while that state is on, the next tick removes it and skips SK1.

- Target within SK1's reach: SK1 on aip3.
- In melee range: A1 on aip1. Outside: approaches on aip2. It idles aip4 frames when that roll fails.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | chance to use SK1 (target within its reach) |
| 4 | idle frames (aip1 or aip2 failed) |

### BloodLord (monai row 125, think `0x5bbb40`)

Confidence: **confirmed**. Outside melee range it approaches on aip2. In melee range it attacks on aip1: SK1 on
aip3 (always cast in the A2 animation, whatever `Sk1mode` says), else A1. It idles aip4 frames when aip1 or aip2
fails. Unlike the ThornHulk it keeps no frenzy counter or cooldown: every attack rolls aip3 on its own.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | chance of SK1 rather than A1 |
| 4 | idle frames (aip1 or aip2 failed) |

### CatapultSpotter (monai row 126)

Confidence: **differs** on the catapult search, otherwise confirmed, classic notes (1.14d think `0x5ee040`). The
invisible unit that does the real bombardment.

- On its first tick, and on 3% of later ticks, it looks up to 3 steps along its facing for a dead monster of its
  catapult's class (its own class - 19). When it finds one it removes itself (death mode).
- Our notes (1.14d routine, search `0x5edf70`) read the search differently: it starts at its own room's corner and
  steps one room's width or height at a time, three times. The direction comes from its place in its monstats
  upgrade chain (1.14d table `0x6ea9d0`: chain 0 (0, 1), 1 and 2 (1, 0), 3 (-1, 0)), not from its facing. At each
  corner it asks the room there for a dead monster of its class - 19 (catapultspotter1 516 looks for catapult1 497);
  one sends the spotter to death mode. 1.14d also reads the act's inactive monster records at each corner, so a dead
  catapult whose room went idle still counts when its level keeps such records.
- The aim: after the random offset, the nearest free spot of size 2 clear of mask 0x805, searched in rings 3 apart
  (1.14d `0x64e7e0`); none found: idle 15. The cast is in A1 with no target, and the strike timer restarts whatever
  the cast gave. The five strikes are drawn RL(5) from 287, 288, 303, 304, 305 (1.14d table `0x6e3514`).
- Every aip2 frames, with a target, it fires on aip1; when it does not fire it idles aip2 frames.
- The aim point is the target's position plus a random offset of up to aip4 on each axis. When that point fails a
  collision check it idles 15.
- The spell does not come from its skill columns. It is one of five built-in skills (ids 287, 288, 303, 304, 305),
  cast in the A1 animation. It keeps one spell for aip5 more shots, then picks again at random.

| aip | Meaning |
|-|-|
| 1 | chance to fire |
| 2 | frames between attempts |
| 4 | aim spread around the target |
| 5 | shots before a new spell is picked |

### NpcBarb (monai row 127)

Confidence: **compendium only**.

- Every tick, when it is not in it yet, it adds itself to one of the game's unit lists (list 8, the one the freed
  Wussie prisoners join). This is what makes monsters pick it as a target.
- In melee range it attacks with A1 and thinks again aip1 frames later.
- Outside it runs at the target (speed bonus 100%) when the target is within aip3 and aip2 succeeds.
- Otherwise, and without a target, it walks toward a random point 21 to 40 subtiles away in the -X direction (with
  two fallback points) and idles 15 when no path is found.

| aip | Meaning |
|-|-|
| 1 | frames between attacks |
| 2 | chance to approach (target within aip3) |
| 3 | approach distance |

### Nihlathak (monai row 128, think `0x5cc240`, alt `0x5af320`)

Confidence: **confirmed**. State 12 is removed from him on every tick. In this order:

- No target: when just hit he teleports with SK1 to a random point up to aip2 from himself on each axis; else idle
  25.
- In melee range: the same teleport on aip1.
- Target closer than aip5: flees 5, 40% of the time.
- SK3 on a corpse within 10 of the target, on aip3.
- SK4 at the target 60% of the time when it is within 14.
- SK2 when it is usable and aip4 succeeds, on the first non-unique minion (BaseId 453, not in state 141) within
  25. Without such a minion he casts SK5 at the target instead, after setting its spawn class to the area's
  variant of BaseId 453, when a placement check (class 528) passes; he wanders 6 when it fails.
- Otherwise SK4 at the target when it is within 14, else idle 5. A 60% walk toward the target is issued before
  that idle, but the idle cancels it, so he never walks.

| aip | Meaning |
|-|-|
| 1 | chance to teleport with SK1 (in melee range) |
| 2 | teleport distance |
| 3 | chance to use SK3 on a corpse |
| 4 | chance to use SK2 on a minion (SK5 when there is none) |
| 5 | flee distance |

### GenericSpawner (monai row 129)

Confidence: **confirmed**, classic notes (1.14d init `0x5e6190`, think `0x5e61b0`).

- On its first tick it picks the class it will spawn: a random monster of the level's monster list if that monster
  has `genericSpawn` set in monstats.txt; else the first monster of the list that has it; else class 496.
- That pick does not decide what spawns: the Nest skill (1.14d srvst 49) takes the caster's monstats `spawn` column
  first, which evilhut fills (imp1). In vanilla the pick only spends one seed draw.
- The spot test is a small unit's cross two east and four south of the hut, in a room and clear of mask 0x3c01. A
  busy spot keeps the try's frame and counts nothing. After aip3 spawns it dies with no treasure.
- Without a target, or with the target farther than 20, it idles 20.
- After aip3 spawns it removes itself (death mode).
- Otherwise, at most every aip1 frames, it casts the built-in skill 167 on the target when a unit of class 528
  would fit at its own position. It idles 20 between attempts.

SK1 is not used.

| aip | Meaning |
|-|-|
| 1 | frames between spawns |
| 3 | spawn count |

### DeathMauler (monai row 130)

Confidence: **confirmed**, classic notes. In melee range it attacks with A1 on aip1, else idle 15. Outside it uses
SK1 when the target is within aip3 and aip4 succeeds; otherwise it approaches on aip2, else idle 15.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | SK1 range |
| 4 | chance to use SK1 (target within aip3) |

### Wussie (monai row 131)

Confidence: **confirmed**, classic notes (1.14d think `0x5ee3c0`, kind 2, no init or alt; 93847 not read). The
trapped barbarians of Act V (act5pow 534, five per cage). No parameters. Like the Harrogath barbarians they are
monstats inTown and good (Align 1).

- Cage portal open: when none of the cage is gone and the portal is 4 or more away, 70% walk at the portal, else 10%
  step within 4 of itself, else idle 10. Otherwise (a soldier of the cage gone, or within 4) it leaves: a quest count
  (1.14d `0x588880`; five gone mark the cage gone) and its removal.
- No portal: it looks for the first player within 15. With no dead cage door (monstats 434 in mode DD) in that
  player's room or the rooms next to it, it tells the quest it was seen (1.14d `0x588dd0`: status 5 unless already
  so or five killed) and hails the player (`0x8a`) when a draw % 1000 is under 100. With a dead door there it joins
  the game's node list 8 (so evil monsters can target it), and when a dead door is within 15 of the soldier it
  calls the door-down step (1.14d `0x5888d0`), which opens the cage's portal and gives progress once. That door search
  breaks out of a room at its first match but goes on to the next rooms, so the last room with a match wins.
- It idles 25 at the end of every tick.
- Quest calls it makes: find the cage's portal (1.14d `0x588d60`), is the cage gone (1.14d `0x588830`), and the three
  above. A plugin replacing this think must keep them, or Rescue on Mount Arreat never completes
  ([quests-acts.md](quests-acts.md) section 8.2).

### AncientStatue (monai row 132, think `0x5c69d0`)

Confidence: **confirmed**. It idles 25 until the Ancients event is active. Then it casts the built-in skill 302
(not its SK1) at its own position in the A1 animation, which brings in its Ancient. A statue in state 146 (set
when that happens) only idles. No parameters.

### Ancient (monai row 133, think `0x5c5f60`)

Confidence: **confirmed**. The three Ancients share this AI and branch on their class: 540 at `0x5c6600`, 541 at
`0x5c6220`, 542 at `0x5c6000`. Until the Ancients event is active they idle 25. Any other class with this AI does
nothing.

Talic (class 540). While he is in state 54 (uninterruptable) he idles 25. When SK1 is usable, the target is within
aip1 and aip2 succeeds, he uses SK1 toward a point aip4 subtiles past the target, on the line through it.
Otherwise he approaches when outside melee range (thinking again 10 frames later); in melee range he attacks on
aip3 (A1 or A2, 50/50), else idle 25.

| aip | Meaning |
|-|-|
| 1 | SK1 range |
| 2 | chance to use SK1 (target within aip1) |
| 3 | chance to attack (in melee range) |
| 4 | how far past the target SK1 ends |

Madawc (class 541). In melee range he flees aip5 on aip4. He uses SK1 (no target) on aip3, but only when he has an
SK1, its aurastate is not on him and the target does not carry its auratargetstate. Within aip1 he throws A1 on
aip2, else idle 10. At aip1 or farther he walks toward the target until he is aip1 away (idle 10 when the walk
fails).

| aip | Meaning |
|-|-|
| 1 | throwing distance |
| 2 | chance to throw A1 (target within aip1) |
| 3 | chance to use SK1 |
| 4 | chance to flee (in melee range) |
| 5 | flee distance |

Korlic (class 542). When SK1 is usable, the target is within aip1 and aip2 succeeds, he leaps at the target with
SK1. Otherwise he approaches when outside melee range (thinking again 10 frames later); in melee range he attacks
on aip3 (A1 or A2, 50/50), else idle 25.

| aip | Meaning |
|-|-|
| 1 | SK1 range |
| 2 | chance to use SK1 (target within aip1) |
| 3 | chance to attack (in melee range) |

### BaalThrone (monai row 134, think `0x5c7070`)

Confidence: **confirmed**. Baal on his throne runs the five minion waves. Only aip1 is read, and SK2 to SK4 are
not used: the wave skills are cast by id.

- When the last wave is dead and he has not taunted yet, he casts the built-in skill 285 on himself in the S3
  animation (it uses up the bodies and deals no damage), laughs and waits 250 frames. The next wave's boss is one of
  superuniques 61 to 65, in order.
- While wave monsters are alive (searched within 64) he casts SK1 at the target on aip1, as long as SK1's
  aurastate is not on him and the target does not carry SK1's auratargetstate; else idle 10.
- After the taunt he casts the built-in skill 286 in the S3 animation at the point 13 subtiles from him along +Y,
  which spawns the wave, and waits 100 frames. Details in [monster-ai.md](monster-ai.md) section 11.
- After the fifth wave he turns into class 559 (the BaalToStairs Baal), gets state 142 and switches to that
  class's AI.
- While he waits between waves the routine works out its delay with the wrong sign, so it thinks again every
  frame. This does no harm.

| aip | Meaning |
|-|-|
| 1 | chance to use SK1 |

### BaalCrab (monai row 135, think `0x5a8b80`)

Confidence: **confirmed** for the chooser, the home point and the A2 rule below 33% life; the single weights are
the compendium's and were not compared one by one. Baal in the Worldstone Chamber. No parameters: a weighted
chooser picks one action per tick and he thinks again 25 frames later. The skills he casts by id (315 to 318) are
not read from his skill columns.

Actions: idle 35 / 15 / 5 frames (Normal / Nightmare / Hell), a straight step of 12 toward the target, running
circle walk 6, wander 16, walk home, SK6 (or SK7 against a player whose maximum mana is at least their maximum
life), the built-in skills 315 (S2), 316 (S3), 317 (S1) and 318 (A1), A2, SK5 (teleport to 25 subtiles from the
target; class 709 teleports onto it) and the clone event.

| Situation | Weights |
|-|-|
| No target | wander 16 when his spot is blocked; else skill 315 8% of the time, idle otherwise |
| Idle (always) | (100 - life %) + 50 in melee range or + 100 outside (75 and 125 in Normal) |
| In melee range | SK6/SK7 20, 315 30, A2 150 (200 with the target below 33% life), 316 10, 317 10, 318 70 (45 in Normal), teleport 40 |
| Outside, line of sight | SK6/SK7 40, 315 40, 316 70, 317 80, 318 60 (40 in Normal), teleport 20, step 5, circle walk 5, wander 5, clone 10 x (2 - clones made) |
| Outside, no line of sight | step 20, circle walk 20, wander 20, SK6/SK7 80, 315 70 |

- In melee range a chilled target (state 11) removes SK6/SK7, 316 and 318; no line of sight removes 316, 317 and
  318.
- With line of sight: beyond 25 subtiles 316 drops out and teleport becomes 30; beyond 35, 318 drops out too and
  the step becomes 15. 316 loses 10 with fewer than two possible targets; 318 gains 25 with more than three. He
  makes two clones at most.
- Without line of sight: with fewer than two possible targets the step becomes 45 and A2 gets 25. When his target
  is more than 75 subtiles from his home point the steps drop out, the circle walk becomes 35 and 316 and 318 get
  25; beyond 100 he walks home (weight 60). The line-of-sight row gets similar home-distance changes.

### BaalTaunt (monai row 136, think `0x5c6cf0`)

Confidence: **confirmed**.

- While its target stands still (neutral mode) a counter goes up by one per tick. Once it passes aip2 it casts the
  built-in skill 284 at the target in the A1 animation and the counter starts again. The counter resets whenever
  the target does anything else.
- Target farther than aip3: it warps next to the target and idles 25. Farther than aip1: it walks to the target.
  Otherwise it idles 25, so next to a target aip2 counts roughly in seconds.
- Without a target it idles 25.

| aip | Meaning |
|-|-|
| 1 | walk distance |
| 2 | ticks the target must stand still before skill 284 |
| 3 | warp distance |

### PutridDefiler (monai row 137)

Confidence: **confirmed**, classic notes.

- In melee range of its target: A1.
- A defiler that is not player-allied looks for a living monster within 25 that is not player-allied, not of
  BaseId 546 or 551 and not in state 110. In melee range of that monster it casts the built-in skill 300 on it in
  the S1 animation (its SK1 is not read); farther away it walks to it.
- Otherwise it flees aip2 when the target is within aip1, else idle 25.

| aip | Meaning |
|-|-|
| 1 | distance at which it flees |
| 2 | flee distance |

### BaalToStairs (monai row 138, think `0x5c7580`)

Confidence: **confirmed**. Baal walking off after the last wave. He looks for object 563 within 25 (idle 25 when
there is none) and walks to it. Once he is closer than aip1 he starts the quest event, takes state 146, removes
himself and makes the object change mode.

| aip | Meaning |
|-|-|
| 1 | distance from object 563 at which he vanishes |

### BaalTentacle (monai row 139, think `0x5c6eb0`)

Confidence: **confirmed**. It dies when the unit it is linked to (Baal) is gone or dead. On its first tick its
lifetime is set to aip3 plus a random 0 to aip3 - 1 seconds; when that runs out it dies. In melee range it attacks
with A1 on aip1; otherwise it idles aip2 frames.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | idle frames (aip1 failed) |
| 3 | lifetime in seconds (up to 2 x aip3 - 1) |

### BaalCrabClone (monai row 140, think `0x5a8cf0`)

Confidence: **confirmed**. The BaalCrab chooser with these changes: it dies when Baal (its leader) or the unit it
is linked to is dead, it idles 15 without a target, and the skill 315 attack, the teleport and the clone event are
replaced by a straight step toward the target. No parameters.

### BaalMinion (monai row 141, think `0x5c7750`)

Confidence: **confirmed**. In melee range it attacks on aip1: SK1 on aip3, else A1. When aip1 fails it stops and
asks for an idle, but the aip4 delay below replaces the length of that idle. Outside melee range it approaches on
aip2. Every tick ends with a delay of aip4 frames, whatever it did.

| aip | Meaning |
|-|-|
| 1 | chance to attack (in melee range) |
| 2 | chance to approach (outside melee range) |
| 3 | chance of SK1 rather than A1 |
| 4 | delay frames after every action |

### ClawViperEx (monai row 142, think `0x5ad0d0`)

Confidence: **confirmed**. The ClawViper with a ranged attack.

- In melee range: A2 on aip3, else idle aip5.
- Target within aip7: on aip4 it shoots A1 if at least aip8 frames have passed since its last shot, and idles aip5
  frames if not.
- SK1 (the charge) when the target is within aip2, aip1 succeeds and SK1 is usable at the target's position, with
  the aip6 glow as for the ClawViper (removed again on its next tick).
- Otherwise 50% approach, 50% idle aip5.

| aip | Meaning |
|-|-|
| 1 | chance to use SK1 (target within aip2) |
| 2 | SK1 range |
| 3 | chance to attack with A2 (in melee range) |
| 4 | chance to shoot A1 (target within aip7) |
| 5 | idle frames |
| 6 | glow colour: 0 none, 2 red (state 91), other values blue (state 90) |
| 7 | shooting distance |
| 8 | frames between shots |

## 3. Open points

Where the two sources disagree. Check these in the game before you build on them.

| AI | Open question |
|-|-|
| CatapultSpotter | Does the catapult search follow the spotter's facing (compendium) or step from its room's corner in a direction set by its upgrade-chain place (our notes, 1.14d routine)? |
| FetishShaman | Is its last step a circle walk 4 on aip4 (compendium) or a walk at the target (our notes, 1.14d routine)? |

Settled since the last update (each was a **differs** row here):

| AI | Answer | How |
|-|-|-|
| Mephisto | aip2 and aip3 are read (weakest enemy, then closest player); the aip3 branch is live only in D2R | the 1.14d routine `0x5f77c0` and the 93847 search `0x5b2370` |
| Summoner | aip6 spends a draw but never makes him back off: the cast or wander later in the same tick replaces the walk | the 1.14d routine `0x5f85c0`, step by step |
| PantherJavelin | aip3 is a squared distance (the PantherWoman's is in subtiles) | the 1.14d routine `0x5e1080`, the compare at `0x5e1213` |

None of the three was watched in the game.

Smaller points:

- Trap-RightArrow and Trap-LeftArrow: aip4 is stored but never read, although [monster-ai.md](monster-ai.md) lists
  it as a second delay.
- Diablo and BaalCrab: the chooser and its main rules are confirmed, but the single weights were not compared one
  by one.
- ReanimatedHorde: the rules for rising again (monster mod 34, aip1 and aip8, skill 293) come from the compendium
  alone.
- ZakarumZealot: the Act III rule that makes it run from a player with quest record 21 comes from the compendium
  alone.
- Every AI marked "classic notes" was checked in the classic 1.14d routine, not in build 93847. The numbers agree
  with the compendium's 3.3 reading, but a re-read in the current build would settle them.

AIs described from the compendium alone (not covered by our own disassembly notes): ElementalBeast, Tentacle,
TentacleHead, SandMaggotQueen, QuillMother, Trap-Missile, Trap-Poison, Trap-Nova, JarJar, InvisoSpawner,
MosquitoNest, Trap-Melee, 7TIllusion, DarkWanderer, ArcaneTower, DesertTurret, Spirit, TrappedSoul, MinionSpawner and
NpcBarb. Sarcophagus, FlyingScimitar, SiegeTower, Catapult, CatapultSpotter, GenericSpawner and Wussie are now
checked against the 1.14d routines.
