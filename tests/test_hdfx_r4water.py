"""Lane r4-water: the game's water_p for WaterMaterial models, and depth-only scene objects.

- `render.water_uniforms`: the WaterMaterial values packed as ps_water_p reads its cb4 (capture r3-calibfix r4 eid 19928:
  Shallow, Deep, TransmissionMax, ..., FoamAlbedo + ShoreFoamDepth, ..., Roughness), colours sRGB-decoded.
- `render.water_material`: entry fs_water, premultiplied over (blend 3), the model's normal / detail / foam maps.
- With a GPU (HDFX_GPU_TESTS=1, pass hdkit.gpugate first): a water quad over a floor just below it draws lighter (shore
  foam, shallow colour) than over a deep floor; a `gpu.Prepared.occluders` quad above it hides it and adds no colour.
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import packers as pk  # noqa: E402
from hdkit.fx import render as fr  # noqa: E402
from hdkit.view import camera  # noqa: E402

# vfx_shrine_mana_water's WaterMaterial (corpus 95032)
SHRINE = {"ShallowAlbedo": [0.00392, 0.04314, 0.25098], "DeepAlbedo": [0.06667, 0.07451, 0.38824], "ShallowAlpha": 0.8,
          "DeepAlpha": 1.0, "Roughness": 0.1, "FoamAlbedo": [0.33725, 0.45098, 0.71765],
          "WaterTransmissionDistanceMax": 0.1, "ShoreFoamDepth": 2.6, "ShoreFoamTextureScale": 2.5,
          "WaterFlowNormalIntensity": 0.5, "WaterFlowNormalTextureScale": 1.25, "WaterFlowNormalDetailTextureScale": 3.0,
          "WaterFlowNormalDetailIntensity": 2.0}


class Cpu(unittest.TestCase):
    def test_uniforms(self):
        w = fr.water_uniforms(SHRINE)
        self.assertEqual(len(w), 23)          # + the flow (lane r4-glow)
        np.testing.assert_allclose(w[0:4], [0.000303, 0.003347, 0.051269, 0.8], atol=3e-4)   # sRGB decoded, alpha kept
        np.testing.assert_allclose(w[8:12], [0.093056, 0.171441, 0.473536, 2.6], atol=3e-4)
        np.testing.assert_allclose(w[12:16], [0.1, 0.1, 0.5, 2.0])
        np.testing.assert_allclose(w[16:19], [0.05, 0.15, 0.2])          # 0.04 x 1.25, x 3, 0.08 x 2.5

    def test_material(self):
        mesh = SimpleNamespace(params={"water": SHRINE}, maps={"NormalTexture": "n.texture", "FoamMaskTexture": "f.texture"})
        m = fr.standard_material(fr.Material(family="standard", opaque=True, entry="fs_dmesh"), mesh)
        self.assertEqual((m.entry, m.blend, m.opaque), ("fs_water", 3, False))
        self.assertEqual((m.textures[10], m.textures[3], m.textures[1]), ("n.texture", "f.texture", "@nrm"))
        self.assertEqual(len(m.water), 23)


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        cls.R = gpu.Renderer(msaa=4)
        cls.fxr = fr.FxRenderer(cls.R, NoVfs())
        cls.cam = camera.OrthoCamera(camera.GAME_EL, camera.GAME_AZ, 40.0)

    def _draw(self):
        pos = np.array([[-2, 1, -2], [2, 1, -2], [2, 1, 2], [-2, 1, 2]], float)
        tris = np.array([[0, 2, 1], [0, 3, 2]])
        v = pk.pack_mesh(pos, np.tile([0.0, 1.0, 0.0], (4, 1)), np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float), tris,
                         [np.eye(4)], pk.particle_block({}, 1, "standard"))
        mat = fr.water_material(fr.Material(family="standard", lit=5), SimpleNamespace(maps={}), SHRINE)
        return fr.FxDraw("mesh", 0, "water", mat, v, entry="fs_water")

    def _render(self, floor, base=None):
        lin = self.fxr.render([self._draw()], self.cam, (128, 128), base, None, None, (64, 64), 1, floor=floor,
                              linear=True)
        return lin[26:34, 60:68].reshape(-1, 4).mean(0)

    def test_custom_srgb_flow_uses_mesh_uv(self):
        from hdkit.view import scene

        maps = {"WaterFlowTexture": "flow.texture", "NormalTexture": "normal.texture"}
        water = dict(SHRINE, UseFlowTexture=1, WaterFlowIntensity=8, WaterFlowDirection=0)
        radial = np.tile(np.array([[[128, 128, 255, 58], [128, 128, 255, 196]]], np.uint8), (2, 1, 1))
        flow = np.array([[[255, 127, 0, 255], [0, 127, 0, 255]]], np.uint8)

        def texture(_fs, path, srgb=False):
            self.assertEqual(srgb, path == "flow.texture")
            rgba = flow if path == "flow.texture" else radial
            return scene.Texture(path, scene.FMT_RGBA8, [rgba], srgb)

        draw = self._draw()
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                draw.material = fr.water_material(draw.material, SimpleNamespace(maps=maps),
                                                   dict(water, UseFlowTexture=enabled))
                # Reuse the same world geometry and change only which flow texels its mesh UV reads.
                images = []
                for u in (0.25, 0.75):
                    draw.verts[:, pk.OFF["uv"][0]] = u
                    draw.gpu.clear()
                    with patch.object(self.fxr.vfs, "exists", return_value=True), patch.object(scene, "read_texture", texture):
                        image = self.fxr.render([draw], self.cam, (128, 128), ss=1, floor=-5, linear=True)
                    images.append(image)
                delta = np.abs(images[0] - images[1]).max()
                if enabled:
                    self.assertGreater(delta, 0.005)
                else:
                    self.assertEqual(delta, 0.0)

    def test_thickness_and_occluder(self):
        from hdkit.view import fxcli, gpu
        thin, deep = self._render(0.98), self._render(-5.0)
        self.assertGreater(deep[3], 0.95)                                # deep water: alpha 1 (DeepAlpha) + Fresnel
        self.assertGreater(thin[:3].sum(), 1.5 * deep[:3].sum())          # shore foam over the shallow floor
        occ = gpu.Prepared([], occluders=[fxcli.ground_draw(self.R, size=3.0, y=1.5)])
        hid = self._render(-5.0, occ)
        self.assertLess(hid[3], 0.01)                                     # hidden, and the occluder draws no colour
        self.assertLess(hid[:3].max(), 0.01)


if __name__ == "__main__":
    unittest.main()
