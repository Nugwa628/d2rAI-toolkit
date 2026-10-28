# D2R address registry: build 93847

**Build 93847 only**: D2R 3.3.0 under D2RLoader 1.3.1, image PE timestamp **`0x6ab3782c`**. All values are RVAs
(image base + RVA) or byte offsets. Before using any of them on another build, verify in Ghidra
([engine-re.md](engine-re.md)). Signatures are Windows x64; "dataFlag" is the game's data-table selector byte
(`game + 0x106`, or `0x34a0e0(unit)`). Sources: a working D2RLoader plugin (hooked or called in game) and its
Ghidra notes. "Hooked" = installed with the entry bytes shown and ran in game.

## Image

| What | Value |
|---|---|
| PE TimeDateStamp | `0x6ab3782c` |
| Allocator object | `0x12063c0` -> allocator; vtable +0x20 frees a block |

## Game structure

| Offset | Meaning |
|---|---|
| game +0x104 | u8 difficulty (0 normal, 1 nightmare, 2 hell) |
| game +0x106 | u8 dataFlag passed to data-table getters |
| game +0x1aac8 | per-game counter map (used by the cube Red Portal; accessor `0x5d88b0`) |

## Units

| Offset | Meaning |
|---|---|
| unit +0x00 | i32 type: 0 player, 1 monster, 2 object, 3 missile, 4 item, 5 tile |
| unit +0x08 | class id (row in monstats / items / missiles / objects; `0x34a330` returns it) |
| unit +0x10 | type-specific data (missile data, object data, item data) |
| unit +0x28 | seed {u32 lo, u32 hi}; RNG step `lo' = lo*0x6ac690c5 + hi`, `hi'` = carry (inlined everywhere) |
| unit +0x38 | path: dynamic (players, monsters, missiles) x u16 +0x02, y u16 +0x06; static (objects, items, tiles) x i32 +0x10, y i32 +0x14 |
| unit +0x88 | stat list (StatListEx when its flags +0x1c < 0); getter `0x34b870` |
| unit +0x124 | flags; bit 0x40 = "skill used" (classic 1.10 unit+0xc4) |
| unit +0x12c, +0x130 | passed by D2R's summon skills to pet setup (`0x55ef80` arg, `0x58d950` arg) |

| RVA | Signature | Meaning |
|---|---|---|
| `0x349860` | (unit, file, line) -> i32 | unit class id (row) |
| `0x34a330` | (unit, file, line) -> i32 | unit +8 |
| `0x34a0e0` | (unit) -> u8 | dataFlag (client functions use this) |
| `0x34a1e0` | (unit) -> u32* | &seed |
| `0x34a360` | (unit, file, line) -> inventory | unit inventory |
| `0x34b440` | (unit) -> room | unit's room |
| `0x34a040` | (unit, i32 xy[2]) | position |
| `0x34a110` | (unit) -> packed position | |
| `0x34ab60` | (unit) -> u32 | unit id |
| `0x34dbe0` | (unit, 1) | mark stats changed |
| `0x34e790` | (unit, on) | set unit +0x124 bit 0x40 |
| `0x34a5c0` | (unit) -> i32 | max speed (175; 250 for players in some states); min speed 15 at `0x34ab50` |
| `0x350a20` | (unit, x, y, mask) -> nonzero blocked | path collision test (teleport uses mask 0x804) |
| `0x491ca0` | (game, unit, 0, x, y, 0, 0) | move/teleport unit |
| `0x491960` | (player, message, player) | client message (0x15 = "can't do that") |
| `0x492790` | (game, a, b, char playerFlag) -> nonzero enemies | hostility test |
| `0x492cf0` | (game, room, x, y, monster, mode, search -1/4, flags) -> unit | spawn monster |
| `0x4327d0` | (game, unit, 0, 0, range, filter, callback, context, 0, file, line) | unit finder; callback (finderCtx {game, caster, count, context}, target) -> counted if nonzero (`0x4398b0` builds the context) |
| `0x2efc10` | (room) -> i32 | room -> level id |
| `0x2f0750` | (room) -> nonzero | room is in town |
| `0x38dfb0` | (room, x, y) -> room | room holding x/y near the given one |
| `0x32c4a0` | (dataFlag, level) -> record | levels.txt record; +0x0e u8 Teleport (0 no, 1 yes, 2 clear line) |
| `0x3e2b5c8` | (dataFlag, level id) -> act | level's act (address as used by the port; unusual range, verify) |

## Stats and stat lists

