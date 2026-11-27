"""Selected mesh-less object effects and their coverage."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import make_hdfx_fixtures as mk
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np
from hdkit.fx import coverage, firedby, modeltrack
from hdkit.view import gpu, scene, vfs
from hdkit.view.scene import Part, Unit


class TrackedMeshes(unittest.TestCase):
    def test_pose_selected_by_unit_part_or_object_effect(self):
        unit = Unit("caster", [Part("body", "", [], None, {}, unit_part="torso"),
                               Part("left hand", "", [], None, {}, unit_part="lhand"),
                               Part("right hand", "", [], None, {}, unit_part="rhand")],
                    tracked_fx=[("body", "data/hd/glow.particles")])
        posed = {"body": ["torso mesh"], "left hand": ["left mesh"], "right hand": ["right mesh"]}
        self.assertEqual(modeltrack.posed_meshes(unit, posed, "RHAND"), ["right mesh"])
        self.assertEqual(modeltrack.posed_meshes(unit, posed, "special7"), [])
        self.assertEqual(modeltrack.posed_meshes(unit, posed, effect="HD/glow.particles"), ["torso mesh"])
        self.assertEqual(modeltrack.posed_meshes(unit, posed), ["torso mesh", "left mesh", "right mesh"])


    def test_selected_entity_supplies_geometry_without_an_opaque_shell(self):
        effect = "hd/vfx/particles/shine.particles"
        doc = {"entities": [
            {"name": "body", "components": [{"type": "ModelDefinitionComponent", "filename": "body.model"}]},
            {"name": "shell", "components": [{"type": "ModelDefinitionComponent", "filename": "shell.model"},
                                             {"type": "ObjectEffectDefinitionComponent", "filename": effect}]},
        ]}
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "unit.json").write_text(json.dumps(doc))
            fs = vfs.Layered([("fixture", Path(d))])
            with mock.patch.object(scene, "load_model_part",
                                   side_effect=lambda _fs, model, name, _lod: Part(name, model, [], None, {})):
                unit = scene.load_unit(fs, "unit.json", track_effect=effect)
        self.assertEqual([(p.name, p.track_only) for p in unit.parts], [("body", False), ("shell", True)])
        self.assertEqual(unit.tracked_fx, [("shell", effect)])
        self.assertEqual(modeltrack.posed_meshes(unit, {"body": [1], "shell": [2]}, effect=effect), [2])


class TrackingCoverage(unittest.TestCase):
    effect = "hd/vfx/particles/glow.particles"
    unit = "hd/character/enemy/caster.json"
    timeline = "hd/character/enemy/caster/animation/combined.timelines"

    def survey(self, explicit_mesh=False):
        b = mk.Builder()
        props = [b.prop("EnableRendering", True), b.prop("ModelInstanceTracking", True)]
        if explicit_mesh:
            props.append(b.obj(mk.hp.PROPERTY, [(0, mk.u32(b.s("General.Mesh"))), (1, mk.u32(mk.hp.T_MESH)),
                                                 (3, mk.u32(b.s("data/hd/test.model")))]))
        b.renderer(2, "Diablo_Uber_Mesh", props)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / Path(self.effect).name
            f.write_bytes(b.data())
            return coverage.survey_file(f, self.effect)

    def index(self, src, **ref):
        chain = [src, self.unit, "monster caster"] if src == self.timeline else [src, "monster caster"]
        return firedby.FiredBy({"effects": {self.effect: {
            "refs": [{"src": src, **ref}], "chains": {src: [chain]},
        }}})

    def test_tracking_without_context_is_partly(self):
        row = self.survey()
        self.assertEqual(coverage.assess(row)["status"], "partly")
        self.assertEqual(row["missing"], ["feature:ModelInstanceTracking"])
        preload = self.index(self.timeline, trigger="manifest")
        self.assertEqual(coverage.assess(row, preload)["status"], "partly")

    def test_tracking_with_object_or_timeline_context_is_drawn(self):
        obj = self.index(self.unit, trigger="object", entity="body")
        tl = self.index(self.timeline, trigger="timeline", comp="ObjectEffectSourceAnimationEventParameter",
                        mode="skill1", bone="rhand")
        self.assertEqual(coverage.assess(self.survey(), obj)["status"], "drawn")
        row = coverage.assess(self.survey(), tl)
        self.assertEqual(row["status"], "drawn")
        self.assertEqual(row["tracking_contexts"], [{"unit": self.unit, "timeline": "skill1", "part": "rhand"}])

    def test_mesh_with_its_own_geometry_needs_no_context(self):
        self.assertEqual(coverage.assess(self.survey(explicit_mesh=True))["status"], "drawn")

    def test_indexed_item_supplies_geometry_only_for_live_object_binding(self):
        item = "hd/items/weapon/sword/sword.json"
        chain = [item, self.unit, "monster caster"]
        index = firedby.FiredBy({"effects": {self.effect: {
            "refs": [{"src": item, "trigger": "object", "entity": "root"}],
            "chains": {item: [chain]}, "status": "bound",
        }}})
        row = coverage.assess(self.survey(), index)
        self.assertEqual(row["status"], "drawn")
        self.assertEqual(row["tracking_contexts"], [{"unit": self.unit, "item": item, "entity": "root"}])
        index.effects[self.effect]["chains"][item] = []
        self.assertEqual(coverage.assess(self.survey(), index)["status"], "partly")

    def test_unreferenced_requires_an_exact_index_entry(self):
        index = firedby.FiredBy({"effects": {self.effect: {"status": "unreferenced"}}})
        self.assertEqual(coverage.assess(self.survey(), index)["status"], "unreferenced")
        self.assertEqual(coverage.assess(self.survey(explicit_mesh=True), index)["status"], "drawn")
        row = self.survey()
        row["path"] = "hd/vfx/particles/copied/glow.particles"
        self.assertEqual(coverage.assess(row, index)["status"], "partly")


class EquipmentTracking(unittest.TestCase):
    def test_root_and_model_bindings_follow_the_wearers_pose(self):
        item = "hd/items/armor/helmet/helm.json"
        root_fx, model_fx = "hd/vfx/root.particles", "hd/vfx/model.particles"
        wearer = {"entities": [
            {"name": "body", "components": [{"type": "ModelDefinitionComponent", "filename": "body.model"}]},
            {"name": "head", "components": [{
                "type": "UnitAttachmentComponent", "filename": item, "overrideBoneName": "HELMET",
                "part": "head", "attachmentTransform": {"translation": {"y": 2}, "scale": {"x": 2}},
            }]},
        ]}
        helm = {"entities": [
            {"name": "root", "components": [{"type": "UnitRootComponent"},
                                            {"type": "ObjectEffectDefinitionComponent", "filename": root_fx}]},
            {"name": "mesh", "components": [{"type": "ModelDefinitionComponent", "filename": "helm.model"},
                                            {"type": "ObjectEffectDefinitionComponent", "filename": model_fx}]},
        ]}
        sk = SimpleNamespace(idx={"helmet": 0})
        mesh = SimpleNamespace(name="helm", joints=None, pos=np.array([[1., 0., 0.]]),
                               nrm=np.array([[1., 1., 0.]]) / np.sqrt(2), tan=np.array([[0., 0., 1., 1.]]),
                               tris=[], maps={})

        def load_part(_fs, model, name, _lod):
            if model == "body.model":
                return Part(name, model, [], sk, {})
            return Part(name, model, [mesh], None, {})

        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "wearer.json").write_text(json.dumps(wearer))
            (folder / item).parent.mkdir(parents=True)
            (folder / item).write_text(json.dumps(helm))
            fs = vfs.Layered([("fixture", folder)])
            with mock.patch.object(scene, "load_model_part", side_effect=load_part):
                unit = scene.load_unit(fs, "wearer.json", track_effect=root_fx)
                standalone = scene.load_unit(fs, item, track_effect=model_fx)
        self.assertEqual(unit.tracked_fx, [("head:mesh", root_fx), ("head:mesh", model_fx)])
        self.assertEqual(standalone.tracked_fx, [("mesh", root_fx), ("mesh", model_fx)])
        self.assertFalse(unit.parts[1].track_only)

        renderer = gpu.Renderer.__new__(gpu.Renderer)
        renderer._skinned = {}
        renderer._prepare_draw = mock.Mock()
        world = np.eye(4)[None]
        pose = SimpleNamespace(worlds=world, clip="walk", time=0., tracks=1, unsupported={})
        for height in (3, 7):
            world[0, 1, 3] = height
            renderer.prepare(unit, poser=lambda _sk: pose)
            tracked = modeltrack.posed_meshes(unit, renderer.last_posed, effect=root_fx)
            self.assertEqual(modeltrack.posed_meshes(unit, renderer.last_posed, part="head"), tracked)
            self.assertEqual(len(tracked), 1)
            self.assertIs(tracked[0][0], mesh)
            np.testing.assert_allclose(tracked[0][1], [[2, height + 2, 0]])
            np.testing.assert_allclose(tracked[0][2], [[1 / np.sqrt(5), 2 / np.sqrt(5), 0]])

    def test_an_item_root_may_also_be_its_model_entity(self):
        item = "hd/items/armor/helmet/helm.json"
        effect = "hd/vfx/helm.particles"
        doc = {"entities": [{"name": "root", "components": [
            {"type": "UnitRootComponent"},
            {"type": "ModelDefinitionComponent", "filename": "helm.model"},
            {"type": "ObjectEffectDefinitionComponent", "filename": effect},
        ]}]}
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / item).parent.mkdir(parents=True)
            (folder / item).write_text(json.dumps(doc))
            fs = vfs.Layered([("fixture", folder)])
            with mock.patch.object(scene, "load_model_part", return_value=Part("root", "helm.model", [], None, {})):
                unit = scene.load_unit(fs, item)
        self.assertEqual(unit.tracked_fx, [("root", effect)])
