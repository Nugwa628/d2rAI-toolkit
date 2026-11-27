"""AlphaMask.AtlasAlphaOnly and Atlas.Flipbook (lane r3-atlasalpha): the game's atlas VS #352 / PS #3746 / #3647 rules
(docs/hdfx-shaders.md 4.1, 4.2). CPU parts always; the GPU check with HDFX_GPU_TESTS=1 (pass hdkit.gpugate first)."""
import os
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
from hdkit.fx import coverage, packers as pk, rspec, sim  # noqa: E402
from hdkit.fx import render as fr  # noqa: E402
from hdkit.view import camera  # noqa: E402

D2R = Path((os.environ.get("HDFX_D2R_ROOT") or "/nonexistent"))
CAST = D2R / "hd/vfx/particles/overlays/sorceress/fire_cast_1/vfx_fire_cast_1.particles"

AM = "data/hd/vfx/textures/test/am_atlas.texture"
BASE = "data/hd/vfx/textures/test/base_ramp.texture"


def _props(alpha_only=True, flipbook=False, atlas=True):
    p = {"Transparent": True, "Transparent.Type": 0, "Blend": True, "Blend.TextureBase": BASE,
         "Blend.TextureBlend": "data/hd/vfx/textures/default/White.texture", "AlphaMask": True, "AlphaMask.Mask": AM}
    if atlas:
        p.update({"Atlas": True, "Atlas.SubDiv": (2, 2), "Atlas.Blending": 0})
    if alpha_only:
        p["AlphaMask.AtlasAlphaOnly"] = True
    if flipbook:
        p["Atlas.Flipbook"] = True
    return p


class Cells(unittest.TestCase):
    def test_cell_wraps_like_the_vs(self):
        np.testing.assert_array_equal(pk.atlas_cell([0, 1, 2, 3, 4, 5.7, 7, 8], (2, 2)), [0, 1, 2, 3, 0, 1, 3, 0])
        # negative ids: the VS's sign rule (s = -N): floor(fract(t / -N) x -N)
        self.assertEqual(float(pk.atlas_cell([-1.0], (2, 2))[0]), -1.0)

    def test_origin_matches_the_atlas_rects_on_square_atlases(self):
        for sd in ((2, 2), (4, 4), (8, 8)):
            g = np.arange(sd[0] * sd[1])
            np.testing.assert_allclose(pk.atlas_cell_origin(g, sd), pk.atlas_rects(sd)[:, :2], atol=1e-6)

    def test_origin_row_divides_by_rows(self):
        # the VS's row = floor(g / SubDiv.y) (not / cols): cell 5 of a 4 x 2 atlas -> column 1, row 2
        np.testing.assert_allclose(pk.atlas_cell_origin([5], (4, 2))[0], [0.25, 1.0])

    def test_local_uv(self):
        sd = (2, 2)
        r = pk.atlas_rects(sd)
        org = pk.atlas_cell_origin([1], sd)
        corners = r[1, :2] + pk.CORNER_UV * (r[1, 2:] - r[1, :2])
        np.testing.assert_allclose(pk.atlas_local_uv(corners, org, sd), pk.CORNER_UV, atol=1e-6)
        # frame B in another cell with A's origin: clamped to that cell's side (constant along the far axis)
        cb = r[2, :2] + pk.CORNER_UV * (r[2, 2:] - r[2, :2])
        lb = pk.atlas_local_uv(cb, org, sd)
        np.testing.assert_allclose(lb[:, 0], 0.0)
        np.testing.assert_allclose(lb[:, 1], 1.0)

    def test_pack_puts_the_cell_in_misc_y(self):
        r, u, w = camera.OrthoCamera().basis()
        blk = pk.particle_block({}, 2)
        v = pk.pack_billboards(0, [[0, 0, 0], [1, 0, 0]], [[1, 1], [1, 1]], [0, 0], (r, u, w), blk,
                               tid=[1.0, 6.0], rects=pk.atlas_rects((2, 2)), alpha_only_subdiv=(2, 2))
        my = pk.OFF["misc"][0] + 1
        np.testing.assert_array_equal(v[:6, my], 1.0 + pk.MISC_Y_CELL_BIAS)
        np.testing.assert_array_equal(v[6:, my], 2.0 + pk.MISC_Y_CELL_BIAS)       # TexID 6 wraps to cell 2 (the CPU rect clamps to 3)
        v0 = pk.pack_billboards(0, [[0, 0, 0]], [[1, 1]], [0], (r, u, w), pk.particle_block({}, 1),
                                tid=[1.0], rects=pk.atlas_rects((2, 2)))
        np.testing.assert_array_equal(v0[:, my], 0.0)


