"""hdkit.fx.cull (lane r3-cull): the game's cull mode per renderer, the viewer's front face, the packers' winding, and
(with a GPU adapter, HDFX_GPU_TESTS=1) culled draws through FxRenderer. No game files needed; the capture check runs when
calib2's Fire Bolt draws are present."""
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
from hdkit.fx import cull, packers as pk, rspec, sim  # noqa: E402
from hdkit.fx.render import Material, SUPPORTED_FEATURES  # noqa: E402
from hdkit.view import camera  # noqa: E402

UBER = "vfx/materials/Diablo_Uber.pkma"
DRAWS = Path(os.environ.get("HDFX_CULL_DRAWS") or "/nonexistent")


def _get(d):
    return d.get


class Rule(unittest.TestCase):
    def test_billboards_cull_unless_planar(self):
        for m in range(5):
            self.assertEqual(cull.cull_mode("billboard", "vfx/Library/PopcornFXCore/Materials/Default_Billboard.pkma",
                                            _get({"General.BillboardingMode": m})), "back", m)
        self.assertEqual(cull.cull_mode("billboard", "x/Default_Billboard.pkma", _get({})), "back")   # absent = 0
        self.assertEqual(cull.cull_mode("billboard", "x/Default_Billboard.pkma",
                                        _get({"General.BillboardingMode": 5})), "none")

    def test_ribbons_cull_only_view_aligned(self):
        f = "x/Default_Ribbon.pkma"
        self.assertEqual(cull.cull_mode("ribbon", f, _get({})), "back")
        self.assertEqual(cull.cull_mode("ribbon", f, _get({"General.BillboardingMode": 1})), "none")
        self.assertEqual(cull.cull_mode("ribbon", f, _get({"General.BillboardingMode": 2})), "none")

    def test_uber_and_liquid_follow_backface_culling(self):
        for mat in (UBER, "vfx/materials/Diablo_Uber_Mesh.pkma", "vfx/materials/Diablo_Smoke.pkma",
                    "vfx/materials/Diablo_Liquid_Ribbon.pkma"):
            for kind in ("billboard", "ribbon", "mesh"):
                self.assertEqual(cull.cull_mode(kind, mat, _get({"BackfaceCulling": True})), "back", (mat, kind))
                self.assertEqual(cull.cull_mode(kind, mat, _get({"BackfaceCulling": False})), "none", (mat, kind))
        # planar with the switch on: culled
        self.assertEqual(cull.cull_mode("billboard", UBER, _get({"BackfaceCulling": True,
                                                                  "General.BillboardingMode": 5})), "back")

    def test_meshes_lights_decals_of_other_classes_not_culled(self):
        for kind, mat in (("mesh", "vfx/materials/Diablo_Default_Mesh.pkma"), ("mesh", "vfx/materials/Diablo_Standard_Mesh.pkma"),
                          ("light", "vfx/materials/Diablo_Light.pkma"), ("decal", "vfx/materials/Diablo_Decal.pkma")):
            self.assertEqual(cull.cull_mode(kind, mat, _get({"BackfaceCulling": True})), "none", mat)

    def test_material_and_coverage(self):
        r = rspec.RSpec(0, 0, "mesh", "vfx/materials/Diablo_Uber_Mesh.pkma", True, "L", {"BackfaceCulling": True}, {})
        self.assertEqual(Material.of(r).cull, "back")
        r = rspec.RSpec(0, 0, "billboard", UBER, True, "L", {}, {})
        self.assertEqual(Material.of(r).cull, "none")
        self.assertIn("BackfaceCulling", SUPPORTED_FEATURES)