| RVA | Signature | Meaning |
|---|---|---|
| `0x2f5020` | (unit, stat, layer) -> i32 | total stat value (thunk) |
| `0x2f48c0` | (unit, stat, layer) -> i32 | base value (`stat(...).base`) |
| `0x2f5c60` | (unit, stat) -> i32 | value without layer (mode 2; D2R's IAS read) |
| `0x2f5e80` | | stat compare helper used by cube conditions |
| `0x2f7d10` | (unit, stat, value, layer) | set stat |
| `0x2f34f0` | (unit, stat, value, layer) | add stat |
| `0x2f7c00` | (dataFlag, list, stat, value, layer) | set stat in a stat list |
| `0x2f3330` | (dataFlag, list, stat, value, layer) | add stat in a stat list |
| `0x2f5b80` | (unit, state, 0) -> list | the unit's stat list for a state (StatListEx: +0x20 state, +0x68 next, +0x90/+0x98 sub-list chains) |
| `0x2f5940` | (unit, state) -> list | stat list for a state |
| `0x2f7920` | (unit, list) | detach list |
| `0x2f4180` | (dataFlag, list) | free list |
| `0x3d5230` | (owner, item, state, flags) -> list | item stat list, created if missing (flags 0x40 = magic list) |

Pools: plain stat lists `0x2a9a430` (element 0x90); extended stat lists `0x2a9a4d0` (element **0x2b00**, 0x200 per
chunk; static constructor `0x3eb60`; pool +8 element size, +0x18/+0x28 chunk lists; chunk creation `0x1230260`);
create `0x2f65d0` (zero-fills 0x2b00), release `0x2f9eb0`. Extended list +0x2af0/+0x2af8: creation args.

## States

| RVA / offset | Meaning |
|---|---|
| `0x3074d0` | states.txt load: asserts `size <= 0x1ff` (MAX_STATES 511), then uses blocks = (count + 31) >> 5 |
| StatListEx **+0xaf0** | current state bits (blocks dwords), then "changed" flags at +0xaf0 + blocks*4; room 16 + 16 dwords to +0xb70; fields at +0xb70/+0xb78 follow (only copied from a zeroed template) |
| `0x2f6220` | check state (reads +0xaf0) |
| `0x2f7ea0` | (unit, state, on) set/clear state bit. Hooked; entry `40 53 56 41 57 48 83 EC 50 48 63 DA 45 8B F8` (PUSH RBX / PUSH RSI / PUSH R15 / SUB RSP,0x50 / MOVSXD RBX,EDX / MOV R15D,R8D) |
| `0x2f6370` | state array pointer |
| `0x2f62d0` | "changed" array pointer (dword index 0x2bc) |
| `0x2f0d80` | stat-list copy routine (32 dword moves of the state area; only caller `0x2f65d0`). **Do not patch** |
| `0x336440` | (unit, mask) -> 1 if any state of the mask is set. Hooked; entry `48 89 5C 24 08 48 89 6C 24 10 56 57 41 56` |
| `0x3356c0`, `0x336330` | other state-array users (loop over the live block count) |
| `0x3351b0` | (unit, state) -> nonzero: has state |
| `0x3355a0` | (unit, state, on) set state (game level) |
| `0x3354c0` | (unit, state, 0) clear state |
| `0x53a210` | server: send changed states. **Replaced by D2RCore `SendChangedWideStateStats`** (`jmp [rip+x]` at entry) |
| `0x539890` | server: send all states. **Replaced by D2RCore `SendAllWideStateStats`** |
| `0x52f9d5` | D2R sets state 186 `sharedstash` on the player at game join |

Displacement sites a "move the state arrays" patch would touch (for reference only: **don't**; D2RCore reads
+0xaf0): `0x3eb79` (0x2b00 pool size), `0x2f66ee` (0x2b00 memset), `0x2f62ba`, `0x2f7f8f`, `0x2f7fb0`, `0x2f7fff`,
`0x2f63c6` (0xaf0), `0x2f634f` (0x2bc).

## Skills

### Tables and dispatch

| RVA | Meaning |
|---|---|
| `0x238ea00` | **srvdofunc table**, 191 x 8-byte pointers (.data). D2R fills 0-171; **172-190 empty** |
| `0x43acb0` | server skill dispatcher (`D2Game\src\Skills\Skills.cpp`): reads srvdofunc (u16 skills +0x4e), rejects > 0xbe, calls (game, unit, skillId, level) if non-null; nonzero = success |
| `0x238e710` | srvstfunc table (indexed by u16 skills +0x4c; size not confirmed) |
| `0x43b3e0` | (game, unit, skill, level, force) start a skill (calls srvstfunc). Hooked; entry `48 89 5C 24 10 48 89 6C 24 18 48 89 74 24 20` |
| `0x235f7d0` | **cltdofunc table**, 130 x 8 bytes, bound at `0x235f7c4`; D2R uses up to 109, **110-129 empty** |
| `0x216d20` | client skill dispatcher: (unit, skill, level) -> nonzero ran |
| `0x33f360` | (unit, skill entry) -> 0 usable, else reason code (D2Common Skills.cpp). Hooked; entry `40 56 57 48 83 EC 28 48 8B FA 48 8B F1 48 85 D2` |

### Record (skills.txt, 748 = 0x2ec bytes)

Getter `0x97790` (dataFlag, skillId) -> record.

| Offset | Field |
|---|---|
| +0x24..+0x28 | flags: +0x24 bit0 decquant, bit1 lob; +0x25 bit0 InTown; +0x28 bit5 usemanaondo, bit7 rightskill |
| +0x4c | u16 srvstfunc |
| +0x4e | u16 srvdofunc |
| +0x58 | prgcalc1 (compiled formula index) |
| +0x66 | i16 srvmissile; +0x68/+0x6a/+0x6c srvmissilea/b/c |
| +0x70 | aurafilter |
| +0x74 | 6 x i16 aurastat1-6 |
| +0x84 | aurarangecalc |
| +0x88 | 6 x i32 aurastatcalc1-6 |
| +0xa0 | i16 aurastate; +0xa2 auratargetstate |
| +0xb4 | i16 passivestate |
| +0x110 | i16 summon (monstats row); +0x112 u8 pettype; +0x113 u8 summode (255 none); +0x114 petmax calc |
| +0x13e | i16 cltmissile; +0x140/+0x142/+0x144 cltmissilea/b/c |
| +0x188 | HitFlags; +0x18c HitClass |
| +0x190 | calc1..calc10 i32 (calc1 +0x190, calc2 +0x194, calc3 +0x198, calc4 +0x19c, calc7 +0x1a8, calc8 +0x1ac, calc9 +0x1b0, calc10 +0x1b4) |
| +0x1b8 | Param1..Param8 i32 (Param3 +0x1c0, Param4 +0x1c4, Param5 +0x1c8) |
| +0x214 | u16 reqlevel |
| +0x226 | i16 minmana; +0x228 u8 manashift; +0x22a i16 mana; +0x22c i16 lvlmana |
| +0x238 | compiled localdelay formula (-1 none) |
| +0x24d | u8 SrcDam (/128) |

### Formulas and calc codes

| RVA | Signature | Meaning |
|---|---|---|
| `0x3b5160` | (dataFlag, unit, calc, skillId, level) -> i32 | evaluate a skill formula |
| `0x33b3b0` | (dataFlag, unit, code, skillId, level) -> u64 | **skill calc code switch** (skillcalc row -> value; unknown rows -> 0). Hooked; entry `48 89 5C 24 10 48 89 6C 24 18 48 89 74 24 20`. D2R row 41 = `blvl` |
| `0x3b4a90` | (dataFlag, missile, owner, calc, class (-1 own), level (-1)) -> i32 | evaluate a missile formula; callback `0x3b5540` |
| `0x3b9dc0` | (dataFlag, missile, owner, code, missileId, level) -> u64 | **missile calc code switch**. Hooked; entry `48 89 5C 24 08 48 89 6C 24 18 56 57 41 56` |
| `0x3b4880` | (dataFlag, player, item, calc) -> i32 | evaluate an items.txt formula |

### Mana cost

| RVA | Meaning |
|---|---|
| `0x33aa00` | (dataFlag, skillId, level) -> cost << 8 (minmana << 8 floor); no unit. Hooked; entry `48 89 5C 24 08 57 48 83 EC 20 41 8B F8 33 DB` |
| `0x436830` | server skill charge (entry already hooked by D2RLoader); calls `0x33aa00` at `0x4369fb`, return `0x436a00`, unit in **RDI** |
| `0x2188b0` | client skill charge; call at `0x218937`, return `0x21893c`, unit in **RBX** |
| `0x338110`, `0x33d480` | other callers (tooltip, checks) |

### D2R server skill functions (srvdofunc slot -> RVA)

| Slot | RVA | Skill / behaviour |
|---|---|---|
| 11 | `0x5562b0` | melee then calc1 missiles (Charged Strike style) |
| 18 | `0x553e60` | self buff (Frozen Armor): builds a state-list request on the stack; expiry callback `0x5551f0` |
| 24 | `0x553950` | wall (Fire Wall); caps active walls |
| 27 | `0x554c40` | Teleport (levels.txt Teleport rule, `0x350a20`, `0x491ca0`) |
| 58 | `0x55e7e0` | Revive |
| 65 | `0x563d90` | stat aura (aurastate on caster, auratargetstate on targets) |
| 66 | `0x5630e0` | damage aura (Holy Fire/Shock); finder callback `0x561280` |
| 68 | `0x567500` | nova + buff (Shout style) |
| 76 | `0x568f40` | Whirlwind do (hit delay `0x56ac00`, called at `0x56916d`, `0x569186`, `0x5691e2`) |
| 109 | `0x5822b0` | itemless town portal (skill 411); `0x46fa70` finds destination town and makes the portal, `0x470ae0` links the town side |
| 113 | `0x584b60` | scroll/book skills: only sends packet 0x3f to the client (`0x47f520`) |
| 121 | `0x576bb0` | Rabies (melee + state + missile; missile choice `0x571700`) |
| 124 | `0x575de0` | timed storm (Hurricane, Armageddon): aurastate + periodic event |
| (Raise Skeleton) | `0x55e440` | pet summon pattern |
| (Multiple Shot) | `0x557840` | missile params pattern |
| srvstfunc 38 | `0x56a420` | Whirlwind start (game, unit, skill, level) -> 1 started. Hooked; entry `48 89 5C 24 10 44 89 4C 24 20 55 56 57 41 54` |

`0x56ac00` (unit, weapon) -> frames until Whirlwind's next hit (weapon attack length at D2R speed: clamp(stat 68 +
120*IAS/(120+IAS), 15, 175); 10 without a weapon). Only Whirlwind calls it. Hooked; entry
`48 89 5C 24 18 55 56 57 41 56 41 57 48 81 EC 80 00 00 00`.

