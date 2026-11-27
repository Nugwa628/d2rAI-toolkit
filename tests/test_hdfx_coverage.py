"""Unit tests of hdkit/fx/coverage.py (the per-effect survey of D2R's HD particle files) on the synthetic files of
tests/fixtures/hdfx/ (built by make_hdfx_fixtures.py; no game files).
Run: python -m unittest discover -s tests -p "test_hd*.py" """
import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))                # the repository root
sys.path.insert(0, str(HERE))                           # tests/ (make_hdfx_fixtures)
import make_hdfx_fixtures as mk  # noqa: E402
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.fx import coverage as cv  # noqa: E402

FIXTURES = mk.FIXTURES


def rows_by_name(rep: dict) -> dict:
    return {Path(r["path"]).name: r for r in rep["rows"]}


class FixtureFiles(unittest.TestCase):
    def test_folder_is_current(self):
        """The committed fixtures are what make_hdfx_fixtures.py builds (rerun it after a change)."""
        for name, data in mk.build().items():
            self.assertEqual((FIXTURES / name).read_bytes(), data, name)


class Survey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = ("SUPPORTED_RENDER_KINDS", "SUPPORTED_RENDER_FEATURES", "SUPPORTED_MATERIALS", "SUPPORTED_OPCODES",
                 "SUPPORTED_CALLS", "SUPPORTED_SAMPLERS")
        with contextlib.ExitStack() as st:        # the baseline: nothing supported (lanes fill the sets at import)
            for n in names:
                st.enter_context(mock.patch.object(cv, n, set()))
            cls.rep = cv.run(FIXTURES)
        cls.rows = rows_by_name(cls.rep)

    def test_counts_and_errors(self):
        self.assertEqual(self.rep["files"], 5)
        self.assertEqual(self.rep["surveyed"], 4)
        self.assertEqual([e["path"] for e in self.rep["errors"]], ["hd/vfx/particles/synth/bad_magic.particles"])
        self.assertIn("not a D2R particle file", self.rep["errors"][0]["error"])

    def test_billboard(self):
        r = self.rows["billboard_blend.particles"]
        self.assertEqual(r["render"]["kinds"], {"billboard": 1})
        self.assertEqual(r["render"]["materials"], {"vfx/materials/Diablo_Uber.pkma": 1})
        self.assertEqual(r["render"]["features"], {"Blend": 1})      # AlphaMask is off; EnableRendering is not a feature
        self.assertEqual(r["samplers"], {"curve": 1})
        self.assertEqual(r["vm"]["opcodes"], {"ld": 1, "call": 1, "add": 1, "st": 1, "eplg": 1})
        self.assertEqual(r["vm"]["calls"], {"rand": 1})

    def test_mesh(self):
        r = self.rows["mesh_uber.particles"]
        self.assertEqual(r["render"]["kinds"], {"mesh": 1})
        self.assertEqual(sorted(r["render"]["features"]), ["Blend", "Fresnel", "SoftParticles"])
        self.assertEqual(r["samplers"], {"turbulence": 1})
        self.assertEqual(r["vm"]["calls"], {"xform.l2w": 1, "effect.age": 1})
        self.assertEqual(r["scripts"], 1)

    def test_light_and_decal(self):
        r = self.rows["light_decal.particles"]
        self.assertEqual(r["render"]["kinds"], {"light": 1, "decal": 1})   # kind 5 with a decal material is a decal
        self.assertEqual(r["render"]["features"], {"Blend": 1})            # the decal's switch is off except Blend
        self.assertEqual(r["samplers"], {"curve": 1, "shape": 1})
        self.assertEqual(r["vm"]["calls"], {})

    def test_no_renderers(self):
        r = self.rows["no_renderers.particles"]
        self.assertEqual((r["renderers"], r["render"]["kinds"]), (0, {}))
        self.assertEqual(r["samplers"], {"text": 1})
        self.assertEqual(r["status"], "not")

    def test_missing_lists_every_need_while_sets_are_empty(self):
        r = self.rows["billboard_blend.particles"]
        self.assertEqual(r["needs"], len(r["missing"]))
        self.assertIn("feature:Blend", r["missing"])
        self.assertIn("call:rand", r["missing"])
        self.assertEqual(self.rep["status"], {"drawn": 0, "partly": 0, "not": 4, "empty": 0, "unreferenced": 0})


class Status(unittest.TestCase):
    def status_with(self, **sets) -> dict:
        names = {"render_kinds": "SUPPORTED_RENDER_KINDS", "render_features": "SUPPORTED_RENDER_FEATURES",
                 "materials": "SUPPORTED_MATERIALS", "opcodes": "SUPPORTED_OPCODES", "calls": "SUPPORTED_CALLS",
                 "samplers": "SUPPORTED_SAMPLERS"}
        patches = [mock.patch.object(cv, names[k], set(v)) for k, v in sets.items()]
        for p in patches:
            p.start()
        try:
            rep = cv.run(FIXTURES)
        finally:
            for p in patches:
                p.stop()
        return rows_by_name(rep)

    def test_drawn_partly_not(self):
        ops = ["ld", "call", "add", "st", "eplg"]
        rows = self.status_with(render_kinds=["billboard"], render_features=["Blend"], opcodes=ops,
                                calls=["rand"], samplers=["curve"], materials=["vfx/materials/Diablo_Uber.pkma"])
        self.assertEqual(rows["billboard_blend.particles"]["status"], "drawn")
        self.assertEqual(rows["billboard_blend.particles"]["missing"], [])
        self.assertEqual(rows["mesh_uber.particles"]["status"], "partly")       # no mesh kind; `mul` not listed
        self.assertIn("kind:mesh", rows["mesh_uber.particles"]["missing"])
        self.assertEqual(rows["light_decal.particles"]["status"], "partly")     # curve sampler, ld/st/eplg listed
        self.assertEqual(rows["no_renderers.particles"]["status"], "partly")    # text sampler and mul unlisted
        self.assertNotIn("op:ld", rows["no_renderers.particles"]["missing"])


class Ranking(unittest.TestCase):
    def test_uses_count_distinct_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.json").write_text('{"model": "hd/vfx/particles/missiles/x/vfx_a.particles"}')
            (d / "b.tsv").write_text("hd/vfx/particles/missiles/x/vfx_a.particles\n"
                                     "hd/vfx/particles/missiles/x/vfx_a.particles\n")
            (d / "c.bin").write_bytes(b"hd/vfx/particles/missiles/x/vfx_a.particles")   # not a text file: ignored
            counts = cv.count_refs([d])
            self.assertEqual(counts["hd/vfx/particles/missiles/x/vfx_a.particles"], 2)

    def test_run_ranks_by_uses(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docs.md").write_text("see hd/vfx/particles/synth/mesh_uber.particles")
            rep = cv.run(FIXTURES, refs=[d])
            self.assertEqual(rep["rows"][0]["path"], "hd/vfx/particles/synth/mesh_uber.particles")
            self.assertEqual(rep["rows"][0]["uses"], 1)


class Cli(unittest.TestCase):
    def test_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cv.main([str(FIXTURES), "--out", d]), 0)
            rep = json.loads((Path(d) / "coverage.json").read_text(encoding="utf-8"))
            self.assertEqual(rep["surveyed"], 4)
            md = (Path(d) / "coverage.md").read_text(encoding="utf-8")
            self.assertIn("| `hd/vfx/particles/synth/mesh_uber.particles` |", md)
            self.assertIn("Not particle files", md)


if __name__ == "__main__":
    unittest.main()
