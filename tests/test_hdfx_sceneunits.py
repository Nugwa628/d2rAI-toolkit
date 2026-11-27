"""Lane r3-aura2: extra scene units, mode-driven unit poses, and the game camera as the effects' view.position."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.fx import aurastencil, sceneunits, vm  # noqa: E402
from hdkit.view import camera  # noqa: E402


class GameEye(unittest.TestCase):
    def test_eye_is_towards_the_camera(self):
        w = camera.basis(camera.GAME_AZ, camera.GAME_EL)[2]
        e = vm.game_eye()
        self.assertAlmostEqual(float(np.linalg.norm(e)), vm.GAME_EYE_DISTANCE, places=2)
        np.testing.assert_allclose(e / np.linalg.norm(e), w, atol=1e-6)
        self.assertAlmostEqual(float(e[1]), 442.0, places=1)       # the captures' eye height

    def test_env_default_and_view_direction(self):
        env = vm.Env.__new__(vm.Env)
        env.camera = vm.game_eye()
        f = vm._view_axes(env)[2]
        np.testing.assert_allclose(f, -camera.basis(45.0, 30.0)[2], atol=1e-6)

    def test_camera_push_stays_on_the_feet_pixel(self):
        """An aura's Position + 2.5 x -view.direction lands on the origin's pixel with the game eye; the old default
        (0, 10, 10) put it 21 px left and 15 px up (the ring off the feet)."""
        cam = camera.OrthoCamera()
        for eye, want in ((vm.game_eye(), (0.0, 0.0)), (np.array([0, 10, 10], np.float32), (-21.2, -15.4))):
            env = vm.Env.__new__(vm.Env)
            env.camera = eye
            p = -2.5 * vm._view_axes(env)[2]
            px = cam.to_pixels(p[None], (0.0, 0.0))[0]
            self.assertAlmostEqual(float(px[0]), want[0], delta=0.2)
            self.assertAlmostEqual(float(px[1]), want[1], delta=0.2)

    def test_new_env_uses_the_game_eye(self):
        np.testing.assert_allclose(vm.Env().camera, vm.game_eye())


class Specs(unittest.TestCase):
    def test_at_and_pos(self):
        a, b = sceneunits.load({"units": [{"unit": "u.json", "at": [2, -1], "mode": "a1", "yaw": 200},
                                          {"unit": "v.model", "pos": [1, 2, 3], "facing": 3, "bogus": 1}]})
        self.assertEqual(a.pos, (4.0, 0.0, -2.0))
        self.assertEqual((a.mode, a.yaw), ("a1", 200))
        self.assertEqual(b.pos, (1.0, 2.0, 3.0))
        self.assertEqual(b.facing, 3)
        self.assertEqual(len(sceneunits.load('[{"unit": "x.json"}]')), 1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps({"units": [{"unit": "x.json", "at": [1, 1]}]}))
            self.assertEqual(sceneunits.load(str(p))[0].pos, (2.0, 0.0, 2.0))


class _FS:
    def __init__(self, files):
        self.files = files

    def read(self, p):
        if p not in self.files:
            raise FileNotFoundError(p)
        v = self.files[p]
        return v if isinstance(v, bytes) else json.dumps(v).encode()


_UNIT = {"entities": [{"name": "entity_root", "components": [
    {"type": "UnitRootComponent", "state_machine_filename": "data/hd/u/u_state_machine.json"}]}]}
_SM = {"name": "u_sm", "states": [
    {"name": "Neutral", "stateId": 1, "modeId": 1, "loopCount": 0,
     "animationBindings": {"hth": ["hth_neutral"], "stf": ["stf_neutral"]}},
    {"name": "Dead", "stateId": 2, "modeId": 12, "loopCount": 1, "animationBindings": {"hth": ["dead"]}}]}


class ModeClip(unittest.TestCase):
    def setUp(self):
        self.fs = _FS({"hd/u.json": _UNIT, "data/hd/u/u_state_machine.json": _SM})
        self.unit = type("U", (), {"clips": {"hth_neutral": 1, "stf_neutral": 1, "dead": 1, "walk": 1}})()

    def test_state_machine_binding(self):
        self.assertEqual(sceneunits.mode_clip(self.fs, "hd/u.json", self.unit, "nu", "stf")[0], "stf_neutral")
        self.assertEqual(sceneunits.mode_clip(self.fs, "hd/u.json", self.unit, "nu", "bow")[0], "hth_neutral")
        self.assertEqual(sceneunits.mode_clip(self.fs, "hd/u.json", self.unit, "dd")[0], "dead")

    def test_fallback_by_name_and_none(self):
        self.assertEqual(sceneunits.mode_clip(self.fs, "hd/u.json", self.unit, "wl")[0], "walk")
        clip, note = sceneunits.mode_clip(self.fs, "hd/u.json", self.unit, "a1")
        self.assertIsNone(clip)
        self.assertIn("bind pose", note)
        self.assertEqual(sceneunits.mode_clip(_FS({}), "m.model", self.unit, "wl")[0], "walk")


class _Prep:
    def __init__(self, draws):
        self.draws = draws

    def select(self, hide):
        return self


class _R:
    def __init__(self):
        self.calls = []

    def prepare(self, unit, clip, t, yaw, cam, pose_kw=None, place=None, light_class="unit", **kw):
        self.calls.append(dict(clip=clip, t=t, yaw=yaw, place=np.asarray(place), scale=unit.root_scale, lc=light_class))
        return _Prep([type("D", (), {"light_class": light_class, "terrain": None})()])


class Prepare(unittest.TestCase):
    def test_extra_units_are_posed_placed_and_mark_the_stencil(self):
        import hdkit.view.scene as scene
        clip = type("C", (), {"duration": 2.0})()
        u = type("U", (), {"clips": {"neutral": clip}, "root_scale": 1.0, "notes": []})()
        orig = scene.load_unit
        scene.load_unit = lambda fs, logical, lod=0: u
        try:
            R = _R()
            specs = sceneunits.load([{"unit": "f.model", "at": [2, -1], "clip": "neutral", "period": 0.5, "t0": 0.1,
                                      "yaw": 30, "scale": 1.5}, {"unit": "f.model", "pos": [0, 0, 5], "mode": "wl"}])
            draws, notes = sceneunits.prepare(R, _FS({}), specs, None, 0.25)
        finally:
            scene.load_unit = orig
        c0, c1 = R.calls
        self.assertEqual(c0["clip"], "neutral")
        self.assertAlmostEqual(c0["t"], 0.1 + 0.25 * 4.0)          # rate = duration / period
        np.testing.assert_allclose(c0["place"][:3, 3], [4.0, 0.0, -2.0])
        self.assertEqual((c0["yaw"], c0["scale"], c0["lc"]), (30, 1.5, "unit"))
        self.assertEqual(u.root_scale, 1.0)                        # restored
        self.assertIsNone(c1["clip"])                              # no walk clip: bind pose, said in the notes
        self.assertAlmostEqual(c1["yaw"], camera.facing_yaw(0))
        self.assertEqual(len(notes), 2)
        specs[0].world_scale = 1.1
        u.root_scale = 2.0
        scene.load_unit = lambda fs, logical, lod=0: u
        try:
            sceneunits.prepare(R, _FS({}), specs[:1], None, 0.0)
        finally:
            scene.load_unit = orig
        self.assertEqual(R.calls[-1]["scale"], 1.1)                # a capture's scale replaces the root scale
        self.assertEqual(u.root_scale, 2.0)
        prep = type("P", (), {"draws": draws})()
        self.assertEqual(len(aurastencil.units_of(prep)), 2)       # both hide aura draws


class FromCapture(unittest.TestCase):
    def _draw(self, root, name, pos, yaw):
        d = Path(root) / name
        d.mkdir()
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])           # camera.rot_y (column vectors)
        M = np.eye(4)
        M[:3, :3] = R.T                                            # row-vector world matrix
        M[3, :3] = pos
        (d / "w.bin").write_bytes(M.astype("<f4").tobytes())
        (d / "draw.json").write_text(json.dumps({"push_pointers": [{"push_offset": 0, "file": "w.bin"}]}))
        return str(d)

    def test_relative_positions_and_yaws(self):
        with tempfile.TemporaryDirectory() as t:
            ref = self._draw(t, "player", [100.0, 0.0, 200.0], 45.0)
            fal = self._draw(t, "fallen", [104.0, 0.0, 198.0], -120.0)
            doc = sceneunits.from_capture({fal: "hd/character/enemy/fallen1.json"}, ref, {fal: "a1"})
        u = doc["units"][0]
        np.testing.assert_allclose(u["pos"], [4.0, 0.0, -2.0], atol=1e-4)
        self.assertAlmostEqual(u["yaw"], -120.0, places=3)
        self.assertEqual(u["mode"], "a1")
        self.assertAlmostEqual(u["world_scale"], 1.0, places=4)
        self.assertEqual(sceneunits.load(doc)[0].pos, (4.0, 0.0, -2.0))


class Cli(unittest.TestCase):
    def test_options(self):
        from hdkit.view import fxcli
        a = fxcli.build_parser().parse_args(["x.particles", "--unit-mode", "nu", "--unit-wclass", "stf",
                                             "--scene-units", "s.json"])
        self.assertEqual((a.unit_mode, a.unit_wclass, a.scene_units), ("nu", "stf", "s.json"))


if __name__ == "__main__":
    unittest.main()
