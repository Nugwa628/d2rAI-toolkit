"""hdkit.fx.lighting and hdkit.view.ibl (lane hdfx-r3-lighting): one lighting setup from the game's data. No game files:
synthetic cubes and vis documents; the numbers the captures hold (RenderDoc, calib3 g4 / calib5 s3 / calib r3) are the
expected values."""
import json
import math
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
from hdkit.fx import lighting as LG, render as R  # noqa: E402
from hdkit.view import ibl  # noqa: E402

# CBV 6 SH of the captures (terrain draw 15460, calib3 g4: act 1 outdoors; liquid draw 8331, calib5 s3: act 1 caves)
CAP_OUTDOORS = [[3.6584, 3.7953, 4.0639], [4.4984, 4.6965, 5.0843], [0.9951, 0.9943, 1.0034], [1.1297, 1.1324, 1.0913],
                [1.6896, 1.6770, 1.6509], [1.6214, 1.6222, 1.6262], [-0.2474, -0.2559, -0.2765], [0.6433, 0.6439, 0.6507],
                [-0.9696, -0.9949, -1.0611]]
CAP_CAVES = [[0.7498, 0.7377, 0.8662], [0.6951, 0.7084, 0.8534], [-0.2825, -0.2939, -0.3502], [1.0123, 1.0463, 1.2694],
             [0.9212, 0.9293, 1.1186], [-0.3032, -0.2918, -0.3367], [-0.1817, -0.1874, -0.2276], [-0.4297, -0.4208, -0.5026],
             [0.1382, 0.1353, 0.1601]]

# act 1 outdoors vis numbers (data/hd/env/vis/act1_outdoors_dawn2.json / _day.json, build 95032)
KEY = {"dawn2": ((1.0, 0.854902, 0.4823529), 5.0,
                 {"x": -0.399301142, "y": 0.119472124, "z": -0.05256899, "w": -0.907480836}),
       "day": ((1.0, 0.9411765, 0.8705882), 13.5, {"x": -0.378458083, "y": 0.313054949, "z": -0.137747481, "w": -0.8601114})}


def vis_doc(key_col, power, quat, ibl_i=0.4, player=2000.0, ev=4.0):
    def light(name, col, pw, q, mask, shadow=False):
        return {"name": name, "components": [
            {"type": "DirectionalLightDefinitionComponent", "color": dict(zip("xyz", col)), "power": pw, "lightMask": mask,
             "shadowCaster": shadow, "diffuseContribution": 1.0, "specularContribution": 1.0},
            {"type": "TransformDefinitionComponent", "orientation": q}]}
    return {"entities": [
        {"name": "vis_data", "components": [{"type": "VisualDataComponent", "exposureValue": ev, "iblIntensity": ibl_i,
                                             "characterBrightnessFactor": 0.4,
                                             "characterContrastPoints": {"x": 0.25, "y": 0.25, "z": 0.75, "w": 0.99},
                                             "playerUnitLightPower": player, "playerUnitLightAttenuation": 5.5,
                                             "playerUnitLightDistanceScaler": 1.333,
                                             "playerUnitLightContributions": {"x": 1.0, "y": 0.666},
                                             "unitLightPower": 500.0, "unitLightAttenuation": 5.0,
                                             "unitLightDistanceOffsetRatio": 0.5, "unitLightDistanceScaler": 1.5}]},
        light("global_key_light", key_col, power, quat, 9, True),
        light("monster_fill_light", (1.0, 0.9372549, 0.8784314), 30.0,
              {"x": -0.3155095, "y": 0.632814169, "z": -0.3155095, "w": -0.632814169}, 74),
        light("monster_back_light", (0.8980392, 0.9176471, 1.0), 4.0, {"x": 0.0, "y": 0.8870108, "z": 0.0, "w": 0.4617486}, 10),
        light("player_key_light", (1.0, 0.9372549, 0.8784314), 30.0,
              {"x": -0.3155095, "y": 0.632814169, "z": -0.3155095, "w": -0.632814169}, 68)]}


