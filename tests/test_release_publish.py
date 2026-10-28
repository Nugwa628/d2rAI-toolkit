"""release/publish_release.py against a folder (DirTarget), against a fake sftp (SftpTarget), and over a local HTTP server
(verify). The server half is dl_ingest.py, run in-process on the same folders. No network.
Run: python -m unittest discover tests"""
import contextlib
import io
import json
import os
import struct
import subprocess
import sys
import unittest
from unittest import mock

from test_release_support import (BASE_FILES, FAKE_SFTP, RELEASE_DIR, ReleaseBase, env_patch, make_mod, needs_7z,  # noqa: F401
                                  serving, sha, tree)

import package_release as pr  # noqa: E402
import publish_release as pub  # noqa: E402
import release_config  # noqa: E402

QUIET = lambda *_: None  # noqa: E731


def exe_with_version(major, minor, patch, pad=b"MZ fake exe\0"):
    """Bytes with a VS_FIXEDFILEINFO the way a Windows linker writes it: the UTF-16 key, padding, the signature, the version."""
    key = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0\0\0"
    return pad + key + bytes.fromhex("bd04effe") + struct.pack("<III", 0x10000, (major << 16) | minor, patch << 16)


class SmallFunctionTests(unittest.TestCase):
    def test_version_key_orders_a_prerelease_before_its_release(self):
        order = ["1.0.0", "1.0.1", "1.1.0-rc1", "1.1.0", "1.10.0", "2.0.0-beta"]
        self.assertEqual(sorted(reversed(order), key=pub.version_key), ["1.0.0", "1.0.1", "1.1.0-rc1", "1.1.0", "1.10.0", "2.0.0-beta"])

    def test_check_version(self):
        self.assertEqual(pub.check_version("v1.2.3"), "1.2.3")
        self.assertEqual(pub.check_version("1.2.3-rc1"), "1.2.3-rc1")
        for bad in ("1.2", "latest", "1.2.3/../x", "1.2.3 "):
            with self.assertRaises(SystemExit):
                pub.check_version(bad)

    def test_exe_version(self):
        d = self.enterContext(__import__("tempfile").TemporaryDirectory())
        p = os.path.join(d, "a.exe")
        with open(p, "wb") as f:
            f.write(exe_with_version(3, 1, 2))
        self.assertEqual(pub.exe_version(p), "3.1.2")
        with open(p, "wb") as f:
            f.write(b"MZ the same signature bytes bd04effe appear in code, but no key")
        self.assertEqual(pub.exe_version(p), "")

    def test_object_path(self):
        s = "ab" + "0" * 62
        self.assertEqual(pub.object_path(s), f"objects/ab/{s}")

    def test_sftp_url(self):
        t = pub.SftpTarget.parse("sftp://modrelease@example.invalid:2222/mymod/")
        self.assertEqual((t.user, t.host, t.port, t.path), ("modrelease", "example.invalid", 2222, "/mymod"))
        t = pub.SftpTarget.parse("sftp://modrelease@example.invalid/~/site")
        self.assertEqual((t.port, t.path), (None, "site"))
        with self.assertRaises(SystemExit):
            pub.SftpTarget.parse("http://example.invalid/x")

    def test_sftp_argv_and_quote(self):
        t = pub.SftpTarget("modrelease", "example.invalid", 2222, "/mymod", identity="/k/id", sftp_cmd=["sftp"])
        a = t._argv()
        self.assertEqual(a[:3], ["sftp", "-b", "-"])
        self.assertIn("-oBatchMode=yes", a)
        self.assertEqual(a[a.index("-P") + 1], "2222")
        self.assertEqual(a[a.index("-i") + 1], "/k/id")
        self.assertEqual(a[-1], "modrelease@example.invalid")
        self.assertEqual(pub.sftp_quote('a b"c'), '"a b\\"c"')

    def test_sftp_command_comes_from_the_environment(self):
        with env_patch(**{release_config.ENV_SFTP: "ssh-wrapper --flag"}):
            self.assertEqual(pub.SftpTarget("u", "h", None, "/x").cmd, ["ssh-wrapper", "--flag"])

    def test_parse_sftp_ls(self):
        lines = ["drwxr-xr-x    2 1000     1000         4096 Jan  1 00:00 ab", "-rw-r--r--    1 1000     1000          123 Jan  1 00:00 f.txt",
                 "sftp> junk"]
        self.assertEqual(pub.parse_sftp_ls(lines), {"ab": (4096, True), "f.txt": (123, False)})

    def test_open_target(self):
        with env_patch(**{release_config.ENV_TARGET: ""}):
            with self.assertRaises(SystemExit):
                pub.open_target(None)
            with self.assertRaises(SystemExit):
                pub.open_target("dir:<your-server>")
            with self.assertRaises(SystemExit):
                pub.open_target("ftp://x")
            self.assertIsInstance(pub.open_target("dir:/tmp/x"), pub.DirTarget)
            self.assertIsInstance(pub.open_target("sftp://u@h/p"), pub.SftpTarget)
            self.assertEqual(pub.open_target(None, cfg={"site": {"target": "dir:/tmp/y"}}).root.as_posix(), "/tmp/y")
        with env_patch(**{release_config.ENV_TARGET: "dir:/tmp/z"}):
            self.assertEqual(pub.open_target(None, cfg={"site": {"target": "dir:/tmp/y"}}).root.as_posix(), "/tmp/z", "env beats the file")
            self.assertEqual(pub.open_target("dir:/tmp/w").root.as_posix(), "/tmp/w", "the option beats both")


