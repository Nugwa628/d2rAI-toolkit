"""Lane hdfx-r3-lighting2: shadow terms in the scene lighting, the game's bloom, scene inputs from a preset, the spot
axis, the unique-monster light (no game files needed; GPU tests opt-in with HDFX_GPU_TESTS=1)."""
import json
import math
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

from hdkit.fx import lighting as L  # noqa: E402
from hdkit.fx import scene_inputs as SI  # noqa: E402
from hdkit.view import tone  # noqa: E402


def scene(**kw):
    """A GameLighting with the act 1 morning key light (Blood Moor capture) and a flat SH."""
    q = [0.0, 0.0, 0.0, 1.0]
    d = np.array([0.320, 0.737, -0.595])                      # towards the key light (Blood Moor capture)
    d /= np.linalg.norm(d)
    ax = np.cross([0.0, 0.0, -1.0], d)                         # the rotation taking -z to d
    th = math.acos(float(np.dot([0.0, 0.0, -1.0], d)))
    ax = ax / np.linalg.norm(ax) * math.sin(th / 2)
    kq = [float(ax[0]), float(ax[1]), float(ax[2]), math.cos(th / 2)]
    lights = {"global_key_light": {"color": [1.0, 0.901, 0.692], "power": 9.59, "mask": 9, "quat": kq, "diffuse": 1.0,
                                   "specular": 1.0, "shadow": True},
              "monster_fill_light": {"color": [1, 1, 1], "power": 30.0, "mask": 74, "quat": q, "diffuse": 1.0,
                                     "specular": 1.0, "shadow": False},
              "monster_back_light": {"color": [1, 1, 1], "power": 4.0, "mask": 10, "quat": q, "diffuse": 1.0,
                                     "specular": 1.0, "shadow": False}}
    sh = np.zeros((9, 3))
    sh[0] = 1.0
    vis = {"iblIntensity": 0.4, "exposureValue": 4.0, "playerUnitLightPower": 2000.0, "bloomCutoff": 1.0,
           "bloomIntensity": 1.0}
    return L.GameLighting("test", 1.54, vis, lights, sh, **kw)


class TestShadowIndex(unittest.TestCase):
    def test_capture_s2_indices(self):
        """s2 unit draw 13030: key (shadowCaster) 0, monster_fill (mask 74) 1, monster_back (mask 10) none."""
        g = scene().game_light(L.MONSTER_MASK)
        idx = {d["name"]: d["shadow_mode"] for d in g["dirs"]}
        self.assertEqual(idx["global_key_light"], 0)
        self.assertEqual(idx["monster_fill_light"], 1)
        self.assertEqual(idx["monster_back_light"], 4294967295)

    def test_scene_light_has_shadows_on(self):
        self.assertEqual(scene().light().shadow, {"on": True})
        self.assertEqual(scene(shadows=False).light().shadow, {})


