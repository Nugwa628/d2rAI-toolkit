"""hdkit.view.shadow: D2R's directional shadow slices, casters, receivers, coverage (lane hdfx-r3-shadows).

CPU tests need no game files; the corpus test skips without the 95032 tree; the GPU test runs with HDFX_GPU_TESTS=1
and a free hdkit GPU gate (it skips otherwise)."""
import os
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from hdkit.view import shadow  # noqa: E402
from hdkit.view.gpu import Light  # noqa: E402

CORPUS = Path(os.environ.get("HDFX_CORPUS") or "/nonexistent")
# calib r3 (rig_meph_r3.json): the moon (shadow index 0), the player key light (1), the fill (none)
R3_DIRS = [{"dir": [0.32465, 0.73728, -0.59248], "color": [9.6633, 8.71856, 6.71935], "shadow_mode": 0},
           {"dir": [0.60182, 0.79864, 0.0], "color": [30.0, 28.11765, 26.35294], "shadow_mode": 1},
           {"dir": [-0.81915, 0.0, 0.57358], "color": [3.59216, 3.67059, 4.0], "shadow_mode": 4294967295}]


def r3_light(**shadow):
    return Light(game={"dirs": R3_DIRS, "sh": [[1.0, 1.0, 1.0]] + [[0.0, 0.0, 0.0]] * 8, "ibl": {"intensity": 0.4}},
                 shadow=dict({"on": True}, **shadow))


class _R:
    def __init__(self, **p):
        self.p = p

    def get(self, k, d=None):
        return self.p.get(k, d)


class TestSpec(unittest.TestCase):
    def test_off(self):
        self.assertIsNone(shadow.spec_for(Light()))
        self.assertIsNone(shadow.spec_for(Light(shadow={"on": False})))

    def test_game_slices(self):
        s = shadow.spec_for(r3_light())
        self.assertEqual([x.name for x in s.slices], ["world", "units"])
        self.assertEqual(s.lights, (0, 1, -1))           # 4294967295 (no shadow) -> -1
        self.assertEqual(s.global_slice, 1)
        self.assertAlmostEqual(s.strength, 0.9)
        for x in s.slices:                                # both along the moon (the light with shadow index 0)
            self.assertTrue(np.allclose(np.asarray(x.dir), np.asarray(R3_DIRS[0]["dir"]) / np.linalg.norm(R3_DIRS[0]["dir"])))
        self.assertEqual([x.casters for x in s.slices], [("world",), ("unit", "fx")])

    def test_lit_model_one_slice(self):
        s = shadow.spec_for(Light(shadow={"on": True}))
        self.assertEqual(len(s.slices), 1)
        self.assertEqual(s.global_slice, -1)
        self.assertEqual(s.lights, (0,))
        self.assertIn("fx", s.slices[0].casters)

    def test_overrides(self):
        s = shadow.spec_for(r3_light(size=1024, slices=[{"mode": 1}, {"mode": 5}], strength=0.5))
        self.assertEqual(s.size, 1024)
        self.assertEqual([x.mode for x in s.slices], [1, 5])
        self.assertEqual(s.strength, 0.5)

    def test_depth_matches_capture(self):
        """The unit's feet sit 189.4 units from the light's eye: slice depths as the capture's matrices give for
        Mephisto's feet (slice 0 z 0.2817, slice 1 z 0.4630) and xy centred."""
        s = shadow.spec_for(r3_light(), centre=(3.0, 0.0, -2.0))
        M0, M1 = s.matrices()
        p = np.array([3.0, 0.0, -2.0, 1.0])
        self.assertAlmostEqual((M0 @ p)[2], 0.2817, delta=0.001)     # the slices' eyes differ by 0.3 units in game
        self.assertAlmostEqual((M1 @ p)[2], 0.4630, delta=0.002)
        self.assertTrue(np.allclose((M1 @ p)[:2], 0.0, atol=1e-9))
        L = np.asarray(s.slices[1].dir)
        q = np.append(p[:3] + 20.0 * L, 1.0)             # towards the light: nearer (larger z, reversed depth)
        self.assertGreater((M1 @ q)[2], (M1 @ p)[2])
        head = np.array([3.0, 20.0, -2.0, 1.0])           # the capture: 20 units up = 14.5 units nearer (0.5967 in slice 1)
        self.assertAlmostEqual((M1 @ head)[2], 0.5967, delta=0.002)

    def test_uniforms(self):
        s = shadow.spec_for(r3_light())
        u = s.uniforms()
        self.assertEqual(len(u), shadow.SH_FLOATS)
        self.assertEqual(tuple(u[0:4]), (2.0, np.float32(0.9), 1.0, np.float32(0.05)))
        self.assertEqual(tuple(u[4:8]), (0.0, 1.0, -1.0, -1.0))
        # light size in uv: 1.5 / the slice's width (cb5[31].xy, [33].xy = 0.00446, 0.00691 / 0.0077, 0.0115)
        self.assertAlmostEqual(u[40 + 2], 0.004463, places=5)
        self.assertAlmostEqual(u[44 + 3], 0.011519, places=5)
        self.assertEqual(tuple(u[52:54]), (np.float32(130.22), np.float32(240.71)))


