"""Own death and dead clips for a 3D monster whose rigid part (cap, hat, shell) falls off, in the unit's own animation file.

  python -m hdkit.monster3d.s5_death --monster m.json <overlay dir> [--preview <png>] [--classic <classic frames dir>]
        [--shell [--shell-lod 4] [--shell-bone NAME] [--shell-drop 12] [--shell-eps 0.05] [--shell-show 0.74]]
        [--cap-turn 96] [--cap-gap 0.4] [--albedo <png>] [--hide-body] [--shell-up 0..1] [--shell-cap] [--pose <json>]
        [--timing <json>] [--dead-fade S] [--shell-unweld]

Needs s5_build.py's overlay (with the rigid part bound to the monster's `part_bone`) and its skinned_lod0.npz beside it,
and the donor's combined.animations and state machine in D2R's extracted files (common.D2R:
hd/character/enemy/<donor>/animation/).

What it writes into the overlay (folder = the monster's `folder`):
- <folder>/animation/combined.animations: the donor's clips, with
  * the part bone held at its rest place under the head in every clip (a donor may animate that bone; the part must not
    wobble),
  * `death` replaced by an own clip (DEATH below: body poses on world axes, the part thrown off and landing on its rim)
    and `dead` by its last pose. The curves are float knots / float controls (Granny DaK32fC32f, degree 1, 60 keys a
    second) and constants (D4Constant32f / D3Constant32f), types the donor's file already has, so its type table is
    reused as it is (ClipWriter). D2R plays such a clip without any exporter.
- <folder>/<name>_state_machine.json: the donor's machine, every clip path moved to the folder above,
- the unit JSON's root points at that machine.
--shell (needs s5_build.py --strip and --corpse-albedo): a corpse of another colour than the live body. D2R ends a unit's
object effects (the glow layer) when it becomes a corpse, and its material has no state, so a pale corpse is a second,
rigid copy of the body: the body of LOD --shell-lod (part left out) in the LAST death pose, pushed out along its normals
by --shell-eps, triangles that face down dropped, its UVs in the atlas strip where s5_build put the miniature of the
corpse albedo. It is added to every LOD's model and bound with weight 1 to the shell bone (--shell-bone, else the
monster's `shell_bone`): a donor bone no vertex uses and that every clip has a track for. Its bind position is where it
shows, so the bone's world matrix is its bind matrix at the end of `death` (from --shell-show seconds) and in `dead`;
everywhere else the bone is moved --shell-drop units straight down (in its parent's bind frame), under the floor. Pick a
bone whose parent stays put when `cog` moves (a toe end bone can follow the hidden body).
--hide-body (with --shell): from --shell-show on and in `dead` the whole skeleton but the part is moved --shell-drop units
under the floor (a translation of `cog`; the engine ignores the root bone's translation), so the shell alone draws the
corpse and no dark body shows through gaps.
--shell-up W: the shell's normals bent toward world up by W (0..1), for an evenly lit pale corpse.
--shell-cap (with --shell and --hide-body): the landed part is part of the shell (rigid, its last death place) and the
live part goes under the floor with the body, so the corpse part shows the corpse albedo's colours (s5_corpsefit.py bake
--cap-from): the live part's glow ends with the unit. The part's 'scale' in a pose is only honoured by the shell (the
part bone's track carries no scale).
--shell-lod L: LOD k carries the shell made from LOD max(k, L) (the shell's triangles count against each LOD's total).
--shell-unweld: the shell's triangles do not share corners (3 vertices each), so a LOD passes 32,767 vertices with the
same triangles; for the in-game check of unsigned 16-bit indices.
--cap-turn / --cap-gap: the landed part's turn about the side axis (degrees) and its distance from the feet (bind units).
--pose <json>: the fall and the landed part of a corpse pose fitted to classic's DD frames (s5_corpsefit.py; set_pose
below); --cap-turn and --cap-gap are then not used.
--timing <json>: the death retimed to classic's DT frames (set_timing).
--dead-fade S: the death -> dead crossfade of the state machine (stock 0.2 s); see main.
--preview: a software render of the death (front and side, unlit albedo) above the classic DT frames of the same directions.
Textured with --albedo, else with s5_build.py's albedo_final.png beside the overlay (its UV layout, strip included); the
corpse shell shows from --shell-show on.

The monster config's "death" block (all optional):
  length, detach, land     the clip's length and when the part leaves the head and lands (seconds); without `length`
                           and with dt_frames / dt_fps the length is classic's DT (frames / fps); the default key table
                           is scaled to the length
  dt_frames, dt_fps        classic's DT frame count and rate (else read from the classic frames.json)
  dd_dy                    rows the DD frame's box sits below the DT box (else from frames.json's boxes)
  keys                     a key table replacing DEATH ([[t, {bone: [rx, ry, rz]}], ...])
  pivot_z                  the heel line (bind z) the body falls back about (default -0.6)
  head_bone, cog_bone      default "head" and "cog" (the template skeleton's names)
The colour test of the part in classic's frames is the clip fit's part_mask (`clipfit.part_mask`: b_min, b_over_rg).

Pose maths (column vectors, 4x4): B_i bind world of bone i, rest local = B_parent^-1 B_i. A pose gives some bones a
rotation D_i about world axes through the bone's bind joint; local_i = B_parent^-1 D_i B_i, so a child turns with its
parent's motion and then by its own D. Every sample the whole body is moved up or down so that its lowest vertex touches
the ground (y = 0). The part follows the head until DETACH, then flies: its centre on a falling path to a resting place in
front of the feet, turning about the side axis, so it lands on its rim with the top facing forward.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from hdkit.formats import d2r_mesh
from hdkit.formats import d2r_model as dm
from hdkit.monster3d import common

FPS = 60
# the default death: a slump, a hold and a fall onto the back over 0.76 s (19 classic frames at 25 fps); its key times
# scale with the monster's death length
BASE_LENGTH = 0.76
LENGTH = 0.76
DETACH, LAND = 0.30, 0.56
# per key: time, then {bone: (rx, ry, rz) degrees about world x (side: + leans forward), y (up), z (forward)}; "cog" also
# takes the fall about the heel line through PIVOT_Z. The bone names are the biped_d2r template's (a T-pose donor whose
# arms are turned down by ARMS0).
ARMS0 = {"L_UpperArm": (0, 0, -70), "R_UpperArm": (0, 0, 70), "L_ForeArm": (20, 0, 0), "R_ForeArm": (20, 0, 0)}
DEATH = [
    (0.00, {**ARMS0}),
    (0.14, {**ARMS0, "spine_01": (-8, 0, 0), "chest": (-6, 0, 0), "head": (-12, 0, 0),
            "L_Thigh": (-12, 0, 0), "R_Thigh": (-12, 0, 0), "L_Calf": (22, 0, 0), "R_Calf": (22, 0, 0)}),
    (0.30, {**ARMS0, "spine_01": (14, 0, 0), "chest": (10, 0, 0), "neck": (6, 0, 0), "head": (22, 0, 0),
            "L_UpperArm": (0, 0, -55), "R_UpperArm": (0, 0, 55),
            "L_Thigh": (-30, 0, 0), "R_Thigh": (-30, 0, 0), "L_Calf": (52, 0, 0), "R_Calf": (52, 0, 0)}),
    (0.40, {**ARMS0, "cog": (-14, 0, 0), "spine_01": (4, 0, 0), "head": (-6, 0, 0),
            "L_UpperArm": (0, 0, -45), "R_UpperArm": (0, 0, 45),
            "L_Thigh": (-26, 0, 0), "R_Thigh": (-26, 0, 0), "L_Calf": (44, 0, 0), "R_Calf": (44, 0, 0)}),
    (0.62, {"cog": (-88, 0, 0), "head": (-14, 8, 0), "L_UpperArm": (0, 0, -22), "R_UpperArm": (0, 0, 28),
            "L_ForeArm": (10, 0, 0), "R_ForeArm": (10, 0, 0),
            "L_Thigh": (-16, 0, 6), "R_Thigh": (-12, 0, -4), "L_Calf": (20, 0, 0), "R_Calf": (16, 0, 0)}),
    (0.68, {"cog": (-82, 0, 0), "head": (-12, 8, 0), "L_UpperArm": (0, 0, -24), "R_UpperArm": (0, 0, 30),
            "L_ForeArm": (10, 0, 0), "R_ForeArm": (10, 0, 0),
            "L_Thigh": (-16, 0, 6), "R_Thigh": (-12, 0, -4), "L_Calf": (20, 0, 0), "R_Calf": (16, 0, 0)}),
    (0.76, {"cog": (-88, 0, 0), "head": (-14, 8, 0), "L_UpperArm": (0, 0, -22), "R_UpperArm": (0, 0, 28),
            "L_ForeArm": (10, 0, 0), "R_ForeArm": (10, 0, 0),
            "L_Thigh": (-16, 0, 6), "R_Thigh": (-12, 0, -4), "L_Calf": (20, 0, 0), "R_Calf": (16, 0, 0)}),
]
FALL_KEY = 4          # the segment into this key is a fall (eased in, like gravity); the others ease in and out
PIVOT_Z = -0.6        # heel line (bind z) the body falls back about
CAP_TURN = 96.0       # degrees about the side axis at landing (top faces forward), then a small rock
CAP_GAP = 0.4         # space between the feet and the landed part (bind units)
HEAD, COG = "head", "cog"


def part_rule(required=True):
    """The colour test of the rigid part in classic's frames: the monster config's clipfit.part_mask
    ({"b_min", "b_over_rg", optional "loose": {...}, "edge_min"}). Stops with a message when it is needed and missing."""
    rule = common.mcfg("clipfit").get("part_mask")
    if not rule and required:
        raise SystemExit("the monster config has no clipfit.part_mask (the colour test of the rigid part in classic's "
                         "frames, e.g. {\"b_min\": 120, \"b_over_rg\": 50} for a saturated blue part)")
    return rule


def part_test(rgba, rule):
    """Opaque pixels that pass the part colour test: B >= b_min and B - max(R, G) > b_over_rg."""
    a = np.asarray(rgba).astype(float)
    R, G, B, A = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    return (A > 0) & (B >= rule["b_min"]) & (B - np.maximum(R, G) > rule["b_over_rg"])


def classic_meta(frames_dir):
    """frames.json of s1_frames.py classic (or {} when the folder has none)."""
    fd = Path(frames_dir)
    for p in (fd / "frames.json", fd.parent / "frames.json"):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}


def dt_timing(frames_dir=None):
    """(frames, fps, dd_dy) of classic's death: the config's death block, else frames.json (fps = 25 x speed / 256;
    dd_dy = the DD box's top minus the DT box's top: classic draws the corpse lower than the dying unit)."""
    dc = common.mcfg("death")
    meta = classic_meta(frames_dir) if frames_dir else {}
    frames, fps, dd_dy = dc.get("dt_frames"), dc.get("dt_fps"), dc.get("dd_dy")
    if "dt" in meta:
        frames = frames or meta["dt"]["frames"]
        fps = fps or 25.0 * meta["dt"]["speed"] / 256.0
        if dd_dy is None and "dd" in meta:
            dd_dy = meta["dd"]["box"][1] - meta["dt"]["box"][1]
    if not frames or not fps:
        raise SystemExit("no classic DT timing: set death.dt_frames and death.dt_fps in the monster config, or give the "
                         "classic frames folder of s1_frames.py (with frames.json)")
    return int(frames), float(fps), float(dd_dy or 0.0)


def configure():
    """Apply the monster config's death block (length, detach, land, keys, pivot_z, bone names) to the module settings."""
    global DEATH, LENGTH, DETACH, LAND, PIVOT_Z, HEAD, COG
    dc = common.mcfg("death")
    HEAD, COG = dc.get("head_bone", HEAD), dc.get("cog_bone", COG)
    PIVOT_Z = float(dc.get("pivot_z", PIVOT_Z))
    if dc.get("keys"):
        DEATH = [(float(tk), {k: tuple(v) for k, v in pose.items()}) for tk, pose in dc["keys"]]
        LENGTH = float(dc.get("length", DEATH[-1][0]))
    else:
        length = dc.get("length")
        if length is None and dc.get("dt_frames") and dc.get("dt_fps"):
            length = float(dc["dt_frames"]) / float(dc["dt_fps"])
        if length is not None:
            s = float(length) / BASE_LENGTH
            DEATH = [(round(tk * s, 6), pose) for tk, pose in DEATH]
            LENGTH = float(length)
            DETACH, LAND = DETACH * s, LAND * s
    DETACH = float(dc.get("detach", DETACH))
    LAND = float(dc.get("land", LAND))


