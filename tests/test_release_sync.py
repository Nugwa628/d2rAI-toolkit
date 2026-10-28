"""release/dl_sync.py (the rclone mirror) with tests/release_fakes/fake_rclone.py standing in for rclone: the "bucket" is a folder.
The site is built by hand, so these tests need no 7-Zip. No network. Run: python -m unittest discover tests"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_release_support import FAKES, env_patch, pub  # noqa: F401  (also puts release/ on sys.path)

import dl_sync  # noqa: E402

REMOTE = "mirror:my-mod-files/mymod"


def sha(b):
    return hashlib.sha256(b).hexdigest()


class SyncBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.site = self.tmp / "site"
        self.state = self.tmp / "state"
        self.space = self.tmp / "space"  # the fake provider
        self.bucket = self.space / "my-mod-files" / "mymod"
        self.log = self.tmp / "rclone.log"
        self.space.mkdir()
        self.conf = self.tmp / "rclone.conf"
        self.conf.write_text("[mirror]\ntype = s3\n")
        fake = str(FAKES / "fake_rclone.py")
        if sys.platform == "win32":
            self.rclone = self.tmp / "rclone.cmd"
            self.rclone.write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
        else:
            self.rclone = self.tmp / "rclone"
            self.rclone.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
            self.rclone.chmod(0o755)
        patch = mock.patch.dict(os.environ, {"FAKE_RCLONE_ROOT": str(self.space), "FAKE_RCLONE_LOG": str(self.log)})
        patch.start()
        self.addCleanup(patch.stop)
        self.objects = {}
        self.make_site()

    # ---------------------------------------------------------------------------------------------- site and bucket
    def put(self, rel, data: bytes):
        p = self.site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def make_release(self, version, bodies):
        files = []
        for i, body in enumerate(bodies):
            s = sha(body)
            self.put(f"objects/{s[:2]}/{s}", body)
            files.append({"path": f"f{i}.txt", "size": len(body), "sha256": s})
            self.objects[s] = body
        man = json.dumps({"version": version, "files": files}).encode()
        self.put(f"releases/{version}/release-manifest.json", man)
        self.put(f"releases/{version}/MyMod-{version}.7z", b"7z for " + version.encode())
        self.put(f"releases/{version}/SHA256SUMS", b"sums\n")
        return man

    def make_site(self):
        man = self.make_release("1.0.0", [b"one", b"two"])
        self.put("channels/stable.json", json.dumps({"version": "1.0.0", "manifestSha256": sha(man)}).encode())
        self.put("downloader/3.1.2/MyMod-Downloader.exe", b"MZ")
        self.put("downloader/3.1.2/MyMod-Downloader.exe.sha256", b"abc  MyMod-Downloader.exe\n")

    def rc(self):
        return dl_sync.Rclone(REMOTE, self.conf, str(self.rclone), self.state, self.state / "cache", "public-read")

    def sync(self, **kw):
        self.state.mkdir(exist_ok=True)
        s = dl_sync.Sync(self.site, self.state, self.rc(), **kw)
        with mock.patch("builtins.print"):
            s.run_pass()
        return s

    def main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dl_sync.main(["--site", str(self.site), "--remote", REMOTE, "--config", str(self.conf), "--state", str(self.state),
                               "--rclone", str(self.rclone), "--lock-wait", "0", *extra])
        return rc, out.getvalue() + err.getvalue()

    def calls(self):
        if not self.log.is_file():
            return []
        return [json.loads(ln) for ln in self.log.read_text("utf-8").splitlines()]

    def uploads(self):
        """[(files)] of every copy call, in order."""
        return [c["files"] for c in self.calls() if "copy" in c["argv"]]

    def bucket_files(self):
        return {p.relative_to(self.bucket).as_posix() for p in self.bucket.rglob("*") if p.is_file()} if self.bucket.is_dir() else set()

    def meta(self, rel):
        return json.loads((self.space / ".meta.json").read_text())[f"my-mod-files/mymod/{rel}"]

    def status(self):
        return json.loads((self.site / ".sync" / "status.json").read_text())


class SmallFunctionTests(unittest.TestCase):
    def test_mirrored(self):
        for ok in ("objects/ab/" + "0" * 64, "channels/stable.json", "releases/1.0.0/MyMod-1.0.0.7z", "downloader/3.1.2/x.exe"):
            self.assertTrue(dl_sync.mirrored(ok), ok)
        for no in ("channels", ".sync/wanted", "other/x", "releases/.hidden/x", "releases/1.0.0/x.part-1a", "objects//x", "readme.txt"):
            self.assertFalse(dl_sync.mirrored(no), no)

    def test_headers_by_prefix(self):
        self.assertEqual(dl_sync.headers("objects/ab/" + "0" * 64), ("public, max-age=31536000, immutable", "application/octet-stream"))
        self.assertEqual(dl_sync.headers("channels/stable.json"), ("no-cache", "application/json"))
        self.assertEqual(dl_sync.headers("releases/1.0.0/release-manifest.json"), ("public, max-age=3600", "application/json"))
        self.assertEqual(dl_sync.headers("releases/1.0.0/SHA256SUMS"), ("public, max-age=3600", "text/plain; charset=utf-8"))
        self.assertEqual(dl_sync.headers("releases/1.0.0/MyMod-1.0.0.7z")[1], "application/x-7z-compressed")
        self.assertEqual(dl_sync.headers("downloader/3.1.2/MyMod-Downloader.exe.sha256")[1], "text/plain; charset=utf-8")

    def test_phase_order(self):
        order = ["objects/ab/x", "releases/1.0.0/MyMod-1.0.0.7z", "releases/1.0.0/release-manifest.json", "channels/stable.json"]
        self.assertEqual([dl_sync.phase(r) for r in order], [0, 1, 2, 3])
        self.assertEqual(sorted(reversed(order), key=dl_sync.phase), order)


class SyncTests(SyncBase):
    def test_first_sync_uploads_everything_with_headers(self):
        s = self.sync()
        site_files = {p.relative_to(self.site).as_posix() for p in self.site.rglob("*") if p.is_file()}
        self.assertEqual(self.bucket_files(), site_files)
        self.assertEqual(s.uploaded, len(site_files))
        obj = next(r for r in site_files if r.startswith("objects/"))
        self.assertEqual(self.meta(obj), {"cache-control": "public, max-age=31536000, immutable",
                                          "content-type": "application/octet-stream", "acl": "public-read"})
        self.assertEqual(self.meta("channels/stable.json")["cache-control"], "no-cache")
        self.assertEqual(self.meta("channels/stable.json")["content-type"], "application/json")
        self.assertEqual(self.meta("releases/1.0.0/MyMod-1.0.0.7z")["content-type"], "application/x-7z-compressed")
        self.assertEqual(self.meta("releases/1.0.0/release-manifest.json")["cache-control"], "public, max-age=3600")

    def test_upload_order_is_objects_then_the_rest_then_manifests_then_channels(self):
        self.sync()
        groups = self.uploads()
        flat = [f for g in groups for f in g]
        kinds = [dl_sync.phase(f) for f in flat]
        self.assertEqual(kinds, sorted(kinds))
        self.assertEqual(kinds[0], 0)
        self.assertEqual(kinds[-1], 3)
        self.assertEqual(flat[-1], "channels/stable.json")

    def test_a_second_sync_uploads_nothing(self):
        self.sync()
        self.log.unlink()
        s = self.sync()
        self.assertEqual(s.uploaded, 0)
        self.assertEqual(self.uploads(), [])

    def test_only_what_changed_goes_up(self):
        self.sync()
        self.log.unlink()
        man = self.make_release("1.0.1", [b"one", b"three"])  # "one" is shared
        self.put("channels/stable.json", json.dumps({"version": "1.0.1", "manifestSha256": sha(man)}).encode())
        s = self.sync()
        sent = sorted(f for g in self.uploads() for f in g)
        self.assertEqual(sent, sorted(["objects/%s/%s" % (sha(b"three")[:2], sha(b"three")), "releases/1.0.1/MyMod-1.0.1.7z",
                                       "releases/1.0.1/SHA256SUMS", "releases/1.0.1/release-manifest.json", "channels/stable.json"]))
        self.assertEqual(s.uploaded, 5)
        self.assertEqual(json.loads((self.bucket / "channels/stable.json").read_text())["version"], "1.0.1")

    def test_an_object_with_the_wrong_size_on_the_bucket_is_replaced(self):
        self.sync()
        s = next(iter(self.objects))
        (self.bucket / "objects" / s[:2] / s).write_bytes(b"cu")
        self.log.unlink()
        self.sync()
        self.assertEqual((self.bucket / "objects" / s[:2] / s).read_bytes(), self.objects[s])

    def test_same_size_changed_json_is_found_by_its_hash(self):
        self.sync()
        p = self.site / "channels/stable.json"
        body = p.read_bytes()
        p.write_bytes(body.replace(b"1.0.0", b"1.0.9"))
        self.log.unlink()
        self.sync()
        self.assertEqual((self.bucket / "channels/stable.json").read_bytes(), p.read_bytes())

    def test_dot_files_temp_files_and_unknown_folders_stay_home(self):
        self.put(".sync/wanted", b"x")
        self.put(".sync/status.json", b"{}")
        self.put("objects/ab/" + "c" * 64 + ".part-12", b"half")
        self.put("incoming/x", b"x")
        self.put("notes.txt", b"x")
        self.put("releases/.hidden/x", b"x")
        self.sync()
        self.assertTrue(all(not any(seg.startswith(".") for seg in f.split("/")) and ".part-" not in f for f in self.bucket_files()))
        self.assertFalse({"notes.txt", "incoming/x"} & self.bucket_files())

    def test_what_gc_removed_is_removed_from_the_bucket(self):
        man = self.make_release("1.0.1", [b"only in 1.0.1"])
        self.sync()
        only = sha(b"only in 1.0.1")
        self.assertIn(f"objects/{only[:2]}/{only}", self.bucket_files())
        shutil.rmtree(self.site / "releases" / "1.0.1")
        (self.site / "objects" / only[:2] / only).unlink()
        s = self.sync()
        self.assertEqual(s.deleted, 3 + 1)
        self.assertNotIn(f"objects/{only[:2]}/{only}", self.bucket_files())
        self.assertFalse(any(f.startswith("releases/1.0.1") for f in self.bucket_files()))
        self.assertIn("releases/1.0.0/release-manifest.json", self.bucket_files())

    def test_no_delete_keeps_the_extra_files(self):
        self.sync()
        (self.bucket / "releases" / "0.9.0").mkdir(parents=True)
        (self.bucket / "releases" / "0.9.0" / "old.7z").write_bytes(b"old")
        s = self.sync(delete=False)
        self.assertEqual(s.deleted, 0)
        self.assertTrue((self.bucket / "releases/0.9.0/old.7z").is_file())

    def test_files_outside_the_known_folders_on_the_bucket_are_not_touched(self):
        self.sync()
        (self.bucket / "other").mkdir()
        (self.bucket / "other" / "x").write_bytes(b"x")
        self.assertEqual(self.sync().deleted, 0)
        self.assertTrue((self.bucket / "other/x").is_file())

    def test_an_object_a_release_still_uses_is_never_deleted(self):
        self.sync()
        s = next(iter(self.objects))
        (self.site / "objects" / s[:2] / s).unlink()  # lost on the site, still in a manifest
        with self.assertRaises(dl_sync.SyncError) as e:
            self.sync()
        self.assertIn("not deleting anything", str(e.exception))
        self.assertTrue((self.bucket / "objects" / s[:2] / s).is_file())

    def test_mass_deletes_need_a_flag(self):
        self.sync()
        for i in range(150):
            p = self.bucket / "objects" / "ff" / f"{i:064x}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        with self.assertRaises(dl_sync.SyncError) as e:
            self.sync()
        self.assertIn("--allow-mass-delete", str(e.exception))
        self.assertEqual(self.sync(mass_delete=True).deleted, 150)

    def test_an_empty_or_unmounted_site_is_not_mirrored(self):
        self.sync()
        shutil.rmtree(self.site / "channels")
        with self.assertRaises(dl_sync.SyncError) as e:
            self.sync()
        self.assertIn("looks empty or unmounted", str(e.exception))
        self.assertIn("channels/stable.json", self.bucket_files())

    def test_dry_run_changes_nothing(self):
        s = self.sync(dry_run=True)
        self.assertEqual((s.uploaded, self.bucket_files()), (0, set()))
        self.assertEqual([c for c in self.calls() if ("copy" in c["argv"] or "delete" in c["argv"])], [])
        self.assertFalse((self.state / "uploaded.json").exists())

    def test_a_json_file_is_uploaded_from_the_copy_taken_when_the_pass_began(self):
        """A channel file the ingest rewrites during the pass cannot reach the bucket before its release's objects."""
        real = dl_sync.Rclone.upload
        seen = []

        def spy(rc, src, rels, cc, ct):
            if "channels/stable.json" in rels:
                seen.append(Path(src))
            if rels and rels[0].startswith("objects/"):  # the ingest publishes while the objects go up
                (self.site / "channels/stable.json").write_bytes(b'{"version": "9.9.9"}')
            return real(rc, src, rels, cc, ct)
        with mock.patch.object(dl_sync.Rclone, "upload", spy):
            self.sync()
        self.assertEqual(seen, [self.state / "stage"])
        self.assertEqual(json.loads((self.bucket / "channels/stable.json").read_text())["version"], "1.0.0")