class TestCasters(unittest.TestCase):
    def test_threshold(self):
        self.assertEqual(shadow.caster_threshold(_R(Shadows=True, **{"Shadows.ShadowThreshold": 0.4})), 0.4)
        self.assertEqual(shadow.caster_threshold(_R(Shadows=True)), 0.0)            # the game's default (r3-shadows2)
        self.assertIsNone(shadow.caster_threshold(_R(**{"Lit.CastShadows": True})))  # never read by the game
        self.assertIsNone(shadow.caster_threshold(_R(Shadows=None)))
        self.assertIsNone(shadow.caster_threshold(_R()))

    def test_raw_props(self):
        class P:
            def __init__(self, v):
                self.value = v

        class H:
            props = {"Shadows": P(b"\xff" * 16), "Shadows.ShadowThreshold": P(b"\xcd\xcc\xcc>" + b"\0" * 12)}
        self.assertAlmostEqual(shadow.caster_threshold(H()), 0.4, places=5)

    @unittest.skipUnless(CORPUS.is_dir(), "no 95032 corpus")
    def test_corpus(self):
        from hdkit.fx import rspec
        f = CORPUS / "hd/vfx/particles/missiles/rock_small/duriel_rock_small.particles"
        e = rspec.load(f.read_bytes())
        self.assertTrue(all(shadow.caster_threshold(r) == 0.5 for r in e.renderers if r.kind == "mesh"))
        f = CORPUS / "hd/vfx/particles/character/enemy/swarm1/vfx_swarm1_core.particles"
        e = rspec.load(f.read_bytes())
        self.assertTrue({round(shadow.caster_threshold(r), 3) for r in e.renderers
                         if shadow.caster_threshold(r) is not None} == {0.4})


class TestShaders(unittest.TestCase):
    def test_stubs_patched(self):
        for name in ("shaders.wgsl", "fx.wgsl"):
            src = (TOOLS / "hdkit" / "view" / name).read_text("utf-8")
            self.assertIn("// @shadow-stub-begin", src)            # the browser page keeps the no-shadow stubs
            self.assertNotIn("@group(1)", src)
            out = shadow.patch(src)
            self.assertNotIn("@shadow-stub-begin", out)
            self.assertIn("var<uniform> shd: ShdU", out)
            self.assertIn("fn shd_light(", out)
        with self.assertRaises(ValueError):
            shadow.patch("fn main() {}")

    def test_game_composition(self):
        """ps_models_p: the directional loop multiplies light k by shd_light(k) and the direct sum by G (sg)."""
        src = (TOOLS / "hdkit" / "view" / "shaders.wgsl").read_text("utf-8")
        self.assertIn("shd_light(k, wpos, n, sg)", src)
        self.assertIn("direct * (u.g2.w * sg)", src)

    def test_poisson(self):
        self.assertEqual(shadow.POISSON.shape, (16, 2))
        self.assertTrue(np.all(np.linalg.norm(shadow.POISSON, axis=1) <= 1.25))
        self.assertIn("vec2<f32>(-0.94201624, -0.39906216)", shadow.RECEIVER_WGSL)


