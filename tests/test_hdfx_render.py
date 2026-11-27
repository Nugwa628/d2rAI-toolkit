"""hdkit.fx render pieces: packers (billboard modes, atlas, ribbons, sort, meshes, decals, lights), the renderer-spec
reader on a synthetic .particles file, materials from props, the fake simulation, and (with a GPU adapter) one composite
render. No game files needed; a real effect is read when the d2r extract is present."""
import os
import struct
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import packers as pk, rspec, sim  # noqa: E402
from hdkit.fx.render import Material, Post, sort_draws, FxDraw  # noqa: E402
from hdkit.view import camera  # noqa: E402

D2R = Path((os.environ.get("HDFX_D2R_ROOT") or "/nonexistent"))
MEPH = D2R / "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles"


def _basis():
    return camera.OrthoCamera().basis()


class Packers(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(pk.FLOATS, 62)          # 16 attributes: lane mats added so_x (dissolve UV)
        self.assertEqual(pk.OFF["color"], (10, 14))
        self.assertEqual(pk.PARTICLE_FLOATS, pk.FLOATS - 10)

    def test_particle_block_defaults_and_inputs(self):
        b = pk.particle_block({"Color.Color": np.array([[1, 0, 0, 0.5], [0, 1, 0, 1]]), "Blend.BaseContribution": 0.95,
                               "Blend.BaseUVScaleAndOffset": (3, 1, 0.2, 0.5), "unknown": 3}, 2)
        self.assertEqual(b.shape, (2, pk.PARTICLE_FLOATS))
        c0 = pk.OFF["color"][0] - 10
        np.testing.assert_allclose(b[0, c0:c0 + 4], [1, 0, 0, 0.5])
        s0 = pk.OFF["sc"][0] - 10
        np.testing.assert_allclose(b[:, s0], [0.95, 0.95], rtol=1e-6)
        np.testing.assert_allclose(b[:, s0 + 2], [1, 1])                       # exposure multiplier default 1
        u0 = pk.OFF["so_base"][0] - 10
        np.testing.assert_allclose(b[1, u0:u0 + 4], [3, 1, 0.2, 0.5])
        m0 = pk.OFF["so_mask"][0] - 10
        np.testing.assert_allclose(b[1, m0:m0 + 4], pk.ID_SO)

    def test_screen_aligned_faces_camera(self):
        r, u, w = _basis()
        P, N = pk.billboard_corners(0, [[1, 2, 3]], [[0.5, 0.25]], [0.0], (r, u, w))
        d = P[0] - np.array([1, 2, 3])
        np.testing.assert_allclose(d @ w, 0, atol=1e-9)                          # in the screen plane
        np.testing.assert_allclose(d[1] - d[0], r * 1.0, atol=1e-9)              # width 2 x half size along right
        np.testing.assert_allclose(d[2] - d[1], u * 0.5, atol=1e-9)
        np.testing.assert_allclose(N[0, 0], w)

    def test_rotation_turns_corners(self):
        r, u, w = _basis()
        P0, _ = pk.billboard_corners(0, [[0, 0, 0]], [[1, 1]], [0.0], (r, u, w))
        P1, _ = pk.billboard_corners(0, [[0, 0, 0]], [[1, 1]], [np.pi / 2], (r, u, w))
        np.testing.assert_allclose(P1[0, 1], P0[0, 2], atol=1e-9)                # 90 degrees: corner 1 -> where 2 was

    def test_axis_aligned_stretches_along_axis(self):
        r, u, w = _basis()
        axis = np.array([[0.0, 4.0, 0.0]])
        P, _ = pk.billboard_corners(2, [[0, 0, 0]], [[0.5, 0.5]], [0.0], (r, u, w), axis)
        span = P[0, 2] - P[0, 1]
        np.testing.assert_allclose(np.linalg.norm(span), 4.0, rtol=1e-6)         # full axis length
        np.testing.assert_allclose(np.cross(span, axis[0]), 0, atol=1e-6)
        side = P[0, 1] - P[0, 0]
        np.testing.assert_allclose(np.linalg.norm(side), 1.0, rtol=1e-6)
        self.assertAlmostEqual(float(side @ axis[0]), 0.0, places=6)

    def test_spheroid_keeps_width_when_axis_points_at_camera(self):
        r, u, w = _basis()
        P, _ = pk.billboard_corners(3, [[0, 0, 0]], [[0.5, 0.5]], [0.0], (r, u, w), np.array([w * 3.0]))
        xs, ys = P[0] @ r, P[0] @ u
        self.assertGreater(np.ptp(xs), 0.9)
        self.assertGreater(np.ptp(ys), 0.9)

    def test_planar_lies_in_plane(self):
        r, u, w = _basis()
        P, N = pk.billboard_corners(5, [[0, 0, 0]], [[1, 1]], [0.0], (r, u, w), [[1, 0, 0]], [[0, 1, 0]])
        np.testing.assert_allclose(P[0][:, 1], 0, atol=1e-9)                     # normal axis2 = y: flat on the ground
        np.testing.assert_allclose(N[0, 0], [0, 1, 0])
        np.testing.assert_allclose(np.ptp(P[0][:, 0]), 2.0, atol=1e-9)               # size is the half extent, as modes 0/1

    def test_viewpos_mode_and_capsule(self):
        r, u, w = _basis()
        P, _ = pk.billboard_corners(1, [[0, 0, 0]], [[1, 1]], [0.0], (r, u, w))
        np.testing.assert_allclose((P[0] - P[0].mean(0)) @ w, 0, atol=1e-9)
        P4, _ = pk.billboard_corners(4, [[0, 0, 0]], [[1, 1]], [0.0], (r, u, w), [[0, 2, 0]])
        self.assertEqual(P4.shape, (1, 4, 3))
        with self.assertRaises(ValueError):
            pk.billboard_corners(9, [[0, 0, 0]], [[1, 1]], [0.0], (r, u, w))

    def test_atlas(self):
        R = pk.atlas_rects((4, 2))
        self.assertEqual(R.shape, (8, 4))
        np.testing.assert_allclose(R[5], [0.25, 0.5, 0.5, 1.0])
        fa, fb, bl = pk.atlas_frame([2.25, 7.9, -1.5, 100], 8)
        self.assertEqual(list(fa), [2, 7, 1, 7])
        self.assertEqual(list(fb), [3, 7, 2, 7])
        np.testing.assert_allclose(bl, [0.25, 0.0, 0.5, 0.0], atol=1e-6)

    def test_pack_billboards_atlas_and_order(self):
        r, u, w = _basis()
        pos = np.array([[0, 0, 0], [5, 0, 5], [-5, 0, -5]], float)
        blk = pk.particle_block({}, 3)
        order = pk.sort_order(pos @ w, 2)
        self.assertEqual(list(order), [2, 0, 1])                                   # farthest first
        self.assertIsNone(pk.sort_order(pos @ w, 0))                                # additive: no sort
        v = pk.pack_billboards(0, pos, np.ones((3, 2)), np.zeros(3), (r, u, w), blk, tid=np.array([0, 1.5, 3]),
                               rects=pk.atlas_rects((2, 2)), blend_frames=True, order=order)
        self.assertEqual(v.shape, (18, pk.FLOATS))
        np.testing.assert_allclose(v[0, 0:3].mean() * 0 + v[0:6, 0:3].mean(0) @ w, pos[2] @ w, atol=1e-9)
        uv = v[6:12, 6:8]                                                           # particle 0: frame 0
        self.assertLessEqual(uv.max(), 0.5 + 1e-6)
        mb = pk.OFF["misc"][0]
        self.assertAlmostEqual(float(v[12, mb]), 0.5, places=5)                     # particle 1: blend 0.5 to frame 2

    def test_ribbon(self):
        r, u, w = _basis()
        pos = np.array([[0, 0, 0], [0, 1, 0], [0, 2, 0], [0, 3, 0]], float)
        v = pk.pack_ribbon(pos, 0.5, (r, u, w), pk.particle_block({}, 4))
        self.assertEqual(v.shape, (3 * 6, pk.FLOATS))
        P = v[:, 0:3]
        np.testing.assert_allclose(np.abs((P - P.round(6)) @ np.array([0, 0, 0])), 0)
        widths = np.linalg.norm(P[0] - P[1])
        self.assertAlmostEqual(float(widths), 1.0, places=6)
        side = P[0] - P[1]
        self.assertAlmostEqual(float(side @ np.array([0, 1, 0])), 0.0, places=6)  # across the strip
        self.assertEqual(len(pk.pack_ribbon(pos[:1], 0.5, (r, u, w), pk.particle_block({}, 1))), 0)

    def test_mesh_instances(self):
        mpos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
        mnrm = np.tile([0, 0, 1.0], (3, 1))
        X = pk.mesh_transforms([[10, 0, 0], [0, 0, 5]], (2.0, 1.0, 1.0), (0, 0.7071068, 0, 0.7071068))
        v = pk.pack_mesh(mpos, mnrm, np.zeros((3, 2)), np.array([[0, 1, 2]]), X, pk.particle_block({}, 2))
        self.assertEqual(v.shape, (6, pk.FLOATS))
        np.testing.assert_allclose(v[1, 0:3], [10, 0, -2], atol=1e-6)               # x 2, then 90 degrees about y
        np.testing.assert_allclose(v[0, 3:6], [1, 0, 0], atol=1e-6)                  # the normal turned too

    def test_decal_box_and_quad(self):
        box = pk.decal_box([1, 0, 2], [4, 1, 4])
        np.testing.assert_allclose((box @ [3, 0, 2, 1])[:3], [0.5, 0, 0], atol=1e-9)
        q = pk.decal_quad(box, _basis(), pk.particle_block({}, 1)[0])
        self.assertEqual(q.shape, (6, pk.FLOATS))

    def test_lights(self):
        L = pk.lights_from([[0, 1, 0]], [(1, 0.5, 0.25, 1.0)], 6.0, 2.0, 1.5)
        p = L[0].point()
        self.assertEqual(p["radius"], 6.0)
        np.testing.assert_allclose(p["color"], np.array([2, 1, 0.5]) / (4 * np.pi * np.pi))
        self.assertEqual(p["atten"], -12.0)          # the game's exponent, 8 x steepness, in the negative (game) form

    def test_game_light_falloff_and_plate(self):
        # D2R's point light (calib3): max(1 - (d/R)^e, 0)^2 / (1 + 0.0929 d^2)
        self.assertAlmostEqual(float(pk.light_falloff(0.0, 20.0, -1.6)), 1.0)
        f = float(pk.light_falloff(10.0, 20.0, -1.6))
        self.assertAlmostEqual(f, (1 - 0.5 ** 1.6) ** 2 / (1 + pk.LIGHT_INV_SQ * 100), places=6)
        self.assertEqual(float(pk.light_falloff(25.0, 20.0, -1.6)), 0.0)
        from hdkit.fx import render as R
        from hdkit.view import camera
        cam = camera.OrthoCamera()
        P = R.ground_points(cam, (50.0, 40.0), (100, 80))
        np.testing.assert_allclose(P[..., 1], 0.0, atol=1e-9)
        px = cam.to_pixels(P[40, 50], (50.0, 40.0))
        np.testing.assert_allclose(px, [50.5, 40.5], atol=1e-6)
        L = pk.lights_from([[0, 4, 0]], [(1, 0.5, 0.0, 1.0)], 20.0, 5000.0, 0.2)
        g = R.plate_gain(P, [L[0].point()], (2.5, 2.5, 2.5))
        self.assertGreater(g[40, 50, 0], 5.0)                   # under the light: a strong pool
        self.assertAlmostEqual(float(g[40, 50, 2]), 1.0)        # no blue in the light
        self.assertLess(g[0, 0, 0], g[40, 50, 0])


def _particles_file(strings, objects) -> bytes:
    """A minimal .particles file: objects as (class string index, payload bytes after the class)."""
    body = b""
    for cls, payload in objects:
        data = b"\x20" + struct.pack("<I", cls) + payload
        body += struct.pack("<I", len(data)) + data
    ncls = 0
    head_len = 28 + 8 * ncls
    so = head_len + len(body)
    head = struct.pack("<7I", 0xCA000B11, 0x01050902, 0x1F40, len(objects), ncls, so, 0)
    tab = struct.pack("<I", len(strings)) + b"".join(bytes([len(s)]) + s.encode() for s in strings)
    return head + body + tab


def _fields(*fs):
    out = struct.pack("<H", len(fs))
    for fid, raw in fs:
        out += struct.pack("<H", fid) + raw
    return out


def _arr(vals):
    return struct.pack("<I", len(vals)) + b"".join(struct.pack("<I", v) for v in vals)


class Spec(unittest.TestCase):
    def synthetic(self):
        S = ["CLayerCompileCache", "CLayerCompileCacheField", "CLayerCompileCacheRenderer",
             "CLayerCompileCacheRendererProperty", "CLayerCompileCacheRendererParticleInput", "Smoke",
             "vfx/materials/Diablo_Uber_Mesh.pkma", "Transparency", "Transparency.Type", "General.Mesh",
             "data/hd/vfx/meshes/x.model", "Blend.BaseContribution", "Color.Color", "SoftParticles.SoftnessDistance",
             "SoftParticles"]
        table = struct.pack("<8f", *([0.0] * 8)) + struct.pack("<8f", *([0.75] * 8))
        objs = [
            # 1: layer cache: name "Smoke" (5), fields 2..3, renderer 4
            (0, _fields((0, struct.pack("<I", len(table) // 16) + table), (2, _arr([2, 3])), (8, _arr([4])),
                        (20, struct.pack("<I", 5)))),
            (1, _fields((1, struct.pack("<I", 31)), (3, struct.pack("<I", 5)), (4, struct.pack("<I", 1)))),   # const slot 1
            (1, _fields((1, struct.pack("<I", 34)), (3, struct.pack("<I", 1)))),                               # evolved float4
            (2, _fields((0, struct.pack("<I", 2)), (1, _arr([5, 6])), (2, _arr([7, 8, 9, 10, 11])), (4, struct.pack("<I", 6)))),
            (4, _fields((1, struct.pack("<I", 0)), (2, struct.pack("<I", 11)), (3, struct.pack("<I", 31)))),
            (4, _fields((1, struct.pack("<I", 1)), (2, struct.pack("<I", 12)), (3, struct.pack("<I", 34)))),
            (3, _fields((0, struct.pack("<I", 7)), (2, b"\xff" * 16))),
            (3, _fields((0, struct.pack("<I", 8)), (1, struct.pack("<I", 3)), (2, struct.pack("<4i", 2, 0, 0, 0)))),
            (3, _fields((0, struct.pack("<I", 9)), (1, struct.pack("<I", 14)), (3, struct.pack("<I", 10)))),
            (3, _fields((0, struct.pack("<I", 14)), (2, b"\xff" * 16))),
            (3, _fields((0, struct.pack("<I", 13)), (1, struct.pack("<I", 7)), (2, struct.pack("<4f", 1.5, 1, 1, 1)))),
        ]
        return _particles_file(S, objs)

    def test_reads_renderer_props_inputs(self):
        e = rspec.load(self.synthetic(), use_graph=False)
        self.assertEqual(len(e.renderers), 1)
        r = e.renderers[0]
        self.assertEqual((r.kind, r.layer, r.enabled), ("mesh", "Smoke", True))
        self.assertEqual(r.props["Transparency.Type"], 2)
        self.assertEqual(r.props["General.Mesh"], "data/hd/vfx/meshes/x.model")
        self.assertEqual(r.props["SoftParticles.SoftnessDistance"], 1.5)
        self.assertEqual(r.inputs["Blend.BaseContribution"].const, (0.75,))
        self.assertIsNone(r.inputs["Color.Color"].const)
        self.assertEqual(r.inputs["Color.Color"].type, "float4")
        m = Material.of(r)
        self.assertEqual(m.blend, 3)                       # Transparency.Type 2 AlphaBlend -> D2R mode 3
        self.assertEqual(m.soft, 1.5)
        self.assertTrue(m.on("SOFT_PARTICLES"))
        self.assertFalse(m.on("POPCORN_ADDITIVE"))

    @unittest.skipUnless(MEPH.exists(), "d2r extract absent")
    def test_mephisto_ambient(self):
        e = rspec.load(MEPH)
        self.assertEqual([r.kind for r in e.renderers], ["mesh"] * 3)          # the PC graph only
        self.assertEqual(e.layers(), ["CylinderInf", "CylinderSkullsInf", "GroundDiscInf"])
        r0 = e.renderers[0]
        self.assertAlmostEqual(r0.inputs["Blend.BaseContribution"].const[0], 0.95, places=5)
        self.assertEqual(r0.inputs["Scale"].const, (1.0, 1.25, 1.0))
        self.assertIsNone(r0.inputs["Blend.BaseUVScaleAndOffset"].const)
        self.assertTrue(r0.props["Fresnel.DoubleSided"])
        m = Material.of(r0)
        self.assertEqual(m.blend, 1)                       # Additive -> D2R mode 1 (rgb x alpha)
        self.assertEqual(m.soft, 1.5)
        self.assertEqual(m.ops[0], 1)
        on = {k for k in m.keywords if m.on(k)}
        self.assertEqual(on & {"FRESNEL", "SOFT_PARTICLES", "POPCORN_ADDITIVE", "POPCORN_BLEND", "POPCORN_BLEND_MASK",
                               "VERTEX_DISPLACEMENT", "UV_DISTORTION"},
                         {"FRESNEL", "SOFT_PARTICLES", "POPCORN_ADDITIVE", "POPCORN_BLEND", "POPCORN_BLEND_MASK",
                          "VERTEX_DISPLACEMENT"})                     # notes 3: PS #1368 + VS #1608 keywords
        self.assertEqual(m.fresnel, (0, 0, 1, 1))
        self.assertTrue(m.srgb(4) and not m.srgb(0))
        self.assertIn(0, m.textures)
        self.assertTrue(m.textures[0].endswith("T_FX_smokey_01_noglitter.texture"))


class Inputs(unittest.TestCase):
    def test_inputs_from_streams_consts_and_enabled(self):
        from hdkit.fx.render import inputs_for
        r = rspec.RSpec(0, 5, "mesh", "m", True, "L", {}, {
            "Position": rspec.Input("Position", "16"), "Color.Color": rspec.Input("Color.Color", "1A"),
            "Blend.BaseContribution": rspec.Input("Blend.BaseContribution", "99", "float", (0.5,)),
            "Enabled": rspec.Input("Enabled", "1D")})
        b = sim.ParticleBatch(3, {"16": np.arange(9, dtype=np.float32).reshape(3, 3),
                                      "1A": np.ones((3, 4), np.float32), "1D": np.array([[1], [0], [1]], np.float32)})
        got = inputs_for(r, b)
        self.assertEqual(got["Position"].shape, (2, 3))                 # the switched-off particle is gone
        np.testing.assert_allclose(got["Blend.BaseContribution"], [[0.5], [0.5]])
        b.renderer_inputs = {0: {"Position": np.zeros((3, 3)), "Enabled": np.array([True, True, False])}}
        got = inputs_for(r, b)                                           # the sim's own map wins, Enabled filters it
        self.assertEqual(got["Position"].shape, (2, 3))

    @unittest.skipUnless(MEPH.exists(), "d2r extract absent")
    def test_mephisto_inputs_from_sim(self):
        from hdkit.fx.render import inputs_for
        e = rspec.load(MEPH)
        s = sim.Sim(MEPH.read_bytes())
        s.seek(1.0)
        st = s.state()
        for r in e.renderers:
            inp = inputs_for(r, st.layers[r.layer])
            self.assertEqual(inp["Position"].shape, (1, 3))
            self.assertEqual(inp["Blend.BaseUVScaleAndOffset"].shape, (1, 4))
            pk.particle_block(inp, 1)


class Lights(unittest.TestCase):
    def test_state_lights_become_points(self):
        from hdkit.fx.render import FxRenderer
        f = object.__new__(FxRenderer)                    # no GPU needed for this part
        f.lights = []
        e = rspec.RenderEffect("x", [rspec.RSpec(3, 9, "light", "Diablo_Light", True, "L",
                                                 {"LightAttenuation.AttenuationSteepness": 2.0}, {})])
        st = sim.SimState(0.0, {}, lights=[{"renderer": 3, "position": [[0, 1, 0], [5, 1, 0]], "color": [[1, 0.5, 0, 1]] * 2,
                                           "power": [2.0, 2.0], "range": [6.0, 6.0], "on": [True, False]}])
        f._state_lights(e, st)
        self.assertEqual(len(f.lights), 1)
        p = f.lights[0].point()
        np.testing.assert_allclose(p["color"], np.array([2, 1, 0]) / (4 * np.pi * np.pi))   # power / 4 pi, Lambert 1 / pi
        self.assertEqual((p["radius"], p["atten"]), (6.0, -16.0))                         # exponent 8 x steepness


class Misc(unittest.TestCase):
    def test_post_exposure(self):
        from hdkit.fx.render import GAME_EXPOSURE

        class R:
            exposure = 0.25
        # pre-exposed effect output -> the viewer's scene-linear target: gain / the rig's exposure
        self.assertAlmostEqual(Post(R(), 2.0).target_scale(), 8.0)
        self.assertAlmostEqual(Post().target_scale(), 1.0)
        # cb10[0].x: the rig's exposure, an explicit one, else the captured game value
        self.assertAlmostEqual(Post(R()).game_exposure(), 0.25)
        self.assertAlmostEqual(Post(R(), exposure=0.073125).game_exposure(), 0.073125)
        self.assertAlmostEqual(Post().game_exposure(), GAME_EXPOSURE)
        self.assertAlmostEqual(Post(R()).physical_scale(), 0.25 / np.pi)
        from hdkit.view import rig
        self.assertAlmostEqual(rig.exposure_for_ev(4.0), GAME_EXPOSURE, places=6)

    def test_inverse_encode_round_trip(self):
        from hdkit.fx.render import inverse_encode
        from hdkit.view import rig
        r = rig.resolve("game")
        x = np.array([[0.05, 0.4, 2.0], [0.0, 1.0, 5.0]])
        d = np.clip(r.encode(x), 0.0, 1.0)
        back = inverse_encode(r.encode, d)
        np.testing.assert_allclose(np.clip(r.encode(back), 0, 1), d, atol=2e-3)

    def test_hdr_over_bg_adds_in_scene_light(self):
        from hdkit.fx.render import hdr_over_bg
        from hdkit.view import gpu as G, rig
        r = rig.resolve("game")
        bg = np.full((2, 2, 3), G.srgb_decode(np.array(0.2)))
        lin = np.zeros((2, 2, 4))
        out0 = hdr_over_bg(lin, bg, r)
        self.assertTrue(np.all(np.abs(out0[..., :3].astype(int) - 51) <= 2))     # nothing added: the shot comes back
        lin[0, 0, :3] = 1.0                                                      # additive light, alpha 0
        out = hdr_over_bg(lin, bg, r)
        self.assertGreater(int(out[0, 0, 0]), int(out0[0, 0, 0]) + 20)
        self.assertEqual(int(out[1, 1, 0]), int(out0[1, 1, 0]))
        self.assertTrue(np.all(out[..., 3] == 255))

    def test_material_game_rules(self):
        def props(mask, mip=True):
            return {"Transparency": True, "Transparency.Type": 0, "Blend": True, "Blend.TextureBase": "a.texture",
                    "Blend.TextureBlend": "b.texture", "BlendMask": True, "BlendMask.Mask": mask,
                    "EnableMipBiasing": mip, "General.MipBias": 0.0, "General.Mesh": "m.model"}
        sk = Material.of(rspec.RSpec(1, 0, "mesh", "Diablo_Uber_Mesh", True, "L",
                                     props("data/hd/vfx/textures/default/White.texture"), {}))
        self.assertFalse(sk.on("POPCORN_BLEND_MASK"))          # PS #600: no BLEND_MASK (calib r3)
        self.assertEqual(sk.mip_bias, -0.5)                     # draw 27810's raw cb4[6].y (lane r3-mipbias, fx/mipbias)
        sm = Material.of(rspec.RSpec(0, 0, "mesh", "Diablo_Uber_Mesh", True, "L",
                                     props("data/hd/vfx/textures/noise/T_FX_gaussian_noise_01.texture"), {}))
        self.assertTrue(sm.on("POPCORN_BLEND_MASK"))
        self.assertEqual(sm.mip_bias, -0.5)                     # cb4[6].y of draws 27788 / 27799
        off = Material.of(rspec.RSpec(0, 0, "mesh", "Diablo_Uber_Mesh", True, "L", props("n.texture", False), {}))
        self.assertEqual(off.mip_bias, -0.5)                    # the game never reads EnableMipBiasing

    def test_sort_draws(self):
        cam = camera.OrthoCamera()
        w = cam.basis()[2]

        def d(z, blend):
            m = Material(blend=blend)
            v = np.zeros((3, pk.FLOATS), np.float32)
            v[:, 0:3] = w * z
            return FxDraw("mesh", 0, "", m, v, name="%g/%d" % (z, blend))
        out = sort_draws([d(5, 0), d(-3, 2), d(1, 5), d(0, 0)], cam)
        self.assertEqual([x.name for x in out], ["-3/2", "0/0", "1/5", "5/0"])        # farthest first


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    def test_additive_billboard_over_ground(self):
        from hdkit.fx import render as fr
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        R = gpu.Renderer(msaa=4)
        fxr = fr.FxRenderer(R, NoVfs())
        e = rspec.RenderEffect("t", [rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", {"Transparent": True,
                                                                                     "Transparent.Type": 0}, {})])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {"Position": np.array([[0, 2.0, 0]]),
                                                                           "Size": np.array([[2.0, 2.0]]),
                                                                           "Color.Color": np.array([[1, 0.5, 0.25, 1]])}})})
        cam = camera.OrthoCamera(k=10.0)
        draws = fxr.prepare(e, st, cam)
        self.assertEqual(len(draws), 1)
        lin = fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 96), 1, None, linear=True)
        c = lin[int(96 - 2 * 10 * cam.basis()[1][1]), 64]
        self.assertGreater(c[0], 0.5)
        self.assertGreater(c[0], c[2])


