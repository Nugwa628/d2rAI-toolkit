# Combat and character formulas (D2R 3.3 server side)

How the D2R 3.3 server computes hit chance, damage, mitigation, experience, regeneration, skill costs, missiles and
hirelings: what a table change does to the numbers, and which function to hook when the table can't express it.
Observed in **D2R 3.3 build 93847**; every RVA below is for **build 93847 only** (image timestamp `0x6ab3782c`,
[d2r-93847-addresses.md](d2r-93847-addresses.md)) and must be re-checked in Ghidra on any other build
([engine-re.md](engine-re.md)).

Confidence: **checked** = the formula was compared against the game's own function on thousands of generated cases
in a hosted game; **read** = read in the 93847 binary, not run; **classic** = the classic 1.10-1.14 logic, the D2R
function was not read; **unverified** = inferred, not proven. Unlabelled lines are **read**.

Calc codes, formula functions and scopes are in [engine-limits.md](engine-limits.md#calc-codes-and-formulas); the
compiler and evaluator details are in [section 9](#9-formula-engine-details) here.

## 0. Conventions

- **Fixed point.** Life, mana and stamina (stats 6-11) carry 8 fraction bits (`40 << 8` = 40 points), in stat lists,
  in the damage block and in the damage fields. Elemental damage from skills carries 8 bits too.
- **Seed.** A unit's seed is at unit +0x28 (`{lo, hi}`); one step is `lo*0x6ac690c5 + hi` (64-bit, low word becomes
  `lo`, high word `hi`). A roll of `n` draws nothing for `n <= 0`, else masks for a power of two and takes `lo % n`
  otherwise. Every roll below is on the attacker's seed unless it says defender or missile. A plugin that rolls on
  a unit's seed changes every later roll of that unit.
- **Percent helper.** The engine's percentage (`v * p / 100`) switches order by size to avoid overflow: for
  `v > 0x100000` it divides first (`(v / 100) * p`), for `p > 0x10000` it computes `v * (p / 100)`. Large values lose
  precision; that is engine behaviour, not a bug in your data.
- **32-bit wrap.** Stat arithmetic wraps as signed 32-bit. Division by a zero data column returns 0 or the dividend
  depending on the helper; it never traps.
- **Edition byte.** `game + 0x106` (the "dataFlag", build 93847): 1 classic, 2 expansion, 3 the 3.x (RotW) edition.
  Several rules below switch on it (classic resist penalty column, phys/magic pierce stats).

## 1. Hit chance

**Attack rating** of a player (`0x3483c0`, build 93847): `tohit + 5 * (dex - 7) + charstats ToHitFactor`.

**Defense** (`0x347b10`): `armorclass + dex / 4`, plus `(item_armor_percent + skill_armor_percent)%`, plus Holy
Shield's term, then the armor-override percent (stat 182).

**To-hit roll** (`0x44ba60`): one `% 100` draw on the attacker.

- The defender's defense adds stat 32 against missiles or 33 against melee.
- Player attackers: ignore-target-defense and fractional target defense (stat 116; **half** against bosses,
  superuniques, hirelings and players), demon/undead to-hit, the to-hit-vs-montype layers, weapon mastery to-hit
  and stat 119 (percent): `AR = to_hit * (100 + pct) / 100`.
- Monster attackers: `stat 19 + 5 * dex + skill to-hit`.

```
factor = 100 * AR / (AR + max(defense, 0))
chance = clamp(2 * factor * attackerLevel / (attackerLevel + defenderLevel), 5, 95)
```

- **Weapon mastery** (stats 342-344 melee, 345-347 throw): layer bits 14-15 tag "dual-wield only" / "not
  dual-wield"; item type 0 matches anything; at most 32 layers read. D2R starts the maximum at INT_MIN, so a
  mastery that is negative everywhere stays negative (classic clamped to 0).
- A player hit with stat 117 (prevent monster heal) on a monster without `nopreventmonsterheal` gives state 52 for
  120000 frames.
- **Auto-hit**: a player defender who is running is always hit.
- The melee range check uses the attacker's range plus 3 for monsters, 1 for players.

## 2. Block, dodge, avoid, evade

**Block rate** (`0x347da0`), capped at **75**, no floor:

- player with a shield: `ToBlock + charstats BlockFactor`, in expansion games times `(dex - 15) / (2 * max(level,
  1))`;
- monster: monstats `ShieldBlockOverride` 1 = always `ToBlock`; 0 = `ToBlock` only while it carries a shield; any
  other value = never blocks.

**Order** (`0x44b930`): a player who is running (or walking in town) blocks at a third. With a block rate above 0
the defender draws `% 100`. Otherwise (`0x44ef70`): a walking or running defender can only **evade** (stat 340);
a standing one tries **weapon block** (stat 348, only with two claws), then **dodge** (338) or **avoid** (339). The
first success stops the chain and requests the skill mode named by stat 350.

A forced skill (skills.txt flag at record +0x29) skips block/dodge. GetHit is set unless the defender has state 54.

## 3. Damage pipeline

`FillDamageValues` (`0x44c030`, **checked**). `srcDam` is a **byte**: only the low 8 bits count, and 0 means 128
(= 100%). This runs on a **miss too**, so its rolls are spent either way.

1. Against monsters: damage-target-defense (permanently lowers the defender's defense), demon and undead damage
   percents (+50 for blunt weapons vs undead), damage-vs-montype layers.
2. **Physical**: weapon min/max (two-handed values for wield type 2; 1-2 bare-handed), stat 111 and flat min/max,
   **enhanced damage** = stat 25 + the weapon's str/dex bonus columns + mastery (bare hands add strength), floored
   at **-90%**, then min/max percent stats, then `srcDam / 128`.
3. **Critical chain**, at most one success: mastery crit (not with temporary weapon stats), passive crit (337),
   **deadly strike** (141). A crit **doubles physical only** and sets result flag 0x2000.
4. Fire, lightning, cold, magic: `min + roll(max - min)` with the element's mastery percent, then `srcDam`.
5. Plain monsters: life/mana/stamina drain rolls (added once; classic added them twice). Players and hirelings:
   the **leech** percents (stats 60 life, 62 mana).
6. **Poison**: one roll per poison source the attacker carries (each item stat list with poison stats is a
   source), then the classic aggregate roll; length from stat 59 split by the poison count, or the override length
   (stat 101).
7. Cold length (56), stun length (66).
8. **Burn** (D2R quirk): the code passes the stat *ids* 316/317 where it means their values, so it always draws once
   and adds `max(srcDam*burn + 316, 0) / 128`. Burn length adds stat 315.
9. Event 3 (attacked in melee) on the defender for player and hireling attackers.
10. Damage **conversion** (type at block +0x149, percent at +0x14c); type 10 (random) draws `% 5 + 1`.
11. **Monster crit**: `(u8)(% 100) < monstats Crit` doubles the six damage fields (not burn).

Skill elemental damage (`0x437d00`): `EMin + roll(EMax - EMin)`; EType `rand` (10) draws `& 3` to pick fire,
lightning, cold or poison (length 50 if none).

**Missile damage** (classic order, missile's seed): physical, fire, magic, lightning, cold, poison, burn, each one
draw when both bounds are positive; then damagepercent (+demon/undead percents, floor -90), deadly strike doubling,
then the missile's **srvdmgfunc** (section 7), and only after that the knockback draw.

## 4. Mitigation

`CalculateTotalDamage` (`0x44df10`, **checked**, no rolls). In order:

1. Flat reductions: stat 34 (damage reduced by) and 35 (magic damage reduced by), `<< 8`, scaled by the missile
   damage rate.
2. difficultylevels **unit-kind percents** on every positive field: player vs player / mercenary / prime evil,
   mercenary vs player / mercenary / boss, prime evil vs player / mercenary / pet, pet vs player.
3. Event 11 (absorb) on the defender.
4. Cannot-be-frozen (153) zeroes cold and freeze length; half-freeze (118) halves them; the antidote/thawing shrine
   states zero poison and burn length.
5. For each of the 12 fields (phys, fire, lightning, cold, magic, cold length, freeze length, poison length, poison,
   and life/mana/stamina leech for monster attackers):
   - **sunder** (monster defenders, not hirelings): when `resist + negative sources > 95` the resist becomes
     `max(that - sunder, 95)` before the negative sources come off again (repeated five times for sunderfull states
     while the base resist is 100+);
   - **pierce**: skipped when a monster's resist is 100+ (immune); else `resist - sources - pierce`; sources from
     sunder-res-reduce states count a fifth against a base of 100+;
   - **difficulty penalty** for non-monsters on every resist except stats 36/37 (difficultylevels ResistPenalty, or
     ResistPenaltyNonExpansion when the edition byte is 1);
   - **cap** for positive player resists: `min(75 + max-resist stat, 95)`, **50 for physical**; floor **-100**
     (ResistFloor);
   - then `damage * (100 - min(resist, 100)) / 100`;
   - absorb percent (capped at **40**) and flat absorb, both into healed life.
6. Total = the six damage fields; event 16; a monster attacker's life leech is added.

The phys and magic **pierce stats (366, 358) only exist in edition 3**. Sanctuary on the attacker zeroes an undead
defender's physical resist.

## 5. Landing the hit

**Hit frame** (`0x44b2b0`) and **ExecuteEvents** (`0x44ce80`): events 5 and 1 (melee) or 6 and 2 (missile) fire on a
successful hit; then leech, absorbed life, life/mana/stamina loss (life below one point becomes 0), stun (champions
and uniques: one roll, 90% shorter), cold (a defender `% 100 < 20` **shatter** roll on every cold hit), freeze,
poison, burn; then events 10 and 9.

- **Leech** (`0x450c90`): one coin-flip draw when a player has both life and mana leech.
- **Crushing blow** (events.txt function 16, `0x583150`): `% 100 < stat 136`. Divisor: players and hirelings **10**;
  monsters **4**, bosses and superuniques **8**, plus the players-count HP bonus percent; missiles double the
  divisor. `cb = life / divisor`, reduced by `min(DR%, 100)`; life floors at 0.
- **Open wounds** (function 15, `0x584170`): `% 100 < stat 135`. Regen loss by attacker level: 40 at level 1, then
  `9L+31`, `18L-104`, `27L-374`, `36L-779`, `45L-1319` in 15-level bands. Players take a quarter (an eighth from
  missiles), champions and uniques half. State 62 for 200 frames with stat 74 = -loss.
- Item event functions run **newest-registered first** on a unit. Which of crushing blow and open wounds rolls
  first when an item has both is **unverified**.
- **Hit recovery** (`0x44a9b0`): a player reacts only when not already in a hit-react mode and `stat 183 + buffer <=
  frame`, where the buffer is difficultylevels' hit-react buffer (vs player / vs monster) and stat 183 is written when
  the player *leaves* get-hit, block or knockback. Then `0x44fba0` makes two defender draws (`& 1` then `& 3`) to
  decide whether hit recovery is skipped. **Frame counts for FHR/FCR/IAS breakpoints were not documented here.**
- **Durability** (`0x4526c0`): `% 7`, then a weighted pick over the armor slots.
- **S->C 0xad** (12 bytes, builder `0x477720`) reports each attack: attacker and defender ids, types, success, crit,
  damage as a 0-255 fraction of max life, hit class.

## 6. Experience, levels, death

**Monster experience** at creation: `monlvl XP[difficulty][level] * monstats Exp[difficulty] / 100` plus the
players-count bonus. Normal uses the monstats level; Nightmare/Hell use the area's MonLvl(Ex) unless `noRatio` or
boss. The `L-` (ladder) monlvl columns follow the game-type byte (`game + 0x101`), **not** the edition byte.

**Players count** (`0x542f40`, only alignment 0): HP% and exp% = `{0, 0, 50, 100, 150, 200, 250, 300, 350}[n]` up to
8 players; from 9, HP% `(n - 2) * 50`, exp% `(n + 26) * 10`. Monster damage and to-hit outside Normal with 2+ players:
`x += x * m / 128`, `m = 8 * (n - 1)` up to 8 players, `8n - 16` above.

**Award** (`0x44ece0`, **checked**): xp clamped to `0x7fffff` (below 1 gives 1); 0 at or above the class MaxLvl.
Level-gap factor (`0x44fef0`, in 256ths, gap capped at 10):

| Gap | 0-5 | 6 | 7 | 8 | 9 | 10 |
|-|-|-|-|-|-|-|
| monster below the player | 256 | 207 | 159 | 110 | 61 | 13 |
| monster above the player (player level <= 24) | 256 | 225 | 174 | 92 | 38 | 5 |

Above level 24, against a higher monster: `xp * playerLevel / monsterLevel`. Then experience.txt ExpRatio
(`xp * ratio >> 10`), then stat 85 (+% experience).

**Party split** (`0x44a3b0`): members alive, within 80 subtiles, at most 8. `pool = xp + (89 * (n - 1) * xp >> 8)`
(64-bit), `share = level_i * pool / levelSum` in double (classic used float and 32 bits). If no member qualifies,
**nobody** gains. The player's mercenary gets its own award first, `* 86 / 256` unless it landed the kill.

**AddExperience** (`0x44f2e0`): u32 add capped at the threshold of MaxLvl - 1; level change calls LevelUp
(`0x52da30`).

**LevelUp**: per level, base maxhp/maxmana/maxstamina + charstats per-level **quarter points**, life/mana/stamina
refilled (life only while above 0), stat points + `StatPerLevel`, skill points + **`SkillsPerLevel`** (a D2R
charstats column; classic hard-coded 1). Item ops that scale per level (op 4/5) are refreshed by re-posting items.

**Death** (player, `0x429d10`):

- **Gold**: `penalty = min(level, 20) * (gold + bank) / 100`. Against a monster the carried gold above the penalty
  drops as a pile; a shortfall comes from the bank (not in single player; a result outside 0..max bank resets the
  bank to 0). Single player keeps `500 * level`. Stat 175 = the penalty.
- **Experience** (`0x424ac0`, above level 1, not killed by a player): `loss = (thr(L) - thr(L-1)) * DeathExpPenalty
  / 100` (difficultylevels: 0/5/10%). Clamped at **exactly** `thr(L-1)` (classic `+1`). Computed in u32 without a
  guard: a save edited so that experience is below the loss wraps to near 2^32 (top level).
- **Corpse**: holds the equipped items and **75%** of the lost experience, returned on pickup. Pickup range is 8
  subtiles with a path test; 9-50 is refused silently. Respawn (C->S 0x3f) is refused in hardcore.

## 7. Skills and missiles

**Effective level** (`0x3400a6` body, build 93847): base + bonus, clamped to 0 and to experience.txt MaxLvl (99). Bonus
= entry bonus + 2 under the skill shrine (state 134) + all skills (127); for players (and monsters standing in for a
class): class skills (83), skill tab (188, layer `page - 1 + 8 * class`), and `item_nonclassskill` (97) capped at 3
for own-class skills, uncapped otherwise; then elemental skills (126, layer = EType) and single skill (107). Item
(charge) skills don't take the bonus.

**Usability** (`0x33f360`, reason codes): 3 not InGame, 7 level 0, 6 aura, 5 passive, 2 bad weapon (itypea/itypeb,
quantity for scrolls and throws), 1 no mana, 4 restricted (`restrict` and State1-3), 1 below `startmana` (unless
state 12), 2 no charges, 8 delay, 4 finisher without enough progressive charges.

**Mana** (`0x33aa00`, **checked** 12,870 skill/level pairs): `((max(L - 1, 0) * lvlmana + mana) << manashift)`,
raised to `minmana << 8`; 8 fraction bits. The "enough mana" test (`0x340900`) uses the same sum **without** the
minmana floor; blood mana (state 114) pays with life. Mana is charged after `srvstfunc` succeeds, or at the do with
`usemanaondo`.

**Delays**: global delay (`0x439470`) and cooldown (`0x439500`) formulas are raised to the class's charstats
**`MinimumCastingDelay`** (a D2R column). Cooldowns are per skill: state 185 with stat 359 layered by skill id, so one
skill's long cooldown never blocks another.

**Do dispatcher** (`0x43acb0`): srvdofunc above 0xbe fails; an empty do slot is not a failure when `srvmissile`
is set (the missile is fired at the target). Free slot ranges are in
[engine-limits.md](engine-limits.md#skills).

**Damage values** (classic, matches in-game tooltips): per-level steps in brackets 2-8, 9-16, 17-22, 23-28, 29+
(lengths use the first three); elemental synergy applies to the minimum only above one point or with a per-level step.

**Missile create** (`0x5371a0`, **read**):

- Velocity `(Vel + VelLev * level / 8) << 8`, times hand-of-Athena% in the slow-missiles state for CanSlow missiles,
  then **times 75 / 100**.
- A target more than 99 subtiles away on an axis refuses the missile.
- **Range is the missiles.txt `Range` formula** (misscalc scope) plus `(SubStop - SubStart) * loops`; classic's
  `Range + level * LevRange` is gone. Activate frame = range - `Activate`.
- Pierce rolls up to four `% 100 < chance` draws. The `Radius` formula is evaluated once, clamped to 0..0x7fff.
- New missile seed hi word is 666.

**Missile movement** (classic): 16.16 fixed point, 64 directions; velocity grows by `Accel` every fifth frame up to
`MaxVel << 8`; the path is walked subtile by subtile against the CollideType mask.

| CollideType | Hits | Blocked by |
|-|-|-|
| 1 | players, good monsters | player, barrier |
| 2, 5 | monsters | monster, barrier |
| 3 | players and monsters | monster, player, barrier |
| 4 | nothing (a stationary missile ends on any touch) | none |
| 6 | nothing | barrier |
| 7 | destroyable missiles | missile |
| 8 | players and monsters | monster, player, barrier, wall |

The collision table sits at `0x2391378` (stride 0x10, build 93847). Hit candidates need unit flags 0x4 | 0x8, must
be hostile unless CollideFriend, and not in `justhit` for NextHit missiles. Explosion missiles keep going after a
hit; CollideKill ends them; each pierce spends one pierce_idx.

**Missile damage functions** (srvdmgfunc, table `0x2391270`, 18 slots, vanilla 1-17):

| Slot | Effect |
|-|-|
| 1 | DmgCalc1% of physical moves to the EType (elemental arrows) |
| 2 | freeze of dParam1% of the cold length |
| 3 | soft hit when a draw `& 127` < dParam1 (fire walls, blaze) |
| 4 | the chill becomes a freeze |
| 5 | fresh EType roll, +dParam1% vs undead, +dParam2% vs demons |
| 6 | target drops no treasure (unless the owner is a mercenary) |
| 7, 9 | stun (dParam1 frames or the skill's params), hit class 0x60 |
| 8 | ProgOverlay on the target |
| 10 | freeze as long as the chill |
| 11 | poison ends at a frame carried in the missile |
| 12 | EType roll + DmgCalc1% of physical as the only damage |
| 13 | monsters only: owner damagepercent, then slot 5 |
| 14 | knockback chance by target size |
| 15 | fresh EType roll; full damage only to sHitPar2 kinds (0 all, 1 undead, 2 demons, 3 both), else dParam1% |
| 16 | a 3.x class skill; body not in the readable image (**unverified**) |
| 17 | every damage field times DmgCalc1 / 100 (float) |

Missile do and hit function tables: `0x2390e80` (do, slots 1-53), `0x2391030` (hit, slots 1-70), dispatchers
`0x466ce0` and `0x464ac0`. srvst table `0x238e710` (94 slots, vanilla 1-70). All build 93847.

**Auras** (classic): the next pulse is `ceil(frame / d) * d + 1`, `d = perdelay` (at least 5). Aura target filter 0
means `0x583`. **Summons** (`0x4350e0`): at `petmax` the oldest pet of the type is removed first; pet level
(`0x55fb50`) = owner level capped at `max(1, 3L / 4 + skill level)`, plus monlvl's expansion defense and to-hit.

## 8. Character stats and itemstatcost ops

**New character** (`0x52d770`): attributes from charstats, `life = (vit + hpadd) << 8`, `mana = int << 8`, stamina
from charstats; the skill list starts with skill 0 and charstats `Skill 1-10`.

**charstats** record (build 93847) is classic's minus the four `Percent*` bytes. Per-level and per-point columns are
**quarter points** (`<< 6` turns them into 8.8). New D2R columns: `SkillsPerLevel`, `HealthPotionPercent` /
`ManaPotionPercent` (replace the classic hard-coded per-class potion bonus), `MinimumCastingDelay`.

**Spending points** (C->S 0x3a / 0x3b carry a count, up to 65536 per request; classic was one point):
energy adds `ManaPerMagic * 64` to mana and max mana, vitality `LifePerVitality * 64` and
`StaminaPerVitality * 64`. Item vitality/energy reach the maxima through ops 9/8.

**Regeneration** (event `0x42e600`, every frame, build 93847):

- Players have **no natural life regen**; stat 74 comes from items and potions.
- Mana: `mult = max(1, maxmana / (25 * ManaRegen))` (ManaRegen 0 means 7500), natural = `mult * (100 + stat 27) /
  100`, plus stat 26; none under state 85.
- Stamina: standing `max >> 8`, walking `max >> 9` per frame, other modes only with stat 28 >= 1000. D2R also
  regenerates while walking below one point (classic didn't).
- Running drain (`0x42dca0`): `2 * RunDrain`, times `(armor speed / 10 + 1)` with body armor, reduced by stat 154,
  at least 1.

**Potions** (item spell 3, `0x57ecb0`): HealthPotionPercent / ManaPotionPercent for players (others x2), then an
attribute roll that can double the value. Timed potion totals: [engine-limits.md](engine-limits.md#items-inventory-sets-runes-cube).

**itemstatcost ops** (update `0x2fa430`, total `0x2f8ad0`, op-table loader `0x31e310`, build 93847). `v` = value so far,
`o` = the op stat's total, `b` = the op base's value `>> ValShift`:

| op | adds | when |
|-|-|-|
| 1 | `v * o / 100` (on an item, `v` is the item's base value) | the list has a unit |
| 2 | `b * o >> param` | owner is a player or monster, `b > 0` |
| 3 | op 2's term as a percent of `v` | as op 2 |
| 4, 5 | as 2 and 3, `b` read from the unit the item is on | that unit is a player or monster |
| 6, 7 | time-of-day term, and as a percent | the list has a unit and a by-time callback |
| 8 | `(o - own base) * ManaPerMagic << 6` | player |
| 9 | the same with LifePerVitality (or StaminaPerVitality for max stamina) | player |
| 11 | `v * o / 100` | player or monster |
| 13 | item base value `* o / 100` | the unit is an item |
| 10, 12 | nothing | |

- An op outside 1-13 is set to 0 at load and the stat is skipped.
- A stat can be the op base for up to 3 stats; each stat holds at most **16 op entries** and up to 64 dependents.
- `fMin`: a player/monster total below `MinAccr` is stored as `MinAccr`. Without `keepzero` a 0 value removes the
  entry. Capping current life at max is the caller's job, not the stat list's.
- Op 4/5 results (for example defense per level) are recomputed only when the item list is posted again: LevelUp
  does that; a plugin that changes level some other way must re-post items.
- Hard-coded stat ids: 0-15 (strength through gold bank), 6/8/10 current and 7/9/11 maxima.
- **States**: at most 16 blocks of 32 bits, and the 9-bit id with terminator `0x1ff` in the all-states message
  caps the table at **511 rows** (0-510). `has_state` (`0x3351b0`) only answers for players, monsters and missiles.

## 9. Formula engine details

Compiler `0xa24290`, evaluator `0xa234b0` (build 93847), for skill, missile, item and treasure-class formulas.
Checked byte for byte: all 9,413 vanilla formula cells compile identically.

- Function calls are 3 bytes (opcode, function index, argument count); `sklvl` takes 3 arguments, `cond` 1 or 2,
  the others 2.
- **Precedence** (low to high): comparisons, `+ -`, `* /`, `^` (power, left-associative), unary minus, ternary.
  Parenthesise conditions and branches: `1+2?3:4` gives 4.
- **Ternary is eager**: both branches run, including both `rand` calls (seed draws).
- Division truncates; x/0 = 0. `^` is integer power; exponent <= 0 gives 1.
- Constant expressions are folded: `8%3` compiles to 8 in skill/missile scope (there is no `%`).
- **Monster formulas** (monpet) use a different opcode set with remainder at 20 (power 21, negate 22, ternary 23).
  Never decode them with the skill alphabet. The native monster compiler was **not identified**.
- Input above 255 bytes or names above 32 bytes fail. Unary minus compiles (prefer `(0-N)` anyway).
- **Unknown names silently become 0**: an unknown skill/missile/stat name in `skill()`, `miss()` etc. links as 0; an
  unknown `cond` name becomes condition 0 (IsType); an unknown condition parameter becomes 0 (`'hel'` typo = normal).
  The dispatcher (`0x35f010`) has 8 conditions: IsType, IsClass, Desecrated, MonsterTestElite, ItemIsType,
  ItemIsModType, Difficulty, IsDesecratedZonesEnabled. `MonsterHasMod` is in the dataguide but **not in this
  dispatcher** (unverified).
- `rand(a, b)` is inclusive and draws nothing when `a >= b`. Skill formulas roll on the caster, missile formulas on
  the missile, item formulas on the player.
- Calc row meanings (skillcalc, misscalc): linear pairs `A + (L - 1) * B`, diminishing pairs
  `A + 110 * L * (B - A) / (100 * (L + 6))`.

## 10. Monsters and hirelings

**Monster stats** (`0x543e90` spawn, `0x3a26c0` by-level): `monlvl[level][col] * monstats value / 100`, or the raw
value for `noRatio` rows. HP = `min + roll(max - min + 1)` on the monster's seed, plus the players-count HP%,
capped at `0x7fffff`. Regen = `(hp << 8) * DamageRegen >> 12`. Classic-data (edition 1) monsters in NM/Hell: half
life, defense x10/12, damage x10/12, to-hit x10/15. Per-attack mode picks the columns: A2 for mode 5, S1 for
modes 7/8, A1 otherwise. Elemental attacks with a `noRatio` row get no DM scaling (a D2R bug).

**Hirelings** (hireling.txt record stride 0x150, build 93847; **checked** over 227,128 cases):

- The hire list per NPC is a game-wide **0x45-entry** list shared by all players, up to 10 available picks; it is
  redrawn only when every entry is hired. A row with more than 0x45 names overflows in the engine.
- Quote level = `max(2, playerLevel - HiringMaxLevelDifference + roll(range))`; expansion rows (range 1) give
  player level - 1.
- Stats at level (`0x544f60`), `d = level - row.Level`: life `max(0x2800, (HP + d * HP/Lvl) << 8)`; str/dex
  `max(10, base + d * perLvl / 8)`; damage per level in eighths; resists `base + d * perLvl / 4`; life regen =
  max life / 2000; skills `Level + (LvlPerLvl * d >> 5)` once skills.txt reqlevel is met.
- Experience curve `req(L) = (L + 1) * L^2 * Exp/Lvl`. A hireling gains `2 * amount`, only while below the owner's
  level; one gain can lift it several levels past the owner. Its gain is capped at `(req(L+1) - req(L)) >> 6`.
- Hire cost `max(Gold, (d * 15 + 100) * Gold / 100)`. Resurrect price `min(max, level^2 / divisor * multiplier)`
  (expansion rows 15 / 2 / 50000); a level-1 hireling's price is 0 and cannot be resurrected.
- **Equipment**: D2R reads monstats `rightArmItemType` / `leftArmItemType` and `canNotUseTwoHandedItems` instead of
  classic's per-class switch; class items follow hireling.txt `equivalentcharclass`; body armor and helms are always
  allowed. A hireling can dual-wield when both arm types allow weapons (classic never). Items with an itemtypes
  `Restricted` socket filler can't be stacked across worn items. Equipment rules are **static only**.

## 11. Functions worth hooking (build 93847)

| RVA | What | Hook for |
|-|-|-|
| `0x3483c0` | player attack rating | custom AR terms |
| `0x347b10` / `0x347da0` | defense / block rate | defense or block rules |
| `0x44ba60` | to-hit roll | hit-chance formula |
| `0x44b720` | attack result flags (range, to-hit, block/dodge) | auto-hit, new avoid stats |
| `0x44c030` | FillDamageValues | new damage sources, crit rules |
| `0x44df10` | CalculateTotalDamage | resist caps, new mitigation |
| `0x44ce80` | ExecuteEvents (leech, stun, cold, poison) | on-hit effects |
| `0x44a9b0` | apply hit, hit recovery | hit-react rules |
| `0x583150` / `0x584170` | crushing blow / open wounds | their formulas |
| `0x44a3b0` / `0x44ece0` | kill experience split / award | experience rules |
| `0x424ac0` / `0x425cb0` | death experience / gold penalty | death rules |
| `0x42e600` | player regeneration event | regen rules |
| `0x33f360` / `0x33aa00` / `0x3404a0` | usability / mana cost / delay check | skill gating |
| `0x5371a0` | missile create | range, speed, pierce |
| `0x544f60` | hireling stats by level | hireling scaling |
| `0x543e90` | monster stats at spawn | monster scaling |

Check D2RLoader's own hooks before patching any of these ([engine-re.md](engine-re.md)): some (for example the skill
charge at `0x436830`) already have a D2RLoader hook at their entry.
