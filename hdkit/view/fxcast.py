"""`python -m hdkit.view fx <any .particles> --cast "<skill>" --tables <excel> [--unit <caster JSON>] [--toward dx,dy] ...`

A whole skill cast drawn as the game does (hdkit.fx.context.cast): the caster's cast overlays (skills.txt castoverlay ...) on
the caster from the cast mode's start and the skill's missiles from the mode's action frame (animdata.d2 of the caster's COF),
from the caster's sub-tile, as many and on the paths the game makes (Charged Bolt: calc1 bolts on seeded random walks).
Every effect of every child driver gets its own Sim; all are drawn in one frame.

  --cast-t0 launch|mode   effect time 0 = the missile launch (default, as a calib bench's ages) or the mode start
  --cast-level 1          skill level (missile count formulas)
  --cast-cof SOSCBOW      the caster's animdata record (default: <unit token>SC<--wclass>; SO for the sorceress)
  --animdata <file>       animdata.d2 (default: <tables>/../animdata.d2, then data/global/animdata.d2 of the roots)
  --wclass bow            the caster's weapon class: its HD clips <wclass>_cast / <wclass>_neutral
  --cast-overlay-at start|lead|cast   when the caster's overlays start (default start: with the cast mode, measured by lane
                          r5-casttiming; lead: 0.08 s before the action frame; cast: at it). Give the caster's real
                          --wclass: the action frame comes from its COF (the bench sorceress holds a staff: SOSCSTF, 0.123 s)
  --cast-body             draw the caster's HD body too (default: only its bones drive the overlays; the background
                          already shows the game's caster)
  --cast-seed N           the bolts' seed offset (default 0; the game's is the cast index + the caster's x sub-tile)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from . import camera, cli, scene, vfs

UNIT_TOKENS = {"amazon": "AM", "sorceress": "SO", "necromancer": "NE", "paladin": "PA", "barbarian": "BA", "druid": "DZ",
               "assassin": "AI", "warlock": "WK"}


class ClipPoses:
    """Bone worlds of a caster: `<wclass>_cast` stretched over the classic mode length, then `<wclass>_neutral` looping
    (hdkit.fx.context poses interface: worlds(t), index(bone), duration)."""

    def __init__(self, unit, skeleton, wclass: str, mode_len: float, hold: float = 0.5):
        self.unit, self.sk, self.mode_len, self.duration = unit, skeleton, mode_len, mode_len + hold
        self.cast = unit.clips.get("%s_cast" % wclass)
        self.neutral = unit.clips.get("%s_neutral" % wclass)
        self._cache: dict = {}

    def clip_at(self, t: float):
        """(clip name, clip time, loop) at driver time t."""
        if t < self.mode_len and self.cast is not None:
            return self.cast.name, max(t, 0.0) / self.mode_len * self.cast.duration, False
        if self.neutral is not None:
            return self.neutral.name, t - self.mode_len, True
        return (self.cast.name, self.cast.duration, False) if self.cast is not None else (None, 0.0, False)

    def worlds(self, t: float) -> np.ndarray:
        from . import gr2
        k = round(t * 100.0)
        if k not in self._cache:
            nm, ct, loop = self.clip_at(t)
            self._cache[k] = gr2.pose_at(self.sk, self.unit.clips.get(nm) if nm else None, ct, loop=loop).worlds
        return self._cache[k]

    def index(self, bone: str) -> int:
        from ..fx import context as C
        return C._bone_index(self.sk, bone)


def _animdata(a, fs, tables_dir):
    paths = [a.animdata] if a.animdata else []
    if tables_dir:
        paths.append(str(Path(tables_dir).parent / "animdata.d2"))
    for p in paths:
        if p and Path(p).is_file():
            return Path(p).read_bytes(), p
    for lg in ("data/global/animdata.d2", "global/animdata.d2"):
        try:
            return fs.read(lg), lg
        except (OSError, KeyError, FileNotFoundError):
            continue
    return None, None


def build(a, fs):
    """(driver, unit, info) of the cast `a.cast`."""
    from ..fx import context as C
    tdir = a.tables or os.environ.get("HDFX_TABLES")
    if not tdir:
        raise SystemExit("--cast needs the txt tables: --tables <folder> or HDFX_TABLES")
    tb = C.Tables.from_dir(tdir)
    sk_fx = C.effects_of_skill(fs, a.cast, tb)
    unit = scene.load_unit(fs, a.unit, a.lod) if a.unit else None
    token = None
    if unit is not None:
        token = UNIT_TOKENS.get(Path(a.unit).stem.lower())
    cof = a.cast_cof or ("%s%s%s" % (token, sk_fx.mode.upper(), a.wclass.upper()) if token else None)
    raw, src = _animdata(a, fs, tdir)
    anim = C.animdata_record(raw, cof) if raw is not None and cof else None
    dx, dy = (float(v) for v in a.toward.split(","))
    yaw = C._yaw_of([2.0 * dx, 0.0, 2.0 * dy])
    mode_len = (anim["frames"] * 256.0 / anim["speed"] / C.FPS) if anim else 0.56
    poses = None
    if unit is not None:
        skel = next((p.skeleton for p in unit.parts if getattr(p, "skeleton", None) is not None), None)
        if skel is not None:
            poses = ClipPoses(unit, skel, a.wclass, mode_len)
    drv = C.cast(sk_fx, unit, (dx, dy), a.cast_level, a.cast_seed, anim, poses=poses, yaw=yaw,
                 overlay_at=a.cast_overlay_at, fs=fs)
    info = dict(drv.cast_info, cof=cof, animdata=src, anim=anim, tables=tdir)
    return drv, unit, info


def items_of(drv):
    """[(label, child driver, layer index, effect path)] of every layer of every child driver."""
    out = []
    for _t, ch in drv.children:
        for i, lay in enumerate(ch.layers):
            if isinstance(lay.effect, str):
                out.append(("%s/%s" % (ch.name, lay.label or i), ch, i, lay.effect))
    return out


def step_to(sm, ch, i, age, h=0.02):
    """Step `sm` to effect age `age` of child driver `ch` (layer i), the context moved before each step (driver time =
    ch.start + age). Returns the layer's transform at that age."""
    lay = ch.layers[i]
    now = float(getattr(sm, "t", 0.0)) if getattr(sm, "started", False) else 0.0
    if now > age + 1e-9:
        sm.reset()
        now = 0.0
    while True:
        step = min(h, age - now)
        sm.ctx = ch.context(ch.start + now + max(step, 0.0), i)
        if getattr(sm, "view_eye", None) is not None and getattr(sm.ctx, "camera", None) is None:
            sm.ctx.camera = sm.view_eye           # view.position = the render camera (lane r3-overlays)
        if hasattr(ch, "attrs_at"):                   # the game's missile attributes (frame counter, index)
            sm.attrs_in = {**ch.attrs_at(ch.start + now + max(step, 0.0), getattr(ch, "index", 0)),
                           **getattr(sm, "user_attrs", {})}
        stop_s = getattr(ch, "stop_s", None)          # a cast overlay's end: effect.isRunning false (lane r3-overlays)
        if stop_s is not None and getattr(sm, "running", True) and now + max(step, 0.0) >= stop_s > now - 1e-9:
            if stop_s - now > 1e-9:
                sm.step(stop_s - now)
                now = stop_s
            sm.stop()
            continue
        if step <= 1e-9:
            if not getattr(sm, "started", False):
                sm.step(0.0)
            break
        sm.step(step)
        now += step
    return ch.layer_transform(ch.start + age, lay)