D2R client skill functions: cltdo 15 `0x2b3750` (random spray around the target), 19 `0x2ae540` (client Charged
Strike fan), 31 `0x2b0ac0` (missile on a corpse).

### Skill helpers

| RVA | Signature | Meaning |
|---|---|---|
| `0x432b70` | (game, unit, &x, &y) -> both nonzero | skill target position |
| `0x48fe20` | (game, unit) -> unit | skill target unit (runs the target check `0x491b50`) |
| `0x44b2b0` | (game, attacker, target, 0, 1) | apply the queued melee hit (SUnitDmg.cpp) |
| `0x589930` | (game, caster, skill, level, target, x, y, &type, &id, &x, &y, 0) | cast a skill as an event (item procs) |
| `0x433d20` | (&StateListParams) -> list | create a timed state stat list (self-buff path). Params (0x30): +0 caster, +8 target, +0x10 skill, +0x14 level, +0x18 duration frames, +0x1c -1, +0x20 0, +0x24 state, +0x28 expiry callback |
| `0x438470` | | client state message sent by buffs |
| `0x9a570` | (unit) -> unit | client: the unit's skill target |
| `0x214c80` | (unit, &x, &y) -> nonzero | client: skill target point |
| `0x3b6700` | | animation lookup by mode (crashes when a skill's mode resolves to -1) |

### Pets

| RVA | Signature | Meaning |
|---|---|---|
| `0x4350e0` | (game, &SummonParams) -> pet | summon and register (enforces petmax). Params (0x30): +0 flags (1 = at x/y), +8 owner, +0x10 u16 monster, +0x14 AI state, +0x18 mode, +0x1c x, +0x20 y, +0x24 pettype, +0x28 petmax |
| `0x55fb50` | (game, owner, pet, 0, level) | pet level from the owner (monlvl AC/AR) |
| `0x55ef80` | (game, owner, pet, skill, level, owner+0x12c, 0) | skill's passive/aura stats and state on the pet |
| `0x58d950` | (game, pet, 0, owner+0x130) | party / AI |

## Damage

| RVA | Signature | Meaning |
|---|---|---|
| `0x437d00` | (unit, &damage, skill, level) | skill's elemental damage (EType/EMin/EMax, synergies) into a block |
| `0x4494b0` | (&to, &from) | copy a damage block |
| `0x44c030` | (game, attacker, target, &damage, 0, srcDam byte) | add weapon damage (srcDam/128; 0 = 128) |
| `0x44ce80` | (game, attacker, target, 1, &damage) | hit |
| `0x44a9b0` | (game, attacker, target, &damage) | apply |

Damage block: **0x180 bytes**: +0 u32 hit flags, +4 u16 result flags, +0x40/+0x48/+0x50 small list (data, count,
capacity; bit 63 = inline storage at +0x58, 16 x 12 bytes), +0x144 hit class, +0x158 tagged callback pointer (1 =
empty). Functions holding one inline its destruction (see `0x5630e0`'s epilogue).

## Missiles

| RVA | Meaning |
|---|---|
| `0x5371a0` | (game, &MissileParams) -> missile: create (server) |
| `0x1b7760` | (&MissileParams, 0) -> missile: create (client) |
| `0x166040` | (dataFlag, missileId) -> record or null |
| `0x1b89a0` | (dataFlag) -> missiles.txt row count |
| `0x490300` | (game, missile) -> owner |
| `0x9a7b0` | (missile) -> owner (client) |
| `0x3bc450` / `0x3bb470` | (missile) -> skill / skill level |
| `0x3bb0f0` | (missile) -> frames elapsed |
| `0x3bb1e0` | (missile) -> frames left |
| `0x3bdbc0` / `0x3bd450` / `0x3bd8a0` | total frames / set frames left / set step counter |
| `0x2390e80` | **server do table** (record +0x2c), no bound, dispatcher `0x466ce0`; 46-54 empty |
| `0x2391030` | **server hit table** (record +0x2e), bound 71 at `0x2391404`, dispatcher `0x464ac0` (game, missile, target or null); **61-70 empty** |
| `0x2358430` | **client do table** (record +0x28), called (missile, float part-frame) from `0x1ba040`; slot 14 unused |
| `0x2358710` | **client hit table** (record +0x2a), bound 81 at `0x2358a64`; slots 6, 7 empty |
| `0x4550e0` / `0x1b5380` | server / client do 1 (plain move) |
| `0x459380` / `0x1a8880` | server do 15 / client do 19 (Frozen Orb style spawner) |
| `0x45bc40` | server hit 1 (explosion: missile damage to units within sHitPar1 / calc1) |
| `0x45ef10` | server hit 21 (auratargetstate + aurastats on the target) |
| `0x1ac8f0` | client hit 2 (spawns CltHitSubMissile1) |
| `0x1b9840` | client spawner (16 directions) |

MissileParams (0xa0 bytes, classic 1.10 block +0x10 after three pointers): +0 u32 flags, +8 owner, +0x10 origin,
+0x18 target, +0x20 missile, +0x24 x, +0x28 y (0 = origin), +0x2c/+0x30 target x/y, +0x34 gfx arg, +0x38
velocity, +0x3c skill, +0x40 level, +0x44 loops, +0x4c frame, +0x50 activate frame, +0x54 attack bonus, +0x5c range
(with flag 0x8000), **+0x70 init kind** (replaces classic init function pointer: 1 = charged-strike bolt, 4 =
multishot), +0x74 init argument (e.g. index).

Missile record (classic 1.13c layout moved +0x24 from +0x24): +0x28 u16 pCltDoFunc, +0x2a pCltHitFunc, +0x2c
pSrvDoFunc, +0x2e pSrvHitFunc, +0x3a i16 SubMissile1, +0x40 CltSubMissile1, +0x46 HitSubMissile1, +0x4e
CltHitSubMissile1, +0x5c/+0x60/+0x64/+0x68/+0x6c Param1-5, +0x70/+0x74 sHitPar1/2, +0x7c/+0x80/+0x84 CltParam1-3,
+0x8c CltParam5, +0x90/+0x94 cHitPar1/2, +0xa4 SrvCalc1, +0xa8 CltCalc1, +0xac SHitCalc1, +0xb0 CHitCalc1, +0x1b6
i16 Skill.

Missile data (unit +0x10): +0x0c skill, +0x0e level, +0x10 i16 total frames, +0x14 frames left (float, x40 per
frame), +0x30 step counter (Fire Wall's).

## Items and inventory

| RVA | Signature | Meaning |
|---|---|---|
| `0x314110` | (dataFlag, classId) -> record | items record (armor/weapons/misc): +0x80 code (4 chars), +0x94 i32 pSpell, +0xa4 calc1-3, +0x144 i8 spellicon (layout as classic 1.13c up to +0x94) |
| `0x373890` | (item, itemtype) -> nonzero | item is of type |
| `0x36cf60` | (item) -> quality | item data +0 |
| `0x36cfe0` | (item) -> u8 | item data +0x55: page (0 inventory, 3 cube, 4 stash) |
| `0x36ca10` | (item) -> u8 | item data +0x54 |
| `0x36dea0` | (item, byte) | set item data +0x5c |
| `0x376de0` | (item, player) -> i32 | level requirement (-1 none) |
| `0x3d4620` | (type, unit, item, mods, index, set, &property, state, flags, 0) | apply a property (classic D2Common 10665); property {i32 prop, param, min, max}; property functions table `0x2386ab0` |
| `0x535f60` | (game, player, item, 1, 0, unit id, byte, byte, position) | item update packet (as after identify `0x46e8c0`) |
| `0x388c10` / `0x38aba0` | (inventory) -> first item / (item) -> next | inventory walk |
| `0x3886d0` | (inventory, bodyloc 0-12) -> item | equipped item |
| `0x382d20` | (i32 location[4], item) | item location: unit id, page, packed x/y |
| `0x472590` | (player, adds, removes, addIds, removeIds, nAdds, nRemoves, 0) -> bool | validated inventory change |
| `0x4712a0` | | removal pattern for a used-up tome |
| `0x43ec10` | (game, item) | free item |
| `0x236fe30` | **item spell table**: 20 x 12 bytes {u32 check, u32 use, u32 flag}; flag 1 = not used up; 16-19 empty; 20+ ignored. Check handlers `0x1d3ea30` (11), use handlers `0x1d3ea90` (16) |
| `0x5817f0` | (game, player, item, target) -> bool | item spell check dispatcher. Hooked; entry `48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18` |
| `0x581680` | (game, player, item, target) | item spell use dispatcher (caller removes a used item). Hooked; entry `48 89 5C 24 18 48 89 6C 24 20 56 57 41 57` |
| `0x3731f0`, `0x373610`, `0x36f000` | | item getters (`0x3731f0` item type) seen in hang thread samples; signatures unverified |
| `0x300a90` | | data tables getter, seen in the same samples; unverified |
| `0x308e80` | (item) -> 1 when the slot flag is 0 | "used up"; client puts it in the use message (`0x1c7a30`), server use-item handler `0x4f40c0` refuses unless both agree |

## Portals and objects

| RVA | Meaning |
|---|---|
| `0x432ce0` | (game, unit, room, x, y, destLevel, &object, objectId, 0, 1) create portal (`D2Game\src\Items\ItemMode.cpp`); object 0x3b town portal, 60 red portal; also places the return portal (`0x435dd0`) |
| `0x517f90` | (game, room, xy[2], 3, 0x400, &room, 5, 100) free spot near xy |
| `0x34e9d0` | (object, level byte) set portal destination (clears the high byte) |
| `0x34ad40` | portal destination getter (16 bits: object data +0x08 low, +0x78 high) |
| `0x5da800` | cube output "Red Portal" |
| `0x5d88b0` | (game + 0x1aac8, &key) -> int* per-game counter |

## Cube

| RVA / offset | Meaning |
|---|---|
| data tables +0x1570 / +0x1578 | cubemain records / count; record **0x16c** bytes: +0 enabled, +1 min diff, +2 class (-1 any), +3 u8 op, +4 param, +8 value, +0xc numinputs, +0xe version, +0x10/+0x12 ladder seasons |
| `0x3e3f60` | (?, index) -> cubemain record (asserts in `D2Common\src\DataTbls\HoradricCube.cpp`) |
| `0x529090` | (int* result, game, player, out) recipe search (`D2Game\src\Player\PlrTrade.cpp`): per record checks enabled, version, ladder (`0x403fe0`), difficulty, class, numinputs, then the player condition switch for **ops 1-14 only** (1/2 date, 3-14 stat compares via `0x2f5020`/`0x2f48c0`/`0x2f5e80`; other ops pass), then `0x529d50` for inputs 0..6; all nonzero -> found |
| `0x529d50` | (game, inventory, record, input#) match one input against the cube items, checking item condition ops 15+ |

## Levels and automap

| RVA | Meaning |
|---|---|
| `0x32db30` | LvlTypes.txt loader (vector: extra types load) |
| `0x32cae0` | AutoMap.txt loader: matches LevelName against 36 names (table `0x1cfe110`, 0x10 bytes each); unmatched -> assert `ii < NUM_LEVEL_TYPES`, type 0 |
| `0x2a9a8e0` | 36 x 8-byte row ranges per level type (followed by another object at `0x2a9aa00`) |
| `0x2a9a8c8` | automap rows vector: data pointer, +8 count; row 0x20 bytes: +0 type, +4 tile type, +8 u8 style (0xff any), +9/+0xa start/end sequence (start 0xff any), +0xc four cels, +0x1c cel count |
| `0x32bdf0` | (levelType, tileType, style, sequence) -> cel or -1: automap tile lookup, **no bounds check on levelType**. Hooked; entry `48 89 5C 24 10 48 89 74 24 18 48 89 7C 24 20` |
| `0x2372a40` | pointer to the automap seed {lo, hi} |
| `0x3da650` | level-entry finder: with levels.txt `Position` (level record +0x90) set it picks the arrival spot with `0x3dac20`, a random room from the maze's entry-room table (count at level +0x200, entries at +0x34, stride 0xc); an empty table gives (0, 0) and `0x3289a0` then reads `[rdx+0x50]` of null |
| `0xd5fe0` / `0xd5710` / `0xd1710` | per-character map memory (`<char>.map`, `.ma0`-`.ma3`): save / load / level switch. 100 buckets indexed by levels `Layer` directly; the loader rejects a cel >= 1499 by writing the node's own offset into its `next` (the next save loops for ever) |

## Sounds

| RVA | Signature | Meaning |
|---|---|---|
| `0x3b20c0` | () -> int | sounds.txt row count |
| `0x3b1fd0` | (id) -> record | sounds.txt record |
| `0xd8c70` | (id) -> u64 | record +0xc7; **reads null+0xc7 for an id outside the table**. Entry `40 53 48 83 EC 20 8B D9` |
| `0xd9180` | (id) -> u64 | record +0xb0 & mask; null+0xb0 when out of range. Entry `40 53 48 83 EC 20 8B D9` |
| `0xd9030` | (id, buffer, size) | the sound's file path into buffer; copies from null+0x32 when out of range. Entry `40 53 55 57 48 83 EC 20` |
| `0xd9e60` | (id, ?, ?) -> id | picks the sound to play; null+0x96 when out of range. Entry `40 55 53 41 54 41 55 48 8D 6C 24 F8` |
| `0xda360` | (id, mode) -> id | sound group variant; null+0x96 when out of range. Entry `40 53 55 56 57 41 55 48 83 EC 20` |
| `0x107dd0` | (unit, id) -> playing sound or null | client unit sound wrapper (calls `0xd8c70`). Entry `48 89 5C 24 08 48 89 74 24 10 57 48 83 EC 20` |
| `0x1079e0` | | client monster update: plays the monsounds Neutral sound (record +0x68, NeuTime +0x6c) through `0x107dd0` |

`0xd9210`, `0xd92f0` test the record for null; `0xd96a0` returns null; `0xdb700`, `0xdb8f0`, `0x1fd600` walk all rows:
these need no guard.

## Death and events

| RVA | Signature | Meaning |
|---|---|---|
| `0x44db20` | (game, victim, killer or null, flag) | unit death function (`D2Game\src\Unit\SUnitDmg.cpp`); returns at once for a monster in mode 0 / 12 or a player in mode 0 / 17; flag 1 also takes a pet off its owner's list. Hooked; entry `48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18 57 41 56 41 57 48 83 EC 50` (24 bytes) |
| `0x44d570` | (game, event, unit, other unit, arg) | unit event dispatch wrapper (raises an events.txt event on a unit) |
| `0x2a9a580` | data managers, 16 bytes apart | itemstatcost array at +0x1258, count at +0x1260; record 0x144 bytes, +0x44 first event id |
| unit +0x0c | u32 | unit mode |

## Missiles, pets, potions, cube outputs

| RVA | Meaning |
|---|---|
| `0x45ce90` | server missile hit function 6: spawns the monstats row `sHitPar1` through `0x50a050(game, room, sHitPar1, ...)` |
| `0x5c1400` | NecroPet AI think; branches on the pet's AI control (monster data +0x30) +0x44: 1 = ranged branch, 0 = melee (observed) |
| `0x57ecb0` | timed potion handler (item spell 3): stat calc = total over the duration for hitpoints, mana, manarecovery, hpregen (`<< 8`); other stats stored as calc / len |
| `0x5269c0` | cube executor; its "portal" output (byte 1) opens only the Cow Level |

## Item packets

| RVA | Meaning |
|---|---|
| `0x1c6b60` | client item packet handler (SCMD_ITEMEX); when the item could not be decoded it calls `0x1c6a40(0)` -> `0x34b870`, which returns `*(unit + 0x88)` with no null test (crash at `0x34b891`) |

### Item type test sites

`0x373890(item, type)` is called with these type numbers written into the code (25 sites with an immediate >= 67,
plus `jewl` 58 elsewhere). A mod's itemtypes.txt must give these codes these ids:

| Type | Code | Sites (instruction loading the type) |
|---|---|---|
| 75 | rune | `0x0cc192`, `0x1c85a4`, `0x23165a`, `0x2bd438`, `0x2c2934`, `0x2d9dc2`, `0x43c979`, `0x43cc33`, `0x475283`, `0x527f5a` |
| 77 | hpot | `0x15a026`, `0x2296d7`, `0x4f1e0e`, `0x4f4d1a` |
| 81 | apot | `0x15a037`, `0x2296f4`, `0x4f4d3c` |
| 82 | wpot | `0x15a046`, `0x2296e5`, `0x4f4d2c` |
| 68 | h2h | `0x3854f9` |
| 73 | pelt | `0x40bbef` |
| 83 | scha | `0x422fec` |
| 84 | mcha | `0x422ff7` |
| 96 | gem4 | `0x527eff` |

`mov edx, imm32` (`BA xx 00 00 00`) at most sites; `lea edx, [reg + imm8]` (`8D 50 xx` / `8D 51 xx`) at the potion
pairs and `scha`/`pelt`. The plugin template's `item_type_check.cpp` checks them.

## Renderer Pool

| What | RVA | Notes |
|---|---|---|
| Pool object | `0x2810ff0` | `+0` vptr, `+8` parent allocator, `+0x10` base, `+0x18` size, `+0x20` TLSF handle, `+0x28` lock |
| Pool start / stop | `0xf6efe0` / `0xf6ef70` | start takes `0xc00000` bytes (12 MB) from the parent, TLSF create `0x1215a30`; strings "Renderer Pool" / "TlsfAlloctor"; called once from the renderer start. Stop zeroes base and size |
| Vtable | `0x1d6d770` | 6 entries: `0x82dcf0` dtor, `0x82dad0` allocate(size, align), `0x82db40` reallocate(p, size, align), `0x82dbf0` TLSF realloc(p, size), `0x82dc60` free(p), `0x82dcb0` usable size(p). **Shared with six other allocator objects**: copy it, never patch it. No direct calls to the entries |
| Allocate wrapper | `0xf6f060` | (size, align); 100+ callers; on null reports "Failed to allocate from Renderer pool!" (`0xf6ef40`) and returns null |
| Free wrapper / object getter | `0xf6f0c0` / `0xf6efd0` | |
| A null-allocation crash site | `0xf3d028` in `0xf3cfd0` | moves 0x80-byte records into a new vector buffer (vector grow `0xf3d587`, model instance build, `D2Render\src\model_instance.cpp`) |

## HD asset indexes

| RVA | Meaning |
|---|---|
| `0xf75c60` | model asset cache init: reads `data/hd/model_lod_desc.bin`; a missing file asserts "Binary desc cache is missing! ALL model loads will be blocking!" |
| `0xf75fe0` | LOD resolver (cache, name, lodHint, out): FNV-1a 32 of the lower-cased path; unlisted: "Failed to find LOD for model %s" and the plain name is used (never drawn) |
| `0xf75af0` | lower-bound lookup in the 44-byte entry array |

## Engine-side crashes not caused by mods

| RVA | Meaning |
|---|---|
| `0x775fc0` | handler of event type 0x10 of the client's HD `UnitTranslationLayer` event loop (`0x77a3b0`); rarely reads a stale event payload and faults at `0x7761dc` while a character loads. A guard can skip events whose payload pointer is not readable |

## Client UI (crash locations seen)

| RVA | Meaning |
|---|---|
| `0x287a50` | skill tooltip line renderer (`D2Client UI/SkillDesc.cpp`): asserts `stringIndex1 != strdummy` when a line type's text is missing |
| `0x199cb0` | text formatting (`UI/text.cpp`), case 6 joins "was slain by" fragments and sprintf's them |
| `0x29538e` | access violation (null + 0x12e) when an item has no HD picture |

## Entry bytes summary (hooked in game)

| RVA | Bytes |
|---|---|
| `0x2f7ea0` | `40 53 56 41 57 48 83 EC 50 48 63 DA 45 8B F8` |
| `0x32bdf0` | `48 89 5C 24 10 48 89 74 24 18 48 89 7C 24 20` |
| `0x336440` | `48 89 5C 24 08 48 89 6C 24 10 56 57 41 56` |
| `0x33aa00` | `48 89 5C 24 08 57 48 83 EC 20 41 8B F8 33 DB` |
| `0x33b3b0` | `48 89 5C 24 10 48 89 6C 24 18 48 89 74 24 20` |
| `0x33f360` | `40 56 57 48 83 EC 28 48 8B FA 48 8B F1 48 85 D2` |
| `0x3b9dc0` | `48 89 5C 24 08 48 89 6C 24 18 56 57 41 56` |
| `0x43b3e0` | `48 89 5C 24 10 48 89 6C 24 18 48 89 74 24 20` |
| `0x56a420` | `48 89 5C 24 10 44 89 4C 24 20 55 56 57 41 54` |
| `0x56ac00` | `48 89 5C 24 18 55 56 57 41 56 41 57 48 81 EC 80 00 00 00` |
| `0x5817f0` | `48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18` |
| `0x581680` | `48 89 5C 24 18 48 89 6C 24 20 56 57 41 57` |
| `0x44db20` | `48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18 57 41 56 41 57 48 83 EC 50` |
| `0xd8c70` | `40 53 48 83 EC 20 8B D9` (an 8-byte expected-bytes check; D2RLoader accepted it and the hook ran in game) |

Table slots are written with `PatchWriteU64(table + 8*slot, expected, 8, function)`: expected 0 for an empty slot,
the current pointer when wrapping a D2R function. srvdofunc `0x238ea00` (free 172-190), cltdofunc `0x235f7d0` (free
110-129), server missile hit `0x2391030` (free 61-70), client missile hit `0x2358710` (6, 7 empty), server missile
do `0x2390e80` (46-54 empty), client missile do `0x2358430` (14 unused). Item spell flag:
`PatchWriteU32(0x236fe30 + 12*slot + 8, 0 -> 1)`.
