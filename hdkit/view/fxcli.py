"""python -m hdkit.view fx <effect.particles> [--unit <unit json>] [--t 1.5 | --frames 24 --dt 0.08] [options]

Draws a PopcornFX effect (hdkit.fx.render) at the game camera, alone on a ground plane or on a unit posed with --clip at
--time (default: the effect time). Writes <out>/<name>_t<t>.png (and .jpg with --jpg), a GIF over --frames, and
metrics.json (renderers, material keywords, stand-ins used, notes). The effect is placed at the unit's origin turned by
the unit's facing (or by the unit JSON's VfxDefinitionComponent transforms, listed in metrics.json).

Simulation: lane vm's hdkit.fx.sim.Sim (the effect's own scripts), one per placed copy.
Every render passes the hdkit GPU gate (--no-gate skips it) and should run under `python -m hdkit.memwatch`.

  --only 0,1   draw only these renderer indices;  --hide-fx 2,7   leave these out
  --gain 1     effect colour scale;  --exposure 0.073125   the game's cb10[0].x (default: the rig's)
  --ground #2a2622 | depth | none   an opaque floor, an invisible depth-only floor (as the game's ground: soft particles
               and depth tests see it, the background shows through), or none;  --ground-size 40
  --facing 0   the unit's facing key (0 = the screen's bottom); --yaw DEG an exact model yaw instead; --hide part:<n>
  --nova N     with --missile: N missiles spread evenly round the launch point (a nova skill)
  --attr NAME=V[,V..]  an effect attribute the game sets at run time (repeatable), e.g. WeaponState=0
  --plate-light R,G,B / off  the ground irradiance of a --bg shot, relit by the effect's lights (default fx.render.PLATE_IRRADIANCE)
  --game-view game_view.json   camera and unit placement read from a capture (hdkit.fx.calib.gamecam): elevation,
               azimuth, px/unit, the unit's yaw and scale (the game's own numbers instead of --scale/--facing/--unit-*)
               With unit.root_offsets (gamecam.root_offsets, measured from the capture's bone palette) each skeleton
               root tree is moved as the game poses it (Mephisto hovers: his upper body rides 6.9 units higher).
  --unit-rate 1 --unit-t0 0 (--unit-period P: rate = clip duration / P)   the unit's clip time = t0 + effect time x rate (the game plays an HD clip over its
               classic mode duration; a side-by-side runner measures the rate from the shots)
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import camera, cli, scene, vfs
from ..fx import gamekinds


def build_parser():
    ap = argparse.ArgumentParser(prog="python -m hdkit.view fx", description=__doc__.split("\n")[0])
    cli.add_common(ap)
    ap.add_argument("--unit", default=None, help="unit JSON or .model the effect is drawn on")
    ap.add_argument("--overlay", default="auto", help="overlay/scene JSON: one copy of the effect per VfxDefinitionComponent "
                                                      "entity naming it, at that entity's transform (e.g. hd/overlays/monster/mephisto.json); "
                                                      "auto (default): the overlay JSON of an effect under particles/overlays/<folder>/<key>/ "
                                                      "(hdkit.view.fxplace), none: the effect at the origin")
    ap.add_argument("--view-eye", default="camera", help="view.position the effect's scripts read: camera (default: the "
                                                           "render camera's eye, camera.OrthoCamera.eye = vm.game_eye(el, az)) or default (the sim's own: vm.game_eye() "
                                                           "at the game's el 30 / az 45)")
    ap.add_argument("--stop", default="auto", help="stop the effect (effect.isRunning false) at this effect time, s: "
                                                     "auto (default) = a cast overlay's overlay.txt pass, Frames x 16 / AnimRate / "
                                                     "25 (needs --tables or HDFX_TABLES), none = never")
    ap.add_argument("--copies", type=int, default=None, help="draw at most this many of the overlay's copies")
    ap.add_argument("--t", type=float, default=1.0, help="effect time, seconds")
    ap.add_argument("--frames", type=int, default=1, help="frames from --t on, --dt apart (GIF when > 1)")
    ap.add_argument("--dt", type=float, default=0.08)
    ap.add_argument("--times", default=None, help="comma list of effect times (overrides --t/--frames/--dt)")
    ap.add_argument("--facing", type=int, default=0)
    ap.add_argument("--monster", default=None, help="monstats.txt id of the --unit (needs --tables or HDFX_TABLES): its "
                    "TransLvl picks the unit's colour variant, entry 2 + TransLvl (view/unitvariant.py; default level0)")
    ap.add_argument("--unit-variant", default=None, help="the --unit's variant entry by position or name (cold, poison, "
                    "level0 .. level4, misc); overrides --monster")
    ap.add_argument("--color-shift", type=int, default=None, help="a colour state's shift (states.txt colorshift, 1..128): "
                    "the unit's model and its effects take transforms.json entry shift - 1 / ColorVariantIndex 37 + shift")
    ap.add_argument("--depth-unit", action="append", default=[], help="an object or unit JSON (or .model) drawn into "
                    "the scene depth only, placed as the effect (its yaw and --offset): its colour is already in the --bg "
                    "shot. The cave shrine's basin: its rim hides the pool's edge and the water reads its depth (lane "
                    "r4-water). Repeatable")
    ap.add_argument("--unit-lift", type=float, default=0.0, help="raise the unit (not its effect) by this many world units")
    ap.add_argument("--unit-scale", type=float, default=1.0, help="scale the unit (a monster's size in game, "
                                                                   "e.g. a super unique's); its effects keep their size")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--missile", default=None, help="missiles.txt name: the effect flies as that missile (hdkit.fx.context "
                                                    "missile driver: the row's Vel/Accel/MaxVel/Range, the HD binding's "
                                                    "transform), launched from the world origin towards --toward")
    ap.add_argument("--tables", default=None, help="folder with missiles.txt (or env HDFX_TABLES), for --missile")
    ap.add_argument("--toward", default="12,0", help="--missile direction in harness sub-tiles dx,dy (world x = 2 dx, "
                                                     "z = 2 dy)")
    ap.add_argument("--skill", default=None, help="--missile: the skill name for skill-filtered bindings")
    ap.add_argument("--speed", type=float, default=None, help="--missile speed override, world units per second")
    ap.add_argument("--sim-dt", type=float, default=0.02, help="simulation step while a driver moves the effect")
    ap.add_argument("--offset", default=None, help="place the effect (and the --missile launch) dx,dy harness sub-tiles "
                                                   "from the origin (world x = 2 dx, z = 2 dy), or dx,dy,height in units")
    ap.add_argument("--effect-yaw", type=float, default=None, help="turn the effect by this world yaw (degrees, +z towards "
                                                                   "+x), instead of the unit's facing")
    ap.add_argument("--only", default=None)
    ap.add_argument("--hide-fx", default=None)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--exposure", type=float, default=None, help="the game's cb10[0].x (default: the rig's exposure)")
    ap.add_argument("--yaw", type=float, default=None, help="exact model yaw in degrees (camera.rot_y), overrides --facing")
    ap.add_argument("--game-view", default=None, help="JSON of hdkit.fx.calib.gamecam.GameView (capture camera + unit)")
    ap.add_argument("--unit-rate", type=float, default=1.0, help="unit clip seconds per effect second")
    ap.add_argument("--unit-t0", type=float, default=0.0, help="unit clip time at effect time 0")
    ap.add_argument("--root-motion", action="store_true", help="keep the clip's root-bone translation (a hovering "
                                                                  "unit's height; gr2.locals_at keep_root_translation)")
    ap.add_argument("--unit-period", type=float, default=None, help="seconds per loop of the unit's clip in game "
                                                                    "(sets --unit-rate = clip duration / period)")
    ap.add_argument("--unit-mode", default=None, help="the unit's mode token (nu, wl, a1, dd ...): its clip from the unit's "
                    "state machine for --unit-wclass (hdkit.fx.sceneunits.mode_clip; --clip/--mode win)")
    ap.add_argument("--unit-wclass", default="hth", help="--unit-mode: the unit's weapon class (stf, 1hs, bow ...)")
    ap.add_argument("--scene-units", default=None, help="JSON of more units in the scene (hdkit.fx.sceneunits): placed, "
                    "posed and drawn with the --unit; they hide EnableAuraStencil draws like it")
    ap.add_argument("--bind", choices=("bone", "origin"), default="bone",
                    help="with --unit: 'bone' (default) plays the effect on each bone the unit binds it to, moved with "
                         "the drawn clip every sim step (hdkit.view.fxbones); 'origin' places it at the unit's origin")
    ap.add_argument("--nova", type=int, default=None, help="with --missile: N copies spread evenly round the launch "
                    "point (a nova skill; Frost Nova's ring)")
    ap.add_argument("--attr", action="append", default=[], help="NAME=V[,V..]: an effect attribute the game sets at run "
                    "time (e.g. WeaponState=0: the file's default 8 hides Fire Bolt's explode); repeatable")
    ap.add_argument("--color-variant", default=None, help="the game's colour variant (attribute ColorVariantIndex, "
                    "hdkit.fx.colorvariant): an index 0..165, a state (cold, poison, red, revive, ...), shift:<states.txt "
                    "colorshift> or rand:<randtransforms entry>; the table is read from hd/global/palette (--root casc)")
    ap.add_argument("--plate-light", default="default", help="base irradiance R,G,B for relighting a --bg shot on "
                    "--depth-unit surfaces and the ground (default: from the rig); off disables plate relighting")
    ap.add_argument("--collision", choices=("game", "authored", "none"), default="game",
                    help="what scene.intersect hits: the floor slab at y 0 as D2R.exe queries it (game, default), the "
                         "same with the script's length and real hit distance (authored), or no world (hdkit.fx.collide)")
    ap.add_argument("--frame-dt", type=float, default=None, help="largest simulation step of an unmoved effect (the game "
                                                                  "updates particles once per rendered frame; default 1/30)")
    ap.add_argument("--ground", default="#2a2622")
    ap.add_argument("--ground-size", type=float, default=40.0)
    ap.add_argument("--cast", default=None, help="skills.txt skill name: draw the whole cast (hdkit.view.fxcast: the caster's "
                                                 "overlays, the skill's missiles with their count and paths); the effect "
                                                 "argument is then only a name")
    ap.add_argument("--cast-t0", choices=("launch", "mode"), default="launch", help="--cast: time 0 = the missile launch "
                                                                                  "or the cast mode's start")
    ap.add_argument("--cast-level", type=int, default=1)
    ap.add_argument("--cast-overlay-at", choices=("lead", "cast", "start"), default="start", help="--cast: the caster's "
                    "overlays start with the cast mode (default, measured: lane r5-casttiming), CAST_OVERLAY_LEAD_S before "
                    "the action frame, or at it")
    ap.add_argument("--cast-seed", type=int, default=0, help="--cast: seed offset of the missiles' paths")
    ap.add_argument("--cast-cof", default=None, help="--cast: the caster's animdata record (e.g. SOSCBOW)")
    ap.add_argument("--animdata", default=None, help="--cast: animdata.d2 (default: <tables>/../animdata.d2)")
    ap.add_argument("--wclass", default="hth", help="--cast: the caster's weapon class (its clips <wclass>_cast ...)")
    ap.add_argument("--cast-body", action="store_true", help="--cast: draw the caster's HD body as well")
    ap.add_argument("--jpg", action="store_true", help="also write a JPG of each frame (and of the GIF's middle frame)")
    ap.add_argument("--gif-fps", type=float, default=12.5)
    ap.add_argument("--sound-markers", action="store_true", help="mark where the effect's sound renderers would play (magenta; viewer only: D2R plays none, fx/gamekinds.py); metrics.json lists them per frame either way")
    ap.add_argument("--timeline", default=None, help="with --unit: the effect is a timeline event of this unit mode (dt = "
                    "death): starts at the event's clip time and follows its bone or model part. The unit plays the clip once "
                    "(--clip, --unit-rate/--unit-period, --unit-t0) and holds its last pose (hdkit.fx.timeline_fx)")
    ap.add_argument("--unit-name", default=None, help="--timeline: the unit's name for its timelines (default: the "
                    "--unit path)")
    ap.add_argument("--attr-before", default=None, help="NAME=V[,V..]@SECONDS: the effect ran that long with this "
                    "attribute value before effect time 0, then took its --attr value (or the file's default): a state "
                    "change of a running effect, e.g. ObjectState=0@3 for a shrine used at t 0 (its light flashes while "
                    "the liquid drains). Static effects only (no --unit bones, --missile, --timeline)")
    ap.add_argument("--unit-fx", choices=("on", "off"), default="on", help="draw sibling VFX entities when previewing "
                    "one of a unit's bone-bound effects")
    ap.add_argument("--object-fx", choices=("on", "off"), default="on", help="with --unit: draw its object effects that "
                    "track its own model (ModelInstanceTracking, e.g. the Wraith's body glow; fx/modeltrack.py)")
    ap.add_argument("--shadows", choices=("rig", "on", "off"), default="rig", help="shadow maps (view/shadow.py): as the rig's "
                    "light.shadow says, on (the game's set-up), or off")
    return ap


def _ints(s):
    return None if not s else [int(x) for x in s.split(",") if x != ""]


def parse_attrs(items, color_variant=None):
    """--attr NAME=V[,V..] values as Sim attrs (a list of up to 4 numbers; ints stay ints); --color-variant as the
    attribute ColorVariantIndex (an --attr of that name wins)."""
    out = {}
    if color_variant not in (None, ""):
        from ..fx import colorvariant
        out[colorvariant.ATTR] = [colorvariant.index_for(color_variant), 0, 0, 0]
    for it in items or []:
        k, _, v = it.partition("=")
        vals = [float(x) if any(c in x for c in ".eE") else int(x) for x in v.split(",") if x.strip()]
        out[k.strip()] = (vals + [0.0] * 4)[:4]
    return out or None


def preroll(sims, spec, attrs) -> float:
    """--attr-before NAME=V@SECONDS (lane r4-glow): each sim runs SECONDS with NAME=V, then NAME takes its --attr value
    or the effect file's default; returns SECONDS (the effect times are counted from the switch)."""
    from .fxplace import advance
    kv, _, sec = spec.rpartition("@")
    before = parse_attrs([kv])
    name = next(iter(before))
    for _nm, _P, sm in sims:
        after = (attrs or {}).get(name)
        sm.attrs_in.update(before)
        advance(sm, float(sec), None)
        if after is not None:
            sm.attrs_in[name] = after
        else:
            sm.attrs_in.pop(name, None)
    return float(sec)


