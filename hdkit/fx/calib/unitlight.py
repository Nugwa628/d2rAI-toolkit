"""The game's unit light, read from one extracted `ps_models_p` draw of a RenderDoc capture (extract + its bda files), as a
light rig for the viewer (gpu.Light.game, shaders.wgsl game_shade). docs/hdfx.md section 3, "Unit light".

  rig = unitlight.rig_from_draw(draw_dir, game_view=gv_dict)       # view.rig.Rig named "capture"
  python -m hdkit.fx.calib unitlight DRAW_DIR --game-view game_view.json [--clut clut.npy] [--direct-scale S] --out rig.json
  (the post LUT: tone.clut; dump it from the post pass's bound "Universal CLUT", R16G16B16A16 65^3, as float rgb)

Where the numbers sit (D2R through vkd3d-proton: root CBVs are buffer addresses in the push block; extract saves the 4 KiB
behind push word N as bda_<N>.bin; the SPIR-V of the pixel shader read with SPIRV-Cross):
  bda_40  (root CBV 5, frame)      float 0: exposure; float 196: directional shadow strength
  bda_48  (root CBV 6, IBL)        floats 0..3: IBL intensity, screen AO on, diffuse weight, specular weight;
                                   float4 1..9: SH irradiance (world axes) 1, y, z, x, xy, yz, 3z^2 - 1, xz, x^2 - y^2
  bda_56  (root CBV 7, material)   float4 18: value curve poly . (1, V, V^2, V^3); floats 76, 77, 79: brightness, strength
                                   (characterBrightnessFactor), on
  bda_72  (root CBV 9, view)       float4 9..11: view -> world rotation rows, 12: eye
  bda_112 (root CBV 14, dir lights) uint 0: count; light i: float4 1+3i colour, 2+3i (diffuse w, specular w, intensity,
                                   shadow mode), 3+3i direction the light travels (view space)
  light grid (descriptors at push words 28 / 29: p_u0_* tile words, p_u1_* index lists, p_t0_* light records):
    tile word bits 0..5 decals, 6..15 point lights, 16..23 spot lights, 24..31 shadowed point lights;
    point record: float4 0 position (view space), 1 colour + radius, 2 x falloff exponent, y weights (2 x 16-bit unorm);
    shadowed point: float4 0 position, 1 colour + radius, 2 specular position, 3 x exponent, y weights.
"""
import json
from pathlib import Path

import numpy as np

SH_NAMES = ("1", "y", "z", "x", "xy", "yz", "3z2-1", "xz", "x2-y2")


def _f(path) -> np.ndarray:
    return np.fromfile(path, np.float32)


def _weights(word) -> tuple:
    w = int(np.asarray(word, np.float32).view(np.uint32))
    return round((w & 0xFFFF) * 1.525902189314365e-05, 4), round((w >> 16) * 1.525902189314365e-05, 4)


def view_basis(cb9: np.ndarray):
    """(R, eye): rows of R are the view axes in world (world = view @ R + eye)."""
    r = cb9.reshape(-1, 4)
    return r[9:12, :3].astype(float), r[12, :3].astype(float)


def grid_lights(ddir: Path) -> list:
    """Every local light the capture's light grid lists (any tile), with its kind."""
    g = next(iter(sorted(ddir.glob("p_u0_*.bin"))), None)
    lst = next(iter(sorted(ddir.glob("p_u1_*.bin"))), None)
    rec = next(iter(sorted(ddir.glob("p_t0_*.bin"))), None)
    if not (g and lst and rec):
        return []
    G = np.fromfile(g, np.uint32)
    L = np.fromfile(lst, np.uint32).reshape(len(G), -1)
    T = _f(rec).reshape(-1, 4)
    seen, out = set(), []
    for t, v in enumerate(G):
        nd, npnt, nsp, nsh = v & 63, (v >> 6) & 1023, (v >> 16) & 255, v >> 24
        for k, i in enumerate(L[t, nd:nd + npnt + nsp + nsh]):
            kind = "point" if k < npnt else ("spot" if k < npnt + nsp else "shadowed_point")
            if (kind, int(i)) in seen:
                continue
            seen.add((kind, int(i)))
            if kind == "spot":
                out.append({"kind": kind, "index": int(i)})
                continue
            par = T[i + 3] if kind == "shadowed_point" else T[i + 2]
            dw, sw = _weights(par[1])
            out.append({"kind": kind, "index": int(i), "pos_view": T[i, :3].tolist(), "color": T[i + 1, :3].tolist(),
                        "radius": float(T[i + 1, 3]), "exp": float(par[0]), "diffuse": dw, "specular": sw})
    return out


def read_draw(ddir) -> dict:
    """The raw unit-light numbers of one extracted ps_models_p draw (view space as the game holds them)."""
    ddir = Path(ddir)
    frame, ibl, mat, view, dl = (_f(ddir / ("bda_%d.bin" % o)) for o in (40, 48, 56, 72, 112))
    dlu = dl.view(np.uint32)
    lights = []
    for i in range(int(dlu[0])):
        c, p, d = dl[4 * (1 + 3 * i):][:4], dl[4 * (2 + 3 * i):][:4], dl[4 * (3 + 3 * i):][:4]
        lights.append({"color": c[:3].tolist(), "diffuse": float(p[0]), "specular": float(p[1]), "intensity": float(p[2]),
                       "shadow_mode": int(np.float32(p[3]).view(np.uint32)), "travel_view": d[:3].tolist()})
    R, eye = view_basis(view)
    return {"exposure": float(frame[0]), "shadow_strength": float(frame[196]),
            "ibl": {"intensity": float(ibl[0]), "screen_ao": float(ibl[1]), "diffuse": float(ibl[2]),
                    "specular": float(ibl[3])},
            "sh": ibl[4:40].reshape(9, 4)[:, :3].tolist(),
            "value": {"poly": mat[72:76].tolist(), "brightness": float(mat[76]), "strength": float(mat[77]),
                      "on": bool(mat.view(np.uint32)[79])},
            "model_flags": int(mat.view(np.uint32)[80]),     # cb7[80]: bit 1 = the shadowed-point loop runs
            "view_to_world": R.tolist(), "eye": eye.tolist(), "dir_lights": lights, "local": grid_lights(ddir)}


