"""hdkit.fx.calib.loadtest (the save -> in-game load test, general part): logical
paths, laying files into a mod's data folder, effect ages of a bench, tint statistics, the pair picture, and the stdio MCP
edit chain on a synthetic effect. No game files, no slot, no GPU.
Run: python -m unittest discover -s tests -p "test_hdfx_loadtest.py" """
import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np
from PIL import Image

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hdkit.fx.calib import loadtest as lt  # noqa: E402

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

LOGICAL = "hd/vfx/particles/test/fx_test.particles"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        self.addCleanup(self.td.cleanup)


class Files(Tmp):
    def test_logical_of_file(self):
        f = self.d / "mod" / "data" / "hd" / "vfx" / "x.particles"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x")
        self.assertEqual(lt.logical_of_file(f), "hd/vfx/x.particles")
        deep = self.d / "data" / "other" / "data" / "hd" / "a.particles"      # the last `data` folder counts
        deep.parent.mkdir(parents=True)
        deep.write_bytes(b"x")
        self.assertEqual(lt.logical_of_file(deep), "hd/a.particles")
        with self.assertRaises(ValueError):
            lt.logical_of_file(self.d / "loose.particles")

    def test_logical_of_session(self):
        (self.d / "sess").mkdir()
        (self.d / "sess" / "session.json").write_text(json.dumps({"logical": "hd/x.particles"}))
        self.assertEqual(lt.logical_of_file(self.d / "sess"), "hd/x.particles")

    def test_lay_files_stays_inside_the_game_folder(self):
        game = self.d / "game"
        data = game / "mods" / "m" / "m.mpq" / "data"
        data.mkdir(parents=True)
        src = self.d / "edited.particles"
        src.write_bytes(b"edited")
        done = lt.lay_files({"hd/vfx/e.particles": src}, data, game)
        self.assertEqual((data / "hd/vfx/e.particles").read_bytes(), b"edited")
        self.assertEqual(done[0]["sha256"], lt.sha256(src))
        self.assertIsNone(done[0]["before"])
        (data / "hd/vfx/e.particles").write_bytes(b"old")
        again = lt.lay_files({"hd/vfx/e.particles": src}, data, game)
        self.assertEqual(again[0]["before"], hashlib.sha256(b"old").hexdigest())
        for bad in ("../../../../escape.particles", "/abs/x.particles", "hd/../../../../../x"):
            with self.assertRaises(RuntimeError):
                lt.lay_files({bad: src}, data, game)
        self.assertFalse((self.d / "escape.particles").exists())
        with self.assertRaises(RuntimeError):
            lt.lay_files({"a": src}, game / "no_such_data", game)

    def test_lay_files_refuses_a_symlink_out_of_the_game(self):
        game = self.d / "game"
        data = game / "data"
        data.mkdir(parents=True)
        outside = self.d / "outside"
        outside.mkdir()
        (data / "link").symlink_to(outside)
        src = self.d / "e.particles"
        src.write_bytes(b"x")
        with self.assertRaises(RuntimeError):
            lt.lay_files({"link/e.particles": src}, data, game)
        self.assertEqual(list(outside.iterdir()), [])


class Numbers(Tmp):
    def test_shot_ages(self):
        bench = {"frames_at_spawn": 100, "clock": [[0.0, 90], [1.0, 115], [2.0, 165]],
                 "shots": [{"t": 0.0}, {"t": 1.0}, {"t": 1.5}]}
        a = lt.shot_ages(bench)
        self.assertAlmostEqual(a[0], -10 / 25)
        self.assertAlmostEqual(a[1], 15 / 25)
        self.assertAlmostEqual(a[2], 40 / 25)

    def test_tint_stats_sees_green(self):
        twin = np.full((20, 20, 3), 40, np.uint8)
        green = twin.copy()
        green[5:10, 5:10] = (40, 200, 40)
        grey = twin.copy()
        grey[5:10, 5:10] = (150, 150, 150)
        sg, sw = lt.tint_stats(green, twin), lt.tint_stats(grey, twin)
        self.assertEqual((sg["pixels"], sw["pixels"]), (25, 25))
        self.assertGreater(sg["green_excess"], 100)
        self.assertEqual(sw["green_excess"], 0)
        self.assertEqual(lt.tint_stats(twin, twin)["pixels"], 0)


def make_bench(folder: Path, colour, n=6, dup_first=False) -> None:
    (folder / "shots").mkdir(parents=True)
    twin = np.full((60, 80, 3), 30, np.uint8)
    Image.fromarray(twin).save(folder / "twin.png")
    shots = []
    for i in range(n):
        fr = twin.copy()
        fr[20:40, 30:50] = colour
        name = "shots/%03d.png" % i
        Image.fromarray(fr).save(folder / name)
        shots.append({"n": i, "file": name, "t": 3.0 + i * 0.5, "dup": dup_first and i == 0})
    bench = {"frames_at_spawn": 100, "clock": [[0.0, 100], [20.0, 600]], "twin": "twin.png", "shots": shots}
    (folder / "bench.json").write_text(json.dumps(bench))


