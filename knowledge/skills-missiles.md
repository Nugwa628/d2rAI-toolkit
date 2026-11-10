# Skills, missiles and mercenaries (D2R 3.3 server side)

How the D2R 3.3 server starts and runs a skill, what each vanilla skill function slot does and where it draws on the
seed, how a missile hits, how timed states from skills and hits behave, and how a mercenary thinks, picks skills and
takes items. Observed in **D2R 3.3 build 93847**; every RVA is for **build 93847 only** (image timestamp
`0x6ab3782c`) and must be re-checked in Ghidra on any other build ([engine-re.md](engine-re.md)).

Formulas (mana, delays, effective level, missile create, srvdmgfunc slots, auras, summons) are in
[combat-formulas.md](combat-formulas.md#7-skills-and-missiles); free table slots and function-number limits in
[engine-limits.md](engine-limits.md#skills); record layouts and helper RVAs in
[d2r-93847-addresses.md](d2r-93847-addresses.md). Monster AI in general is in [monsters-spawn.md](monsters-spawn.md).

Confidence: **high** = read in the binary and matched against a hosted game; **static only** = read in the 93847
binary, not run; **observed** = seen in live captures, cause not read; **unverified** = inferred.

## 1. Record fields the server reads

Fields not already in [d2r-93847-addresses.md](d2r-93847-addresses.md#record-skillstxt-748--0x2ec-bytes):

| What | Value (build 93847) | Confidence |
|-|-|-|
| skills force / no-block flag (alwayshit-like; skips block and dodge) | byte +0x29 | static only |
| skills srvoverlay | +0x6e | static only |
| skills startmana | i16 +0x224 | high |
| skills prgchargestocast | +0x22e | high |
| skills ItemTarget mode, 1-5 -> target masks `4`, `0x1c09`, `0x180`, `0x804`, `0x805` | byte +0x231 | static only |
| skills globaldelay formula | +0x234 | high |
| skills EType | byte +0x284 | high |
| skills restrict / State1-3 | +0x2d0 / +0x2d2 | high |
| skills SeqInput, sumumod formula, aura falloff floats | +0x36, +0x138, +0x1cc / +0x1d0 | static only |
| missiles flags: +0x25 bit 2 Town, bit 3 SrcTown | +0x24 word | high |
| missiles Range / Radius formulas | +0xbc / +0x1c8 | high |
| missiles Vel, VelLev, MaxVel (u8), Accel (i16) | +0xc0, +0xc1, +0xc2, +0xc4 | high |
| missiles Activate (u8) | +0x15e | high |
| missiles SubLoop, SubStart, SubStop, CollideType, Collision | +0x1a8..+0x1ac | high |

**Result flags** (skills.txt and missiles.txt `ResultFlags`, and the damage block's result word) keep the classic
values: 1 hit, 2 will die, 4 get-hit, 8 knockback, 0x10 block, 0x100 avoid, 0x200 evade, 0x2000 critical, 0x4000
soft hit.

## 2. Start and do

- **Start** `0x43b3e0`: needs a room, the record and (unless forced) mana. A skill without InTown used in town puts
  the unit back to neutral. srvstfunc below 0x5b indexes `0x238e710`; an empty slot counts as success. Mana is
  charged after a successful start unless `usemanaondo`.
- **Do** `0x43acb0`: re-checks town and the skill level, runs the srvdo slot, then fires `srvmissile` (straight
  `0x4333f0`, lob `0x432f90`). Firing counts as success. Then the charge (when not charged at start) and, for players,
  both delays.
- **srvmissile** `0x4333f0`: AtXY and TargetXY from the caster to the target unit, else the skill target point;
  monsters add their to-hit stat as the missile's attack bonus, players the skill's to-hit; `decquant` uses up
  quantity.
- **Normal attack** (srvdo 1, `0x42feb0`): adds progressive to-hit and the charge-ups' leech and elements
  (`0x56d050` on a hit, `0x56cbe0` after the weapon fill), and releases the charges unless the attacker has a state
  with a states.txt flag tested through `0x336280(unit, 0x27)` (column not identified).
- **srvdo 2** (`0x430240`, Kick, Bash, Stun, Power Strike, Impale and others): the caster's aurastate first, then
  the hit frame; srvoverlay and auratargetstate go on only when the queued hit lands. A hit frame that finds no
  stored block for this attacker and target lands nothing ([combat-formulas.md](combat-formulas.md#5-landing-the-hit)).
- **Forced result flags** `0x437000`: a record whose ResultFlags already hold the hit bit returns them without the
  range test or the to-hit draw; otherwise it passes byte +0x29 as the to-hit's force. Used by Fire Claws, Hunger and
  srvdo 142. **static only**.
- **Own weapon fill**: Dragon Claw (`0x572bd0`), Fire Claws (`0x577e10`), Hunger (`0x5767aa`) and srvdo 142's
  callback (`0x56e408`) fill with bTemporaryWeaponStats 0 and the record's SrcDam; the assassin kick (`0x573af0` /
  `0x574029`) fills with 1 and srcDam 0x80. They set SkipFill so AllocCombat does not fill a second time. A SrcDam
  byte of 0 means no fill at all here.

## 3. Monster per-attack stats inside skill slots

`0x588d50(unit, mode)` writes a monster's attack stats for a mode and draws on the **unit seed**: one `% 100` per
monstats El slot of that mode with a chance of 1-99, plus `% 5 + 1` for a random-element slot. Mode change
(`0x4475c0`) calls it for every mode but GH; five slots call it themselves in their monster branch:

| Slot | RVA | Skills | Mode passed | Where the draws fall |
|-|-|-|-|-|
| srvdo 7 | `0x557260` | Jab | S1 (8) | after the to-hit factor, before the to-hit roll, hit or miss |
| srvdo 67 | `0x5622c0` | Charge | class 73 -> S1; 211, 436 -> A2; else A1 | after the strike's elemental roll |
| srvdo 150 | `0x564930` | Smite | A2 (5) | first, before calc2 and the swing |
| srvst 40 | `0x569c60` | Leap, MonLeap | A1 (4), only with a target unit | before one swing through `0x588800`, then the jump |
| srvst 42 | `0x57ddd0` | Fire Hit | S1 (8) | only on a hit, after the to-hit roll; a player caster goes to `0x5695d0` |

Confidence **static only** (decompiles plus the slot tables). In vanilla only claw vipers, sand leapers and Duriel
carry El columns on those modes, so Act I never draws here. A plugin that replaces one of these slots must make the
same draws in the same place or that monster's later rolls drift.

## 4. Skill elemental roll and Smite

- `0x437d00`: one draw for max - min, a second (`& 3`) for EType `rand`. EType `cold` writes the chill length
  (block +0x118), `frze` the freeze length (+0x11c); both add cold damage (+0x34) with hit class 0x30. **static only**.
- **Smite** (`0x564930`), player branch: melee range (`0x348650`) and a shield (`0x385be0`) are checked first; with
  either missing nothing is drawn. Then the shield's damage (item record +0x10f / +0x110, `<< 8`) plus Holy Shield's
  when state 101 is on, calc1 enhanced damage, the physical roll (`0x44d710` -> `0x4501e0`, one draw), calc2 stun
  length, the conversion calc for an EType, the elemental roll, hit flags 2, and only then the to-hit and block rolls
  with the hit bit ORed in last. The monster branch has no elemental roll. **static only**.

## 5. Vanilla slot RVAs (build 93847)

Slots implemented and read; all others are in the tables at `0x238e710` (srvst), `0x238ea00` (srvdo),
`0x2390e80` (missile do) and `0x2391030` (missile hit). D2RCore holds srvst 64 and 66, srvdo 23, 58, 157 and 166,
missile hit 49 and srvdmgfunc 16.

| Table | Slot -> RVA |
|-|-|
| srvst | 1 `0x42f990`, 4 `0x558250`, 5 `0x558900`, 6 `0x558c70`, 7 `0x558590`, 8 `0x558ee0`, 9 `0x5583d0`, 10 `0x558930`, 11 `0x555300`, 12 `0x555440`, 13 `0x555640`, 14 `0x555270`, 15 `0x560430`, 16 `0x5601a0`, 17 `0x55ffb0`, 18 `0x213760` (shared stub, returns 1), 19 `0x55ff70`, 20 `0x55fff0`, 22 `0x572270`, 23 / 25 `0x571f10`, 24 `0x5721e0`, 26 `0x571af0`, 27 `0x571f40`, 28 `0x571ce0`, 29 `0x565240`, 31 `0x564dc0`, 32 `0x5695d0`, 33 `0x569bb0`, 34 `0x569c00`, 35 `0x565520`, 36 `0x565200`, 37 `0x5582a0`, 39 `0x569670`, 40 `0x569c60`, 41 `0x56a150`, 42 `0x57ddd0`, 46 `0x57dac0`, 49 `0x57e870`, 50 `0x57e9a0`, 56 `0x5779e0`, 57 `0x577f00`, 58 `0x577c90`, 65 `0x42fa40`, 66 `0x42fa90` (thunk), 67 `0x569c30`, 68 `0x569b20` |
| srvdo | 1 `0x42feb0`, 2 `0x430240`, 6 `0x556f70`, 7 `0x557260`, 8 `0x557840`, 9 `0x567e80`, 10 `0x556cf0`, 11 `0x5562b0`, 12 `0x557c10`, 13 `0x5568e0`, 14 `0x557510`, 15 `0x556560`, 16 `0x558000`, 17 `0x5533d0`, 18 `0x553e60`, 19 `0x554460`, 20 `0x5546b0`, 21 `0x554880`, 22 `0x553cf0`, 24 `0x553950`, 25 `0x553650`, 26 `0x553250`, 27 `0x554c40`, 28 `0x554560`, 29 `0x554cf0`, 30 `0x55c840`, 31 `0x55e440`, 32 `0x55cc50`, 33 `0x570d70`, 34 `0x570a70`, 35 `0x570650`, 36 `0x56df80`, 37 `0x56e070`, 42 `0x570420`, 43 `0x571400`, 44 `0x56f370`, 45 `0x5714e0`, 46 `0x56fb50`, 47 `0x56f750`, 48 `0x56f1e0`, 49 `0x5710c0`, 50 `0x570000`, 51 `0x570780`, 52 `0x56fc80`, 54 `0x56f610`, 55 `0x559dc0`, 56 `0x55c080`, 57 `0x55c3f0`, 59 `0x55ccb0`, 60 `0x55d1c0`, 61 `0x55d6b0`, 62 `0x55cf90`, 63 `0x55e2b0`, 64 `0x5645c0`, 65 `0x563d90`, 66 `0x5630e0`, 67 `0x5622c0`, 68 `0x567500`, 69 `0x567bd0`, 70 `0x5678b0`, 71 `0x568ad0`, 72 `0x567d00`, 73 `0x561f80`, 74 `0x567a20`, 77 `0x568690`, 78 `0x568910`, 79 `0x562990`, 80 `0x562ee0`, 81 `0x563720`, 82 `0x564260`, 85 `0x57d400`, 91 `0x57c590`, 92 `0x57cad0`, 97 `0x57cdf0`, 109 `0x5822b0`, 110 `0x57b120`, 113 `0x584b60`, 114 `0x576f70`, 115 `0x575bc0`, 116 `0x5775b0`, 117 `0x5764e0`, 118 `0x5772d0`, 119 `0x575990`, 120 `0x576160`, 121 `0x576bb0`, 122 `0x576630`, 142 `0x56ea10`, 144 `0x554130`, 150 `0x564930`, 154 `0x523db0`, 155 `0x521ec0`, 164 `0x521750`, 166 `0x522e70`, 167 `0x522cd0` |
| missile do | 1 `0x4550e0`, 2 `0x457e20`, 3 `0x458060`, 5 `0x458160`, 6 `0x458360`, 7 `0x458560`, 10 `0x458960`, 13 `0x458f70`, 14 `0x4592a0`, 15 `0x459380`, 16 `0x459550`, 20 `0x459b90`, 23 `0x45a080`, 25 `0x45a260`, 27 `0x45a540`, 30 `0x45b0b0`, 37 `0x454d30`, 41 `0x455690` |
| missile hit | 1 `0x45bc40`, 2 `0x45c720`, 3 `0x45cb70`, 4 `0x45ddb0`, 7 `0x45cb80`, 8 `0x45ce40`, 9 `0x45d020`, 10 `0x45d3f0`, 12 `0x45d5e0`, 13 `0x45d9b0`, 14 `0x45e0b0`, 15 `0x45e460`, 16 `0x45e610`, 17 `0x45e810`, 18 `0x45e9a0`, 20 `0x45ec20`, 21 `0x45ef10`, 22 `0x45f060`, 29 `0x45fb50`, 31 `0x45fee0`, 36 `0x460640`, 37 `0x4607d0`, 41 `0x456270`, 47 `0x460f10`, 48 `0x4612c0`, 53 `0x452910` |

## 6. What changed from classic, per slot

Read in the 93847 binary (**static only** unless marked). "Draws" are the slot's own seed draws, besides the combat
stages ([combat-formulas.md](combat-formulas.md)) and missile creation.

| Slot(s) | Skills | Draws | D2R behaviour worth knowing |
|-|-|-|-|
| srvdo 6 | Inner Sight, Slow Missiles | none | all six aurastats apply (classic one) |
| srvst 6 | Power Strike | caster elemental roll on a hit | runs without an EType too |
| srvst 7 | Impale | caster `% 100` wear draw (stack or durable weapon) | attack rate from Param9; mode event re-planned before the swing |
| missile do 2 | Poison Javelin | none | loops `2 * level - 2`; SrvCalc1 only gates the Loops flag |
| missile hit 4 | Exploding / Freezing Arrow | the submissile's hit | the submissile hits at once and is removed |
| srvdo 20 | Static Field | `& 3` per target for EType rand | |
| srvst 12 / srvdo 21 | Telekinesis | `% 100` knockback, physical, elemental | knockback chance is calc1; no busy-player test |
| srvst 11 / srvdo 19 | Inferno, Arctic Blast | none | monsters skip the mana check; a re-cast moves the list 7 frames on; the start no longer calls the do |
| srvdo 116 | Werewolf, Werebear | none | Param12 1 turns wolf into bear in one cast; a length <= 0 is permanent |
| srvdo 117 / missile do 23 | Firestorm | each walker's own seed | Charged Bolt-style walkers |
| missile hit 47 / 48 | Molten Boulder | missile damage roll | the impact runs without a target unit too; 18 fire offsets from a table in the image (6.1) |
| missile do 25 | Eruption | missile seed reset to `{x + frames left, 666}`, then x and y over `2 * (range - 1)` | |
| srvst 56 / srvdo 120 | Feral Rage, Maul | combat's | the start succeeds without a target |
| srvst 29 / srvdo 64 | Sacrifice | weapon roll, elemental; none on a miss | no melee-range test; hit flags OR 1; the caster keeps Param4 life |
| missile hit 7 | Holy Bolt, FoH bolt | missile: calc1..calc2 heal, then min..max damage when below 1 | ends after a heal only with sHitPar3 1 or 3 |
| srvst 37 / srvdo 13 | Zeal, Fend, Fury | elemental on a hit | start succeeds without a target; target filter 0x100003; melee offset 3 monster / 1 player; next target by guid |
| srvst 31 / srvdo 67 | Charge | elemental, also on a miss; then a monster's `0x588d50` mode draws | speed bonus is calc2 (1.14d: Param1); the run in 6.1 |
| missile hit 17 | Howl | none | fear from calc2 / calc3 |
| srvst 33 / srvdo 69, srvst 34 / srvdo 72 | Find Potion, Find Item | `% 100` chance, `% 100` kind; Find Item's drop draws on the corpse and game seeds | |
| srvst 68 / srvdo 70 | Double Swing | elemental on a hit | new start: an attack-rate list per skill |
| srvst 22 / srvdo 33 | Psychic Hammer | physical, elemental, `% 100` knockback when chance > 0 | |
| srvst 23 / 25, srvdo 34 / 35 | Tiger / Cobra Strike, Fists of Fire, Claws of Thunder, Blades of Ice | one elemental roll per charge state | start only resets the entry counter; hands alternate by that counter |
| srvst 24 / srvdo 42 | Dragon Talon | last kick: `% 100` knockback, kick roll, elemental | every kick in the do; release after the kick |
| srvdo 46 | Dragon Claw | combat's and the charges' | SkSrvDoFunc picked by the entry counter |
| `0x56c3e0` / `0x56bfe0` | charge release (srvdo 1, 42, 46) | `% 100` vs item_charge_noconsume, then one restored draw per charged state | a finisher uses up prgchargesconsumed charges (1 in vanilla) |
| srvdo 43 | Shock Web | caster seed reset to `{target x, 666}`, x and y per missile | |
| srvdo 44 | Blade Sentinel | none | AI command first value 11 |
| srvdo 47, 49, 81, 82 | Cloak of Shadows, Shadow Warrior/Master, Holy Freeze, Redemption | see below | 14 passive stats (classic 5) |
| srvdo 10, missile do 7, hit 10 | Guided Arrow, Bone Spirit | none | re-aim (`0x467640`) frame count = Range formula at level - 1; target delta packed `dy * 0x10000 + dx` |
| srvdo 11 | Charged Strike | Charged Bolt walk per bolt | bolts start at the target, heading for the point mirrored through it |
| missile hit 2 | Plague Javelin, poison potions | none | 16-point ring; count and speeds from sHitPar1-3 and sub-missile Param1/2 (FP8) |
| srvst 8 / srvdo 12 | Strafe | none | no shot without a target point; rewind `(100 - Param6)%` of the first event frame |
| missile hit 9 | Immolation Arrow | burst element roll on the arrow's seed | fire missiles go to clients in groups of five (`0x4640f0`) |
| srvst 10 / srvdo 14 | Lightning Strike | lightning roll on a hit with max > min | bolt aims at the next unit's x and y (classic used y twice) |
| srvdo 15 | Dopplezon | none | pet level stat = caster's before the pet level set-up |
| srvdo 25 | Enchant | none | target only when on the caster's side (`0x48e4d0`), else the caster |
| srvdo 26, missile hit 12 | Chain Lightning, Lightning Strike | none | fired through srvmissile; next target by guid (filter 0x88583) gets a new missile with one jump fewer; jump number `calc1 - jumps + 1` |
| missile hit 13 | Glacial Spike | EType roll on the missile | range / length from sHitPar1/2, else aurarangecalc / auralencalc |
| srvst 13 / srvdo 29 | Thunder Storm | none | puts stat 202 from unit +0x12c on the list |
| missile do 10 | Blizzard | seed reset `{x + frames gone, 666}`, x and y `% 2(calc1 - 1)` | |
| missile do 15 / 16, hit 29 | Frozen Orb | none | ring index `TargetX & 63`, advancing `(Param2 + index) & 63` |
| srvst 14 / srvdo 144 | Hydra | none | end = calc1 + frame; a hireling caster summons for its owner; three heads; the end frame goes in each head's AI p[0] (1.14d `0x5b0d70`), read by think 86 ([monster-ai.md](monster-ai.md#7-hirelings-and-summons)) |
| srvdo 61 | Confuse | `% 100` per eligible target whose curse list lacks this cast id | all six aurastats; resist cut to a fifth for base resist >= 100 (`0x55bfb0`) |
| srvdo 59 | Attract | none | length divided by difficultylevels AiCurseDiv |
| srvst 19 / srvdo 62 | Bone Prison | none | 12 pieces on a ring; none in a town room |
| srvst 35 | Vengeance | weapon roll on a hit | force from byte +0x29 |
| srvdo 73 | Blessed Hammer | none | concentration bonus (`0x33ae70`) uses the hammer's calc1 |
| srvdo 81 | Holy Freeze | elemental each pulse; `% 100` per target on its seed (shatter < 20) | per-frame throttle `0x432c40`; distance falloff `0x560f60` |
| srvdo 79 | Conversion | `% 100` only for a queued physical hit on an eligible monster | a failed convert lets the hit land |
| srvdo 80, missile hit 22 | Fist of the Heavens | none | item_damagetargetac lowers a monster's armour; bolt range / count from sHitPar1/2 |
| srvdo 82 | Redemption | `% 100` per corpse in range; none in town | mana spent only when something was redeemed |
| srvst 41 / srvdo 78 | Leap Attack | elemental on a hit | landing player sends a 64-missile ring (not in 1.14d); filter 0x100583; flight and landing in 6.1 |
| srvst 67 / srvdo 9 | Frenzy | elemental per swing on a hit | one swing per call through the action frames; later swings at the next unit in reach; frenzy stats only after a hit |
| srvst 39 | Berserk | elemental on a hit | no target: no swing, the state still goes on |
| srvst 57 / srvdo 121, missile do 30 | Rabies | elemental on a hit; missile do: `% (2 * Param2 + 1)` x then y every Param1 frames, caster's seed | length only on a hit; plague only when the state is new |
| srvst 58 | Fire Claws | elemental after the weapon damage | block queued on a miss too |
| srvdo 118, missile do 27 | Twister, Tornado | none | the missile's hit class goes on the pulse |
| srvdo 122 | Hunger | elemental after the weapon damage | hit frame runs on a miss too |
| srvst 26 / srvdo 48 | Blade Fury | none | list lasts 20 frames (classic 21); re-cast moves its end 7 frames on |
| srvst 27 / srvdo 50 | Dragon Tail | kick roll (none when max = min) | fire = `(calc1 + fire mastery)%` of the queued physical, to every unit around the target |
| srvdo 37 | Claws of Thunder 3-charge release | one caster draw on entry, then a local seed `{value, 666}` per missile | draws before its tests |
| srvdo 51 | Mind Blast | physical, elemental, `% 100` per target, `roll(Param4)` length on a win | chance calc1, length calc2 + roll |
| srvdo 52 | Dragon Flight | kick roll | teleport and kick alternate by the entry counter |
| srvst 28 / srvdo 54 / 142 | Blade Shield | physical, elemental | a swing at each aura target around the caster |

**Warlock** (3.3 class `war`): Ring of Fire srvdo 154 (missile pick `0x571700`, ring `0x435ae0`, count rounded up to a
power of two, 1-64); Death Mark srvdo 155 (pet callback `0x520930` clears stun, freeze and chill on pets); Cleave
srvdo 164 (`0x51ea00`, arc `0x3c4c50` / `0x3c4ce0`: a swing at every target in the arc); Miasma Bolt missile hit 41
and do 37 / 41 (pulse `0x466ea0`, one roll per element per pulse on the cloud's seed); Sigils missile do 14 (runs
srvdo Param2 as the missile every Param1 frames); Miasma Chains srvdo 167. Echoing Strike (srvdo 166, srvdmgfunc 16)
and Blood Oath (srvst 66) hand off to D2RCore. None of these draw on their own besides formulas and missile creation.

Not read in 93847: Revive (D2RCore), Grim Ward (srvdo 75, missile hit 26 / 27), Whirlwind (srvst 38 / srvdo 76,
`0x56a420` / `0x568f40`, hit delay `0x56ac00`), Volcano (srvdo 123), Armageddon / Hurricane (srvdo 124, hit 56), the
charge releases srvdo 38-41 / 143, Kick, Throw and Unsummon. Their classic bodies are in 6.1.

### 6.1 Slots read in classic 1.14d

These bodies were read in the classic **1.14d** Game.exe (with D2MOO for names) because the 93847 dump did not give
them. **Every address in this subsection is a 1.14d virtual address, not a 93847 RVA**; the slot numbers match D2R's
tables. A D2R change from 1.14d would not show here. Draws are on the caster's seed unless stated.

| Slots (1.14d) | Skill | Seed draws, in order | Behaviour |
|-|-|-|-|
| srvst 38 `0x5d8f50`, srvdo 76 `0x5d9580` (hits `0x5d9320`, end `0x5d9460`, next unit `0x56bd10`) | Whirlwind | per hit: the to-hit, then the elemental roll on a hit; a monster caster: one caster step per call for its hit count | the start refuses an Inferno caster (`0x5d8fbf`, not in D2MOO); sets run flag 1 and keeps the path end in param 0..1; uninterruptable and skill_move go on; the search reaches 5 subtiles but each swing lands with range offset 0, so a unit beyond melee reach is picked and not hit; the end (`0x5d9460`) returns before the aura part when the unit has no dynamic path, so the aurastate stays |
| srvst 31 `0x5cf6b0`, srvdo 67 `0x5cf900` | Charge | the elemental roll, then a monster's mode draws (MONSTER_RecalculateCombatStats, D2R `0x588d50`) | the run path goes from the unit to the target unit (else the point), no path type set, at the run velocity x (max(velocitypercent, 50) + bonus)%; a monster's path gets distance 20; kept frames 7, 15 for Duriel (class 211). Each run event steps the path, and srvdo 67 moves its mode change to the next frame; in melee reach it drops the path and rewinds to the attack frame index (`0x5cf8c0`: the first row with event 0, minus 1). A path ending short sets run flag 2 and the next do ends the skill. 1.14d's speed bonus is Param1 (D2R: calc2). No uninterruptable |
| srvst 21 `0x5c3350` (check `0x5c3300`), srvdo 58 `0x5c56c0` (life `0x6538a0`, percent `0x483360`, pet `0x575d90`) | Revive | one draw on the **corpse's** seed: `roll(maxHP - minHP + 1) + minHP` from monstats/monlvl at the corpse's level (expansion L- columns) | skill level 0 returns 0 (`0x5c56ff`); maxhp (stat 7) is set before hitpoints (6), both for the roll and after the percent; the corpse rises (`0x5c5430`), gets AI, owner and alignment (`0x5c55c0`), then its node and collision (`0x5b1900`, `0x649ca0`); class 3 (Paladin in 1.14d's numbering) pays a life cost (`0x5c2ff0`); debuff check `0x56e2f0` |
| srvdo 75 `0x5d89b0` (corpse test `0x645590`), missile hit 26 `0x5ab8d0`, 27 `0x5aba00` | Grim Ward (start is Find Potion's srvst 33) | none | the ward goes on a free spot of size 2, mask 0x1000, near the corpse; the missile by monstats2 size; srvdo 75 does not test corpse_noselect (srvst 33 refuses a used corpse) |
| srvdo 123 `0x5c8080`, missile do 28 `0x5afb80`, hit 51 `0x5ac870` | Volcano | do: one `roll(256)`, kept as target x; missile do, per spew frame: seed reset to `{target x, 666}`, x and y `roll(2r + 1) - r`, the new low word kept | the point must be free (mask 0x5); unit message `0x571aa0` to clients; the lob height (Param5) is client-only |
| srvdo 124 `0x5c8190` | Armageddon, Hurricane | one `roll(0x10000)`, kept in the entry's param 1 | the aurastate list lasts max(auralencalc, 1) with a RemoveState event at its end (`0x5c82f0`); an ActiveState timer at Param4 frames |
| srvdo 145 `0x5c8380` | Hurricane's tick (states.txt srvactivefunc) | physical roll, then elemental, once per tick outside town | any failure deletes the skill's ActiveState timers; in town 0, timer kept |
| srvdo 146 `0x5c8520`, missile hit 56 `0x5acc50` | Armageddon's tick and rock | tick: seed reset to `{param 1, 666}`, `roll(1000000)` as the next param 1, then up to five tries of x and y `roll(2R + 1) - R`; hit: none | a try needs line of sight (mask 0x805), a room and no player collision; rock radius sHitPar1, else aurarangecalc |
| srvdo 38 `0x5d3e80` | Blades of Ice / Fists of Fire, two charges | the swing's hit frame, then physical and elemental | area at the target point within prgcalc2 (else aurarangecalc), War Cry's damage callback; no point: 0 |
| srvdo 39 `0x5d3f90` | Blades of Ice / Fists of Fire, three charges | one caster step before any test; local seed `{that low word, 666}`: r squared tries of x then y `r - roll(2r)` | srvmissilec only inside the disc and in a room; 1.14d does not cap r |
| srvdo 40 `0x5d5010` | Royal Strike, one charge (meteor) | none | the charge-up missile at the point |
| srvdo 41 `0x5d5080`, missile do 35 `0x5b0640` | Royal Strike, three charges (chaos ice) | do: one caster step; local seed `{low word, 666}`, x then y `step % 40 - 20` per missile; missile do every Param1 frames: seed reset to `{target x, 666}`, one step | (0, 0) becomes (20, 0); the missile adds a quarter of the offset's perpendicular, one way on an even step and the other on an odd one (truncated toward zero) |
| srvdo 143 `0x5d4f40` (ring `0x5d4870`, spiral `0x5d4680`) | Royal Strike two charges (chain lightning), Fists of Fire one charge | one caster step; local seed `{low word, 666}`, one step per ring missile | ring k = 0, stride, ... below 64 at radius 30, spiral init; a stride <= 0 loops forever in 1.14d; Fists of Fire has no srvmissilea, so its release is the caster step alone |
| srvst 2 `0x56caf0` (+ srvdo 2) | Kick | none | a fixed blow MinDam << HitShift, result 9, hit flags 2, hit class 1, source damage 0x80 |
| srvst 3 `0x56cba0`, srvdo 4 `0x56cc20` | Unsummon | none | target must be the player caster's pet with pettype unsummon; the do drops the pet link, sends the 0x7a pet message, removes the monster at frame end |
| srvst 65 `0x56cab0`, srvdo 3 `0x56f460`, 5 `0x56f550` | Throw, Left Hand Throw | missile creation's | the start checks ammunition; a potion flies as a lob; the item's to-hit and damage percent go on the missile |
| srvst 41 `0x5da540`, srvdo 78 `0x5da7e0` (path `0x5d9f70`, landing `0x5da120`, Sand Leaper run `0x5da020`) | Leap Attack's flight and landing | none | below |
| missile hit 47 `0x5ac550`, hit 14 `0x5aabb0` (burst `0x5aaa90`) | Molten Boulder's and Meteor's fire | none of their own | HitSubMissile1 at every sHitPar2-th (at least 1) of **18 fixed offsets** (x `0x6e2550`, y `0x6e2598`, no formula; D2R's copy at `0x1d1b370` / `0x1d1b3c0`, read by `0x467420`, not compared value by value); Molten Boulder's pieces last the impact radius in frames, Meteor's Param3 + (level - 1) x Param4 |
| srvdo 147 `0x5d2b20` (dispatch `0x56d790`, owner `0x552fd0`) | states.txt srvactivefunc of "attached" (Imp) | none | only with unit flags_ex 0x400 (StoreOwnerInfo; set by `0x621c30`, cleared by `0x621ce0` without owner) the unit's path moves onto its owner's position (path flag 8 when the subtiles differ, 1 when the rooms differ); returns 1 either way. The attach is srvdo 129 `0x5d1ab0` -> `0x5d18e0`: an imp (base 0x1ec) on a host of base 0x1b3 / 0x1b9, each the other's owner, the imp's ActiveState timers reset and one fired at once (`0x541650`) |

**Finishing-move releases** are not srvdofunc values but a charge-up's srvprgfunc1..3, run through the srvdo table
with the charge count in aurastat1: Fists of Fire 143 / 38 / 39, Blades of Ice - / 38 / 39, Royal Strike 40 / 143 /
41.

**Moving skills (run flags).** Whirlwind, Charge and the leaps keep run flags in the skill entry: flag 1 = running,
2 = the path has ended. While flag 1 is set, `PLRMODE_UpdateSkill` (`0x580460`) steps the path once per mode-change
event of the skill mode and calls the do on every such event whatever its animation flag; it only revalidates the
path's target (`0x553490` drops a unit no longer in the game), so a target that leaves does not stop the do. The do
rewinds the animation to keep the mode running (Whirlwind to frame 3, see
[movement-modes.md](movement-modes.md#75-skill-loops-and-the-rewind-114d)); when the path reports its end, flag 2
goes on and the next do ends the skill. Whirlwind's row 7 do returns 0. The skill_move state the start sets is cleared
only by the **client's** 0xA5 handler (`0x45d6b0`), never by the server. A replacement that sets flag 1 without
stepping the path leaves the uninterruptable state on and the unit can no longer act.

**Leap Attack** (1.14d). srvst 41 keeps the landing in param 0..1, reserves it with the unit's pattern and 0x80, and
sets run flags 0x1080 (monsters too; D2R starts a monster's path at once). The landing is SKILL_FindTeleportPosition
(`0x645ca0`): none for a target in melee reach; two subtiles past the aim along caster -> aim; a colliding landing
(0x1c09) takes a free spot near the aim; then line of sight (mask 0x804) and no town room (`0x645c60`). srvdo 78 with
flag 0x80 lays the path (`0x5d9f70`: footprint lifted, collision pattern 0, path type 9 Leap, straight, at the run
velocity) and sets 0x1101 before the landing test; 1.14d returns 1 whatever the path call does. Each later srvdo 78
(flag 0x100) compares the position with the landing, which must match **exactly** on both axes: off it, a player's
animation goes back to frame 10, a monster's sequence frame to 0 (Sand Leaper 8; Ancient Barbarian 12 within 2,
else 10) with its frame count one frame on; on it, the landing (`0x5da120`) frees the 0x80 reservation (0x100 for a
monster, whose 0x80 stays), restores collision type 0x80 / 0x100, pattern 0x1c09 / 0x3c01, path type 7 / 2, sets
run flags 0x200, and a Sand Leaper runs on to `3 x landing - 2 x target`. Both landing branches clear uninterruptable
(`0x5544b0` at `0x5da2c7`) before the 0xA5 skill stop (`0x571b70`); then AcquireNextTarget (`0x5da490`, no alive
check) and a rewind to frame 16. **1.14d sends no missile ring on landing**; D2R's 64-missile ring for a landing
player is a D2R addition. A death blow in flight does not stop the landing (see the death delay in
[combat-formulas.md](combat-formulas.md#5-landing-the-hit)). Leap (srvdo 77, `0x5da370`) shares this path and landing.

## 7. Missile hit handler (`0x4639a0`)

D2R's order, **static only**:

1. NextHit test through a **per-game table** (`0x324a50` on game +0x1aa80, keyed by the missile's skill and the
   target), where classic used the justhit state.
2. A per-missile list of units already hit (`0x3be5b0` / `0x3bd580`).
3. Collide-mode type test; hostility unless CollideFriend.
4. Pierce: the missile's Pierce flag, then the owner's stat 166 layer 0, stat 156, or 166 layer 0x30; the missile's
   own pierce_idx (stat 328) is spent while nonzero.
5. ToHit missiles roll to-hit (`0x44ba60` as a missile, the missile's stat 19). A miss fires unit event 0 on the
   target and queues an 0xad miss record from the owner (`0x538f00`).
6. The hit slot; with result bit 2: the damage roll (`0x465420`), the srvdmgfunc slot, the hit itself (`0x462e40`,
   [combat-formulas.md](combat-formulas.md#5-landing-the-hit)), then a monster target that is not a hireling gets the
   missile's stat 120 added to its defense (floor 0).

**Classic 1.14d for comparison** (1.14d addresses):

- **LastCollide** (arrows, javelins, the bolts, Bone Spear, Holy Bolt): the unit-find callback skips the unit the
  missile hit last and searches on (`0x5a8730`); the hit handler (`0x5adf10`) remembers the target before the
  collide-mode test, in the missile's owner pair (SetOwner `0x64a400`, CheckUnitIfOwner `0x64a470`). A missile that
  flies on hits a unit once while passing, and two units on one subtile in turn.
- **NextHit** (`0x5adb00`): justhit goes on the target for NextDelay frames and the target's RemoveState event (type
  12) is set at that frame (SetEvent `0x5417d0`). The event runs after the missiles of its frame
  (EVENT_DispatchAllTimers `0x5414d0`, missiles queue first), so a missile that stays on the unit hits it every
  **NextDelay + 1** frames (Volcano: every 11).

Around the damage stage D2R switches the owner's current skill to the missile's skill (`0x3e2b652`). The frame
dispatcher `0x466ce0` kills SrcTown missiles when the owner is in town and other missiles in town unless Town; a do
result of 2 removes the missile. A check through `0x3bcd00` / `0x467150` there is not identified.

The cast id at unit +0x12c is handed to missiles (MissileParams +0x68), summons and state lists
(modifierlist_castid); Confuse and Poison Explosion use it so one cast affects each target once.

## 8. Timed states from hits and skills (`0x433d20`)

`0x433d20` is a D2RCore thunk; the vanilla body is at `0x433d26`. Open wounds and prevent-heal use it with skill 0,
level 1 and one stat; many skill buffs and curses go through it too. **high** for the open-wounds path (matched on
thousands of live cases), **static only** otherwise.

1. A monster target whose monstats row is npc (record byte +0x3d bit 0) or whose monstats2 row is not isAtt (byte
   +5 bit 1) gets nothing.
2. Curse states: curse resistance (stat 109) of 100+ refuses, otherwise shortens the length by that percent; the
   Attract state (57) refuses every curse.
3. Existing list (the target's curse list for a curse, else the state's list): same state, skill and level only moves
   the expire frame to `frame + length` and keeps the old stats; a lower level is refused; anything else is freed.
4. Otherwise the state bit goes on and a new list is attached (curse flag, timed when the length is nonzero, skill,
   level, the stat).

So a second, stronger open wound only restarts the 200 frames and keeps the first wound's regen loss (classic rewrote
it). Remove callbacks on state lists (Inferno's end, a timed-out shapeshift, Confuse, Conversion, Blade Fury,
`0x436240`) were not read.

**When a timed list ends** (1.14d, addresses 1.14d's): a monster's timed lists are freed only when its RemoveState
event (type 12) fires (monster event slot `0x5a7ef0` -> RemoveStatListAfterTimeout `0x627460`); a player's expire in
the vitals pass. So every path that puts on or moves a timed list also sets that event at the list's end frame: the
state helper (SetStateWithExpiryTime `0x56e970`, new or refreshed), srvdo 2's auratargetstate (`0x56f1f0`), missile
hit 16 (`0x5aae10`), Rabies (`0x5c7b10`), Frenzy (`0x5d8d9e`), Berserk (`0x5d99e5`), the charge-ups (`0x5d3403`),
Blade Fury and Inferno on the new and the moved list (`0x5d6a99` / `0x5d6af8`, `0x5c8eff` / `0x5c8f6f`), a timed
shape (`0x5c6fe0`), Feral Rage / Maul (`0x5c7926`), Armageddon / Hurricane (`0x5c82f0`), Conversion (`0x5d04dd`) and
NextHit's justhit (`0x5adb00`). Setting it (SetEventInternal `0x5416b0`) replaces only the unit's type-12 timer. A
plugin that adds a timed list to a monster without that event leaves the stats on for good.

**Corpses**: the selection check is `0x33fbe0` (dead mode, no undead-mask state, monstats2 CorpseSel). It has no
Velocity test, so a corpse Raise refuses can still be exploded. Corpse Explosion marks the corpse used.

## 9. Mercenaries in the world

The think, its helpers and C->S 0x51 for a hired unit. Hire list, hireling.txt stats and experience are in
[combat-formulas.md](combat-formulas.md#10-monsters-and-hirelings).

### 9.1 The Hireable think (`0x5beac0`)

monai 61 for every hireling class, special state 4; every draw is on the merc's seed. **high** for the town
branches (7,786 live thinks of an Act I and an Act II merc matched draw for draw), **static only** for Combat,
Select and the warp.

- No owner (`0x4a53c0`) or the owner is not a player: special state 6 (5 when aip1 is 1), idle 10. Nothing brings
  it back to 4.
- Clears Inferno (state 0xc). While walking or running, the walk finishes first.
- Leash: near 16, far 24, steps 5; with p0 in 17-19, near p0, far 2p0, steps p0 >> 1. Distance is `0x596720`.
  - over 100: warp next to the owner (PetMove 3);
  - over far: catch up along the owner's trail (PetMove 1, run, speed 60);
  - over near: follow (PetMove 0), walking or running as the owner does.
- Not neutral: drop the AI event, idle 5.
- Stuck (collision mask 0x40 at its own spot) and `(draw & 0x7f) < (aip1 == 2 ? 6 : 12)`: walk close.
- Out of town, a target from `0x595f80` closer than 25: Combat.
- Another area than the owner (area id at a room point, so two areas of one level differ): run to the owner.
- Within 2: PetMove 5. Else one roll, `< 5`: walk to the owner; else idle 5. A merc beside a still owner spends one
  draw per think and idles 95% of the time.

**Combat** (`0x5bf6e0`): chance `98` for melee (aip1 2), else `min(p0 + 40 + 2 * level, 95)`. One draw; a failure adds
10 to p0 (p0 doubles as the leash value), a success clears it. Melee mercs run to the target when not in melee
range; ranged ones within 3 draw again and back off (walk to owner, else escape `0x4a7df0`) half the time.

**Select** (`0x5bf070`): the hireling.txt row at the merc's level (`0x3965d0`); `acc = DefaultChance`, then for up to
six skill slots, stopping at the first empty one: skip a skill at level 0, an aitype 1 skill whose aurastate every
buff target has, an aitype 2 skill whose auratargetstate the target has, and Inferno (41) beyond `level / 2 + 4`;
else `acc += Chance + (level - row.Level) * ChancePerLvl / 4`. `RollLimited(acc + 1)`: at or above DefaultChance
the first slot whose running total reaches the roll is used (buffs at the buff target, auras become the right skill,
idle 10); otherwise the basic attack (aip2 > 0: that skill in its mode, else mode aip3) when ranged or in melee range.

**Buff target**, in order: the owner (TargetAlly), the merc, party members (`0x506f90`, TargetAlly), then the
owner's and party members' attackable monster pets (TargetPet), the first that lacks the aurastate.

**PetMove** (`0x5c1460`) cases: 0 tries 8 points 8 subtiles around the owner's path point (`0x596b40`) in a sector
order from table `0x1d40c58`; 1 walks back through the owner's 20-point position history (player data +0x110 index,
+0x120 points), speed `% 15 + 50` when 0; 2 wanders; 3 warps (`0x596d40`); 4 escapes when other pets crowd within 2;
5 walks to the owner. `0x4a7510` (run to target) sets the step count to MeleeRng and then to 1; the second write wins.
`0x4a84e0` ignores its target and wanders around the unit itself.

### 9.2 Stats on the unit (`0x544f60`)

Written in this order: level (12), next experience (30), experience raised to the level minimum (13, signed compare),
strength and dexterity, max life then life (a level-up refills it), defense (31), stats 0xb9 / 0xba, attack rating
(19), resistances 39 / 43 / 41 / 45, regen 74, then each row skill through `0x433910`, which **sets** a level rather
than adding one. Select skips a not-yet-learnt skill but still counts its slot.

### 9.3 Giving items: C->S 0x51 (handler `0x4c0e20`)

17 bytes: @1 the player's item (-1 none), @5 the merc's guid, @9 the merc's item (-1 none), @13 body location (11+
becomes 0); not both none. **static only**.

- Both items come out, the given item's requirements are checked on the merc (`0x36bc50`), both are placed
  (`0x471500`); any failure puts everything back. Failed requirements send merc event 0x5d (a sound); a bad location
  or a duplicate id 0x5c.
- Success sends, in order: 0x42 (item leaves the cursor), 0x9d Unequip of the taken item (owner type 1, the merc's
  guid), 0x9d Equip of the given item, 0x9c ToCursor of the taken item, merc event 0x5e, the player's stat refresh.
- Placement runs the main-weapon insert rule on the given item, again on each changed arm, then the item refresh
  `0x470ca0` on the merc (unit flag 0x200): every worn item that is not broken comes off and returns in location
  order while its requirements are met, round after round. Requirements read the merc's strength and dexterity with
  the items already back, so a helm needing the gloves' strength returns a round later. The handler clears the
  no-equip flag (`0x36d8f0(item, 0x4000, 0)`) only after that.
- Afterwards the merc is marked for a stat refresh (`0x34dbe0(merc, 1)` only sets the flag) and its event 3 is
  re-armed for the next frame.
- **Left-hand component**: an item at location 4 or 5 shows as component 6 (left hand) when both arms hold usable
  items (`0x36ad20`) and neither is two-handed, the class test `0x348510` passes (a hireling, `0x3aecf0`, whose
  monstats `rightArmItemType` +0x80 and `leftArmItemType` +0x82 both read as weapon type 0x2d through `0x373c40`; 0
  reads as weapon, an empty column loads as -1 and fails), and it is not the main weapon (`0x387690`). In vanilla
  only `act5hire1` (swor / swor) passes. The merc's main-weapon guid is at its inventory +0x38.
