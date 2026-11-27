"""Lane hdfx-r3-shapes: AlphaRemap.Dissolve in the Uber path, effects on moving unit bones (view/fxbones.py), units whose
every model entity carries an object effect (scene.load_unit). No game files needed; the corpus checks skip without it."""
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit.fx import context as C
from hdkit.fx import render, rspec
from hdkit.view import fxbones, scene

CORPUS = Path(os.environ.get("HDFX_CORPUS") or "/nonexistent")
PAINWORM = CORPUS / "hd/vfx/particles/character/enemy/painworm1/vfx_painworm1_chest_glow.particles"


def _uber(dissolve, bound=True, kind="mesh", material="vfx/materials/Diablo_Uber_Mesh.pkma"):
    props = {"AlphaRemap": True, "AlphaRemap.Dissolve": dissolve, "AlphaRemap.AlphaMap": "noise.texture",
             "Transparency": True, "Transparency.Type": 0}
    ins = {"AlphaRemap.AlphaCursor": rspec.Input("AlphaRemap.AlphaCursor", 1, "float2")} if bound else \
        {"Color.Color": rspec.Input("Color.Color", 2, "float4")}
    return rspec.RSpec(0, 0, kind, material, True, "L", props, ins)


def _draw(mat):
    return render.FxDraw("mesh", 0, "L", mat, np.zeros((0, 62), np.float32))


class DissolveTest(unittest.TestCase):
    def test_flag_follows_keyword_and_property(self):
        self.assertTrue(render.Material.of(_uber(True)).dissolve)
        self.assertFalse(render.Material.of(_uber(False)).dissolve)
        self.assertFalse(render.Material.of(_uber(True, bound=False)).dissolve)    # no ALPHA_REMAP permutation
        self.assertTrue(render.Material.of(_uber(True, kind="billboard", material="vfx/materials/Diablo_Uber.pkma")).dissolve)

    def test_uniform_lq2_y(self):
        post = render.Post()
        on = render.mats_uniforms(_draw(render.Material.of(_uber(True))), None, post)
        off = render.mats_uniforms(_draw(render.Material.of(_uber(False))), None, post)
        self.assertEqual(on[25], 1.0)
        self.assertEqual(off[25], 0.0)

    def test_shader_has_dissolve_branch(self):
        src = (Path(render.__file__).parents[1] / "view" / "fx.wgsl").read_text(encoding="utf-8")
        i = src.index("fn uber(")
        body = src[i:src.index("fn finish(", i)]
        self.assertIn("u.lq2.y > 0.5", body)
        self.assertIn("i.so_x", body)
        self.assertIn("- i.cur.x", body)

    @unittest.skipUnless(PAINWORM.exists(), "corpus 95032 absent")
    def test_painworm_chest_glow_is_dissolve(self):
        eff = rspec.load(PAINWORM.read_bytes())
        m = render.Material.of(eff.renderers[0])
        self.assertTrue(m.dissolve)
        self.assertEqual(m.blend, 1)


class _Sk:
    """A two-bone stand-in skeleton (index by name) for ClipPoses / BoneDriver."""
    idx = {"root": 0, "chest": 1}


def _trans(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


class BoneDriverTest(unittest.TestCase):
    def _poses(self):
        # the chest bone rises 1 unit per clip second from y = 2
        def poser(sk, ct):
            return SimpleNamespace(worlds=np.stack([np.eye(4), _trans(0.0, 2.0 + ct, 0.0)]))
        return fxbones.ClipPoses(_Sk(), None, t0=0.5, rate=2.0, poser=poser)

    def test_clip_time_and_cache(self):
        p = self._poses()
        self.assertAlmostEqual(p.clip_time(1.0), 2.5)
        self.assertAlmostEqual(p.worlds(1.0)[1][1, 3], 4.5)
        self.assertIs(p.worlds(1.0), p.worlds(1.0))
        self.assertEqual(p.index("CHEST"), 1)

    def test_world_matches_unit_draw(self):
        # world = outer x rot_y(yaw) x root scale x bone world x entity transform (as gpu.Renderer.prepare draws the unit)
        p = self._poses()
        lay = [C.Layer("fx.particles", _trans(0.0, 0.0, 1.0), "chest")]
        outer = _trans(0.0, 3.0, 0.0)
        d = fxbones.BoneDriver(lay, p, unit_scale=2.0, outer=outer, yaw=90.0)
        np.testing.assert_allclose(d.place, np.eye(4))           # fxcli.drive_to left-multiplies place
        M = d.layer_transform(0.0, lay[0])
        R = np.eye(4)
        R[:3, :3] = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]) * 2.0
        np.testing.assert_allclose(M, outer @ R @ _trans(0.0, 2.5, 0.0) @ _trans(0.0, 0.0, 1.0), atol=1e-9)
        # the effect moves with the bone, and the sim context carries that motion as velocity
        self.assertGreater(d.layer_transform(0.25, lay[0])[1, 3], M[1, 3])
        ctx = d.context(0.25, 0)
        self.assertAlmostEqual(float(ctx.velocity[1]), 4.0, places=3)    # 1 unit/clip s x rate 2 x scale 2

    def test_bone_drivers_from_bindings(self):
        sk = _Sk()
        unit = SimpleNamespace(parts=[SimpleNamespace(skeleton=sk)], root_scale=1.0)
        binds = [C.Binding("vfx", file="data/hd/vfx/a.particles", entity="e_chest", bone="chest", xf=np.eye(4)),
                 C.Binding("vfx", file="data/hd/vfx/a.particles", entity="e_tail", bone="tail_missing", xf=np.eye(4)),
                 C.Binding("vfx", file="data/hd/vfx/a.particles", entity="e_root", bone="root", xf=np.eye(4))]
        poser = lambda s, ct: SimpleNamespace(worlds=np.stack([np.eye(4), _trans(0, 2, 0)]))   # noqa: E731
        with mock.patch.object(fxbones, "bindings_for", return_value=binds):
            ds = fxbones.bone_drivers(None, "u.json", unit, "data/hd/vfx/a.particles", poser=poser)
        self.assertEqual([nm for nm, _d in ds], ["e_chest@chest", "e_tail@tail_missing", "e_root@root"])
        self.assertAlmostEqual(ds[0][1].layer_transform(0.0, ds[0][1].layers[0])[1, 3], 2.0)
        self.assertEqual(ds[1][1].skipped, ["tail_missing"])                 # unknown bone: the unit frame
        self.assertAlmostEqual(ds[1][1].layer_transform(0.0, ds[1][1].layers[0])[1, 3], 0.0)
        self.assertIsNone(ds[2][1].layers[0].bone)

    def test_no_skeleton_no_drivers(self):
        unit = SimpleNamespace(parts=[SimpleNamespace(skeleton=None)], root_scale=1.0)
        self.assertEqual(fxbones.bone_drivers(None, "u.json", unit, "x.particles"), [])