@needs_7z
class PublishDirTests(ReleaseBase):
    def test_publish_then_ingest_lays_out_the_site(self):
        info = self.publish("1.0.0", "test")
        self.assertEqual(info["state"], "uploaded")
        self.assertEqual(sorted(p.name for p in self.site.iterdir()), [".sync", "channels", "objects", "releases"])
        rel = self.site / "releases" / "1.0.0"
        self.assertEqual(sorted(p.name for p in rel.iterdir()),
                         ["MyMod-1.0.0.7z", "RELEASE_NOTES-1.0.0.md", "SHA256SUMS", "release-manifest.json"])
        man = json.loads((rel / "release-manifest.json").read_text())
        self.assertEqual(man["schemaVersion"], 2)
        self.assertEqual(man["objectsBase"], "objects/")
        self.assertEqual(man["archive"]["path"], "releases/1.0.0/MyMod-1.0.0.7z")
        self.assertEqual(man["archive"]["sha256"], pub.sha256_file(rel / "MyMod-1.0.0.7z"))
        self.assertNotIn("launcher", man)
        for f in man["files"]:
            self.assertEqual(pub.sha256_file(self.site / pub.object_path(f["sha256"])), f["sha256"])
        distinct = {f["sha256"] for f in man["files"]}
        self.assertEqual(len(list((self.site / "objects").rglob("*"))) - len(list((self.site / "objects").iterdir())), len(distinct))
        self.assertLess(len(distinct), len(man["files"]), "two files with the same bytes are one object")
        ch = self.channel("test")
        self.assertEqual((ch["schemaVersion"], ch["version"], ch["manifest"]), (1, "1.0.0", "releases/1.0.0/release-manifest.json"))
        self.assertEqual(ch["manifestSha256"], pub.sha256_file(rel / "release-manifest.json"))
        self.assertRegex(ch["publishedAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertFalse((self.site / "channels" / "stable.json").exists())
        self.assertEqual([p.name for p in self.incoming.iterdir()], [], "the ingest cleans up incoming")
        sums = (rel / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 3)
        self.assertIn(f"{man['archive']['sha256']}  MyMod-1.0.0.7z", sums)

    def test_the_7z_the_server_builds_holds_the_mod_folder(self):
        self.publish("1.0.0")
        out = self.tmp / "unpacked"
        subprocess.run([self.seven, "x", "-y", f"-o{out}", str(self.site / "releases/1.0.0/MyMod-1.0.0.7z")], check=True, capture_output=True)
        self.assertEqual(tree(out / "MyMod")["MyMod.mpq/data/global/excel/armor.txt"], BASE_FILES["global/excel/armor.txt"])
        self.assertTrue((out / "MyMod" / "d2rloader" / "patches").is_dir(), "empty folders come back too")

    def test_ready_is_written_last_and_the_folder_is_renamed_after_it(self):
        order = []

        class Spy(pub.DirTarget):
            def put_files(self, items):
                order.extend(rel for _, rel in items)
                super().put_files(items)

            def deliver(self, up, final, items, ready, listing):
                order.append(("deliver", up, final))
                super().deliver(up, final, items, ready, listing)

        self.publish("1.0.0", target=Spy(self.site), ingest=False)
        up = "../incoming/1.0.0.uploading"
        self.assertEqual(order[0], ("deliver", up, "../incoming/1.0.0"))
        names = [o.rsplit("/", 1)[1] for o in order[1:]]
        self.assertEqual(names[-1], "READY")
        self.assertEqual(set(names), {"release-manifest.json", "RELEASE_NOTES-1.0.0.md", "READY"} | {n for n in names if n.startswith("objects-")})
        self.assertTrue(names.index("release-manifest.json") > [i for i, n in enumerate(names) if n.startswith("objects-")][0])
        self.assertFalse((self.incoming / "1.0.0.uploading").exists())
        self.assertTrue((self.incoming / "1.0.0" / "READY").is_file())

    def test_ready_file_v1(self):
        self.publish("1.0.0", "test", ingest=False)
        ready = self.ready("1.0.0")
        self.assertEqual(sorted(ready), ["channel", "files", "force", "launcher", "objectCount", "objectsPack", "schemaVersion", "version"])
        self.assertEqual((ready["schemaVersion"], ready["version"], ready["channel"], ready["force"], ready["launcher"]),
                         (1, "1.0.0", "test", False, None))
        self.assertRegex(ready["objectsPack"], r"^objects-[0-9a-f]{16}\.7z$")
        self.assertEqual(set(ready["files"]), {ready["objectsPack"], "release-manifest.json", "RELEASE_NOTES-1.0.0.md"})
        pack = self.incoming / "1.0.0" / ready["objectsPack"]
        self.assertEqual(ready["files"][ready["objectsPack"]], {"size": pack.stat().st_size, "sha256": pub.sha256_file(pack)})
        self.assertTrue(ready["objectsPack"].startswith("objects-" + ready["files"][ready["objectsPack"]]["sha256"][:16]))
        man = json.loads((self.incoming / "1.0.0" / "release-manifest.json").read_text())
        self.assertNotIn("archive", man, "the server builds the 7z")
        self.assertEqual(man["objectsBase"], "objects/")

    def test_no_channel_uploads_only(self):
        self.publish("1.0.0", None)
        self.assertFalse((self.incoming / "1.0.0").exists())
        self.assertTrue((self.site / "releases/1.0.0/release-manifest.json").is_file())
        self.assertFalse((self.site / "channels").exists() and any((self.site / "channels").iterdir()))

    def test_second_version_uploads_only_what_is_new(self):
        self.publish("1.0.0")
        files = dict(BASE_FILES, **{"global/excel/armor.txt": b"armor table, changed\n"})
        self.publish("1.0.1", files=files, ingest=False)
        ready = self.ready("1.0.1")
        # armor.txt and the plugin metadata file (it names the version) are the only new bytes
        self.assertEqual(ready["objectCount"], 2)
        self.ingest()
        self.assertEqual(self.channel("test")["version"], "1.0.1")
        small = (self.site / "releases/1.0.1/release-manifest.json").read_bytes()
        self.assertIn(sha(b"armor table, changed\n").encode(), small)
        self.assertTrue((self.site / pub.object_path(sha(b"armor table, changed\n"))).is_file())

    def test_same_release_again_changes_nothing_but_moves_the_channel(self):
        self.publish("1.0.0", "test")
        dist = self.tmp / "dist-1.0.0"
        before = tree(self.site / "releases")
        info = pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "stable", log=QUIET)
        self.assertEqual(info["state"], "published")
        self.assertEqual(tree(self.site / "releases"), before)
        self.assertEqual(self.channel("stable")["version"], "1.0.0")
        self.assertEqual([p.name for p in self.incoming.iterdir()], [])

    def test_a_waiting_upload_is_not_uploaded_twice(self):
        self.publish("1.0.0", ingest=False)
        dist = self.tmp / "dist-1.0.0"
        info = pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", log=QUIET)
        self.assertEqual(info["state"], "waiting")
        self.ingest()
        self.assertEqual(self.channel("test")["version"], "1.0.0")

    def test_another_upload_of_the_same_version_is_refused_until_forced(self):
        self.publish("1.0.0", ingest=False)
        other = self.make_dist("1.0.0", files={"global/excel/armor.txt": b"something else\n"})
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(pub.DirTarget(self.site), other, "1.0.0", "test", log=QUIET)
        self.assertIn("another upload", str(e.exception))
        info = pub.publish(pub.DirTarget(self.site), other, "1.0.0", "test", force=True, log=QUIET)
        self.assertEqual(info["state"], "uploaded")
        self.assertTrue(self.ready("1.0.0")["force"])
        self.ingest()
        self.assertTrue((self.site / pub.object_path(sha(b"something else\n"))).is_file())

    def test_a_published_version_never_changes_unless_forced(self):
        self.publish("1.0.0")
        other = self.make_dist("1.0.0", files={"global/excel/armor.txt": b"something else\n"})
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(pub.DirTarget(self.site), other, "1.0.0", "test", log=QUIET)
        self.assertIn("never changes", str(e.exception))
        pub.publish(pub.DirTarget(self.site), other, "1.0.0", "test", force=True, log=QUIET)
        self.ingest()
        man = json.loads((self.site / "releases/1.0.0/release-manifest.json").read_text())
        self.assertIn(sha(b"something else\n"), {f["sha256"] for f in man["files"]})

    def test_published_but_objects_lost_needs_force(self):
        self.publish("1.0.0")
        victim = next((self.site / "objects").rglob("*.*"), None) or next(p for p in (self.site / "objects").rglob("*") if p.is_file())
        victim.unlink()
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(pub.DirTarget(self.site), self.tmp / "dist-1.0.0", "1.0.0", "test", log=QUIET)
        self.assertIn("--force", str(e.exception))

    def test_a_refused_upload_stays_for_a_look_and_blocks_until_forced(self):
        self.publish("1.0.0", ingest=False)
        (self.incoming / "1.0.0" / "release-manifest.json").write_bytes(b"{}")  # damaged after the upload
        self.ingest(expect=1)
        refused = (self.incoming / "1.0.0" / "REFUSED").read_text()
        self.assertIn("does not match READY", refused)
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(pub.DirTarget(self.site), self.tmp / "dist-1.0.0", "1.0.0", "test", log=QUIET)
        self.assertIn("refused", str(e.exception))
        self.assertIn("does not match READY", str(e.exception))
        pub.publish(pub.DirTarget(self.site), self.tmp / "dist-1.0.0", "1.0.0", "test", force=True, log=QUIET)
        self.ingest()
        self.assertEqual(self.channel("test")["version"], "1.0.0")
        self.assertFalse((self.incoming / "1.0.0").exists())

    def test_incomplete_packages_are_not_for_players(self):
        dist = self.make_dist("1.0.0-rc1", complete=False)
        target = pub.DirTarget(self.site)
        with self.assertRaises(pub.PublishError):
            pub.publish(target, dist, "1.0.0-rc1", "test", log=QUIET)
        with self.assertRaises(pub.PublishError):
            pub.publish(target, dist, "1.0.0-rc1", "stable", allow_incomplete=True, log=QUIET)
        pub.publish(target, dist, "1.0.0-rc1", "test", allow_incomplete=True, log=QUIET)
        self.ingest()
        self.assertTrue((self.site / "releases/1.0.0-rc1/MyMod-1.0.0-rc1-incomplete.7z").is_file())
        with self.assertRaises(pub.PublishError) as e:
            pub.promote(target, "1.0.0-rc1", "stable", log=QUIET)
        self.assertIn("complete=false", str(e.exception))

    def test_bad_input_is_refused_before_anything_is_written(self):
        target = pub.DirTarget(self.site)
        dist = self.make_dist("1.0.0")
        with self.assertRaises(SystemExit):
            pub.publish(target, dist, "latest", "test", log=QUIET)
        with self.assertRaises(pub.PublishError):
            pub.publish(target, dist, "1.0.1", "test", log=QUIET)  # the manifest is 1.0.0
        with self.assertRaises(pub.PublishError):
            pub.publish(target, self.tmp / "nowhere", "1.0.0", "test", log=QUIET)
        man = json.loads((dist / "release-manifest.json").read_text())
        man["schemaVersion"] = 1
        (dist / "release-manifest.json").write_text(json.dumps(man))
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(target, dist, "1.0.0", "test", log=QUIET)
        self.assertIn("schemaVersion", str(e.exception))
        man["schemaVersion"] = 2
        man["files"][0]["path"] = "../escape.txt"
        (dist / "release-manifest.json").write_text(json.dumps(man))
        with self.assertRaises(pub.PublishError):
            pub.publish(target, dist, "1.0.0", "test", log=QUIET)
        man["files"][0]["path"] = "ok.txt"
        (dist / "release-manifest.json").write_text(json.dumps(man))
        (dist / "MyMod-1.0.0.7z").write_bytes(b"not the archive")
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(target, dist, "1.0.0", "test", log=QUIET)
        self.assertIn("archive.sha256", str(e.exception))
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_the_objects_7z_of_an_earlier_run_is_reused(self):
        dist = self.make_dist("1.0.0")
        lines = []
        pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", log=lines.append)
        pack = self.ready("1.0.0")["objectsPack"]
        import shutil
        shutil.rmtree(self.incoming / "1.0.0")  # as if the upload had been lost
        lines.clear()
        pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", log=lines.append)
        self.assertTrue(any("reused" in ln for ln in lines), lines)
        self.assertEqual(self.ready("1.0.0")["objectsPack"], pack, "the same bytes: the same name")

    def test_a_stage_folder_can_be_the_source(self):
        dist = self.make_dist("1.0.0")
        stage = pr.stage(make_mod(self.tmp / "src-1.0.0", "MyMod", BASE_FILES, "1.0.0"), self.tmp / "stage" / "MyMod")
        pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", source=pub.DirSource(stage), log=QUIET)
        self.ingest()
        self.assertEqual(self.channel("test")["version"], "1.0.0")

    def test_a_stage_folder_that_differs_from_the_manifest_is_caught(self):
        dist = self.make_dist("1.0.0")
        stage = pr.stage(make_mod(self.tmp / "src-x", "MyMod", {"global/excel/armor.txt": b"changed\n"} | {k: v for k, v in BASE_FILES.items() if "armor" not in k}, "1.0.0"),
                         self.tmp / "stage" / "MyMod")
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", source=pub.DirSource(stage), log=QUIET)
        self.assertIn("does not match the manifest", str(e.exception))

    # ------------------------------------------------------------------------------------------------ the downloader
    def test_downloader_is_published_with_its_own_version(self):
        exe = self.make_exe(body=exe_with_version(3, 1, 2))
        self.publish("1.0.0", downloader=exe, ingest=False)
        ready = self.ready("1.0.0")
        self.assertEqual(ready["launcher"], "MyMod-Downloader.exe")
        man = json.loads((self.incoming / "1.0.0" / "release-manifest.json").read_text())
        self.assertEqual(man["launcher"], {"version": "3.1.2", "path": "downloader/3.1.2/MyMod-Downloader.exe",
                                           "size": exe.stat().st_size, "sha256": pub.sha256_file(exe)})
        self.ingest()
        d = self.site / "downloader" / "3.1.2"
        self.assertEqual((d / "MyMod-Downloader.exe").read_bytes(), exe.read_bytes())
        self.assertEqual((d / "MyMod-Downloader.exe.sha256").read_text(), f"{pub.sha256_file(exe)}  MyMod-Downloader.exe\n")
        self.assertEqual(json.loads((self.site / "releases/1.0.0/release-manifest.json").read_text())["launcher"]["version"], "3.1.2")

    def test_downloader_without_a_version_resource_needs_the_option(self):
        exe = self.make_exe(body=b"MZ plain")
        dist = self.make_dist("1.0.0")
        with self.assertRaises(pub.PublishError) as e:
            pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", downloader=exe, log=QUIET)
        self.assertIn("--downloader-version", str(e.exception))
        pub.publish(pub.DirTarget(self.site), dist, "1.0.0", "test", downloader=exe, downloader_version="0.9.0", log=QUIET)
        self.ingest()
        self.assertTrue((self.site / "downloader/0.9.0/MyMod-Downloader.exe").is_file())

    def test_downloader_version_that_disagrees_with_the_exe_is_refused(self):
        exe = self.make_exe(body=exe_with_version(3, 1, 2))
        with self.assertRaises(pub.PublishError):
            pub.publish(pub.DirTarget(self.site), self.make_dist("1.0.0"), "1.0.0", "test", downloader=exe, downloader_version="3.1.4", log=QUIET)

    # ------------------------------------------------------------------------------------------------ promote, list, gc
    def three_releases(self):
        self.publish("1.0.0")
        self.publish("1.0.1", files=dict(BASE_FILES, **{"global/excel/only101.txt": b"only in 1.0.1\n"}))
        self.publish("1.0.2")

    def test_promote_moves_a_channel_and_asks_for_a_mirror_sync(self):
        self.three_releases()
        target = pub.DirTarget(self.site)
        pub.promote(target, "1.0.0", "stable", log=QUIET)
        ch = self.channel("stable")
        self.assertEqual(ch["version"], "1.0.0")
        self.assertEqual(ch["manifestSha256"], pub.sha256_file(self.site / "releases/1.0.0/release-manifest.json"))
        self.assertIn("promote 1.0.0 stable", (self.site / ".sync" / "wanted").read_text())
        pub.promote(target, "1.0.0", "stable", log=QUIET)  # idempotent
        self.assertEqual(self.channel("stable")["version"], "1.0.0")

    def test_promote_refuses_what_the_host_cannot_serve(self):
        self.publish("1.0.0")
        target = pub.DirTarget(self.site)
        with self.assertRaises(pub.PublishError) as e:
            pub.promote(target, "9.9.9", "stable", log=QUIET)
        self.assertIn("not on the host", str(e.exception))
        next(p for p in (self.site / "objects").rglob("*") if p.is_file()).unlink()
        with self.assertRaises(pub.PublishError) as e:
            pub.promote(target, "1.0.0", "stable", log=QUIET)
        self.assertIn("missing", str(e.exception))
        self.assertFalse((self.site / "channels" / "stable.json").exists())

    def test_list(self):
        self.three_releases()
        pub.promote(pub.DirTarget(self.site), "1.0.0", "stable", log=QUIET)
        (self.incoming / "1.0.3").mkdir()
        (self.site / ".sync" / "status.json").write_text(json.dumps({"result": "ok", "finishedAt": "2026-01-01T00:00:00Z", "uploaded": 4,
                                                                      "deleted": 0, "remote": "mirror:my-mod-files/mymod"}))
        lines = []
        versions, chans = pub.list_cmd(pub.DirTarget(self.site), lines.append)
        self.assertEqual(versions, ["1.0.0", "1.0.1", "1.0.2"])
        self.assertEqual({k: v["version"] for k, v in chans.items()}, {"stable": "1.0.0", "test": "1.0.2"})
        text = "\n".join(lines)
        self.assertRegex(text, r"channel stable\s+1\.0\.0")
        self.assertRegex(text, r"release 1\.0\.0\s.*<- stable")
        self.assertRegex(text, r"release 1\.0\.2\s.*<- test")
        self.assertIn("incoming 1.0.3", text)
        self.assertIn("Mirror sync: ok at 2026-01-01T00:00:00Z (4 uploaded, 0 deleted; mirror:my-mod-files/mymod)", text)

    def test_list_on_an_empty_host(self):
        lines = []
        self.assertEqual(pub.list_cmd(pub.DirTarget(self.site), lines.append), ([], {}))
        self.assertRegex("\n".join(lines), r"channel stable\s+\(none\)")

    def test_gc_keeps_what_a_channel_or_a_newer_release_needs(self):
        self.three_releases()
        target = pub.DirTarget(self.site)
        pub.promote(target, "1.0.0", "stable", log=QUIET)
        only = self.site / pub.object_path(sha(b"only in 1.0.1\n"))
        self.assertTrue(only.is_file())
        doomed, orphans = pub.gc(target, 1, dry_run=True, log=QUIET)
        self.assertEqual(doomed, ["1.0.1"])
        self.assertTrue(only.is_file(), "a dry run deletes nothing")
        self.assertTrue((self.site / "releases/1.0.1").is_dir())
        doomed, orphans = pub.gc(target, 1, log=QUIET)
        self.assertEqual(doomed, ["1.0.1"])
        self.assertFalse((self.site / "releases/1.0.1").exists())
        self.assertFalse(only.exists())
        self.assertEqual(sorted(p.name for p in (self.site / "releases").iterdir()), ["1.0.0", "1.0.2"])
        for v in ("1.0.0", "1.0.2"):  # every object the remaining releases use is still there
            man = json.loads((self.site / f"releases/{v}/release-manifest.json").read_text())
            for f in man["files"]:
                self.assertTrue((self.site / pub.object_path(f["sha256"])).is_file(), f["path"])
        self.assertIn("gc --keep 1", (self.site / ".sync" / "wanted").read_text())

    def test_gc_removes_old_downloaders_and_temp_files(self):
        self.publish("1.0.0", downloader=self.make_exe(body=exe_with_version(3, 1, 2)))
        self.publish("1.0.1", downloader=self.make_exe("b.exe", exe_with_version(3, 1, 4)))
        stale = self.site / "objects" / "ab" / ("c" * 64 + ".part-1234")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"x")
        pub.gc(pub.DirTarget(self.site), 1, log=QUIET)
        self.assertEqual(sorted(p.name for p in (self.site / "downloader").iterdir()), ["3.1.4"])
        self.assertFalse(stale.exists())

    def test_gc_refuses_when_it_cannot_trust_the_reference_list(self):
        self.publish("1.0.0")
        (self.site / "releases/1.0.0/release-manifest.json").unlink()
        with self.assertRaises(pub.PublishError):
            pub.gc(pub.DirTarget(self.site), 1, log=QUIET)
        with self.assertRaises(pub.PublishError):
            pub.gc(pub.DirTarget(self.site), 0, log=QUIET)

    # ------------------------------------------------------------------------------------------------ the command line
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), env_patch(**(env or {})):
            rc = pub.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_cli_publish_promote_list_gc(self):
        dist = self.make_dist("1.0.0")
        t = f"dir:{self.site}"
        rc, out, err = self.run_cli("publish", "--target", t, "--version", "1.0.0", "--dist", str(dist))
        self.assertEqual(rc, 0, err)
        self.assertIn("uploading", out)
        self.ingest()
        rc, out, err = self.run_cli("promote", "1.0.0", "--target", t)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.channel("stable")["version"], "1.0.0")
        rc, out, err = self.run_cli("list", "--target", t)
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"channel stable\s+1\.0\.0")
        rc, out, err = self.run_cli("gc", "--keep", "1", "--dry-run", "--target", t)
        self.assertEqual(rc, 0, err)
        self.assertIn("dry run", out)

    def test_cli_target_from_the_environment_and_errors_on_stderr(self):
        rc, out, err = self.run_cli("list", env={release_config.ENV_TARGET: f"dir:{self.site}"})
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("promote", "1.0.0", env={release_config.ENV_TARGET: f"dir:{self.site}"})
        self.assertEqual(rc, 1)
        self.assertIn("error: release 1.0.0 is not on the host", err)
        rc, out, err = self.run_cli("publish", "--version", "1.0.0", "--dist", str(self.tmp / "x"),
                                    env={release_config.ENV_TARGET: f"dir:{self.site}"})
        self.assertEqual(rc, 1)

    def test_cli_reads_the_config_file(self):
        conf = self.tmp / "release.toml"
        conf.write_text(f'[mod]\nname = "Other"\nfolder = "Other"\nsave_folder = "Other/"\n[site]\ntarget = "dir:{self.site.as_posix()}"\n'
                        'channels = ["stable", "beta"]\npublish_channel = "beta"\nincoming = "../incoming"\n'
                        '[downloader]\nexe_name = "Other-Downloader.exe"\n', encoding="utf-8")
        try:
            rc, out, err = self.run_cli("--config", str(conf), "list")
            self.assertEqual(rc, 0, err)
            self.assertRegex(out, r"channel beta\s+\(none\)")
            self.assertNotIn("channel test", out)
            self.assertEqual(pub.LAUNCHER_NAME, "Other-Downloader.exe")
        finally:
            pub.configure(release_config.defaults())


