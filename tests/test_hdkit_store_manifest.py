"""Tests for hdkit.store.Manifest: logical paths of files moved out of a work folder into the art store."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hdkit import store  # noqa: E402


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.base, self.st = t / "work", t / "store"
        (self.base / "m" / "stages" / "5-build").mkdir(parents=True)
        self.f = self.base / "m" / "stages" / "5-build" / "albedo.png"
        self.f.write_bytes(b"x" * 1000)

    def man(self):
        return store.Manifest(self.base, store=self.st)

    def test_put_find_fetch_check(self):
        m = self.man()
        r = m.put_files([self.f], delete=True, log=lambda s: None)
        self.assertEqual((r["new"], r["copied"], r["deleted"]), (1, 1, 1))
        self.assertFalse(self.f.exists())
        logical = "m/stages/5-build/albedo.png"
        # a fresh reader finds the object by its logical path, also given as an absolute path under base
        m2 = self.man()
        obj = m2.find(logical)
        self.assertEqual(obj.read_bytes(), b"x" * 1000)
        self.assertEqual(m2.find(self.base / logical), obj)
        self.assertEqual(m2.check(), [])
        n, skipped = m2.fetch(self.base, m2.select(["m/stages/*"]))
        self.assertEqual((n, skipped), (1, 0))
        self.assertEqual(self.f.read_bytes(), b"x" * 1000)
        self.assertEqual(m2.fetch(self.base, m2.select([logical])), (0, 1))
        # the manifest is copied into the store
        self.assertTrue((self.st / "manifests" / "monster3d" / "ART-MANIFEST.json").is_file())
        saved = json.loads((self.base / "ART-MANIFEST.json").read_text())
        self.assertEqual([f["path"] for f in saved["files"]], [logical])

    def test_bad_object_is_reported(self):
        m = self.man()
        m.put_files([self.f], log=lambda s: None)
        digest = m.data()["files"][0]["sha256"]
        m.object_path(digest).write_bytes(b"changed")
        self.assertEqual(self.man().check(), ["m/stages/5-build/albedo.png"])

    def test_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.man().find("nothing/here.png")
        with self.assertRaises(SystemExit):
            self.man().select(["nothing/*"])

    def test_outside_base_refused(self):
        other = Path(self.tmp.name) / "elsewhere.bin"
        other.write_bytes(b"y")
        with self.assertRaises(SystemExit):
            self.man().put_files([other], log=lambda s: None)


if __name__ == "__main__":
    unittest.main()