COLLISION = None          # hdkit.fx.collide.World of scene.intersect (run() sets it from --collision; None: floor slab)
MAX_DT = 1 / 30           # largest step of a static sim (run() sets it from --frame-dt)


def make_sim(data, seed, transform, attrs=None, eye=None):
    """Lane vm's Sim on the graph of `data`, placed by `transform`; `eye`: the render camera's eye (view.position,
    camera.OrthoCamera.eye), kept by drive_to as well. Collision world and step size: COLLISION / MAX_DT
    (lanes r3-intersect2, r3-overlays, r3-aura2: one SimContext with all their fields)."""
    from ..fx import graph, sim as fxsim
    g = graph.load(data)
    g.data = data
    cam = None if eye is None else np.asarray(eye, np.float32)
    sm = fxsim.Sim(g, seed=seed, attrs=attrs, ctx=fxsim.SimContext(transform=np.asarray(transform, np.float32), camera=cam,
                                                                   collision=COLLISION), max_dt=MAX_DT)
    sm.view_eye = cam
    return sm


def ground_draw(R, colour="#2a2622", size=40.0, y=0.0):
    """An opaque floor quad (y = 0, `size` world units square) as a gpu.Draw."""
    from .gpu import Draw
    rgb = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    h = size / 2.0
    pos = np.array([[-h, y, -h], [h, y, -h], [h, y, h], [-h, y, h]], float)
    nrm = np.tile([0.0, 1.0, 0.0], (4, 1))
    tan = np.tile([1.0, 0.0, 0.0, 1.0], (4, 1))
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    tris = np.array([[0, 2, 1], [0, 3, 2]])
    vbuf, n = R._vbuf(pos, nrm, tan, uv, tris)
    ub = R._ubuf()
    alb = R.texture([np.full((4, 4, 4), rgb + [255], np.uint8)], srgb=True)
    bind = R._bind(ub, alb, R.default("nrm"), R.default("orm"), R.default("mask"), R.default("strip"))
    return Draw(vbuf, n, ub, bind, [], 0.0, pos[tris], uv[tris] * 4.0, "ground", "ground", "ground", light_class="world")