@needs_7z
class VerifyTests(ReleaseBase):
    """verify_url is what a downloader does, against the files the ingest wrote, served over HTTP with Range."""

    def setUp(self):
        super().setUp()
        self.publish("1.0.0", "test")

    def test_verify_checks_every_object_the_range_and_the_7z(self):
        lines = []
        with serving(self.site) as base:
            n = pub.verify_url(base, "test", log=lines.append)
        self.assertGreaterEqual(n, 4)
        text = "\n".join(lines)
        self.assertIn("channel test: 1.0.0", text)
        self.assertIn("immutable", text)
        self.assertIn("Range requests: ok (206)", text)
        self.assertIn("MyMod-1.0.0.7z: sha256 ok", text)

    def test_sample_checks_fewer_objects_and_only_the_size_of_the_7z(self):
        lines = []
        with serving(self.site) as base:
            n = pub.verify_url(base, "test", sample=2, log=lines.append)
        self.assertEqual(n, 2)
        self.assertIn("size ok (--sample: not hashed)", "\n".join(lines))

    def test_a_damaged_object_is_found(self):
        man = json.loads((self.site / "releases/1.0.0/release-manifest.json").read_text())
        f = max(man["files"], key=lambda x: x["size"])
        p = self.site / pub.object_path(f["sha256"])
        p.write_bytes(b"x" * f["size"])
        with serving(self.site) as base, self.assertRaises(pub.PublishError) as e:
            pub.verify_url(base, "test", log=QUIET)
        self.assertIn("bad content", str(e.exception))

    def test_a_damaged_7z_is_found(self):
        p = self.site / "releases/1.0.0/MyMod-1.0.0.7z"
        data = bytearray(p.read_bytes())
        data[-1] ^= 0xFF
        p.write_bytes(bytes(data))
        with serving(self.site) as base, self.assertRaises(pub.PublishError) as e:
            pub.verify_url(base, "test", log=QUIET)
        self.assertIn("bad content", str(e.exception))

    def test_a_manifest_that_does_not_match_the_channel_is_found(self):
        p = self.site / "releases/1.0.0/release-manifest.json"
        p.write_bytes(p.read_bytes() + b" ")
        with serving(self.site) as base, self.assertRaises(pub.PublishError) as e:
            pub.verify_url(base, "test", log=QUIET)
        self.assertIn("manifest sha256", str(e.exception))

    def test_a_missing_channel_is_an_error(self):
        import urllib.error
        with serving(self.site) as base, self.assertRaises(urllib.error.HTTPError):
            pub.verify_url(base, "stable", log=QUIET)

    def test_expect_version(self):
        with serving(self.site) as base:
            pub.verify_url(base, "test", sample=1, expect_version="1.0.0", log=QUIET)
            with self.assertRaises(pub.PublishError) as e:
                pub.verify_url(base, "test", sample=1, expect_version="1.0.1", log=QUIET)
        self.assertIn("shows 1.0.0, not 1.0.1", str(e.exception))

    def test_wait_for_channel_waits_until_the_channel_shows_the_version(self):
        self.publish("1.0.1", "stable")  # stable shows 1.0.1; test shows 1.0.0
        seen = []
        with serving(self.site) as base:
            def moves(_seconds):
                seen.append(_seconds)
                if len(seen) == 2:
                    pub.promote(pub.DirTarget(self.site), "1.0.1", "test", log=QUIET)
            with mock.patch.object(pub.time, "sleep", side_effect=moves):
                pub.wait_for_channel(base, "test", "1.0.1", wait=60, log=QUIET, interval=1)
        self.assertEqual(len(seen), 2)

    def test_wait_for_channel_gives_up(self):
        with serving(self.site) as base, self.assertRaises(pub.PublishError) as e:
            pub.wait_for_channel(base, "test", "9.9.9", wait=0, log=QUIET)
        self.assertIn("shows 1.0.0, not 9.9.9", str(e.exception))

    def test_cli_verify(self):
        out, err = io.StringIO(), io.StringIO()
        with serving(self.site) as base, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(pub.main(["verify", "--url", base, "--channel", "test", "--sample", "3", "--expect-version", "1.0.0"]), 0)
            self.assertEqual(pub.main(["verify", "--url", base, "--channel", "test", "--expect-version", "2.0.0"]), 1)
        self.assertIn("error: channel test", err.getvalue())


