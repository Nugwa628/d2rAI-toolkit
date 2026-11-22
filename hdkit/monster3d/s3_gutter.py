"""Gutter fill: fill the atlas gutter (texels no triangle covers) from the nearest covered texel, in every map of an overlay.

  python -m hdkit.monster3d.s3_gutter [--monster <monster.json>] <overlay dir> <skinned_lod0.npz> [--strip 0.125]
        [--name NAME] [--report <json>]

Why: a monster about 80 px tall on screen samples its atlas at mip 3 to 4 (13x minification, knowledge/hd-monsters.md).
Every mip averages a chart's border texels with the gutter next to it. A projection bake leaves the gutter with other
colours (measured on one rigid part: 812,860 gutter texels at albedo L 82 and glow 10, against L 6 and glow 0 in its dark
centre), so at game size bright blobs creep into the chart borders of a dark part. Filling the gutter with each chart's
own edge colours keeps the mips true. Maps: the albedo, the ORM and the glow masks (<vfx_textures>/*_glow_*_mask) of the monster config's folders;
--name picks the file names (default the config's name). The
corpse strip (the right --strip of the atlas, top --strip rows) is left as it is; only the live area is filled.
Coverage: the LOD0 triangles rasterised in the (squashed) live UV layout, row = v * height, plus one texel of margin.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as nd

from hdkit.formats import d2r_texture as t
from hdkit.monster3d import common


def coverage(npz, size):
    z = np.load(npz)
    U, T = z["uv"], z["tris"]
    im = Image.new("L", (size, size))
    dr = ImageDraw.Draw(im)
    for tri in T:
        dr.polygon([(U[i, 0] * size, U[i, 1] * size) for i in tri], fill=1)
    return nd.binary_dilation(np.asarray(im).astype(bool), iterations=1)


def fill_array(a, cov, strip):
    """Texels of the live area (the left 1 - strip of the width) that `cov` does not cover take the nearest covered texel's
    value. Returns (filled copy, hole mask)."""
    h, w = cov.shape
    x0 = int(round(w * (1 - strip)))
    live = np.zeros((h, w), bool)
    live[:, :x0] = True
    hole = live & ~cov
    src = cov & live
    _, (iy, ix) = nd.distance_transform_edt(~src, return_indices=True)
    out = a.copy()
    out[hole] = a[iy[hole], ix[hole]]
    return out, hole


def fill(path, cov_full, strip, report):
    fmt, w, h, lv = t.read_texture(path)[:4]
    a = t.decode(fmt, w, h, lv[0])
    cov = np.asarray(Image.fromarray(cov_full.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 127
    out, hole = fill_array(a, cov, strip)
    ch = out.shape[-1] if out.ndim == 3 else 1
    before = float(a[hole].astype(float).mean()) if hole.any() else 0.0
    after = float(out[hole].astype(float).mean()) if hole.any() else 0.0
    if fmt == 63:
        t.write_bc4(path, out[..., 0] if out.ndim == 3 else out, len(lv))
    else:
        t.write_bc3(path, out, mips=len(lv), linear=path.name.endswith("_orm.texture"))
    report[path.name] = {"format": fmt, "size": [w, h], "gutter_texels": int(hole.sum()), "channels": ch,
                         "gutter_mean_before": round(before, 2), "gutter_mean_after": round(after, 2)}


def main():
    a = sys.argv[1:]
    opt = lambda n, d: a[a.index(n) + 1] if n in a else d  # noqa: E731
    ov, npz = Path(a[0]), a[1]
    strip, name = float(opt("--strip", "0.125")), opt("--name", common.MONSTER["name"])
    if not name:
        raise SystemExit("s3_gutter: give --monster <monster.json> or --name NAME")
    folder = common.FOLDER if common.MONSTER["name"] == name else "hd/character/enemy/m3d/" + name
    vfx = common.MONSTER["vfx_textures"] if common.MONSTER["name"] == name else "hd/vfx/textures/m3d/" + name
    cov = coverage(npz, 2048)
    rep = {"coverage_share_2048": round(float(cov.mean()), 4)}
    maps = [ov / folder / ("%s_%s.texture" % (name, k)) for k in ("alb", "orm")]
    maps += sorted((ov / vfx).glob("%s_glow_*_mask.texture" % name))
    for p in maps:
        if p.is_file():
            fill(p, cov, strip, rep)
    print(json.dumps(rep))
    if "--report" in a:
        Path(opt("--report", "")).write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
