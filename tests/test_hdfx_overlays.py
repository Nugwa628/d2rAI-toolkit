"""lane hdfx-r3-overlays: overlay placement (the overlay JSON's transform, found from the effect's path) and the viewer's
--overlay auto default. No game files needed."""
import os
import json
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from hdkit.view import fxplace  # noqa: E402


def _ovl(filename, pos=(0.0, 14.0, 0.0), with_xf=True):
    comps = [{"type": "VfxDefinitionComponent", "name": "v", "filename": filename, "hardKillOnDestroy": True}]
    if with_xf:
        comps.append({"type": "TransformDefinitionComponent", "name": "t",
                      "position": dict(zip("xyz", pos)), "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
                      "scale": {"x": 1, "y": 1, "z": 1}, "inheritOnlyPosition": False})
    return {"type": "UnitDefinition", "entities": [{"type": "Entity", "name": "entity_root", "components": comps}]}


class FakeFS:
    def __init__(self, files):
        self.files = {k.lower(): (v if isinstance(v, bytes) else json.dumps(v).encode()) for k, v in files.items()}

    def exists(self, p):
        return p.lower() in self.files

    def read(self, p):
        return self.files[p.lower()]


STAMINA = "data/hd/vfx/particles/overlays/common/shrine_stamina/vfx_shrine_stamina_looping.particles"


class OverlayFor(unittest.TestCase):
    def test_candidates_from_the_effect_path(self):
        self.assertEqual(fxplace.overlay_candidates(STAMINA), ["hd/overlays/common/shrine_stamina.json"])
        self.assertEqual(fxplace.overlay_candidates("hd/vfx/particles/overlays/sorceress/light_cast_2/LightningCast_02.particles"),
                         ["hd/overlays/sorceress/light_cast_2.json", "hd/overlays/common/light_cast_2.json"])
        self.assertEqual(fxplace.overlay_candidates("data/hd/vfx/particles/missiles/x/x.particles"), [])

    def test_found_only_when_an_entity_names_the_effect(self):
        fs = FakeFS({"hd/overlays/common/shrine_stamina.json": _ovl(STAMINA)})
        self.assertEqual(fxplace.overlay_for(fs, STAMINA), "hd/overlays/common/shrine_stamina.json")
        fs2 = FakeFS({"hd/overlays/common/shrine_stamina.json": _ovl("data/hd/vfx/particles/other.particles")})
        self.assertIsNone(fxplace.overlay_for(fs2, STAMINA))
        self.assertIsNone(fxplace.overlay_for(FakeFS({}), STAMINA))

    def test_arg(self):
        fs = FakeFS({"hd/overlays/common/shrine_stamina.json": _ovl(STAMINA)})
        self.assertEqual(fxplace.resolve_overlay_arg(fs, "auto", STAMINA), "hd/overlays/common/shrine_stamina.json")
        self.assertEqual(fxplace.resolve_overlay_arg(fs, None, STAMINA), "hd/overlays/common/shrine_stamina.json")
        self.assertIsNone(fxplace.resolve_overlay_arg(fs, "none", STAMINA))
        self.assertEqual(fxplace.resolve_overlay_arg(fs, "hd/x.json", STAMINA), "hd/x.json")

    def test_transform_lifts_the_effect(self):
        from hdkit.view.scene import vfx_entities
        ents = vfx_entities(_ovl(STAMINA))
        M = np.asarray(ents[0]["transform"], float)
        self.assertAlmostEqual(M[1, 3], 14.0)

    def test_viewer_default_is_auto(self):
        from hdkit.view import fxcli
        a = fxcli.build_parser().parse_args(["x.particles"])
        self.assertEqual(a.overlay, "auto")


CORPUS = Path(os.environ.get("HDFX_CORPUS") or "/nonexistent")
STAMINA_FILE = CORPUS / "hd/vfx/particles/overlays/common/shrine_stamina/vfx_shrine_stamina_looping.particles"