class ClipWriter:
    """Writes tracks into a parsed .animations tree with the curve types the file already has: float knots / float
    controls (DaK32fC32f, degree 1) for moving tracks and D4Constant32f / D3Constant32f for held ones. The type table of
    the file is reused as it is, so no exporter is needed."""

    def __init__(self, tree):
        kinds = {}
        for an in tree["Animations"]:
            for tg in an["TrackGroups"]:
                for tr in tg["TransformTracks"]:
                    for k in ("OrientationCurve", "PositionCurve"):
                        v = tr[k]["CurveData"]
                        kinds.setdefault(list(v.obj.f)[0], v)
        try:
            self.tmpl_k, self.tmpl_q, self.tmpl_p = (kinds["CurveDataHeader_DaK32fC32f"],
                                                     kinds["CurveDataHeader_D4Constant32f"],
                                                     kinds["CurveDataHeader_D3Constant32f"])
        except KeyError as e:
            raise SystemExit("the animation file has no %s curve to copy the type from" % e)
        self.real = self.tmpl_k.obj["Knots"][0].td

    @staticmethod
    def header(tmpl, degree):
        hk = list(tmpl.obj.f)[0]
        h = tmpl.obj[hk]
        return hk, dm.Obj(h.td, {"Format": h["Format"], "Degree": degree})

    def curve_k(self, knots, controls):
        hk, h = self.header(self.tmpl_k, 1)
        return dm.Variant(self.tmpl_k.td, dm.Obj(self.tmpl_k.obj.td, {
            hk: h, "Padding": 0, "Knots": [dm.Obj(self.real, {"Real32": float(x)}) for x in knots],
            "Controls": [dm.Obj(self.real, {"Real32": float(x)}) for x in controls]}))

    def curve_c(self, tmpl, values):
        hk, h = self.header(tmpl, 0)
        return dm.Variant(tmpl.td, dm.Obj(tmpl.obj.td, {hk: h, "Padding": 0, "Controls": [float(x) for x in values]}))

    def set_track(self, tr, qs, ps, ts):
        """Orientation quaternions qs (n, 4) and positions ps (n, 3) at times ts into the track `tr`; a track that does
        not move becomes a constant."""
        qs = np.array(qs, float)
        for i in range(1, len(qs)):          # one hemisphere, so the interpolation takes the short way
            if qs[i] @ qs[i - 1] < 0:
                qs[i] = -qs[i]
        ps = np.array(ps, float)
        tr["OrientationCurve"]["CurveData"] = (self.curve_c(self.tmpl_q, qs[0]) if np.abs(qs - qs[0]).max() < 1e-6
                                               else self.curve_k(ts, qs.ravel()))
        tr["PositionCurve"]["CurveData"] = (self.curve_c(self.tmpl_p, ps[0]) if np.abs(ps - ps[0]).max() < 1e-6
                                            else self.curve_k(ts, ps.ravel()))