def effect_transform(yaw_deg, place=None):
    from .camera import rot_y
    T = np.eye(4)
    T[:3, :3] = rot_y(yaw_deg)
    if place is not None:
        T = np.asarray(place, float) @ T
    return T


def depth_occluders(R, fs, paths, cam, yaw, T, lod=0) -> list:
    """--depth-unit: the draws of each object/unit (bind pose) turned by the effect's `yaw` and moved by the
    translation of its transform `T`, for gpu.Prepared.occluders (depth only)."""
    out = []
    for path in paths or ():
        u = scene.load_unit(fs, path, lod)
        place = np.eye(4)
        place[:3, 3] = np.asarray(T, float)[:3, 3]
        out += R.prepare(u, None, 0.0, yaw, cam, place=place).draws
    return out


def _tables(tdir):
    if not tdir:
        return None
    from ..fx import context as C
    try:
        return C.Tables.from_dir(tdir)
    except (OSError, ValueError):
        return None


def missile_driver(a, fs, T):
    """--missile: a hdkit.fx.context MissileDriver from the origin towards --toward (harness sub-tiles: world x = 2 dx,
    z = 2 dy) far enough for the row's range; only the bindings of the effect being drawn. Returns (driver, places)."""
    import os
    from ..fx import context as C
    tdir = a.tables or os.environ.get("HDFX_TABLES")
    tb = C.Tables.from_dir(tdir) if tdir else None
    me = C.effects_of_missile(fs, a.missile, tb)
    want = vfs.normalise(a.asset)
    binds = [b for b in me.vfx(a.skill) if vfs.normalise(b.file) == want] or me.vfx(a.skill)
    dx, dy = (float(v) for v in a.toward.split(","))
    d = np.array([2.0 * dx, 0.0, 2.0 * dy])
    d = d / max(np.linalg.norm(d), 1e-9)
    facts = me.row or {}
    v = a.speed if a.speed is not None else facts.get("speed_units_s") or 0.0
    rng = (facts.get("range") or 50) / C.FPS
    length = max(1.0, v * rng * 1.5 + (facts.get("max_speed_units_s") or 0.0) * rng)
    drv = C.missile(binds or [a.asset], start=[0.0, 0.0, 0.0], end=(d * length).tolist(), row=me if me.row else None,
                    speed=a.speed if a.speed is not None or not me.row else None, skill=a.skill)
    drv.start = 0.0
    places = [(l.label or "missile", np.asarray(T, float) @ drv.layer_transform(0.0, l)) for l in drv.layers]
    drv.place = np.asarray(T, float)
    return drv, places