class ViewEye(unittest.TestCase):
    def test_eye_is_towards_the_camera(self):
        from hdkit.view import camera
        cam = camera.OrthoCamera(30.0, 45.0)
        e = cam.eye()
        self.assertAlmostEqual(float(np.linalg.norm(e)), camera.GAME_EYE_DIST, places=3)
        np.testing.assert_allclose(e / np.linalg.norm(e), [0.6124, 0.5, 0.6124], atol=1e-3)
        np.testing.assert_allclose(cam.eye((1, 2, 3), 10.0) - [1, 2, 3], 10.0 * cam.basis()[2])

    def test_viewer_flag(self):
        from hdkit.view import fxcli
        self.assertEqual(fxcli.build_parser().parse_args(["x.particles"]).view_eye, "camera")
        self.assertEqual(fxcli.build_parser().parse_args(["x.particles", "--view-eye", "default"]).view_eye, "default")

    @unittest.skipUnless(STAMINA_FILE.exists(), "current-build corpus not present")
    def test_shrine_mesh_lands_on_the_overlay_point(self):
        """The shrine foot's mesh particle sits 3 units along view.direction from the overlay point: with the render
        camera's eye it projects onto the point (as in game, bench o1). Since the r3 integration the sim's default eye is
        the same game eye (vm.game_eye, lane r3-aura2); the old default (0, 10, 10) put it 25 px off."""
        from hdkit.view import camera, fxcli
        from hdkit.fx import rspec, render as R
        data = STAMINA_FILE.read_bytes()
        eff = rspec.load(data)
        cam = camera.OrthoCamera(30.0, 45.0)
        P = np.eye(4)
        P[1, 3] = 14.0
        out = {}
        for tag, eye in (("camera", cam.eye()), ("default", None), ("old", np.array([0.0, 10.0, 10.0], np.float32))):
            sm = fxcli.make_sim(data, 0, P, None, eye)
            sm.seek(1.0)
            st = sm.state()
            r = next(r for r in eff.renderers if r.kind == "mesh")
            pos = np.asarray(R.inputs_for(r, R.layer_batch(st, r))["Position"], float)[0]
            out[tag] = cam.to_pixels(pos, (0.0, 0.0)) - cam.to_pixels([0.0, 14.0, 0.0], (0.0, 0.0))
        self.assertLess(float(np.abs(out["camera"]).max()), 1.0)
        self.assertLess(float(np.abs(out["default"]).max()), 1.0)
        self.assertGreater(float(np.abs(out["old"][0])), 15.0)


