"""release/package_release.py --nexus: the Nexus Mods archive and its INSTALL.txt, on a small fake mod folder.
Run: python -m unittest discover tests"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_release_support import RELEASE_DIR, SEVEN, make_mod, needs_7z  # also puts release/ on sys.path

import package_release as pr  # noqa: E402
import release_config  # noqa: E402

SCRIPT = str(RELEASE_DIR / "package_release.py")


class NexusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cfg = release_config.defaults()
        self.dll = self.tmp / "mymod-core.dll"
        self.dll.write_bytes(b"MZ..." + b"1.2.3" + b"\0...")

    def mod(self):
        return make_mod(self.tmp / "src", "MyMod", {"global/excel/armor.txt": b"armor\n"}, "1.2.3")

    def test_install_text_with_a_plugin_needs_d2rloader(self):
        text = pr.nexus_install_text(self.cfg, "1.2.3", plugin=True)
        self.assertTrue(text.startswith("My Mod 1.2.3 for Diablo II: Resurrected\r\n====="))
        for part in ("D2RLoader 1.3.1-beta", "does not run without it", "Default Mod to MyMod",
                     "<D2R folder>\\mods\\MyMod\\MyMod.mpq\\modinfo.json", "mods\\MyMod\\MyMod\\", "build 93847"):
            self.assertIn(part, text)
        self.assertNotIn("-mod MyMod -txt", text)
        self.assertNotIn("\n", text.replace("\r\n", ""), "CRLF line endings only")

    def test_install_text_without_a_plugin_uses_the_command_line(self):
        text = pr.nexus_install_text(self.cfg, "1.2.3", plugin=False)
        self.assertIn("D2RLoader is not needed", text)
        self.assertIn("-mod MyMod -txt", text)
        self.assertNotIn("Default Mod", text)

    def test_install_text_never_mentions_the_downloader(self):
        text = pr.nexus_install_text(self.cfg, "1.2.3", plugin=True)
        self.assertNotIn(self.cfg["downloader"]["exe_name"], text)
        self.assertNotIn(self.cfg["site"]["url"], text)

    def test_has_plugin_looks_for_a_dll(self):
        mod = pr.stage(self.mod(), self.tmp / "stage" / "MyMod")
        self.assertFalse(pr.has_plugin(mod))
        mod = pr.stage(self.mod(), self.tmp / "stage" / "MyMod", self.dll)
        self.assertTrue(pr.has_plugin(mod))
        self.assertEqual(pr.nexus_archive_name(self.cfg, "1.2.3"), "MyMod-1.2.3-nexus.7z")

    @needs_7z
    def test_main_writes_the_nexus_archive(self):
        out = self.tmp / "dist"
        r = subprocess.run([sys.executable, "-I", SCRIPT, "--mod-dir", str(self.mod()), "--version", "1.2.3",
                            "--plugin", str(self.dll), "--nexus", "--out", str(out)], capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["MyMod-1.2.3-nexus.7z", "MyMod-1.2.3.7z", "RELEASE_NOTES-1.2.3.md", "SHA256SUMS", "release-manifest.json"])
        nexus = out / "MyMod-1.2.3-nexus.7z"
        listing = subprocess.run([SEVEN, "l", "-slt", str(nexus)], capture_output=True, text=True).stdout
        paths = [line[7:].replace("\\", "/") for line in listing.splitlines() if line.startswith("Path = ")][1:]  # [0]: the 7z
        self.assertIn("MyMod/INSTALL.txt", paths)
        self.assertTrue(all(p.split("/")[0] == "MyMod" for p in paths), paths)
        main = subprocess.run([SEVEN, "l", "-slt", str(out / "MyMod-1.2.3.7z")], capture_output=True, text=True).stdout
        self.assertNotIn("INSTALL.txt", main)
        man = json.loads((out / "release-manifest.json").read_text())
        self.assertNotIn("INSTALL.txt", [f["path"] for f in man["files"]])
        sums = (out / "SHA256SUMS").read_text().splitlines()
        self.assertIn(f"{pr.sha256(nexus)}  {nexus.name}", sums)
        self.assertEqual(len(sums), 4)
        self.assertIn(nexus.name, r.stdout)
        self.assertFalse((out / "stage").exists())


if __name__ == "__main__":
    unittest.main()