def drive_to(sm, drv, i, t, h=0.02):
    """Step `sm` (layer `i` of driver `drv`) to effect age `t` in steps of at most `h`, the context (transform,
    velocity) moved before each step. Returns the layer's transform at `t`."""
    lay = drv.layers[min(i, len(drv.layers) - 1)]
    now = float(getattr(sm, "t", 0.0)) if getattr(sm, "started", False) else 0.0
    if now > t + 1e-9:
        sm.reset()
        now = 0.0
    while True:
        step = min(h, t - now)
        ctx = drv.context(now + max(step, 0.0), min(i, len(drv.layers) - 1))
        ctx.transform = np.asarray(drv.place @ ctx.transform, np.float32)
        if getattr(sm, "view_eye", None) is not None and getattr(ctx, "camera", None) is None:
            ctx.camera = sm.view_eye
        if getattr(ctx, "collision", None) is None and COLLISION is not None:
            ctx.collision = COLLISION
        sm.ctx = ctx
        if hasattr(drv, "attrs_at"):                  # the game's missile attributes (frame counter), --attr wins
            sm.attrs_in = {**drv.attrs_at(now + max(step, 0.0), getattr(drv, "index", 0)), **getattr(sm, "user_attrs", {})}
        if step <= 1e-9:
            if not getattr(sm, "started", False):
                sm.step(0.0)
            break
        sm.step(step)
        now += step
    return drv.place @ drv.layer_transform(t, lay)


