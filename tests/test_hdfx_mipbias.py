"""fx/mipbias.py: D2R's material mip bias (cb4[6].y) from General.MipBias, General.DrawResolution and the game settings.
No game files needed; the corpus check is skipped when the build-95032 root is absent."""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.fx import mipbias as mb, rspec  # noqa: E402
from hdkit.fx.render import Material, SUPPORTED_FEATURES  # noqa: E402

ROOT = Path((os.environ.get("HDFX_ROOT") or "/nonexistent"))
WGSL = Path(__file__).resolve().parents[1] / "hdkit" / "view" / "fx.wgsl"

# (draw resolution, cb4[6].y) of the 50 captured effect draws (visual profile: Transparency Quality 4, Anti Aliasing 3,
# General.MipBias 0): calib r3 27583-27821, calib2 g4/g5/g7, calib3 g2 18518, calib4 g3, calib5 s3, polish rd3
CAPTURED = {0: -0.5, 1: -0.5, 2: -1.5}


def _r(dr=None, mip=0.0, emb=True, kind="billboard", mat="vfx/materials/Diablo_Uber.pkma"):
    props = {"Transparent": True, "EnableMipBiasing": emb, "General.MipBias": mip, "Blend": True,
             "Blend.TextureBase": "a.texture"}
    inputs = {} if dr is None else {mb.DRAW_RESOLUTION: rspec.Input(mb.DRAW_RESOLUTION, 3, "int", dr)}
    return rspec.RSpec(0, 0, kind, mat, True, "L", props, inputs)


class TestFormula(unittest.TestCase):
    def test_captured_draws(self):
        q = mb.Quality(4, 3)
        for dr, want in CAPTURED.items():
            self.assertEqual(mb.material_bias(0.0, dr, q), want, dr)

    def test_settings(self):
        # k = clamp(DR - TQ + 3, 0, 2); -0.5 only with temporal AA (SMAA T2x and up: 3, 4, 5)
        self.assertEqual(mb.material_bias(0.0, 0, mb.Quality(0, 0)), -2.0)       # the slots' "test" profile
        self.assertEqual(mb.material_bias(0.0, 2, mb.Quality(5, 0)), 0.0)
        self.assertEqual(mb.material_bias(0.0, 2, mb.Quality(3, 2)), -2.0)
        self.assertEqual(mb.material_bias(0.0, 1, mb.Quality(3, 5)), -1.5)
        self.assertEqual(mb.downscale(2, mb.Quality(4, 3)), 1)
        self.assertEqual(mb.material_bias(-16.0, 0, mb.Quality(4, 3)), -16.5)    # General.MipBias adds straight in
        self.assertEqual(mb.material_bias(0.5068398, 1, mb.Quality(4, 0)), 0.5068398)

    def test_sample_biases(self):
        q = mb.Quality(4, 3, global_bias=0.25)
        self.assertEqual(mb.sample_biases(0.0, 2, q), (0.25 - 1.5, 0.25 - 0.5, 0.25))

    def test_quality_sources(self):
        self.assertEqual(mb.Quality.of({"Transparency Quality": 0, "Anti Aliasing": 0}), mb.Quality(0, 0))
        self.assertEqual(mb.Quality.of((2, 4)), mb.Quality(2, 4))
        with mock.patch.dict(os.environ, {"HDFX_TRANSPARENCY_QUALITY": "1", "HDFX_ANTI_ALIASING": "0"}):
            self.assertEqual(mb.Quality.of(None), mb.Quality(1, 0))
        with mock.patch.dict(os.environ, {"HDFX_TRANSPARENCY_QUALITY": "", "HDFX_ANTI_ALIASING": "x"}):
            self.assertEqual(mb.Quality.of(None), mb.Quality(4, 3))

    def test_draw_resolution_input(self):
        self.assertEqual(mb.draw_resolution(_r((2,))), 2)
        self.assertEqual(mb.draw_resolution(_r(None)), 0)
        notes = []
        r = _r(None)
        r.inputs[mb.DRAW_RESOLUTION] = rspec.Input(mb.DRAW_RESOLUTION, 5, "int", None)      # stream-fed (hurricane)
        self.assertEqual(mb.draw_resolution(r, notes), 0)
        self.assertEqual(mb.draw_resolution(_r((7,)), notes), 0)
        self.assertEqual(len(notes), 2)


class TestMaterial(unittest.TestCase):
    def test_material_reads_inputs(self):
        with mock.patch.dict(os.environ, {"HDFX_TRANSPARENCY_QUALITY": "", "HDFX_ANTI_ALIASING": ""}):
            m = Material.of(_r((2,)))
            self.assertEqual((m.draw_resolution, m.mip_bias), (2, -1.5))
            self.assertEqual(Material.of(_r((1,), emb=False)).mip_bias, -0.5)    # switch off: the game biases anyway
            self.assertEqual(Material.of(_r((0,), mip=-16.0)).mip_bias, -16.5)
            for mat in ("vfx/materials/Diablo_Liquid.pkma", "vfx/materials/Diablo_Default_Mesh.pkma",
                        "vfx/materials/Diablo_Stencil.pkma", "vfx/Library/PopcornFXCore/Materials/Default_Billboard.pkma"):
                self.assertEqual(Material.of(_r((2,), mat=mat)).mip_bias, -1.5, mat)

    def test_supported(self):
        self.assertIn("EnableMipBiasing", SUPPORTED_FEATURES)

    def test_wgsl_bias_per_family(self):
        s = WGSL.read_text(encoding="utf-8")
        # Uber: distortion and gradient remap unbiased (game t14/t15/t17), the rest by pad0.x
        self.assertIn("textureSample(t6, s_rep, uvsf(uv, i.so_db))", s)
        self.assertIn("textureSample(t9, s_clamp", s)
        self.assertRegex(s, r"textureSampleBias\(t4, s_clamp, vec2<f32>\(1\.0 - f, i\.misc\.z\), u\.pad0\.x\)")
        # liquid / default mesh normal maps at the fixed -0.5 (pad0.y), the ORM map unbiased; + the water's flow normal
        # (lane r4-glow fs_water's water_n; ps_water_p: cb10[13].y + cb10[66].w)
        self.assertEqual(len(re.findall(r"textureSampleBias\(t10, s_rep, uv[B]?, u\.pad0\.y\)", s)), 3)
        self.assertIn("let orm = textureSample(t2, s_rep, uv);", s)


@unittest.skipUnless((ROOT / "hd/vfx/particles").is_dir(), "no build-95032 root")
class TestCorpus(unittest.TestCase):
    def test_mephisto(self):
        eff = rspec.load(ROOT / "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles")
        got = sorted((r.layer, mb.draw_resolution(r)) for r in eff.renderers if r.kind == "mesh")
        # calib r3: the skirt (CylinderInf) and polar disc draw at resolution 1, the skulls at 0
        self.assertIn(("CylinderInf", 1), got)
        self.assertIn(("CylinderSkullsInf", 0), got)


if __name__ == "__main__":
    unittest.main()
