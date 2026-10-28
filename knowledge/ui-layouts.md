# HD UI layouts

D2R's HD UI is data: `data/global/ui/layouts/*hd.json` (classic UI: the same names without `hd`). A mod overrides a
layout by shipping the same file. **Patch D2R's own file** (read it from CASC, change the widgets you need, write
it whole) rather than writing a new one: the panel code looks widgets up by name.

## File format

- JSON with comments (`//`) and trailing commas: edit as text (or a lenient parser), don't round-trip through a
  strict JSON library unless it keeps comments.
- Root: `{ "type": "<PanelClass>", "name": "<LayoutName>", "fields": {...}, "children": [ widgets ] }`. The panel
  class (e.g. `PlayerInventoryPanel`, `BankPanel`, `HoradricCubePanel`, `SkillsTreePanel`) is code; the file only
  places its widgets.
- `"basedOn": "<Other>HD.json"` inherits another layout; children listed again with the same name override or
  extend it (e.g. `bankexpansionlayouthd.json` is based on `BankOriginalLayoutHD.json`,
  `playerinventoryexpansionlayouthd.json` on `PlayerInventoryOriginalLayoutHD.json`).
- Each widget: `{ "type": "...", "name": "...", "fields": {...}, "children": [...] }`. Coordinates are relative to
  the parent; children draw in order.
- `$Name` values are variables from `_profilehd.json` (and `_profilelv.json`, `_profileasian.json` for other
  profiles): e.g. `$ItemCellSize` = `{ "x": 98, "y": 98 }`, `$LeftPanelRect` = `{ x 236, y -651, 1162 x 1507 }`,
  `$RightPanelRect` = `{ x -1394, y -651, 1162 x 1507 }`, `$PanelClickCatcherRect` = 1162 x 1507. Text `@key` is a
  string key.
- `rect`: `{ "x", "y" [, "width", "height"] [, "scale"] }`. `"scale"` scales the widget and its contents.
- Panels are 1162 x 1507 HD pixels.

Common widget types: `ImageWidget` (`filename`, `frame`), `ButtonWidget` (`filename`, `hoveredFrame`,
`onClickMessage`, `tooltipString`), `TextBoxWidget` (`text`, `style`), `TabWidget` (`activationRect`,
`onClickMessage`), `TabBarWidget`, `InventoryGridWidget`, `InventorySlotWidget`, `AdvancedStashSlotWidget`,
`AnimatedImageWidget`, `ClickCatcherWidget`, `FocusableWidget` (tooltips), `ToggleButtonWidget`, plain `Widget`
(container).

## Grids

```json
{ "type": "InventoryGridWidget", "name": "grid",
  "fields": { "rect": { "x": 93, "y": 897 }, "cellCount": { "x": 10, "y": 4 }, "cellSize": "$ItemCellSize",
              "gemSocketFilename": "PANEL\\gemsocket" } }
```

- `cellCount` must equal inventory.txt `gridX`/`gridY` for that page (set **every** matching inventory.txt row:
  two rows per page).
- **Items draw at 98 px per cell whatever `cellSize` says.** Shrinking `cellSize` to fit a larger grid makes items
  overlap. To fit a bigger grid: keep `cellSize` `{ "x": 98, "y": 92 }` (or 98 x 98) and give the grid's `rect` a
  `"scale"` so the whole widget shrinks. The drawn cell is `98 * scale` px: a 12 x 12 grid in the space of
  the vanilla 10 x 10 one needs a scale of at most 10/12 = **0.83** (the showcase example uses 0.8).
- **Equipment slots** (`InventorySlotWidget`, names `slot_head`, `slot_neck`, `slot_torso`, `slot_right_arm`,
  `slot_left_arm`, `slot_right_hand`, `slot_left_hand`, `slot_belt`, `slot_feet`, `slot_gloves`; field `location`):
  keep D2R's native `width`/`height`, add the **same `scale`** as the grid, and centre the scaled box on the
  slot's place in your art: `x = (left + right - width*scale) / 2`.
- D2R's slot sizes (cells of 98): head, gloves, feet 196 x 196; neck and both ring slots (`slot_right_hand`,
  `slot_left_hand`) 98 x 98; torso 196 x 294; weapon arms 196 x 392; belt 196 x 98.

## Panel backgrounds

`"filename": "PANEL\\x\\y"` resolves to `data/hd/global/ui/panel/x/y.sprite` (+ `.lowend.sprite`). Replace a
panel by shipping your sprite under a new folder (e.g. `panel/mymod/inventory.sprite`) and changing the layout's
`filename` to `PANEL\\mymod\\inventory`. The stash lists its backgrounds per tab in the panel's
`backgroundFile` array (personal, shared, gems, materials, runes).

## Measuring positions

