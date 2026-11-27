"""hdkit.fx.collide: the game's scene.intersect / intersectExt (D2R.exe 0xbf18d0, 0xbf2230, 0x7e80c0; lane r3-intersect).

No game files needed; the corpus test at the end runs only when the 95032 corpus root is present."""
import json
import os
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.fx import collide as C  # noqa: E402
from hdkit.fx import vm  # noqa: E402

CORPUS = Path((os.environ.get("HDFX_CORPUS") or "/nonexistent"))
DOWN = np.array([[0.0, -1.0, 0.0]])


class Queries(unittest.TestCase):
    def test_filter_index_is_lowest_bit_capped(self):          # 0x7e81b1: bsf, cmovb 6
        self.assertEqual(C.query_index(16), 4)
        self.assertEqual(C.QUERIES[4][0], "effectfloor")
        self.assertEqual(C.query_index(1), 0)
        self.assertEqual(C.query_index(0x30), 4)
        self.assertEqual(C.query_index(1 << 20), 6)
        self.assertEqual(C.query_index(0), C.MASK0_INDEX)
        self.assertEqual(C.QUERIES[C.MASK0_INDEX][0], "all")

    def test_effectfloor_ignores_walls(self):
        w = C.World([C.Box([0, 0.5, 0.5], np.eye(3), [5, 5, 0.1], C.CAT["wall"])])
        o, d = np.array([[0.0, 0.5, 0.0]]), np.array([[0.0, 0.0, 1.0]])
        self.assertTrue(np.isfinite(C.intersect(w, o, d, 2.0, mask=0)[0, 3]))        # all
        self.assertTrue(np.isfinite(C.intersect(w, o, d, 2.0, mask=1)[0, 3]))        # effectenvironment
        self.assertTrue(np.isinf(C.intersect(w, o, d, 2.0, mask=16)[0, 3]))          # effectfloor


class Kernel(unittest.TestCase):
    def setUp(self):
        self.w = C.floor_world(0.0)

    def test_hit_is_normal_and_distance_zero(self):
        out = C.intersect(self.w, [[0, 0.6, 0]], DOWN, 5.0)
        np.testing.assert_array_equal(out, [[0, 1, 0, 0]])

    def test_ray_is_one_unit_whatever_the_length(self):         # end = origin + direction (0x7e8231)
        out = C.intersect(self.w, [[0, 1.5, 0]], DOWN, 100.0)
        np.testing.assert_array_equal(out, [[1, 0, 0, np.inf]])
        out = C.intersect(self.w, [[0, 1.5, 0]], DOWN * 2, 0.1)                       # a longer direction reaches
        np.testing.assert_array_equal(out, [[0, 1, 0, 0]])

    def test_length_only_gates(self):                            # distance 0 < length
        self.assertTrue(np.isinf(C.intersect(self.w, [[0, 0.5, 0]], DOWN, 0.0)[0, 3]))
        self.assertEqual(C.intersect(self.w, [[0, 0.5, 0]], DOWN, 1e-4)[0, 3], 0)

    def test_tiny_direction_is_no_ray(self):                    # |d|^2 <= 1e-12 -> zeros (0xbf5070)
        self.assertTrue(np.isinf(C.intersect(self.w, [[0, 0.001, 0]], [[0, -1e-7, 0]], 1.0)[0, 3]))

    def test_disabled_or_no_world_is_zeros(self):
        np.testing.assert_array_equal(C.intersect(None, [[0, 0.5, 0]], DOWN, 1.0), [[0, 0, 0, 0]])
        np.testing.assert_array_equal(C.intersect(self.w, [[0, 0.5, 0]], DOWN, 1.0, enabled=False), [[0, 0, 0, 0]])

    def test_start_inside_is_no_hit(self):
        self.assertTrue(np.isinf(C.intersect(self.w, [[0, -0.5, 0]], DOWN, 1.0)[0, 3]))

    def test_authored_mode_uses_length_and_distance(self):
        w = C.floor_world(0.0, mode="authored")
        out = C.intersect(w, [[0, 2.0, 0]], DOWN, 5.0)
        np.testing.assert_allclose(out, [[0, 1, 0, 2 - C.SWEEP_RADIUS]], atol=1e-6)

    def test_ext_bits(self):                                     # 0xbf2230 + 0xa39970 defaults
        e = C.intersect_ext(self.w, [[0, 0.5, 0], [0, 5, 0]], DOWN, 5.0)
        f = e.view(np.float32)
        self.assertEqual((f[0, 0], f[0, 1], f[0, 2]), (0.0, 1.0, 0.0))
        self.assertEqual(e[0, 3], -1)
        self.assertEqual((f[1, 0], e[1, 1], f[1, 2], e[1, 3]), (1.0, 0, np.inf, 0))
        w = C.World([C.Box([0, 0, 1], np.eye(3), [5, 5, 0.1], C.CAT["wall"])])     # normal -z: sign bit kept in .z
        e = C.intersect_ext(w, [[0, 0, 0.5]], [[0, 0, 1]], 5.0)
        self.assertEqual(e[0, 2], np.int32(-0x80000000))
        nx, ny = e[0, :2].view(np.float32)
        nz = np.float32(np.sqrt(max(0.0, 1 - nx * nx - ny * ny)))                    # how the scripts rebuild it
        nz = (np.array([nz]).view(np.int32) | (e[0, 2] & np.int32(-0x80000000))).view(np.float32)[0]
        self.assertEqual(nz, -1.0)