class Winding(unittest.TestCase):
    def setUp(self):
        self.cam = camera.OrthoCamera(k=10.0)
        self.r, self.u, self.w = self.cam.basis()
        self.M = self.cam.matrix((64, 64), (0, 0, 128, 128), -50.0, 50.0)

    def test_towards_camera_is_w(self):
        np.testing.assert_allclose(cull.towards_camera(self.M), self.w, atol=1e-9)

    def test_front_face_keeps_camera_facing(self):
        ff = cull.front_face(self.M)
        tri = np.array([[0, 0, 0], self.r, self.u])              # r x u = w: faces the camera
        a = cull.ndc_winding(self.M, tri)
        self.assertEqual(ff, "ccw" if a > 0 else "cw")
        self.assertTrue(cull.facing(tri[None], self.w)[0])
        self.assertFalse(cull.facing(tri[None, ::-1], self.w)[0])

    def test_billboards_face_the_camera(self):
        rng = np.random.default_rng(3)
        pos, ax, ax2 = rng.normal(size=(40, 3)), rng.normal(size=(40, 3)), rng.normal(size=(40, 3))
        for mode in range(5):                                    # the game culls these back by default: all must face
            P, _ = pk.billboard_corners(mode, pos, np.full((40, 2), 0.5), rng.uniform(0, 6, 40), (self.r, self.u, self.w),
                                        ax, ax2)
            tris = P[:, [0, 1, 2]]
            self.assertTrue(cull.facing(tris, self.w).all(), mode)

    def test_planar_faces_axis2(self):
        P, _ = pk.billboard_corners(5, [[0, 0, 0]], [[1, 1]], [0.3], (self.r, self.u, self.w), [self.r], [-self.w])
        self.assertFalse(cull.facing(P[:, [0, 1, 2]], self.w)[0])
        P, _ = pk.billboard_corners(5, [[0, 0, 0]], [[1, 1]], [0.3], (self.r, self.u, self.w), [self.r], [self.w])
        self.assertTrue(cull.facing(P[:, [0, 1, 2]], self.w)[0])

    def test_view_aligned_ribbon_faces(self):
        pts = np.array([[0, 0, 0], [1, 0.2, 0], [2, 0.1, 0.5], [3, 0, 1]], float)
        v = pk.pack_ribbon(pts, 0.3, (self.r, self.u, self.w), pk.particle_block({}, 1))
        T = v[:, :3].reshape(-1, 3, 3)
        self.assertTrue(cull.facing(T, self.w).all())

    def test_axial_side_is_dir_cross_to_eye(self):
        """CAxialBillboarderQuad (PopcornFX-Web): side = cross(dir, toEye); corner 0 -> 1 runs along +side."""
        d = np.array([0.0, 1.0, 0.0])
        P, _ = pk.billboard_corners(2, [[0, 0, 0]], [[0.5, 0.5]], [0.0], (self.r, self.u, self.w), [d * 2])
        side = P[0, 1] - P[0, 0]
        ref = np.cross(d, self.w)
        self.assertGreater(float(side @ ref), 0.0)


@unittest.skipUnless((DRAWS / "18103" / "vb0.bin").exists(), "calib2 g7 draws absent")
class Capture(unittest.TestCase):
    """Fire Bolt in game (calib2 g7, frame 5563): the Default_Billboard sparks (mode 2) drawn with cull Back face the
    eye by the right-hand rule; the Uber mesh (BackfaceCulling on, cull Back) winds like its stored normals."""
    EYE = np.array([12111.0537, 440.1233, 12107.0615])

    def test_sparks_kept_face_eye(self):
        v = np.fromfile(DRAWS / "18103" / "vb0.bin", np.float32).reshape(-1, 4)[:, :3]
        ib = np.fromfile(DRAWS / "18103" / "ib.bin", np.uint16).reshape(-1, 3)
        n = np.cross(v[ib[:, 1]] - v[ib[:, 0]], v[ib[:, 2]] - v[ib[:, 0]])
        self.assertTrue(((n * (self.EYE - v[ib[:, 0]])).sum(1) > 0).all())

    def test_mesh_winding_matches_normals(self):
        z = np.load(DRAWS / "17881" / "draw.npz")
        P, N, ib = z["v_ATTR"][:, :3], z["v_ATTR_1"][:, :3], z["idx"].reshape(-1, 3)
        n = np.cross(P[ib[:, 1]] - P[ib[:, 0]], P[ib[:, 2]] - P[ib[:, 0]])
        self.assertTrue(((n * N[ib].sum(1)).sum(1) > 0).all())


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    """A planar billboard turned away from the camera: drawn without BackfaceCulling, hidden with it; turned towards the
    camera: drawn either way."""

    def _render(self, props, axis2):
        from hdkit.fx import render as fr
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        if not hasattr(Gpu, "R"):
            Gpu.R = gpu.Renderer(msaa=4)
        fxr = fr.FxRenderer(Gpu.R, NoVfs())
        p = {"Transparent": True, "Transparent.Type": 0, "General.BillboardingMode": 5}
        p.update(props)
        e = rspec.RenderEffect("t", [rspec.RSpec(0, 0, "billboard", UBER, True, "L", p, {})])
        cam = camera.OrthoCamera(k=10.0)
        r, _u, _w = cam.basis()
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {
            "Position": np.array([[0, 2.0, 0]]), "Size": np.array([[2.0, 2.0]]), "Axis": np.array([r]),
            "Axis2": np.array([axis2]), "Color.Color": np.array([[1, 0.5, 0.25, 1]])}})})
        draws = fxr.prepare(e, st, cam)
        lin = fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 96), 1, None, linear=True)
        return float(lin[..., 0].max())

    def test_planar_culled_only_when_away_and_switched_on(self):
        w = camera.OrthoCamera().basis()[2]
        self.assertGreater(self._render({}, -w), 0.3)                              # no switch: both sides drawn
        self.assertLess(self._render({"BackfaceCulling": True}, -w), 1e-3)         # away + switch: hidden
        self.assertGreater(self._render({"BackfaceCulling": True}, w), 0.3)        # towards + switch: drawn


if __name__ == "__main__":
    unittest.main()