def run(a, log=print) -> dict:
    from .. import gpugate
    from ..fx import render as fxr_mod, rspec
    from . import fxcli, gpu, rig
    if not a.no_gate:
        rd = gpugate.check()
        if not rd.free:
            raise SystemExit("GPU gate: %s (use --no-gate to override)" % rd.why)
    fs = vfs.Layered(vfs.parse_roots(a.root))
    drv, unit, info = build(a, fs)
    R = gpu.Renderer(msaa=a.msaa, aniso=a.aniso)
    from ..fx import lighting as fx_lighting
    rg = fx_lighting.resolve_rig(a.rig, fs)          # scene:<biome>@tod: the general setup (fx/lighting.py)
    post = fxr_mod.Post(rg, a.gain, a.exposure)
    fxr = fxr_mod.FxRenderer(R, fs, post)
    W, H = cli.parse_size(a.size)
    origin = cli._origin(a, W, H)
    cam = camera.OrthoCamera(a.el, a.az, camera.K_1600x900 * a.scale)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    effs, sims = {}, []
    attrs = fxcli.parse_attrs(getattr(a, "attr", None), getattr(a, "color_variant", None))
    plate = (getattr(rg, "lighting", None) or fxr_mod.PLATE_IRRADIANCE) \
        if getattr(a, "plate_light", "default") == "default" else (
        None if a.plate_light in ("off", "none", "0") else [float(x) for x in a.plate_light.split(",")])
    for k, (label, ch, i, path) in enumerate(items_of(drv)):
        from ..fx import context as C
        real = C.resolve_particle(fs, path) or path
        if real not in effs:
            data = fs.read(real)
            eff = rspec.load(data)
            eff.path = real
            effs[real] = (eff, data)
        eff, data = effs[real]
        sm = fxcli.make_sim(data, a.seed + k, ch.layer_transform(ch.start, ch.layers[i]), attrs, cam.eye() if getattr(a, "view_eye", "camera") != "default" else None)
        sm.user_attrs = dict(attrs or {})
        sims.append((label, ch, i, eff, sm))
    t0 = info["launch_s"] if a.cast_t0 == "launch" else 0.0
    only, hide = fxcli._ints(a.only), fxcli._ints(a.hide_fx) or []
    floor = 0.0 if a.ground == "depth" else None
    gdraw = fxcli.ground_draw(R, a.ground, a.ground_size) if a.ground and a.ground not in ("none", "depth") else None
    bg = cli.parse_bg(a.bg, (W, H))
    name = a.name or ("cast_" + a.cast.replace(" ", "_").lower())
    times = [float(x) for x in a.times.split(",")] if a.times else [a.t + k * a.dt for k in range(max(1, a.frames))]
    report = {"command": "fx --cast", "cast": info, "camera": {"el": a.el, "az": a.az, "scale": a.scale}, "size": [W, H],
              "ss": a.ss, "rig": rg.as_dict(), "effects": sorted(effs), "t0": a.cast_t0, "frames": [],
              "driver": {"cast": a.cast, "toward": a.toward, "children": [(round(t, 4), c.name) for t, c in drv.children]},
              "renderers": {p: rspec.summary(e) for p, (e, _d) in effs.items()}}
    images = []
    for t in times:
        tic = time.time()
        td = t + t0
        draws, lights, alive = [], [], []
        for label, ch, i, eff, sm in sims:
            age = td - ch.start
            if age < 0:
                continue
            P = step_to(sm, ch, i, age, a.sim_dt)
            draws += fxr.prepare(eff, sm.state(), cam, P, only, hide)
            lights += fxr.lights
            alive.append(label)
        fxr.lights = lights
        base = gpu.Prepared([])
        shadow_caster = unit is not None and not a.cast_body and bool(getattr(rg.light, "shadow", None))
        if unit is not None and (a.cast_body or shadow_caster):
            poses = drv.poses
            nm, ct, loop = poses.clip_at(td) if hasattr(poses, "clip_at") else (None, 0.0, False)
            base = R.prepare(unit, nm, ct, drv.yaw, cam, pose_kw={"loop": loop}).select(a.hide)
            if shadow_caster:     # the shot already shows the caster: its body only casts (its shadow in E0 / E1)
                for dr in base.draws:
                    dr.cast_only = True
        if gdraw is not None:
            base = gpu.Prepared(base.draws + [gdraw], base.notes, base.pose)
        draws = fxr_mod.sort_draws(draws, cam)
        img = fxr.render(draws, cam, (W, H), base, None, post, origin, a.ss, bg, floor=floor, plate_light=plate)
        png = out / ("%s_t%05.2f.png" % (name, t))
        cli._save(img, png)
        report["frames"].append({"t": round(t, 4), "driver_t": round(td, 4), "png": str(png), "alive": alive,
                                 "draws": len(draws), "lights": len(lights), "seconds": round(time.time() - tic, 2)})
        images.append(img)
        log("t=%.2f (cast %.2f): %d draws, %d effects (%.1fs)" % (t, td, len(draws), len(alive), time.time() - tic))
    if len(images) > 1:
        from PIL import Image
        gif = out / ("%s_t%05.2f-%05.2f.gif" % (name, times[0], times[-1]))
        fr = [Image.fromarray(im[..., :3]) for im in images]
        fr[0].save(gif, save_all=True, append_images=fr[1:], duration=int(1000 / a.gif_fps), loop=0)
        report["gif"] = str(gif)
    report["notes"] = sorted(set(fxr.notes))
    if R.shadow.spec is not None:
        report["shadow"] = dict(R.shadow.spec.as_dict(), stats=R.shadow.stats)
    report["sim_stats"] = {lab: {"spawned": dict((getattr(sm, "stats", {}) or {}).get("spawned", {}))}
                           for lab, _c, _i, _e, sm in sims}
    report["files"] = fs.provenance()
    (out / "metrics.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report