class FakeFS:
    def __init__(self, files):
        self.files = {k.lower(): v for k, v in files.items()}

    def read(self, path):
        k = path.replace("\\", "/").lower()
        if k not in self.files:
            raise FileNotFoundError(path)
        v = self.files[k]
        return v if isinstance(v, bytes) else json.dumps(v).encode()


def outdoor_fs():
    names = ["data/hd/env/vis/act1_outdoors_%s.json" % n for n in LG.TOD_NAMES]
    files = {"data/hd/env/biome/act1_outdoors.json": {
        "visualDataFilenames": [{"visualDataFilename": n} for n in names],
        "iblSettings": {"cubemapFilename": "data/hd/env/ibl/act1_outdoors_CUBEMAP.ibls"}}}
    for n, nm in zip(LG.TOD_NAMES, names):
        col, pw, q = KEY.get(n, KEY["day"])
        files[nm] = vis_doc(col, pw, q)
    return FakeFS(files)


class Ibl(unittest.TestCase):
    def test_constant_sky(self):
        faces = np.ones((6, 16, 16, 3))
        sh = ibl.irradiance_sh(faces)
        self.assertAlmostEqual(sh[0, 0], math.pi, places=2)          # E = pi L everywhere
        self.assertLess(np.abs(sh[1:]).max(), 0.02)
        n = np.array([[0, 1, 0], [0, -1, 0], [1, 0, 0]], float)
        np.testing.assert_allclose(ibl.irradiance(sh, n), math.pi, rtol=0.01)

    def test_sky_above(self):
        d, _ = ibl.face_directions(32)
        faces = np.repeat((d[..., 1:2] > 0).astype(float), 3, -1)      # radiance 1 over the upper hemisphere
        sh = ibl.irradiance_sh(faces)
        up, down = ibl.irradiance(sh, np.array([[0, 1.0, 0], [0, -1.0, 0]]))[:, 0]
        self.assertAlmostEqual(up, math.pi, delta=0.15)              # l <= 2 band-limit of a step: ~3 %
        self.assertLess(down, 0.15)
        self.assertGreater(sh[1, 0], 1.0)                            # the y term carries it

    def test_face_axes(self):
        d, dw = ibl.face_directions(4)
        np.testing.assert_allclose(d[0, 2, 2], d[0, 2, 2] / np.linalg.norm(d[0, 2, 2]))
        self.assertGreater(d[0, :, :, 0].min(), 0)       # +x face
        self.assertLess(d[3, :, :, 1].max(), 0)          # -y face
        self.assertGreater(d[4, :, :, 2].min(), 0)       # +z face
        self.assertAlmostEqual(dw.sum(), 4 * math.pi, places=6)

    def test_read_cube(self):
        n, mips = 8, 2
        hdr = b"DDS " + struct.pack("<31I", 124, 0, n, n, 0, 0, mips, *([0] * 11), 32, 4, 0x30315844, *([0] * 5),
                                    0x1000, 0xFE00, 0, 0, 0)
        dx10 = struct.pack("<5I", ibl.DXGI_BC6H_UF16, 3, 4, 1, 0)
        body = b"".join(bytes([f]) * (4 * 16) + bytes([f]) * 16 for f in range(6))
        c = ibl.read_cube(hdr + dx10 + body)
        self.assertEqual((c["size"], c["mips"], len(c["faces"])), (8, 2, 6))
        self.assertEqual(len(c["faces"][5][0]), 64)
        self.assertEqual(c["faces"][5][1], bytes([5]) * 16)
        with self.assertRaises(ValueError):
            ibl.read_cube(hdr + struct.pack("<5I", ibl.DXGI_BC6H_UF16, 3, 0, 1, 0) + body)   # not a cube

    def test_table_equals_captures(self):
        t = LG.sh_table()
        np.testing.assert_allclose(t["data/hd/env/ibl/act1_outdoors_cubemap.ibls"]["sh"], CAP_OUTDOORS, atol=0.003)
        np.testing.assert_allclose(t["data/hd/env/ibl/act1_caves_cubemap.ibls"]["sh"], CAP_CAVES, atol=0.003)
        self.assertGreaterEqual(len(t), 15)


