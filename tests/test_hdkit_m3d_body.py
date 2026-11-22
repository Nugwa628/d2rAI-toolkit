"""hdkit.monster3d body and rigid-part scripts (s3_bodymat, s4_procbody, s4_hdbody, s4_hdcap, s4_hdcorpse) on synthetic
data: the skeleton edit, tubes and weights, the design graph, the weights smoothing, the BC block splice that protects
texels, the radial part's grid and UV discs, the corpse timing and strip. No game files. The Blender smoke test runs only
when a Blender is found (HDKIT_M3D_BLENDER or `blender` on PATH)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
    import skimage, numba  # noqa: E401,F401  (monster3d's extra dependencies)
except ImportError as _e:
    raise unittest.SkipTest("hdkit.monster3d needs numpy, Pillow, scipy, scikit-image and numba: %s" % _e)
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hdkit.formats import d2r_model as dm, d2r_texture  # noqa: E402
from hdkit.monster3d import common  # noqa: E402
from hdkit.monster3d import s3_bodymat as bm  # noqa: E402
from hdkit.monster3d import s4_hdbody as hb  # noqa: E402
from hdkit.monster3d import s4_hdcap as hc  # noqa: E402
from hdkit.monster3d import s4_hdcorpse as hcp  # noqa: E402
from hdkit.monster3d import s4_procbody as pb  # noqa: E402
from hdkit.view import gr2  # noqa: E402

MONSTER = common.load_monster(data={"name": "testbiped", "donor": "testdonor", "part_bone": "jaw",
                                    "shell_bone": "spare"})


def monster(**extra):
    m = dict(MONSTER)
    m.update(extra)
    return mock.patch.object(common, "MONSTER", m)


# ---- a synthetic T-pose skeleton with the bone names of a humanoid donor ---------------------------------------------
BONES = [  # name, parent, local offset
    ("root", -1, (0, 0, 0)), ("pelvis", 0, (0, 3.9, 0)), ("spine_01", 1, (0, 0.5, 0)), ("spine_02", 2, (0, 0.5, 0)),
    ("chest", 3, (0, 0.6, 0)), ("neck", 4, (0, 0.85, 0)), ("head", 5, (0, 0.45, 0)), ("jaw", 6, (0, 0.2, 0.1)),
]
for _s, _x in (("L", 1.0), ("R", -1.0)):
    _c = len(BONES)
    BONES += [("%s_Clavicle" % _s, 4, (0.3 * _x, 0.5, 0)), ("%s_UpperArm" % _s, _c, (0.5 * _x, 0, 0)),
              ("%s_ForeArm" % _s, _c + 1, (1.2 * _x, 0, 0)), ("%s_Wrist" % _s, _c + 2, (1.1 * _x, 0, 0))]
    _t = len(BONES)
    BONES += [("%s_Thigh" % _s, 1, (0.4 * _x, -0.1, 0)), ("%s_Calf" % _s, _t, (0, -1.8, 0.05)),
              ("%s_Foot" % _s, _t + 1, (0, -1.6, -0.05)), ("%s_Ball" % _s, _t + 2, (0, -0.3, 0.45)),
              ("%s_Toe_END" % _s, _t + 3, (0, -0.1, 0.3))]
    _w = BONES.index(("%s_Wrist" % _s, _c + 2, (1.1 * _x, 0, 0)))
    for _f, _dz in (("A", 0.08), ("B", 0.0), ("D", -0.08)):
        _p = _w
        for _k in "ABC":
            BONES.append(("%s_finger_%s_%s" % (_s, _f, _k), _p, (0.12 * _x, -0.02, _dz if _k == "A" else 0)))
            _p = len(BONES) - 1
    _p = _w
    for _k in "ABC":
        BONES.append(("%s_thumb_A_%s" % (_s, _k), _p, (0.06 * _x, -0.03, 0.1 if _k == "A" else 0.05)))
        _p = len(BONES) - 1
IDQ = gr2.compose(np.zeros(3), (0, 0, 0, 1), np.eye(3).ravel())
QUAT = (0, 0, 0, 1) if np.allclose(IDQ, np.eye(4)) else (1, 0, 0, 0)


def skeleton():
    """bone dicts as edit_skeleton reads them (Name, ParentIndex, Transform, InverseWorldTransform)."""
    out, W = [], []
    for name, parent, off in BONES:
        tr = dm.Transform(0, off, QUAT, np.eye(3).ravel())
        loc = gr2.compose(np.array(off, float), np.array(QUAT, float), np.eye(3).ravel())
        W.append(loc if parent < 0 else W[parent] @ loc)
        out.append({"Name": name, "ParentIndex": parent, "Transform": tr,
                    "InverseWorldTransform": [float(x) for x in np.linalg.inv(W[-1]).T.ravel()]})
    return out


class Skeleton(unittest.TestCase):
    def test_edit_scales_arms_and_keeps_the_part_chain(self):
        bones = skeleton()
        before = {b["Name"]: list(b["InverseWorldTransform"]) for b in bones}
        P = dict(pb.DEFAULT, upper=1.5, fore=1.2, hip=1.4, clav=1.25)
        Wb = pb.edit_skeleton(bones, P)
        # the rigid part's chain (cog .. head, part bone) is untouched
        for n in ("pelvis", "chest", "head", "jaw"):
            self.assertEqual(before[n], [b for b in bones if b["Name"] == n][0]["InverseWorldTransform"])
        J0 = {n: np.array(o) for n, _p, o in BONES}
        fore = np.linalg.norm(Wb["L_Wrist"][:3, 3] - Wb["L_ForeArm"][:3, 3])
        self.assertAlmostEqual(fore, 1.1 * 1.2, places=5)
        upper = np.linalg.norm(Wb["L_ForeArm"][:3, 3] - Wb["L_UpperArm"][:3, 3])
        self.assertAlmostEqual(upper, np.linalg.norm(J0["L_ForeArm"]) * 1.5, places=5)
        self.assertAlmostEqual(Wb["L_Thigh"][0, 3] - Wb["pelvis"][0, 3], 0.4, places=5)   # hip scales local z, not x
        # the inverse binds follow the new worlds
        b = [b for b in bones if b["Name"] == "L_Wrist"][0]
        inv = np.array(b["InverseWorldTransform"]).reshape(4, 4).T
        self.assertTrue(np.allclose(inv @ Wb["L_Wrist"], np.eye(4), atol=1e-9))

    def test_scale_map_names(self):
        s = pb.scale_map({"upper": 2, "fore": 3, "hip": 4})
        self.assertEqual(s["L_ForeArm"].tolist(), [2, 1, 1])
        self.assertEqual(s["R_Wrist"].tolist(), [3, 1, 1])
        self.assertEqual(s["R_Thigh"].tolist(), [1, 1, 4])
        self.assertEqual(s["L_UpperArm"].tolist(), [1, 1, 1])


class Tubes(unittest.TestCase):
    def test_grid_tris(self):
        t = pb.grid_tris(4, 5)
        self.assertEqual(t.shape, (2 * 3 * 4, 3))
        self.assertEqual(t.max(), 19)

    def test_blend_weights(self):
        v = np.linspace(0, 3, 301)
        w = pb.blend_weights(v, [1.0, 2.0], ["a", "b", "c"], [0.2, 0.1])
        self.assertTrue(np.allclose(w.sum(1), 1))
        self.assertTrue(np.allclose(w[v < 0.79], [1, 0, 0]))
        self.assertTrue(np.allclose(w[(v > 1.21) & (v < 1.89)], [0, 1, 0]))
        mid = np.argmin(np.abs(v - 1.0))
        self.assertAlmostEqual(w[mid, 0], 0.5, places=6)

    def test_resample_and_frames(self):
        C, s, sk = pb.resample([[0, 0, 0], [0, 1, 0], [1, 1, 0]], 0.05)
        self.assertAlmostEqual(sk[-1], 2.0)
        self.assertTrue(np.allclose(C[0], 0) and np.allclose(C[-1], [1, 1, 0]))
        T, N, B = pb.frames_along(C)
        for X in (T, N, B):
            self.assertTrue(np.allclose(np.linalg.norm(X, axis=1), 1, atol=1e-6))
        self.assertTrue(np.allclose((T * N).sum(1), 0, atol=1e-6))

    def test_tube_radius_and_domes(self):
        C, s, _sk = pb.resample([[0, 0, 0], [0, 2, 0]], 0.1)
        pos, v, u = pb.tube(C, s, lambda x: 0.3, 12)
        self.assertEqual(pos.shape[1:], (13, 3))
        mid = pos[len(pos) // 2]
        r = np.hypot(mid[:, 0] - C[len(C) // 2 - 0, 0], mid[:, 2] - C[len(C) // 2, 2])
        self.assertTrue(np.allclose(r, 0.3, atol=1e-6))
        self.assertLess(v.min(), 0)
        self.assertGreater(v.max(), s[-1])
        self.assertTrue(np.allclose(pb.gnarl_field(None, 1)(np.zeros(3), np.zeros(3)), 0))

    def test_smooth_normals_sphere(self):
        C, s, _sk = pb.resample([[0, -1, 0], [0, 1, 0]], 0.05)
        pos, v, u = pb.tube(C, s, lambda x: 1.0, 16)
        n = pb.smooth_normals(pos.reshape(-1, 3), pb.grid_tris(*pos.shape[:2]))
        self.assertTrue(np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-6))

    def test_build_and_assemble(self):
        bones = skeleton()
        P = dict(pb.DEFAULT)
        Wb = pb.edit_skeleton(bones, P)
        parts = pb.build_body(Wb, P, 8, 0.25)
        self.assertEqual(sorted({p.kind for p in parts}), ["arm", "foot", "leg", "torso"])
        pos, uv, t, bn, ww, kinds = pb.assemble(parts, P)
        self.assertEqual(len(pos), len(uv))
        self.assertTrue(np.allclose(ww.sum(1), 1))
        x0, y0, x1, y1 = P["rect"]
        self.assertTrue((uv[:, 0] * 2048 >= x0 - 1e-6).all() and (uv[:, 0] * 2048 <= x1 + 1e-6).all())
        self.assertTrue((uv[:, 1] * 2048 >= y0 - 1e-6).all() and (uv[:, 1] * 2048 <= y1 + 1e-6).all())
        self.assertTrue(set(bn.ravel()) <= {b[0] for b in BONES})


class Config(unittest.TestCase):
    def test_bones_from_the_monster(self):
        with monster():
            self.assertEqual(pb.part_bone(), "jaw")
            self.assertEqual(pb.shell_bone(), "spare")
            self.assertEqual(bm.part_bone(), "jaw")
        with monster(part_bone="", shell_bone=""):
            self.assertRaises(SystemExit, pb.part_bone)
            self.assertRaises(SystemExit, pb.shell_bone)

    def test_params_order(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.json"
            f.write_text(json.dumps({"upper": 9}))
            with monster(body={"params": {"upper": 7, "fore": 2}, "rect": [0, 0, 64, 64]}):
                P = pb.params(["--params", str(f)])
        self.assertEqual((P["upper"], P["fore"], P["rect"]), (9, 2, [0, 0, 64, 64]))
        self.assertEqual(P["hip"], pb.DEFAULT["hip"])

    def test_corpse_times(self):
        with monster():
            t = hc.corpse_times(0.76)
        self.assertTrue(np.allclose(t, (0.40, 0.60, 0.55, 0.50, 0.60, 0.64), atol=0.002))
        with monster(corpse={"timing": {"hide": 1.5}}):
            self.assertEqual(hc.corpse_times(2.0)[-1], 1.5)


class Design(unittest.TestCase):
    def joints(self):
        Wb = pb.edit_skeleton(skeleton(), dict(pb.DEFAULT))
        return {n: M[:3, 3].copy() for n, M in Wb.items()}

    def test_graph(self):
        D = dict(hb.DESIGN)
        nodes, edges = hb.graph(self.joints(), D)
        P = np.array([n["p"] for n in nodes])
        L = np.linalg.norm(P[[a for a, _ in edges]] - P[[b for _, b in edges]], axis=1)
        self.assertLessEqual(L.max(), D["step"] + 1e-9)
        for n in nodes:
            self.assertAlmostEqual(sum(n["w"].values()), 1.0, places=6)
        chains = {n["chain"] for n in nodes}
        self.assertTrue({"torso", "L_arm", "R_arm", "L_leg", "R_leg", "L_foot"} <= chains)
        self.assertTrue(any(c.startswith("L_claw_") for c in chains))
        # mirrored: as many nodes on the right as on the left
        self.assertEqual(sum(c.startswith("L_") for c in (n["chain"] for n in nodes)),
                         sum(c.startswith("R_") for c in (n["chain"] for n in nodes)))

    def test_noise(self):
        X = np.random.default_rng(0).uniform(-5, 5, (2000, 3))
        n = hb.perlin(X, 3)
        self.assertLess(np.abs(n).max(), 1.2)
        self.assertTrue(np.allclose(n, hb.perlin(X, 3)))
        self.assertFalse(np.allclose(n, hb.perlin(X, 4)))
        self.assertTrue(np.allclose(hb.perlin(np.floor(X), 3), 0, atol=1e-9))   # gradient noise is 0 on the lattice

    def test_fields_and_paint(self):
        nodes, edges = hb.graph(self.joints(), dict(hb.DESIGN))
        G = {"nodes": [{"p": n["p"].tolist(), "r": n["r"], "w": n["w"], "chain": n["chain"], "kind": n["kind"]}
                       for n in nodes], "edges": [list(e) for e in edges], "joints": {}, "design": dict(hb.DESIGN)}
        rng = np.random.default_rng(1)
        Pn = np.array([n["p"] for n in G["nodes"]])
        X = Pn[rng.integers(len(Pn), size=500)] + rng.normal(0, 0.05, (500, 3))
        N = rng.normal(size=(500, 3))
        N /= np.linalg.norm(N, axis=1, keepdims=True)
        sh = dict(hb.SHAPE, knobs=20)
        disp, aux = hb.fields(X, N, G, sh, 5, fine=True)
        self.assertEqual(disp.shape, (500,))
        self.assertLess(np.abs(disp).max(), 0.5)
        col, glow, rough = hb.paint_vertices(X, N, aux, G, dict(hb.PAINT), 5)
        self.assertEqual(col.shape, (500, 3))
        self.assertTrue((col >= 0).all() and (col <= 1).all())
        self.assertTrue((glow >= 0).all() and (glow <= 1).all() and (rough >= 0).all() and (rough <= 1).all())

    def test_weights_smoothing(self):
        # a straight two-node chain: a mesh along it gets weights mixed by position and smoothed, top 4, sum 1
        G = {"nodes": [{"p": [0, 0, 0], "r": 0.2, "w": {"a": 1}, "chain": "c", "kind": "leg"},
                       {"p": [0, 1, 0], "r": 0.2, "w": {"b": 1}, "chain": "c", "kind": "leg"},
                       {"p": [0, 2, 0], "r": 0.2, "w": {"b": 0.5, "c": 0.5}, "chain": "c", "kind": "leg"}],
               "edges": [[0, 1], [1, 2]]}
        C, s, _sk = pb.resample([[0, 0, 0], [0, 2, 0]], 0.05)
        pos, v, u = pb.tube(C, s, lambda x: 0.2, 8, dome0=False, dome1=False)
        tris = pb.grid_tris(*pos.shape[:2])
        pos = pos.reshape(-1, 3)
        with tempfile.TemporaryDirectory() as d:
            W = Path(d)
            (W / "graph.json").write_text(json.dumps(G))
            np.savez(W / "lod0.npz", pos=pos, nrm=pos * 0 + [1, 0, 0], uv=np.zeros((len(pos), 2)), tris=tris)
            hb.cmd_weights([str(W), "--smooth", "5"])
            z = np.load(W / "body_lod0.npz")
            self.assertTrue(np.allclose(z["ww"].sum(1), 1))
            bot = z["ww"][pos[:, 1] < 0.1]
            names = z["bn"][pos[:, 1] < 0.1]
            self.assertTrue((names[np.arange(len(bot)), bot.argmax(1)] == "a").all())
            top = pos[:, 1] > 1.9
            self.assertTrue(set(z["bn"][top][z["ww"][top] > 0.2].tolist()) <= {"b", "c"})


class Splice(unittest.TestCase):
    """The BC block splice: blocks without a changed texel (and blocks touching a protected texel) keep their bytes."""

    def make(self, d, rgba):
        p = Path(d) / "t.texture"
        d2r_texture.write_bc3(p, rgba, 3)
        return p

    def test_block_any(self):
        m = np.zeros((16, 16), bool)
        m[5, 9] = True
        g = bm.block_any(m, 4, 4)
        self.assertEqual(int(g.sum()), 1)
        self.assertTrue(g[1, 2])

    def test_write_spliced_keeps_unchanged_blocks(self):
        rng = np.random.default_rng(3)
        A = rng.integers(0, 256, (32, 32, 4)).astype(np.uint8)
        A[..., 3] = 255
        with tempfile.TemporaryDirectory() as d:
            p = self.make(d, A)
            fmt, w, h, mips = d2r_texture.read_texture(p)
            B = A.copy()
            B[0:4, 0:4, :3] = 0           # one level-0 block changed
            changed = (B != A).any(-1)
            new = d2r_texture._dds_levels(B, len(mips), "DXT5", "RGBA")
            rep = bm.write_spliced(p, fmt, w, h, new, mips, changed, 16)
            _f, _w, _h, mips2 = d2r_texture.read_texture(p)
            self.assertEqual(mips2[0][16:], mips[0][16:])          # every other level-0 block byte for byte
            self.assertNotEqual(mips2[0][:16], mips[0][:16])
            self.assertEqual(rep["blocks_new"], 1 + 1 + 1)         # the block over it at each of the 3 mips

    def test_capsafe_protects_part_blocks(self):
        rng = np.random.default_rng(4)
        A = rng.integers(0, 256, (32, 32, 4)).astype(np.uint8)
        A[..., 3] = 255
        with tempfile.TemporaryDirectory() as d:
            p = self.make(d, A)
            fmt, w, h, mips = d2r_texture.read_texture(p)
            B = A.copy()
            B[:, :, :3] = 255 - A[:, :, :3]          # everything changed
            part = np.zeros((32, 32), bool)
            part[6, 6] = True                         # one protected texel in level-0 block (1, 1)
            changed = (B != A).any(-1)
            new = bm.levels_keeping_part(B, fmt, mips, part)
            hb.write_spliced_capsafe(p, fmt, w, h, new, mips, changed, 16, part)
            _f, _w, _h, mips2 = d2r_texture.read_texture(p)
            k = (1 * 8 + 1) * 16
            self.assertEqual(mips2[0][k:k + 16], mips[0][k:k + 16])        # the part's block kept
            self.assertNotEqual(mips2[0][:16], mips[0][:16])               # a body block rewritten
            self.assertEqual(mips2[1][:16], mips[1][:16])                  # mip 1 block 0 covers the part: kept
            dec = d2r_texture.decode(fmt, w, h, mips2[0])
            self.assertTrue((dec[6, 6] == d2r_texture.decode(fmt, w, h, mips[0])[6, 6]).all())


class RadialPart(unittest.TestCase):
    def frame(self, R=1.5):
        return {"cen": [0, 7.5, 0], "up": [0, 1, 0], "ex": [1, 0, 0], "ez": [0, 0, 1], "R_bins": [R] * 72,
                "S_local": [0.1, -0.05]}

    def test_outline_round(self):
        C = dict(hc.CAP, wobble=[0, 5, 9])
        th = np.linspace(-np.pi, np.pi, 50)
        self.assertTrue(np.allclose(hc.outline(self.frame(), C, th), 1.5))
        C["R_scale"] = 2.0
        self.assertTrue(np.allclose(hc.outline(self.frame(), C, th), 3.0))

    def test_grid_mesh_and_discs(self):
        C = json.loads(json.dumps(hc.CAP))
        fr = self.frame()
        g = hc.build_grid(fr, C, 32, 12, lump=False)
        self.assertEqual(g["X"].shape, (32, 13, 3))
        pos, nrm, uv, tris = hc.mesh_from_grid(fr, g, C)
        self.assertEqual(len(pos), len(uv))
        self.assertTrue(np.allclose(np.linalg.norm(nrm, axis=1), 1, atol=1e-6))
        top = pos[:, 1] > 7.5 + 0.5
        self.assertGreater(nrm[top, 1].mean(), 0.8)            # outward: the dome's normals point up
        px = uv * 2048
        inside = np.zeros(len(px), bool)
        for l, t, r in (C["uv_top"], C["uv_under"]):
            inside |= np.hypot(px[:, 0] - l - r, px[:, 1] - t - r) <= r + 2
        self.assertTrue(inside.all())                          # every UV in one of the two discs
        self.assertLess(tris.max(), len(pos))
        T = hc.grid_tris(32, 13)
        self.assertEqual(len(T), 32 * 2 + 2 * 32 * 10)


class Corpse(unittest.TestCase):
    def test_strip_box(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(hcp.strip_box(d), (1792, 0, 256))
            (Path(d) / "build.json").write_text(json.dumps({"strip": 0.25}))
            self.assertEqual(hcp.strip_box(d), (1536, 0, 512))

    def test_pack(self):
        pos = hcp.pack([(10, 10), (20, 5), (30, 30)], 64, 1)
        self.assertIsNotNone(pos)
        self.assertIsNone(hcp.pack([(70, 10)], 64, 1))

    def test_align(self):
        a = np.array([0.3, 0.2, 0.9])
        a /= np.linalg.norm(a)
        R = hcp.align(a, np.array([0.0, 1.0, 0.0]))
        self.assertTrue(np.allclose(R @ a, [0, 1, 0], atol=1e-9))
        self.assertTrue(np.allclose(R @ R.T, np.eye(3), atol=1e-9))

    def test_part_bone_in_settle(self):
        with monster(part_bone="mandible"):
            self.assertEqual(hcp.bone_name(hcp.PART), "mandible")
            self.assertEqual(hcp.bone_name("head"), "head")


def blender_exe():
    b = os.environ.get("HDKIT_M3D_BLENDER") or shutil.which("blender")
    return b if b and Path(b).is_file() else None


@unittest.skipUnless(blender_exe(), "no Blender (set HDKIT_M3D_BLENDER or put blender on PATH)")
class BlenderSmoke(unittest.TestCase):
    def test_model_tiny_graph(self):
        nodes = [{"p": [0, y, 0], "r": 0.3, "w": {"pelvis": 1}, "chain": "torso", "kind": "torso"} for y in (0, 0.5, 1)]
        nodes += [{"p": [x, 1, 0], "r": 0.15, "w": {"L_UpperArm": 1}, "chain": "L_arm", "kind": "arm"} for x in (0.5, 1)]
        G = {"nodes": nodes, "edges": [[0, 1], [1, 2], [2, 3], [3, 4]], "root": 1}
        script = ROOT / "hdkit" / "monster3d" / "blender" / "hdbody_bl.py"
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "graph.json").write_text(json.dumps(G))
            cmd = [blender_exe(), "-b", "--factory-startup", "-noaudio", "--python", str(script), "--", "model", d]
            if shutil.which("prlimit"):
                cmd = ["prlimit", "--as=10000000000"] + cmd
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            self.assertIn("MODEL low", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])
            lo, hi = np.load(Path(d) / "low_base.npz"), np.load(Path(d) / "high_base.npz")
            self.assertGreater(len(hi["V"]), len(lo["V"]))
            self.assertLess(np.abs(lo["V"][:, 1] - 0.5).max(), 1.0)      # back in game axes (y up)


if __name__ == "__main__":
    unittest.main()
