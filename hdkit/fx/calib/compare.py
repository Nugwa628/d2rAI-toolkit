"""A viewer render of an effect against a game shot at the same camera and effect time.

The game shot holds the unit and its effect over the scene; the twin is the same camera before the unit was spawned. The
effect's footprint in the game is where shot and twin differ (|shot - twin| per channel above `thresh`); the render's is
its alpha (or its difference from a given base render). Both are cut to the render's size around the anchor (the unit's
feet: `feet` in the shot, `render_feet` in the render), the best shift within `reach` px is found
(hdkit.view.compare.best_shift), then per mask: IoU, mean added light (shot - twin, the effect's contribution, which is what
an additive / blended particle layer puts on screen), luma middle values (p50) and the colour of the brightest tenth.
Nothing here is specific to one effect or game build."""
import json
from pathlib import Path

import numpy as np

from ...view import compare as vcmp
from ...view import metrics


def _load(p, mode="RGB"):
    from PIL import Image
    return np.asarray(Image.open(p).convert(mode))


def effect_mask(shot, twin, thresh: float = 12.0) -> np.ndarray:
    d = np.abs(shot[..., :3].astype(np.int16) - twin[..., :3].astype(np.int16)).max(-1)
    return d > thresh


def _bright(rgb, m, frac=0.1):
    if m.sum() < 10:
        return None
    px = rgb[m].astype(np.float32)
    lum = px @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    k = max(1, int(len(lum) * frac))
    return np.round(px[np.argsort(lum)[-k:]].mean(0), 2).tolist()


def compare(render, shot, twin, feet, render_feet=None, base=None, reach: int = 12, thresh: float = 12.0,
            alpha_cut: int = 8) -> tuple:
    """render: (H, W, 3|4) uint8; shot, twin: (h, w, 3) uint8; base: the render without the effect (same size), else the
    render's alpha is its mask. Returns (result dict, images dict)."""
    r = np.asarray(render)
    H, W = r.shape[:2]
    rf = tuple(render_feet or (W / 2.0, H * 0.75))
    s = vcmp.cut(np.asarray(shot)[..., :3], feet, rf, (W, H))
    t = vcmp.cut(np.asarray(twin)[..., :3], feet, rf, (W, H))
    gm = effect_mask(s, t, thresh)
    if base is not None:
        rm = effect_mask(r[..., :3], np.asarray(base)[..., :3], thresh)
        radd = r[..., :3].astype(np.float32) - np.asarray(base)[..., :3].astype(np.float32)
    elif r.shape[-1] == 4:
        rm = r[..., 3] >= alpha_cut
        radd = r[..., :3].astype(np.float32) * (r[..., 3:4] / 255.0)
    else:
        raise ValueError("compare: an RGBA render or a base render is needed for the render's mask")
    iou0 = metrics.iou(rm, gm)
    iou, dx, dy = vcmp.best_shift(rm, gm, None, reach)
    rm2 = np.roll(np.roll(rm, dy, 0), dx, 1)
    radd2 = np.roll(np.roll(radd, dy, 0), dx, 1)
    rgb2 = np.roll(np.roll(r[..., :3], dy, 0), dx, 1)
    gadd = s.astype(np.float32) - t.astype(np.float32)

    def mean(a, m):
        return np.round(a[m].mean(0), 3).tolist() if m.sum() else None
    both = gm & rm2
    res = {"size": [W, H], "feet": list(feet), "render_feet": list(rf), "thresh": thresh,
           "iou_at_feet": round(float(iou0), 4), "iou_best": round(float(iou), 4), "shift": [int(dx), int(dy)],
           "game_px": int(gm.sum()), "render_px": int(rm.sum()),
           "added_game": mean(gadd, gm), "added_render": mean(radd2, rm2),
           "added_game_shared": mean(gadd, both), "added_render_shared": mean(radd2, both),
           "luma_game": vcmp.luma_p50(s, gm), "luma_render": vcmp.luma_p50(rgb2, rm2),
           "bright_game": _bright(s, gm), "bright_render": _bright(rgb2, rm2)}
    return res, {"shot": s, "twin": t, "game_mask": gm, "render_mask": rm2, "render": rgb2}


def panel(imgs) -> np.ndarray:
    """shot | render | masks (game red, render green, both yellow)."""
    s, r = imgs["shot"], imgs["render"]
    m = np.zeros_like(s)
    m[..., 0] = imgs["game_mask"] * 255
    m[..., 1] = imgs["render_mask"] * 255
    return np.concatenate([s, r, m], 1).astype(np.uint8)


def compare_files(render, shot, twin, feet, render_feet=None, out=None, base=None) -> dict:
    res, imgs = compare(_load(render, "RGBA"), _load(shot), _load(twin), feet, render_feet,
                        _load(base) if base else None)
    if out:
        from PIL import Image
        o = Path(out)
        o.mkdir(parents=True, exist_ok=True)
        Image.fromarray(panel(imgs)).save(o / "compare_panel.jpg", quality=88)
        (o / "compare.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        res["panel"] = str(o / "compare_panel.jpg")
    return res
