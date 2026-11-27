"""Lane hdfx-r3-decal2: register lanes in the VM (pool growth), the one-pass decal walk (overlap, receivers), timeline
placement (death-clip spawn time and bone). No game files needed; the GPU class runs with HDFX_GPU_TESTS=1."""
import os
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit.fx import decal as dc
from hdkit.fx import decal_pass as dp
from hdkit.fx import timeline_fx as tf
from hdkit.fx import vm

F32, I32 = np.float32, np.int32


class Lanes(unittest.TestCase):
    """A register read under another type without `cvt` keeps its bits (D2R's executor)."""

    def test_float_bits_as_int(self):
        a = vm.as_lanes(np.array([[0.04]], F32), "i")
        self.assertEqual(a.dtype, I32)
        self.assertEqual(int(a[0, 0]), int(np.array([0.04], F32).view(I32)[0]))

    def test_bool_is_all_ones(self):
        self.assertEqual(int(vm.as_lanes(np.array([[True]]), "i")[0, 0]), -1)
        self.assertEqual(int(vm.as_lanes(np.array([[False]]), "i")[0, 0]), 0)

    def test_dt_mask(self):
        """asfloat(asint(effect.dt) & asint(cond)): dt where cond, else 0 (vilechild pool size, bloodlord fade)."""
        dt = vm.as_lanes(np.array([[0.04]], F32), "i")
        for cond, want in ((True, 0.04), (False, 0.0)):
            m = vm.as_lanes(np.array([[cond]]), "i")
            got = vm.as_lanes(dt & m, "f")
            self.assertAlmostEqual(float(got[0, 0]), want, places=6)

    def test_nonzero_bits_are_true(self):
        self.assertTrue(bool(vm.as_lanes(np.array([[-0.0]], F32), "b")[0, 0]))   # sign bit only: true, as the game
        self.assertFalse(bool(vm.as_lanes(np.array([[0]], I32), "b")[0, 0]))

    def test_same_type_untouched(self):
        a = np.array([[1.5, 2.5]], F32)
        self.assertIs(vm.as_lanes(a, "f"), a)


def _rec(flags=dc.ALBEDO | dc.ROUGHNESS, seq=(0, 0)):
    r = dc.pack((0.0, 0.0, 0.0), (2.0, 2.0, 2.0), mat_flags=flags, has_orm=True)
    r.seq = seq
    return r


def _tex(alb, rough=0.1):
    return {"albedo": np.asarray(alb, float), "orm": (1.0, rough, 0.0, 1.0), "nrm": (0.5, 0.5, 0.5, 0.5), "em": 0.0}


