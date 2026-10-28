"""release/dl_ingest.py, the server half of a release upload, on temp folders (srv/mymod + srv/incoming). The uploads are made by
the real publisher, then damaged by the tests. Run: python -m unittest discover tests"""
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from test_release_support import BASE_FILES, ReleaseBase, dl_ingest, needs_7z, pub, sha, tree  # noqa: F401


class SafeRelTests(unittest.TestCase):
    def test_safe_rel(self):
        for ok in ("a", "a/b.txt", "MyMod.mpq/data/global/excel/armor.txt", "a b/c"):
            self.assertTrue(dl_ingest.safe_rel(ok), ok)
        for bad in ("", "/a", "a/", "a//b", "../a", "a/../b", "./a", "a\\b", "C:/x", "a/b.", "a /b", "a/b*", "a/b?", 'a/"b', "a/\x01",
                    "x" * 401, None, 5):
            self.assertFalse(dl_ingest.safe_rel(bad), repr(bad))

    def test_version_key_and_find_7z(self):
        self.assertLess(dl_ingest.version_key("1.2.0-rc1"), dl_ingest.version_key("1.2.0"))
        self.assertLess(dl_ingest.version_key("1.9.0"), dl_ingest.version_key("1.10.0"))
        self.assertEqual(dl_ingest.find_7z("/x/7z"), "/x/7z")


class FolderTests(unittest.TestCase):
    def test_site_and_incoming_must_be_real_folders_in_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                dl_ingest.Ingest(d, "mymod")
            (Path(d) / "mymod").mkdir()
            with self.assertRaises(SystemExit):
                dl_ingest.Ingest(d, "mymod")  # no incoming
            (Path(d) / "incoming").mkdir()
            dl_ingest.Ingest(d, "mymod")


