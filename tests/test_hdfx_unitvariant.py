"""hdkit.view.unitvariant (lane hdfx-r3-unitmat): a unit's colour variant, the KTINT recolour of its model (D2R
ps_models_p) and the ColorVariantIndex its effects get. No game files needed; the swarm checks run when the game's CASC
(or a d2r extract with hd/character) is reachable, the WGSL parity check with HDFX_GPU_TESTS=1."""
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
from hdkit.view import unitvariant as uv  # noqa: E402


def ct(adjust=(0, 0, 0, 1), h0=(0, 0, 0, 0), h1=(0, 0, 0, 0), s0=(0, 0, 0, 0), s1=(0, 0, 0, 0), tint=(0, 0, 0, 0)):
    xyzw = lambda v: dict(zip("xyzw", map(float, v)))   # noqa: E731
    return {"type": "ColorTransform", "colorAdjustment": xyzw(adjust), "hueAdjustment0": xyzw(h0),
            "hueAdjustment1": xyzw(h1), "satAdjustment0": xyzw(s0), "satAdjustment1": xyzw(s1), "colorTint": xyzw(tint)}


def table_doc(n=8, names=("cold", "poison", "level0", "level1", "level2", "level3", "level4", "misc")):
    return {"type": "VariantTransformTable", "entries": [
        {"type": "VariantColorTransform", "name": names[i],
         "transforms": [ct((0.1 * i, 0, 0, 1)), ct((0, 0, -0.1 * i, 1)), ct(tint=(1, 0, 0, i / 8.0))]} for i in range(n)]}


class FakeVfs:
    def __init__(self, files):
        self.files = {k.lower(): v for k, v in files.items()}

    def exists(self, p):
        return p.lower() in self.files

    def read(self, p):
        return json.dumps(self.files[p.lower()]).encode("utf-8")


class Maths(unittest.TestCase):
    rng = np.random.default_rng(5)

    def test_identity(self):
        c = self.rng.random((500, 3))
        self.assertLess(np.abs(uv.apply_one(c, uv.IDENTITY) - c).max(), 1e-9)
        self.assertLess(np.abs(uv.apply(c, self.rng.random((500, 3)), np.tile(uv.IDENTITY, 3)) - c).max(), 1e-9)

    def test_hue_value_saturation_gamma_tint(self):
        red = np.array([[1.0, 0.0, 0.0]])
        f = uv.transform_floats
        np.testing.assert_allclose(uv.apply_one(red, f(ct((1 / 3, 0, 0, 1)))), [[0, 1, 0]], atol=1e-6)      # hue 120 deg
        np.testing.assert_allclose(uv.apply_one(red, f(ct((0, 0, -0.5, 1)))), [[0.5, 0, 0]], atol=1e-6)    # value x 0.5
        np.testing.assert_allclose(uv.apply_one(red, f(ct((0, -1, 0, 1)))), [[1, 1, 1]], atol=1e-6)        # grey = V
        g = np.array([[0.25, 0.25, 0.25]])
        np.testing.assert_allclose(uv.apply_one(g, f(ct((0, 0, 0, 2)))), g ** 2, atol=1e-6)                # gamma
        np.testing.assert_allclose(uv.apply_one(g, f(ct(tint=(0, 1, 0, 0.5)))), [[0.125, 0.25, 0.125]], atol=1e-6)
        out = uv.apply_one(np.array([[0.2, 0.2, 0.2]]), f(ct((0, 0, 9, 1))))                               # clamped
        np.testing.assert_allclose(out, [[1, 1, 1]], atol=1e-6)

    def test_band_saturation(self):
        # red sits in band 0 (hue 0): satAdjustment0.x scales it, satAdjustment0.y (band 60 deg) does not
        c = np.array([[0.8, 0.4, 0.4]])
        f = uv.transform_floats
        a = uv.apply_one(c, f(ct(s0=(-1, 0, 0, 0))))
        np.testing.assert_allclose(a, [[0.8, 0.8, 0.8]], atol=1e-6)
        np.testing.assert_allclose(uv.apply_one(c, f(ct(s0=(0, -1, 0, 0)))), c, atol=1e-6)

    def test_mask_mix(self):
        c = np.array([[0.6, 0.3, 0.1]])
        T = np.concatenate([uv.transform_floats(ct((0, 0, -1, 1))), uv.IDENTITY, uv.IDENTITY])   # R channel: black
        np.testing.assert_allclose(uv.apply(c, [[0, 0, 0]], T), c, atol=1e-9)              # no mask: albedo
        np.testing.assert_allclose(uv.apply(c, [[1, 0, 0]], T), [[0, 0, 0]], atol=1e-9)     # full R: transform 0
        np.testing.assert_allclose(uv.apply(c, [[0.5, 0, 0]], T), c * 0.5, atol=1e-9)
        np.testing.assert_allclose(uv.apply(c, [[0, 0, 0]], T, floor=1.0), c * 2 / 3, atol=1e-9)   # floor: all, / 3
        np.testing.assert_allclose(uv.apply(c, [[1, 1, 0]], T), c * 0.5, atol=1e-9)         # over 1: normalised

    def test_black(self):
        out = uv.apply_one(np.zeros((1, 3)), uv.transform_floats(ct((0.3, 0.5, 0.2, 1))))
        self.assertTrue(np.all(np.isfinite(out)))
        np.testing.assert_allclose(out, 0, atol=1e-9)