class GameCam(unittest.TestCase):
    """hdkit.fx.calib.gamecam on calib r3's numbers (cb3 of draw 27788, Mephisto's world matrix of draw 24407)."""
    CB3 = np.zeros((20, 4))
    CB3[0] = (1600, 900, 1 / 1600, 1 / 900)
    CB3[5:9] = [(0.70711, -0.35355, 0.61237, 0), (0, 0.86603, 0.5, 0), (-0.70711, -0.35355, 0.61237, 0),
                (456.79053, 7964.1592, -14678.277, 1)]
    CB3[12] = (11481.327, 441.97452, 12127.327, 1)
    CB3[13:17] = [(0.02121, 0, 0, 0), (0, 0.03771, 0, 0), (0, 0, 1e-4, 0), (3.1e-4, -5.6e-4, 1.001, 1)]
    MEPH = np.array([[-0.1951, 0, 0.9808, 0], [0, 1, 0, 0], [-0.9808, 0, -0.1951, 0], [10949.0615, 0.0328, 11573.0615, 1]])

    def test_view(self):
        from hdkit.fx.calib import gamecam
        gv = gamecam.from_cb3(self.CB3)
        self.assertAlmostEqual(gv.el, 30.0, places=2)
        self.assertAlmostEqual(gv.az, 45.0, places=2)
        self.assertAlmostEqual(gv.k, camera.K_1600x900, places=2)               # the viewer's px/unit, confirmed
        gv.unit = gamecam.placement(self.MEPH)
        gv.feet = gv.project(gv.unit["position"])
        self.assertAlmostEqual(gv.feet[0], 1064.0, delta=1.0)                   # the sbs crop's --feet 1064,425
        self.assertAlmostEqual(gv.feet[1], 426.0, delta=2.0)      # (rounded rows here; 426.0 from the file)
        self.assertAlmostEqual(gv.unit["yaw"], -101.25, places=1)               # 18 x 5.625 deg
        np.testing.assert_allclose(gv.unit["scale"], [1, 1, 1], atol=1e-3)     # no unit scale in game
        gv2 = gamecam.GameView.from_dict(gv.as_dict())
        self.assertEqual(gv2.project(gv.unit["position"]), gv.feet)
        # the viewer's camera puts a world point at the same pixel offset from the feet
        cam = camera.OrthoCamera(gv.el, gv.az, gv.k)
        P = np.array(gv.unit["position"]) + [2.0, 3.0, -1.0]
        px = cam.to_pixels([[2.0, 3.0, -1.0]], gv.feet)[0]
        np.testing.assert_allclose(px, gv.project(P), atol=0.05)

    def test_rot_y_convention(self):
        from hdkit.fx.calib import gamecam
        R = camera.rot_y(-101.25)
        M = np.eye(4)
        M[:3, :3] = R.T
        self.assertAlmostEqual(gamecam.placement(M)["yaw"], -101.25, places=4)


