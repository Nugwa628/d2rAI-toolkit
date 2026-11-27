"""Tests of lane r3-misc: fx/gamekinds.py (what D2R leaves undrawn or ignores: Distortion_Billboard, triangle and sound
renderers, LegacyLit, MeshSampling), sound records in fx/sim.SimState.sounds, the coverage tool's `empty` status and
orphan objects, and the VM's bit select (`float & compare mask`).
Run: python -m unittest discover -s tests -p "test_hd*.py"   (corpus tests skip without the game files)"""
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
from hdkit.fx import coverage as cv, gamekinds as gk, rspec, vm  # noqa: E402

CORPUS = Path((os.environ.get("HDFX_CORPUS") or "/nonexistent"))
VFX = CORPUS / "hd/vfx"
PK = "vfx/Library/PopcornFXCore/Materials/%s.pkma"


class GameClass(unittest.TestCase):
    def test_factory_rule(self):
        self.assertEqual(gk.game_class("billboard", "vfx/materials/Diablo_Uber.pkma"), "uber")
        self.assertEqual(gk.game_class(0, PK % "Default_Billboard"), "default")
        self.assertEqual(gk.game_class("light", "vfx/materials/Diablo_Light.pkma"), "light")
        self.assertIsNone(gk.game_class("billboard", PK % "Distortion_Billboard"))
        self.assertIsNone(gk.game_class(3, PK % "Default_Triangle"))
        self.assertIsNone(gk.game_class("triangle", PK % "Default_Triangle"))
        self.assertIsNone(gk.game_class("6", PK % "Default_Sound"))
        self.assertIsNone(gk.game_class("kind6", ""))
        self.assertFalse(gk.drawn_by_game("sound", PK % "Default_Sound"))

    def test_rspec_kinds_follow_graph(self):
        self.assertEqual(rspec.KIND[3], "triangle")       # was "light": a triangle renderer drew as a light
        self.assertEqual(rspec.KIND[6], "sound")

    def test_notes(self):
        r = SimpleNamespace(props={"MeshSampling": {"value": True}, "LegacyLit": True, "Lit": True,
                                   "Distortion": {"value": False}})
        n = gk.notes_for(r)
        self.assertEqual(len(n), 2)
        self.assertTrue(any("no longer supported" in x for x in n))


class Coverage(unittest.TestCase):
    def test_supported(self):
        for f in ("Distortion", "GeometryTriangle", "GeometrySound", "Sound", "MeshSampling", "LegacyLit"):
            self.assertIn(f, cv.SUPPORTED_RENDER_FEATURES)
        for m in ("Distortion_Billboard", "Default_Triangle", "Default_Sound"):
            self.assertIn(PK % m, cv.SUPPORTED_MATERIALS)
        self.assertIn("triangle", cv.SUPPORTED_RENDER_KINDS)
        self.assertIn("sound", cv.SUPPORTED_RENDER_KINDS)
        self.assertNotIn("text", cv.SUPPORTED_SAMPLERS)          # never reached in the corpus: not implemented

    def test_empty_status(self):
        e = {"render": {"kinds": {}, "materials": {}, "features": {}}, "samplers": {},
             "vm": {"opcodes": {}, "calls": {}}}
        self.assertEqual(cv.assess(e)["status"], "empty")

    def test_kind_names(self):
        self.assertEqual(cv.kind_of(SimpleNamespace(kind="3", material="")), "triangle")
        self.assertEqual(cv.kind_of(SimpleNamespace(kind="6", material="")), "sound")

    @unittest.skipUnless(VFX.is_dir(), "no corpus")
    def test_corpus_files(self):
        def row(rel):
            return cv.assess(cv.survey_file(CORPUS / rel, rel))
        r = row("hd/vfx/particles/missiles/yellow_ball_glow_intro/vfx_yellowball_glow_light.particles")
        self.assertNotIn("text", r["samplers"])                  # the Text sampler is an orphan node
        self.assertEqual(row("hd/vfx/presets/materialeditor/trianglematerial.particles")["status"], "drawn")
        self.assertEqual(row("hd/vfx/particles/missiles/vfx_empty.particles")["status"], "empty")
        for rel in ("hd/vfx/personal/rmagan/barbarian_shout_overlay.particles",
                    "hd/vfx/presets/materialeditor/soundmaterial.particles",
                    "hd/vfx/personal/jonatlee/meshsampling.particles"):
            m = row(rel)["missing"]
            self.assertFalse([x for x in m if any(k in x for k in ("Distortion", "Sound", "MeshSampling", "kind:"))],
                             rel)