class Choice(unittest.TestCase):
    def setUp(self):
        self.doc = table_doc()
        self.vfs = FakeVfs({"data/hd/u/u_variant.json": self.doc,
                            uv.TRANSFORMS_JSON: {"transforms": [ct((0.01 * i, 0, 0, 1)) for i in range(128)]}})
        self.unit = {"entities": [{"components": [{"type": "VariantDefinitionComponent",
                                                   "filename": "data/hd/u/u_variant.json"}]}]}

    def test_table(self):
        t = uv.load_table(self.vfs, self.unit)
        self.assertEqual(t.names[2], "level0")
        self.assertEqual(t.entries[3].shape, (3, 24))
        self.assertAlmostEqual(float(t.entries[3][0][0]), 0.3, places=6)
        self.assertIsNone(uv.load_table(self.vfs, {"entities": []}))

    def test_choose(self):
        t = uv.load_table(self.vfs, self.unit)
        c = uv.choose(t)                                        # default: TransLvl 0 -> level0, entry 2
        self.assertEqual((c.effect_index, c.label), (2, "2 level0"))
        self.assertEqual(uv.choose(t, trans_lvl=1).effect_index, 3)
        self.assertEqual(uv.choose(t, trans_lvl=7).effect_index, 7)       # clamped to the table
        self.assertEqual(uv.choose(t, "misc").effect_index, 7)
        self.assertEqual(uv.choose(t, "4").effect_index, 4)
        self.assertRaises(ValueError, uv.choose, t, "nosuch")
        s = uv.choose(t, shift=108, vfs=self.vfs)
        self.assertEqual((s.effect_index, s.floor), (145, 1.0))
        self.assertAlmostEqual(float(s.floats[24]), 1.07, places=5)      # entry 107 in all three channels
        none = uv.choose(None)
        self.assertTrue(none.identity())
        self.assertIsNone(none.effect_index)
        p = c.pack()
        self.assertEqual(len(p), uv.PACK_FLOATS)
        self.assertEqual(tuple(p[72:74]), (1.0, 0.0))

    def test_effect_attrs(self):
        c = uv.choose(uv.load_table(self.vfs, self.unit), trans_lvl=2)
        self.assertEqual(uv.effect_attrs(c, None), {"ColorVariantIndex": [4, 0, 0, 0]})
        self.assertEqual(uv.effect_attrs(c, {"ColorVariantIndex": [145, 0, 0, 0]})["ColorVariantIndex"][0], 145)
        self.assertIsNone(uv.effect_attrs(None, None))

    def test_monstats(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "monstats.txt").write_text("Id\tBaseId\tTransLvl\nswarm1\tswarm1\t0\nswarm2\tswarm1\t1\nx\tx\t\n",
                                               encoding="latin-1")
            self.assertEqual(uv.monster_trans_lvl(d, "Swarm2"), 1)
            self.assertEqual(uv.monster_trans_lvl(d, "x"), 0)
            self.assertRaises(KeyError, uv.monster_trans_lvl, d, "nope")


