# HD graphics: formats and rules

D2R draws HD from `data/hd/...` and classic ("legacy", G key) from `data/global/...`. Both are loaded: some classic
files are needed even in HD. A mod overrides any file by shipping the same path in its MPQ folder.

## `.sprite` (SpA1) format

All HD UI pictures (items, panels, skill icons, buttons) are `.sprite` files, plus a `.lowend.sprite` at half size
for the low graphics setting.

| Offset | Type | Value |
|---|---|---|
| 0x00 | char[4] | `SpA1` |
| 0x04 | u16 | version **31** |
| 0x06 | u16 | frame width (single-frame sprites: = width; strips: one frame's width, e.g. 130) |
| 0x08 | u32 | total width (strip: frames x stride) |
| 0x0c | u32 | height |
| 0x10 | u32 | 0 |
| 0x14 | u32 | frame count |
| 0x18 | u32 | 0 |
| 0x1c | u32 | 0 |
| 0x20 | u32 | pixel data size (width x height x 4) |
| 0x24 | u32 | 4 |
| 0x28 | | RGBA8 pixels, row by row, top to bottom |

Verified on D2R's own `amskillicon.sprite`: v31, frame width 130, width 7920 (60 frames x 132), height 130, 60
frames, size 4,118,400. Maximum texture width **16,384 px**. Write premultiplied-alpha-aware scaling (bilinear with
alpha weighting) so edges don't go dark.

`.lowend.sprite`: the same header and layout at half the pixel size.

## Item pictures

| Rule | Value |
|---|---|
| Inventory cell | **98 px** HD, **49 px** lowend (classic 28 px; scale x3.5) |
| Sprite size | `invwidth x 98` by `invheight x 98`, picture centred |
| Location | `data/hd/global/ui/items/<weapon|armor|misc>/<folder>/<name>.sprite` (+ `.lowend.sprite`) |
| Asset name | the path under that table folder without extension, e.g. `axe/hand_axe` |

Maps under `data/hd/items/` (JSON arrays of one-key objects):

| File | Key | Value |
|---|---|---|
| `items.json` | base item `code` | `{ "asset": "axe/hand_axe" }` |
| `uniques.json` | uniqueitems `index` lowercased, spaces and hyphens -> `_`, apostrophes dropped (`Buriza-Do Kyanon` -> `buriza_do_kyanon`) | `{ "normal": ..., "uber": ..., "ultra": ... }` (picture per base tier) |
| `sets.json` | setitems `index`, same normalisation | same as uniques |

**Every item code, unique and set item needs an entry pointing at an existing sprite.** A missing one makes the
client request `data/hd/global/ui/items/<table>/[default_unit]` every frame (blz-log spam) and then crash (vendor
screen, Item Spawner: access violation at D2R+0x29538e). Entries for uniques/sets the mod removed must go too (a
dangling entry for a removed unique crashed the Item Spawner). Stopgap: map new items to a D2R picture of the same type until art
exists.

## Skill icons

| Rule | Value |
|---|---|
| Classic file | `data/global/ui/spells/<xx>skillicon.dc6` (shared: `skillicon.dc6`) |
| HD file | `data/hd/global/ui/spells/<class>/<xx>skillicon.sprite`: `amazon/amskillicon`, `sorceress/soskillicon`, `necromancer/neskillicon`, `paladin/paskillicon`, `barbarian/baskillicon`, `druid/drskillicon`, `assassin/asskillicon`, `warlock/waskillicon`; non-class skills `submenu/skillicon`; mercenary skills `hireables/hrskillicon` (skilldesc `HireableIconCel`) |
| Referenced as | `Spells\<Class>\<Xx>Skillicon` (skillstreepanelhd.json `skillButtonFile`) |
| Frames | two per icon: normal, then pressed. skilldesc `IconCel` = frame index (even) |
| Size | **130 px** icons, **132 px stride**, one horizontal strip; D2R draws icons at the sprite's pixel size (no scaling to the slot) |
| Capacity | 16,384 / 132 / 2 = **62 icons** per strip at full size; more icons need smaller frames (looks small) |
| Shared sheet | Every class-less skill (Attack, Throw, item charges and oskills) reads `submenu/skillicon` by `IconCel`, the same cel in classic and HD. Its 62-icon cap is shared by all of them: choose which item skills keep an icon (most granted first) |
| Converting a classic mod's sheets | Class sheets are pairs (normal, pressed), but a classic **shared** sheet may hold **one frame per icon**. Read each sheet's real layout: rounding every frame down to even showed the previous icon for every odd frame |

Skill tree backgrounds: `Spells\Skill_Trees\<Xx>Skilltree` (`skillBackgroundFile`, one per class).

## Panel backgrounds

| Rule | Value |
|---|---|
| Size | **1162 x 1507** (`$LeftPanelRect` / `$RightPanelRect` in `_profilehd.json`) |
| Reference | layout `"filename": "PANEL\\x\\y"` = `data/hd/global/ui/panel/x/y.sprite` (case-insensitive) |
| From classic 512 x 704 art | scale x 2.2695 across, x 2.1406 down |
| Examples | `PANEL\\Inventory\\Classic_Background`, `PANEL\\Inventory\\Background`, `PANEL\\Horadric_Cube\\HoradricCube_BG`, `PANEL\\Stash\\StashPanel_BG_Personal` / `_Shared` |

Panel art may hold things D2R draws itself elsewhere (a gold box where D2R's stash tabs go): cut and move them in
the sprite build step. See [ui-layouts.md](ui-layouts.md).

## Missiles

- `data/hd/missiles/missiles.json`: one object, `"dependencies": {...}` first, then **missile name (lowercased) ->
  model name**; models are `data/hd/missiles/<model>.json` (+ particles/textures).
- `CelFile` in missiles.txt is used **only by the classic renderer**.
- A missile missing from the map is **invisible in HD** (it still works). Map new missiles to a D2R model with a
  similar look; `"null"` for invisible ones. A good automatic stopgap: the model D2R uses for a vanilla missile with
  the same `CelFile`.

## Overlays (auras, cast effects, state visuals)

- overlay.txt `Character` -> folder: `data/hd/overlays/<Character>/<overlay>.json` (name = overlay key). D2R's
  folders: `common` (141 rows), `paladin` 45, `monster` 35, `sorceress` 29, `druid` 26, `warlock` 25, `assassin` 10,
  `amazon` 6, `necro` 6, `barbarian` 4, `object`, `objects`, `npcs`, `item`, `test`.
- **Empty `Character` = no HD overlay** (classic still draws `Filename`). Symptoms: no aura glow, no cast effect,
  Whirlwind with no spin effect (only the get-hit frames show).
- New overlays: copy a D2R overlay json into your folder, or reuse D2R's overlay row.

## Player animations (HD state machines)

- `data/hd/character/player/<class>/<class>_state_machine.json` (amazon, assassin, barbarian, druid, necromancer,
  paladin, sorceress, warlock; barbarian and sorceress also have a `_shell` variant).
- JSON: `states` (each `AnimationState` has `stateId`, `modeId`, `skillIndex`, `name`, `animationBindings` per weapon
  class `hth`/`1hs`/`2hs`/`stf`/..., `enterEvents`, `exitEvents`) and `transitions` (one `AnimationTransitionGroup`
  per `from` state, with `settings` items `{to, crossfadeSeconds}`).
- **Sequence skills pick their clip by skill ID**: a state with `modeId` **18** (SQ) and `skillIndex` = the skill's
  row ID (Barbarian `PMODE_SEQUENCE_WHIRLWIND`: skillIndex 151).
- A new sequence skill has no HD body animation until it has a state. Fix: copy the D2R state whose skill has the
  **same `seqnum`**, give it a new `stateId` (max + 1) and the new `skillIndex`; add a transition group from it
  (copy of the source's group) and an entry to it in every other group (copy of the entry to the source).
- Skills whose sequence has no D2R clip have no HD body animation (new art needed).

## Monsters

- Two sources: monstats `Code` -> classic animation set `data/global/monsters/<code>/cof/<code><mode><wclass>.cof`
  (+ DCC), **used even in HD for timing**; and `data/hd/character/monsters.json`: **monstats `Id` -> HD model name**
  (`data/hd/character/enemy/<model>/...`, with its own `<model>_state_machine.json`).
- A missing COF crashes ("Could not add cof file"); a monster `Id` missing from monsters.json has no HD model.
- Cheapest new monster: reuse a D2R monster's `Code` and monstats2 visual columns, and map its `Id` to that HD model.
  With its own classic files (COF/DCC + `animdata.d2` records) it keeps its own timing and classic art and only
  borrows the HD model.
- `animdata.d2` (classic frame timing) must contain every Code+mode+weapon class used; merge D2R's file with yours.

## New models and textures at new paths

`.model`, `.skeleton`, `.animations` and `.texture` files work at **new paths** as plain files of the mod folder
(e.g. `data/hd/character/<mod>/<unit>/`), so you never have to overwrite a vanilla unit. But D2R finds models and
textures through two index files built at start-up, not through the file system:

| Index | Key | A path that is not listed |
|---|---|---|
| `data/hd/model_lod_desc.bin` (14,310 entries in 93847) | FNV-1a 32 (`0x811c9dc5` / `0x1000193`) of the lower-cased game path, e.g. `data/hd/character/enemy/<unit>/torso.model` | Opened as the plain file, loaded without an error, **never drawn**. The log says `Failed to find LOD for model ...` |
| `data/hd/texture_desc_cache.json` (31,073 entries) | `filename` | Read with the wrong format or size: colour noise, or the GPU device is lost |

A mod's own copy of an index replaces the game's, so build both at build time from the installed game's files with
your paths added. Each new entry copies the entry of a vanilla unit of the same kind.

`model_lod_desc.bin` layout (little endian): a header of three `{u32 count, i32 self-relative offset}` pairs; array
1 is `count1` 44-byte entries **sorted by key**: `u32 key | f32 bbMin[3] | f32 bbMax[3] | u8 lodMask (bit k+1 = LOD
k) + 3 pad | u32 lodCount | u32 streamOffset | u32 streamSize`. `streamOffset` is self-relative and points into
array 2 (12-byte records into array 3, 32-byte per-mesh stream records). Adding an entry moves arrays 2 and 3, so
**rewrite every entry's `streamOffset`**: a stale one asserts `isMeshFlipbook == GetModelInfo()->isMeshFlipbook`
(model_asset.cpp:397, `Invalid data detected!` in d2rloader.log) and the game dies in the first preload.

Other rules for new units:

- A listed model path is opened as `<base>_lodN.model` for each LOD its mask lists.
- Every `Model` in a `.model` file needs at least one mesh binding (`model_asset.cpp:305 MeshBindingCount >= 1`).
- Animations are read from `<clip directory>/combined.animations`, not from single `.animation` files.
- Unit JSON, `monsters.json` and state machine JSON files are not indexed; any path works.
- D2RLoader's ResourceService can serve plugin files at new paths too (limits: 256 MiB per plugin load, 64 MiB per
  file), but models still need the index entry. Plain mod-folder files are simpler.

## Stand-ins first

Make the rules work before the art. For each new thing, ship the closest vanilla HD look:

| New thing | Stand-in |
|---|---|
| Monster | The vanilla monster closest in shape and role (map its monstats `Id` to that HD model in `monsters.json`; scale or recolour later) |
| Missile | The vanilla missile model with the same element and shape (`missiles.json`) |
| Item | The vanilla picture of the same base type (`items.json`), or a bilinear upscale of the classic DC6 |
| Skill icon | An upscale of the classic icon |
| Panel | An upscale of the classic panel (1162 x 1507) |

Keep a list of every stand-in so the art work is easy to plan.

## Legacy (classic, G mode) files still needed

| File type | Why |
|---|---|
| `.dt1` / `.ds1` (`data/global/tiles`) | Map building, in HD too. Missing tile -> black screen / crash on entering the area |
| `.cof` + `.dcc` monsters, `animdata.d2` | Timing in HD; art in classic mode |
| `.dcc` missiles and overlays | Classic mode crashes on a missing graphics file when the effect spawns |
| `.dc6` item pictures (`data/global/items/<invfile>.dc6`) | Classic inventory art |
| `.dc6` skill icon sheets | Classic skill icons |

D2R ships its own legacy art (about 21,000 .dcc, 3,600 .cof, 1,300 .dc6, 266 .dt1, 2,277 .ds1): reuse it freely.
Ship only new files; leave D2R's names alone so its HD and classic versions stay matched.