class Rebased(unittest.TestCase):
    """gr2.Skeleton.rebased: a model skeleton missing the file's upper bones (Mephisto's cog) takes the file hierarchy."""
    @staticmethod
    def _sk(bones):
        from hdkit.view import gr2

        class T:
            def __init__(self, p):
                self.position, self.orientation, self.scale_shear = p, (0, 0, 0, 1), np.eye(3).ravel()
        return gr2.Skeleton({"Name": "s", "Bones": [{"Name": n, "ParentIndex": pi, "Transform": T(pos),
                                                     "InverseWorldTransform": list(np.eye(4).ravel())} for n, pi, pos in bones]})

    def test_cog_lift(self):
        from hdkit.view import gr2
        full = self._sk([("root", -1, (0, 0, 0)), ("cog", 0, (0, 6.9, 0)), ("spine", 1, (0, 0, 0)), ("pelvis", 1, (0, -0.02, 0))])
        model = self._sk([("spine", -1, (0, 0, 0)), ("pelvis", -1, (0, 6.1, 0))])
        model.iwt[1][1, 3] = -6.1
        rb = model.rebased(full)
        self.assertIsNotNone(rb)
        self.assertEqual(rb.parent[rb.idx["spine"]], rb.idx["cog"])
        np.testing.assert_allclose(rb.iwt[rb.idx["pelvis"]][1, 3], -6.1)          # the model's bind kept
        W = gr2.pose_at(rb, None, 0.0).worlds
        self.assertAlmostEqual(W[rb.idx["spine"]][1, 3], 6.9)                     # spine rides on the cog
        self.assertIsNone(full.rebased(full))


if __name__ == "__main__":
    unittest.main()
