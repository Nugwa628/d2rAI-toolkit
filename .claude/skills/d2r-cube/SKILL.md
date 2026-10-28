---
name: d2r-cube
description: Create or change Horadric Cube recipes in a Diablo II Resurrected modkit mod - crafting, upgrades, rerolls, socketing, portals, gold or condition-gated recipes. Use for any cube/crafting/transmute request.
---

# Cube recipes (cubemain.txt)

`python -m modkit show cubemain "<description>"` shows a vanilla recipe; `find cubemain op=...` finds by column.

## A recipe row
- `description` (key, any text), `enabled` 1, `version` 100 (expansion), `min diff`, `class` (restrict).
- Inputs: `numinputs` (total item count), `input 1-7`: an item code, item type, unique/set name in quotes, with
  qualifiers after commas: `"rin,mag"` (magic ring), `qty=3`, `sock=2`, `nos` (no sockets), `pre=`/`suf=`, `upg`,
  `bas`/`exc`/`eli`, `nru`, `noe`. Quote a cell with commas.
- Output: `output` (code, type, "Cow Portal", "Pandemonium Portal", "useitem", "usetype", ...) with qualifiers
  (`mag`, `rar`, `uni`, `set`, `crf`, `tmp`, `sock=N`, `rep`, `rch`, `pre=`, `suf=`, `reg`, `exc`, `eli`,
  `uns`, `rem`), `lvl`/`plvl`/`ilvl` (item level rules), `mod 1-5` + `chance`/`param`/`min`/`max` (properties);
  `output b`/`output c` with the same columns for extra outputs.

## Conditions (op/param/value): D2R hard limits
D2R checks two kinds of conditions (recipe search 0x529090 and input matcher 0x529d50, build 93847):
- **Player conditions, ops 1-14** (on the player: date ranges, stat comparisons using param = stat ID and
  value). Any other op number here is **ignored (the recipe always passes)**, it does not fail.
- **Item conditions, ops 15-27+** are checked on each input item (stat comparisons on the item).
So a recipe gated on something D2R doesn't check (gold cost, a quest flag, a random chance, a second condition)
needs a plugin hook on the recipe search; see the d2r-engine skill and knowledge/d2r-93847-addresses.md (cube).
Prefer item-based gating (an input token item, a stat on the input) which works in data alone.

## Build and test
Build, validate (it checks codes and qualifiers), install with the game closed. Test list: each recipe with exact
inputs, with one input missing (must fail), with an extra item (must fail), and the output's stats.