def shell_show_default():
    """When the corpse shell shows by default: 0.74 of the default 0.76 s death, scaled to the death's length."""
    return "%.4f" % (LENGTH * 0.74 / BASE_LENGTH)


def sample_times(length, fps=FPS):
    """Sample times 0, 1/fps, ... up to and including `length`."""
    times = np.round(np.arange(0, length + 1e-6, 1.0 / fps), 6)
    if times[-1] < length - 1e-6:
        times = np.append(times, length)
    return times


def qmat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mat_q(m):
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = np.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k]) * 2
        q = [0.0] * 4
        q[i] = 0.25 * s
        q[j] = (m[j, i] + m[i, j]) / s
        q[k] = (m[k, i] + m[i, k]) / s
        q[3] = (m[k, j] - m[j, k]) / s
    q = np.array(q)
    return q / np.linalg.norm(q)


def euler(rx, ry, rz):
    a, b, c = np.radians([rx, ry, rz])
    X = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Y = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Z = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return Z @ Y @ X


def about(R, p):
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = p - R @ p
    return M


def trans(v):
    M = np.eye(4)
    M[:3, 3] = v
    return M


class Skeleton:
    def __init__(self, model_path):
        _g, tree = d2r_mesh.load_model(model_path)
        bones = tree["Skeletons"][0]["Bones"]
        self.names = [b["Name"] for b in bones]
        self.parent = [b["ParentIndex"] for b in bones]
        self.B = [np.linalg.inv(np.array(b["InverseWorldTransform"]).reshape(4, 4).T) for b in bones]
        self.Binv = [np.linalg.inv(m) for m in self.B]
        self.idx = {n: i for i, n in enumerate(self.names)}

    def pose(self, D, lift=np.zeros(3)):
        """(locals, worlds) for {bone index: 4x4 world-axis motion about its joint}; `lift` moves the whole body."""
        loc, W = [], []
        for i, p in enumerate(self.parent):
            M = D.get(i, np.eye(4))
            if p < 0:
                L = trans(lift) @ M @ self.B[i]
            else:
                L = self.Binv[p] @ M @ self.B[i]
            loc.append(L)
            W.append(W[p] @ L if p >= 0 else L)
        return loc, W