@needs_7z
class IngestTests(ReleaseBase):
    def upload(self, version="1.0.0", **kw):
        return self.publish(version, ingest=False, **kw)

    # ------------------------------------------------------------------------------------------------ the good path
    def test_two_uploads_are_published_in_version_order(self):
        self.upload("1.0.1")
        self.upload("1.0.0", channel="stable")
        self.ingest()
        self.assertEqual(self.channel("test")["version"], "1.0.1")
        self.assertEqual(self.channel("stable")["version"], "1.0.0")
        self.assertEqual(sorted(p.name for p in (self.site / "releases").iterdir()), ["1.0.0", "1.0.1"])
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_the_channel_file_is_written_last(self):
        self.upload()
        written = []
        real = dl_ingest.write_atomic

        def spy(dest, *a, **kw):
            written.append(Path(dest).relative_to(self.site).as_posix())
            return real(dest, *a, **kw)
        with mock.patch.object(dl_ingest, "write_atomic", spy):
            self.ingest()
        self.assertEqual(written[-2:], ["channels/test.json", ".sync/wanted"])
        self.assertEqual([w for w in written if w.startswith("channels/")], ["channels/test.json"])
        self.assertLess(written.index("releases/1.0.0/release-manifest.json"), written.index("channels/test.json"))
        self.assertLess(max(i for i, w in enumerate(written) if w.startswith("objects/")), written.index("releases/1.0.0/MyMod-1.0.0.7z"))

    def test_no_channel_in_ready_writes_no_channel_file(self):
        self.upload(channel=None)
        self.ingest()
        self.assertTrue((self.site / "releases/1.0.0/release-manifest.json").is_file())
        self.assertFalse((self.site / "channels").exists())

    def test_a_sync_request_follows_a_publish_and_only_a_publish(self):
        self.ingest()
        self.assertFalse((self.site / ".sync" / "wanted").exists(), "nothing to do: no request")
        self.upload()
        self.ingest()
        self.assertIn("ingest 1.0.0", (self.site / ".sync" / "wanted").read_text())

    def test_publishing_twice_with_the_same_contents_keeps_the_archive(self):
        self.publish("1.0.0")
        arch = self.site / "releases/1.0.0/MyMod-1.0.0.7z"
        before = (arch.read_bytes(), (self.site / "releases/1.0.0/release-manifest.json").read_bytes())
        pub.publish(pub.DirTarget(self.site), self.tmp / "dist-1.0.0", "1.0.0", "test", force=True, log=lambda *_: None)
        self.ingest()
        self.assertEqual((arch.read_bytes(), (self.site / "releases/1.0.0/release-manifest.json").read_bytes()), before)

    def test_the_server_7z_is_the_same_bytes_as_the_packagers(self):
        self.publish("1.0.0")
        packaged = (self.tmp / "dist-1.0.0" / "MyMod-1.0.0.7z").read_bytes()
        self.assertEqual((self.site / "releases/1.0.0/MyMod-1.0.0.7z").read_bytes(), packaged)

    def test_the_manifest_gets_the_servers_archive_entry(self):
        self.publish("1.0.0")
        man = json.loads((self.site / "releases/1.0.0/release-manifest.json").read_text())
        arch = self.site / "releases/1.0.0/MyMod-1.0.0.7z"
        self.assertEqual(man["archive"], {"format": "7z", "name": arch.name, "size": arch.stat().st_size,
                                          "sha256": pub.sha256_file(arch), "path": "releases/1.0.0/MyMod-1.0.0.7z"})

    def test_leftover_temp_files_are_cleaned_when_old_enough(self):
        old, young = self.site / "objects" / "ab" / "x.part-1", self.site / "releases" / "y.part-2"
        for p in (old, young):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        t = time.time() - 2 * dl_ingest.PART_AGE
        os.utime(old, (t, t))
        self.ingest()
        self.assertFalse(old.exists())
        self.assertTrue(young.exists(), "may belong to an upload that is still running")

    def test_folders_still_being_uploaded_are_left_alone(self):
        self.upload()
        shutil.copytree(self.incoming / "1.0.0", self.incoming / "1.0.5.uploading")
        (self.incoming / "1.0.5.uploading" / "READY").write_text("{}")
        self.ingest()
        self.assertTrue((self.incoming / "1.0.5.uploading").is_dir())
        self.assertFalse((self.site / "releases" / "1.0.5").exists())

    def test_a_custom_channel_list(self):
        self.upload(channel="test")
        self.ingest(expect=1, channels=("stable", "beta"))
        self.assertIn("unknown channel", (self.incoming / "1.0.0" / "REFUSED").read_text())

    # ------------------------------------------------------------------------------------------------ refusals
    def refused(self, version="1.0.0"):
        self.ingest(expect=1)
        text = (self.incoming / version / "REFUSED").read_text()
        self.assertFalse((self.site / "channels" / "test.json").exists(), "nothing was published: " + text)
        self.assertFalse((self.incoming / version / ".work").exists(), "the scratch folder is gone")
        return text

    def edit_ready(self, **changes):
        p = self.incoming / "1.0.0" / "READY"
        r = json.loads(p.read_text())
        r.update(changes)
        p.write_text(json.dumps(r))

    def test_damaged_pack(self):
        self.upload()
        pack = self.incoming / "1.0.0" / self.ready("1.0.0")["objectsPack"]
        data = bytearray(pack.read_bytes())
        data[10] ^= 0xFF
        pack.write_bytes(bytes(data))
        self.assertIn("does not match READY", self.refused())

    def test_extra_file_that_ready_does_not_list(self):
        self.upload()
        (self.incoming / "1.0.0" / "extra.txt").write_text("x")
        self.assertIn("files that READY does not list: extra.txt", self.refused())

    def test_missing_file_that_ready_lists(self):
        self.upload()
        (self.incoming / "1.0.0" / "RELEASE_NOTES-1.0.0.md").unlink()
        self.assertIn("RELEASE_NOTES-1.0.0.md is missing", self.refused())

    def test_bad_ready(self):
        self.upload()
        self.edit_ready(schemaVersion=2)
        self.assertIn("READY schemaVersion 2", self.refused())

    def test_ready_for_another_version(self):
        self.upload()
        self.edit_ready(version="1.0.9")
        self.assertIn("READY is for version", self.refused())

    def test_ready_that_is_not_json(self):
        self.upload()
        (self.incoming / "1.0.0" / "READY").write_text("not json")
        self.assertIn("READY is not readable JSON", self.refused())

    def test_ready_that_lists_a_file_it_may_not(self):
        self.upload()
        files = self.ready("1.0.0")["files"]
        files["../evil"] = {"size": 1, "sha256": "0" * 64}
        self.edit_ready(files=files)
        self.assertIn("a file it may not", self.refused())

    def test_ready_whose_launcher_and_file_list_disagree(self):
        self.upload()
        self.edit_ready(launcher="MyMod-Downloader.exe")
        self.assertIn("launcher entry and its file list disagree", self.refused())

    def test_manifest_problems(self):
        self.upload()
        p = self.incoming / "1.0.0" / "release-manifest.json"
        good = json.loads(p.read_text())
        cases = {
            "schemaVersion": ({"schemaVersion": 1}, "manifest schemaVersion"),
            "version": ({"version": "1.0.1"}, "the manifest is version"),
            "objectsBase": ({"objectsBase": "../objects/"}, "objectsBase"),
            "modFolder": ({"modFolder": "../MyMod"}, "bad modFolder"),
            "count": ({"fileCount": 1}, "fileCount / totalSize"),
            "emptyDirs": ({"emptyDirs": ["../x"]}, "bad emptyDirs"),
            "launcher": ({"launcher": {"version": "1.0.0"}}, "names a launcher, but none was uploaded"),
        }
        for name, (change, want) in cases.items():
            with self.subTest(name):
                p.write_text(json.dumps({**good, **change}))
                ready = self.ready("1.0.0")
                ready["files"]["release-manifest.json"] = {"size": p.stat().st_size, "sha256": pub.sha256_file(p)}
                self.edit_ready(files=ready["files"])
                (self.incoming / "1.0.0" / "REFUSED").unlink(missing_ok=True)
                self.ingest(expect=1)
                self.assertIn(want, (self.incoming / "1.0.0" / "REFUSED").read_text())

    def test_manifest_entries(self):
        self.upload()
        p = self.incoming / "1.0.0" / "release-manifest.json"
        good = json.loads(p.read_text())
        a, b = dict(good["files"][0]), dict(good["files"][1])
        cases = {
            "../escape": ([{**a, "path": "../escape.txt"}] + good["files"][1:], "bad manifest entry"),
            "bad sha": ([{**a, "sha256": "xyz"}] + good["files"][1:], "bad manifest entry"),
            "negative size": ([{**a, "size": -1}] + good["files"][1:], "bad manifest entry"),
            "duplicate": ([a, {**b, "path": a["path"].upper()}] + good["files"][2:], "duplicate path"),
            "empty": ([], "lists no files"),
        }
        for name, (files, want) in cases.items():
            with self.subTest(name):
                p.write_text(json.dumps({**good, "files": files}))
                ready = self.ready("1.0.0")
                ready["files"]["release-manifest.json"] = {"size": p.stat().st_size, "sha256": pub.sha256_file(p)}
                self.edit_ready(files=ready["files"])
                (self.incoming / "1.0.0" / "REFUSED").unlink(missing_ok=True)
                self.ingest(expect=1)
                self.assertIn(want, (self.incoming / "1.0.0" / "REFUSED").read_text())

    def test_incomplete_manifest_never_reaches_stable(self):
        ready = {"channel": "stable"}
        man = json.dumps({"schemaVersion": 2, "version": "1.0.0", "objectsBase": "objects/", "modFolder": "MyMod", "complete": False,
                          "files": [{"path": "a", "size": 1, "sha256": "0" * 64}]}).encode()
        with self.assertRaises(dl_ingest.Refused) as e:
            dl_ingest.check_manifest(man, "1.0.0", ready)
        self.assertIn("not for the stable channel", str(e.exception))
        dl_ingest.check_manifest(man, "1.0.0", {"channel": "test"})

    def test_a_missing_object_is_refused_with_a_hint(self):
        self.publish("1.0.0")
        self.upload("1.0.1")  # armor, weapons ... are on the host, so the pack holds only the new bytes
        victim = self.site / pub.object_path(sha(BASE_FILES["global/excel/armor.txt"]))
        victim.unlink()
        self.ingest(expect=1)
        self.assertIn("objects the host does not have", (self.incoming / "1.0.1" / "REFUSED").read_text())
        self.assertEqual(self.channel("test")["version"], "1.0.0", "the channel still shows the old version")

    def test_a_different_published_version_is_refused_without_force(self):
        self.publish("1.0.0")
        other = self.make_dist("1.0.0", files={"global/excel/armor.txt": b"other\n"})
        pub.publish(pub.DirTarget(self.site), other, "1.0.0", "test", force=True, log=lambda *_: None)
        self.edit_ready(force=False)
        self.ingest(expect=1)
        self.assertIn("never changes", (self.incoming / "1.0.0" / "REFUSED").read_text())
        self.assertNotIn(sha(b"other\n").encode(), (self.site / "releases/1.0.0/release-manifest.json").read_bytes())

    def test_force_replaces_a_published_version(self):
        self.publish("1.0.0")
        other = self.make_dist("1.0.0", files={"global/excel/armor.txt": b"other\n"})
        pub.publish(pub.DirTarget(self.site), other, "1.0.0", "test", force=True, log=lambda *_: None)
        self.ingest()
        self.assertIn(sha(b"other\n").encode(), (self.site / "releases/1.0.0/release-manifest.json").read_bytes())

    def test_no_7zip_on_the_server(self):
        self.upload()
        with mock.patch("builtins.print"):
            self.assertEqual(dl_ingest.Ingest(self.srv, "mymod", sevenzip=None).run(), 1)
        self.assertIn("no 7-Zip", (self.incoming / "1.0.0" / "REFUSED").read_text())

    def test_a_refused_upload_is_skipped_until_retried(self):
        self.upload()
        junk = self.incoming / "1.0.0" / "junk.txt"
        junk.write_text("x")
        self.ingest(expect=1)
        self.ingest(expect=0)  # skipped: it has REFUSED
        self.assertTrue((self.incoming / "1.0.0").is_dir())
        junk.unlink()
        with mock.patch("builtins.print"):
            rc = dl_ingest.main(["--root", str(self.srv), "--site", "mymod", "--7z", self.seven, "--retry", "1.0.0"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.channel("test")["version"], "1.0.0")

    def test_main_rejects_bad_arguments(self):
        with self.assertRaises(SystemExit):
            dl_ingest.main(["--root", str(self.srv), "--site", "mymod", "--channels", "Bad Name"])
        with self.assertRaises(SystemExit):
            dl_ingest.main(["--root", str(self.srv), "--site", "mymod", "--7z", self.seven, "--retry", "../x"])
        with self.assertRaises(SystemExit):
            dl_ingest.main(["--root", str(self.srv), "--site", "nosuch"])

    # ------------------------------------------------------------------------------------------------ the objects 7z
    def make_pack(self, files: dict) -> Path:
        src = self.tmp / "packsrc"
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir()
        for name, body in files.items():
            p = src / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
        out = self.tmp / "pack.7z"
        out.unlink(missing_ok=True)
        subprocess.run([self.seven, "a", "-t7z", "-bd", "-y", str(out), "*"], cwd=src, check=True, capture_output=True)
        return out

    def test_list_pack_accepts_plain_objects_named_by_sha(self):
        body = b"object bytes"
        pack = self.make_pack({sha(body): body})
        self.assertEqual(dl_ingest.list_pack(self.seven, pack, {sha(body): len(body)}), {sha(body): len(body)})

    def test_list_pack_refuses_everything_else(self):
        body = b"object bytes"
        good = {sha(body): len(body)}
        cases = {
            "a name that is not a sha": ({"evil.txt": body}, "not an object"),
            "a folder": ({f"sub/{sha(body)}": body}, "not an object"),
            "an object the manifest does not use": ({sha(b"x"): b"x"}, "does not use"),
            "the wrong size": ({sha(body): body + b"!"}, "wrong size"),
        }
        for name, (files, want) in cases.items():
            with self.subTest(name):
                with self.assertRaises(dl_ingest.Refused) as e:
                    dl_ingest.list_pack(self.seven, self.make_pack(files), good)
                self.assertIn(want, str(e.exception))
        with self.assertRaises(dl_ingest.Refused):
            dl_ingest.list_pack(self.seven, self.tmp / "not-a-7z.7z", good)

    def test_a_pack_with_a_wrongly_named_object_is_refused_whole(self):
        """The pack's entries pass the listing, but the bytes do not hash to their name."""
        self.upload()
        ready = self.ready("1.0.0")
        man = json.loads((self.incoming / "1.0.0" / "release-manifest.json").read_text())
        wanted = {f["sha256"]: f["size"] for f in man["files"]}
        sha0, size0 = next(iter(sorted(wanted.items())))
        files = {s: b"x" * n for s, n in wanted.items()}  # right names and sizes, wrong bytes
        pack = self.make_pack(files)
        name = "objects-" + pub.sha256_file(pack)[:16] + ".7z"
        (self.incoming / "1.0.0" / ready["objectsPack"]).unlink()
        shutil.copyfile(pack, self.incoming / "1.0.0" / name)
        ready["files"].pop(ready["objectsPack"])
        ready["files"][name] = {"size": pack.stat().st_size, "sha256": pub.sha256_file(pack)}
        self.edit_ready(files=ready["files"], objectsPack=name)
        self.ingest(expect=1)
        self.assertIn("object is damaged", (self.incoming / "1.0.0" / "REFUSED").read_text())
        self.assertEqual([p for p in (self.site / "objects").rglob("*") if p.is_file()], [], "nothing was moved into the site")


if __name__ == "__main__":
    unittest.main()