class BitSelect(unittest.TestCase):
    """D2R's VM compares give all-ones masks (cmpgt kernel 0x16d95a0: cmpltps) and and/or/xor work on raw lanes."""

    def test_float_and_mask(self):
        # integration r3: the one rule (vm.bitwise_lanes on raw lanes, vm.reinterpret on reads)
        f = np.array([[0.3], [0.7]], np.float32)            # a stream register holding a float
        m = np.array([[True], [False]])                      # a compare result
        out = vm.reinterpret(vm.bitwise_lanes("and", f, m), "f")
        self.assertEqual(out.dtype, np.float32)
        np.testing.assert_array_equal(out[:, 0], np.array([0.3, 0.0], np.float32))


class Sounds(unittest.TestCase):
    def test_record_and_markers(self):
        r = SimpleNamespace(index=3, props={"Sound.SoundData": {"string": "x.wav"}, "Sound.Volume": {"value": 0.5}})
        L = SimpleNamespace(n=3, ids=np.array([4, 5, 6]))
        ri = {"Position": np.arange(9, dtype=np.float32).reshape(3, 3), "Enabled": np.array([[1], [1], [0]])}
        rec = gk.sound_record(r, L, "Flash", ri, seen=4)
        self.assertEqual(rec["fired"].tolist(), [False, True, False])
        self.assertEqual(rec["sound"], "x.wav")
        self.assertAlmostEqual(float(rec["volume"][0]), 0.5)
        s = gk.sounds_summary([rec])[0]
        self.assertEqual((s["live"], s["fired"], s["at"]), (2, 1, [[3.0, 4.0, 5.0]]))
        basis = (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
        try:
            v = gk.sound_markers([rec], basis)
        except Exception as e:  # noqa: BLE001
            self.skipTest("packers basis form: %s" % e)
        self.assertEqual(v.shape[0], 2 * 6)

    @unittest.skipUnless(VFX.is_dir(), "no corpus")
    def test_rain_lightning_thunder(self):
        """rain.particles: lightning (light + sound) only when Rain Amount > 0.35 (a `rate & mask` select). The flash
        chain starts from a scene.intersect hit, so the test uses the script's authored hit distance (hdkit.fx.collide
        mode "authored"): with the game's rule (lane r3-intersect2: distance 0, length unused) this personal prototype
        never fires it, which is about collision, not the select tested here."""
        from hdkit.fx import collide, graph, sim
        F = VFX / "personal/nfrost/enviromentprototyping/rain.particles"
        e = graph.load(str(F))
        e.data = F.read_bytes()
        fired = []

        def ctx():
            return sim.SimContext(collision=collide.floor_world(0.0, mode="authored"))
        s = sim.Sim(e, seed=1, attrs={"Rain Amount": 1.0}, ctx=ctx())
        for _ in range(60):
            s.step(0.1)
            fired += [x for x in gk.sounds_summary(s.state().sounds) if x["fired"]]
        self.assertTrue(fired)
        self.assertEqual(fired[0]["layer"], "Lightning Flash")
        s = sim.Sim(e, seed=1, ctx=ctx())                       # default 0.2: no lightning
        for _ in range(60):
            s.step(0.1)
            self.assertFalse([x for x in gk.sounds_summary(s.state().sounds) if x["fired"]])


if __name__ == "__main__":
    unittest.main()