def _bone_drivers(a, fs, unit, clip, yaw, place, gv, asset=None) -> list:
    """fxbones.bone_drivers for the CLI's unit: the drawn clip (fixed --time, or --unit-t0 + t x --unit-rate), the
    unit's yaw and lift, the capture's root offsets with --game-view."""
    from . import fxbones
    pkw = {"loop": True, "keep_root_translation": a.root_motion}
    t0, rate = (a.time, 0.0) if a.time is not None else (a.unit_t0, a.unit_rate)
    poser = None
    if gv is not None and gv.unit.get("root_offsets"):
        from ..fx.calib import gamecam
        from . import gr2

        def poser(sk, ct):
            return gamecam.offset_pose(gr2.pose_at(sk, unit.clips.get(clip), ct, **pkw), sk, gv.unit["root_offsets"])
    return fxbones.bone_drivers(fs, a.unit, unit, asset or a.asset, unit.clips.get(clip) if clip else None, t0, rate, yaw,
                                place, pkw, poser)


def _sibling_effects(fs, unit, asset):
    """Other VFX entities from the unit, once per file. Bone drivers handle repeated bindings."""
    paths = {vfs.normalise(e["filename"]): e["filename"] for e in unit.effects if e["filename"]}
    paths.pop(vfs.normalise(asset), None)
    return [p for p in paths.values() if fs.exists(p)]


