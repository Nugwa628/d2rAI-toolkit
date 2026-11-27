"""Lane hdfx-r3-shadows2: spot-light and shadowed-point shadows, depth clamp, the game's caster rules (view/shadow.py).
No game files needed; the GPU test is opt-in (HDFX_GPU_TESTS=1, GPU gate free) like the other GPU tests."""
import math
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
from hdkit.view import shadow  # noqa: E402


class _R:
    def __init__(self, **kw):
        self.props = kw

    def get(self, k, d=None):
        return self.props.get(k, d)


def _clip(M, p):
    c = M @ np.append(np.asarray(p, float), 1.0)
    return c[:3] / c[3], c[3]


class TestCasterRules(unittest.TestCase):
    def test_lit_cast_shadows_is_not_read(self):
        # the game's Default / Uber material set-up reads only Shadows and Shadows.ShadowThreshold (default 0.0)
        self.assertIsNone(shadow.caster_threshold(_R(**{"Lit.CastShadows": True})))
        self.assertEqual(shadow.caster_threshold(_R(Shadows=True)), 0.0)
        self.assertEqual(shadow.caster_threshold(_R(Shadows=True, **{"Lit.CastShadows": True,
                                                                     "Shadows.ShadowThreshold": 0.3})), 0.3)

    def test_old_guess_switch(self):
        old = shadow.LIT_CASTS_SHADOWS
        try:
            shadow.LIT_CASTS_SHADOWS = True
            self.assertEqual(shadow.caster_threshold(_R(**{"Lit.CastShadows": True})), 0.0)
        finally:
            shadow.LIT_CASTS_SHADOWS = old


class TestDepthClamp(unittest.TestCase):
    def test_casters_write_clamped_depth(self):
        # depth clamp emulated: clip z kept inside, the slice z clamped in the fragment stage (both caster kinds)
        self.assertIn("o.clip.z = 0.5 * o.clip.w", shadow.CASTER_WGSL)
        self.assertIn("clamp(i.zw.x / i.zw.y, 0.0, 1.0)", shadow.CASTER_WGSL)
        self.assertIn("fn vs_shadow_fx", shadow.FX_CASTER_WGSL)
        self.assertIn("clamp(c.z / c.w, 0.0, 1.0)", shadow.FX_CASTER_WGSL)