@needs_7z
class PublishSftpTests(ReleaseBase):
    """The sftp backend, with tests/release_fakes/fake_sftp.py standing in for OpenSSH. The remote path /mymod is the site
    inside a chroot whose root is srv/."""

    def setUp(self):
        super().setUp()
        self.log = self.tmp / "sftp.log"
        patch = mock.patch.dict(os.environ, {"FAKE_SFTP_ROOT": str(self.srv), "FAKE_SFTP_LOG": str(self.log)})
        patch.start()
        self.addCleanup(patch.stop)

    def target(self):
        return pub.SftpTarget("modrelease", "example.invalid", 2222, "/mymod", sftp_cmd=FAKE_SFTP)

    def commands(self):
        return self.log.read_text("utf-8").splitlines() if self.log.is_file() else []

    def test_publish_through_sftp_makes_the_same_site(self):
        t = self.target()
        self.publish("1.0.0", "test", target=t)
        self.assertEqual(self.channel("test")["version"], "1.0.0")
        self.assertTrue((self.site / "releases/1.0.0/MyMod-1.0.0.7z").is_file())
        with serving(self.site) as base:
            pub.verify_url(base, "test", sample=3, log=QUIET)
        self.assertEqual([p.name for p in self.incoming.iterdir()], [])
        self.assertEqual(self.commands().count("LOGIN"), t.logins)

    def test_publish_uses_two_logins_and_sends_ready_last(self):
        t = self.target()
        self.publish("1.0.0", "test", target=t, ingest=False)
        self.assertEqual(t.logins, 2, "one to read the host, one to deliver")
        cmds = self.commands()
        puts = [c for c in cmds if c.startswith(("put ", "reput "))]
        self.assertIn("READY", puts[-1])
        renames = [c for c in cmds if c.startswith("rename ")]
        self.assertIn("1.0.0.uploading", renames[-1])
        self.assertTrue(cmds.index(renames[-1]) > cmds.index(puts[-1]))
        self.assertTrue((self.incoming / "1.0.0" / "READY").is_file())

    def test_a_dropped_connection_resumes_the_objects_7z_with_reput(self):
        cut = self.tmp / "cut"
        cut.write_text("1 40")  # the first upload loses its connection after 40 bytes
        with mock.patch.dict(os.environ, {"FAKE_SFTP_CUT": str(cut)}):
            lines = []
            pub.publish(self.target(), self.make_dist("1.0.0"), "1.0.0", "test", log=lines.append)
        self.assertTrue(any("interrupted" in ln for ln in lines), lines)
        cmds = self.commands()
        self.assertTrue(any(c.startswith("reput ") for c in cmds), cmds)
        self.assertEqual(cut.read_text().split()[0], "0")
        self.ingest()  # the resumed bytes are the right bytes: the server accepts them
        self.assertEqual(self.channel("test")["version"], "1.0.0")

    def test_a_failure_that_does_not_stop_is_reported_after_the_retries(self):
        with mock.patch.dict(os.environ, {"FAKE_SFTP_FAIL_PUT": "1"}):
            with self.assertRaises(pub.PublishError) as e:
                pub.publish(self.target(), self.make_dist("1.0.0"), "1.0.0", "test", retries=1, log=QUIET)
        self.assertIn("upload failed 2 times", str(e.exception))
        self.assertIn("Run the same command again", str(e.exception))

    def test_a_server_without_posix_rename_still_gets_the_files(self):
        self.publish("1.0.0", "test", target=self.target())
        with mock.patch.dict(os.environ, {"FAKE_SFTP_LEGACY_RENAME": "1"}):
            pub.promote(self.target(), "1.0.0", "stable", log=QUIET)
            pub.promote(self.target(), "1.0.0", "stable", log=QUIET)  # replaces an existing channel file
        self.assertEqual(self.channel("stable")["version"], "1.0.0")
        leftovers = [p for p in self.site.rglob("*") if ".part-" in p.name]
        self.assertEqual(leftovers, [])

    def test_promote_list_gc_through_sftp(self):
        t = self.target()
        self.publish("1.0.0", target=t)
        self.publish("1.0.1", target=t, files=dict(BASE_FILES, **{"global/excel/only101.txt": b"only in 1.0.1\n"}))
        pub.promote(t, "1.0.0", "stable", log=QUIET)
        lines = []
        versions, chans = pub.list_cmd(t, lines.append)
        self.assertEqual((versions, chans["stable"]["version"], chans["test"]["version"]), (["1.0.0", "1.0.1"], "1.0.0", "1.0.1"))
        pub.promote(t, "1.0.1", "stable", log=QUIET)
        pub.gc(t, 1, log=QUIET)
        self.assertEqual([p.name for p in (self.site / "releases").iterdir()], ["1.0.1"])
        self.assertFalse((self.site / "downloader").exists())

    def test_cli_prints_the_login_count_for_sftp(self):
        out = io.StringIO()
        env = {release_config.ENV_SFTP: " ".join(a.replace("\\", "/") for a in FAKE_SFTP)}  # shlex.split: forward slashes
        with env_patch(**env), contextlib.redirect_stdout(out):
            rc = pub.main(["list", "--target", "sftp://modrelease@example.invalid:2222/mymod"])
        self.assertEqual(rc, 0)
        self.assertRegex(out.getvalue(), r"\(\d+ sftp logins\)")


if __name__ == "__main__":
    unittest.main()