class Pair(Tmp):
    def test_build_pair_rows_and_numbers(self):
        edit, ref, view = self.d / "edit", self.d / "ref", self.d / "view"
        make_bench(edit, (40, 220, 40))
        make_bench(ref, (200, 200, 200), dup_first=True)
        view.mkdir()
        frames = []
        for i in range(6):
            p = view / ("v%d.png" % i)
            im = np.full((40, 60, 3), 30, np.uint8)
            im[10:30, 20:40] = (30, 230, 30)
            Image.fromarray(im).save(p)
            frames.append({"png": str(p), "t": 3.0 * 0 + i})
        (view / "metrics.json").write_text(json.dumps({"frames": frames}))
        res = lt.build_pair(edit, ref, view, (10, 10, 70, 50), self.d / "pair.jpg", self.d / "pair.gif", rows=3, min_age=0.0)
        with Image.open(res["jpg"]) as im:
            self.assertEqual(im.size, (3 * 60 + 12, 3 * 44))
        self.assertEqual(len(res["rows"]), 3)
        for r in res["rows"]:
            self.assertGreater(r["game_edit"]["green_excess"], 50)         # the edited run is green in the game ...
            self.assertGreater(r["viewer_edit"]["green_excess"], 50)       # ... and in the viewer ...
            self.assertLessEqual(r["game_ref"]["green_excess"], 1)         # ... the reference is not
        self.assertTrue(Path(res["gif"]).is_file())
        self.assertLess(Path(res["gif"]).stat().st_size, 5_000_000)
        self.assertIsNone(res["jpg4"])

    def test_build_pair_with_the_unedited_viewer_adds_a_column(self):
        edit, ref, view = self.d / "edit", self.d / "ref", self.d / "view"
        make_bench(edit, (40, 220, 40))
        make_bench(ref, (200, 200, 200))
        view.mkdir()
        (view / "metrics.json").write_text(json.dumps({"frames": [{"png": str(self.d / "v.png")}] * 6}))
        im = np.full((40, 60, 3), 30, np.uint8)
        im[10:30, 20:40] = (200, 200, 200)
        Image.fromarray(im).save(self.d / "v.png")
        res = lt.build_pair(edit, ref, view, (10, 10, 70, 50), self.d / "pair.jpg", rows=2, viewer_ref_dir=view)
        with Image.open(res["jpg4"]) as im4:
            self.assertEqual(im4.size, (4 * 66 - 6, 2 * 44))
        self.assertEqual(Path(res["jpg4"]).name, "pair4.jpg")
        self.assertLessEqual(res["rows"][0]["viewer_ref"]["green_excess"], 1)

    def test_build_pair_needs_a_usable_shot(self):
        edit, ref, view = self.d / "edit", self.d / "ref", self.d / "view"
        make_bench(edit, (40, 220, 40), n=2)
        make_bench(ref, (200, 200, 200), n=2)
        view.mkdir()
        (view / "metrics.json").write_text(json.dumps({"frames": []}))
        with self.assertRaises(RuntimeError):
            lt.build_pair(edit, ref, view, (0, 0, 40, 40), self.d / "p.jpg")


@unittest.skipUnless(HAVE_MCP, "needs the mcp package (official Python SDK) in this python")
class McpChain(Tmp):
    def test_recolour_through_the_stdio_server(self):
        import test_hdfx_api as ta
        game = self.d / "game"
        f = game / LOGICAL
        f.parent.mkdir(parents=True)
        f.write_bytes(ta.particle_file())
        res = lt.mcp_recolour(LOGICAL, (0.1, 1.0, 0.1), self.d / "out", TOOLS, roots=["game=%s" % game])
        self.assertEqual(res["streams"], ["Color"])
        self.assertTrue(res["validate"])
        out = Path(res["file"])
        self.assertTrue(out.is_file())
        self.assertEqual(lt.logical_of_file(out), LOGICAL)
        self.assertEqual(lt.sha256(out), res["sha256"])
        self.assertNotEqual(out.read_bytes(), f.read_bytes())           # the game file itself is untouched, the export differs
        self.assertEqual(f.read_bytes(), ta.particle_file())
        tools = [t["tool"] for t in res["transcript"][1:]]
        self.assertEqual(tools, ["fx_inspect", "fx_edit", "fx_validate", "fx_export"])
        self.assertTrue((self.d / "out" / "mcp-transcript.json").is_file())


if __name__ == "__main__":
    unittest.main()
