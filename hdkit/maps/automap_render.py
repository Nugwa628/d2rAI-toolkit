"""The automap (minimap) of a map as the game builds it, classic and HD side by side.

Both graphics modes build the automap the same way, on the client, from the map's own tiles: each floor and wall tile's
orientation gives a tile code (fl, wl, wr, wtlr, wtll, wtr, wbl, wbr, wld, wrd, wle, wre, co, sh, tr, rf, ...), and
automap.txt gives the cell for (level type, code, style, sequence): the row whose style matches and whose sequence range
holds the tile's sequence, else that style's row with sequence -1 (any). A row lists up to four cells; one is picked per
tile. Preset objects add their objects.txt AutoMap cell. Classic draws the cells from global/ui/automap/maximap.dc6
(16 x 32), D2R HD from hd/global/ui/automap/defaultNN/tiles.sprite (64 x 128, 100 cells a sheet). A mod tile whose
(style, sequence) has no row of its level type is drawn as nothing in both modes; automap_rows adds those rows.

  python -m hdkit.maps automap-render MAP OUT_PREFIX [--level-type "2 Tomb"] [--automap automap.txt] [--art MOD_DATA]
writes OUT_PREFIX_classic.png, OUT_PREFIX_hd.png, OUT_PREFIX_pair.png (classic scaled 4x | HD) and OUT_PREFIX.json:
per tile code, how many tiles found a cell, the cells used, and the (code, style, sequence) keys without a row.

D2R's extract (the folder holding data/) comes from hdkit.toml ([mod] d2r_extract, default modkit's d2r-data/<build>);
`set_d2r()` or --d2r overrides it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from . import legacy

CODES = ["fl", "wl", "wr", "wtlr", "wtll", "wtr", "wbl", "wbr", "wld", "wrd", "wle", "wre", "co", "sh", "tr", "rf",
         "ld", "rd", "fd", "fi"]          # by orientation 0-19 (Diablo II's AutoMap.txt tile names)
CLASSIC_CELL = (16, 32, 8, 20)            # cell w, h; the tile diamond's left corner and top row inside the cell
HD_SCALE = 4

AUTOMAP_NAME = {"Act 5 - Ice Caves": "5 Ice"}     # automap.txt's LevelName where it is not lvltypes' name shortened

_D2R: list = []


def set_d2r(extract) -> None:
    """Use this D2R extract (the folder holding data/) instead of the configured one."""
    _D2R[:] = [Path(extract) / "data"]


def d2r_data() -> Path:
    """D2R's extract's data/ folder."""
    if _D2R:
        return _D2R[0]
    from hdkit import site
    ex = site.adapter().d2r_extract()
    if not ex:
        raise RuntimeError("no D2R extract: set [mod] d2r_extract in hdkit.toml, or fetch the game files with modkit")
    return Path(ex) / "data"


def level_type_name(lvltype: str) -> str:
    """'Act 2 - Tomb' -> '2 Tomb' (automap.txt LevelName; 'Act 5 - Ice Caves' -> '5 Ice')."""
    if lvltype in AUTOMAP_NAME:
        return AUTOMAP_NAME[lvltype]
    if lvltype.startswith("Act ") and " - " in lvltype:
        a, _, b = lvltype[4:].partition(" - ")
        return "%s %s" % (a.strip(), b.strip())
    return lvltype


def read_rows(path: Path, level: str) -> list:
    rows = []
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    head = lines[0].split("\t")
    for ln in lines[1:]:
        v = ln.split("\t")
        if len(v) < len(head):
            continue
        r = dict(zip(head, v))
        if r["LevelName"] != level:
            continue
        cels = [int(r["Cel%d" % i]) for i in range(1, 5) if r.get("Cel%d" % i, "").lstrip("-").isdigit()]
        rows.append((r["TileName"], int(r["Style"]), int(r["StartSequence"]), int(r["EndSequence"]),
                     [c for c in cels if c >= 0]))
    return rows


def lookup(rows: list, code: str, style: int, seq: int):
    """The cells of the row for this tile: exact sequence range first, then the style's any-sequence row."""
    anyseq = None
    for c, st, a, b, cels in rows:
        if c != code or st not in (style, -1):
            continue
        if a == -1:
            anyseq = anyseq or cels
        elif a <= seq <= b:
            return cels
    return anyseq


