"""release/github_release.py with a fake HTTP opener (no network, no token on disk) and a syntax check of release/release.sh.
Run: python -m unittest discover tests"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

from test_release_support import RELEASE_DIR, env_patch  # noqa: F401  (also puts release/ on sys.path)

import github_release as gh  # noqa: E402
import release_config  # noqa: E402

CHANGELOG = """# Changelog

## 1.x

### 1.0.1 (2026-02-01)

- Fixed a thing. See [the guide](docs/downloader.md).

### 1.0.0-rc1

- A candidate.

### 1.0.0

- First release.
"""


class FakeForge:
    """Records requests; answers like a release API with one release already there when `existing` is set."""

    def __init__(self, existing=None):
        self.requests, self.release = [], existing

    def __call__(self, req):
        body = req.data.decode("utf-8", "replace") if req.data and req.headers.get("Content-type") == "application/json" else None
        self.requests.append((req.get_method(), req.full_url, body, req.headers))
        url, m = req.full_url, req.get_method()
        if m == "GET" and "/releases/tags/" in url:
            if self.release is None:
                raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b""))
            return 200, json.dumps(self.release).encode()
        if m == "POST" and url.endswith("/releases"):
            self.release = {"id": 7, "html_url": "https://example.invalid/r/7", "assets": [], **json.loads(body)}
            return 201, json.dumps(self.release).encode()
        if m == "PATCH":
            self.release = {**self.release, **json.loads(body)}
            return 200, json.dumps(self.release).encode()
        return 200, b"{}"


class ChangelogTests(unittest.TestCase):
    def test_section_by_version(self):
        self.assertEqual(gh.changelog_section(CHANGELOG, "1.0.0"), "- First release.")
        self.assertIn("Fixed a thing", gh.changelog_section(CHANGELOG, "1.0.1"))
        self.assertEqual(gh.changelog_section(CHANGELOG, "1.0.0-rc1"), "- A candidate.")

    def test_a_missing_section_is_an_error(self):
        with self.assertRaises(SystemExit):
            gh.changelog_section(CHANGELOG, "2.0.0")

    def test_body_has_links_from_the_config(self):
        cfg = release_config.defaults()
        body = gh.release_body(cfg, "1.0.1", "- x", "3.1.2", "https://example.invalid/docs/")
        self.assertIn("https://files.example.com/mymod/downloader/3.1.2/MyMod-Downloader.exe", body)
        self.assertIn("https://files.example.com/mymod/releases/1.0.1/MyMod-1.0.1.7z", body)
        self.assertIn("build 93847", body)
        self.assertIn("](https://example.invalid/docs/downloader.md)", gh.release_body(cfg, "1.0.1", "[g](docs/downloader.md)", "", "https://example.invalid/docs/"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.cfg = release_config.defaults()
        self.cfg["repos"].update(github="owner/repo", forgejo="owner/repo", forgejo_url="https://git.example.invalid")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def run_it(self, forge, **kw):
        lines = []
        url = gh.run(self.cfg, "1.0.1", changelog_text=CHANGELOG, opener=forge, token="SECRET-TOKEN", log=lines.append, **kw)
        return url, lines

    def test_dry_run_sends_nothing_and_needs_no_token(self):
        forge = FakeForge()
        lines = []
        gh.run(self.cfg, "1.0.1", dry_run=True, changelog_text=CHANGELOG, opener=forge, log=lines.append)
        self.assertEqual(forge.requests, [])
        self.assertIn("v1.0.1", lines[0])
        self.assertIn("Fixed a thing", lines[0])

    def test_creates_the_release_on_github(self):
        forge = FakeForge()
        url, lines = self.run_it(forge, service="github", commit="abc123", latest=True)
        self.assertEqual(url, "https://example.invalid/r/7")
        post = next(r for r in forge.requests if r[0] == "POST")
        self.assertEqual(post[1], "https://api.github.com/repos/owner/repo/releases")
        sent = json.loads(post[2])
        self.assertEqual((sent["tag_name"], sent["target_commitish"], sent["name"], sent["make_latest"], sent["prerelease"]),
                         ("v1.0.1", "abc123", "My Mod 1.0.1", "true", False))
        self.assertIn("github.com/owner/repo/blob/main/docs/downloader.md", sent["body"])
        self.assertEqual(post[3]["Authorization"], "Bearer SECRET-TOKEN")

    def test_a_prerelease_version_is_marked(self):
        forge = FakeForge()
        gh.run(self.cfg, "1.0.0-rc1", changelog_text=CHANGELOG, opener=forge, token="t", log=lambda *_: None)
        self.assertTrue(json.loads(next(r for r in forge.requests if r[0] == "POST")[2])["prerelease"])

    def test_running_again_updates_instead_of_failing(self):
        forge = FakeForge(existing={"id": 7, "html_url": "https://example.invalid/r/7", "assets": []})
        self.run_it(forge, service="forgejo", commit="abc")
        methods = [r[0] for r in forge.requests]
        self.assertIn("PATCH", methods)
        self.assertNotIn("POST", methods)
        self.assertTrue(forge.requests[0][1].startswith("https://git.example.invalid/api/v1/repos/owner/repo/"))
        self.assertEqual(forge.requests[0][3]["Authorization"], "token SECRET-TOKEN")

    def test_the_downloader_asset_replaces_an_old_one_and_the_token_is_never_logged(self):
        exe = self.tmp / "MyMod-Downloader.exe"
        exe.write_bytes(b"MZ")
        forge = FakeForge(existing={"id": 7, "html_url": "u", "assets": [{"id": 3, "name": exe.name}]})
        url, lines = self.run_it(forge, service="github", commit="abc", downloader=exe, downloader_version="3.1.2")
        methods = [(r[0], r[1].split("/")[-1].split("?")[0]) for r in forge.requests]
        self.assertIn(("DELETE", "3"), methods)
        self.assertTrue(any(r[0] == "POST" and "uploads.github.com" in r[1] and "name=MyMod-Downloader.exe" in r[1] for r in forge.requests))
        self.assertNotIn("SECRET-TOKEN", "\n".join(lines) + url)

    def test_token_from_the_environment_then_the_file(self):
        self.cfg["repos"]["github_token_env"] = "MODKIT_TEST_TOKEN"
        self.cfg["repos"]["github_token_file"] = str(self.tmp / "tok")
        with env_patch(MODKIT_TEST_TOKEN=""):
            with self.assertRaises(SystemExit) as e:
                gh.read_token(self.cfg, "github")
            self.assertIn("no github token", str(e.exception))
            (self.tmp / "tok").write_text("from-file\n")
            self.assertEqual(gh.read_token(self.cfg, "github"), "from-file")
        with env_patch(MODKIT_TEST_TOKEN="from-env"):
            self.assertEqual(gh.read_token(self.cfg, "github"), "from-env")

    def test_http_errors_say_what_failed_without_the_token(self):
        def refuse(req):
            raise urllib.error.HTTPError(req.full_url, 403, "no", {}, io.BytesIO(b"forbidden"))
        with self.assertRaises(SystemExit) as e:
            gh.run(self.cfg, "1.0.1", changelog_text=CHANGELOG, opener=refuse, token="SECRET-TOKEN", log=lambda *_: None)
        self.assertIn("HTTP 403", str(e.exception))
        self.assertNotIn("SECRET-TOKEN", str(e.exception))

    def test_cli_dry_run(self):
        cl = self.tmp / "CHANGELOG.md"
        cl.write_text(CHANGELOG, encoding="utf-8")
        conf = self.tmp / "release.toml"
        conf.write_text(f'[repos]\nchangelog = "{cl.as_posix()}"\ngithub = "owner/repo"\n', encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = gh.main(["--config", str(conf), "1.0.0", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("First release.", out.getvalue())


class ReleaseShellTests(unittest.TestCase):
    def test_release_sh_is_valid_bash_with_unix_line_endings(self):
        sh = RELEASE_DIR / "release.sh"
        self.assertNotIn(b"\r", sh.read_bytes())
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash on this machine")
        r = subprocess.run([bash, "-n", sh.as_posix()], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_release_sh_prints_usage_and_refuses_a_bad_version(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash on this machine")
        sh = (RELEASE_DIR / "release.sh").as_posix()
        r = subprocess.run([bash, sh, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("release.sh <version> --mod-dir", r.stdout)
        r = subprocess.run([bash, sh, "latest", "--mod-dir", "."], capture_output=True, text=True, cwd=tempfile.gettempdir())
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