def ease(s, fall):
    return s * s if fall else s * s * (3 - 2 * s)


POSE = None           # --pose: a fitted corpse pose (s5_corpsefit.py), see set_pose


def set_pose(pose):
    """Replace the fall of DEATH (keys from 0.40 on) by a fitted corpse pose.

    pose = {"cog": [rx, ry, rz], "pivot": [x, y, z], "shift": [dx, dy, dz], "bones": {name: [rx, ry, rz]},
            "cap": {"rot": [rx, ry, rz], "centre": [x, z], "sink": s}}
    cog turns about `pivot` and then moves by `shift`; the other bones turn about their bind joints (world axes, as DEATH).
    The keys keep the bow of 0.14 and 0.30; 0.40 is a quarter of the fall, 0.62 the full pose, 0.68 a small bounce back,
    0.76 the pose again. The cap rests with world rotation `rot` (from its bind orientation) at `centre`, its lowest point
    `sink` under the ground (see cap_rest)."""
    global POSE, DEATH, FALL_KEY
    POSE = pose
    fin = {k: tuple(v) for k, v in pose["bones"].items()}
    fin[COG] = tuple(pose["cog"])
    fin["_shift"] = tuple(pose.get("shift", (0, 0, 0)))

    def part(f, fb=None):
        return {k: tuple(f * np.array(v, float) if k in (COG, "_shift") else (fb if fb is not None else f) * np.array(v, float))
                for k, v in fin.items()}
    DEATH = DEATH[:3] + [(0.40, {**ARMS0, **part(0.25, 0.5)}), (0.62, part(1.0)), (0.68, part(0.94, 1.0)), (0.76, part(1.0))]
    FALL_KEY = 4


def set_timing(tm):
    """--timing <json>: retime the death clip to classic's DT frames.

    tm = {"keys": [[t, ref], ...], "length": L, "detach": D, "land": A, "fall_key": k}
    ref is an index into DEATH as it stands (after --pose), so a pose can be held by naming it at two times, or
    {"base": index, "bones": {bone: [rx, ry, rz]}} for a new pose: the base pose with these bones replaced. Missing
    fields keep their values."""
    global DEATH, LENGTH, DETACH, LAND, FALL_KEY
    old = list(DEATH)
    keys = []
    for t, ref in tm.get("keys", []):
        if isinstance(ref, int):
            pose = dict(old[ref][1])
        else:
            pose = dict(old[int(ref.get("base", 0))][1])
            pose.update({k: tuple(v) for k, v in ref.get("bones", {}).items()})
        keys.append((float(t), pose))
    if keys:
        DEATH = keys
    LENGTH = float(tm.get("length", LENGTH))
    DETACH = float(tm.get("detach", DETACH))
    LAND = float(tm.get("land", LAND))
    FALL_KEY = int(tm.get("fall_key", FALL_KEY))
    if DEATH[-1][0] < LENGTH - 1e-6:
        DEATH.append((LENGTH, dict(DEATH[-1][1])))


def classic_curve(frames_dir, mode="dt", dd_dy=None, frames=None, fps=None, rule=None):
    """classic's fall progress per DT frame: the part's centroid row (part colour test) and the sprite's top row, per
    direction, 0 at frame 0 and 1 at the DD frame (its box sits dd_dy rows lower), the middle value of the 8 facings.
    frames, fps and dd_dy default to dt_timing(frames_dir); rule to part_rule()."""
    if frames is None or fps is None or dd_dy is None:
        f_, r_, d_ = dt_timing(frames_dir)
        frames, fps = frames or f_, fps or r_
        dd_dy = d_ if dd_dy is None else dd_dy
    rule = rule or part_rule()
    cap, top = [], []
    fd = Path(frames_dir)
    dt_dir = fd / mode if (fd / mode).is_dir() else fd     # --classic names the DT folder (as --preview reads it)
    dd_dir = fd / "dd" if (fd / "dd").is_dir() else fd.parent / "dd"
    for d in range(8):
        rc, rt = [], []
        fs = [(dt_dir / ("d%d_f%02d.png" % (d, i)), 0) for i in range(frames)] + [(dd_dir / ("d%d_f00.png" % d), dd_dy)]
        for f, oy in fs:
            im = np.asarray(Image.open(f).convert("RGBA")).astype(int)
            al = im[..., 3]
            m = part_test(im, rule)
            rc.append(np.nonzero(m)[0].mean() + oy if m.sum() > 10 else np.nan)
            rt.append(np.nonzero((al > 0).any(1))[0].min() + oy)
        rc, rt = np.array(rc), np.array(rt, float)
        cap.append((rc - rc[0]) / (rc[-1] - rc[0]))
        top.append((rt - rt[0]) / (rt[-1] - rt[0]))
    t = np.arange(frames + 1) / fps
    return t, np.nanpercentile(cap, 50, 0), np.nanpercentile(top, 50, 0)


def fall_progress(times, caps, body_pts, cb, el=35.0):
    """3D fall progress as classic's camera sees it: screen-down position of the cap centre and of the body's top at 8
    facings (orthographic, elevation el), 0 at the first sample and 1 at the last, the middle value over facings."""
    e = np.radians(el)
    cap_c = np.array([(C @ np.append(cb, 1))[:3] for C in caps])
    cap_out, top_out = [], []
    for az in np.radians(np.arange(0, 360, 45)):
        f = np.array([np.sin(az), 0.0, np.cos(az)])
        sc = -(cap_c[:, 1] * np.cos(e) + cap_c @ f * np.sin(e))
        st = np.array([-(P[:, 1] * np.cos(e) + P @ f * np.sin(e)).min() for P in body_pts])
        for s, o in ((sc, cap_out), (st, top_out)):
            if abs(s[-1] - s[0]) > 1e-3:
                o.append((s - s[0]) / (s[-1] - s[0]))
    return np.percentile(cap_out, 50, 0), np.percentile(top_out, 50, 0)


