"""release/package_release.py and release/release_config.py on a small fake mod folder; no game files, no plugin build.
Run: python -m unittest discover tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_release_support import RELEASE_DIR, SEVEN, BASE_FILES, make_mod, needs_7z  # also puts release/ on sys.path

import package_release as pr  # noqa: E402
import release_config  # noqa: E402

SCRIPT = str(RELEASE_DIR / "package_release.py")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cfg = release_config.defaults()
        self.dll = self.tmp / "mymod-core.dll"
        self.dll.write_bytes(b"MZ..." + b"1.2.3" + b"\0...")

    def mod(self, files=None):
        return make_mod(self.tmp / "src", "MyMod", files or {"global/excel/armor.txt": b"armor\n"}, "1.2.3")

    def fake_7z(self, name="MyMod-1.2.3.7z"):
        out = self.tmp / name
        out.write_bytes(b"7z fake")
        return out

    def run_main(self, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, text=True, cwd=self.tmp)

    # ------------------------------------------------------------------------------------------------ manifest
    def test_manifest_lists_every_file(self):
        mod = pr.stage(self.mod(), self.tmp / "stage" / "MyMod", self.dll)
        files = pr.file_list(mod)
        m = pr.manifest(self.cfg, "1.2.3", files, self.fake_7z(), True, pr.empty_dirs(mod), "1.2.3")
        self.assertEqual(m["schemaVersion"], 2)
        self.assertNotIn("zip", m)
        self.assertEqual((m["name"], m["modFolder"], m["saveFolder"]), ("My Mod", "MyMod", "MyMod/"))
        self.assertEqual(m["d2r"], {"build": "93847", "version": "3.3.0"})
        self.assertEqual(m["d2rloader"]["version"], "1.3.1-beta")
        self.assertEqual(len(m["d2rloader"]["sha256"]), 64)
        self.assertEqual(m["launch"], {"default_mod": "MyMod", "launch_arguments": ""})
        self.assertEqual(m["pluginVersion"], "1.2.3")
        paths = [f["path"] for f in files]
        self.assertEqual(paths, sorted(paths))
        self.assertIn("MyMod.mpq/data/global/excel/armor.txt", paths)
        self.assertIn("d2rloader/plugins/mymod-core.dll", paths)
        armor = next(f for f in files if f["path"].endswith("armor.txt"))
        self.assertEqual((armor["size"], len(armor["sha256"])), (len(b"armor\n"), 64))
        self.assertEqual((m["fileCount"], m["totalSize"]), (len(files), sum(f["size"] for f in files)))
        self.assertEqual(m["emptyDirs"], ["d2rloader/patches"])
        self.assertEqual(m["preserve"], ["d2rloader/config/d2rloader.toml"])
        self.assertTrue(set(m["preserve"]) <= set(paths))
        self.assertTrue(m["complete"])
        self.assertNotIn("launcherMin", m)

    def test_manifest_optional_fields_and_archive_entry(self):
        self.cfg["game"]["image_timestamp"] = "0x1234abcd"
        self.cfg["downloader"]["min_version"] = "3.0.0"
        seven = self.fake_7z()
        m = pr.manifest(self.cfg, "1.2.3", [], seven, False)
        self.assertEqual(m["d2r"]["imageTimestamp"], "0x1234abcd")
        self.assertEqual(m["launcherMin"], "3.0.0")
        self.assertNotIn("pluginVersion", m)
        self.assertFalse(m["complete"])
        self.assertEqual(m["archive"], {"format": "7z", "name": "MyMod-1.2.3.7z", "size": 7, "sha256": pr.sha256(seven)})

    def test_manifest_date_follows_source_date_epoch(self):
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1767225600"}):  # 2026-01-01
            m = pr.manifest(self.cfg, "1.2.3", [], self.fake_7z(), True)
        self.assertEqual(m["released"], "2026-01-01")

    def test_archive_name(self):
        self.assertEqual(pr.archive_name(self.cfg, "1.0.0"), "MyMod-1.0.0.7z")
        self.assertEqual(pr.archive_name(self.cfg, "1.0.0", complete=False), "MyMod-1.0.0-incomplete.7z")

    def test_release_notes_skeleton_uses_the_config(self):
        text = pr.release_notes(self.cfg, "1.2.3", "MyMod-1.2.3.7z")
        for want in ("# My Mod 1.2.3", "build 93847", "D2RLoader **1.3.1-beta**", "MyMod-Downloader.exe",
                     "https://files.example.com/mymod/", "`MyMod-1.2.3.7z`", "Windows 11 opens .7z files", "mods\\MyMod"):
            self.assertIn(want, text)

    # ------------------------------------------------------------------------------------------------ staging
    def test_stage_copies_and_does_not_touch_the_source(self):
        src = self.mod()
        before = {p.relative_to(src).as_posix() for p in src.rglob("*")}
        mod = pr.stage(src, self.tmp / "stage" / "MyMod", self.dll)
        self.assertEqual({p.relative_to(src).as_posix() for p in src.rglob("*")}, before)
        self.assertTrue((mod / "d2rloader" / "plugins" / "mymod-core.dll").is_file())
        self.assertTrue((mod / "d2rloader" / "patches").is_dir())
        self.assertTrue((mod / "MyMod.mpq" / "modinfo.json").is_file())

    def test_plugin_checks(self):
        self.assertEqual(pr.check_release_plugin(self.dll, "1.2.3", ["testharness"]), [])
        dev = self.tmp / "dev.dll"
        dev.write_bytes(self.dll.read_bytes() + b"mymod-testharness.txt\0")
        self.assertEqual(len(pr.check_release_plugin(dev, "1.2.3", ["testharness"])), 1)
        self.assertEqual(len(pr.check_release_plugin(self.dll, "9.9.9", ["testharness"])), 1)
        self.assertEqual(pr.check_release_plugin(self.dll), [])

    def test_test_and_player_files_are_refused(self):
        mod = pr.stage(self.mod({"global/excel/armor.txt": b"x", "global/x.txt": b"y"}), self.tmp / "stage" / "MyMod")
        self.assertEqual(pr.forbidden_files(mod), [])
        bad = ["d2rloader/plugins/mymod-testharness.txt", "Hero.d2s", "Hero.d2i", "d2rloader/logs/core.log",
               "d2rloader/config/input-bindings.toml", "Hero.map", "Hero.d2rl", "Hero.ctl", "imgui.ini", "crash.dmp"]
        for rel in bad:
            (mod / rel).parent.mkdir(parents=True, exist_ok=True)
            (mod / rel).write_text("x")
        self.assertEqual(sorted(pr.forbidden_files(mod)), sorted(bad))
        self.assertEqual(pr.forbidden_files(mod, [r"\.dmp$"]), ["crash.dmp"], "the config's patterns replace the defaults")

    def test_config_patterns_default_to_the_builtin_ones(self):
        self.assertEqual(self.cfg["package"]["forbidden"], release_config.DEFAULT_FORBIDDEN)

    def test_byte_copies_of_game_files_are_left_out(self):
        mod = pr.stage(self.mod({"global/excel/armor.txt": b"mod armor\n", "global/excel/sounds.txt": b"vanilla\n",
                                 "global/dataversionbuild.txt": b"93847"}), self.tmp / "stage" / "MyMod")
        extract = self.tmp / "extract"
        for rel, body in {"global/excel/sounds.txt": b"vanilla\n", "global/excel/armor.txt": b"vanilla armor\n",
                          "global/dataversionbuild.txt": b"93847"}.items():
            (extract / "data" / rel).parent.mkdir(parents=True, exist_ok=True)
            (extract / "data" / rel).write_bytes(body)
        n = pr.drop_game_copies(mod, extract, "MyMod.mpq/data", ["global/dataversionbuild.txt"])
        self.assertEqual(n, 1)
        data = mod / "MyMod.mpq" / "data"
        self.assertFalse((data / "global/excel/sounds.txt").exists())
        self.assertTrue((data / "global/excel/armor.txt").exists(), "different bytes: ours")
        self.assertTrue((data / "global/dataversionbuild.txt").exists(), "kept on request")
        self.assertEqual(pr.drop_game_copies(mod, self.tmp / "nowhere", "MyMod.mpq/data"), 0)

    # ------------------------------------------------------------------------------------------------ 7-Zip
    def test_no_7zip_is_an_error(self):
        mod = pr.stage(self.mod(), self.tmp / "stage" / "MyMod")
        out = self.tmp / "r.7z"
        with mock.patch.object(pr, "find_7z", return_value=None):
            with self.assertRaises(RuntimeError) as e:
                pr.write_7z(mod, out)
        self.assertIn("no 7-Zip found", str(e.exception))
        self.assertFalse(out.exists())

    def test_main_stops_early_without_7zip(self):
        out = self.tmp / "dist"
        argv = ["package_release.py", "--mod-dir", str(self.mod()), "--version", "1.2.3", "--out", str(out)]
        code = (f"import sys; sys.path.insert(0, {str(RELEASE_DIR)!r}); import package_release as pr; pr.find_7z = lambda: None; "
                f"sys.exit(pr.main({argv[1:]!r}))")
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no 7-Zip found", r.stdout)
        self.assertFalse(out.exists(), "nothing staged")

    def test_find_7z_looks_in_program_files_then_path(self):
        pf = self.tmp / "pf"
        (pf / "7-Zip").mkdir(parents=True)
        (pf / "7-Zip" / "7z.exe").write_bytes(b"")
        none = {"ProgramFiles": str(self.tmp / "none"), "ProgramFiles(x86)": ""}
        with mock.patch.dict("os.environ", {"ProgramFiles": str(pf)}), mock.patch.object(pr.shutil, "which", return_value="x"):
            self.assertEqual(pr.find_7z(), str(pf / "7-Zip" / "7z.exe"))
        with mock.patch.dict("os.environ", none), \
                mock.patch.object(pr.shutil, "which", side_effect=lambda n: "/bin/7zz" if n == "7zz" else None):
            self.assertEqual(pr.find_7z(), "/bin/7zz")
        with mock.patch.dict("os.environ", none), mock.patch.object(pr.shutil, "which", return_value=None):
            self.assertIsNone(pr.find_7z())

    def test_failing_7zip_raises_and_leaves_nothing(self):
        mod = pr.stage(self.mod(), self.tmp / "stage" / "MyMod")
        out = self.tmp / "r.7z"
        fake = self.tmp / "fail.py"
        fake.write_text("import sys; print('boom'); sys.exit(2)")
        if sys.platform == "win32":
            script = self.tmp / "fail.cmd"
            script.write_text(f'@"{sys.executable}" "{fake}"\r\n')
        else:
            script = self.tmp / "fail.sh"
            script.write_text(f'#!/bin/sh\n"{sys.executable}" "{fake}"\n')
            script.chmod(0o755)
        with self.assertRaises(RuntimeError):
            pr.write_7z(mod, out, str(script))
        self.assertFalse(out.exists())

    @needs_7z
    def test_real_7z_holds_the_mod_folder_and_is_reproducible(self):
        mod = pr.stage(self.mod({"global/excel/armor.txt": b"armor\n" * 5000, "global/ui/a.json": b"{}"}),
                       self.tmp / "stage" / "MyMod")
        a, b = self.tmp / "a.7z", self.tmp / "b.7z"
        pr.write_7z(mod, a)
        for p in mod.rglob("*"):  # other mtimes: write_7z sets them again, the bytes stay
            os.utime(p, (1.5e9, 1.5e9))
        pr.write_7z(mod, b)
        self.assertEqual(pr.sha256(a), pr.sha256(b))
        listing = subprocess.run([SEVEN, "l", "-slt", str(a)], capture_output=True, text=True).stdout
        self.assertIn("Method = LZMA2", listing)
        self.assertIn("Solid = +", listing)
        paths = {ln[7:].replace("\\", "/") for ln in listing.splitlines() if ln.startswith("Path = ")} - {a.as_posix()}
        self.assertTrue(all(n == "MyMod" or n.startswith("MyMod/") for n in paths), paths)
        out = self.tmp / "unpacked"
        subprocess.run([SEVEN, "x", "-y", f"-o{out}", str(a)], check=True, capture_output=True)
        self.assertEqual(pr.file_list(out / "MyMod"), pr.file_list(mod))
        self.assertTrue((out / "MyMod" / "d2rloader" / "patches").is_dir())

    @needs_7z
    def test_write_7z_names_the_top_folder_after_the_folder_given(self):
        """downloader/tests/data/make_sample_7z.py calls write_7z(mod_dir, out_path) on a folder of its own."""
        sample = make_mod(self.tmp / "other", "SampleMod", BASE_FILES)
        out = self.tmp / "s.7z"
        pr.write_7z(sample, out)
        listing = subprocess.run([SEVEN, "l", "-slt", str(out)], capture_output=True, text=True).stdout
        self.assertIn("Path = SampleMod", listing)

    @needs_7z
    def test_main_writes_the_release_files(self):
        out = self.tmp / "dist"
        out.mkdir()
        (out / "MyMod-1.2.2.7z").write_bytes(b"an earlier release")
        r = self.run_main("--mod-dir", str(self.mod({"global/excel/armor.txt": b"armor\n" * 1000})), "--version", "1.2.3",
                          "--plugin", str(self.dll), "--plugin-version", "1.2.3", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        seven = out / "MyMod-1.2.3.7z"
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["MyMod-1.2.3.7z", "RELEASE_NOTES-1.2.3.md", "SHA256SUMS", "release-manifest.json"])
        man = json.loads((out / "release-manifest.json").read_text())
        self.assertEqual(man["schemaVersion"], 2)
        self.assertEqual(man["pluginVersion"], "1.2.3")
        self.assertEqual(man["archive"], {"format": "7z", "name": seven.name, "size": seven.stat().st_size, "sha256": pr.sha256(seven)})
        self.assertIn("d2rloader/plugins/mymod-core.dll", [f["path"] for f in man["files"]])
        sums = (out / "SHA256SUMS").read_text().splitlines()
        self.assertIn(f"{pr.sha256(seven)}  {seven.name}", sums)
        self.assertEqual(len(sums), 3)
        self.assertFalse((out / "stage").exists())
        self.assertIn("as 7z", r.stdout)
        self.assertNotIn(b"\r", (out / "release-manifest.json").read_bytes(), "LF line endings")

    @needs_7z
    def test_main_refuses_bad_input_before_writing(self):
        mod = self.mod()
        out = self.tmp / "dist"
        r = self.run_main("--mod-dir", str(mod), "--version", "latest", "--out", str(out))
        self.assertEqual((r.returncode, out.exists()), (1, False), r.stdout)
        dev = self.tmp / "dev.dll"
        dev.write_bytes(b"MZ testharness 1.2.3\0")
        r = self.run_main("--mod-dir", str(mod), "--version", "1.2.3", "--plugin", str(dev), "--out", str(out))
        self.assertEqual((r.returncode, out.exists()), (1, False), r.stdout)
        self.assertIn("testharness", r.stdout)
        (mod / "Hero.d2s").write_bytes(b"save")
        r = self.run_main("--mod-dir", str(mod), "--version", "1.2.3", "--out", str(out))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("refusing to package test or player files: Hero.d2s", r.stdout)
        self.assertEqual(list(out.glob("*.7z")), [])
        r = self.run_main("--mod-dir", str(self.tmp / "nothing"), "--version", "1.2.3", "--out", str(out))
        self.assertEqual(r.returncode, 1)

    @needs_7z
    def test_incomplete_package_is_marked(self):
        out = self.tmp / "dist"
        r = self.run_main("--mod-dir", str(self.mod()), "--version", "1.2.3-rc1", "--incomplete", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([p.name for p in out.glob("*.7z")], ["MyMod-1.2.3-rc1-incomplete.7z"])
        man = json.loads((out / "release-manifest.json").read_text())
        self.assertFalse(man["complete"])
        self.assertEqual(man["archive"]["name"], "MyMod-1.2.3-rc1-incomplete.7z")

    @needs_7z
    def test_main_reads_the_config_file(self):
        conf = self.tmp / "release.toml"
        conf.write_text('[mod]\nname = "Other Mod"\nfolder = "OtherMod"\nsave_folder = "OtherMod/"\n'
                        '[package]\nforbidden = ["\\\\.secret$"]\n', encoding="utf-8")
        mod = make_mod(self.tmp / "src2", "whatever", {"a.txt": b"a"})
        (mod / "x.secret").write_text("s")
        out = self.tmp / "dist"
        r = self.run_main("--config", str(conf), "--mod-dir", str(mod), "--version", "2.0.0", "--out", str(out))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("x.secret", r.stdout)
        (mod / "x.secret").unlink()
        r = self.run_main("--config", str(conf), "--mod-dir", str(mod), "--version", "2.0.0", "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        man = json.loads((out / "release-manifest.json").read_text())
        self.assertEqual((man["name"], man["modFolder"], man["archive"]["name"]), ("Other Mod", "OtherMod", "OtherMod-2.0.0.7z"))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_the_example_config_loads_and_is_all_placeholders(self):
        cfg = release_config.load(RELEASE_DIR / "release.example.toml")
        self.assertEqual(cfg["mod"]["folder"], "MyMod")
        self.assertEqual(cfg["site"]["url"], "https://files.example.com/mymod/")
        self.assertEqual(cfg["site"]["channels"], ["stable", "test"])
        self.assertEqual(cfg["game"]["build"], "93847")
        self.assertEqual(cfg["package"]["forbidden"], release_config.DEFAULT_FORBIDDEN, "the file and the built-in list agree")
        self.assertEqual(cfg["package"]["preserve"], ["d2rloader/config/d2rloader.toml"])
        self.assertTrue(release_config.placeholders(cfg))

    def test_example_has_every_default_key(self):
        """A key added to DEFAULTS must be documented in the example file."""
        import tomllib
        data = tomllib.loads((RELEASE_DIR / "release.example.toml").read_text("utf-8"))

        def keys(d, prefix=""):
            for k, v in d.items():
                if isinstance(v, dict):
                    yield from keys(v, f"{prefix}{k}.")
                else:
                    yield f"{prefix}{k}"
        self.assertEqual(set(keys(release_config.DEFAULTS)), set(keys(data)))

    def test_defaults_when_no_file_and_errors_for_a_named_missing_file(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            self.assertEqual(release_config.load()["mod"]["folder"], "MyMod")
            with self.assertRaises(release_config.ConfigError):
                release_config.load(required=True)
        finally:
            os.chdir(cwd)
        with self.assertRaises(release_config.ConfigError):
            release_config.load(self.tmp / "nope.toml")

    def test_file_overrides_defaults_section_by_section(self):
        p = self.tmp / "c.toml"
        p.write_text('[mod]\nfolder = "Abc"\n[site]\nchannels = ["stable", "test", "beta"]\n', encoding="utf-8")
        cfg = release_config.load(p)
        self.assertEqual((cfg["mod"]["folder"], cfg["mod"]["name"]), ("Abc", "My Mod"))
        self.assertEqual(cfg["site"]["channels"], ["stable", "test", "beta"])

    def test_bad_values_are_refused(self):
        for text in ('[mod]\nfolder = "a/b"\n', '[downloader]\nexe_name = "x.bat"\n', '[site]\nchannels = ["Stable"]\n',
                     '[site]\npublish_channel = "nope"\n', '[site]\nincoming = "/abs"\n', '[package]\nforbidden = ["("]\n', "not toml ["):
            with self.subTest(text=text):
                p = self.tmp / "bad.toml"
                p.write_text(text, encoding="utf-8")
                with self.assertRaises(release_config.ConfigError):
                    release_config.load(p)

    def test_get_command(self):
        r = subprocess.run([sys.executable, "-I", str(RELEASE_DIR / "release_config.py"), "--config",
                            str(RELEASE_DIR / "release.example.toml"), "get", "site.channels"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["stable", "test"]))
        r = subprocess.run([sys.executable, "-I", str(RELEASE_DIR / "release_config.py"), "get", "no.such.key"],
                           capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