class TestIrradiance(unittest.TestCase):
    P = np.zeros((1, 3))

    def test_composition(self):
        """Ground (terrain set: the key light only, index 0): E = ibl SH + G (key N.L G s0 + locals)."""
        S = scene(player=None)
        e0 = S.irradiance(self.P)
        amb = S.ibl * np.maximum(__import__("hdkit.view.ibl", fromlist=["x"]).irradiance(S.sh, np.array([0, 1, 0.0])), 0)
        key = e0 - amb
        G, s0 = 0.5, 0.25
        e = S.irradiance(self.P, visibility={"G": G, "s": [s0, 1.0]})
        np.testing.assert_allclose(e, amb + key * G * s0 * G, rtol=1e-6)
        e3 = S.irradiance(self.P, visibility=np.array([[G, s0, 1.0]]))       # ground_vis layout
        np.testing.assert_allclose(e3, e, rtol=1e-6)

    def test_old_single_factor(self):
        """A plain array still scales the directional lights only (the old interface)."""
        S = scene(player=None)
        self.assertTrue(np.all(S.irradiance(self.P, visibility=np.zeros(1)) < S.irradiance(self.P)))

    def test_player_light_auto(self):
        """player_on_plate None: the player's light counts on the plate only with the shadow terms."""
        S = scene(player=(0.0, 0.0, 0.0))
        P = np.array([[3.0, 0.0, 0.0]])
        without = S.irradiance(P)
        with_terms = S.irradiance(P, visibility={"G": 1.0, "s": [1.0, 1.0]})
        self.assertGreater(float(with_terms.sum()), float(without.sum()))

    def test_plate_gain(self):
        S = scene(player=None)
        P = np.array([[[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]]])
        fx = [{"pos": [0.0, 3.0, 0.0], "radius": 10.0, "color": [10.0, 10.0, 10.0], "atten": -4.0}]
        g0 = S.plate_gain(P, fx)
        E0 = S.irradiance(P) / math.pi
        from hdkit.fx import render as R
        np.testing.assert_allclose(g0, R.plate_gain(P, fx, E0), rtol=1e-6)
        vis = np.full((1, 2, 3), 1.0)
        vis[..., 0] = 0.4                                     # the caster's units-slice shadow G
        g1 = S.plate_gain(P, fx, visibility=vis)
        E0s = S.irradiance(P, visibility=vis) / math.pi
        Efx = R.plate_gain(P, fx, 1.0) - 1.0
        np.testing.assert_allclose(g1, (E0s + 0.4 * Efx) / E0s, rtol=1e-6)
        vf = vis.copy()
        vf[..., 0] = 0.2                                      # the effect's casters darken further
        g2 = S.plate_gain(P, [], visibility=vis, visibility_fx=vf)
        self.assertTrue(np.all(g2 < 1.0))


class TestBloom(unittest.TestCase):
    def test_store_truncates(self):
        x = np.array([[[1.0 + 1.5 / 64, 3.3, 0.7]]], np.float32)
        y = tone.r11g11b10(x)
        self.assertAlmostEqual(float(y[0, 0, 0]), 1.0 + 1.0 / 64, places=6)
        self.assertTrue(np.all(y <= x))

    def test_cutoff_and_energy(self):
        v = np.zeros((64, 96, 3), np.float32)
        self.assertEqual(float(tone.game_bloom(v).sum()), 0.0)
        v[30:34, 40:44] = 0.9                                 # below the cutoff: no bloom
        self.assertEqual(float(tone.game_bloom(v).sum()), 0.0)
        v[30:34, 40:44] = 4.0
        b = tone.game_bloom(v, store=None)
        c = 4.0 / (1.0 + 4.0) * 16 * 3                        # cutoff output summed (16 px, 3 channels)
        self.assertGreater(float(b.sum()), 0.2 * c)            # at least the first level's light, 0.2 x the cutoff sum
        self.assertLess(float(b.sum()), 0.2 * c * 4 * 6)
        np.testing.assert_allclose(tone.game_bloom(v, intensity=2.0, store=None), 2 * b, rtol=1e-5)

    def test_rig_bloom_model(self):
        r = scene().rig(clut=None)
        self.assertEqual(r.post["bloom"]["model"], "game")
        self.assertIsNotNone(r.game_bloom())


class FakeFS:
    def __init__(self, files):
        self.files = {k.lower(): (json.dumps(v).encode() if not isinstance(v, bytes) else v) for k, v in files.items()}

    def read(self, p):
        return self.files[p.lower().replace("\\", "/")]

    def exists(self, p):
        return p.lower() in self.files


def tf(pos, quat=(0, 0, 0, 1)):
    return {"type": "TransformDefinitionComponent", "position": dict(zip("xyz", pos)),
            "orientation": dict(zip("xyzw", quat)), "scale": {"x": 1, "y": 1, "z": 1}}


