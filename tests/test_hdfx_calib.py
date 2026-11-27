"""hdkit.fx.calib: RenderDoc layer env, qrenderdoc settings, capture decoding, viewer-vs-game compare (no game files,
no RenderDoc, no GPU)."""
import json
import struct
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

from hdkit.fx.calib import compare, draws, rdoc  # noqa: E402


def fake_release(root: Path) -> Path:
    (root / "lib").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "lib" / "librenderdoc.so").write_bytes(b"")
    (root / "bin" / "qrenderdoc").write_bytes(b"")
    d = root / "etc" / "vulkan" / "implicit_layer.d"
    d.mkdir(parents=True)
    (d / "renderdoc_capture.json").write_text(json.dumps({"layer": {"name": rdoc.LAYER,
                                                                    "library_path": "/io/dist/lib/librenderdoc.so"}}))
    return root


class RdocTest(unittest.TestCase):
    def test_layer_env_only(self):
        with tempfile.TemporaryDirectory() as t:
            root = fake_release(Path(t) / "rd")
            env = rdoc.game_env(rdoc.find_root(root), Path(t) / "work", "/x/cap", "/x/log")
            man = json.loads((Path(env["VK_LAYER_PATH"]) / "renderdoc_capture.json").read_text())
            self.assertEqual(man["layer"]["library_path"], str((root / "lib" / "librenderdoc.so").resolve()))
            self.assertTrue(env["VK_LAYER_PATH"].startswith(t))           # nothing outside the work folder
            self.assertEqual(env["VK_INSTANCE_LAYERS"], rdoc.LAYER)
            self.assertEqual(env["RENDERDOC_CAPFILE"], "/x/cap")

    def test_find_root_refuses_missing(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(SystemExit):
            rdoc.find_root(t)

    def test_home_config_opts_out(self):
        with tempfile.TemporaryDirectory() as t:
            rdoc.ensure_home(Path(t))
            cfg = json.loads((Path(t) / ".local/share/qrenderdoc/UI.config").read_text())
            self.assertEqual(cfg["rdocConfigData"], 1)
            self.assertTrue(cfg["Analytics_TotalOptOut"])
            self.assertFalse(cfg["CheckUpdate_AllowChecks"])
            cfg["Analytics_TotalOptOut"] = False
            (Path(t) / ".local/share/qrenderdoc/UI.config").write_text(json.dumps(cfg))
            rdoc.ensure_home(Path(t))
            self.assertTrue(json.loads((Path(t) / ".local/share/qrenderdoc/UI.config").read_text())["Analytics_TotalOptOut"])


class DrawsTest(unittest.TestCase):
    def test_decode_attr_formats(self):
        rows = [struct.pack("<3f", i, i * 2, i * 3) + bytes([i * 50, 255, 0, 128]) for i in range(4)]
        data = b"".join(rows)
        pos = draws.decode_attr(data, 16, 0, {"comp": "Float", "width": 4, "count": 3}, 1, 2)
        np.testing.assert_allclose(pos, [[1, 2, 3], [2, 4, 6]])
        col = draws.decode_attr(data, 16, 12, {"comp": "UNorm", "width": 1, "count": 4, "bgra": True}, 0, 2)
        np.testing.assert_allclose(col[1], [0, 1, 50 / 255, 128 / 255], atol=1e-6)

    def test_decode_draw(self):
        with tempfile.TemporaryDirectory() as t:
            dd = Path(t) / "draws" / "7"
            dd.mkdir(parents=True)
            vb = b"".join(struct.pack("<2f", i, -i) for i in range(5))
            (dd / "vb0.bin").write_bytes(vb)
            (dd / "ib.bin").write_bytes(struct.pack("<6H", 0, 1, 2, 2, 1, 3))
            inst = b"".join(struct.pack("<4f", 1, 2, 3, k) for k in range(3))
            (dd / "vb1.bin").write_bytes(inst)
            (dd / "push.bin").write_bytes(struct.pack("<4f", 0.5, 1, 2, 4))
            (dd / "p_cb0.bin").write_bytes(struct.pack("<8f", *range(8)))
            info = {"eid": 7, "name": "DrawIndexedInstanced", "indices": 6, "instances": 3, "index_offset": 0,
                    "base_vertex": 1, "vertex_offset": 0, "instance_offset": 0, "indexed": True,
                    "inputs": [{"name": "POSITION", "buffer": 0, "offset": 0, "per_instance": False,
                                "format": {"comp": "Float", "width": 4, "count": 2}},
                               {"name": "TEXCOORD1", "buffer": 1, "offset": 0, "per_instance": True,
                                "format": {"comp": "Float", "width": 4, "count": 4}}],
                    "vbuffers": [{"slot": 0, "stride": 8, "file": "vb0.bin"}, {"slot": 1, "stride": 16, "file": "vb1.bin"}],
                    "ibuffer": {"stride": 2, "file": "ib.bin"}, "push": {"file": "push.bin"},
                    "stages": {"Pixel": {"cbuffers": [{"index": 0, "file": "p_cb0.bin", "bytes": 32}]}}}
            (dd / "draw.json").write_text(json.dumps(info))
            res = draws.decode_dir(Path(t))
            z = np.load(dd / "draw.npz")
            self.assertEqual(z["idx"].tolist(), [1, 2, 3, 3, 2, 4])
            self.assertEqual(z["v_POSITION"].shape, (5, 2))
            self.assertEqual(z["i_TEXCOORD1"][:, 3].tolist(), [0, 1, 2])
            self.assertEqual(z["p_cb0_f32"].shape, (2, 4))
            self.assertEqual(res["7"]["cbuffers"]["p_cb0"]["rows"][1], [4, 5, 6, 7])
            self.assertAlmostEqual(float(z["push_f32"][0]), 0.5)


class CompareTest(unittest.TestCase):
    def test_shifted_blob(self):
        rng = np.random.default_rng(1)
        twin = rng.integers(20, 60, (200, 300, 3)).astype(np.uint8)
        shot = twin.copy()
        shot[80:120, 140:180] = np.clip(shot[80:120, 140:180].astype(int) + 90, 0, 255)
        render = np.zeros((120, 120, 4), np.uint8)
        render[33:73, 43:83, :3] = 90            # the same blob, 3 px right of and 7 px above the game's (40:80)
        render[33:73, 43:83, 3] = 255
        res, imgs = compare.compare(render, shot, twin, feet=(150, 130), render_feet=(50, 90))
        self.assertGreater(res["iou_best"], 0.95)
        self.assertEqual(res["shift"], [-3, 7])
        self.assertAlmostEqual(res["added_game"][0], 90, delta=1)
        self.assertEqual(compare.panel(imgs).shape, (120, 360, 3))


if __name__ == "__main__":
    unittest.main()
