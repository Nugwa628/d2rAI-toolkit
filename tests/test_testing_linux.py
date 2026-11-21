"""Tests for testing/linux_env.py (no Wine, X or GPU needed: fake files and a fake helper process)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "testing"))

import linux_env as le  # noqa: E402


class EnvTests(unittest.TestCase):
    def test_dxvk_config_keeps_other_options(self):
        self.assertEqual(le.dxvk_config(2048), "dxgi.maxDeviceMemory = 2048")
        self.assertEqual(le.dxvk_config(1024, "dxgi.maxDeviceMemory = 4096; d3d11.cachedDynamicResources = a"),
                         "d3d11.cachedDynamicResources = a; dxgi.maxDeviceMemory = 1024")
        self.assertEqual(le.dxvk_config(0, "dxgi.maxDeviceMemory=4096"), None)
        self.assertEqual(le.dxvk_config(None, "x = 1"), "x = 1")

    def test_game_env(self):
        env = le.game_env(base={}, vram_mb=3072, display=101)
        self.assertEqual(env["DISPLAY"], ":101")
        self.assertEqual(env["MESA_VK_WSI_DEBUG"], "sw")
        self.assertIn("VK_KHR_present_wait", env["VKD3D_DISABLE_EXTENSIONS"])
        self.assertEqual(env["DXVK_CONFIG"], "dxgi.maxDeviceMemory = 3072")
        self.assertNotIn("DXVK_CONFIG", le.game_env(base={}))

    def test_free_display_and_screen(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "X100").touch()
            Path(d, ".X101-lock").touch()
            self.assertEqual(le.free_display(100, xdir=d, lockdir=d), 102)
        self.assertEqual(le.screen_for(1600, 900), (1920, 1080))
        self.assertEqual(le.screen_for(1920, 1080), (1984, 1176))
        self.assertIn("1984x1176x24", le.xvfb_command(105, 1984, 1176))


USER_REG = """WINE REGISTRY Version 2
;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000

#arch=win64

[Software\\\\Wine\\\\X11 Driver] 1700000000
#time=1
"UseTakeFocus"="Y"

[Software\\\\Wine\\\\Other] 1700000000
"Keep"="me"
"""


class RegTests(unittest.TestCase):
    def test_set_values_in_an_existing_section(self):
        out = le.set_reg_values(USER_REG, le.X11_DRIVER_KEY, le.NO_FOCUS_VALUES)
        vals = le.reg_values(out, le.X11_DRIVER_KEY)
        self.assertEqual(vals, {"UseTakeFocus": "N", "GrabPointer": "N", "GrabFullscreen": "N"})
        self.assertEqual(le.reg_values(out, "Software\\\\Wine\\\\Other"), {"Keep": "me"})
        self.assertEqual(out.count("[Software\\\\Wine\\\\X11 Driver]"), 1)

    def test_add_a_missing_section(self):
        text = USER_REG.replace("[Software\\\\Wine\\\\X11 Driver] 1700000000\n#time=1\n\"UseTakeFocus\"=\"Y\"\n\n", "")
        out = le.set_reg_values(text, le.X11_DRIVER_KEY, le.NO_FOCUS_VALUES, stamp=5)
        self.assertIn("[Software\\\\Wine\\\\X11 Driver] 5", out)
        self.assertEqual(len(le.reg_values(out, le.X11_DRIVER_KEY)), 3)
        self.assertTrue(out.endswith("\n"))

    def test_apply_no_focus(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "user.reg")
            Path(p).write_text(USER_REG)
            self.assertEqual(len(le.apply_no_focus(p, dry_run=True)), 3)
            self.assertEqual(Path(p).read_text(), USER_REG)
            self.assertEqual(len(le.apply_no_focus(p)), 3)
            self.assertEqual(le.apply_no_focus(p), [])


class ProfileTests(unittest.TestCase):
    def test_shipped_profiles_have_the_same_keys(self):
        test, visual = le.load_profile("test"), le.load_profile("visual")
        self.assertEqual(set(test), set(visual))
        self.assertEqual(test["Framerate Target"], 30)
        self.assertEqual(test["Resolution Scale"], 50)
        with self.assertRaises(ValueError):
            le.load_profile("ultra")

    def test_apply_keeps_other_keys_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Settings.json")
            Path(p).write_text(json.dumps({"Audio": 7, "VSync": 1, "Resolution Scale": 100}, indent=4))
            changed = le.apply_profile(p, {"VSync": 0, "Resolution Scale": 50})
            self.assertEqual(changed, {"VSync": (1, 0), "Resolution Scale": (100, 50)})
            got = json.loads(Path(p).read_text())
            self.assertEqual(list(got), ["Audio", "VSync", "Resolution Scale"])
            self.assertEqual(got["Audio"], 7)
            self.assertFalse(Path(p).read_text().endswith("\n"))
            self.assertEqual(le.apply_profile(p, {"VSync": 0}), {})

    def test_apply_to_a_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "save", "Settings.json")
            le.apply_profile(p, {"VSync": 0})
            self.assertEqual(json.loads(Path(p).read_text()), {"VSync": 0})


class GateTests(unittest.TestCase):
    def test_readings(self):
        with tempfile.TemporaryDirectory() as d:
            for card, busy in (("card0", "3"), ("card1", "55")):
                dev = Path(d, card, "device")
                dev.mkdir(parents=True)
                (dev / "gpu_busy_percent").write_text(busy + "\n")
                (dev / "mem_info_vram_used").write_text(str(3 * 2**30))
                (dev / "mem_info_vram_total").write_text(str(16 * 2**30))
            self.assertEqual(le.gpu_busy(d, samples=3, sleep=lambda _s: None), 55)
            self.assertEqual(le.vram_used_mb(d)["card1"], (3072, 16384))
            la = Path(d, "loadavg")
            la.write_text("8.00 6.00 5.00 2/900 1234\n")
            self.assertEqual(le.cpu_load_percent(str(la), cpus=16), 50.0)
        self.assertIsNone(le.gpu_busy("/nonexistent"))

    def test_gate(self):
        self.assertEqual(le.gate(5, 10), (True, "quiet"))
        self.assertFalse(le.gate(25, 10)[0])
        self.assertIn("CPU load", le.gate(5, 70)[1])
        self.assertTrue(le.gate(None, None)[0])


class FakeProc:
    def __init__(self, answers):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(a + "\n" for a in answers))
        self.killed = False

    def poll(self):
        return None if not self.killed else 0

    def wait(self, _t=None):
        self.killed = True

    def kill(self):
        self.killed = True


class WctlTests(unittest.TestCase):
    def test_requests(self):
        proc = FakeProc(["ok wctl 2 ready", "ok 65538 1234 0x400001 1600 900 0", "- 1\tSave", "ok", "err no window"])
        w = le.Wctl(["wctl"], popen=lambda *a, **k: proc)
        self.assertEqual(w.find("Diablo II: Resurrected"), (65538, 1234, 0x400001, 1600, 900, False))
        self.assertEqual(w.request("buttons", 65538), ("", ["1\tSave"]))
        with self.assertRaises(le.WctlError):
            w.request("rect", 1)
        self.assertIn("find\tDiablo II: Resurrected\t0\n", proc.stdin.getvalue())
        with self.assertRaises(le.WctlError):
            w.request("post", "a\nb")

    def test_bad_start(self):
        with self.assertRaises(le.WctlError):
            le.Wctl(["wctl"], popen=lambda *a, **k: FakeProc(["wine: could not load"]))


if __name__ == "__main__":
    unittest.main()