1. Draw or take the panel art at 1162 x 1507 (from classic 512 x 704 art: x 2.2695, y 2.1406).
2. Read pixel positions of grid corners and slots off the art; HD position = art position (relative to the
   panel's top-left).
3. Grid `rect.x/y` = top-left of the first cell; with a `scale`, the drawn cell is `98 * scale` wide.
4. Iterate from in-game screenshots: a few pixels off is normal on the first pass.

## Main layout files (93847 baseline)

| File | Purpose / key widgets |
|---|---|
| `playerinventoryoriginallayouthd.json` | Inventory (Classic/LoD look): `background` (`PANEL\\Inventory\\Classic_Background`), `grid`, `slot_*`, `gold_amount` (+ `gold_amount_tooltip`), `gold_button`, `close` |
| `playerinventoryexpansionlayouthd.json` | Inventory with weapon swap (based on the original): `PANEL\\Inventory\\Background`, swap tabs `background_right_arm(_selected)`, `background_left_arm(_selected)`, `text_i_left`/`text_ii_left`/`text_i_right`/`text_ii_right`, `weaponswap_right_arm`/`weaponswap_left_arm` |
| `bankoriginallayouthd.json` | Classic stash |
| `bankexpansionlayouthd.json` | Expansion stash (based on bankoriginal): `TabBarWidget` **`BankTabs`** (5 tabs: personal, shared, gems, materials, runes), `basicstash_container` with `grid` (10 x 10), `gold_amount`, `gold_withdraw`, `SharedStashTabContainer` -> `StashNavigation` (`StashLeftArrow`, `CurrentSharedStashText`, `StashRightArrow`), `PreviousSeasonToggleDisplay`, `advancedstash_gems` / `advancedstash_materials` / runes (`AdvancedStashSlotWidget` per item code) |
| `horadriccubelayouthd.json` | Cube: `background` (`PANEL\\Horadric_Cube\\HoradricCube_BG`), `grid` (3 x 4), `convert` (transmute button), `flourish` (animation drawn over the grid area: move it off screen, e.g. x/y -5000, if your grid sits there), `close` |
| `skillstreepanelhd.json` | Skill tree: fields `skillRow` (6 y values), `skillColumn` (3 x values), `skillButtonFile` / `skillBackgroundFile` / `textTab0-2` (one entry per class, 8 classes incl. Warlock); widgets `Tab0-2` (`TabWidget`, `activationRect`, `onClickMessage` `SkillsTreePanelMessage:ActivateTab:<n>`, child `Background` frame), `TextTab0-2`, `Title`, `SkillCounter`, `Cancel` |
| `skillselecthd.json` | Skill picker (hotbar left/right skill menu) |
| `tradelayouthd.json` | Player trade window (two grids) |
| `vendorpanellayouthd.json` | NPC shop |
| `hirelinginventorypanelhd.json` | Mercenary equipment |
| `characterstatspanelhd.json`, `advancedstatspanelhd.json` | Character sheet and the advanced stats panel |
| `hudpanelhd.json` | In-game HUD: orbs, belt, skill buttons, run/walk, menus |
| `globaldatahd.json` | Shared definitions (styles, colours) used by other layouts |
| `_profilehd.json` (+ `_profilelv.json`, `_profileasian.json`) | Variables (`$...`) for HD / low-vision / Asian profiles |
| `questlog*`, `waypointspanel*`, `partypanelhd.json`, `npcdialogpanelhd.json`, `automaphd.json`, `minimenupanelhd.json`, `tooltipspanelhd.json` | Other in-game panels |
| `mainmenupanelhd.json`, `characterselectpanelhd.json`, `charactercreatepanelhd.json`, `creategamepanelhd.json` | Front end |
| `controller/*.json` | Controller UI (same names, `basedOn` the PC layout, adds focus navigation `up`/`down`/`left`/`right` by widget **name** and `defaultWidget`); `controller/console/` and `controller/pc/` hold variants, plus `paladinaurapanelhd.json`, `emotewheelpanelhd.json`, `controlleroverlayhd.json` |

When you rename, move or resize widgets, apply the same change to the controller layout of that panel (it
references widgets by name).

## Tab bars

`TabBarWidget` (stash `BankTabs`): one row of `tabCount` tabs, each `tabSize` (195 x 72 in the stash) apart by
`tabPadding`; `filename` (tab sprite with inactive/active/disabled frames per tab), `textStrings` per tab,
`onSwitchTabMessage`. Move the whole bar with its `rect`; it does not wrap.

Skill tree tabs are separate `TabWidget`s. More than D2R's 3 work: 8 TabWidgets in one row (`ActivateTab:0` ..
`ActivateTab:7`) with their own `TextBoxWidget` labels were confirmed. D2R's panel fills tab titles only for
`textTab0-2`, and the stock per-tab `Background` frames assume 3 tabs (move them off screen). Keep the stock
`TextTab0-2` widgets (off screen) in case the code looks them up.

## Gold widgets

Inventory: `gold_button` (drop gold, `PANEL\\GoldButton`), `gold_amount` (`$StyleGoldAmount`) with a
`gold_amount_tooltip` child. Stash: `gold_withdraw` and `gold_amount` inside `basicstash_container`. They can be
moved anywhere on the panel with their `rect`.

## Checklist for a resized grid

1. inventory.txt `gridX`/`gridY` on every row of that page (both resolutions).
2. Layout `cellCount`, `cellSize` 98 x 92 (or 98), `rect.scale` so the grid fits.
3. Slots: native size, same scale, centred.
4. Panel sprite with matching art (1162 x 1507 + lowend).
5. Controller layout for the same panel.
6. Back up saves: a smaller grid later can strand items.
