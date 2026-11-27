"""Lane mats: the other D2R materials (docs/hdfx-shaders.md 8-12). Material classes and keywords (fx/shad.py), the
packers' liquid inputs and normal bending, Material.of for Smoke / lit Uber / Liquid / default mesh / standard / stencil,
the uniforms block, the coverage sets, and (GPU gate free) one render per new fragment entry. No game files needed."""
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import coverage as cv, packers as pk, render as fr, rspec, shad, sim  # noqa: E402
from hdkit.view import camera  # noqa: E402

M = "vfx/materials/%s.pkma"


def spec(kind, material, props, inputs=None):
    ins = {k: rspec.Input(k, None, "float", v) for k, v in (inputs or {}).items()}
    return rspec.RSpec(0, 0, kind, material, True, "L", props, ins)


class Families(unittest.TestCase):
    def test_d2r_name_rules(self):
        f = shad.material_family
        self.assertEqual(f(M % "Diablo_Uber_Ribbon", "ribbon"), "uber")
        self.assertEqual(f(M % "Diablo_Smoke_Mesh", "mesh"), "smoke")
        self.assertEqual(f(M % "Diablo_Liquid_Mesh", "mesh"), "liquid")
        self.assertEqual(f(M % "Diablo_Default_Mesh", "mesh"), "dmesh")
        self.assertEqual(f(M % "Diablo_Standard_Mesh", "mesh"), "standard")
        self.assertEqual(f(M % "Diablo_Stencil_Mesh", "mesh"), "stencil")
        self.assertEqual(f("vfx/Library/PopcornFXCore/Materials/Default_Ribbon.pkma", "ribbon"), "default")
        self.assertEqual(f(M % "Diablo_Decal", "decal"), "decal")
        self.assertIsNone(f("vfx/Library/PopcornFXCore/Materials/Distortion_Billboard.pkma", "billboard"))
        self.assertEqual(shad.containers("smoke", "billboard"), ("ps_popcorn_uber_billboard_p", "vs_popcorn_uber_billboard_v"))
        self.assertEqual(shad.containers("liquid", "mesh"), ("ps_popcorn_liquid_mesh_p", "vs_popcorn_liquid_v"))
        self.assertIsNone(shad.containers("standard", "mesh"))

    def test_liquid_keywords(self):
        p = {"Transparent": True, "Transparent.Type": 2, "UVDistortion": True, "AlphaRemap": True,
             "LiquidColorMap": True, "CorrectDeformation": False}
        kw = shad.material_keywords(p.get, "billboard", M % "Diablo_Liquid", inputs={"AlphaRemap.AlphaCursor"})
        self.assertEqual((kw["UV_DISTORTION"], kw["ALPHA_REMAP"], kw["LIQUID_COLOR_MAP"], kw["POPCORN_ADDITIVE"],
                          kw["MESH"]), ("1", "1", "1", "0", "0"))
        kw = shad.material_keywords(p.get, "billboard", M % "Diablo_Liquid", inputs=set())
        self.assertEqual(kw["ALPHA_REMAP"], "0")          # no AlphaCursor input bound

    def test_default_mesh_keywords(self):
        p = {"Transparency": True, "Transparency.Type": 4, "Lit": True}
        kw = shad.material_keywords(p.get, "mesh", M % "Diablo_Default_Mesh")
        self.assertEqual((kw["POPCORN_LIGHTING"], kw["ALPHA_BLEND"], kw["POPCORN_ADDITIVE"]), ("1", "0", "0"))
        p = {"Transparency": True, "Transparency.Type": 0, "Fresnel": True}
        kw = shad.material_keywords(p.get, "mesh", M % "Diablo_Default_Mesh")
        self.assertEqual((kw["POPCORN_LIGHTING"], kw["ALPHA_BLEND"], kw["POPCORN_ADDITIVE"], kw["FRESNEL"]),
                         ("0", "1", "1", "1"))

    def test_smoke_uses_uber_keywords(self):
        p = {"Transparent": True, "Transparent.Type": 2, "FastLit": True, "Blend": True, "Blend.TextureBlend": "x"}
        kw = shad.material_keywords(p.get, "billboard", M % "Diablo_Smoke")
        self.assertEqual((kw["POPCORN_LIGHTING"], kw["POPCORN_BLEND"]), ("2", "1"))


