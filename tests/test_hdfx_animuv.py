"""Lane r3-animuv: skinned FX meshes (Animation), ribbon UV flags (TextureUVs.RotateTexture, TextureRepeat) and decal
Emissive. No game files needed; the corpus checks skip when the root is absent."""
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

from hdkit.fx import animmesh, emissive, packers as pk, render  # noqa: E402

CORPUS = Path((os.environ.get("HDFX_CORPUS") or "/nonexistent"))


class Spec(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


class AnimTime(unittest.TestCase):
    def test_spec(self):
        r = Spec({"Animation": True, "Animation.AnimationFile": "data/hd/vfx/meshes/x/animation/Walk.animation",
                  "Animation.Skeleton": "data/hd/vfx/meshes/x/skeleton/x.skeleton", "Animation.Speed": 1.5,
                  "Animation.LoopCount": 2})
        s = animmesh.spec_of(r)
        self.assertEqual(s.combined, "data/hd/vfx/meshes/x/animation/combined.animations")
        self.assertEqual(s.clip_name, "Walk")
        self.assertEqual((s.speed, s.loops), (1.5, 2))
        self.assertIsNone(animmesh.spec_of(Spec({"Animation": False})))
        self.assertIsNone(animmesh.spec_of(Spec({"Animation": True})))

    def test_clip_time(self):
        age = np.array([0.0, 0.5, 1.5, 2.5, 10.0])
        t = animmesh.clip_time(age, 1.0, speed=1.0, loops=2)       # two plays, then the last frame holds
        np.testing.assert_allclose(t, [0.0, 0.5, 0.5, 1.0, 1.0])
        t = animmesh.clip_time(age, 1.0, speed=2.0, loops=0)       # forever, twice as fast
        np.testing.assert_allclose(t, [0.0, 0.0, 0.0, 0.0, 0.0])
        t = animmesh.clip_time([0.3], 1.0, speed=1.0, loops=0)
        np.testing.assert_allclose(t, [0.3])

    def test_age_and_groups(self):
        f = {"self.lifeRatio": np.array([[0.5], [0.25]]), "self.invLife": np.array([[0.5], [0.0]])}
        np.testing.assert_allclose(animmesh.particle_age(f, 2), [1.0, 0.0])
        g = animmesh.time_groups([0.0, 0.0001, 0.5])
        self.assertEqual(len(g), 2)
        self.assertEqual(list(g[0][1]), [0, 1])


class RibbonUV(unittest.TestCase):
    # PopcornFX-Web RIB_ROWS (corners [a+, a-, b+, b-] of one segment, t 0 -> 1, side + = 0)
    ROWS = {(0, 0, 0): [(0, 0), (0, 1), (1, 0), (1, 1)], (1, 0, 0): [(1, 0), (1, 1), (0, 0), (0, 1)],
            (0, 1, 0): [(0, 1), (0, 0), (1, 1), (1, 0)], (1, 1, 0): [(1, 1), (1, 0), (0, 1), (0, 0)],
            (0, 0, 1): [(0, 1), (1, 1), (0, 0), (1, 0)], (1, 0, 1): [(0, 0), (1, 0), (0, 1), (1, 1)],
            (0, 1, 1): [(1, 1), (0, 1), (1, 0), (0, 0)], (1, 1, 1): [(1, 0), (0, 0), (1, 1), (0, 1)]}

    def test_flags_match_popcornfx_rows(self):
        RC = [[0, 1], [1, 1], [1, 0], [0, 0]]
        rows = [[3, 0, 2, 1], [2, 1, 3, 0], [0, 3, 1, 2], [1, 2, 0, 3], [0, 1, 3, 2], [3, 2, 0, 1], [1, 0, 2, 3], [2, 3, 1, 0]]
        for (fu, fv, rot), _ in self.ROWS.items():
            want = [tuple(RC[j]) for j in rows[fu + 2 * fv + 4 * rot]]
            got = [pk.ribbon_uv(t, s, bool(fu), bool(fv), bool(rot)) for t, s in ((0, 0), (0, 1), (1, 0), (1, 1))]
            self.assertEqual([tuple(map(float, g)) for g in got], [tuple(map(float, w)) for w in want], (fu, fv, rot))

    def test_repeat_keeps_u(self):
        self.assertEqual(pk.ribbon_uv(3.25, 0.0), (3.25, 0.0))          # not normalised: the wrap sampler repeats it
        self.assertEqual(pk.ribbon_uv(3.25, 1.0, rotate=True), (1.0, -2.25))

    def test_pack_rotated(self):
        pos = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], float)
        basis = (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
        pb = np.zeros((1, pk.PARTICLE_FLOATS), np.float32)
        a = pk.pack_ribbon(pos, 0.5, basis, pb, u=np.array([0.0, 1.0, 2.0]))
        b = pk.pack_ribbon(pos, 0.5, basis, pb, u=np.array([0.0, 1.0, 2.0]), rotate=True)
        uv = pk.OFF["uv"][0]
        np.testing.assert_allclose(b[:, uv], a[:, uv + 1])              # u <- side
        np.testing.assert_allclose(b[:, uv + 1], 1.0 - a[:, uv])        # v <- 1 - t
        self.assertEqual(a[:, uv].max(), 2.0)


class Emissive(unittest.TestCase):
    def test_encoding(self):
        b0, b1 = emissive.encode([1.0, 100.0, 1e4, 1e6, 1e9], [0.0, 1.0, 4.0, 8.0, 20.0])
        np.testing.assert_array_equal(b0, [0, 0, 127, 255, 255])
        np.testing.assert_array_equal(b1, [0, 31, 127, 255, 255])
        I, ex = emissive.decode(b0, b1)
        np.testing.assert_allclose(I[[0, 3]], [100.0, 1e6], rtol=1e-5)
        np.testing.assert_allclose(ex[3], 64.0, rtol=1e-3)

    def test_params_gate(self):
        on = Spec({"Emissive": True, "Decal.EmissiveMap": "data/x_E.texture"})
        p = emissive.params(on, [1.0, 0.0], [0.0, 0.0])
        np.testing.assert_allclose(p[:, 2], [1.0, 0.0])                # Intensity <= 0: off
        np.testing.assert_allclose(p[0, 0], 100.0, rtol=1e-6)
        nomap = Spec({"Emissive": True})
        self.assertEqual(emissive.params(nomap, [5.0], [0.0])[0, 2], 0.0)

    def test_slot(self):
        # integration r3: the decal pass binds the map (fx/decal.TEX_SLOTS) and decal.pack uses this module's bytes
        from hdkit.fx import decal
        self.assertIn("Decal.EmissiveMap", decal.TEX_SLOTS.values())
        r = decal.pack((0, 0, 0), (1, 1, 1), em_intensity=1000.0, em_focus=2.0, mat_flags=decal.EMISSIVE)
        self.assertEqual((r.em, r.focus), tuple(int(b) for b in emissive.encode(1000.0, 2.0)))
        self.assertAlmostEqual(r.em_intensity(), float(emissive.decode(r.em, r.focus)[0]), places=3)


class Supported(unittest.TestCase):
    def test_features(self):
        for f in ("Animation", "TextureRepeat", "TextureUVs.RotateTexture", "Emissive"):
            self.assertIn(f, render.SUPPORTED_FEATURES)


@unittest.skipUnless((CORPUS / "hd/vfx/meshes/lit_mesh/shrine_experience").is_dir(), "corpus absent")
class Corpus(unittest.TestCase):
    def test_shrine_scroll_moves(self):
        from hdkit.fx import rspec
        from hdkit.view import scene, vfs as V
        fs = V.Layered(V.parse_roots(["d2r=%s" % CORPUS]))
        eff = rspec.load(fs.read("hd/vfx/particles/overlays/common/shrine_experience/vfx_shrine_experience_looping.particles"))
        r = next(r for r in eff.renderers if animmesh.spec_of(r))
        sp = animmesh.spec_of(r)
        part = scene.load_model_part(fs, r.get("General.Mesh"), "fx")
        a = animmesh.Animator(fs)
        d = a.duration(sp, part.skeleton)
        self.assertGreater(d, 0.5)
        p0 = a.skinned(part.meshes, part.skeleton, sp, 0.0)[0][0]
        pm = a.skinned(part.meshes, part.skeleton, sp, d / 2)[0][0]
        self.assertLess(np.abs(p0 - part.meshes[0].pos).max(), 0.01)    # the clip starts at the bind pose
        self.assertGreater(np.abs(pm - p0).max(), 0.05)                 # and moves


if __name__ == "__main__":
    unittest.main()