class Setup(unittest.TestCase):
    def setUp(self):
        self.fs = outdoor_fs()

    def test_time_of_day_blend(self):
        L = LG.for_biome(self.fs, "act1_outdoors", 1.531)       # calib3 g4 capture 4750
        self.assertEqual(L.sources["vis"][0].rsplit("_", 1)[-1], "dawn2.json")
        d = L.dirs(LG.TERRAIN_MASK)
        self.assertEqual([x["name"] for x in d], ["global_key_light"])
        np.testing.assert_allclose(d[0]["color"], np.array([1.0, 0.9007, 0.6885]) * 9.5128, rtol=0.003)
        np.testing.assert_allclose(d[0]["dir"], [0.3200, 0.7373, -0.5950], atol=0.003)   # pitch 47.5 deg kept by slerp
        self.assertAlmostEqual(L.exposure, 0.073125, places=6)
        self.assertAlmostEqual(L.ibl, 0.4)
        self.assertEqual(LG.tod_index("day"), 2.0)

    def test_mephisto_unit_light(self):
        L = LG.for_biome(self.fs, "act1_outdoors", 1.549, player=(-10.0, 0.0, 12.0))
        g = L.game_light(LG.MONSTER_MASK, unit=(7, (212, 212, 255)))        # Uber Mephisto: Light 7 as captured
        cols = {d["name"]: d["color"] for d in g["dirs"]}
        self.assertEqual(sorted(cols), ["global_key_light", "monster_back_light", "monster_fill_light"])
        np.testing.assert_allclose(cols["monster_fill_light"], [30.0, 28.11765, 26.35294], rtol=1e-4)
        np.testing.assert_allclose(cols["monster_back_light"], [3.59216, 3.67059, 4.0], rtol=1e-4)
        u, p = g["points"]
        self.assertAlmostEqual(u["radius"], 21.0)
        np.testing.assert_allclose(u["pos"], [0, 10.5, 0])
        np.testing.assert_allclose(u["color"], [33.0793, 33.0793, 39.7887], rtol=1e-4)
        np.testing.assert_allclose(p["pos"], [-10.0, 8.0, 12.0])
        np.testing.assert_allclose(p["color"], [159.1549] * 3, rtol=1e-5)
        self.assertAlmostEqual(p["radius"], 34.658, places=3)
        self.assertEqual((p["exp"], p["specular"]), (5.5, 0.666))
        np.testing.assert_allclose(g["value"]["poly"], [0.0, 0.36, 3.2, -2.56], atol=1e-6)
        self.assertAlmostEqual(g["value"]["strength"], 0.4)

    def test_light_points_once(self):
        L = LG.for_biome(self.fs, "act1_outdoors", 2.0)
        lt = L.light(LG.MONSTER_MASK, unit=(7, (212, 212, 255)))
        self.assertEqual(lt.game["points"], [])                  # the local lights are in Light.points only
        self.assertEqual(len(lt.points), 2)
        self.assertTrue(all(p["fx"] and p["atten"] < 0 for p in lt.points))
        self.assertAlmostEqual(lt.points[1]["color"][0] * math.pi, 159.1549, places=3)
        self.assertGreater(lt.key_col[0], 0)
        r = L.rig(LG.MONSTER_MASK)
        self.assertIs(r.lighting, L)
        self.assertEqual(r.tone, "lut")
        self.assertAlmostEqual(r.exposure, 0.073125, places=6)

    def test_ground_irradiance(self):
        L = LG.for_biome(self.fs, "act1_outdoors", 1.531, player=None)
        E = L.irradiance(np.zeros((1, 3)))[0]
        sun = 9.5128 * 0.7373 * np.array([1.0, 0.9007, 0.6885])
        sky = 0.4 * (np.array(CAP_OUTDOORS[0]) + np.array(CAP_OUTDOORS[1]) - np.array(CAP_OUTDOORS[6])
                     - np.array(CAP_OUTDOORS[8]))                               # SH at n = +y
        np.testing.assert_allclose(E, sun + sky, rtol=0.01)
        # the fitted constant of lanes calib3/4 (in FxLight units, x pi): R, G within 10 %
        np.testing.assert_allclose((E / math.pi)[:2], np.array(R.PLATE_IRRADIANCE)[:2], rtol=0.1)
        Lp = LG.for_biome(self.fs, "act1_outdoors", 1.531, player=(0.0, 0.0, 0.0), player_on_plate=True)
        Ep = Lp.irradiance(np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0]]))
        self.assertGreater(Ep[0, 0] - E[0], 20.0)           # the player's light under the caster (22.9)
        self.assertLess(Ep[1, 0] - E[0], 1.0)               # and nearly nothing 30 units away
        Lq = LG.for_biome(self.fs, "act1_outdoors", 1.531, player=(0.0, 0.0, 0.0))          # default: own shadow
        np.testing.assert_allclose(Lq.irradiance(np.zeros((1, 3)))[0], E)

    def test_plate_gain(self):
        L = LG.for_biome(self.fs, "act1_outdoors", 1.531, player=None)
        P = np.array([[[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]]])
        fx = [{"pos": [0.0, 2.0, 0.0], "radius": 10.0, "color": [100.0 / math.pi] * 3, "atten": -4.0, "fx": True}]
        g = L.plate_gain(P, fx)
        E = L.irradiance(P.reshape(-1, 3)).reshape(P.shape)
        f = R.plate_gain(P, fx, (1.0, 1.0, 1.0)) - 1.0                     # E_fx / pi
        np.testing.assert_allclose(g, 1.0 + f * math.pi / E, rtol=1e-6)
        self.assertGreater(g[0, 0, 0], g[0, 1, 0])

    def test_spec(self):
        o = LG.parse_spec("scene:act1_caves;tod=1.54;mask=19;unit=3,212,212,255;player=6,0,0;clut=/x.npy;plate_player=1;"
                          "light=1,2,3,10,20,30,5,4")
        self.assertEqual((o["biome"], o["tod"], o["mask"]), ("act1_caves", 1.54, 19))
        self.assertEqual(o["unit"], (3.0, (212.0, 212.0, 255.0)))
        self.assertEqual(o["player"], (6.0, 0.0, 0.0))
        self.assertTrue(o["player_on_plate"])
        self.assertFalse(LG.parse_spec("scene:x")["player_on_plate"])
        self.assertEqual(o["extra"][0]["radius"], 5.0)
        self.assertEqual(LG.parse_spec("scene:act1_outdoors;tod=day")["tod"], "day")
        sk = LG.for_biome(outdoor_fs(), "act1_outdoors", 2.0).unit_light(2, (0, 255, 255))     # the Pit's skeletons
        self.assertAlmostEqual(sk["radius"], 6.0)
        np.testing.assert_allclose(sk["color"], [0.0, 39.7887, 39.7887], rtol=1e-4)
        self.assertIsNone(LG.parse_spec("scene:x;player=none")["player"])

    def test_slerp_keeps_pitch(self):
        a = LG._q(KEY["dawn2"][2])
        b = LG._q(KEY["day"][2])
        for t in (0.0, 0.3, 0.7, 1.0):
            self.assertAlmostEqual(LG.towards(LG.slerp(a, b, t))[1], 0.7373, places=3)


if __name__ == "__main__":
    unittest.main()