class Packers(unittest.TestCase):
    def test_liquid_inputs_and_family_defaults(self):
        b = pk.particle_block({"Liquid.AlphaThreshold": np.array([[0.3]]), "Liquid.CenterColor": np.array([[1, 0, 0]]),
                               "Liquid.Roughness": np.array([[0.7]])}, 1, "liquid")
        o = lambda k: pk.OFF[k][0] - 10                    # noqa: E731
        self.assertAlmostEqual(float(b[0, o("so_blend")]), 0.3, places=6)       # AlphaThreshold
        self.assertEqual(float(b[0, o("so_blend") + 1]), 1.0)                  # LiquidHardness default
        self.assertEqual(list(b[0, o("color"):o("color") + 4]), [1, 0, 0, 1])
        self.assertAlmostEqual(float(b[0, o("so_alpha")]), 0.7, places=6)
        self.assertEqual(list(b[0, o("so_db"):o("so_db") + 3]), [1, 1, 1])     # EdgeColor default white (calib5: so_db)
        b = pk.particle_block({}, 1)
        self.assertEqual(list(b[0, o("so_x"):o("so_x") + 4]), [1, 1, 0, 0])     # dissolve UV: identity

    def test_bend_normals(self):
        basis = camera.OrthoCamera().basis()
        P, N = pk.billboard_corners(0, np.zeros((1, 3)), np.ones((1, 2)), None, basis)
        B = pk.bend_normals(P, N, np.zeros((1, 3)), 0.5)
        self.assertTrue(np.allclose(np.linalg.norm(B, axis=-1), 1.0))
        d = P[0, 0] / np.linalg.norm(P[0, 0])
        self.assertGreater(float(B[0, 0] @ d), float(N[0, 0] @ d))           # tilted towards its corner
        self.assertTrue(np.allclose(pk.bend_normals(P, N, np.zeros((1, 3)), 0.0), N))


class Materials(unittest.TestCase):
    def test_smoke_flag_and_lit(self):
        m = fr.Material.of(spec("billboard", M % "Diablo_Smoke", {
            "Transparent": True, "Transparent.Type": 2, "FastLit": True, "NormalWrap": True,
            "NormalWrap.WrapFactor": 0.5, "NormalBend": True, "NormalBend.NormalBendingFactor": 0.5}))
        self.assertEqual((m.family, m.inv[2], m.lit, m.entry, m.default), ("smoke", 1, 1, "fs_lit", False))
        self.assertEqual((m.wrap, m.bend), (0.5, 0.5))

    def test_unlit_uber_unchanged(self):
        m = fr.Material.of(spec("billboard", M % "Diablo_Uber", {"Transparent": True, "Transparent.Type": 0}))
        self.assertEqual((m.family, m.lit, m.entry, m.inv[2]), ("uber", 0, "", 0))

    def test_liquid(self):
        m = fr.Material.of(spec("billboard", M % "Diablo_Liquid", {
            "Transparent": True, "Transparent.Type": 2, "Liquid": True, "Liquid.LiquidBase": "b.texture",
            "Liquid.NormalMap": "n.texture", "Liquid.LiquidBaseOp": 2, "Liquid.HeadlightContribution":
            (1065353216, 990057071, 0, 0)}))
        self.assertEqual((m.family, m.lit, m.entry, m.blend, m.default), ("liquid", 2, "fs_liquid", 3, False))
        self.assertEqual((m.textures[0], m.textures[10]), ("b.texture", "n.texture"))
        self.assertEqual(m.liquid[0], 2)
        self.assertAlmostEqual(m.liquid[6], 1.0)
        self.assertAlmostEqual(m.liquid[7], 0.002, places=6)

    def test_default_mesh_opaque_and_standard(self):
        m = fr.Material.of(spec("mesh", M % "Diablo_Default_Mesh", {
            "Transparency": True, "Transparency.Type": 4, "Lit": True, "Diffuse.DiffuseMap": "d.texture"}))
        self.assertEqual((m.family, m.lit, m.entry, m.opaque, m.textures[2]), ("dmesh", 3, "fs_dmesh", True, "@orm"))
        self.assertTrue(m.srgb(0))
        m = fr.Material.of(spec("mesh", M % "Diablo_Default_Mesh", {"Transparency": True, "Transparency.Type": 0}))
        self.assertEqual((m.lit, m.opaque, m.blend), (4, False, 1))

        class Mesh:
            maps = {"AlbedoTexture": "a.texture", "ORMTexture": "o.texture", "NormalTexture": "n.texture"}
        s = fr.Material.of(spec("mesh", M % "Diablo_Standard_Mesh", {"Transparency": True, "Transparency.Type": 0}))
        t = fr.standard_material(s, Mesh())
        self.assertEqual((t.lit, t.opaque, t.textures[0], t.textures[2], t.textures[10]),
                         (5, True, "a.texture", "o.texture", "n.texture"))

    def test_stencil_first(self):
        m = fr.Material.of(spec("billboard", M % "Diablo_Stencil", {"AlphaThreshold": True, "AlphaThreshold.Invert": True,
                                                                     "AlphaThreshold.Mask": "m.texture"}))
        self.assertEqual((m.family, m.entry, m.liquid[2], m.textures[0]), ("stencil", "fs_stencil", 1, "m.texture"))
        self.assertLess(m.sort_offset, -1e8)

    def test_headlight_bits(self):
        self.assertEqual(fr.headlight((1073741824, 990057071, 0, 0))[0], 2.0)
        self.assertEqual(fr.headlight((0.5, 0.1)), (0.5, 0.1))

    def test_uniform_block(self):
        from hdkit.view import gpu
        m = fr.Material.of(spec("billboard", M % "Diablo_Liquid", {"Transparent": True, "Transparent.Type": 2,
                                                                    "NormalWrap": True, "NormalWrap.WrapFactor": 0.25}))
        d = fr.FxDraw("billboard", 0, "L", m, np.zeros((6, pk.FLOATS), np.float32))
        L = gpu.Light(points=({"pos": [0, 1, 0], "radius": 5.0, "color": [1, 0.5, 0], "atten": 2.0},))
        u = fr.mats_uniforms(d, L, fr.Post(gain=2.0))
        self.assertEqual(len(u), fr.MATS_FLOATS)
        self.assertEqual(tuple(u[0:4]), (2.0, 0.25, 2.0, 0.0))
        self.assertAlmostEqual(float(np.linalg.norm(u[4:7])), 1.0, places=5)
        self.assertEqual(u[28], 1)
        self.assertEqual(list(u[32:40]), [0, 1, 0, 5, 1, 0.5, 0, 2])