def tiles(ds):
    """(x, y, orientation, style, sequence) of every floor and wall tile drawn (not hidden, not empty)."""
    for f in ds.floors():
        ys, xs = np.nonzero(f)
        for y, x in zip(ys, xs):
            c = int(f[y, x])
            if (c & 0xFF) and not (c >> 31) & 1:
                yield int(x), int(y), 0, (c >> 20) & 0x3F, (c >> 8) & 0x3F
    for j, w in enumerate(ds.walls()):
        o = ds.orientation(j)
        ys, xs = np.nonzero(w)
        for y, x in zip(ys, xs):
            c = int(w[y, x])
            if (c & 0xFF) and not (c >> 31) & 1:
                yield int(x), int(y), int(o[y, x]), (c >> 20) & 0x3F, (c >> 8) & 0x3F


def object_cells(src) -> list:
    """(cell x, cell y, automap cell) of the map's preset objects with an objects.txt AutoMap cell (D2R's table; a mod
    that keeps D2R's object rows in place resolves the same way)."""
    from hdkit.view.mapview import units, world
    rows = world.read_table(d2r_data() / "global" / "excel" / "objects.txt")
    out = []
    act = src.ds.act + 1
    for o in src.ds.objects:
        if o.type != 2:
            continue
        cls = src.tables.objpreset.get((act, o.id))
        if not cls:
            continue
        i = units.d2r_object_rows(src.roots).get(cls.strip().lower())
        if i is None or i >= len(rows):
            continue
        am = (rows[i].get("AutoMap") or "").strip()
        if am.lstrip("-").isdigit() and int(am) > 0:
            out.append((o.x / 5.0, o.y / 5.0, int(am)))
    return out


class Art:
    """The automap cels of an act: classic from maximap.dc6 in the act palette, HD from the tiles.sprite sheets. `art`: a
    mod's data folder whose automap files come first (automap_art writes them)."""

    def __init__(self, act: int, art: Path = None):
        self.art = Path(art) if art else None
        self.dc6 = legacy.parse_dc6(self._file(Path("global") / "ui" / "automap" / "maximap.dc6").read_bytes())
        self.pal = legacy.act_palette(act)
        self._c, self._h, self._sheets = {}, {}, {}

    def _file(self, rel: Path) -> Path:
        return self.art / rel if self.art and (self.art / rel).is_file() else d2r_data() / rel

    def classic(self, cel: int) -> np.ndarray:
        if cel not in self._c:
            w, h, _ox, _oy, block = self.dc6[cel]
            self._c[cel] = legacy.decode(w, h, block, self.pal)
        return self._c[cel]

    def hd(self, cel: int) -> np.ndarray:
        if cel not in self._h:
            k = cel // 100
            if k not in self._sheets:
                self._sheets[k] = (self._file(Path("hd") / "global" / "ui" / "automap" / ("default%02d" % k)
                                              / "tiles.sprite")).read_bytes()
            self._h[cel] = legacy.sprite_frame(self._sheets[k], cel % 100)
        return self._h[cel]