class TestSpot(unittest.TestCase):
    def setUp(self):
        self.pos = np.array([3.0, 10.0, -2.0])
        self.axis = np.array([0.2, -1.0, 0.1])
        self.axis /= np.linalg.norm(self.axis)
        self.c = math.cos(math.radians(30.0))
        self.M = shadow.spot_matrix(self.pos, self.axis, self.c, 25.0, 0.5)
        self.M1 = shadow.spot_matrix(self.pos, self.axis, self.c)

    def test_depth_reversed(self):
        # the game's form: z / w = near / d, no far plane (near 0.5 here)
        q, _w = _clip(self.M, self.pos + self.axis * 0.5)
        self.assertAlmostEqual(q[2], 1.0, places=5)
        q, _w = _clip(self.M, self.pos + self.axis * 25.0)
        self.assertAlmostEqual(q[2], 0.5 / 25.0, places=6)

    def test_matches_game_forge_spot(self):
        # r1 capture frame 4928 (Rogue Encampment): the forge spot's matrix (Global Attenuation registers._m11, view space,
        # row vectors; x / w, y / w = the map uv) and the view inverse; preset: angle 90, its +Z axis, y 3.185224
        G = np.array([[5.76031983e-01, 4.86816943e-01, 0.0, 9.86043990e-01],
                      [-1.96061566e-01, -4.10231590e-01, 0.0, 6.59748316e-02],
                      [3.60196799e-01, -3.07756305e-01, 0.0, -1.52854681e-01],
                      [3.76434570e+02, -2.53453247e+02, 1.0, -1.03916504e+02]])
        VI = np.array([[7.0710671e-01, 2.9802315e-08, -7.0710671e-01, 0.0],
                       [-3.5355335e-01, 8.6602533e-01, -3.5355330e-01, 0.0],
                       [6.1237246e-01, 4.9999994e-01, 6.1237246e-01, 0.0],
                       [1.0269326e+04, 4.4197427e+02, 1.1849327e+04, 1.0]])
        V = np.linalg.inv(VI)
        GW = V @ G                                   # world (row vectors) -> game clip
        A = np.stack([GW[:3, 0], GW[:3, 1], GW[:3, 3]])
        eye = np.linalg.solve(A, -np.array([GW[3, 0], GW[3, 1], GW[3, 3]]))
        self.assertAlmostEqual(eye[1], 3.185224, places=2)
        x, y, z, w = -0.01606924, 0.952274, -0.01526641, 0.3044387     # the preset's orientation
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = shadow.spot_from_preset({"angle": 90.0, "distance": 25.0, "shadowCaster": True}, np.block(
            [[R, eye[:, None]], [np.zeros((1, 3)), np.ones((1, 1))]]), shadow=0)
        self.assertEqual(M["spot"]["shadow"], 0)
        S = shadow.spot_matrix(M["pos"], M["spot"]["dir"], M["spot"]["cone"])
        rng = np.random.default_rng(5)
        for _ in range(50):
            d = rng.normal(size=3)
            d /= np.linalg.norm(d)
            p = eye + (np.asarray(M["spot"]["dir"]) * 3.0 + d) * rng.uniform(1.0, 8.0)
            g = np.append(p, 1.0) @ GW
            if g[3] <= 0.1:
                continue
            q, _w = _clip(S, p)
            self.assertAlmostEqual(q[2], g[2] / g[3], places=4)                 # depth
            gu = g[:2] / g[3] - 0.5                                            # uv about the centre
            mu = 0.5 * q[:2]
            self.assertAlmostEqual(np.linalg.norm(gu), np.linalg.norm(mu), places=3)

    def test_axis_and_cone_edge(self):
        q, w = _clip(self.M, self.pos + self.axis * 7.0)
        self.assertAlmostEqual(q[0], 0.0, places=6)
        self.assertAlmostEqual(q[1], 0.0, places=6)
        self.assertAlmostEqual(w, 7.0, places=5)
        # a point on the cone's edge lands on the map's edge
        side = np.cross(self.axis, [0.0, 1.0, 0.0])
        side /= np.linalg.norm(side)
        half = math.acos(self.c)
        p = self.pos + 7.0 * (math.cos(half) * self.axis + math.sin(half) * side)
        q, _ = _clip(self.M, p)
        self.assertAlmostEqual(max(abs(q[0]), abs(q[1])), 1.0, places=5)

    def test_records_and_uniforms(self):
        class L:
            game = {"points": [{"name": "torch", "pos": (1.0, 2.0, 3.0), "radius": 20.0, "color": [1, 1, 1], "exp": 2.0,
                                "spot": {"dir": [0, -1, 0], "cone": 0.8, "shadow": 2}},
                               {"name": "plain", "pos": (5.0, 2.0, 3.0), "radius": 9.0, "color": [1, 1, 1]}]}
            points = ({"name": "player", "pos": (0.0, 8.0, 0.0), "radius": 34.0, "color": [1, 1, 1], "cube": 0},
                      {"name": "bad", "pos": (0.0, 8.0, 9.0), "radius": 4.0, "color": [1, 1, 1],
                       "spot": {"dir": [1, 0, 0], "cone": 0.5, "shadow": 7}})
        recs = shadow.local_records(L)
        self.assertEqual([r["name"] for r in recs], ["torch", "player", "bad"])     # the plain point needs no record
        self.assertEqual(recs[2]["shadow"], -1)                                     # only map indices 0..3
        u = shadow.local_uniforms(recs)
        self.assertEqual(len(u), shadow.SH_FLOATS)
        self.assertEqual(u[56], 3)
        self.assertEqual(tuple(u[64:68]), (1.0, 2.0, 3.0, 1.0))                     # pos, kind spot
        self.assertEqual(tuple(u[68:72]), (0.0, -1.0, 0.0, np.float32(0.8)))        # travel dir, cone
        self.assertEqual(u[72], 2.0)                                                # spot map index
        self.assertEqual(u[64 + 12 + 3], 2.0)                                       # the cube record
        m = 64 + 12 * shadow.MAX_LOCAL + 16 * 2
        self.assertTrue(np.allclose(u[m:m + 16].reshape(4, 4).T, recs[0]["M"], atol=1e-5))
        # taps and offsets read from the game (cb5[192..195]; ps_models_p's 0.2 normal offset)
        self.assertAlmostEqual(u[57], 0.0029296875)
        self.assertAlmostEqual(u[58], 0.0009765625)
        self.assertAlmostEqual(u[59], 0.005859375)
        self.assertAlmostEqual(u[60], 0.001953125)
        self.assertAlmostEqual(u[61], 0.2, places=6)
        self.assertAlmostEqual(u[63], 0.0005, places=7)

    def test_game_points_keep_spot(self):
        from hdkit.view import gpu
        g = {"points": [{"pos": (0, 1, 0), "radius": 5, "color": [1, 1, 1], "spot": {"dir": [0, -1, 0], "cone": 0.9}}]}
        self.assertEqual(gpu.game_points(g)[0]["spot"]["cone"], 0.9)