class TestPresetInputs(unittest.TestCase):
    def fs(self):
        point = {"type": "PointLightDefinitionComponent", "power": 200.0, "radius": 25.0, "attenuation": 4.0,
                 "color": {"x": 1.0, "y": 0.6941177, "z": 0.4313726}, "diffuseContribution": 1.0}
        zero = {"type": "SpotLightDefinitionComponent", "power": 0.0, "distance": 0.0, "attenuation": 4.0}
        prefab = {"entities": [{"name": "accent_point01_04", "components": [tf((1.0, 4.27, 2.0)), point, zero]}]}
        q = (0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4))     # 90 degrees about +y
        spot = {"type": "SpotLightDefinitionComponent", "power": 3000.0, "distance": 25.0, "attenuation": 8.0,
                "angle": 90.0, "color": {"x": 1, "y": 0.27, "z": 0}}
        pre = {"biomeFilename": "data/hd/env/biome/act1_outdoors.json", "entities": [
            {"name": "pf", "components": [tf((100.0, 0.0, 50.0)), {"type": "PrefabPlacementDefinitionComponent",
                                                                   "prefab": "data/pf.json"}]},
            {"name": "forge_spot", "components": [tf((90.0, 3.2, 40.0), q), spot]},
            {"name": "far", "components": [tf((900.0, 3.0, 900.0)), point]}]}
        return FakeFS({"hd/env/preset/act1/town/test.json": pre, "data/pf.json": prefab})

    def test_capture_point133(self):
        """The r3 capture's record point133 is the townn1 prefab light: power 200 -> 200 / (4 pi) x colour."""
        ls = SI.preset_lights(self.fs(), "act1/town/test", near=(100.0, 50.0), radius=40.0)
        p = next(x for x in ls if x["name"].endswith("accent_point01_04"))
        np.testing.assert_allclose(p["color"], [15.915494, 11.047227, 6.865508], rtol=1e-5)
        self.assertEqual((p["radius"], p["exp"]), (25.0, 4.0))
        np.testing.assert_allclose(p["pos"], [101.0, 4.27, 52.0], atol=1e-5)
        self.assertFalse(any(x["name"] == "far" for x in ls))
        self.assertFalse(any(x["color"] == [0, 0, 0] for x in ls))          # power-0 spot left out

    def test_spot_axis_plus_z(self):
        ls = SI.preset_lights(self.fs(), "act1/town/test")
        s = next(x for x in ls if x["name"] == "forge_spot")
        np.testing.assert_allclose(s["spot"]["dir"], [1.0, 0.0, 0.0], atol=1e-6)   # +Z turned 90 deg about y -> +x

    def test_mapview_spot_axis(self):
        """mapview.hd.preset_lights puts a spot half way along +Z (the game's axis), not -Z."""
        from hdkit.view.mapview import hd
        ls = hd.preset_lights(self.fs(), "hd/env/preset/act1/town/test.json")
        s = next(x for x in ls if x["name"] == "forge_spot")
        self.assertGreater(s["pos"][0], 90.0 + 10.0)

    def test_biome(self):
        self.assertEqual(SI.biome_of(self.fs(), "act1/town/test"), "act1_outdoors")

    def test_spec(self):
        o = L.parse_spec("scene:preset=act1/town/townn1;at=372,140;radius=30;fxlights=0;bloom=0;shadows=0")
        self.assertEqual((o["preset"], o["at"], o["radius"], o["fx_lights"], o["bloom"], o["shadows"]),
                         ("act1/town/townn1", (372.0, 140.0), 30.0, False, False, False))
        self.assertIsNone(o["biome"])


