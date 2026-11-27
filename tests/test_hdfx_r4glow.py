"""Lane r4-glow: object effects on the unit's own model, the water's flow, an attribute switch of a running effect.

- `fx/modeltrack.py`: a mesh renderer with ModelInstanceTracking and no General.Mesh draws the posed world meshes in
  `FxRenderer.tracked` as they are (no particle transform); `scene.load_unit` keeps the object effects no mask/strip layer
  takes as `Unit.tracked_fx` (Wraith: vfx_wraith1_objectFX on entity_torso; the casc cache copy, skipped when absent).
- `render.water_flow_vector` / `water_time_uniforms`: ps_water_p's flow vector of the shrine water (default_WATER flow map,
  WaterFlowDirection 90 fed to sin/cos as radians, x WaterFlowIntensity) and the time slots.
- `fxcli.preroll` (--attr-before): the sims run with the earlier value, then take the --attr one.
"""
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
from hdkit.fx import modeltrack  # noqa: E402
from hdkit.fx import packers as pk  # noqa: E402
from hdkit.fx import render as fr  # noqa: E402
from hdkit.view import fxcli  # noqa: E402

CACHE = Path((os.environ.get("HDFX_CASC_CACHE") or "/nonexistent"))
SHRINE = {"WaterFlowNormalSpeed": 0.2, "WaterFlowIntensity": 0.5, "WaterFlowDirection": 90.0,
          "ShoreFoamAnimationSpeed": 0.25}


class Tracked(unittest.TestCase):
    def test_posed_mesh_drawn_as_is(self):
        P = np.array([[1.0, 2.0, 3.0], [2.0, 2.0, 3.0], [1.0, 3.0, 3.0]])
        m = SimpleNamespace(name="body", uv=np.zeros((3, 2)), tris=np.array([[0, 1, 2]]), col=None)
        fxr = SimpleNamespace(tracked=[(m, P, np.tile([0.0, 0.0, 1.0], (3, 1)))])
        r = SimpleNamespace(index=0, layer="ObjectFX", get={"ModelInstanceTracking": True}.get)
        mat = fr.Material(family="uber")
        pb = pk.particle_block({}, 1, "uber")
        d = fr.FxRenderer._prep_mesh(fxr, r, mat, {}, 1, np.array([[9.0, 9.0, 9.0]]), pb, np.eye(4))
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0].name, "0:tracked:body")
        np.testing.assert_allclose(d[0].verts[:, 0:3], P, atol=1e-6)   # the particle's position is not added

    def test_without_tracked_meshes_nothing(self):
        fxr = SimpleNamespace(tracked=[], _mesh={}, notes=[])
        r = SimpleNamespace(index=0, layer="x", get={"ModelInstanceTracking": True}.get)
        self.assertIsNone(fr.FxRenderer._prep_mesh(fxr, r, fr.Material(family="uber"), {}, 1, np.zeros((1, 3)),
                                                   pk.particle_block({}, 1, "uber"), np.eye(4)))

    @unittest.skipUnless((CACHE / "hd/character/enemy/wraith1.json").exists(), "casc cache copy absent")
    def test_wraith_object_effect(self):
        from hdkit.view import scene, vfs
        fs = vfs.Layered(vfs.parse_roots(["cache=%s" % CACHE]))
        u = scene.load_unit(fs, "data/hd/character/enemy/wraith1.json")
        self.assertIn(("entity_torso", "data/hd/vfx/particles/character/enemy/wraith1/vfx_wraith1_objectFX.particles"),
                      u.tracked_fx)
        te = modeltrack.TrackedEffects(fs, u, fxcli.make_sim)
        self.assertEqual([p for p, _e, _s in te.items], ["entity_torso"])   # the empty left/right files add nothing


    @unittest.skipUnless((CACHE / "hd/character/enemy/wraith1.json").exists(), "casc cache copy absent")
    def test_wraith_sibling_upper_arm_meshes(self):
        from hdkit.fx import rspec
        from hdkit.view import fxbones, scene, vfs

        fs = vfs.Layered(vfs.parse_roots(["cache=%s" % CACHE]))
        unit_path = "data/hd/character/enemy/wraith1.json"
        unit = scene.load_unit(fs, unit_path)
        neutral = "data/hd/vfx/particles/character/enemy/wraith1/vfx_wraith1_neutral.particles"
        arms = []
        for path in fxcli._sibling_effects(fs, unit, neutral):
            renderers = rspec.load(fs.read(path)).renderers
            if not any("upperarm_glow" in (r.get("General.Mesh") or "").lower() for r in renderers):
                continue
            arms.extend(driver.layers[0].bone for _, driver in fxbones.bone_drivers(fs, unit_path, unit, path))
        self.assertCountEqual(arms, ["L_Forearm", "R_Forearm"])


class Flow(unittest.TestCase):
    def test_flow_vector(self):
        fx, fz = fr.water_flow_vector(SHRINE)
        self.assertAlmostEqual(fx, 0.0332, places=3)          # (2 x 0.212 - 1, 1) turned by 90 rad, x 0.5
        self.assertAlmostEqual(fz, 0.5759, places=3)

    def test_time_slots(self):
        w = fr.water_uniforms(SHRINE)
        ops, fres = fr.water_time_uniforms(w, 2.0)
        np.testing.assert_allclose(ops, [0.0332, 0.5759, 0.4, 0.0], atol=1e-3)
        np.testing.assert_allclose(fres, [-0.5, 0.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(fr.water_time_uniforms(w[:19], 2.0)[0], [0, 0, 0, 0])   # older 19-float tuples: still


class Preroll(unittest.TestCase):
    def test_mana_shrine_uses_authored_power_after_switch(self):
        from hdkit.view.fxplace import advance

        path = (CACHE / "hd/vfx/particles/objects/shrines_healthmana/mana_shrine_caves_act_1"
                / "vfx_mana_shrine_caves_act_1.particles")
        if not path.is_file():
            self.skipTest("casc cache copy of mana shrine absent")
        attrs = {"ObjectState": [1, 0, 0, 0]}
        sm = fxcli.make_sim(path.read_bytes(), 0, np.eye(4), attrs)
        switch = fxcli.preroll([("shrine", None, sm)], "ObjectState=0@3", attrs)
        for t, power in ((0.0, 0.0), (0.2, 138.02), (0.757, 800.0), (1.0, 0.0)):
            advance(sm, switch + t)
            lights = sm.state().lights
            self.assertEqual(len(lights), 1)
            self.assertAlmostEqual(float(lights[0]["power"][0]), power, delta=0.1)

    def test_switch(self):
        class Sim:
            def __init__(self):
                self.attrs_in, self.t, self.started, self.seen = {"ObjectState": [1, 0, 0, 0]}, 0.0, False, []

            def reset(self):
                self.t, self.started = 0.0, False

            def step(self, dt):
                self.started = True
                self.t += dt
                self.seen.append((round(self.t, 3), self.attrs_in.get("ObjectState", [None])[0]))

        sm = Sim()
        pre = fxcli.preroll([("e", None, sm)], "ObjectState=0@3", {"ObjectState": [1, 0, 0, 0]})
        self.assertEqual(pre, 3.0)
        self.assertEqual(sm.seen, [(3.0, 0)])
        self.assertEqual(sm.attrs_in["ObjectState"][0], 1)
        sm2 = Sim()
        sm2.attrs_in = {}
        fxcli.preroll([("e", None, sm2)], "ObjectState=0@2", None)
        self.assertNotIn("ObjectState", sm2.attrs_in)          # no --attr: back to the file's default


if __name__ == "__main__":
    unittest.main()
