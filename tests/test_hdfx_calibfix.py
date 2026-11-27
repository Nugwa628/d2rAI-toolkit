"""Lane r3-calibfix: the four game-vs-viewer mismatches of calib4/calib5, each at its root cause.

- Fire Bolt up/down: effect.orientation() of an effect turned 180 degrees (vm.mat_quat; was the identity).
- Frost Nova smoke: a CBVM register read under another type is a bit reinterpretation, bool true = all ones
  (vm.reinterpret; the smoke's `dt * 4 & !isRunning` fade), and a missile's effect is stopped when the missile dies
  (context.Driver.stop_t -> SimContext.running -> Sim.step).
- Cave mana shrine: a WaterMaterial model draws as water, not Standard_Mesh's white (gr2.material_params "water",
  render.standard_material).
No game files needed. Run: python -m unittest tests.test_hdfx_calibfix
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import context as C  # noqa: E402
from hdkit.fx import lang  # noqa: E402
from hdkit.fx import render as R  # noqa: E402
from hdkit.fx import sim as S  # noqa: E402
from hdkit.fx import vm  # noqa: E402
from hdkit.view import gr2  # noqa: E402

FADE = '''#kind 5
#fns "effect.isRunning_pCtxI"
extern ctx ParticleContextI = "ParticleContextI" [3 2 1];
extern float effect.dt = "effect.dt" [4 2 1];
extern float self._8 = "8" [4 0 3];
extern none;
float t0 = min(self._8 + asfloat(asint(effect.dt * 4.0) & asint(!effect.isRunning())), 1.0);
self._8 = t0;
'''


class OrientationTest(unittest.TestCase):
    def _q(self, M):
        return vm._env_q(SimpleNamespace(transform=M))

    def test_yaw_180_turns_forward(self):
        q = self._q(C._rot_y(180.0))
        z = vm.qrot(q, np.array([[0.0, 0.0, 1.0]], np.float32))[0]
        np.testing.assert_allclose(z, [0.0, 0.0, -1.0], atol=1e-5)

    def test_every_yaw_matches_the_matrix(self):
        for deg in np.arange(-180.0, 181.0, 7.5):
            M = C._rot_y(float(deg))
            q = self._q(M)
            for axis in np.eye(3):
                got = vm.qrot(q, axis.reshape(1, 3).astype(np.float32))[0]
                np.testing.assert_allclose(got, M[:3, :3] @ axis, atol=1e-5, err_msg="yaw %s" % deg)

    def test_half_turns_about_other_axes_and_scale(self):
        for R_ in (np.diag([1.0, -1.0, -1.0]), np.diag([-1.0, -1.0, 1.0]), np.diag([-1.0, 1.0, -1.0])):
            M = np.eye(4)
            M[:3, :3] = R_ * 2.5                         # a scaled effect transform
            q = self._q(M)
            for axis in np.eye(3):
                got = vm.qrot(q, axis.reshape(1, 3).astype(np.float32))[0]
                np.testing.assert_allclose(got, R_ @ axis, atol=1e-5)

    def test_firebolt_head_flies_head_first_both_ways(self):
        q90 = np.array([[0.7071068, 0.0, 0.0, 0.7071067]], np.float32)   # MeshFlameHead: effect.orientation() x this
        heads = []
        for deg in (0.0, 180.0):
            q = vm.qmul(self._q(C._rot_y(deg)), q90)
            heads.append(vm.qrot(q, np.array([[0.0, 1.0, 0.0]], np.float32))[0])   # the mesh's +y after the 90 deg
        np.testing.assert_allclose(heads[0], -heads[1], atol=1e-5)


class ReinterpretTest(unittest.TestCase):
    def test_bits(self):
        f = np.array([[0.5], [-2.0]], np.float32)
        np.testing.assert_array_equal(vm.reinterpret(f, "i"), f.view(np.int32))
        np.testing.assert_array_equal(vm.reinterpret(f.view(np.int32), "f"), f)
        np.testing.assert_array_equal(vm.reinterpret(np.array([[True], [False]]), "i"), [[-1], [0]])
        np.testing.assert_array_equal(vm.reinterpret(np.array([[0], [8]], np.int32), "b"), [[False], [True]])
        np.testing.assert_array_equal(vm.reinterpret(f, "f"), f)             # same type: unchanged

    def test_stop_fade_mask(self):
        """Frost Nova's SmokePuff: `self._8 += dt * 4 & !isRunning` grows only once the effect is stopped."""
        s = lang.compile(FADE)
        for running, want in ((True, 0.0), (False, 0.2)):
            env = vm.Env(s, 3, dt=0.05)
            env.running = running
            vm.run(s, 3, env)
            np.testing.assert_allclose(env.streams["8"].ravel(), [want] * 3, atol=1e-7)


class StopTest(unittest.TestCase):
    def test_missile_stops_at_its_end(self):
        d = C.MissileDriver([C.Layer(None)], [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], 20.0, start=1.0)
        self.assertAlmostEqual(d.stop_t, 1.5, places=6)
        self.assertTrue(d.context(1.49).running)
        self.assertFalse(d.context(1.5).running)
        self.assertTrue(C.Driver([C.Layer(None)]).context(99.0).running)     # other drivers never stop

    def test_sim_step_stops_on_context(self):
        sim = SimpleNamespace(running=True, ctx=S.SimContext(running=False), stopped=0)
        sim.stop = lambda: (setattr(sim, "running", False), setattr(sim, "stopped", sim.stopped + 1))
        sim.started, sim.max_dt, sim.t = True, 1.0, 0.0
        sim._frame = lambda h: None
        S.Sim.step(sim, 0.0)
        self.assertFalse(sim.running)
        self.assertEqual(sim.stopped, 1)


class WaterTest(unittest.TestCase):
    class _Ext:
        def __init__(self, d):
            self.f, self.d = list(d), d

        def get(self, k):
            return self.d.get(k)

    def test_material_params_and_draw(self):
        ext = {"technique": "WaterMaterial", "ShallowAlbedo": [0.0, 0.04, 0.25], "DeepAlbedo": [0.07, 0.07, 0.39],
               "ShallowAlpha": 0.8, "DeepAlpha": 1.0, "Roughness": 0.1}
        p = gr2.material_params({"ExtendedData": SimpleNamespace(obj=self._Ext(ext))})
        self.assertIn("water", p)
        mesh = SimpleNamespace(params=p, maps={"NormalTexture": "data/hd/env/texture/global/water/n.texture"})
        mat = R.standard_material(R.Material(family="standard", opaque=True), mesh)
        self.assertEqual(mat.textures[0], "@white")
        self.assertEqual(mat.textures[10], "data/hd/env/texture/global/water/n.texture")
        self.assertFalse(mat.opaque)
        r, g, b, a = mat.tint
        self.assertGreater(b, 3 * r)                    # blue, not white
        self.assertLess(max(r, g, b), 0.5)
        self.assertAlmostEqual(a, 0.9, places=6)
        plain = R.standard_material(R.Material(family="standard"), SimpleNamespace(params={}, maps={}))
        self.assertEqual(plain.tint, (1.0, 1.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
