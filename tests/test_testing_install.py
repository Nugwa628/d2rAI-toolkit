"""Tests for testing/release_install.py (run: python -m unittest discover tests).

The "published site" is built in a temporary folder and read with dir:<path> (and once over http on 127.0.0.1).
No internet, no game. The 7z tests are skipped when 7-Zip is not installed.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "testing"))

import game_slots as gs  # noqa: E402
import release_install as ri  # noqa: E402
import testing_config as tc  # noqa: E402

FILES = {
    "MyMod.mpq/modinfo.json": b'{"name": "My Mod", "savepath": "MyMod/"}',
    "MyMod.mpq/data/global/excel/example.txt": b"a\tb\n1\t2\n",
    "d2rloader/plugins/my-plugin.dll": b"release-dll-bytes",
    "d2rloader/plugins/copy-of-same.dll": b"release-dll-bytes",  # same bytes: one object
    "d2rloader/config/readme.txt": b"hello",
}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_cfg(tmp: str, **over) -> tc.Config:
    t = Path(tmp).as_posix()
    data = {
        "game": {"install_dir": f"{t}/install", "slots_dir": f"{t}/slots", "slot_count": 3},
        "mod": {"folder": "MyMod", "plugin_dlls": ["my-plugin.dll"]},
        "saves": {"root": f"{t}/saves"},
        "runs": {"dir": f"{t}/runs"},
    }
    return tc.Config(tc.deep_merge(data, over), base_dir=tmp)


def build_site(root: str, contents=FILES, version="1.0.0", archive_with=None, tamper=None, **manifest_over) -> dict:
    """Write a release site under root. archive_with: path of 7z to also publish the 7z."""
    root_p = Path(root)
    entries = []
    for path, data in sorted(contents.items()):
        h = sha(data)
        obj = root_p / "objects" / h[:2] / h
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(data)
        entries.append({"path": path, "size": len(data), "sha256": h})
    man = {"schemaVersion": 2, "name": "My Mod", "version": version, "complete": True, "modFolder": "MyMod",
           "launch": {"default_mod": "MyMod", "launch_arguments": "-txt"}, "objectsBase": "objects/",
           "emptyDirs": ["d2rloader/patches"], "files": entries}
    if archive_with:
        work = Path(tempfile.mkdtemp())
        try:
            for path, data in contents.items():
                p = work / "MyMod" / path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            (work / "MyMod" / "d2rloader" / "patches").mkdir(parents=True, exist_ok=True)
            arc = root_p / "releases" / version / f"MyMod-{version}.7z"
            arc.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([archive_with, "a", "-t7z", "-bso0", "-bsp0", str(arc), "MyMod"], cwd=work, check=True)
            man["archive"] = {"format": "7z", "name": arc.name, "size": arc.stat().st_size,
                              "sha256": ri.sha256_file(str(arc)), "path": f"releases/{version}/{arc.name}"}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    man.update(manifest_over)
    raw = json.dumps(man, indent=1).encode()
    mp = root_p / "releases" / version / "release-manifest.json"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_bytes(raw)
    ch = {"schemaVersion": 1, "version": version, "manifest": f"releases/{version}/release-manifest.json",
          "manifestSha256": sha(raw)}
    (root_p / "channels").mkdir(exist_ok=True)
    (root_p / "channels" / "stable.json").write_text(json.dumps(ch), encoding="utf-8")
    if tamper:
        tamper(root_p, man)
    return man


class SiteBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.site_dir = os.path.join(self.dir, "site")
        os.makedirs(self.site_dir)
        self.cfg = make_cfg(self.dir)
        self.spec = "dir:" + self.site_dir
        env = {k: v for k, v in os.environ.items() if k not in ("MODKIT_RUN", "MODKIT_SLOT")}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.log = []

    def prepare_slot(self, n=2, owner="lane-a"):
        """A slot with an old mod folder, a loader config and the slot lock held by `owner`."""
        mod = Path(self.cfg.mod_dir(n))
        (mod / "MyMod.mpq").mkdir(parents=True)
        (mod / "dev-only.txt").write_text("developer install")
        cfgp = Path(self.cfg.loader_config_path(n))
        cfgp.parent.mkdir(parents=True)
        cfgp.write_text('[d2rloader]\ndefault_mod = "Other"\nlaunch_arguments = "-dev"\n')
        if owner:
            self.assertTrue(gs.acquire(self.cfg, n, owner, pid=0)[0])

    def install(self, n=2, **kw):
        kw.setdefault("owner", "lane-a")
        kw.setdefault("method", "objects")
        return ri.install(self.cfg, n, "stable", self.spec, log=self.log.append, **kw)


class HelperTests(unittest.TestCase):
    def test_safe_rel(self):
        for ok in ("a", "a/b.txt", "d2rloader/plugins/x.dll"):
            self.assertEqual(ri.safe_rel(ok), ok)
        for bad in ("", "/etc/passwd", "../x", "a/../b", "a//b", "C:/x", "a\\b", None, "a/", 5):
            with self.assertRaises(ri.ReleaseError, msg=repr(bad)):
                ri.safe_rel(bad)

    def test_valid_sha(self):
        self.assertTrue(ri.valid_sha("a" * 64))
        for bad in ("a" * 63, "A" * 64, "g" * 64, None, 5):
            self.assertFalse(ri.valid_sha(bad))

    def test_site_specs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ri.Site("dir:" + d).root, os.path.abspath(d))
            self.assertEqual(ri.Site(d).root, os.path.abspath(d))
            self.assertEqual(ri.Site(Path(d).as_uri()).root, os.path.abspath(d))
            self.assertEqual(ri.Site("https://files.example.com/my-mod").base, "https://files.example.com/my-mod/")
        for bad in ("", "ftp://x/y", "no/such/folder/anywhere", "dir:" + os.path.join(d, "gone")):
            with self.assertRaises(ri.ReleaseError, msg=bad):
                ri.Site(bad)

    def test_find_7z_prefers_the_config(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "my7z.exe")
            Path(fake).write_bytes(b"")
            self.assertEqual(ri.find_7z(make_cfg(d, release={"seven_zip": fake})), fake)


class ReadReleaseTests(SiteBase):
    def test_channel_and_manifest_are_read_and_verified(self):
        build_site(self.site_dir)
        ch, man = ri.load_release(self.cfg, ri.Site(self.spec), "stable")
        self.assertEqual(man["version"], "1.0.0")
        self.assertEqual(len(man["files"]), len(FILES))
        self.assertEqual(ri.object_rel(man, "ab" + "0" * 62), "objects/ab/ab" + "0" * 62)

    def test_manifest_that_does_not_match_the_channel(self):
        def tamper(root, man):
            p = root / "releases" / "1.0.0" / "release-manifest.json"
            p.write_bytes(p.read_bytes() + b" ")
        build_site(self.site_dir, tamper=tamper)
        with self.assertRaisesRegex(ri.ReleaseError, "sha256"):
            ri.load_release(self.cfg, ri.Site(self.spec), "stable")

    def test_refusals(self):
        cases = {
            "incomplete": ({"complete": False}, "complete=false"),
            "schema": ({"schemaVersion": 9}, "schemaVersion"),
            "unsafe path": ({"files": [{"path": "../evil", "size": 1, "sha256": "a" * 64}]}, "unsafe"),
            "bad sha": ({"files": [{"path": "a", "size": 1, "sha256": "zz"}]}, "bad file entry"),
            "no files": ({"files": []}, "no files"),
            "unsafe empty dir": ({"emptyDirs": ["/abs"]}, "unsafe"),
            "folder": ({"modFolder": "OtherMod"}, "modFolder"),
        }
        for name, (over, rx) in cases.items():
            shutil.rmtree(self.site_dir)
            os.makedirs(self.site_dir)
            build_site(self.site_dir, **over)
            with self.assertRaisesRegex(ri.ReleaseError, rx, msg=name):
                ri.load_release(self.cfg, ri.Site(self.spec), "stable")

    def test_missing_channel_and_bad_channel_name(self):
        with self.assertRaises(ri.ReleaseError):
            ri.read_channel(ri.Site(self.spec), "stable", self.cfg)
        with self.assertRaises(ri.ReleaseError):
            ri.read_channel(ri.Site(self.spec), "../stable", self.cfg)

    def test_check_command(self):
        build_site(self.site_dir)
        conf = os.path.join(self.dir, "c.json")
        Path(conf).write_text(json.dumps({"mod": {"folder": "MyMod"}}), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ri.main(["--config", conf, "check", "--site", self.spec])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("release 1.0.0", out.getvalue())
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(ri.main(["--config", conf, "check", "--site", "dir:" + self.dir]), 2)


class InstallTests(SiteBase):
    def test_install_replaces_only_the_slots_mod_folder(self):
        build_site(self.site_dir)
        self.prepare_slot()
        other = Path(self.cfg.slot_dir(3), "mods", "MyMod")
        other.mkdir(parents=True)
        (other / "slot3.txt").write_text("another slot")
        slot1 = Path(self.cfg.mod_dir(1))
        slot1.mkdir(parents=True)
        (slot1 / "mine.txt").write_text("the user's own game")
        ch = self.install()
        mod = Path(self.cfg.mod_dir(2))
        for path, data in FILES.items():
            if path != "MyMod.mpq/modinfo.json":  # rewritten for the slot's own saves
                self.assertEqual((mod / path).read_bytes(), data, path)
        self.assertTrue((mod / "d2rloader" / "patches").is_dir())  # an empty folder of the release
        self.assertFalse((mod / "dev-only.txt").exists())
        self.assertEqual((Path(ri.backup_dir(self.cfg, 2)) / "MyMod" / "dev-only.txt").read_text(), "developer install")
        self.assertEqual((other / "slot3.txt").read_text(), "another slot")
        self.assertEqual((slot1 / "mine.txt").read_text(), "the user's own game")
        self.assertEqual(ch["version"], "1.0.0")
        self.assertFalse(os.path.exists(os.path.join(self.cfg.install_state_dir(2), "stage")))

    def test_savepath_and_launch_keys(self):
        build_site(self.site_dir)
        self.prepare_slot()
        self.install()
        info = json.loads(Path(self.cfg.mod_dir(2), "MyMod.mpq", "modinfo.json").read_text())
        self.assertEqual(info["savepath"], "MyMod-s2/")
        toml = Path(self.cfg.loader_config_path(2)).read_text()
        self.assertIn('default_mod = "MyMod"', toml)
        self.assertIn('launch_arguments = "-txt -w"', toml)
        self.assertTrue(os.path.isdir(self.cfg.save_dir(2)))
        state = json.loads(Path(ri.state_file(self.cfg, 2)).read_text())
        self.assertEqual(state["version"], "1.0.0")
        self.assertEqual(state["launch_arguments"], "-txt -w")

    def test_apply_launch_can_be_turned_off(self):
        build_site(self.site_dir)
        self.cfg = make_cfg(self.dir, release={"apply_launch": False})
        self.prepare_slot()
        self.install()
        self.assertIn('default_mod = "Other"', Path(self.cfg.loader_config_path(2)).read_text())

    def test_restore_puts_everything_back(self):
        build_site(self.site_dir)
        self.prepare_slot()
        self.install()
        ri.restore(self.cfg, 2, owner="lane-a", log=self.log.append)
        mod = Path(self.cfg.mod_dir(2))
        self.assertEqual((mod / "dev-only.txt").read_text(), "developer install")
        self.assertFalse((mod / "d2rloader").exists())
        self.assertIn('default_mod = "Other"', Path(self.cfg.loader_config_path(2)).read_text())
        self.assertFalse(os.path.exists(ri.backup_dir(self.cfg, 2)))
        self.assertFalse(os.path.exists(ri.state_file(self.cfg, 2)))
        with self.assertRaises(ri.ReleaseError):
            ri.restore(self.cfg, 2, owner="lane-a", log=self.log.append)  # nothing to restore any more

    def test_installing_twice_keeps_the_first_backup(self):
        build_site(self.site_dir)
        self.prepare_slot()
        self.install()
        self.install()
        self.assertEqual((Path(ri.backup_dir(self.cfg, 2)) / "MyMod" / "dev-only.txt").read_text(), "developer install")
        ri.restore(self.cfg, 2, owner="lane-a", log=self.log.append)
        self.assertTrue(Path(self.cfg.mod_dir(2), "dev-only.txt").exists())

    def test_slot_without_a_mod_folder_yet(self):
        build_site(self.site_dir)
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", pid=0)[0])
        self.install()
        self.assertTrue(Path(self.cfg.mod_dir(2), "MyMod.mpq", "modinfo.json").is_file())
        ri.restore(self.cfg, 2, owner="lane-a", log=self.log.append)
        self.assertFalse(Path(self.cfg.mod_dir(2)).exists())

    def test_harness_dll_replaces_the_release_plugin_and_keeps_it(self):
        build_site(self.site_dir)
        self.prepare_slot()
        dev = Path(self.dir, "my-plugin.dll")
        dev.write_bytes(b"dev-build-with-harness")
        ch = self.install(harness_dlls=[str(dev)])
        self.assertEqual(Path(self.cfg.plugins_dir(2), "my-plugin.dll").read_bytes(), b"dev-build-with-harness")
        self.assertEqual(Path(self.cfg.install_state_dir(2), "my-plugin.dll.release").read_bytes(), b"release-dll-bytes")
        self.assertEqual(ch["plugins"], ["my-plugin.dll"])
        with self.assertRaises(ri.ReleaseError):
            self.install(harness_dlls=[os.path.join(self.dir, "missing.dll")])

    def test_one_object_for_identical_files(self):
        build_site(self.site_dir)
        fetched = []
        real = ri.Site.fetch_to

        def counting(self_, rel, *a, **k):
            fetched.append(rel)
            return real(self_, rel, *a, **k)

        self.prepare_slot()
        with mock.patch.object(ri.Site, "fetch_to", counting):
            self.install()
        self.assertEqual(len(fetched), len(FILES) - 1)
        self.assertEqual(len(set(fetched)), len(fetched))


class InstallRefusalTests(SiteBase):
    def test_slot_one_is_refused(self):
        build_site(self.site_dir)
        with self.assertRaisesRegex(ri.ReleaseError, "slot 1"):
            ri.install(self.cfg, 1, "stable", self.spec, owner="lane-a", log=self.log.append)
        for fn in (ri.restore, ri.status):
            with self.assertRaisesRegex(ri.ReleaseError, "slot 1"):
                fn(self.cfg, 1)
        self.assertFalse(os.path.exists(self.cfg.mod_dir(1)))

    def test_needs_the_lock_of_the_run(self):
        build_site(self.site_dir)
        self.prepare_slot(owner=None)
        with self.assertRaisesRegex(ri.ReleaseError, "not locked"):
            self.install()  # nobody holds the slot
        gs.acquire(self.cfg, 2, "lane-b", pid=0)
        with self.assertRaisesRegex(ri.ReleaseError, "not locked"):
            self.install()  # somebody else holds it
        with self.assertRaisesRegex(ri.ReleaseError, "not locked"):
            self.install(owner=None)  # MODKIT_RUN is not set
        with mock.patch.dict(os.environ, {"MODKIT_RUN": "lane-b"}):
            self.install(owner=None)  # the env var names the owner
        self.assertTrue(Path(self.cfg.mod_dir(2), "d2rloader").is_dir())

    def test_refuses_while_a_game_runs_in_the_slot(self):
        build_site(self.site_dir)
        self.prepare_slot()
        with mock.patch.object(gs, "slot_processes", return_value=[(1, "D2R.exe")]):
            with self.assertRaisesRegex(ri.ReleaseError, "running game"):
                self.install()
        self.assertTrue(Path(self.cfg.mod_dir(2), "dev-only.txt").exists())

    def test_a_bad_download_changes_nothing_in_the_slot(self):
        def tamper(root, man):
            victim = next(f for f in man["files"] if f["path"].endswith("example.txt"))
            (root / "objects" / victim["sha256"][:2] / victim["sha256"]).write_bytes(b"corrupted!")
        build_site(self.site_dir, tamper=tamper)
        self.prepare_slot()
        with self.assertRaisesRegex(ri.ReleaseError, "giving up"):
            self.install()
        self.assertEqual(Path(self.cfg.mod_dir(2), "dev-only.txt").read_text(), "developer install")
        self.assertFalse(os.path.exists(ri.backup_dir(self.cfg, 2)))
        self.assertFalse(os.path.exists(ri.state_file(self.cfg, 2)))
        self.assertIn('default_mod = "Other"', Path(self.cfg.loader_config_path(2)).read_text())

    def test_a_missing_object_changes_nothing_in_the_slot(self):
        def tamper(root, man):
            f = man["files"][0]
            (root / "objects" / f["sha256"][:2] / f["sha256"]).unlink()
        build_site(self.site_dir, tamper=tamper)
        self.prepare_slot()
        with self.assertRaises(ri.ReleaseError):
            self.install()
        self.assertTrue(Path(self.cfg.mod_dir(2), "dev-only.txt").exists())

    def test_leftover_backup_without_a_record_stops(self):
        build_site(self.site_dir)
        self.prepare_slot()
        os.makedirs(ri.backup_dir(self.cfg, 2))
        with self.assertRaisesRegex(ri.ReleaseError, "delete it yourself"):
            self.install()
        self.assertTrue(Path(self.cfg.mod_dir(2), "dev-only.txt").exists())

    def test_install_will_not_replace_a_folder_outside_the_slots_mods(self):
        build_site(self.site_dir)
        self.prepare_slot()
        with mock.patch.object(self.cfg, "mod_dir", return_value=os.path.join(self.dir, "elsewhere", "MyMod")):
            with self.assertRaisesRegex(ri.ReleaseError, "refusing"):
                self.install()

    def test_main_exit_codes(self):
        build_site(self.site_dir)
        conf = os.path.join(self.dir, "c.json")
        t = Path(self.dir).as_posix()
        Path(conf).write_text(json.dumps({
            "game": {"install_dir": f"{t}/install", "slots_dir": f"{t}/slots"}, "mod": {"folder": "MyMod"},
            "runs": {"dir": f"{t}/runs"}, "saves": {"root": f"{t}/saves"}}), encoding="utf-8")
        for argv in (["install", "--slot", "1", "--site", self.spec], ["install", "--site", self.spec],
                     ["install", "--slot", "2", "--site", self.spec]):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = ri.main(["--config", conf, *argv])
            self.assertEqual(rc, 2, argv)
            self.assertIn("error:", err.getvalue())


class MethodTests(SiteBase):
    def test_method_archive_without_7zip_is_a_clear_error(self):
        build_site(self.site_dir)
        self.prepare_slot()
        with mock.patch.object(ri, "find_7z", return_value=None):
            with self.assertRaisesRegex(ri.ReleaseError, "7-Zip"):
                self.install(method="archive")

    def test_auto_falls_back_to_objects_without_7zip(self):
        build_site(self.site_dir)
        self.prepare_slot()
        with mock.patch.object(ri, "find_7z", return_value=None):
            self.install(method="auto")
        self.assertTrue(any("object by object" in m for m in self.log))

    def test_archive_method_with_7zip(self):
        seven = ri.find_7z(self.cfg)
        if not seven:
            self.skipTest("7-Zip is not installed")
        build_site(self.site_dir, archive_with=seven)
        self.prepare_slot()
        self.install(method="archive")
        mod = Path(self.cfg.mod_dir(2))
        for path, data in FILES.items():
            if path != "MyMod.mpq/modinfo.json":  # rewritten for the slot's own saves
                self.assertEqual((mod / path).read_bytes(), data, path)
        self.assertTrue(any("unpacking with 7-Zip" in m for m in self.log))

    def test_archive_gaps_are_filled_from_the_objects(self):
        seven = ri.find_7z(self.cfg)
        if not seven:
            self.skipTest("7-Zip is not installed")
        extra = dict(FILES, **{"MyMod.mpq/data/new.txt": b"only in the objects"})
        build_site(self.site_dir, contents=extra, archive_with=seven)
        # build the archive from a SMALLER file set, but keep the full manifest: the 7z lacks one file
        site = Path(self.site_dir)
        man_path = site / "releases" / "1.0.0" / "release-manifest.json"
        man = json.loads(man_path.read_text())
        small = {k: v for k, v in extra.items() if k != "MyMod.mpq/data/new.txt"}
        shutil.rmtree(site / "releases")
        shutil.rmtree(site / "channels")
        small_man = build_site(self.site_dir, contents=small, archive_with=seven)
        man["archive"] = small_man["archive"]
        raw = json.dumps(man).encode()
        man_path.write_bytes(raw)
        (site / "channels" / "stable.json").write_text(json.dumps(
            {"schemaVersion": 1, "version": "1.0.0", "manifest": "releases/1.0.0/release-manifest.json",
             "manifestSha256": sha(raw)}), encoding="utf-8")
        self.prepare_slot()
        self.install(method="archive")
        self.assertEqual(Path(self.cfg.mod_dir(2), "MyMod.mpq", "data", "new.txt").read_bytes(), b"only in the objects")
        self.assertTrue(any("fetching them one by one" in m for m in self.log))

    def test_tampered_archive_is_refused(self):
        seven = ri.find_7z(self.cfg)
        if not seven:
            self.skipTest("7-Zip is not installed")

        def tamper(root, man):
            p = root / man["archive"]["path"]
            p.write_bytes(p.read_bytes() + b"x")
        build_site(self.site_dir, archive_with=seven, tamper=tamper)
        self.prepare_slot()
        with self.assertRaisesRegex(ri.ReleaseError, "giving up"):
            self.install(method="archive")
        self.assertTrue(Path(self.cfg.mod_dir(2), "dev-only.txt").exists())


class HttpTests(SiteBase):
    def test_install_over_http_from_loopback(self):
        build_site(self.site_dir)
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

        handler = functools.partial(Quiet, directory=self.site_dir)
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError:
            self.skipTest("cannot listen on loopback")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        self.prepare_slot()
        ri.install(self.cfg, 2, "stable", url, method="objects", owner="lane-a", log=self.log.append)
        self.assertEqual(Path(self.cfg.mod_dir(2), "d2rloader", "config", "readme.txt").read_bytes(), b"hello")
        with self.assertRaises(ri.ReleaseError):
            ri.Site(url).fetch("objects/zz/missing")


if __name__ == "__main__":
    unittest.main()
