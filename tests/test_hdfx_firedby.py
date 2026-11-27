"""Unit tests of hdkit/fx/firedby.py (what fires each HD particle effect: the reverse binding index), its
`fx_fired_by` API tool and the coverage report's fired section, on a synthetic data tree (no game files).
The last class checks a real index when HDFX_FIRED_BY names one (skipped otherwise).
Run: python -m unittest discover -s tests -p "test_hd*.py" """
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))                # the repository root
sys.path.insert(0, str(HERE))                           # tests/ (make_hdfx_fixtures)
import make_hdfx_fixtures as mk  # noqa: E402
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.fx import api  # noqa: E402
from hdkit.fx import coverage as cv  # noqa: E402
from hdkit.fx import firedby as fb  # noqa: E402

P = "data/hd/vfx/particles/"


def vfx(name, file, comp="VfxDefinitionComponent"):
    return {"name": name, "components": [{"type": comp, "name": name + "_c", "filename": P + file}]}


def unit(*ents, deps=()):
    return {"dependencies": {"particles": [{"path": P + d} for d in deps]}, "entities": list(ents)}


TREE = {
    # a missile row -> missiles.json -> hd/missiles/frost.json; the skill fires the row
    "global/excel/missiles.txt": "Missile\tExplosionMissile\tProgOverlay\nFrostNova\t\t\nFrostShard\t\t\n",
    "global/excel/skills.txt": "skill\tcltmissilea\tcastoverlay\nFrost Nova\tFrostNova\tFrostCast\n",
    "global/excel/overlay.txt": "overlay\tCharacter\nFrostCast\tSorceress\n",
    "global/excel/states.txt": "state\toverlay1\nchill\t\n",
    "global/excel/monstats.txt": "Id\nfallen1\nfallen2\n",
    "global/excel/objects.txt": "Class\tName\nWallTorch1\tDummy\n",
    "global/excel/lvlprest.txt": "Name\tFile1\tFile2\nAct 1 - Town 1\tAct1/Town/TownN1.ds1\t0\n",
    "global/excel/lvlwarp.txt": "Name\tId\nAct 1 Jail Down\t1\n",
    "hd/missiles/missiles.json": {"frostnova": "frost_nova", "frostshard": "null"},
    "hd/missiles/frost_nova.json": unit(vfx("entity_vfx", "missiles/frost/fx_frost_nova.particles"),
                                        deps=["missiles/frost/fx_frost_nova_preloaded.particles"]),
    "hd/overlays/sorceress/frostcast.json": unit(vfx("entity_root", "overlays/frost/fx_frost_cast.particles")),
    "hd/character/monsters.json": {"fallen1": "fallen1", "fallen2": "fallen1"},
    "hd/character/enemy/fallen1.json": unit(vfx("entity_torch", "character/enemy/fallen1/torch.particles",
                                                "ObjectEffectDefinitionComponent")),
    "hd/character/enemy/fallen1/animation/combined.timelines": {"timelines": {"death": {"animationEvents": [
        {"type": "SpawnVFX", "name": "ev1", "eventTimestamp": 0.1, "eventParams": [
            {"type": "VFXSourceAnimationEventParameter", "value": P + "character/enemy/fallen1/death.particles",
             "parameterName": "filename"},
            {"type": "BoneNameAnimationEventParameter", "value": "chest", "parameterName": "boneName"}]}]}}},
    "hd/objects/objects.json": {"wall_torch_1": {"asset_path": "braziers"}},
    "hd/objects/braziers/wall_torch_1.json": unit(vfx("vfx", "objects/braziers/fx_torch_flame.particles")),
    # a level preset with a prefab; a prop palette nobody names (an orphan)
    "hd/env/preset/act1/town/townn1.json": {"entities": [{"name": "p", "components": [
        {"type": "PrefabComponent", "name": "pf", "prefab": "data/hd/env/light/prefab/pf_candle.json"}]}]},
    "hd/env/light/prefab/pf_candle.json": unit(vfx("vfx", "env/act1/fx_candle_flame.particles")),
    "hd/env/preset/prop_palette/act1/props.json": unit(vfx("vfx", "env/act1/fx_ashembers_20x20.particles")),
    "hd/roomtiles/act_1_jail_down.json": unit(vfx("vfx", "objects/warp/fx_warp_glow.particles")),
    "hd/global/footprintdecals.json": {"parameters": [{"type": "FootprintParameter", "vfxFile": P + "character/common/fx_step.particles"}]},
    # relaxed JSON (comments, trailing comma) as the game ships some files
    "hd/global/excel/relaxed.json": '{\n // a comment\n "x": "data/hd/vfx/particles/env/fx_relaxed.particles",\n}\n',
}
CORPUS = [
    "missiles/frost/fx_frost_nova.particles", "missiles/frost/fx_frost_nova_preloaded.particles",
    "missiles/frost/fx_frost_nova_axe_hex.particles", "overlays/frost/fx_frost_cast.particles",
    "character/enemy/fallen1/torch.particles", "character/enemy/fallen1/death.particles",
    "objects/braziers/fx_torch_flame.particles", "env/act1/fx_candle_flame.particles",
    "env/act1/fx_ashembers_20x20.particles", "env/act1/fx_ashembers_10x10.particles", "objects/warp/fx_warp_glow.particles",
    "character/common/fx_step.particles", "env/fx_relaxed.particles", "objects/vfx_only/portal/vfx_portal.particles",
    "env/act2/fx_nowhere.particles",
]
EXTRA = ["hd/vfx/personal/someone/fx_candle_flame.particles", "hd/vfx/personal/someone/rnd_test.particles"]
EXE = b"\0data/hd/vfx/particles/objects/vfx_only/portal/vfx_portal.particles\0\0\0" + "ui/x.json".encode("utf-16-le")