def cap_rest(capb, cb, pose):
    """World 4x4 of the landed cap of a fitted pose: bind cap turned by rot and scaled by `scale` (default 1) about its
    centre, centred at (x, ?, z), lowest point at -sink. Returns (4x4, centre, the rotation without the scale)."""
    c = pose["cap"]
    R = euler(*c["rot"])
    S = float(c.get("scale", 1.0))
    P = S * (R @ (capb - cb).T).T
    centre = np.array([c["centre"][0], -P[:, 1].min() - float(c.get("sink", 0.0)), c["centre"][1]])
    M = np.eye(4)
    M[:3, :3] = S * R
    return trans(centre) @ M @ trans(-cb), centre, R


def slerp_m(Ra, Rb, s):
    qa, qb = mat_q(Ra), mat_q(Rb)
    if qa @ qb < 0:
        qb = -qb
    d = float(np.clip(qa @ qb, -1, 1))
    if d > 0.9995:
        q = qa + s * (qb - qa)
    else:
        th = np.arccos(d)
        q = (np.sin((1 - s) * th) * qa + np.sin(s * th) * qb) / np.sin(th)
    return qmat(q / np.linalg.norm(q))


def body_motion(sk, t):
    """{bone index: 4x4} of the DEATH keys at time t."""
    for k in range(len(DEATH) - 1):
        if DEATH[k][0] <= t <= DEATH[k + 1][0]:
            break
    (t0, a), (t1, b) = DEATH[k], DEATH[k + 1]
    s = ease((t - t0) / (t1 - t0) if t1 > t0 else 1.0, k + 1 == FALL_KEY)
    D = {}
    lerp = lambda name: (1 - s) * np.array(a.get(name, (0, 0, 0)), float) + s * np.array(b.get(name, (0, 0, 0)), float)  # noqa: E731
    for name in set(a) | set(b):
        if name.startswith("_"):
            continue
        r = lerp(name)
        i = sk.idx[name]
        if name == COG:
            piv = np.array(POSE["pivot"], float) if POSE else np.array([0.0, 0.0, PIVOT_Z])
            D[i] = trans(lerp("_shift")) @ about(euler(*r), piv)
        else:
            D[i] = about(euler(*r), sk.B[i][:3, 3])
    return D


def add_shell(a, opt, out, folder, name, donor, sk, body_W, body_loc, times, pbone, W_last=None, C_last=None):
    """The corpse shell (see --shell): rewrites torso_lod0..4.model with it and sets the shell bone in body_loc (death/dead)."""
    from hdkit.monster3d import s5_build
    from hdkit.sprite3d import render_soft
    build = json.loads((out.parent / "build.json").read_text(encoding="utf-8"))
    strip = build.get("strip")
    if not strip:
        raise SystemExit("--shell needs s5_build.py --strip and --corpse-albedo")
    lod, bone = int(opt("--shell-lod", "4")), opt("--shell-bone", common.MONSTER.get("shell_bone") or "")
    if not bone:
        raise SystemExit("--shell needs a shell bone: --shell-bone NAME or shell_bone in the monster config")
    drop, eps, show = float(opt("--shell-drop", "12")), float(opt("--shell-eps", "0.05")), float(opt("--shell-show", shell_show_default()))
    S = sk.idx[bone]
    up_w = float(opt("--shell-up", "0"))
    par = sk.parent[S]
    Wl = body_W[-1] if W_last is None else W_last

    def shell_of(src_lod):
        """The shell made from LOD src_lod's body: positions in the last death pose, normals, kept triangles, strip UVs."""
        src = np.load(out.parent / ("skinned_lod%d.npz" % src_lod))
        if bone in list(src["bones"]):
            raise SystemExit("shell bone %s is used by the mesh" % bone)
        names = list(src["bones"])
        vb = np.array([[sk.idx[names[j]] for j in row] for row in src["bone_idx"]])
        body = vb[:, 0] != pbone
        # --shell-cap: the landed cap goes into the shell too (rigid, at C_last), so the corpse cap takes its colours from
        # the corpse albedo (the live part's glow ends with the unit)
        with_cap = "--shell-cap" in a and C_last is not None
        keep_t = body[src["tris"]].all(1) | (with_cap & (~body)[src["tris"]].all(1))
        used = np.unique(src["tris"][keep_t])
        remap = -np.ones(len(src["pos"]), int)
        remap[used] = np.arange(len(used))
        T = remap[src["tris"][keep_t]]
        V = np.hstack([src["pos"][used], np.ones((len(used), 1))])
        P = np.zeros((len(used), 3))
        for k in range(4):
            Mk = np.stack([Wl[j] @ sk.Binv[j] for j in range(len(Wl))])[vb[used, k]]
            P += src["weights"][used, k, None] * np.einsum("nij,nj->ni", Mk, V)[:, :3]
        if with_cap:
            cu = ~body[used]
            P[cu] = (C_last @ V[cu].T).T[:, :3]
        N = render_soft.vertex_normals(P, T)
        fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        # a face of the body turned to the floor is never seen on a corpse (the standing cap's faces all can be)
        T = T[(fn[:, 1] > -0.2) | (~body[used])[T].all(1)]
        P = P + eps * N
        # --shell-up: the shell's normals bent toward world up, so the night light falls on it evenly (with its own normals
        # the pale corpse reads black and white: every face turned from the light goes black)
        N = (1 - up_w) * N + up_w * np.array([0.0, 1.0, 0.0])
        N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
        U = src["uv"][used].copy()
        U[:, 0] = (1 - strip) + strip * U[:, 0] / (1 - strip)
        U[:, 1] = strip * U[:, 1]
        if "--shell-unweld" in a:
            # every shell triangle gets its own 3 corners (same look; 3x the shell's vertices): a model above 32,767
            # vertices without more triangles, for the in-game check of unsigned 16-bit indices
            P, N, U = P[T].reshape(-1, 3), N[T].reshape(-1, 3), U[T].reshape(-1, 2)
            T = np.arange(len(P)).reshape(-1, 3)
        return P, N, T, U

    # LOD k carries the shell made from LOD max(k, --shell-lod): a fine shell on the near LODs, no bigger than the LOD on far ones
    shells = {k: shell_of(k) for k in range(lod, 5)}
    stem = "data/" + folder + "/" + name
    tris_total, lod_verts = [], []
    for k in range(5):
        P, N, T, U = shells[max(k, lod)]
        m = np.load(out.parent / ("skinned_lod%d.npz" % k))
        mn = list(m["bones"]) + [bone]
        n0 = len(m["pos"])
        I = np.vstack([m["bone_idx"], np.tile([len(mn) - 1, 0, 0, 0], (len(P), 1))])
        Wt = np.vstack([m["weights"], np.tile([1.0, 0, 0, 0], (len(P), 1))])
        TT = np.vstack([m["tris"], T + n0])
        s5_build.write_torso(k, "hd/character/enemy/" + donor, np.vstack([m["pos"], P]), np.vstack([m["nrm"], N]),
                             np.vstack([m["uv"], U]), TT, I, Wt, mn, out, folder, stem,
                             common.MONSTER.get("keep", ()))
        tris_total.append(int(len(TT)))
        lod_verts.append(int(n0 + len(P)))
    P, N, T, U = shells[lod]
    np.savez(out.parent / "shell.npz", pos=P, tris=T, uv=U)
    hidden = trans(np.array([0.0, -drop, 0.0])) @ sk.B[S]
    for t, loc, W in zip(times, body_loc, body_W):
        loc[S] = np.linalg.inv(W[par]) @ (sk.B[S] if t >= show - 1e-6 else hidden)
    return {"bone": bone, "lod": lod, "triangles": int(len(T)), "vertices": int(len(P)), "lod_triangles": tris_total,
            "lod_vertices": lod_verts, "unweld": "--shell-unweld" in a,
            "shell_up": up_w, "shell_triangles": {k: int(len(shells[k][2])) for k in shells},
            "show": show, "drop": drop, "eps": eps, "hidden_local": sk.Binv[par] @ hidden, "cap": "--shell-cap" in a}


