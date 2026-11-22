"""hdkit.monster3d build-side steps and the format modules they use, on synthetic data only (no game files, no Blender).

s5_build (rigid part split, DXT5nm layout, the monster config), s5_stretch (edge stretch), s3_gutter (gutter fill),
common.squash_u, hdkit.formats.d2r_mesh (vertex encode, tangents), monster_sheet (COF parse, compose, classic root),
hdkit.sprite3d.upscale (bleed, alpha), render_soft (a cube), rig_fit (rotation), obj_npz (round trip)."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
try:
    import numpy, PIL, scipy, numba  # noqa: E401,F401  (pip install -r hdkit/requirements.txt, plus numba)
except ImportError as _e:
    raise unittest.SkipTest("hdkit.monster3d needs numpy, Pillow, scipy and numba: %s" % _e)
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from hdkit.formats import d2r_mesh, monster_sheet  # noqa: E402
from hdkit.monster3d import common, s3_gutter, s5_build, s5_stretch  # noqa: E402
from hdkit.sprite3d import render_soft, rig_fit, upscale  # noqa: E402


def cube():
    pos = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = np.array([t for a, b, c, d in q for t in ((a, b, c), (a, c, d))], int)
    return pos, tris


class Config(unittest.TestCase):
    def test_monster_defaults_and_names(self):
        m = common.load_monster(data={"name": "stalker", "donor": "mummy1", "rigid_part": [0.7, 0.2], "part_bone": "jaw"})
        self.assertEqual(m["unit"], "m3d_stalker")
        self.assertEqual(m["folder"], "hd/character/enemy/m3d/stalker")
        self.assertEqual(m["profile"], "mummy1")
        with mock.patch.object(common, "MONSTER", m), mock.patch.object(common, "need_monster", lambda: m):
            cfg = s5_build.monster_cfg()
        self.assertEqual(cfg["cap"], (0.7, 0.2))
        self.assertEqual(cfg["cap_bone"], "jaw")
        self.assertEqual(cfg["K"], 9.0)

    def test_no_part_without_plane(self):
        m = common.load_monster(data={"name": "x", "donor": "zombie1"})
        with mock.patch.object(common, "need_monster", lambda: m):
            cfg = s5_build.monster_cfg()
        self.assertNotIn("cap", cfg)

    def test_token_name(self):
        self.assertEqual(common.load_monster(data={"token": "~k", "donor": "d"})["name"], "7ek")


class Build(unittest.TestCase):
    def test_split_cap_leaves_no_joining_triangle(self):
        # a strip of triangles crossing the plane y = 0.5
        ys = np.linspace(0, 1, 6)
        pos = np.array([[x, y, 0.0] for y in ys for x in (0.0, 1.0)])
        tris = np.array([[2 * i, 2 * i + 1, 2 * i + 2] for i in range(5)] + [[2 * i + 1, 2 * i + 3, 2 * i + 2] for i in range(5)])
        nrm = np.tile([0, 0, 1.0], (len(pos), 1))
        uv = pos[:, :2].copy()
        P, N, U, T, cap = s5_build.split_cap(pos, nrm, uv, tris, 0.5, 0.0)
        side = cap[T]
        self.assertFalse((side.any(1) & ~side.all(1)).any())
        self.assertEqual(len(T), len(tris))
        self.assertGreater(len(P), len(pos))
        # every copy sits where its original was
        for v in range(len(pos), len(P)):
            self.assertTrue(np.any(np.all(np.isclose(pos, P[v]), 1)))

    def test_dxt5nm(self):
        src = np.zeros((2, 2, 3), np.uint8)
        src[..., 0], src[..., 1], src[..., 2] = 200, 50, 255
        out = s5_build.dxt5nm(src)
        self.assertEqual(out.shape, (2, 2, 4))
        self.assertEqual(tuple(out[0, 0]), (255, 205, 0, 200))
        flat = np.zeros((1, 1, 3), np.uint8) + np.array([128, 127, 255], np.uint8)
        self.assertEqual(tuple(s5_build.dxt5nm(flat)[0, 0]), s5_build.FLAT_NRM)

    def test_squash_u(self):
        img = np.full((8, 16, 3), 200, np.uint8)
        out = common.squash_u(img, 0.25, 7)
        self.assertTrue((out[:, 12:] == 7).all())
        self.assertTrue((out[:, :12] == 200).all())
        self.assertIs(common.squash_u(img, 0), img)


class Stretch(unittest.TestCase):
    def test_edge_stretch(self):
        pos, tris = cube()
        posed = pos.copy()
        posed[7] *= 3.0                       # one corner pulled out
        r, ex = s5_stretch.edge_stretch(pos, posed, tris)
        self.assertAlmostEqual(float(r.min()), 1.0)
        self.assertGreater(float(r.max()), 2.0)
        self.assertGreater(float(ex.sum()), 0)
        body = np.ones(8, bool)
        body[7] = False                       # the rigid part is left out
        r2, ex2 = s5_stretch.edge_stretch(pos, posed, tris, body)
        self.assertTrue(np.allclose(r2, 1.0))
        self.assertEqual(float(ex2.sum()), 0.0)

    def test_edges_unique(self):
        _, tris = cube()
        self.assertEqual(len(s5_stretch.edges(tris)), 18)


class Gutter(unittest.TestCase):
    def test_fill_from_nearest_and_keep_strip(self):
        a = np.zeros((8, 8, 4), np.uint8)
        a[2:4, 2:4] = 100
        a[:, 6:] = 50                          # the corpse strip (right quarter)
        cov = np.zeros((8, 8), bool)
        cov[2:4, 2:4] = True
        out, hole = s3_gutter.fill_array(a, cov, 0.25)
        self.assertTrue((out[:, :6] == 100).all())
        self.assertTrue((out[:, 6:] == 50).all())
        self.assertEqual(int(hole.sum()), 8 * 6 - 4)

    def test_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.npz"
            np.savez(p, uv=np.array([[0.0, 0.0], [0.5, 0.0], [0.0, 0.5]]), tris=np.array([[0, 1, 2]]))
            cov = s3_gutter.coverage(p, 32)
        self.assertTrue(cov[2, 2])
        self.assertFalse(cov[30, 30])


class Mesh(unittest.TestCase):
    def test_tangents_follow_u(self):
        pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], float)
        nrm = np.tile([0, 0, 1.0], (4, 1))
        uv = pos[:, :2].copy()
        tris = np.array([[0, 1, 2], [1, 3, 2]])
        t, hand = d2r_mesh.tangents(pos, nrm, uv, tris)
        self.assertTrue(np.allclose(t, [1, 0, 0]))
        self.assertTrue(np.all(np.abs(hand) == 1))

    def test_i8_and_scale(self):
        self.assertEqual(list(d2r_mesh.i8(np.array([1.0, -1.0, 0.0, 2.0]))), [127, -127, 0, 127])
        self.assertGreater(d2r_mesh.vertex_scale(np.array([[0, -3.0, 1]])), 3.0)


def synthetic_cof(n_frames=2, dirs=1):
    """A COF with one `tr` layer (code 1), the given frames and directions, box and speed 256."""
    b = bytearray([1, n_frames, dirs, 20]) + bytes(4)
    b += struct.pack("<4i", -10, -20, 10, 0) + struct.pack("<I", 256)
    b += bytes([1, 1, 1, 0, 0]) + b"hth\0"
    b += bytes(range(n_frames))
    b += bytes([1] * (n_frames * dirs))           # draw order: the composite code of each layer (1 = tr)
    return bytes(b)


class ClassicFrames(unittest.TestCase):
    def test_parse_cof(self):
        c = monster_sheet.parse_cof(synthetic_cof(3, 2))
        self.assertEqual((c["frames"], c["dirs"], c["speed"]), (3, 2, 256))
        self.assertEqual(c["layers"][0]["comp"], "tr")
        self.assertEqual(c["layers"][0]["wclass"], "hth")
        self.assertEqual(c["keys"], [0, 1, 2])
        self.assertEqual(c["box"], (-10, -20, 10, 0))
        with self.assertRaises(ValueError):
            monster_sheet.parse_cof(synthetic_cof(2, 1) + b"x")

    def test_compose_and_box(self):
        cof = monster_sheet.parse_cof(synthetic_cof(1, 1))
        pix = bytes([0, 5, 5, 0])                         # 2 x 2: index 0 is transparent
        entry = {"cof": cof, "layers": {"tr": [(0, 0, 2, 2, [(-1, -2, 2, 2, pix)])]}}
        box = monster_sheet.mode_box(entry)
        self.assertEqual(box, (-1, -2, 1, 0))
        pal = [(0, 0, 0)] * 256
        pal[5] = (10, 20, 30)
        im = np.asarray(monster_sheet.compose(entry, 0, 0, box, pal))
        self.assertEqual(tuple(im[0, 1]), (10, 20, 30, 255))
        self.assertEqual(im[0, 0, 3], 0)

    def test_classic_root_from_env(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "data/global/monsters/AB").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"HDKIT_CLASSIC": d}):
                self.assertEqual(monster_sheet.token_dir("ab").name, "AB")
                with self.assertRaises(SystemExit):
                    monster_sheet.token_dir("zz")


class Upscale(unittest.TestCase):
    def test_bleed_keeps_colour(self):
        rgba = np.zeros((9, 9, 4), np.uint8)
        rgba[4, 4] = (200, 100, 50, 255)
        out = upscale.bleed(rgba, 3)
        self.assertTrue(np.allclose(out[4, 6], (200, 100, 50), atol=1))

    def test_alpha_up_size_and_edge(self):
        a = np.zeros((4, 4), np.uint8)
        a[1:3, 1:3] = 255
        up = upscale.alpha_up(a, 4)
        self.assertEqual(up.shape, (16, 16))
        self.assertEqual(int(up[8, 8]), 255)
        self.assertEqual(int(up[0, 0]), 0)

    def test_nearest_upscale(self):
        f = np.zeros((4, 4, 4), np.uint8)
        f[1:3, 1:3] = (100, 150, 200, 255)
        (big,) = upscale.upscale([f], 2, "nearest")
        self.assertEqual(big.shape, (8, 8, 4))
        self.assertEqual(tuple(big[4, 4]), (100, 150, 200, 255))


class SoftRender(unittest.TestCase):
    def test_cube(self):
        pos, tris = cube()
        img = render_soft.render(pos, tris, 0.0, 30.0, 64, ss=1)
        self.assertEqual(img.shape, (64, 64, 4))
        self.assertGreater(int((img[..., 3] > 0).sum()), 200)
        n = render_soft.vertex_normals(pos, tris)
        self.assertTrue(np.allclose(np.linalg.norm(n, axis=1), 1))


class RigFit(unittest.TestCase):
    def test_rot_between(self):
        a, b = np.array([1.0, 0, 0]), np.array([0, 0, 2.0])
        R = rig_fit.rot_between(a, b)
        self.assertTrue(np.allclose(R @ a, [0, 0, 1]))
        self.assertTrue(np.allclose(R @ R.T, np.eye(3)))

    def test_profiles_map_roles(self):
        for name, p in rig_fit.PROFILES.items():
            self.assertIn("u", p, name)
            self.assertIn("pelvis", p["bones"], name)


class ObjNpz(unittest.TestCase):
    def test_round_trip(self):
        pos, tris = cube()
        uv = (pos[:, :2] + 1) / 2
        with tempfile.TemporaryDirectory() as d:
            src, obj, back = Path(d) / "a.npz", Path(d) / "a.obj", Path(d) / "b.npz"
            np.savez(src, pos=pos, nrm=render_soft.vertex_normals(pos, tris), uv=uv, tris=tris)
            env = dict(os.environ, PYTHONPATH=str(REPO))
            for args in (["to_obj", str(src), str(obj), "--uv"], ["to_npz", str(back), str(obj)]):
                subprocess.run([sys.executable, "-m", "hdkit.sprite3d.obj_npz", *args], check=True, env=env,
                               stdout=subprocess.DEVNULL)
            r = np.load(back)
            self.assertEqual(len(r["pos"]), len(pos))
            self.assertEqual(len(r["tris"]), len(tris))
            key = lambda p_, u_: sorted(map(tuple, np.round(np.hstack([p_, u_]), 4)))  # noqa: E731
            self.assertEqual(key(r["pos"], r["uv"]), key(pos, uv))
            # the same triangles, by their corner positions
            tri_key = lambda P, T: sorted(tuple(sorted(map(tuple, np.round(P[t_], 4)))) for t_ in T)  # noqa: E731
            self.assertEqual(tri_key(r["pos"], r["tris"]), tri_key(pos, tris))


if __name__ == "__main__":
    unittest.main()