def _paste(can: np.ndarray, im: np.ndarray, x: int, y: int):
    h, w = im.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, can.shape[1]), min(y + h, can.shape[0])
    if x0 >= x1 or y0 >= y1:
        return
    src = im[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    dst = can[y0:y1, x0:x1].astype(np.float32)
    a = src[..., 3:4] / 255.0
    dst[..., :3] = src[..., :3] * a + dst[..., :3] * (1 - a)
    dst[..., 3:4] = np.maximum(dst[..., 3:4], src[..., 3:4])
    can[y0:y1, x0:x1] = dst.astype(np.uint8)


def render(key: str, level_type: str = None, automap: Path = None, bg=(16, 12, 20), art: Path = None) -> dict:
    from hdkit.view.mapview import api
    src = api.open_map(key, "mod")          # the mod's tables, also for a D2R map a mod level uses
    info = api.map_info(key, "mod")
    lt = level_type or level_type_name(info["levels"][0]["level_type"])
    rows = read_rows(automap or d2r_data() / "global" / "excel" / "automap.txt", lt)
    ds = src.ds
    if art and (Path(art) / "global" / "tiles" / (key + ".ds1")).is_file():     # the mod's copy (automap_void)
        from hdkit.view.mapview.ds1 import Ds1
        ds = Ds1.read(Path(art) / "global" / "tiles" / (key + ".ds1"))
    art = Art(src.ds.act + 1, art)
    W, H = src.ds.width, src.ds.height
    cw, ch, ax, ay = CLASSIC_CELL
    ox, oy = (H + 1) * 8 + cw, ch                                   # canvas origin: cell (0, 0)'s diamond top
    size = ((W + H + 2) * 8 + 2 * cw, (W + H + 2) * 4 + 2 * ch)
    cl = np.zeros((size[1], size[0], 4), np.uint8)
    hd = np.zeros((size[1] * HD_SCALE, size[0] * HD_SCALE, 4), np.uint8)
    cl[...] = bg + (255,)
    hd[...] = bg + (255,)
    stats = {"level_type": lt, "rows": len(rows), "codes": {}, "cells": Counter(), "no_row": Counter()}
    order = sorted(tiles(ds), key=lambda t: (t[0] + t[1], t[2] != 0))   # back to front, floors first per cell
    for x, y, o, st, sq in order:
        code = CODES[o] if o < len(CODES) else "o%d" % o
        s = stats["codes"].setdefault(code, {"tiles": 0, "with_cell": 0})
        s["tiles"] += 1
        cels = lookup(rows, code, st, sq)
        if not cels:
            if code not in ("fl", "sh"):                      # most floors have no cell: that is the automap's design
                stats["no_row"]["%s %d %d" % (code, st, sq)] += 1
            continue
        s["with_cell"] += 1
        cel = cels[(x * 31 + y * 17) % len(cels)]
        stats["cells"][cel] += 1
        px, py = ox + (x - y) * 8 - ax, oy + (x + y) * 4 - ay
        _paste(cl, art.classic(cel), px, py)
        _paste(hd, art.hd(cel), px * HD_SCALE, py * HD_SCALE)
    objs = object_cells(src)
    for x, y, cel in objs:
        px, py = int(ox + (x - y) * 8 - ax), int(oy + (x + y) * 4 - ay)
        _paste(cl, art.classic(cel), px, py)
        _paste(hd, art.hd(cel), px * HD_SCALE, py * HD_SCALE)
        stats["cells"][cel] += 1
    stats["objects"] = len(objs)
    stats["cells"] = dict(stats["cells"].most_common())
    stats["no_row"] = dict(stats["no_row"].most_common())
    return {"classic": cl, "hd": hd, "stats": stats}


def main(argv=None) -> int:
    from PIL import Image
    ap = argparse.ArgumentParser(prog="python -m hdkit.maps automap-render", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map")
    ap.add_argument("prefix")
    ap.add_argument("--level-type", default=None)
    ap.add_argument("--automap", default=None)
    ap.add_argument("--art", default=None, help="a mod's data folder: its automap.txt and automap art come first")
    ap.add_argument("--d2r", default=None, help="D2R's extract (the folder holding data/)")
    a = ap.parse_args(argv)
    if a.d2r:
        set_d2r(a.d2r)
    am = Path(a.automap) if a.automap else None
    if a.art and not am and (Path(a.art) / "global" / "excel" / "automap.txt").is_file():
        am = Path(a.art) / "global" / "excel" / "automap.txt"
    r = render(a.map, a.level_type, am, art=a.art)
    out = Path(a.prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(r["classic"]).save(str(out) + "_classic.png")
    Image.fromarray(r["hd"]).save(str(out) + "_hd.png")
    big = Image.fromarray(r["classic"]).resize((r["hd"].shape[1], r["hd"].shape[0]), Image.NEAREST)
    pair = Image.new("RGB", (big.width * 2, big.height))
    pair.paste(big, (0, 0))
    pair.paste(Image.fromarray(r["hd"]).convert("RGB"), (big.width, 0))
    pair.save(str(out) + "_pair.png")
    Path(str(out) + ".json").write_text(json.dumps(r["stats"], indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in r["stats"].items() if k != "cells"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