def write_tree(root: Path) -> None:
    for lg, v in TREE.items():
        f = root / lg
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v if isinstance(v, str) else json.dumps(v), encoding="utf-8")


def build_index(root: Path, raw=None) -> dict:
    read, names = fb._folder_reader(root)
    corpus = ["hd/vfx/particles/" + c for c in CORPUS] + EXTRA
    return fb.build(read, names, fb.Tables.from_dir(root / "global" / "excel"), EXE, corpus, raw)


class Index(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        write_tree(cls.root)
        raw = [{"src": "casc:global/excel/levels.txt", "target": "hd/vfx/particles/env/act2/fx_nowhere.particles",
                "kind": "stem", "token": "fx_nowhere"}]
        cls.idx = build_index(cls.root, raw)
        cls.e = cls.idx["effects"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def st(self, name):
        return self.e["hd/vfx/particles/" + name]

    def test_statuses(self):
        self.assertEqual(self.idx["status"], {"bound": 9, "exe": 1, "manifest": 1, "orphan": 1, "unreferenced": 5})

    def test_missile_chain_reaches_the_skill(self):
        e = self.st("missiles/frost/fx_frost_nova.particles")
        self.assertEqual(e["status"], "bound")
        self.assertEqual(e["refs"][0]["trigger"], "entity")
        ch = e["chains"]["hd/missiles/frost_nova.json"]
        self.assertEqual(ch[0], ["hd/missiles/frost_nova.json", "missile FrostNova <- skill Frost Nova (cltmissilea)"])

    def test_overlay_chain_reaches_the_skill(self):
        ch = self.st("overlays/frost/fx_frost_cast.particles")["chains"]["hd/overlays/sorceress/frostcast.json"]
        self.assertEqual(ch[0][-1], "overlay FrostCast <- skill Frost Nova (castoverlay)")

    def test_unit_object_and_timeline(self):
        e = self.st("character/enemy/fallen1/torch.particles")
        self.assertEqual((e["status"], e["refs"][0]["trigger"]), ("bound", "object"))
        self.assertEqual(sorted(c[-1] for c in e["chains"]["hd/character/enemy/fallen1.json"]),
                         ["monster fallen1", "monster fallen2"])
        t = self.st("character/enemy/fallen1/death.particles")
        r = t["refs"][0]
        self.assertEqual((r["trigger"], r["mode"], r["bone"], r["comp"]), ("timeline", "death", "chest",
                                                                           "VFXSourceAnimationEventParameter"))
        tl = "hd/character/enemy/fallen1/animation/combined.timelines"
        self.assertEqual(t["chains"][tl][0], [tl, "hd/character/enemy/fallen1.json", "monster fallen1"])

    def test_object_level_warp_global_roots(self):
        o = self.st("objects/braziers/fx_torch_flame.particles")["chains"]["hd/objects/braziers/wall_torch_1.json"]
        self.assertEqual(o[0][-1], "object wall_torch_1 (objects.txt WallTorch1)")
        c = self.st("env/act1/fx_candle_flame.particles")["chains"]["hd/env/light/prefab/pf_candle.json"]
        self.assertEqual(c[0], ["hd/env/light/prefab/pf_candle.json", "hd/env/preset/act1/town/townn1.json",
                                "level Act 1 - Town 1"])
        w = self.st("objects/warp/fx_warp_glow.particles")["chains"]["hd/roomtiles/act_1_jail_down.json"]
        self.assertEqual(w[0][-1], "warp Act 1 Jail Down")
        g = self.st("character/common/fx_step.particles")
        self.assertEqual((g["status"], g["refs"][0]["trigger"]), ("bound", "global"))
        self.assertEqual(self.st("env/fx_relaxed.particles")["status"], "bound")       # relaxed JSON parsed

    def test_exe_manifest_orphan_unreferenced(self):
        self.assertEqual(self.st("objects/vfx_only/portal/vfx_portal.particles")["status"], "exe")
        m = self.st("missiles/frost/fx_frost_nova_preloaded.particles")
        self.assertEqual(m["status"], "manifest")
        self.assertTrue(m["evidence"][0].startswith("named only in preload lists"))
        o = self.st("env/act1/fx_ashembers_20x20.particles")
        self.assertEqual(o["status"], "orphan")
        self.assertIn("hd/env/preset/prop_palette/act1/props.json", o["evidence"][0])
        n = self.st("env/act2/fx_nowhere.particles")
        self.assertEqual(n["status"], "unreferenced")
        self.assertTrue(any(x.startswith("bare-name hits only") for x in n["evidence"]))
        self.assertIn("no definition, table or exe string names it", n["evidence"])

    def test_evidence_variant_copy_sibling_authoring(self):
        v = self.st("missiles/frost/fx_frost_nova_axe_hex.particles")["evidence"]
        self.assertTrue(any(x.startswith("variant: the bound hd/vfx/particles/missiles/frost/fx_frost_nova.particles")
                            for x in v), v)
        s = self.st("env/act1/fx_ashembers_10x10.particles")["evidence"]
        self.assertEqual(s, ["no definition, table or exe string names it"])         # its 20x20 sibling is an orphan
        c = self.e["hd/vfx/personal/someone/fx_candle_flame.particles"]["evidence"]
        self.assertEqual(c, ["authoring/test folder (hd/vfx/personal/)",
                             "copy: the same file name is bound at hd/vfx/particles/env/act1/fx_candle_flame.particles"])

    def test_sibling_rule(self):
        idx = {"effects": {
            "hd/vfx/particles/env/a/fx_haze_10x10.particles": {"status": "bound"},
            "hd/vfx/particles/env/a/fx_haze_20x20.particles": {"status": "unreferenced", "refs": []}}}
        ev = fb.evidence("hd/vfx/particles/env/a/fx_haze_20x20.particles",
                         idx["effects"]["hd/vfx/particles/env/a/fx_haze_20x20.particles"], idx["effects"], {}, [])
        self.assertEqual(ev, ["size/number sibling bound: hd/vfx/particles/env/a/fx_haze_10x10.particles"])

    def test_queries_and_report(self):
        f = fb.FiredBy(self.idx)
        self.assertEqual(f.of("fx_torch_flame")["path"], "hd/vfx/particles/objects/braziers/fx_torch_flame.particles")
        self.assertIsNone(f.of("fx_candle_flame"))                                    # ambiguous bare name
        self.assertEqual(f.of("Data\\HD\\vfx\\particles\\env\\act1\\FX_Candle_Flame.particles")["status"], "bound")
        self.assertEqual(f.summary("fx_frost_nova.particles"),
                         "entity hd/missiles/frost_nova.json -> missile FrostNova <- skill Frost Nova (cltmissilea)")
        self.assertTrue(f.summary("fx_nowhere").startswith("unreferenced: "))
        md = fb.markdown(self.idx, legacy={"hd/vfx/particles/env/act2/fx_nowhere.particles"})
        self.assertIn("## unreferenced (5)", md)
        self.assertIn("`hd/vfx/personal/someone/rnd_test.particles` (new since bind2)", md)
        self.assertNotIn("`hd/vfx/particles/env/act2/fx_nowhere.particles` (new", md)

    def test_scan_definition_and_helpers(self):
        refs, js = fb.scan_definition("hd/env/vis/x.json", {"entities": [{"name": "e", "components": [
            {"type": "VisualDataComponent", "name": "c", "screenspaceVfx": P + "env/vis/motes.particles",
             "biomeFilename": "data/hd/env/biome/b.json"}]}]})
        self.assertEqual((refs[0].trigger, refs[0].entity, sorted(js)), ("screenspace", "e", ["hd/env/biome/b.json"]))
        self.assertEqual(fb.logical("Data\\HD\\VFX\\A.particles"), "hd/vfx/a.particles")
        self.assertEqual(fb.snake("TorchAct2"), "torch_act_2")
        self.assertIsNone(fb.loads(b"{nope"))
        self.assertIn("ui/x.json", fb.exe_strings(EXE))


class Tool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        write_tree(root / "data")
        cls.index = root / "firedby.json"
        cls.index.write_text(json.dumps(build_index(root / "data")), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fx_fired_by(self):
        r = api.call("fx_fired_by", path="/some/root/hd/vfx/particles/missiles/frost/fx_frost_nova.particles",
                     index=str(self.index))
        self.assertTrue(r["ok"])
        self.assertEqual((r["status"], r["refs"][0]["src"]), ("bound", "hd/missiles/frost_nova.json"))
        self.assertIn("skill Frost Nova", r["summary"])
        self.assertEqual(api.call("fx_fired_by", path="fx_none", index=str(self.index))["error"]["code"], "not_found")
        old = os.environ.pop("HDFX_FIRED_BY", None)
        try:
            self.assertEqual(api.call("fx_fired_by", path="x")["error"]["code"], "not_found")
        finally:
            if old is not None:
                os.environ["HDFX_FIRED_BY"] = old

    def test_coverage_fired_section(self):
        idx = {"effects": {"hd/vfx/particles/synth/billboard_blend.particles": {
            "path": "hd/vfx/particles/synth/billboard_blend.particles", "status": "unreferenced", "refs": [],
            "chains": {}, "evidence": ["no definition, table or exe string names it"]}}}
        f = Path(self.tmp.name) / "cov_index.json"
        f.write_text(json.dumps(idx), encoding="utf-8")
        rep = cv.run(mk.FIXTURES, fired=f)
        row = next(r for r in rep["rows"] if r["path"].endswith("billboard_blend.particles"))
        self.assertEqual(row["fired_by"]["status"], "unreferenced")
        self.assertEqual(rep["fired"]["unreferenced"], 1)
        self.assertEqual(rep["fired"]["unknown"], rep["surveyed"] - 1)
        md = cv.markdown(rep)
        self.assertIn("## What fires each effect", md)
        self.assertIn("unreferenced: no definition, table or exe string names it", md)
        self.assertNotIn("What fires", cv.markdown(cv.run(mk.FIXTURES)))


@unittest.skipUnless(os.environ.get("HDFX_FIRED_BY") and Path(os.environ["HDFX_FIRED_BY"]).is_file(),
                     "HDFX_FIRED_BY names no index (build one from the game data)")
class RealIndex(unittest.TestCase):
    """The index built from build 95032's data: a few bindings checked against what the research lanes found."""

    @classmethod
    def setUpClass(cls):
        cls.f = fb.FiredBy.load(os.environ["HDFX_FIRED_BY"])

    def test_known_bindings(self):
        m = self.f.of("hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles")
        self.assertEqual(m["status"], "bound")
        self.assertIn(["hd/overlays/monster/mephisto.json", "hd/character/enemy/mephisto.json", "monster ubermephisto"],
                      m["chains"]["hd/overlays/monster/mephisto.json"])
        self.assertIn("skill Frost Nova", self.f.summary("hd/vfx/particles/missiles/frost_nova/fx_ice_frost_nova.particles"))
        self.assertEqual(self.f.of("hd/vfx/particles/objects/vfx_only/town_portal/vfx_town_portal.particles")["status"], "exe")

    def test_known_unused(self):
        b = self.f.of("hd/vfx/particles/missiles/bladedance/hex_bane/vfx_bladedance_axe_missile_hex_bane.particles")
        self.assertEqual(b["status"], "unreferenced")
        self.assertTrue(any("vfx_bladedance_missile.particles" in x for x in b["evidence"]))
        p = self.f.of("hd/vfx/personal/nfrost/lightningspire/fx_lightningspire_ambient_ball.particles")
        self.assertTrue(any(x.startswith("copy:") for x in p["evidence"]))


if __name__ == "__main__":
    unittest.main()
