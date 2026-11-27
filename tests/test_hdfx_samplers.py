"""Tests of hdkit/fx/samplers.py (curves, shapes, turbulence, rand, engine calls).

Golden values (tests/data/hdfx_samplers_golden.json) were computed by running PopcornFX-Web's own JS engine with node
(tests/data/hdfx_samplers_gen.mjs); when node and that engine folder are present the same comparison also runs live.
No game files are needed; the corpus smoke test skips when the extracted `.particles` root is absent.
Run: python -m unittest tests.test_hdfx_samplers   (or discover -p "test_hdfx_*.py")
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import samplers as S  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
GOLDEN = json.loads((DATA / "hdfx_samplers_golden.json").read_text())
ENGINE_DIRS = [os.environ.get("HDFX_POPCORNFX_ENGINE", "")]
CORPUS = Path((os.environ.get("HDFX_PARTICLES_ROOT") or "/nonexistent"))


def f32b(*v):
    return struct.pack(f"<{len(v)}f", *v)


def curve_from_case(c):
    n, d = len(c["times"]), c["dim"]
    return S.Curve(c["times"], c["values"], c["tan"] or None, dim=d, mode="linear" if c.get("linear") else None,
                   min_limits=c.get("min"), max_limits=c.get("max"))


class CurveTests(unittest.TestCase):
    def test_passes_through_keys(self):
        c = S.Curve([0, 0.3, 1], [1, 4, 2], [0, 0, 1, 1, -1, 0])
        self.assertTrue(np.allclose(c.sample([0, 0.3, 1]).ravel(), [1, 4, 2], atol=1e-6))
        lin = S.Curve([0, 0.3, 1], [1, 4, 2])
        self.assertEqual(lin.mode, "linear")
        self.assertTrue(np.allclose(lin.sample([0, 0.3, 1]).ravel(), [1, 4, 2]))

    def test_hand_checked_values(self):
        # smoothstep: zero tangents between (0,0) and (1,1): 3u^2 - 2u^3
        c = S.Curve([0, 1], [0, 1], [0, 0, 0, 0])
        self.assertTrue(np.allclose(c.sample([0.25, 0.5]).ravel(), [0.15625, 0.5]))
        # real corpus curve (vfx_bonewall_bvt): keys 0,0.1,1 values 0,1,1, out tangent of key 0 = 11.9276 * 0.1 (scaled)
        w = S.Curve([0, 0.1, 1], [0, 1, 1], [11.9276, 1.1928, 0, 0, 0, 0])
        self.assertAlmostEqual(float(w.sample([0.05])[0, 0]), 0.5 * 1 + 0.125 * 1.1928, places=5)
        # the second tangent of a pair is the segment-scaled one: from_slopes stores slope * dt and the raw slope on the end keys
        s = S.Curve.from_slopes([0, 0.1, 1], [0, 1, 1], slopes_out=[11.9276, 0, 0], slopes_in=[11.9276, 0, 0])
        self.assertTrue(np.allclose(s.tangents[:2], [11.9276, 1.19276], atol=1e-5))
        self.assertTrue(np.allclose(s.sample([0.05]), w.sample([0.05]), atol=1e-4))

    def test_modes_wrap_and_invalid(self):
        step = S.Curve([0, 0.5, 1], [1, 2, 3], mode="step")
        self.assertEqual(step.sample([0, 0.4, 0.5, 0.9, 1.2]).ravel().tolist(), [1, 1, 2, 2, 3])
        lin = S.Curve([0, 1], [0, 2])
        self.assertEqual(lin.sample([-1, 2]).ravel().tolist(), [0, 2])
        self.assertAlmostEqual(float(S.Curve([0, 1], [0, 2], wrap="repeat").sample([1.25])[0, 0]), 0.5, places=6)
        self.assertAlmostEqual(float(S.Curve([0, 1], [0, 2], wrap="mirror").sample([1.25])[0, 0]), 1.5, places=6)
        bad = S.Curve([0.5], [3])                                   # fewer than two keys: engine samples zero
        self.assertFalse(bad.valid)
        self.assertEqual(bad.sample([0.5, 2]).ravel().tolist(), [0, 0])
        self.assertFalse(S.Curve([0, 1], [0, 1, 2]).valid)
        self.assertFalse(S.Curve([0, 1], [0, 1], [0, 0, 0]).valid)  # tangent count mismatch

    def test_limits_dim_and_json(self):
        c = S.Curve([0, 0.5, 1], [-1, 3, 0], [0, 0, 4, 4, 0, 0], min_limits=[0], max_limits=[2])
        self.assertTrue(np.all((c.sample(np.linspace(0, 1, 50)) >= 0) & (c.sample(np.linspace(0, 1, 50)) <= 2)))
        v = S.Curve([0, 1], [0, 1, 2, 3, 2, 1], [0] * 12, dim=3)
        self.assertEqual(v.sample([0.5]).shape, (1, 3))
        j = json.loads(json.dumps(v.to_json()))
        self.assertTrue(np.allclose(S.Curve.from_json(j).sample([0.3, 0.7]), v.sample([0.3, 0.7])))
        f = v.to_fields()
        self.assertTrue(np.allclose(S.Curve.from_fields(f).sample([0.3, 0.7]), v.sample([0.3, 0.7])))
        self.assertEqual(S.Curve.from_fields({17: f32b(0, 1), 18: f32b(0, 1)}).mode, "linear")   # no field 19

    def test_derive_and_integrate(self):
        c = S.Curve([0, 0.4, 1], [0, 2, 1], [0, 0.5, 0.3, 0.3, -0.2, 0])
        x = np.array([0.1, 0.35, 0.6, 0.9])
        h = 1e-5
        fd = (c.sample(x + h, np.float64) - c.sample(x - h, np.float64)) / (2 * h)
        self.assertTrue(np.allclose(c.derive(x), fd, atol=1e-3))
        xs = np.linspace(0, 0.8, 4001)
        ys = c.sample(xs, np.float64).ravel()
        trap = float(np.sum((ys[1:] + ys[:-1]) * np.diff(xs) / 2))
        self.assertAlmostEqual(float(c.integrate([0], [0.8])[0, 0]), trap, places=5)
        self.assertEqual(float(c.integrate([2], [3])[0, 0]), 0.0)          # outside the keys: nothing

    def test_golden_js(self):
        for c in GOLDEN["curves"]:
            with self.subTest(c["name"]):
                cv = curve_from_case(c)
                got = cv.sample(c["ts"], np.float64)
                ref = np.array(c["out"], dtype=np.float64)
                self.assertTrue(np.allclose(got, ref, rtol=1e-9, atol=1e-9), (got, ref))
                if c["dim"] == 1 and "min" not in c:
                    for b, want in zip([0, 0.2, 0.9], c["integral"]):
                        self.assertAlmostEqual(float(cv.integrate([0], [b], np.float64)[0, 0]), want, places=6)

    def test_double_curve(self):
        d = S.DoubleCurve(S.Curve([0, 1], [0, 1]), S.Curve([0, 1], [10, 20]))
        self.assertTrue(np.allclose(d.sample([0.5, 0.5], [0, 1]).ravel(), [0.5, 15]))


class TurbulenceTests(unittest.TestCase):
    @staticmethod
    def sampler(p):
        m = {"Strength": "strength", "Wavelength": "wavelength", "Octaves": "octaves", "Lacunarity": "lacunarity",
             "Gain": "gain", "Interpolator": "interpolator", "TimeScale": "time_scale", "TimeBase": "time_base",
             "TimeRandomVariation": "time_random_variation", "FlowFactor": "flow_factor",
             "DivergenceFactor": "divergence_factor", "InitialSeed": "initial_seed", "FastFakeFlow": "fast_fake_flow",
             "GlobalScale": "global_scale"}
        return S.TurbulenceSampler(lattice="web113", **{m[k]: v for k, v in p.items()})   # the JS goldens are 1.13

    def check_case(self, case):
        t = self.sampler(case["p"])
        grid = np.array(case["grid"], dtype=np.float32)
        for row in case["rows"]:
            for kind, fn in (("curl", t.sample_curl), ("potential", t.sample_potential)):
                got = fn(grid, row["time"])
                ref = np.array(row[kind], dtype=np.float32)
                self.assertTrue(np.array_equal(got, ref), (case["name"], kind, row["time"], np.abs(got - ref).max()))

    def test_bit_exact_against_js(self):
        for case in GOLDEN["turbulence"]:
            with self.subTest(case["name"]):
                self.check_case(case)

    def test_deterministic_and_seeded(self):
        pos = np.random.default_rng(1).uniform(-5, 5, (50, 3)).astype(np.float32)
        a = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=1).sample(pos, 0.7)
        b = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=1).sample(pos, 0.7)
        self.assertTrue(np.array_equal(a, b))
        c = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=1, initial_seed=7).sample(pos, 0.7)
        self.assertFalse(np.allclose(a, c))
        self.assertEqual(a.dtype, np.float32)
        self.assertTrue(np.all(np.isfinite(a)))

    def test_time_array_matches_scalar_calls(self):
        t = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=1)
        pos = np.random.default_rng(2).uniform(-3, 3, (6, 3)).astype(np.float32)
        times = np.array([0.0, 0.5, 0.5, 1.5, 0.0, 2.0])
        got = t.sample(pos, times)
        for i in range(6):
            self.assertTrue(np.array_equal(got[i], t.sample(pos[i:i + 1], times[i])[0]))

    def test_static_and_curl_is_div_free(self):
        t = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=0, octaves=2, interpolator="quintic")
        p = np.array([[0.31, 0.42, 0.53]], dtype=np.float64)
        h = 1e-2
        div = 0.0
        for a in range(3):
            e = np.zeros(3)
            e[a] = h
            div += (t.sample(p + e, 0)[0, a] - t.sample(p - e, 0)[0, a]) / (2 * h)
        self.assertLess(abs(div), 0.2 * float(np.abs(t.sample(p, 0)).max()) + 0.05)
        self.assertTrue(np.array_equal(t.sample(p, 0), t.sample(p, 9)))   # time_scale 0 = no animation

    def test_fields_mapping(self):
        t = S.TurbulenceSampler.from_fields({10: f32b(2.0), 11: f32b(0.05), 19: f32b(1.0), 20: struct.pack("<i", 4), 23: struct.pack("<i", 2),
                                             30: b"\x01"})
        self.assertEqual((t.p["global_scale"], t.p["octaves"], t.interp, t.p["fast_fake_flow"]), (2.0, 4, 2, True))
        self.assertAlmostEqual(t.p["strength"], 0.05, places=6)
        self.assertEqual(S.TurbulenceSampler.from_fields({}).p["octaves"], 2)

    def test_fast_trig_close_to_libm(self):
        x = np.linspace(-20, 20, 801, dtype=np.float32)
        self.assertLess(float(np.abs(S.fast_sin(x) - np.sin(x)).max()), 1.1e-3)
        y = np.linspace(-1, 1, 401, dtype=np.float32)
        self.assertLess(float(np.abs(S.fast_asin(y) - np.arcsin(y)).max()), 1e-3)

    @unittest.skipUnless(shutil.which("node") and any(d and (Path(d) / "turbulence.js").exists() for d in ENGINE_DIRS),
                         "needs node and the PopcornFX-Web engine folder")
    def test_live_js_reference(self):
        src = next(Path(d) for d in ENGINE_DIRS if d and (Path(d) / "turbulence.js").exists())
        with tempfile.TemporaryDirectory(prefix="hdfx-ref-") as td:
            eng = Path(td) / "engine"
            eng.mkdir()
            for n in ("curves.js", "turbulence.js", "parser.js"):
                shutil.copy(src / n, eng / n)
            (Path(td) / "package.json").write_text('{"type":"module"}')
            r = subprocess.run(["node", str(DATA / "hdfx_samplers_gen.mjs"), str(eng)], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            live = json.loads(r.stdout)
        self.assertEqual(live["turbulence"], GOLDEN["turbulence"])
        self.assertEqual(live["curves"], GOLDEN["curves"])



class D2RTurbulenceTests(unittest.TestCase):
    """Turbulence as D2R.exe 2.9 runs it (lane vmfix; docs/hdfx-vm-re.md "Turbulence sampler")."""

    def test_field_ids(self):
        f = {24: f32b(1.0), 25: f32b(0.3), 26: f32b(0.0), 31: f32b(0.5)}
        p = S.TurbulenceSampler.from_fields(f).p
        self.assertEqual((p["time_scale"], p["gain_multiplier"]), (1.0, 0.5))
        self.assertAlmostEqual(p["time_base"], 0.3, places=6)
        self.assertEqual(p["time_random_variation"], 0.0)

    def test_lattice_odd_negated_and_continuous(self):
        d, w = S.RotatableNoise(lattice="d2r"), S.RotatableNoise(lattice="web113")
        np.testing.assert_array_equal(d.V[:256:2], w.V[:256:2])
        np.testing.assert_array_equal(d.V[1:256:2], -w.V[1:256:2])
        np.testing.assert_array_equal(d.base, S.fast_asin(d.V[:256]))
        v0 = d.V.copy()
        d.set_time(1e-4)
        self.assertLess(np.abs(d.V - v0).max(), 5e-3)              # no jump between phase 0 and the next frames
        # odd entries run the other way round than in 1.13: sin(s t - a) vs sin(s t + a + pi)
        w.set_time(0.3), d.set_time(0.3)
        self.assertFalse(np.allclose(d.V[1:256:2], w.V[1:256:2], atol=1e-3))
        np.testing.assert_allclose(d.V[:256:2], w.V[:256:2], atol=2e-3)

    def test_time_argument_ignored(self):
        class Ctx:
            count, time, rand = 2, 0.4, None
        vf = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=1)
        p = np.array([[0.1, 0.2, 0.3], [0.5, 0.1, 0.9]], np.float32)
        out = S.ENGINE_CALLS["samplerVectorField:sample_float3_float_SceneCtx"](Ctx, vf, p, np.zeros(2, np.float32))
        np.testing.assert_array_equal(out, vf.sample(p, 0.4))

class ShapeTests(unittest.TestCase):
    def test_golden_js(self):
        for c in GOLDEN["shapes"]:
            with self.subTest(c["name"]):
                p = c["p"]
                sym = lambda k: p[k]["sym"] if k in p else None
                kind = {"BOX": "box", "SPHERE": "sphere", "COMPLEX_ELLIPSOID": "ellipsoid", "CYLINDER": "cylinder",
                        "CAPSULE": "capsule", "CONE": "cone"}[sym("ShapeType")]
                sh = S.ShapeSampler(kind, box=p.get("BoxDimensions", (0.5, 0.5, 0.5)), radius=p.get("Radius", 1), inner=p.get("InnerRadius", 0),
                                    height=p.get("Height", 0), position=p.get("Position", (0, 0, 0)), euler=p.get("EulerOrientation", (0, 0, 0)),
                                    scale=p.get("NonUniformScale"), hemisphere=p.get("Hemisphere", False), volume=c.get("volume", False))
                pcs = np.array(c["pcs"])
                self.assertTrue(np.allclose(sh.sample_position(pcoords=pcs, dtype=np.float64), np.array(c["pos"]), atol=1e-9))
                self.assertTrue(np.allclose(sh.sample_normal(pcoords=pcs, dtype=np.float64), np.array(c["nrm"]), atol=1e-9))

    def test_surface_properties(self):
        r = S.RandCtx(3)
        sph = S.ShapeSampler("sphere", radius=2).sample_position(r, 500)
        self.assertTrue(np.allclose(np.linalg.norm(sph, axis=1), 2, atol=1e-5))
        vol = S.ShapeSampler("sphere", radius=2, volume=True).sample_position(r, 500)
        self.assertLessEqual(float(np.linalg.norm(vol, axis=1).max()), 2 + 1e-5)
        box = S.ShapeSampler("box", box=(2, 4, 6), position=(1, 1, 1)).sample_position(r, 500) - 1
        on_face = np.isclose(np.abs(box), [1, 2, 3], atol=1e-5).any(axis=1)
        self.assertTrue(on_face.all() and (np.abs(box) <= [1.0001, 2.0001, 3.0001]).all())
        cyl, nrm = S.ShapeSampler("cylinder", radius=1.5, height=4).sample(r, 300)
        self.assertTrue(np.allclose(np.hypot(cyl[:, 0], cyl[:, 2]), 1.5, atol=1e-5) and np.abs(cyl[:, 1]).max() <= 2 + 1e-6)
        self.assertTrue(np.allclose(np.linalg.norm(nrm, axis=1), 1, atol=1e-5))
        cone = S.ShapeSampler("cone", radius=1, height=2).sample_position(r, 300)
        self.assertTrue(np.allclose(np.hypot(cone[:, 0], cone[:, 2]), 1 - cone[:, 1] / 2, atol=1e-5))
        cap = S.ShapeSampler("capsule", radius=1, height=2).sample_position(r, 400)
        d = np.hypot(cap[:, 0], cap[:, 2]) ** 2 + (np.abs(cap[:, 1]) - 1).clip(0) ** 2
        self.assertTrue(np.allclose(d, 1, atol=1e-4))

    def test_pcoords_agree_and_determinism(self):
        sh = S.ShapeSampler("ellipsoid", radius=1, scale=(1, 2, 3), euler=(0, 45, 0))
        pc = sh.sample_pcoords(20, S.RandCtx(5))
        p, n = sh.sample(pcoords=pc)
        self.assertTrue(np.array_equal(p, sh.sample_position(pcoords=pc)) and np.array_equal(n, sh.sample_normal(pcoords=pc)))
        self.assertTrue(np.array_equal(sh.sample_position(S.RandCtx(9), 10), sh.sample_position(S.RandCtx(9), 10)))

    def test_mesh_surface_and_collection(self):
        quad = S.ShapeSampler("mesh").set_mesh([[0, 0, 0], [2, 0, 0], [2, 0, 2], [0, 0, 2]], [0, 1, 2, 0, 2, 3])
        p, n = quad.sample(S.RandCtx(2), 300)
        self.assertTrue(np.allclose(p[:, 1], 0) and (p[:, [0, 2]] >= -1e-6).all() and (p[:, [0, 2]] <= 2 + 1e-6).all())
        self.assertTrue(np.allclose(np.abs(n[:, 1]), 1))
        big = S.ShapeSampler("mesh", mesh_mode="uniform").set_mesh([[0, 0, 0], [1, 0, 0], [0, 0, 1], [10, 0, 0], [10, 0, 10], [0, 0, 10]],
                                                                  [0, 1, 2, 3, 4, 5])
        self.assertGreater(float((big.sample_position(S.RandCtx(1), 400)[:, 0] > 1.5).mean()), 0.9)   # area weighted
        coll = S.ShapeSampler(subs=[(S.ShapeSampler("sphere", radius=1, position=(-10, 0, 0)), 1),
                                    (S.ShapeSampler("sphere", radius=1, position=(10, 0, 0)), 3)])
        x = coll.sample_position(S.RandCtx(4), 800)[:, 0]
        self.assertAlmostEqual(float((x > 0).mean()), 0.75, delta=0.06)

    def test_from_fields_corpus_layouts(self):
        # fx_act1monastery_leaves_30x30: cylinder (11=3) radius 3 height 12, volume (10=3)
        c = S.ShapeSampler.from_fields({10: struct.pack("<i", 3), 11: struct.pack("<i", 3), 13: f32b(3.0), 16: f32b(12.0)})
        self.assertEqual((c.type, c.radius, c.height, c.volume), ("cylinder", 3.0, 12.0, True))
        # fx_nebula: ellipsoid (11=2), radius 200, inner 75.2, hemisphere, euler
        n = S.ShapeSampler.from_fields({11: struct.pack("<i", 2), 13: f32b(200.0), 14: f32b(75.2), 17: b"\x01", 30: f32b(-51.5, -37.8, 180.0)})
        self.assertEqual((n.type, n.inner, n.hemisphere), ("ellipsoid", np.float32(75.2).item(), True))
        # fx_lightningspire_attack: sphere with inherit flags off and an offset
        s = S.ShapeSampler.from_fields({11: struct.pack("<i", 1), 13: f32b(1.28), 27: b"\x00", 28: b"\x00", 29: f32b(0, 8, 0)})
        self.assertTrue(np.linalg.norm(s.sample_position(S.RandCtx(0), 20), axis=1).max() <= 1.28 + 1e-5)   # translate off: centred
        j = S.ShapeSampler.from_json(json.loads(json.dumps(c.to_json())))
        self.assertEqual((j.type, j.radius, j.volume), ("cylinder", 3.0, True))


class RandAndCallTests(unittest.TestCase):
    def test_rand_vrand(self):
        r = S.RandCtx(11)
        x = r.rand(2.0, 5.0, 1000)
        self.assertTrue(x.min() >= 2 and x.max() < 5 and abs(x.mean() - 3.5) < 0.1)
        v = r.rand([0, 10, 20], [1, 11, 22], 400)
        self.assertTrue(((v >= [0, 10, 20]) & (v < [1, 11, 22])).all())
        sph = r.vrand(-2, 2, 500)
        self.assertTrue(np.allclose(np.linalg.norm(sph, axis=1), 2, atol=1e-4))
        ball = r.vrand(0, 2, 2000)
        self.assertTrue(np.linalg.norm(ball, axis=1).max() <= 2 + 1e-5 and np.linalg.norm(ball, axis=1).min() < 1)
        self.assertTrue(np.allclose(np.linalg.norm(r.vrand(n=10), axis=1), 1, atol=1e-5))
        self.assertTrue(np.array_equal(S.RandCtx(4).rand(0, 1, 5), S.RandCtx(4).rand(0, 1, 5)))

    def test_value_noise_range_and_determinism(self):
        p = np.random.default_rng(0).uniform(-30, 30, (500, 3))
        v = S.value_noise(p)
        self.assertEqual(v.shape, (500, 1))
        self.assertTrue(np.abs(v).max() <= 1.0001 and v.std() > 0.1)
        self.assertTrue(np.array_equal(v, S.value_noise(p)))

    def test_engine_calls(self):
        class Ctx:
            count = 6
            time = 0.0
            rand = S.RandCtx(2)

        eng = S.register({})
        for name in ("samplerCurve1:sample_float", "samplerCurve4:sample_float", "samplerCurve1:derive_float_pCtxI",
                     "samplerVectorField:sample_float3_float_SceneCtx", "samplerShape:samplePosition_RandCtx",
                     "rand_float_float_RandCtx", "vrand_RandCtx", "noise_float", "fast.noise_float"):
            self.assertIn(name, eng)
        cv = S.Curve([0, 1], [0, 4])
        self.assertEqual(eng["samplerCurve1:sample_float"](Ctx, cv, np.array([[0.25]]))[0, 0], 1.0)
        self.assertEqual(eng["samplerCurve1:derive_float_pCtxI"](Ctx, cv, [0.5])[0, 0], 4.0)
        pos = eng["samplerShape:samplePosition_RandCtx"](Ctx, S.ShapeSampler("sphere", radius=3))
        self.assertEqual(pos.shape, (6, 3))
        self.assertTrue(np.allclose(np.linalg.norm(pos, axis=1), 3, atol=1e-5))
        fixed = np.full((4, 3), 0.3, dtype=np.float32)
        a = eng["samplerShape:samplePosition_int3"](Ctx, S.ShapeSampler("box"), fixed)
        self.assertTrue(np.array_equal(a[0], a[3]))
        vf = S.TurbulenceSampler(strength=1, wavelength=1, time_scale=1)
        p3 = np.zeros((3, 3), np.float32) + 0.2
        out = eng["samplerVectorField:sample_float3_float_SceneCtx"](Ctx, vf, p3, np.array([[0.0], [0.5], [0.0]]))
        self.assertTrue(np.array_equal(out, vf.sample(p3, Ctx.time)))      # D2R ignores t: the scene time (ctx.time)
        self.assertEqual(eng["rand_float3_float3_RandCtx"](Ctx, [0, 0, 0], [1, 1, 1]).shape, (6, 3))
        self.assertEqual(eng["vrand_float_float_RandCtx"](Ctx, -1.0, 1.0).shape, (6, 3))
        wrapped = type("W", (), {"sampler": cv})()
        self.assertEqual(eng["samplerCurve1:sample_float"](Ctx, wrapped, [0.5])[0, 0], 2.0)

    def test_make_dispatch(self):
        self.assertIsInstance(S.make("curve", {"times": [0, 1], "values": [[0], [1]], "dim": 1}), S.Curve)
        self.assertIsInstance(S.make("shape", {"type": "sphere", "radius": 2}), S.ShapeSampler)
        self.assertIsInstance(S.make("turbulence", {"strength": 1.0}), S.TurbulenceSampler)
        self.assertIsInstance(S.make("curve", {17: f32b(0, 1), 18: f32b(0, 1)}), S.Curve)


# ---- the corpus: every Curve/Shape/Turbulence object of a sample of the game's effects builds and samples ------------

_SCHEMA = {  # field id -> size (negative = array of that element size), from the mod studio schema (MIT)
    "CParticleNodeSamplerData_Curve": {0: -8, 1: -8, 3: 4, 7: 8, 9: 4, 10: 4, 11: 4, 12: 16, 13: 16, 14: 4, 16: 1, 17: -4, 18: -4, 19: -4},
    "CParticleNodeSamplerData_Shape": {0: -8, 1: -8, 3: 4, 6: -4, 7: 8, 9: 4, 10: 4, 11: 4, 12: 12, 13: 4, 14: 4, 16: 4, 17: 1, 18: 12, 19: 4,
                                       20: 12, 27: 1, 28: 1, 29: 12, 30: 12},
    "CParticleNodeSamplerData_Turbulence": {0: -8, 1: -8, 3: 4, 7: 8, 9: 4, 10: 4, 11: 4, 19: 4, 20: 4, 21: 4, 22: 4, 23: 4, 24: 4, 25: 4,
                                            26: 4, 27: 4, 28: 4, 29: 4, 30: 1},
}


def corpus_samplers(path):
    b = path.read_bytes()
    if len(b) < 28:
        return
    _m, _ver, _build, nobj, ncls, so = struct.unpack_from("<6I", b, 0)
    n = struct.unpack_from("<I", b, so)[0]
    p, strs = so + 4, []
    for _ in range(n):
        strs.append(b[p + 1:p + 1 + b[p]].decode("latin-1"))
        p += 1 + b[p]
    p = 28 + 8 * ncls
    while p < so:
        ln, cl = struct.unpack_from("<I", b, p)[0], struct.unpack_from("<I", b, p + 5)[0]
        d, name = b[p + 9:p + 4 + ln], strs[cl]
        p += 4 + ln
        sch = _SCHEMA.get(name)
        if not sch:
            continue
        nf, q, fields = struct.unpack_from("<H", d, 0)[0], 2, {}
        for _ in range(nf):
            fid = struct.unpack_from("<H", d, q)[0]
            q += 2
            sz = sch[fid]
            if sz < 0:
                c = struct.unpack_from("<I", d, q)[0]
                fields[fid] = d[q + 4:q + 4 - sz * c]
                q += 4 - sz * c
            else:
                fields[fid] = d[q:q + sz]
                q += sz
        yield name.rsplit("_", 1)[1].lower(), fields


@unittest.skipUnless(CORPUS.is_dir(), "no extracted D2R .particles root (HDFX_PARTICLES_ROOT)")
class CorpusSmoke(unittest.TestCase):
    def test_samplers_build_and_sample(self):
        files = sorted(CORPUS.rglob("*.particles"))[::15][:40]
        counts = {"curve": 0, "shape": 0, "turbulence": 0}
        invalid = 0
        pos = np.random.default_rng(0).uniform(-4, 4, (7, 3)).astype(np.float32)
        for f in files:
            for kind, fields in corpus_samplers(f):
                s = S.make(kind, fields)
                counts[kind] += 1
                if kind == "curve":
                    out = s.sample(np.linspace(-0.1, 1.1, 9))
                    invalid += not s.valid
                    self.assertEqual(out.shape, (9, s.dim))
                    if s.valid and s.min_limits is None and s.max_limits is None:       # keys are hit exactly
                        uniq = np.r_[True, np.diff(s.times) > 0] & np.r_[np.diff(s.times) > 0, True]    # not at jump keys
                        self.assertTrue(np.allclose(s.sample(s.times)[uniq], s.V[uniq], atol=1e-4), f.name)
                elif kind == "shape":
                    p, n = s.sample(S.RandCtx(0), 8)
                    out = p
                    self.assertTrue(np.all(np.isfinite(n)))
                else:
                    out = s.sample(pos, 0.5)
                self.assertTrue(np.all(np.isfinite(out)), (f.name, kind))
        self.assertTrue(all(counts.values()), counts)
        self.assertLessEqual(invalid, 0.02 * max(counts["curve"], 1))


if __name__ == "__main__":
    unittest.main()