class Layout(unittest.TestCase):
    def test_uniform_offset(self):
        """shaders.wgsl struct U puts kt at gpu.KT_AT floats and fits in gpu.UBUF_BYTES."""
        from hdkit.view import gpu
        src = (Path(gpu.__file__).with_name("shaders.wgsl")).read_text(encoding="utf-8")
        body = src[src.index("struct U {"):]
        body = body[:body.index("};")]
        size = {"mat4x4<f32>": 16, "vec4<f32>": 4}
        at = 0
        for line in body.splitlines()[1:]:
            line = line.split("//")[0].strip()
            if not line:
                continue
            name, typ = [x.strip() for x in line.rstrip(",").split(":", 1)]
            if name == "kt":
                self.assertEqual(at, gpu.KT_AT)
            if typ.startswith("array<"):
                inner, n = typ[6:-1].rsplit(",", 1)
                at += size[inner.strip()] * int(n)
            else:
                at += size[typ]
        self.assertLessEqual(at * 4, gpu.UBUF_BYTES)
        self.assertEqual(gpu.KT_AT + uv.PACK_FLOATS, at)


CACHE = Path((os.environ.get("HDFX_CASC_CACHE_ROOT") or "/nonexistent"))


def _casc_fs():
    """The CASC cache folder as a plain root (no CASC open, so no wait while a game runs)."""
    from hdkit.view import vfs
    return vfs.Layered(vfs.parse_roots(["cache=%s" % CACHE]))


@unittest.skipUnless((CACHE / "hd/character/enemy/swarm1/swarm1_variant.json").is_file(), "casc cache absent")
class Swarm(unittest.TestCase):
    def test_level0_matches_capture(self):
        """swarm1 loads entry 2 'level0': the s2 capture's cb7 of its models_p draw (eid 13030)."""
        from hdkit.view import scene
        u = scene.load_unit(_casc_fs(), "hd/character/enemy/swarm1.json")
        self.assertEqual(u.variant.effect_index, 2)
        np.testing.assert_allclose(u.variant.floats[[0, 1, 2, 3, 24, 25, 26, 27, 48, 49, 50, 51]],
                                   [0.34, 0, -0.43, 1, 0, 0, -0.76, 1, 0.37, 0.48, -0.37, 1], atol=1e-6)


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    def test_wgsl_matches_numpy(self):
        """The albedo pass of a flat quad with a variant = unitvariant.apply on the same texel."""
        from hdkit.view import camera, gpu, gr2, scene
        alb8, mask8 = (180, 90, 40, 255), (200, 60, 30, 255)
        tex = lambda px, srgb: scene.Texture("t", 0, [np.full((4, 4, 4), px, np.uint8)], srgb)   # noqa: E731
        cam = camera.OrthoCamera(k=10.0)
        r, up, w = cam.basis()
        P = np.array([-r - up, r - up, r + up, -r + up], float) * 3.0 + np.array([0.0, 5.0, 0.0])   # above the floor clip
        m = gr2.Mesh("q", P, np.tile(w, (4, 1)), np.tile(np.append(r, 1.0), (4, 1)), np.array([[0, 0], [1, 0], [1, 1], [0, 1.0]]),
                     np.array([[0, 1, 2], [0, 2, 3]]), {}, [])
        part = scene.Part("p", "p.model", [m], None, {"AlbedoTexture": tex(alb8, True), "MaskTexture": tex(mask8, False)})
        T = np.concatenate([uv.transform_floats(ct((0.2, 0.3, -0.2, 1.2), h0=(0.1, 0, 0, 0), s0=(0.5, 0, 0, 0))),
                            uv.transform_floats(ct((0, -0.5, 0.1, 1), tint=(0.2, 0.8, 1, 0.7))),
                            uv.transform_floats(ct((0.5, 0, 0, 0.8)))])
        unit = scene.Unit("q", [part], variant=uv.Choice(T.astype(np.float32), 0.0, 2, "test"))
        R = gpu.Renderer(msaa=4)
        prep = R.prepare(unit, cam=cam)
        lin = R.render(prep, cam, (64, 64), (32, 32), pass_name="albedo", light=gpu.Light(), glow=False, linear=True)
        cov = lin[..., 3] > 0.99                                  # the quad's pixels (the texture is one colour)
        self.assertGreater(int(cov.sum()), 50)
        got = np.percentile(lin[cov][:, :3], 50, axis=0)
        srgb = np.array(alb8[:3]) / 255.0
        a_lin = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
        want = uv.apply(a_lin[None], np.array(mask8[:3])[None] / 255.0, T)[0]
        np.testing.assert_allclose(got, want, atol=0.01)
        self.assertGreater(np.abs(want - a_lin).max(), 0.05)     # the variant really changes it


if __name__ == "__main__":
    unittest.main()