class Materials(unittest.TestCase):
    def test_flags(self):
        m = fr.Material.of(rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", _props(flipbook=True), {}))
        self.assertTrue(m.atlas["alpha_only"])
        self.assertTrue(m.atlas["flipbook"])
        rib = fr.Material.of(rspec.RSpec(0, 0, "ribbon", "Diablo_Uber_Ribbon", True, "L", _props(), {}))
        self.assertFalse(rib.atlas["alpha_only"])           # only the billboard atlas VS reads cb4[4].y
        no = fr.Material.of(rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", _props(atlas=False), {}))
        self.assertFalse(no.atlas)                          # UV_MODE 0: the flag does nothing

    def test_uniforms(self):
        m = fr.Material.of(rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", _props(flipbook=True), {}))
        d = fr.FxDraw("billboard", 0, "L", m, np.zeros((6, pk.FLOATS), np.float32))
        me = SimpleNamespace(post=fr.Post())
        u = fr.FxOverlay.uniforms(me, d, np.eye(4), (64, 64), (0.0, 1.0), camera.OrthoCamera())
        self.assertEqual(u[43], 1.0)                        # inv.w AtlasAlphaOnly
        self.assertEqual(u[51], 1.0)                        # exq.w Atlas.Flipbook
        np.testing.assert_array_equal(u[94:96], [2.0, 2.0])  # vp.zw SubDiv
        m2 = fr.Material.of(rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", _props(False), {}))
        u2 = fr.FxOverlay.uniforms(me, fr.FxDraw("billboard", 0, "L", m2, d.verts), np.eye(4), (64, 64), (0.0, 1.0),
                                   camera.OrthoCamera())
        self.assertEqual(u2[43], 0.0)
        self.assertEqual(u2[51], 0.0)

    def test_coverage_counts_them(self):
        got = coverage.supported()
        self.assertIn(("feature", "AlphaMask.AtlasAlphaOnly"), got)
        self.assertIn(("feature", "Atlas.Flipbook"), got)

    @unittest.skipUnless(CAST.exists(), "d2r extract absent")
    def test_fire_cast_smoke(self):
        e = rspec.load(CAST)
        r = [s for s in e.renderers if s.index == 10][0]
        m = fr.Material.of(r)
        self.assertTrue(m.atlas["alpha_only"])
        self.assertEqual(m.atlas["subdiv"], (2, 2))


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    """A 2 x 2 alpha atlas whose cell 1 (top right) is white, a base texture whose red ramps 0 -> 1 left to right.
    TexID 1: with AtlasAlphaOnly the base spans its whole ramp across the quad (cell-local uv); without, the base is cut
    to the cell (u 0.5..1), so the left edge is half bright. Flipbook: the base's UVScaleAndOffset is ignored."""

    def _render(self, props, so_base=(1.0, 1.0, 0.0, 0.0)):
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        R = gpu.Renderer(msaa=4)
        fxr = fr.FxRenderer(R, NoVfs())
        n = 64
        am = np.zeros((n, n, 4), np.uint8)
        am[: n // 2, n // 2:] = 255                          # cell 1: top row, right column (row-major from the top)
        ramp = np.zeros((n, n, 4), np.uint8)
        ramp[..., 0] = np.linspace(0, 255, n).astype(np.uint8)[None, :]
        ramp[..., 3] = 255
        fxr._texinfo[(False, AM)] = SimpleNamespace(mips=[am], srgb=False)
        fxr._texinfo[(False, BASE)] = SimpleNamespace(mips=[ramp], srgb=False)
        fxr._texinfo[(True, BASE)] = fxr._texinfo[(False, BASE)]
        e = rspec.RenderEffect("t", [rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", props, {})])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {
            "Position": np.array([[0, 2.0, 0]]), "Size": np.array([[2.0, 2.0]]), "TextureID": np.array([[1.0]]),
            "Color.Color": np.array([[1, 1, 1, 1.0]]), "Blend.BaseUVScaleAndOffset": np.array([so_base]),
            "Blend.BaseContribution": np.array([[1.0]])}})})
        cam = camera.OrthoCamera(k=20.0)
        draws = fxr.prepare(e, st, cam)
        self.assertEqual(len(draws), 1)
        lin = fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 96), 1, None, linear=True)
        r, u, _w = cam.basis()
        cy = int(round(96 - 2.0 * 20.0 * u[1]))
        row = lin[cy, :, 0]
        lit = np.nonzero(row > 1e-3)[0]
        return row, lit

    def test_atlas_alpha_only_spans_the_base(self):
        row, lit = self._render(_props(True))
        self.assertGreater(len(lit), 10)
        left, right = row[lit[0] + 4], row[lit[-1] - 1]     # (the base is sRGB: 0.5 -> 0.21 linear)
        self.assertLess(left, 0.05)                          # whole ramp: dark at the left edge
        self.assertGreater(right, 0.7)
        row0, lit0 = self._render(_props(False))
        self.assertGreater(row0[lit0[0] + 4], 0.15)          # without: the base cut to its right half

    def test_flipbook_ignores_uv_scale_and_offset(self):
        so = (1.0, 1.0, 0.5, 0.0)                            # +0.5 u: wraps the ramp mid-quad unless ignored
        row_f, lit_f = self._render(_props(True, flipbook=True), so)
        row_n, lit_n = self._render(_props(True, flipbook=False), so)
        self.assertLess(row_f[lit_f[0] + 4], 0.05)           # flipbook: the ramp as without an offset
        self.assertGreater(row_n[lit_n[0] + 4], 0.15)        # offset applied: starts at 0.5


if __name__ == "__main__":
    unittest.main()