def run(a, log=print) -> dict:
    global COLLISION, MAX_DT
    from ..fx import collide
    MAX_DT = getattr(a, "frame_dt", None) or 1 / 30
    mode = getattr(a, "collision", "game")
    COLLISION = collide.World(mode="game") if mode == "none" else collide.floor_world(0.0, mode=mode)
    if a.cast:
        from . import fxcast
        return fxcast.run(a, log)
    from .. import gpugate
    from ..fx import modeltrack, render as fxr_mod, rspec
    from . import gpu, rig
    if not a.no_gate:
        rd = gpugate.check()
        if not rd.free:
            raise SystemExit("GPU gate: %s (use --no-gate to override)" % rd.why)
    fs = vfs.Layered(vfs.parse_roots(a.root))
    data = fs.read(a.asset)
    eff = rspec.load(data)
    eff.path = a.asset
    R = gpu.Renderer(msaa=a.msaa, aniso=a.aniso)
    from ..fx import lighting as fx_lighting
    rg = fx_lighting.resolve_rig(a.rig, fs)          # scene:<biome>@tod: the general setup (fx/lighting.py)
    if getattr(a, "shadows", "rig") != "rig":
        from dataclasses import replace as _rep
        rg = _rep(rg, light=_rep(rg.light, shadow={"on": True} if a.shadows == "on" else {}))
    post = fxr_mod.Post(rg, a.gain, a.exposure)
    gv = None
    if a.game_view:
        from ..fx.calib import gamecam
        gv = gamecam.GameView.from_dict(json.loads(Path(a.game_view).read_text(encoding="utf-8")))
        a.el, a.az = gv.el, gv.az
        a.scale = gv.k / camera.K_1600x900
    fxr = fxr_mod.FxRenderer(R, fs, post)
    fxr.show_sounds = bool(getattr(a, "sound_markers", False))
    W, H = cli.parse_size(a.size)
    origin = cli._origin(a, W, H)
    cam = camera.OrthoCamera(a.el, a.az, camera.K_1600x900 * a.scale)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    unit, yaw = None, 0.0
    if a.unit:
        track_effect = a.asset if not a.missile and any(modeltrack.uses_tracked_mesh(r) for r in eff.renderers) else None
        unit = scene.load_unit(fs, a.unit, a.lod, track_effect=track_effect, equipment=True)
        yaw = camera.facing_yaw(camera.key_dir64(a.facing), a.yaw0, a.sign)
        if gv is not None and gv.unit:
            yaw = float(gv.unit["yaw"])
        if a.yaw is not None:
            yaw = a.yaw
        if a.monster or a.unit_variant is not None or a.color_shift:
            from . import unitvariant
            tl = None
            if a.monster:
                tdir = a.tables or os.environ.get("HDFX_TABLES")
                if not tdir:
                    raise SystemExit("--monster needs --tables (or HDFX_TABLES): the folder with monstats.txt")
                tl = unitvariant.monster_trans_lvl(tdir, a.monster)
            unit.variant = unitvariant.choose(unit.variant_table, a.unit_variant, tl, a.color_shift, fs)
    unit_notes = []
    if unit is not None and a.unit_mode and not (a.clip or a.mode):
        from ..fx import sceneunits
        a.clip, how = sceneunits.mode_clip(fs, a.unit, unit, a.unit_mode, a.unit_wclass)
        unit_notes.append(how)
    extra = None
    if a.scene_units:
        from ..fx import sceneunits
        extra = sceneunits.load(a.scene_units)
    clip = cli.clip_name(a)
    if unit is not None and a.unit_period and clip in getattr(unit, "clips", {}):
        a.unit_rate = float(unit.clips[clip].duration) / float(a.unit_period)
    uscale = a.unit_scale
    if gv is not None and gv.unit and a.unit_scale == 1.0:
        uscale = float(gv.unit["scale"][1])            # the capture's world matrix (calib r3 Mephisto: 1.0)
    if unit is not None and uscale != 1.0:
        unit.root_scale *= uscale
    from .fxplace import OVERLAY_WORLD_YAW, resolve_overlay_arg
    overlay = None if a.missile else resolve_overlay_arg(fs, a.overlay, a.asset)
    eyaw = a.effect_yaw if a.effect_yaw is not None else (OVERLAY_WORLD_YAW if overlay else yaw)  # overlays keep a world
    T = effect_transform(eyaw)            # yaw (fxplace); the unit's scale and lift do not move its
    if a.offset:                                                          # effects (calib r3: xz scale 1.0)
        o = [float(v) for v in a.offset.split(",")]
        M = np.eye(4)
        M[:3, 3] = [2.0 * o[0], o[2] if len(o) > 2 else 0.0, 2.0 * o[1]]
        T = M @ T
    # the lift raises only the unit (a floating monster); its ground effects stay on the floor
    place = None
    if a.unit_lift:
        place = np.eye(4)
        place[1, 3] = a.unit_lift
    places = [("effect", T)]
    occ = depth_occluders(R, fs, a.depth_unit, cam, eyaw, T, a.lod)
    if overlay:
        doc = json.loads(fs.read(overlay).decode("utf-8"))
        want = vfs.normalise(a.asset)
        ents = [e for e in scene.vfx_entities(doc) if vfs.normalise(e["filename"]) == want]
        places = [(e["name"], T @ e["transform"]) for e in ents] or places
    if a.copies is not None:
        places = places[:max(1, a.copies)]
    drv = None
    if a.missile:
        drv, places = missile_driver(a, fs, T)
        report_drv = {"missile": a.missile, "toward": a.toward, "speed_units_s": drv.v0, "t_end": drv.t_end,
                      "layers": [{"label": l.label, "xf": np.asarray(l.xf).round(4).tolist()} for l in drv.layers]}
    tlx = None
    if a.timeline and unit is not None:                 # a timeline event (lane r3-decal2): start and bone of the game
        from ..fx import timeline_fx
        unit_name = a.unit if a.unit.lower().endswith(".json") else Path(a.unit).stem
        tlx = timeline_fx.TimelineEffect.of(fs, a.unit_name or unit_name, unit, a.asset, a.timeline,
                                            a.unit_rate, a.unit_t0, clip=unit.clips.get(clip))
        drv = tlx.driver()
        drv.place = np.asarray(T, float)
        places = [("timeline", drv.place @ drv.layer_transform(0.0, drv.layers[0]))]
        report_drv = {"timeline": a.timeline, "event_clip_time": tlx.binding.time, "start": tlx.start,
                      "bone": tlx.bone, "follow": tlx.follow, "file": tlx.binding.file}
    attrs = parse_attrs(a.attr, getattr(a, "color_variant", None))
    if unit is not None and not a.missile:            # the effects a unit starts get its variant (lane r3-unitmat);
        from . import unitvariant                     # an explicit --color-variant / --attr ColorVariantIndex wins
        attrs = unitvariant.effect_attrs(getattr(unit, "variant", None), attrs)
    bone_drv = []
    if unit is not None and drv is None and not overlay and not a.offset and getattr(a, "bind", "bone") == "bone":
        bone_drv = _bone_drivers(a, fs, unit, clip, yaw, place, gv)
    if bone_drv:                                             # lane r3-shapes: effects on the unit's moving bones
        places = [(nm, d.place @ d.layer_transform(0.0, d.layers[0])) for nm, d in bone_drv]
        if a.copies is not None:
            places, bone_drv = places[:max(1, a.copies)], bone_drv[:max(1, a.copies)]
        report_drv = {"bones": [{"label": nm, "bone": d.layers[0].bone, "skipped": d.skipped,
                                 "xf": np.asarray(d.layers[0].xf).round(4).tolist()} for nm, d in bone_drv]}
    # view.position / view.direction of the effect: the render camera's eye (= vm.game_eye(el, az); r3-aura2 + r3-overlays)
    eye = cam.eye() if getattr(a, "view_eye", "camera") != "default" else None
    sims = [(nm, P, make_sim(data, a.seed + i, P, attrs, eye)) for i, (nm, P) in enumerate(places)]
    drivers = [(d, 0) for _nm, d in bone_drv] if bone_drv else [(drv, i) for i in range(len(sims))]
    if drv is not None and a.nova and a.nova > 1:
        # a nova: N copies of the missile spread evenly round the launch point, the first towards --toward, each with
        # its MissileIndex (Frost Nova's ring is its missiles' Core segments side by side)
        dx, dy = (float(v) for v in a.toward.split(","))
        ang0, r0 = math.atan2(dy, dx), max(math.hypot(dx, dy), 1.0)
        base_toward, sims, drivers = a.toward, [], []
        for k in range(a.nova):
            th = ang0 + 2.0 * math.pi * k / a.nova
            a.toward = "%.6f,%.6f" % (r0 * math.cos(th), r0 * math.sin(th))
            dk, pk_ = missile_driver(a, fs, T)
            dk.index = k
            for i, (nm, P) in enumerate(pk_):
                sims.append(("%s#%d" % (nm, k), P, make_sim(data, a.seed + k * 7 + i, P, attrs, eye)))
                drivers.append((dk, i))
        a.toward = base_toward
        report_drv["nova"] = a.nova
    for _nm, _P, sm in sims:
        sm.user_attrs = dict(attrs or {})
    pre = preroll(sims, a.attr_before, attrs) if getattr(a, "attr_before", None) else 0.0
    from .fxplace import advance, stop_time
    tdir = a.tables or os.environ.get("HDFX_TABLES")
    stop_at = None if drv is not None else stop_time(a.stop, overlay, _tables(tdir), fs)
    sim = sims[0][2]
    name = a.name or Path(a.asset).stem
    clip = cli.clip_name(a)
    bg = cli.parse_bg(a.bg, (W, H))
    images, report = [], {"command": "fx", "effect": a.asset, "unit": a.unit, "clip": clip,
                          "adapter": dict(R.adapter.info), "camera": {"el": a.el, "az": a.az, "scale": a.scale},
                          "size": [W, H], "ss": a.ss, "rig": rg.as_dict(),
                          "post": {"gain": a.gain, "exposure": post.game_exposure(), "target_scale": post.target_scale()},
                          "unit_place": {"yaw": yaw, "effect_yaw": eyaw, "scale": uscale, "lift": a.unit_lift, "rate": a.unit_rate,
                                         "t0": a.unit_t0, "game_view": a.game_view},
                          "renderers": rspec.summary(eff), "overlay": overlay, "stop_at": stop_at, "frames": [],
                          "unit_variant": getattr(getattr(unit, "variant", None), "as_dict", lambda: None)(),
                          "attrs": {k: list(v) for k, v in (attrs or {}).items()}}
    only, hide = _ints(a.only), _ints(a.hide_fx) or []
    floor = 0.0 if a.ground == "depth" else None
    plate = (getattr(rg, "lighting", None) or fxr_mod.PLATE_IRRADIANCE) if a.plate_light == "default" else (
        None if a.plate_light in ("off", "none", "0") else [float(x) for x in a.plate_light.split(",")])
    gdraw = ground_draw(R, a.ground, a.ground_size) if a.ground and a.ground not in ("none", "depth") else None
    ucache = {}
    tracked = None
    if unit is not None and getattr(a, "object_fx", "on") == "on":
        tracked = modeltrack.TrackedEffects(fs, unit, make_sim, attrs, a.seed, eye, skip=a.asset)
        report["object_fx"] = [{"part": p, "effect": e.path} for p, e, _s in tracked.items]
    siblings = []
    if (bone_drv and bone_drv[0][1].binding.trigger == "entity" and a.copies is None and only is None and not hide
            and getattr(a, "unit_fx", "on") == "on"):
        for path in _sibling_effects(fs, unit, a.asset):
            bindings = _bone_drivers(a, fs, unit, clip, yaw, place, gv, path)
            if not bindings:
                continue
            raw = fs.read(path)
            sibling = rspec.load(raw)
            sibling.path = path
            for label, driver in bindings:
                if driver.binding.trigger != "entity":
                    continue
                siblings.append((sibling, label, driver, make_sim(raw, a.seed + len(siblings) + 1, np.eye(4), attrs, eye)))
        report["unit_fx"] = [{"effect": e.path, "binding": label} for e, label, _driver, _sim in siblings]
    part = tlx.binding.bone if tlx is not None and tlx.binding.extra.get("unit_part") else None
    times = [float(x) for x in a.times.split(",")] if a.times else [a.t + k * a.dt for k in range(max(1, a.frames))]
    for t in times:
        t0 = time.time()
        states = []
        for i, (_nm, P, sm) in enumerate(sims):
            if tlx is not None:
                if t >= tlx.start - 1e-9:                 # before its event the effect does not exist yet
                    P = drive_to(sm, drivers[i][0], drivers[i][1], t - tlx.start, a.sim_dt)
                    states.append((P, sm.state()))
                continue
            if drivers[i][0] is not None:
                P = drive_to(sm, drivers[i][0], drivers[i][1], t, a.sim_dt)
                states.append((P, sm.state()))
                continue
            advance(sm, t + pre, None if stop_at is None else stop_at + pre)   # in order: carry on, no restart
            states.append((P, sm.state()))
        base, posed = gpu.Prepared([]), {}
        if unit is not None:
            ut = a.time if a.time is not None else a.unit_t0 + t * a.unit_rate
            pkw = {"loop": True, "keep_root_translation": a.root_motion}
            if tlx is not None and a.time is None:        # the mode plays once and holds (a death: the corpse)
                ut, pkw["loop"] = tlx.clip_time(t), False
            poser = None
            if gv is not None and gv.unit.get("root_offsets"):
                from ..fx.calib import gamecam
                from . import gr2

                def poser(sk, _t=ut):
                    return gamecam.offset_pose(gr2.pose_at(sk, unit.clips.get(clip), _t, **pkw), sk, gv.unit["root_offsets"])
            base = R.prepare(unit, clip, ut, yaw, cam, pose_kw=pkw, poser=poser, place=place).select(a.hide)
            posed = R.last_posed
        if extra:
            from ..fx import sceneunits
            xd, xn = sceneunits.prepare(R, fs, extra, cam, t, a.lod, ucache)
            base = gpu.Prepared(base.draws + xd, base.notes + xn, base.pose)
            report["scene_units"] = xn
        if gdraw is not None:
            base = gpu.Prepared(base.draws + [gdraw], base.notes, base.pose)
        if occ:
            base = gpu.Prepared(base.draws, base.notes, base.pose, list(base.occluders) + occ)
        draws, lights, sounds = [], [], []
        fxr.tracked = modeltrack.posed_meshes(unit, posed, part, a.asset) if unit is not None and not a.missile else []
        for P, st in states:
            draws += fxr.prepare(eff, st, cam, P, only, hide)
            lights += fxr.lights
            sounds += getattr(fxr, "sounds", None) or []
        fxr.tracked = []
        for sibling, _label, driver, sm in siblings:
            P = drive_to(sm, driver, 0, t, a.sim_dt)
            draws += fxr.prepare(sibling, sm.state(), cam, P)
            lights += fxr.lights
            sounds += getattr(fxr, "sounds", None) or []
        if tracked is not None and tracked.items:     # the unit's object effects on its own model (fx/modeltrack.py)
            draws += tracked.draws(fxr, posed, t, cam, advance)
            lights += fxr.lights
        fxr.lights = lights
        draws = fxr_mod.sort_draws(draws, cam)
        img = fxr.render(draws, cam, (W, H), base, None, post, origin, a.ss, bg, floor=floor, plate_light=plate)
        png = out / ("%s_t%05.2f.png" % (name, t))
        cli._save(img, png)
        e = {"t": round(t, 4), "png": str(png), "draws": [(d.name, d.kind, fxr_mod.KIND_NAMES[d.material.blend])
                                                          for d in draws], "lights": len(fxr.lights),
             "sounds": gamekinds.sounds_summary(sounds),
             "seconds": round(time.time() - t0, 2)}
        if a.jpg:
            from PIL import Image
            jp = png.with_suffix(".jpg")
            Image.fromarray(img[..., :3]).save(jp, quality=90)
            e["jpg"] = str(jp)
        report["frames"].append(e)
        images.append(img)
        log("t=%.2f: %d draws, %s (%.1fs)" % (t, len(draws), png, e["seconds"]))
    if len(images) > 1:
        from PIL import Image
        gif = out / ("%s_t%05.2f-%05.2f.gif" % (name, times[0], times[-1]))
        frames = [Image.fromarray(im[..., :3]) for im in images]
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=int(1000 / a.gif_fps), loop=0)
        report["gif"] = str(gif)
    if draws:
        report["materials"] = {str(d.renderer): d.material.keywords for d in draws}
    report["notes"] = sorted(set(fxr.notes))
    if R.shadow.spec is not None:
        report["shadow"] = dict(R.shadow.spec.as_dict(), stats=R.shadow.stats)
    report["copies"] = [nm for nm, _P, _s in sims]
    if drv is not None or bone_drv:
        report["driver"] = report_drv
    report["sim_stats"] = {k: (dict(v) if hasattr(v, "items") else v) for k, v in (getattr(sim, "stats", {}) or {}).items()}
    report["files"] = fs.provenance()
    if unit is not None:
        report["unit_notes"] = unit.notes + unit_notes
        report["unit_effects"] = [{k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in e.items()}
                                  for e in getattr(unit, "effects", [])]
    (out / "metrics.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv=None) -> int:
    a = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    run(a)
    return 0