def to_world(raw: dict, unit_pos, reach=(8.0, 14.0)) -> dict:
    """Light.game (world axes, positions relative to the unit's origin) from read_draw's numbers. Local lights are kept
    when their sphere reaches the unit's (centre `reach[0]` above the origin, radius `reach[1]`)."""
    R, eye = np.asarray(raw["view_to_world"]), np.asarray(raw["eye"])
    up = np.array([0.0, 1.0, 0.0])
    unit = np.asarray(unit_pos, float)
    dirs = [{"name": "dir%d" % i, "dir": (-np.asarray(L["travel_view"]) @ R).round(5).tolist(),
             "color": [round(c * L["intensity"], 5) for c in L["color"]], "diffuse": L["diffuse"],
             "specular": L["specular"], "shadow_mode": L["shadow_mode"]} for i, L in enumerate(raw["dir_lights"])]
    pts = []
    for p in raw["local"]:
        if "pos_view" not in p:
            continue
        rel = np.asarray(p["pos_view"]) @ R + eye - unit
        if np.linalg.norm(rel - up * reach[0]) > p["radius"] + reach[1]:
            continue
        pts.append({"name": "%s%d" % (p["kind"], p["index"]), "pos": rel.round(4).tolist(), "color": p["color"],
                    "radius": p["radius"], "exp": p["exp"], "diffuse": p["diffuse"], "specular": p["specular"],
                    "shadowed": p["kind"] == "shadowed_point"})
    return {"dirs": dirs, "sh": [list(map(float, c)) for c in raw["sh"]], "sh_terms": list(SH_NAMES),
            "ibl": {k: raw["ibl"][k] for k in ("intensity", "diffuse", "specular")}, "points": pts,
            "exposure": raw["exposure"], "value": raw["value"], "direct_scale": 1.0,
            "shadowed_points": bool(raw.get("model_flags", 0) & 2)}


def rig_from_draw(ddir, game_view: dict | None = None, unit_pos=None, source: str = ""):
    """A view.rig.Rig ("capture") whose unit light is the game's at that draw. The legacy light fields (used by lit
    particles and other draws) stay the `game` preset's, so only units change."""
    from ...view import rig as Rg
    raw = read_draw(ddir)
    if unit_pos is None:
        unit_pos = ((game_view or {}).get("unit") or {}).get("position") or [0.0, 0.0, 0.0]
    g = to_world(raw, unit_pos)
    base = Rg.game()
    from dataclasses import replace
    r = Rg.Rig("capture", replace(base.light, game=g), raw["exposure"], base.tone, None, [], [], [], {}, {})
    r.meta = {"source": source or str(ddir), "unit_pos": list(map(float, unit_pos)),
              "shadow_strength": raw["shadow_strength"], "screen_ao": raw["ibl"]["screen_ao"],
              "lights_view": raw["dir_lights"], "local_all": raw["local"]}
    r.assumptions = ["shadow maps not drawn: direct_scale %g stands in for the directional shadow (strength %g in game)"
                     % (g["direct_scale"], raw["shadow_strength"]),
                     "split-sum LUT and the specular cube: Karis's analytic fit and the SH irradiance at the reflection "
                     "direction / pi stand in", "lights are those of this frame (camera, time of day, the unit's place)"]
    r.unknown = ["specular cube map (not extracted)", "shadow map visibility per pixel"]
    return r


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="hdkit.fx.calib unitlight")
    ap.add_argument("draw")
    ap.add_argument("--game-view")
    ap.add_argument("--unit-pos", help="x,y,z world position of the unit's origin (else the game view's)")
    ap.add_argument("--direct-scale", type=float, default=1.0, help="stand-in for the directional shadow (no shadow map "
                    "in the viewer): the direct light's mean visibility over the unit (Mephisto r3: 0.65, fitted)")
    ap.add_argument("--clut", help="the game's post LUT as .npy (65, 65, 65, 3): tone 'clut' (tone.clut)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    gv = json.loads(Path(a.game_view).read_text()) if a.game_view else None
    up = [float(v) for v in a.unit_pos.split(",")] if a.unit_pos else None
    r = rig_from_draw(a.draw, gv, up, source=str(Path(a.draw).resolve()))
    r.light.game["direct_scale"] = a.direct_scale
    if a.direct_scale != 1.0:
        r.assumptions[0] = ("shadow maps not drawn: direct_scale %g (fitted: the capture's flesh display luma) stands in for "
                            "the directional shadow (strength %g in game)" % (a.direct_scale, r.meta["shadow_strength"]))
    if a.clut:
        r.tone, r.post["clut"] = "clut", str(Path(a.clut).resolve())
    r.save(a.out)
    g = r.light.game
    print(json.dumps({"out": a.out, "exposure": g["exposure"], "dirs": [(d["name"], d["color"], d["dir"]) for d in g["dirs"]],
                      "points": [(p["name"], p["color"], p["radius"]) for p in g["points"]], "value": g["value"]}, indent=1))


if __name__ == "__main__":
    main()
