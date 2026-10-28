---
name: d2r-items
description: Create or change Diablo II Resurrected items in a modkit mod - base items (weapons, armor, misc, charms, jewels), item types, uniques, set items and sets, runewords, gems/runes, magic/rare affixes, item properties and stats, treasure classes and drops, vendors, and item pictures in HD. Use for any loot, itemization or drop-rate request.
---

# Items, affixes and loot

Read knowledge/tables.md (item tables section) and knowledge/engine-limits.md first in a session. Always start
from a vanilla row: `python -m modkit show weapons "Crystal Sword"`, `show uniqueitems "The Gnasher"`.

## Base items (weapons.txt, armor.txt, misc.txt)
- `clone_row` a similar base, then set `code` (3-4 chars, unique across all three tables), `name` (string key),
  `namestr`, damage/defense columns, `levelreq`, `reqstr`/`reqdex`, `gemsockets`, `type` (itemtypes code),
  `invwidth`/`invheight`, `normcode`/`ubercode`/`ultracode` (the exceptional/elite chain), `level` (item level for
  treasure classes), `rarity`, `cost`.
- Pictures: classic `invfile` (an existing DC6 name, e.g. the clone's) and HD via the `item_hd` op: an existing
  asset (`"asset": "weapon/sword/crystal_sword"`) or your PNG (`"png"`, `"table"`, `"cells": [w, h]`, 98 px per
  cell). Without an items.json entry the item shows a missing-picture placeholder and vendors can crash.
- Give the name a string (`string` op, file `item-names.json`).
- New item types: itemtypes.txt row (`Code`, `Equiv1/2` parents, `BodyLoc1/2`, `MaxSockets*`, `Throwable`, ...).

## Stats and properties
Item stats come from properties.txt codes (`prop` in uniques/sets/runewords/affixes) that map to
itemstatcost.txt stats (func 1-36 per property). New behaviour on a stat (procs, conversions) is engine code;
new *display* is itemstatcost `descfunc`/`descval`/`descstrpos`/`descstrneg` (vanilla descfunc numbers only).
Adding stats to itemstatcost: append rows; `Save Bits`/`Save Add`/`CSvBits` decide how they persist; more than D2R's
stat ID width needs D2RLoader extended saves.

## Uniques, sets, runewords
- uniqueitems.txt: `index` (key), `code` (base), `lvl`, `lvl req`, `rarity`, `prop1-12` + `par`/`min`/`max`.
  HD picture: `item_hd` op with `"unique": "<index>"`.
- setitems.txt + sets.txt: a set has at most 6 items in D2R (engine limit); `prop1-9` and partial bonuses
  `aprop1a..5b`; sets.txt `PCode2a..`/`FCode1..` for set bonuses.
- runes.txt (runewords): `Rune1-6` (misc codes), `itype1-6`/`etype1-3`, `T1Code1-7` properties: **7 property
  slots max** in D2R; `complete` 1 to enable.
- Gems/runes as socket fillers: gems.txt (weaponMod/helmMod/shieldMod 1-3 per socket target).

## Affixes
magicprefix.txt / magicsuffix.txt (+ rareprefix/raresuffix for names; automagic.txt for class items): `name`
(string key), `spawnable`, `rare`, `level`, `maxlevel`, `levelreq`, `group` (one affix per group), `mod1-3`
code/param/min/max, `itype1-7`/`etype1-5`, `frequency`.

## Drops (treasureclassex.txt)
Rows: `Treasure Class` (key), `Picks` (negative = pick each item that many times), `NoDrop`, `Item1-10` +
`Prob1-10`, `Unique`/`Set`/`Rare`/`Magic` quality boosts, `level`. Items are base codes, other TC names, or
auto classes `weapN`/`armoN` (built from item `level` in steps of 3). A unique/set by name is allowed. Monsters
point at TCs in monstats `TreasureClass` / `TreasureClassChamp` / `TreasureClassUnique` / `TreasureClassQuest`
(+ `(N)` / `(H)` per difficulty, and the Terror Zone `TreasureClassDesecrated*` columns); superuniques `TC`.

## Vendors
Vendor stock: weapons/armor/misc columns `<Vendor>Min/Max/MagicMin/MagicMax/MagicLvl` (Charsi, Gheed, Akara,
Fara, Lysander, Drognan, Hratli, Alkor, Ormus, Elzix, Asheara, Cain, Halbu, Jamella, Larzuk, Malah, Anya).

## Engine traps
- itemtypes: keep D2R's own rows at D2R's numbers (`warl` 67); D2R's code tests 75 rune, 77 hpot, 68 h2h ... by
  number (knowledge/engine-limits.md).
- A property with function 28 (layer = param) needs `Save Param Bits` on its stat, or the item crashes the client
  when it drops (knowledge/saves.md).
- Timed potions (`pSpell` 3): the calc of stats other than life/mana/manarecovery/hpregen is per frame; multiply by
  `len`.

## Build and test
Build, fix validator errors, install with the game closed. Test list: spawn with the debug Item Spawner, check
the tooltip, HD and classic pictures, sockets/runeword, drops from the intended monster (many kills or a TC test).