class Shapes(unittest.TestCase):
    def test_box_rotated(self):
        R = C._quat_matrix((0, np.sin(np.pi / 8), 0, np.cos(np.pi / 8)))             # 45 degrees about y
        w = C.World([C.Box([0, 0, 2], R, [1, 1, 1], C.CAT["wall"])])
        hit, t, n = w.cast([[0.3, 0, 0]], [[0.3, 0, 4]], radius=0)                   # the face z - x = 2 - sqrt 2
        self.assertTrue(hit[0])
        self.assertAlmostEqual(t[0] * 4, 2 - np.sqrt(2) + 0.3, places=5)
        np.testing.assert_allclose(n[0], [np.sqrt(0.5), 0, -np.sqrt(0.5)], atol=1e-6)

    def test_sphere_and_mesh(self):
        w = C.World([C.Sphere([0, 0, 3], 1.0, C.CAT["monster"])])
        hit, t, n = w.cast([[0, 0, 0]], [[0, 0, 4]], radius=0)
        self.assertAlmostEqual(t[0] * 4, 2.0, places=6)
        np.testing.assert_allclose(n[0], [0, 0, -1])
        m = C.Mesh([[-1, 0, -1], [1, 0, -1], [0, 0, 1]], [[0, 1, 2]], C.CAT["floor"])
        hit, t, n = C.World([m]).cast([[0, 1, 0], [5, 1, 0]], [[0, -1, 0], [5, -1, 0]], radius=0)
        self.assertEqual(list(hit), [True, False])
        np.testing.assert_allclose(n[0], [0, 1, 0])

    def test_body_component(self):
        comp = {"type": "PhysicsBodyDefinitionComponent", "filter": "walltransparent", "fixturedefs": [
            {"shapetype": {"type": "PhysicsBoxDefinition", "center": {"x": 0, "y": 1, "z": 0},
                           "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}, "extents": {"x": 1, "y": 1, "z": 1}}},
            {"shapetype": {"type": "PhysicsFileDefinition", "filename": "x.physics"}}]}
        w = C.World()
        M = np.eye(4)
        M[:3, 3] = [10, 0, 0]
        self.assertEqual(C.bodies(w, comp, M), 1)
        self.assertEqual(w.skipped, {"PhysicsFileDefinition": 1})
        b = w.shapes[0]
        np.testing.assert_allclose(b.c, [10, 1, 0])
        self.assertEqual(b.cat, C.CAT["walltransparent"])

    def test_world_from_preset_adds_default_floor(self):
        doc = {"entities": [{"name": "w", "components": [
            {"type": "TransformDefinitionComponent", "position": {"x": 4, "y": 0, "z": 0}},
            {"type": "PhysicsBodyDefinitionComponent", "filter": "wall", "fixturedefs": [
                {"shapetype": {"type": "PhysicsBoxDefinition", "extents": {"x": 0.5, "y": 3, "z": 3}}}]}]}]}

        class FS:
            def read(self, p):
                return json.dumps(doc).encode()
        w = C.world_from_preset(FS(), "p.json")
        self.assertEqual([s.kind for s in w.shapes], ["box", "floor"])
        out = C.intersect(w, [[3.2, 1, 0], [0, 0.5, 0]], [[1, 0, 0], [0, -1, 0]], 1.0)
        np.testing.assert_allclose(out, [[-1, 0, 0, 0], [0, 1, 0, 0]])


class VmCalls(unittest.TestCase):
    def call(self, name, env, *args, dst="float4"):
        from hdkit.fx import cbvm as cb
        Cx = vm.CallCtx.__new__(vm.CallCtx)
        Cx.env, Cx.n, Cx.name, Cx.ins = env, len(args[0]), name, None
        Cx.dst_type = cb.TYPES.index(dst)
        return vm.ENGINE[name](Cx, *args)

    def test_env_world_is_used(self):
        env = vm.Env(n=2)
        env.collision = C.World([C.Box([0, 0, 1], np.eye(3), [5, 5, 0.1], C.CAT["wall"])])
        o = np.float32([[0, 0.5, 0.5], [0, 0.5, 0.5]])
        d = np.float32([[0, 0, 1], [0, -1, 0]])
        out = self.call("scene.intersect_float3_float3_float_bool_int_SceneCtx", env, o, d, np.float32([[2]]),
                        np.array([[True]]), np.int32([[0]]))
        np.testing.assert_allclose(out, [[0, 0, -1, 0], [1, 0, 0, np.inf]])      # no floor in this world
        env.collision = None                                                         # default: floor at ground_y
        out = self.call("scene.intersect_float3_float3_float_bool_int_SceneCtx", env, o, d, np.float32([[2]]),
                        np.array([[True]]), np.int32([[0]]))
        np.testing.assert_allclose(out[1], [0, 1, 0, 0])
        e = self.call("scene.intersectExt_float3_float3_float_bool_int_SceneCtx", env, o, d, np.float32([[2]]),
                      np.array([[True]]), np.int32([[0]]), dst="int4")
        self.assertEqual(e.dtype, np.int32)
        self.assertEqual(int(e[1, 3]), -1)
        self.assertNotIn("doubtful", vm.NOTES["scene.intersectExt_float3_float3_float_bool_int_SceneCtx"])


@unittest.skipUnless(CORPUS.is_dir(), "no corpus root")
class CorpusEffects(unittest.TestCase):
    def test_bouncing_debris_stays_above_floor(self):
        from hdkit.fx import sim as S
        p = CORPUS / "hd/vfx/particles/character/enemy/barricadedoor1/barricade_door_death.particles"
        if not p.is_file():
            self.skipTest("effect missing")
        M = np.eye(4, dtype=np.float32)
        M[1, 3] = 4.0                                    # the door's height: the spawn box lies above the floor
        s = S.Sim(p.read_bytes(), seed=1, ctx=S.SimContext(transform=M, collision=C.floor_world(0.0)))
        lowest, ys = np.inf, None
        for _ in range(72):                              # 1.2 s at the game's 60 fps
            s.step(1 / 60)
            b = s.state().layers.get("@45")              # the debris layer (scripts 160/161: physics with collisions)
            if b is not None and b.count:
                ys = b.fields["0"][:, 1]
                lowest = min(lowest, float(ys.min()))
        self.assertIsNotNone(ys)
        self.assertGreater(lowest, -0.05)
        self.assertTrue(((ys > 0) & (ys <= 1.05)).all())   # resting within the 1-unit ray above the floor


if __name__ == "__main__":
    unittest.main()