def _ent(name, *comps):
    return {"name": name, "components": [dict(c) for c in comps]}


class _FS:
    def __init__(self, files):
        self.files = files

    def read(self, p):
        return self.files[p]

    def exists(self, p):
        return p in self.files


class ObjectEffectBodyTest(unittest.TestCase):
    def _load(self, ents):
        fs = _FS({"u.json": json.dumps({"name": "u", "entities": ents}).encode()})
        loaded = []

        def fake_part(vfs, logical, name="model", lod=0):
            loaded.append(logical)
            return scene.Part(name, logical, [], None, {})
        with mock.patch.object(scene, "load_model_part", side_effect=fake_part), \
                mock.patch.object(scene, "pick_layer_textures", return_value=([], None, None)):
            u = scene.load_unit(fs, "u.json")
        return u, loaded

    def test_wraith_like_unit_draws_its_object_effect_models(self):
        oe = {"type": "ObjectEffectDefinitionComponent", "filename": "fx.particles"}
        u, loaded = self._load([
            _ent("torso", {"type": "UnitPartComponent"}, {"type": "ModelDefinitionComponent", "filename": "torso.model"}, oe),
            _ent("shell", {"type": "ModelDefinitionComponent", "filename": "shell.model"}, oe)])
        self.assertEqual(loaded, ["torso.model"])                  # the UnitPart one; the bare shell stays an effect
        self.assertEqual([p.name for p in u.parts], ["torso"])
        self.assertTrue(any("drawn as the body" in n for n in u.notes))

    def test_shell_named_entities_stay_effects(self):
        oe = {"type": "ObjectEffectDefinitionComponent", "filename": "fx.particles"}
        part = {"type": "UnitPartComponent"}
        u, loaded = self._load([
            _ent("entity_torso", part, {"type": "ModelDefinitionComponent", "filename": "torso.model"}, oe),
            _ent("entity_topWingsShell", part, {"type": "ModelDefinitionComponent", "filename": "wshell.model"}, oe)])
        self.assertEqual(loaded, ["torso.model"])

    def test_units_with_plain_parts_keep_skipping_shells(self):
        oe = {"type": "ObjectEffectDefinitionComponent", "filename": "fx.particles"}
        u, loaded = self._load([
            _ent("body", {"type": "UnitPartComponent"}, {"type": "ModelDefinitionComponent", "filename": "body.model"}),
            _ent("shell", {"type": "UnitPartComponent"}, {"type": "ModelDefinitionComponent", "filename": "shell.model"}, oe)])
        self.assertEqual(loaded, ["body.model"])
        self.assertFalse(any("drawn as the body" in n for n in u.notes))


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1", "GPU tests off (HDFX_GPU_TESTS=1)")
class DissolveGpuTest(unittest.TestCase):
    @unittest.skipUnless(PAINWORM.exists(), "corpus 95032 absent")
    def test_painworm_glow_is_soft(self):
        """The chest glow's alpha falls off from the disc centre (dissolve f = 1 with cursor -1): before the fix the
        AlphaMap lookup made it a uniform hard disc."""
        from hdkit.fx.sim import Sim
        from hdkit.view import camera, gpu, vfs
        fs = vfs.Layered(vfs.parse_roots(["d2r=%s" % CORPUS]))
        eff = rspec.load(PAINWORM.read_bytes())
        sim = Sim(str(PAINWORM), seed=0)
        sim.seek(1.5)
        R = gpu.Renderer()
        fxr = render.FxRenderer(R, fs, render.Post())
        cam = camera.OrthoCamera(k=camera.K_1600x900 * 2.0)   # the game view (el 90 gave a degenerate basis: nothing drawn)
        img = fxr.render(fxr.prepare(eff, sim.state(), cam), cam, (256, 256), linear=True)
        lum = np.asarray(img, float)[..., :3].max(-1)
        lit = lum > lum.max() * 0.02
        self.assertGreater(lit.sum(), 50)
        self.assertLess(np.percentile(lum[lit], 50), 0.5 * lum.max())     # a soft falloff, not a flat plateau


if __name__ == "__main__":
    unittest.main()
