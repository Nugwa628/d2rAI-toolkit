---
name: d2r-ui
description: Change Diablo II Resurrected's HD user interface in a modkit mod - inventory, stash, cube and trade grid sizes, panel backgrounds and art, widget positions, tabs, gold and buttons, skill tree layout, fonts/text in UI. Use when a request involves how a game screen looks or is laid out.
---

# HD UI: layouts, panels and grids

Read knowledge/ui-layouts.md first. D2R's HD UI = layout files `data/global/ui/layouts/*hd.json` (widget trees)
+ sprites under `data/hd/global/ui/`. Controller layouts are separate files (`*controller*` / `*hd.json` pairs):
change both if the mod supports controllers.

## Find what to change
- `python -m modkit widgets bankexpansionlayouthd.json` lists the widgets with their rects.
- `python -m modkit sprite2png d2r-data/<build>/data/hd/global/ui/panel/<...>.sprite out.png` shows any panel art
  (fetch it first with `python -m modkit fetch --set hd-ui`).
- Positions are panel pixels: HD panels are 1162 x 1507. Measure on the art you use; scale with
  `layouts.scaled()` if the art was drawn at another size.

## Grid sizes (inventory, stash, cube, trade)
Two places must agree:
1. inventory.txt `gridX` / `gridY` for the page (rows per page exist twice, e.g. `Big Bank Page 1` and
   `Big Bank Page2`, `Amazon` and `Amazon2`: set both). This is what the game uses for item placement and saves.
2. The layout's `InventoryGridWidget`: `cellCount`. Items always draw at 98 px per cell whatever `cellSize` says,
   so a bigger grid in the same space uses `"cellSize": {"x": 98, "y": 92}`-style sizes plus a rect `"scale"`
   (e.g. 0.8) to shrink the whole grid, items included. Equipment slots: keep D2R's width/height, give the rect
   the same scale, and centre it on the slot art.
Example op: `{"op": "layout", "file": "bankexpansionlayouthd.json", "widget": "grid", "set": {"cellCount": {"x": 12, "y": 12}, "cellSize": {"x": 98, "y": 92}, "rect": {"x": 89, "y": 219, "scale": 0.8}}}` (the showcase example's stash).
The personal stash, inventory and cube grids save item positions in 4 bits: at most 16 x 16.

## Panel art
`panel` op: a PNG (scaled to 1162 x 1507) becomes `PANEL\<mod>\<name>` and the layout's image reference switches to
it (`replaceImage`: the old `PANEL\...` name(s), e.g. both the personal and the shared stash backgrounds).
If your art has elements where D2R draws its own widgets (tabs, gold, buttons), either move them in the art or
move the widgets (`layout` op on their `rect`). Tab bars (`TabBarWidget`) draw all tabs in one row from `rect`
with `tabSize` + `tabPadding`; hidden tabs (e.g. stash extra tabs) still take their slot if they appear.

## Skill tree
skillstreepanelhd.json: tabs, icon positions per row/column (skilldesc SkillPage/Row/Column feed it), the
background sprites `data/hd/global/ui/spells/skill_trees/<xx>skilltree.sprite`. More than 3 tabs means new
TabWidgets in the layout and the game still only knows pages 1-3 from skilldesc (more pages = engine work).

## Build and test
Build, install with the game closed. Ask for a screenshot of each changed screen: check grid lines against the art,
items in the far corners, tab clicks, gold display, both HD and controller layouts if used, and low texture
quality (the .lowend sprites).