def main():
    a = [x for x in sys.argv[1:]]
    opt = lambda n, d: a[a.index(n) + 1] if n in a else d  # noqa: E731
    out = Path(a[0])
    configure()
    global CAP_TURN, CAP_GAP
    CAP_TURN, CAP_GAP = float(opt("--cap-turn", CAP_TURN)), float(opt("--cap-gap", CAP_GAP))
    if "--pose" in a:
        set_pose(json.loads(Path(opt("--pose", "")).read_text(encoding="utf-8")))
    if "--timing" in a:
        set_timing(json.loads(Path(opt("--timing", "")).read_text(encoding="utf-8")))
    cfg = common.need_monster()
    name, donor, cap_bone = common.NAME, cfg["donor"], cfg.get("part_bone")
    if not cap_bone:
        raise SystemExit("s5_death needs the rigid part on its own bone: part_bone in the monster config")
    D2R = common.D2R
    folder = common.FOLDER
    sk = Skeleton(D2R / "hd/character/enemy" / donor / "torso_lod0.model")
    skin = np.load(out.parent / "skinned_lod0.npz")
    bnames = list(skin["bones"])
    V = np.hstack([skin["pos"], np.ones((len(skin["pos"]), 1))])
    vb = np.array([[sk.idx[bnames[j]] for j in row] for row in skin["bone_idx"]])
    wts = skin["weights"]
    capv = vb[:, 0] == sk.idx[cap_bone]
    head, pbone = sk.idx[HEAD], sk.idx[cap_bone]
    cb = skin["pos"][capv].mean(0)

    def skinned(W, sel):
        P = np.zeros((sel.sum(), 3))
        for k in range(4):
            Mk = np.stack([W[j] @ sk.Binv[j] for j in range(len(W))])[vb[sel, k]]
            P += wts[sel, k, None] * np.einsum("nij,nj->ni", Mk, V[sel])[:, :3]
        return P

    times = sample_times(LENGTH)
    if "--shell" in a and "--timing" in a:
        # the corpse hand-over (shell up, body down) is a step: a knot just before it keeps the linear interpolation
        # between two samples from putting both halfway under the floor for a frame
        show0 = float(opt("--shell-show", shell_show_default()))
        if 0 < show0 < LENGTH:
            times = np.unique(np.append(times, [round(show0 - 0.0005, 6), round(show0, 6)]))
    # body: lowest vertex on the ground every sample
    body_loc, body_W, lifts = [], [], []
    for t in times:
        D = body_motion(sk, t)
        _l, W = sk.pose(D)
        lift = np.array([0.0, -skinned(W, ~capv)[:, 1].min(), 0.0])
        loc, W = sk.pose(D, lift)
        body_loc.append(loc)
        body_W.append(W)
        lifts.append(lift[1])
    # the part: follows the head until DETACH, then a falling path to its resting place
    Md = body_W[int(np.argmin(np.abs(times - DETACH)))][head] @ sk.Binv[head]
    pd = (Md @ np.append(cb, 1))[:3]
    Rd = Md[:3, :3]
    capb = skin["pos"][capv]

    def cap_at(phi, centre):
        R = euler(phi, 0, 0) @ Rd
        C = trans(centre) @ np.block([[R, np.zeros((3, 1))], [np.zeros((1, 3)), np.ones((1, 1))]]) @ trans(-cb)
        return C

    feet_z = skinned(body_W[-1], ~capv)[:, 2].max()
    probe = (cap_at(CAP_TURN, np.zeros(3)) @ np.hstack([capb, np.ones((len(capb), 1))]).T).T[:, :3]
    pf = np.array([pd[0], -probe[:, 1].min(), feet_z + CAP_GAP - probe[:, 2].min()])
    if POSE and "cap" in POSE:
        C_rest, pf, R_rest = cap_rest(capb, cb, POSE)
    caps = []
    for t, W in zip(times, body_W):
        if t <= DETACH:
            C = W[head] @ sk.Binv[head]
        elif POSE and "cap" in POSE:
            # fitted pose: the cap turns from its pose at DETACH to its rest pose on a falling path, lands at LAND
            s = min(1.0, (t - DETACH) / (LAND - DETACH))
            centre = np.array([pd[0] + (pf[0] - pd[0]) * s, pd[1] + (pf[1] - pd[1]) * s * s, pd[2] + (pf[2] - pd[2]) * (2 * s - s * s)])
            M = np.eye(4)
            M[:3, :3] = slerp_m(Rd, R_rest, s)
            C = trans(centre) @ M @ trans(-cb)
        elif t <= LAND:
            s = (t - DETACH) / (LAND - DETACH)
            centre = np.array([pd[0] + (pf[0] - pd[0]) * s, pd[1] + (pf[1] - pd[1]) * s * s, pd[2] + (pf[2] - pd[2]) * (2 * s - s * s)])
            C = cap_at(CAP_TURN * s, centre)
        else:
            s = (t - LAND) / (LENGTH - LAND)
            C = cap_at(CAP_TURN + 9 * np.sin(np.pi * 2 * s) * (1 - s), pf)
        caps.append(C)
    # --hide-body: from --shell-show on (and in `dead`) the whole skeleton but the cap sits --shell-drop units under the floor,
    # so the pale shell alone draws the corpse and the dark live body cannot show through it
    body_W_vis, W_last, caps_vis, C_last = list(body_W), body_W[-1], list(caps), caps[-1]
    if "--hide-body" in a:
        show_t, drop_d = float(opt("--shell-show", shell_show_default())), float(opt("--shell-drop", "12"))
        for i, t in enumerate(times):
            if t >= show_t - 1e-6:
                # through `cog`, not the root: the engine ignores the root's translation (with a dropped root the body
                # stays and the bones placed against it rise instead)
                D = body_motion(sk, t)
                D[sk.idx[COG]] = trans(np.array([0.0, -drop_d, 0.0])) @ D.get(sk.idx[COG], np.eye(4))
                body_loc[i], body_W[i] = sk.pose(D, np.array([0.0, lifts[i], 0.0]))
                if "--shell-cap" in a:
                    caps[i] = trans(np.array([0.0, -drop_d, 0.0])) @ caps[i]
        report_hide = {"from": show_t, "drop": drop_d, "cap_too": "--shell-cap" in a}
    else:
        report_hide = None
    # fall progress against classic's DT frames (--classic): what the camera sees, at classic's own frame times
    progress = None
    if "--classic" in a:
        sub = np.nonzero(~capv)[0][::20]
        sel = np.zeros(len(capv), bool)
        sel[sub] = True
        pc, pt = fall_progress(times, caps_vis, [skinned(W, sel) for W in body_W_vis], cb)
        ct, cc, ctop = classic_curve(opt("--classic", ""))
        tt = np.minimum(ct, LENGTH)
        mc, mt = np.interp(tt, times, pc), np.interp(tt, times, pt)
        progress = {"t": np.round(ct, 3).tolist(),
                    "classic_cap": np.round(cc, 3).tolist(), "classic_top": np.round(ctop, 3).tolist(),
                    "hd_cap": np.round(mc, 3).tolist(), "hd_top": np.round(mt, 3).tolist(),
                    "rms_cap": round(float(np.sqrt(np.nanmean((mc - cc) ** 2))), 3),
                    "rms_top": round(float(np.sqrt(np.nanmean((mt - ctop) ** 2))), 3)}
    # cap bone local = head^-1 (cap motion) B_cap
    for loc, W, C in zip(body_loc, body_W, caps):
        loc[pbone] = np.linalg.inv(W[head]) @ C @ sk.B[pbone]

    shell = None
    if "--shell" in a:
        shell = add_shell(a, opt, out, folder, name, donor, sk, body_W, body_loc, times, pbone, W_last, C_last)

    # the animation file
    src = D2R / "hd/character/enemy" / donor / "animation" / "combined.animations"
    g = dm.Gr2File.read(src.read_bytes())
    tree = dm.parse_tree(g)
    set_track = ClipWriter(tree).set_track

    rest_part = sk.Binv[head] @ sk.B[pbone]
    report = {"clips": {}, "length": LENGTH, "detach": DETACH, "land": LAND, "cap_vertices": int(capv.sum())}
    for an in tree["Animations"]:
        root = next(x for x in an["TrackGroups"] if x["Name"] == "root")
        tracks = {tr["Name"]: tr for tr in root["TransformTracks"]}
        if an["Name"] == "death":
            for bn, tr in tracks.items():
                i = sk.idx[bn]
                set_track(tr, [mat_q(L[i][:3, :3]) for L in body_loc], [L[i][:3, 3] for L in body_loc], times)
            an["Duration"] = float(LENGTH)
            report["clips"]["death"] = "own"
        elif an["Name"] == "dead":
            for bn, tr in tracks.items():
                i = sk.idx[bn]
                set_track(tr, [mat_q(body_loc[-1][i][:3, :3])], [body_loc[-1][i][:3, 3]], [0.0])
            report["clips"]["dead"] = "own (last death pose)"
        else:
            set_track(tracks[cap_bone], [mat_q(rest_part[:3, :3])], [rest_part[:3, 3]], [0.0])
            report["clips"][an["Name"]] = "donor, %s held at rest" % cap_bone
            if shell:
                hid = shell["hidden_local"]
                set_track(tracks[shell["bone"]], [mat_q(hid[:3, :3])], [hid[:3, 3]], [0.0])
                report["clips"][an["Name"]] += ", %s under the floor" % shell["bone"]
    dest = out / folder / "animation" / "combined.animations"
    size = d2r_mesh.write_model(g, tree, dest)
    report["animations_bytes"] = size

    # state machine and unit
    sm = json.loads((D2R / "hd/character/enemy" / donor / ("%s_state_machine.json" % donor)).read_text(encoding="utf-8"))
    old, new = "data/hd/character/enemy/%s/animation/" % donor, "data/%s/animation/" % folder
    if "--dead-fade" in a:
        # the death -> dead crossfade blends the bone transforms of both clips; with the corpse hand-over done by
        # dropping bones under the floor, a blend of "body down, shell up" with anything else puts both partly under the
        # floor, and nothing is drawn for most of the fade (a blink). Stock fades are 0.2 s; 0.01 is stock too.
        fade = float(opt("--dead-fade", "0.2"))
        for grp in sm.get("transitions", []):
            for it in grp.get("settings", []):
                if grp.get("from") == 0 and it.get("to") == 12:
                    it["crossfadeSeconds"] = fade
        report["dead_fade"] = fade
    sm_text = json.dumps(sm).replace(old, new)
    sm_path = "data/%s/%s_state_machine.json" % (folder, name)
    (out / folder / ("%s_state_machine.json" % name)).write_text(sm_text, encoding="utf-8")
    unit_file = out / common.UNIT_JSON
    unit = json.loads(unit_file.read_text(encoding="utf-8"))
    for e in unit["entities"]:
        for c in e["components"]:
            if "state_machine_filename" in c:
                c["state_machine_filename"] = sm_path
    deps = unit["dependencies"]
    deps["json"] = [j for j in deps["json"] if not j["path"].endswith("_state_machine.json")] + [{"path": sm_path}]
    unit_file.write_text(json.dumps(unit, separators=(",", ":")), encoding="utf-8")
    report["state_machine"] = sm_path
    report["ground_lift_range"] = [round(float(min(lifts)), 3), round(float(max(lifts)), 3)]
    report["cap_rest"] = [round(float(x), 3) for x in pf]
    if report_hide:
        report["hide_body"] = report_hide
    if progress:
        report["progress"] = progress
    report["keys"] = [round(k[0], 3) for k in DEATH]
    if shell:
        report["shell"] = {k: v for k, v in shell.items() if k != "hidden_local"}
    (out.parent / "death.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report))

    if "--preview" in a:
        from hdkit.sprite3d import render_soft
        tex_path = opt("--albedo", "") if "--albedo" in a else out.parent / "albedo_final.png"
        tex = np.asarray(Image.open(tex_path).convert("RGB")).astype(np.float32) if Path(tex_path).is_file() else None
        allv = np.ones(len(V), bool)
        picks = [int(np.argmin(np.abs(times - x))) for x in np.linspace(0, LENGTH, 10)]
        frames = [skinned(body_W_vis[i], ~capv) for i in picks]
        caps_p = [(caps_vis[i] @ V[capv].T).T[:, :3] for i in picks]
        tris = skin["tris"]
        pts = np.vstack([np.vstack([f, c]) for f, c in zip(frames, caps_p)])
        scale, centre = render_soft.framing(pts, 220)
        rows = []
        order = np.concatenate([np.nonzero(~capv)[0], np.nonzero(capv)[0]])
        inv = np.empty(len(order), int)
        inv[order] = np.arange(len(order))
        T2 = inv[tris]
        sh = np.load(out.parent / "shell.npz") if shell and (out.parent / "shell.npz").is_file() else None
        for az, cl in ((0, 4), (90, 5)):
            row = []
            for i, f, c in zip(picks, frames, caps_p):
                P, TT, UU = np.vstack([f, c]), T2, skin["uv"][order]
                if sh is not None and times[i] >= shell["show"] - 1e-6:
                    TT = np.vstack([T2, sh["tris"] + len(P)])
                    P = np.vstack([P, sh["pos"]])
                    UU = np.vstack([UU, sh["uv"]])
                img = render_soft.render(P, TT, az, 30, 220, scale, centre, uv=UU if tex is not None else None,
                                         tex=tex, bg=(40, 40, 48))
                row.append(img[..., :3])
            rows.append(np.hstack(row))
            if "--classic" in a:
                cf = Path(opt("--classic", ""))
                cf = cf / "dt" if (cf / "dt").is_dir() else cf
                n_dt = dt_timing(opt("--classic", ""))[0]
                cr = []
                for i in np.linspace(0, n_dt - 1, 10).round().astype(int):
                    im = Image.open(cf / ("d%d_f%02d.png" % (cl, i))).convert("RGBA")
                    tile = Image.new("RGB", (220, 220), (40, 40, 48))
                    im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
                    tile.paste(im, ((220 - im.width) // 2, (220 - im.height) // 2), im)
                    cr.append(np.asarray(tile))
                rows.append(np.hstack(cr))
        sheet = Image.fromarray(np.vstack(rows).astype(np.uint8))
        dr = ImageDraw.Draw(sheet)
        labs = ["3D front", "classic DT d%d" % 4, "3D side", "classic DT d%d" % 5] if "--classic" in a else ["3D front", "3D side"]
        for k, lab in enumerate(labs):
            dr.text((4, 4 + 220 * k), lab, fill=(230, 230, 230))
        sheet.save(opt("--preview", ""))
        print("preview", opt("--preview", ""), sheet.size)


if __name__ == "__main__":
    main()