class MainTests(SyncBase):
    def test_main_syncs_and_writes_the_status(self):
        rc, out = self.main()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"done: \d+ uploaded")
        st = self.status()
        self.assertEqual((st["schemaVersion"], st["result"], st["remote"]), (1, "ok", REMOTE))
        self.assertEqual(st["lastSuccessAt"], st["finishedAt"])
        self.assertGreater(st["uploaded"], 0)
        rc, out = self.main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.status()["uploaded"], 0)

    def test_a_failed_upload_is_recorded_and_the_next_run_finishes_the_job(self):
        self.assertEqual(self.main()[0], 0)
        good = self.status()["lastSuccessAt"]
        self.put("channels/stable.json", b'{"version": "1.0.1"}')
        with env_patch(FAKE_RCLONE_FAIL="channels/"):
            rc, out = self.main()
        self.assertEqual(rc, 1)
        st = self.status()
        self.assertEqual(st["result"], "failed")
        self.assertIn("rclone copy failed", st["error"])
        self.assertEqual(st["lastSuccessAt"], good, "the last good run is remembered")
        self.assertEqual(json.loads((self.bucket / "channels/stable.json").read_text())["version"], "1.0.0")
        self.assertEqual(self.main()[0], 0)
        self.assertEqual(json.loads((self.bucket / "channels/stable.json").read_text())["version"], "1.0.1")

    def test_the_publisher_reads_the_status(self):
        self.assertIsNone(pub.sync_status(pub.DirTarget(self.site)))
        self.main()
        self.assertRegex(pub.sync_status(pub.DirTarget(self.site)), r"^Mirror sync: ok at \d{4}-.* \(\d+ uploaded, 0 deleted; mirror:my-mod-files/mymod\)$")
        self.put("channels/stable.json", b"{}")
        with env_patch(FAKE_RCLONE_FAIL="channels/"):
            self.main()
        line = pub.sync_status(pub.DirTarget(self.site))
        self.assertIn("Mirror sync: FAILED", line)
        self.assertIn("journalctl -u mod-release-sync", line)
        self.put(".sync/status.json", b"not json")
        self.assertEqual(pub.sync_status(pub.DirTarget(self.site)), "Mirror sync: status unreadable")

    def test_a_site_that_changes_during_a_pass_gets_another_pass(self):
        real = dl_sync.Sync.run_pass
        passes = []

        def once(sync):
            passes.append(1)
            if len(passes) == 1:  # the ingest touches .sync/wanted while the first pass runs
                self.put(".sync/wanted", b"ingest 1.0.1\n")
            return real(sync)
        with mock.patch.object(dl_sync.Sync, "run_pass", once):
            rc, out = self.main()
        self.assertEqual((rc, len(passes)), (0, 2), out)
        self.assertIn("once more", out)

    def test_dry_run_and_check_leave_no_status(self):
        rc, out = self.main("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("dry run", out)
        self.assertFalse((self.site / ".sync").exists())
        self.assertEqual(self.bucket_files(), set())

    def test_acl_can_be_switched_off(self):
        rc, out = self.main("--acl", "")
        self.assertEqual(rc, 0, out)
        self.assertFalse(any("--s3-acl" in c["argv"] for c in self.calls()))

    def test_a_running_sync_blocks_a_second_one(self):
        self.state.mkdir()
        held = dl_sync.lock(self.state / "lock", 0)
        try:
            rc, out = self.main()
            self.assertEqual(rc, 1)
            self.assertIn("another dl_sync is running", out)
        finally:
            os.close(held)
        self.assertEqual(self.main()[0], 0)

    def test_no_rclone(self):
        with mock.patch.object(dl_sync.shutil, "which", return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                rc = dl_sync.main(["--site", str(self.site), "--state", str(self.state), "--lock-wait", "0"])
        self.assertEqual(rc, 1)
        self.assertIn("no rclone", out.getvalue())


class CheckTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.main()[0], 0)

    def test_a_good_mirror_passes(self):
        rc, out = self.main("--check", "--full")
        self.assertEqual(rc, 0, out)
        self.assertIn("the bucket matches the site", out)

    def test_check_finds_a_missing_file_a_wrong_size_an_extra_file_and_bad_bytes_and_headers(self):
        s1, s2 = sorted(self.objects)
        (self.bucket / "objects" / s1[:2] / s1).unlink()
        (self.bucket / "objects" / s2[:2] / s2).write_bytes(b"x")
        (self.bucket / "releases" / "0.9.0").mkdir(parents=True)
        (self.bucket / "releases/0.9.0/old.7z").write_bytes(b"old")
        (self.bucket / "releases/1.0.0/SHA256SUMS").write_bytes(b"SUMS\n")  # same size, other bytes
        meta = json.loads((self.space / ".meta.json").read_text())
        meta["my-mod-files/mymod/channels/stable.json"]["cache-control"] = "public, max-age=9"
        (self.space / ".meta.json").write_text(json.dumps(meta))
        rc, out = self.main("--check", "--full")
        self.assertEqual(rc, 1)
        for want in (f"missing on the bucket: objects/{s1[:2]}/{s1}", f"size differs: objects/{s2[:2]}/{s2}",
                     "on the bucket only: releases/0.9.0/old.7z", "content differs (sha256): releases/1.0.0/SHA256SUMS",
                     "headers differ: channels/stable.json"):
            self.assertIn(want, out)

    def test_check_with_a_sample_always_hashes_channels_and_manifests(self):
        (self.bucket / "channels/stable.json").write_bytes(b'{"version": "6.6.6"}')
        self.put("channels/stable.json", b'{"version": "1.0.0"}')  # same size as the tampered one
        (self.bucket / "channels/stable.json").write_bytes(b'{"version": "6.6.6"}')
        rc, out = self.main("--check", "--sample", "0")
        self.assertEqual(rc, 1)
        self.assertIn("content differs (sha256): channels/stable.json", out)


if __name__ == "__main__":
    unittest.main()