class TestCube(unittest.TestCase):
    @staticmethod
    def _face_uv(d):
        """The D3D / Vulkan cube table: (face, u, v) of direction d."""
        x, y, z = d
        ax = np.argmax(np.abs(d))
        if ax == 0:
            f, sc, tc, ma = (0 if x > 0 else 1), (-z if x > 0 else z), -y, abs(x)
        elif ax == 1:
            f, sc, tc, ma = (2 if y > 0 else 3), x, (z if y > 0 else -z), abs(y)
        else:
            f, sc, tc, ma = (4 if z > 0 else 5), (x if z > 0 else -x), -y, abs(z)
        return f, (sc / ma + 1) / 2, (tc / ma + 1) / 2

    def test_faces_match_sampling(self):
        pos = np.array([2.0, 5.0, -1.0])
        Ms = shadow.cube_face_matrices(pos)
        rng = np.random.default_rng(3)
        for _ in range(200):
            d = rng.normal(size=3)
            d /= np.linalg.norm(d)
            f, u, v = self._face_uv(d)
            q, w = _clip(Ms[f], pos + 4.0 * d)
            self.assertGreater(w, 0.0)
            self.assertAlmostEqual((q[0] + 1) / 2, u, places=6)
            self.assertAlmostEqual((1 - q[1]) / 2, v, places=6)


class TestShaders(unittest.TestCase):
    def test_stubs_and_hooks(self):
        for name in ("shaders.wgsl", "fx.wgsl"):
            src = (TOOLS / "hdkit" / "view" / name).read_text("utf-8")
            stub = shadow.STUB_RE.search(src).group(0)
            self.assertIn("fn shd_local(", stub)                  # the browser page: a plain point light
            self.assertIn("shd_local(", src[src.index("@shadow-stub-end"):])
            patched = shadow.patch(src)
            self.assertIn("fn shd_spot_vis", patched)
            self.assertIn("fn shd_cube_vis", patched)
            self.assertNotIn("{ return 1.0; }\nfn shd_local", patched)

    def test_ground_keeps_cone_in_bare(self):
        self.assertIn("bare = bare + e * shd_local(gu.pp[k].xyz, p, n, 0.0)", shadow.GROUND_WGSL)


def _gpu_free():
    import os
    if os.environ.get("HDFX_GPU_TESTS") != "1":
        return False
    try:
        from hdkit import gpugate
        return gpugate.check().free
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_gpu_free(), "GPU tests are opt-in (HDFX_GPU_TESTS=1) and need the GPU gate free")
class TestGpu(unittest.TestCase):
    def test_pipelines_build(self):
        # the patched receivers, the casters (slices, spot maps, cube) and the ground pass compile and bind
        from hdkit.view import gpu
        R = gpu.Renderer()
        sm = R.shadow
        sm._alloc(64, 32, 16)
        sm._model_pipe()
        sm._cube_pipe()
        dev = R.device
        dev.create_shader_module(code=shadow.RECEIVER_WGSL + shadow.GROUND_WGSL)
        dev.create_shader_module(code=shadow.patch((TOOLS / "hdkit" / "view" / "shaders.wgsl").read_text("utf-8")))
        from hdkit.fx import render as fr
        sm._fx_pipe(fr.FxRenderer(R, None))                 # the effect casters (vs_fx wrapped for the depth clamp)


if __name__ == "__main__":
    unittest.main()
