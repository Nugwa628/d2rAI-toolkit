"""Event children's local frame from the payload XForms elements (lane trailre).

D2R.exe 0xbfdd60 (xform*_masked): when the evaluation context has spawner fields (ctx+0xb8, set only by the event
kick's _SpawnMedium 0xae9250) and the mask goes from a local space (2/3) to a world space (0/1), the matrices come from
the event payload's first XFormsPositions element (builtin kind 1, 0xa76bb0) and first XFormsOrientations element
(kind 2), applied by 0xc019b0 (mask bit 1: v + p, bit 2: q v q*, never a scale); otherwise the EffectID transform."""
import os
import types
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit.fx import routing as rt
from hdkit.fx import sim, vm

F32 = np.float32
ROOT = (os.environ.get("HDFX_D2R_ROOT") or "/nonexistent")
CAST = os.path.join(ROOT, "hd/vfx/personal/rbakewell/lightningcastoverlay.particles")


def _q_axis_angle(axis, ang):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    return np.array([[*(axis * np.sin(ang / 2)), np.cos(ang / 2)]], F32)


class XformPayload(unittest.TestCase):
    pos = np.array([[1, 2, 3], [-4, 5, 6]], F32)

    def test_translate_only(self):                       # mask 17: l2w translation (MainBranch's xform_l2w(0, 17))
        out = vm.xform_payload(17, np.zeros((1, 3), F32), self.pos, None)
        np.testing.assert_array_equal(out, self.pos)

    def test_rotate_then_translate(self):                # mask 19 with an orientation: q v q* + p (0xc019b0 case 3)
        q = np.repeat(_q_axis_angle((0, 1, 0), np.pi / 2), 2, axis=0)
        out = vm.xform_payload(19, np.array([[1, 0, 0]], F32), self.pos, q)
        np.testing.assert_allclose(out, self.pos + np.array([0, 0, -1], F32), atol=1e-6)

    def test_rotation_needs_the_orientation_element(self):   # no kind-2 element: the rotation bit is dropped
        out = vm.xform_payload(19, np.array([[1, 0, 0]], F32), self.pos, None)
        np.testing.assert_allclose(out, self.pos + np.array([1, 0, 0], F32))

    def test_direction_and_no_scale(self):               # mask 18 (direction), 23 (all): scale is never applied
        q = np.repeat(_q_axis_angle((0, 0, 1), np.pi / 2), 2, axis=0)
        np.testing.assert_allclose(vm.xform_payload(18, np.array([[2, 0, 0]], F32), self.pos, q),
                                   [[0, 2, 0], [0, 2, 0]], atol=1e-6)
        np.testing.assert_allclose(vm.xform_payload(23, np.array([[2, 0, 0]], F32), self.pos, q),
                                   self.pos + np.array([0, 2, 0], F32), atol=1e-6)

    def _xf(self, mask, xp):
        env = types.SimpleNamespace(transform=np.diag([1, 1, 1, 1]).astype(F32), prev_transform=None,
                                    birth_frac=None, xform_payload=xp)
        env.transform[:3, 3] = (10, 0, 0)
        ctx = types.SimpleNamespace(env=env, n=2)
        return vm.ENGINE["xform_l2w_f_masked_float3_int_pCtxS"](ctx, np.zeros((1, 3), F32), np.array([[mask]], np.int32))

    def test_only_local_to_world(self):
        np.testing.assert_array_equal(self._xf(17, (self.pos, None)), self.pos)        # 2 -> 0: the payload
        np.testing.assert_array_equal(self._xf(17 | 32, (self.pos, None)), self.pos)   # 2 -> 1: the payload too
        np.testing.assert_array_equal(self._xf(66 - 2 + 1, (self.pos, None)), [[-10, 0, 0]])  # 0 -> 2: the effect
        np.testing.assert_array_equal(self._xf(17, None), [[10, 0, 0]])                # no payload: the effect


class Kick(unittest.TestCase):
    def _kick(self, names, sems, elements):
        es = rt.EventSlot(index=0, name="E", source=0, targets=[1], payload=names, semantics=sems, fired=True)
        me = types.SimpleNamespace(routing=types.SimpleNamespace(event_for=lambda s, e: es), events=[], queue=[], t=0.0)
        sim.Sim.kick(me, 0, "E", np.array([2]), elements)
        return me.queue[0][2]

    def test_first_element_of_each_kind(self):
        p0, p1 = np.array([[1, 2, 3]], F32), np.array([[7, 8, 9]], F32)
        q = np.array([[0, 0, 0, 1]], F32)
        pl = self._kick(["Position", "Other", "Orientation"], [1, 1, 2],
                        {0: (p0, p0, 0), 1: (p1, p1, 0), 2: (q, q, 0)})
        np.testing.assert_array_equal(pl[sim.XPOS], np.repeat(p0, 2, axis=0))
        np.testing.assert_array_equal(pl[sim.XORI], np.repeat(q, 2, axis=0))

    def test_wrong_type_keeps_the_effect(self):          # the first kind-1 element is an int: no lookup succeeds
        pl = self._kick(["Id", "Position"], [1, 1],
                        {0: (np.array([[1, 2, 3]], np.int32),) * 2, 1: (np.ones((1, 3), F32),) * 2})
        self.assertNotIn(sim.XPOS, pl)


@unittest.skipUnless(os.path.isfile(CAST), "no D2R corpus")
class ChargedBoltCastOverlay(unittest.TestCase):
    def test_arcs_follow_the_plasma_points(self):
        """MainBranch (`__e_TrailOut` of @391, payload Position kind 1, Orientation kind 2): a ground ring at
        0.07 s rising to ~7 units by 0.25 s, as in calib2 g7 (before: within +-1 unit of the feet)."""
        s = sim.Sim(CAST, seed=1)
        ys = {}
        for i in range(1, 16):
            s.step(1 / 60)
            if i in (4, 15):
                b = s.state().layers["MainBranch"]
                p = next(ri["Position"] for ri in b.renderer_inputs.values() if "Position" in ri)[:b.count]
                ys[i] = (np.percentile(p[:, 1], 50), np.percentile(np.hypot(p[:, 0], p[:, 2]), 50))
        self.assertLess(ys[4][0], 1.0)
        self.assertGreater(ys[4][1], 3.0)
        self.assertGreater(ys[15][0], 5.0)


if __name__ == "__main__":
    unittest.main()
