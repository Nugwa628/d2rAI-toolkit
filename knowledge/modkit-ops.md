# modkit patch operations

A patch is a JSON file in `<project>/patches/` holding `{"ops": [...]}`. Patches apply in file-name order (use
numbered names: `010-skills.json`, `020-stash.json`), each op in order, on top of the pristine game files and the
project's `files/data/` copies. `modkit build` reports the first failing op with its file and index.

Paths are game paths (`data/global/...`, `data/hd/...`). Table names are file names without `.txt`
(`skills`, `skilldesc`, `missiles`, `weapons`, `treasureclassex`, ...). Unless an op says otherwise, table ops
apply to both banks, `excel/` (what D2R plays) and `excel/base/` (the Classic/LoD bank), when the table exists
there; pass `"bank": "excel"` or `"bank": "base"` to limit it.

## Tables

| op | fields | effect |
|---|---|---|
| `set` | `table`, `key` (+ `column` to match another column) or `where: {col: value}`, `set: {col: value}` | change cells of existing rows |
| `clone_row` | `table`, `from`, `key`, `set` | copy a row under a new key, appended (new ID), then override |
| `add_row` | `table`, `values` | append a new row |
| `delete_row` | `table`, `key` | remove a row: keyed tables only; in indexed tables it shifts every later ID (knowledge/tables.md) |

Formula cells: bare negative numbers inside formulas are rewritten to `(0-N)` (D2R's parser rejects `-N`).
Plain numbers stay as they are.

## Strings

| op | fields | effect |
|---|---|---|
| `string` | `key`, `text` (str, or `{lang: text}`), `file` (default `item-modifiers.json`), `allLanguages` (default true), `force` | set a string key, or add it with a fresh ID; changing printf placeholders of an existing key is refused unless `force` (D2R formats some keys itself) |

## UI

| op | fields | effect |
|---|---|---|
| `layout` | `file` (layout name or game path), `widget`, `field` + `value` and/or `set: {field: value}`, `occurrence`, `replace: [[old, new]]` | edit a layout widget's fields (rect, cellCount, filename, textStrings, ...) or replace text |
| `panel` | `png`, `name` (`mymod/stash`), `layout`, `replaceImage` (str or list), `size` (default 1162 x 1507) | new panel background sprite + switch the layout's image reference to `PANEL\mymod\stash` |
| `sprite` | `png`, `to` (game path ending `.sprite`), `size`, `frames`, `lowend` | any PNG as an HD sprite (+ half-size `.lowend`) |

## HD looks

| op | fields | effect |
|---|---|---|
| `missile_hd` | `missile`, `like` (vanilla missile) or `model` | missiles.json entry: without one a new missile is invisible in HD |
| `item_hd` | `code` / `unique` / `set`, `asset`, optional `png` + `table` + `cells` | inventory picture: points the item at an existing asset, or converts a PNG to one (98 px per cell) |
| `hd_sequence` | `skill`, `like`, optional `class` | HD body animation for a sequence (anim `SQ`) skill, copied from a skill that has one |
| `json` | `file`, `path` (list of keys / indexes), `value` | set any value in any JSON game file |
| `copy` | `from` (project file), `to` (game path) | ship a file as it is (sounds, models, anything prepared elsewhere) |

## Skills

`skill` builds a whole skill (skills + skilldesc rows, strings, tree slot, icon, missiles, states, HD
animation). Its fields are documented at the top of `modkit/skill.py` and in `.claude/skills/d2r-skill/SKILL.md`.
New class skills: each class has exactly 30, and saves keep one byte per class skill. Without
`"extendedSaves": true` in mod.json (D2RLoader's extended save format) the op refuses to add a 31st, so
rework an existing skill with `replace` instead.

## Not ops (do these elsewhere)

- Engine behaviour that no table column can express (a new skill function, a new cube condition, new calc codes,
  more level types): a D2RLoader plugin, from `plugin-template/` (knowledge/engine-re.md).
- Character saves for testing: `modkit save-info` / `save-set` (knowledge/saves.md).