class OverlayStop(unittest.TestCase):
    def tables(self):
        from hdkit.fx import context as C
        return C.Tables({"overlay": [{"overlay": "druid_fire_cast_1", "frames": "21", "animrate": "16"},
                                     {"overlay": "shrine_stamina", "frames": "15", "animrate": "8"}],
                         "skills": [{"skill": "Lava Pit", "castoverlay": "druid_fire_cast_1"}]})

    def test_overlay_seconds(self):
        from hdkit.fx.context import overlay_seconds
        self.assertAlmostEqual(overlay_seconds({"frames": "21", "animrate": "16"}), 0.84)
        self.assertAlmostEqual(overlay_seconds({"frames": "15", "animrate": "8"}), 1.2)
        self.assertIsNone(overlay_seconds({"frames": "0", "animrate": "16"}))
        self.assertIsNone(overlay_seconds(None))

    def test_cast_stop_reads_sprite_frame_count_when_table_is_blank(self):
        from hdkit.fx import context as C

        row = {"overlay": "fire_cast_1", "filename": "FireCast", "frames": "", "animrate": "16"}
        tb = C.Tables({"overlay": [row], "skills": [{"skill": "Fire Bolt", "castoverlay": "fire_cast_1"}]})
        path = "hd/overlays/common/fire_cast_1.json"
        header = bytes([0x74, 6, 1]) + (14).to_bytes(4, "little") + bytes(8)
        fs = FakeFS({path: _ovl("fire.particles"), "global/overlays/firecast.dcc": header})

        self.assertAlmostEqual(fxplace.stop_time("auto", path, tb, fs), 0.56)
        driver = C.cast(C.effects_of_skill(fs, "Fire Bolt", tb), fs=fs)
        self.assertAlmostEqual(driver.children[0][1].stop_s, 0.56)
        self.assertAlmostEqual(C.overlay_seconds(dict(row, frames="10"), fs), 0.40)
        self.assertIsNone(C.overlay_seconds(row, FakeFS({"global/overlays/firecast.dcc": header[:4]})))
        self.assertIsNone(C.overlay_seconds(row, FakeFS({})))

    def test_stop_time(self):
        tb = self.tables()
        self.assertAlmostEqual(fxplace.stop_time("auto", "hd/overlays/common/druid_fire_cast_1.json", tb), 0.84)
        self.assertIsNone(fxplace.stop_time("auto", "hd/overlays/common/shrine_stamina.json", tb))   # a state overlay
        self.assertIsNone(fxplace.stop_time("auto", "hd/overlays/common/druid_fire_cast_1.json", None))
        self.assertIsNone(fxplace.stop_time("none", "hd/overlays/common/druid_fire_cast_1.json", tb))
        self.assertAlmostEqual(fxplace.stop_time("0.5", None, None), 0.5)

    def test_advance_stops_once_at_the_time(self):
        class S:
            def __init__(self):
                self.t, self.started, self.running, self.log = 0.0, False, True, []

            def reset(self):
                self.t, self.started, self.running = 0.0, False, True
                self.log.append("reset")

            def step(self, dt):
                self.started = True
                self.t += dt
                self.log.append(("step", round(dt, 3)))

            def stop(self):
                self.running = False
                self.log.append(("stop", round(self.t, 3)))
        sm = S()
        fxplace.advance(sm, 0.5, 0.84)
        fxplace.advance(sm, 1.0, 0.84)
        self.assertIn(("stop", 0.84), sm.log)
        self.assertAlmostEqual(sm.t, 1.0)
        self.assertFalse(sm.running)
        fxplace.advance(sm, 0.2, 0.84)                    # back in time: restart, running again
        self.assertTrue(sm.running)


class VmBitwise(unittest.TestCase):
    def test_float_masked_by_bool_keeps_its_bits(self):
        from hdkit.fx import vm
        dt = np.array([[0.02], [0.02]], np.float32)
        m = np.array([[True], [False]])
        out = vm.reinterpret(vm.bitwise_lanes("and", dt, m), "f")       # integration r3: the one rule
        np.testing.assert_allclose(out[:, 0], [0.02, 0.0])
        self.assertEqual(int(vm.bitwise_lanes("and", np.array([[3]], np.int32), np.array([[True]]))[0, 0]), 3)
        np.testing.assert_allclose(vm.reinterpret(vm.bitwise_lanes("or", np.array([[0.0]], np.float32),
                                                                   np.array([[False]])), "f")[:, 0], [0.0])

    @unittest.skipUnless((CORPUS / "hd/vfx/particles/overlays/common/druid_fire_cast_1").exists(), "corpus not present")
    def test_lava_pit_scorch_runs_its_fade(self):
        """vfx_druid_fire_cast_1's MaindecalSmokeSahder: `and dt, running` timers fade the scorch in while the effect
        runs and out after the stop (before the fix both stayed 0)."""
        from hdkit.view import fxcli
        data = (CORPUS / "hd/vfx/particles/overlays/common/druid_fire_cast_1/vfx_druid_fire_cast_1.particles").read_bytes()
        sm = fxcli.make_sim(data, 0, np.eye(4))
        fxplace.advance(sm, 0.8, 0.84)
        f_on = float(sm.state().layers["MaindecalSmokeSahder"].fields["F"][0, 0])
        fxplace.advance(sm, 2.0, 0.84)
        b = sm.state().layers["MaindecalSmokeSahder"]
        self.assertAlmostEqual(f_on, 1.0, places=3)
        self.assertLess(float(b.fields["F"][0, 0]), 0.5)
        self.assertGreater(float(b.fields["10"][0, 0]), 0.5)


if __name__ == "__main__":
    unittest.main()