class Walk(unittest.TestCase):
    """decal_pass.blend: the game's material walk (view/fx_decals.wgsl follows it)."""

    def test_albedo_split_equals_sequential_lerps(self):
        A = np.array([0.3, 0.5, 0.2])
        layers = [(_rec(), 0.4, _tex((1, 0, 0))), (_rec(), 0.3, _tex((0, 0, 1)))]
        m = dp.blend(layers)
        want = A
        for _r, w, t in layers:
            want = want + w * (t["albedo"] - want)
        np.testing.assert_allclose(A * m["cA"] + m["aD"], want, atol=1e-12)

    def test_material_mixed_once_in_order(self):
        layers = [(_rec(), 0.5, _tex((1, 0, 0), 0.04)), (_rec(), 0.5, _tex((0, 1, 0), 0.5))]
        m = dp.blend(layers, ground_rough=0.9)
        self.assertAlmostEqual(m["rough"], (0.9 + 0.5 * (0.04 - 0.9)) + 0.5 * (0.5 - (0.9 + 0.5 * (0.04 - 0.9))))
        self.assertEqual(m["used"], 2)

    def test_walk_stops_at_summed_weight_one(self):
        layers = [(_rec(), 0.7, _tex((1, 0, 0))), (_rec(), 0.6, _tex((0, 1, 0))), (_rec(), 0.9, _tex((0, 0, 1)))]
        m = dp.blend(layers)
        self.assertEqual(m["used"], 2)               # 0.7 + 0.6 >= 1: the third is never read
        self.assertAlmostEqual(m["cA"], 0.3 * 0.4)

    def test_order_by_seq_one_per_decal(self):
        class D:
            def __init__(self, r):
                self.decal_rec = r
        a, b = _rec(seq=(2, 0)), _rec(seq=(1, 3))
        ds = [D(a), D(a), D(b), D(b), D(None)]
        self.assertEqual([d.decal_rec for d in dp.order(ds)], [b, a])

    def test_zero_size_decal_is_skipped(self):
        inp = {"Decal.DecalSize": np.array([[0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [1.0, 0.0, 1.0]])}
        recs = dc.records({}, inp, 3, np.zeros((3, 3)))
        self.assertEqual([r is None for r in recs], [True, False, True])

    def test_records_buffer_layout(self):
        r = _rec(dc.ALBEDO | dc.NORMAL)
        buf = dp.records_buffer([r, r], [(0, 1, 2, 3), (1, 0, 0, 0)], plane_y=0.5, tol=0.25, receivers=True)
        self.assertEqual(buf.shape, (1 + 2 * dp.ROWS, 4))
        np.testing.assert_allclose(buf[0], (2, 0.5, 0.25, 1))
        np.testing.assert_allclose(buf[1:4], r.world_to_box[:3], atol=1e-6)
        self.assertEqual(int(buf[4, 3]), r.flags)
        np.testing.assert_allclose(buf[7, 2:], (0, 1))
        np.testing.assert_allclose(buf[8, :2], (2, 3))

    def test_shader_entries(self):
        from pathlib import Path
        src = (Path(dp.__file__).parents[1] / "view" / "fx_decals.wgsl").read_text()
        for e in ("vs_decals", "fs_decals_mul", "fs_decals_add", "break"):
            self.assertIn(e, src)


class Timeline(unittest.TestCase):
    def test_event_start_scales_with_rate(self):
        self.assertAlmostEqual(tf.event_start(0.5), 0.5)
        self.assertAlmostEqual(tf.event_start(0.5, rate=2.0), 0.25)       # clip played twice as fast
        self.assertAlmostEqual(tf.event_start(0.5, rate=1.0, t0=0.2), 0.3)

    def test_clip_plays_once_and_holds(self):
        class Clip:
            duration = 1.25
        p = tf.ClipPoses(None, Clip(), rate=0.5)
        self.assertAlmostEqual(p.clip_time(1.0), 0.5)
        self.assertAlmostEqual(p.clip_time(10.0), 1.25)                  # the corpse pose
        self.assertAlmostEqual(p.clip_time(-1.0), 0.0)
        self.assertFalse(p.pose_kw["loop"])

    def test_root_event_follows_unit_frame(self):
        """A `root` event is the unit's own frame: the driver's age-0 transform is the effect place x root scale."""
        from hdkit.fx import context as C
        b = C.Binding("vfx", entity="death:x", file="a.particles", bone="root", xf=np.eye(4), trigger="timeline",
                      mode="dt", attach=True, time=0.5, extra={"attach_to_dead_body": True})
        te = tf.TimelineEffect(b, tf.event_start(b.time), None, None, True, 1.05)
        d = te.driver()
        M = d.layer_transform(0.0, d.layers[0])
        np.testing.assert_allclose(M[:3, :3], np.eye(3) * 1.05)
        np.testing.assert_allclose(M[:3, 3], 0.0)
        self.assertAlmostEqual(te.start, 0.5)


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    """The decal pass over the depth-only ground: draws on the ground, not on a raised surface (a unit)."""

    @classmethod
    def setUpClass(cls):
        from hdkit.fx import render as fr
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        cls.fr = fr
        cls.R = gpu.Renderer(msaa=4)
        cls.fxr = fr.FxRenderer(cls.R, NoVfs())

    def render(self, top=None, decals=True):
        from hdkit.fx import rspec, sim
        from hdkit.view import camera, gpu
        from hdkit.view.fxcli import ground_draw
        props = {"Decal": True, "Decal.DiffuseMap": "x.texture", "Decal.ApplyToAlbedo": True}
        e = rspec.RenderEffect("t", [rspec.RSpec(0, 0, "decal", "vfx/materials/Diablo_Decal.pkma", True, "L", props, {})])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(2, {}, {0: {"Position": np.array([[0, 0.0, 0], [0.5, 0.0, 0]]),
                                                                   "Decal.DecalSize": np.array([[4.0, 2.0, 4.0]] * 2)}})})
        cam = camera.OrthoCamera(k=10.0)
        draws = self.fxr.prepare(e, st, cam)
        base = gpu.Prepared([ground_draw(self.R, "#000000", 2.0, top)]) if top is not None else None
        lin = self.fxr.render(draws if decals else [], cam, (128, 128), base, None, self.fr.Post(bare_alpha=True), (64, 64), 1, None,
                              linear=True, floor=0.0)
        return draws, lin

    def test_one_pass_on_ground(self):
        draws, lin = self.render()
        self.assertEqual([d.entry for d in dp.group(self.fxr, draws)[:2]], ["fs_decals_mul", "fs_decals_add"])
        self.assertGreater(float(lin[64, 64, :3].max()), 0.01)

    def test_raised_surface_is_not_a_receiver(self):
        _d, control = self.render(top=0.6, decals=False)  # the raised quad has its own specular light
        _d, lin = self.render(top=0.6)
        np.testing.assert_allclose(lin[64, 64, :3], control[64, 64, :3], atol=1e-3)
        dp.RECEIVERS = False
        try:
            _d, lin = self.render(top=0.6)
            self.assertGreater(float((lin[64, 64, :3] - control[64, 64, :3]).max()), 0.01)
        finally:
            dp.RECEIVERS = True