class Coverage(unittest.TestCase):
    def test_render_sets_filled(self):
        self.assertIn("mesh", cv.SUPPORTED_RENDER_KINDS)
        self.assertIn(M % "Diablo_Liquid", cv.SUPPORTED_MATERIALS)
        self.assertIn(M % "Diablo_Smoke_Mesh", cv.SUPPORTED_MATERIALS)
        self.assertIn("FastLit", cv.SUPPORTED_RENDER_FEATURES)
        self.assertIn("ColorVariant", cv.SUPPORTED_RENDER_FEATURES)          # lane r3-colorvariant
        # Distortion_Billboard: D2R makes no material for it (not drawn), supported as such by fx/gamekinds.py (r3-misc)
        self.assertIn("vfx/Library/PopcornFXCore/Materials/Distortion_Billboard.pkma", cv.SUPPORTED_MATERIALS)


def _gpu_free() -> bool:
    try:
        from hdkit import gpugate
        return bool(gpugate.check().free)
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_gpu_free(), "GPU gate busy or no adapter")
class Gpu(unittest.TestCase):
    """One draw per new entry point over a ground quad: each must compile and put something on screen."""

    @classmethod
    def setUpClass(cls):
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        cls.fxr = fr.FxRenderer(gpu.Renderer(msaa=4), NoVfs())

    def draw(self, material, props, kind="billboard"):
        e = rspec.RenderEffect("t", [spec(kind, M % material, props)])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {"Position": np.array([[0, 2.0, 0]]),
                                                                    "Size": np.array([[2.0, 2.0]])}})})
        cam = camera.OrthoCamera(k=10.0)
        draws = self.fxr.prepare(e, st, cam)
        lin = self.fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 96), 1, None,
                              linear=True)
        return draws, lin

    def test_liquid_and_lit_smoke_draw(self):
        d, lin = self.draw("Diablo_Liquid", {"Transparent": True, "Transparent.Type": 2})
        self.assertEqual(d[0].entry, "fs_liquid")
        self.assertGreater(float(lin[..., 3].max()), 0.1)
        d, lin = self.draw("Diablo_Smoke", {"Transparent": True, "Transparent.Type": 2, "FastLit": True})
        self.assertEqual(d[0].entry, "fs_lit")
        self.assertGreater(float(lin[..., :3].max()), 0.01)

    def test_stencil_draws_nothing_visible(self):
        d, lin = self.draw("Diablo_Stencil", {"AlphaThreshold": True})
        self.assertEqual(d[0].entry, "fs_stencil")
        self.assertLess(float(lin[..., :3].max()), 1e-3)


if __name__ == "__main__":
    unittest.main()