class TestGround(unittest.TestCase):
    def test_terms_game(self):
        amb, dirs, pts = shadow.ground_terms(r3_light())
        self.assertTrue(np.allclose(amb, 0.4 / np.pi))     # SH L0 1 at the up normal, x IBL 0.4 / pi
        # the terrain's own light set (lane r3-shadows2, s2 terrain draw): the shadow-index-0 light only
        self.assertEqual(len(dirs), 1)
        self.assertTrue(np.allclose(dirs[0][1], np.asarray(R3_DIRS[0]["color"]) / np.pi))
        self.assertEqual(dirs[0][2], 0)
        g = dict(r3_light().game, terrain_dirs=[0, 1, 2])
        self.assertEqual(len(shadow.ground_terms(type("L", (), {"game": g, "points": ()})())[1]), 3)

    def test_terms_lit(self):
        L = Light(ambient=(0.1, 0.2, 0.3), key_col=(1.0, 1.0, 1.0))
        amb, dirs, _p = shadow.ground_terms(L)
        self.assertEqual(tuple(amb), (0.1, 0.2, 0.3))
        self.assertEqual(len(dirs), 1)


class TestCoverage(unittest.TestCase):
    def test_supported(self):
        from hdkit.fx import coverage, render
        for f in ("Shadows", "Lit.CastShadows"):
            self.assertIn(f, render.SUPPORTED_FEATURES)
            self.assertIn(f, coverage.SUPPORTED_RENDER_FEATURES)


def _gpu_free():
    import os
    if os.environ.get("HDFX_GPU_TESTS") != "1":
        return False
    try:
        from hdkit import gpugate
        return gpugate.check().free
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_gpu_free(), "GPU tests: set HDFX_GPU_TESTS=1 (and a free GPU gate)")
class TestGpu(unittest.TestCase):
    def test_box_shadow(self):
        """A box over a ground quad: the ground behind the box (away from the key light) is darker with shadows on, the
        ground on the light's side is not, and the receivers read 'off' again afterwards."""
        from hdkit.view import camera, gpu as G
        from hdkit.view.fxcli import ground_draw
        R = G.Renderer(msaa=4)                 # render_rect always resolves MSAA
        cam = camera.OrthoCamera(30, 45, 8.0)
        g = ground_draw(R, "#808080", 40.0)
        v = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float) * 2.0 + [0.0, 4.0, 0.0]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        pos, nrm, tris = [], [], []
        for q in faces:
            a, b, c, d = (v[i] for i in q)
            n = np.cross(b - a, d - a)
            o = len(pos)
            pos += [a, b, c, d]
            nrm += [n / np.linalg.norm(n)] * 4
            tris += [[o, o + 1, o + 2], [o, o + 2, o + 3]]
        pos, nrm, tris = np.array(pos), np.array(nrm), np.array(tris)
        vb, n = R._vbuf(pos, nrm, np.tile([1.0, 0, 0, 1], (len(pos), 1)), np.zeros((len(pos), 2)), tris)
        ub = R._ubuf()
        bind = R._bind(ub, R.texture([np.full((4, 4, 4), (200, 60, 60, 255), np.uint8)], srgb=True), R.default("nrm"),
                       R.default("orm"), R.default("mask"), R.default("strip"))
        box = G.Draw(vb, n, ub, bind, [], 0.0, pos[tris], np.zeros((len(tris), 3, 2)), "box", "box", "box")
        prep = G.Prepared([g, box])
        kd = np.array([-0.55, 0.75, 0.35])
        imgs = {}
        for on in (False, True):
            L = G.Light(key_dir=tuple(kd), key_col=(2.5, 2.5, 2.4), ambient=(0.3, 0.32, 0.35),
                        shadow={"on": True} if on else {})
            imgs[on] = R.render(prep, cam, (320, 200), (160, 130), "lit", L, True, ss=1).astype(float)
        # ground points under the box, pushed away from / towards the light, to pixels
        away = np.array([0.0, 0.0, 0.0]) - 4.0 * np.array([kd[0], 0.0, kd[2]]) / np.linalg.norm([kd[0], kd[2]])
        toward = -away
        for P, dark in ((away, True), (toward, False)):
            x, y = cam.to_pixels(P[None], (160, 130))[0]
            a, b = imgs[False][int(y), int(x), :3].mean(), imgs[True][int(y), int(x), :3].mean()
            if dark:
                self.assertLess(b, 0.7 * a)
            else:
                self.assertAlmostEqual(a, b, delta=2.0)
        self.assertEqual(R.shadow.stats["casters"], {"all": 2})
        R.render(prep, cam, (64, 40), (32, 26), "lit", G.Light(), True, ss=1)
        self.assertIsNone(R.shadow.spec)


if __name__ == "__main__":
    unittest.main()
