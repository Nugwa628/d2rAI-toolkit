"""Reference frames: a monster's classic frames, and optionally their 4x upscale.

  python -m hdkit.monster3d.s1_frames --monster <monster.json> classic
        every mode: frame PNGs at 1x on the mode's common canvas + frames.json (each mode's box with the unit's ground point
        as origin, directions, frames, COF speed), in <stages>/1-frames/classic/<mode>/d<D>_f<NN>.png
  python -m hdkit.monster3d.s1_frames --monster <monster.json> up MODE:FRAME [...]
        the directions of those frames 4x (de-dither + Real-ESRGAN x4plus, alpha by Lanczos + threshold), GPU gate first.
        Optional: needs realesrgan-ncnn-vulkan (HDKIT_M3D_REALESRGAN, an outside program; its model weights are
        BSD-3-Clause and never shipped here). The hand-built route needs only `classic`.

The token comes from the monster config (token), the palette from its act (act, default 1). The classic files are read from
the folder hdkit.formats.monster_sheet finds (HDKIT_CLASSIC, hdkit.toml [mod] classic, else D2R's own extract).
"""
import json
import sys

import numpy as np
from PIL import Image

from hdkit.formats import monster_sheet
from hdkit.monster3d import common

OUT = common.STAGES / "1-frames"


def need_token() -> str:
    if not common.TOKEN:
        raise SystemExit("s1_frames: the monster config has no token (the classic monster token)")
    return common.TOKEN


def classic():
    data = monster_sheet.load(need_token())
    pal = monster_sheet.act_palette(int(common.MONSTER.get("act") or 1))
    meta = {}
    for mode, e in data.items():
        box = monster_sheet.mode_box(e)
        cof = e["cof"]
        d = OUT / "classic" / mode
        d.mkdir(parents=True, exist_ok=True)
        for di in range(cof["dirs"]):
            for f in range(cof["frames"]):
                monster_sheet.compose(e, di, f, box, pal).save(d / ("d%d_f%02d.png" % (di, f)))
        meta[mode] = {"box": box, "dirs": cof["dirs"], "frames": cof["frames"], "speed": cof["speed"]}
        print(mode, meta[mode], flush=True)
    (OUT / "classic" / "frames.json").write_text(json.dumps(meta, indent=1))


def up(picks, method="dedither+esrgan:realesrgan-x4plus"):
    from hdkit.sprite3d import upscale
    if not common.gpu_gate(240):
        return 1
    data = monster_sheet.load(need_token())
    pal = monster_sheet.act_palette(int(common.MONSTER.get("act") or 1))
    work = common.EV / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    for pick in picks:
        mode, f = pick.split(":")
        e = data[mode.lower()]
        box = monster_sheet.mode_box(e)
        frames = [np.asarray(monster_sheet.compose(e, d, int(f), box, pal)) for d in range(e["cof"]["dirs"])]
        big = upscale.upscale(frames, 4, method, {"exe": str(common.ESRGAN), "work": str(work)})
        d = OUT / "x4" / ("%s%02d" % (mode.lower(), int(f)))
        d.mkdir(parents=True, exist_ok=True)
        for di, b in enumerate(big):
            Image.fromarray(b, "RGBA").save(d / ("nu_d%d.png" % di))
        print(pick, "->", d, big[0].shape, flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("classic", "up"):
        raise SystemExit(__doc__)
    if sys.argv[1] == "classic":
        classic()
    else:
        sys.exit(up(sys.argv[2:]))