class TestUniqueLight(unittest.TestCase):
    def test_uber_mephisto(self):
        """Capture r3: Uber Mephisto's light radius 21 = 2 x (monstats2 Light 3 + the unique 'light' umod 4) x 1.5."""
        self.assertEqual(L.monster_light({"Light": "3", "light-r": "212", "light-g": "212", "light-b": "255"},
                                         unique=True), (7.0, (212.0, 212.0, 255.0)))
        self.assertEqual(L.monster_light({"Light": "2", "light-r": "0", "light-g": "255", "light-b": "255"}),
                         (2.0, (0.0, 255.0, 255.0)))
        u = scene().unit_light(*L.monster_light({"Light": "3", "light-r": "212", "light-g": "212", "light-b": "255"},
                                                unique=True))
        self.assertAlmostEqual(u["radius"], 21.0)
        self.assertAlmostEqual(u["pos"][1], 10.5)


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS"), "GPU tests are opt-in (HDFX_GPU_TESTS=1)")
class TestGpu(unittest.TestCase):
    def test_ground_vis(self):
        """A box over the ground: ground_vis gives G < 1 under it and 1 far away; its slices are in 0..1."""
        from hdkit.view import camera, gpu as G, shadow as S
        R = G.Renderer(msaa=4)
        light = scene(player=None).light()
        sm = R.shadow
        cam = camera.OrthoCamera(30.0, 45.0, 20.0)
        P = np.array([[x, y, z] for x in (-1, 1) for y in (0, 6) for z in (-1, 1)], float)
        prep = G.Prepared([])
        enc = R.device.create_command_encoder()
        sm.encode(enc, prep, light, None)
        R.device.queue.submit([enc.finish()])
        if sm.spec is None:
            self.skipTest("shadows off")
        v = sm.ground_vis(light, cam, (64, 48), (128, 96), 0.0)
        self.assertEqual(v.shape, (96, 128, 4))           # G, slice 0, slice 1, the cube (lane r5-playerlight)
        self.assertTrue(np.all((v >= 0) & (v <= 1.0001)))


if __name__ == "__main__":
    unittest.main()


class TestClut(unittest.TestCase):
    def dds(self, lut):
        """A 65^3 RGBA16F volume .dds as data/hd/env/lut holds them."""
        h = bytearray(128)
        h[0:4] = b"DDS "
        np.frombuffer(h, "<u4")[1:6] = [124, 0x80100F, 65, 65, 520]
        np.frombuffer(h, "<u4")[6] = 65
        np.frombuffer(h, "<u4")[21] = 113
        a = np.concatenate([lut, np.ones(lut.shape[:3] + (1,), np.float32)], -1).astype(np.float16)
        return bytes(h) + a.tobytes()

    def test_read_and_identity(self):
        from hdkit.view import clut as C
        I = C.identity()
        np.testing.assert_array_equal(C.read_grading_lut(self.dds(I)), I.astype(np.float16).astype(np.float32))
        self.assertEqual(tuple(I[0, 0, 64]), (1.0, 0.0, 0.0))            # [b, g, r]: r = x

    def test_default_matches_none(self):
        from hdkit.view import clut as C
        np.testing.assert_allclose(C.universal(C.identity()), C.universal(None), atol=1e-6)

    def test_grey_axis(self):
        """Black stays black, grey stays neutral and rises, the top reaches the captured 1.641 (SDR, PARAMS)."""
        from hdkit.view import clut as C
        U = C.universal(None)
        d = np.array([U[i, i, i] for i in range(0, 65, 8)])
        self.assertAlmostEqual(float(d[0].max()), 0.0, places=5)
        np.testing.assert_allclose(d[:, 0], d[:, 1], atol=2e-3)
        self.assertTrue(np.all(np.diff(d[:, 0]) > 0))
        self.assertAlmostEqual(float(d[-1, 0]), 1.641, places=2)

    def test_grading_composes(self):
        """A darker grading LUT gives a darker CLUT, sampled at the same place as the game (inside the tone curve)."""
        from hdkit.view import clut as C
        I = C.identity()
        dark = I * 0.9
        a, b = C.universal(I), C.universal(dark)
        self.assertLess(float(b[32, 32, 32, 0]), float(a[32, 32, 32, 0]))
        np.testing.assert_allclose(C.blend_grading(I, dark, 0.5), I * 0.95, atol=1e-6)
