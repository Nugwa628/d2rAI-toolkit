"""Tests for tools/check_public.py and tools/install_hooks.py (run: python -m unittest discover tests)."""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_public as cp  # noqa: E402
import install_hooks as ih  # noqa: E402

TERM = "zorblax"          # a made-up banned term; real lists live outside the repo
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def chunk(ctype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))


def make_png(*extra: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return cp.PNG_MAGIC + chunk(b"IHDR", ihdr) + b"".join(extra) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def scanner(terms=(TERM,), allow=None) -> cp.Scanner:
    return cp.Scanner(list(terms), allow or cp.Allow())


def kinds(sc: cp.Scanner) -> list[str]:
    return [h.kind for h in sc.hits]


def run_main(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cp.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                          env=dict(os.environ, **GIT_ENV))


class TermFileTests(unittest.TestCase):
    def test_load_skips_comments_and_blanks_and_lowercases(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.txt"
            p.write_text("# comment\n\nFooBar\n  baz  \n", encoding="utf-8")
            self.assertEqual(cp.load_terms(p), ["foobar", "baz"])

    def test_missing_file_is_none(self):
        self.assertIsNone(cp.load_terms(Path("/definitely/not/here.txt")))

    def test_env_and_cli_override(self):
        old = os.environ.get(cp.ENV_TERMS)
        try:
            os.environ[cp.ENV_TERMS] = "/tmp/from-env.txt"
            self.assertEqual(cp.terms_path(None), Path("/tmp/from-env.txt"))
            self.assertEqual(cp.terms_path("/tmp/cli.txt"), Path("/tmp/cli.txt"))
            del os.environ[cp.ENV_TERMS]
            self.assertEqual(cp.terms_path(None), cp.DEFAULT_TERMS)
        finally:
            if old is not None:
                os.environ[cp.ENV_TERMS] = old


class GenericCheckTests(unittest.TestCase):
    def hits(self, text, binary=False):
        return [k for k, _, _ in cp.generic_line_hits(text, binary)]

    def test_email(self):
        self.assertEqual(self.hits("mail someone@gmail.com now"), ["email"])
        self.assertEqual(self.hits("12345+Nugwa@users.noreply.github.com"), [])
        self.assertEqual(self.hits("you@example.com and a@b.example.org"), [])
        self.assertEqual(self.hits("icon@2x.png"), [])

    def test_email_is_masked(self):
        _, detail, _ = cp.generic_line_hits("secretname@gmail.com")[0]
        self.assertNotIn("secretname", detail)

    def test_ipv4(self):
        self.assertEqual(self.hits("host 10.1.2.3 up"), ["ip-address"])
        self.assertEqual(self.hits("203.0.113.9 127.0.0.1 0.0.0.0"), [])
        self.assertEqual(self.hits("version 1.2.3 and 1.2.3.4.5"), [])
        self.assertEqual(self.hits("addr 10.1.2.3", binary=True), [])

    def test_windows_user_path(self):
        self.assertEqual(self.hits("C:\\Users\\jdoe\\Desktop\\x"), ["user-path"])
        self.assertEqual(self.hits("c:/users/jdoe/x"), ["user-path"])
        self.assertEqual(self.hits("C:\\Users\\<you>\\x and C:\\Users\\Public\\y and %USERPROFILE%"), [])
        self.assertEqual(self.hits("C:\\Users\\%USERNAME%\\z"), [])

    def test_home_path(self):
        self.assertEqual(self.hits("/home/jdoe/.config"), ["user-path"])
        self.assertEqual(self.hits("/home/user/.config"), [])
        self.assertEqual(self.hits("see ~/.d2r-modkit/x and ./home/jdoe"), [])

    def test_secrets(self):
        self.assertEqual(self.hits("-----BEGIN " + "RSA PRIVATE KEY-----"), ["secret"])
        self.assertEqual(self.hits("tok = " + "ghp_" + "a" * 36), ["secret"])
        self.assertEqual(self.hits("AKIA" + "ABCDEFGHIJKLMNOP"), ["secret"])
        self.assertEqual(self.hits('api_key = "abcdefghijkl1234"'), ["secret"])
        self.assertEqual(self.hits("token = " + "0123456789abcdef" * 2), ["secret"])
        self.assertEqual(self.hits("token = get_token()"), [])

    def test_secret_is_truncated(self):
        _, detail, _ = cp.generic_line_hits("ghp_" + "S" * 36)[0]
        self.assertNotIn("S" * 10, detail)


class ScannerTests(unittest.TestCase):
    def test_term_hit_has_line_and_no_term_text(self):
        sc = scanner()
        sc.scan_text("a.md", f"fine\nthis mentions {TERM.upper()} here\n")
        self.assertEqual([(h.line, h.kind) for h in sc.hits], [("2", "private-term")])
        self.assertNotIn(TERM, str(sc.hits[0]).lower())
        self.assertIn("#1", str(sc.hits[0]))

    def test_file_name_hit(self):
        sc = scanner()
        sc.scan_name(f"docs/{TERM}-notes.md")
        self.assertEqual(kinds(sc), ["private-term"])
        self.assertNotIn(TERM, str(sc.hits[0]))
        self.assertIn("docs/<#1>-notes.md", str(sc.hits[0]))

    def test_content_hit_in_a_file_named_after_a_term_does_not_leak(self):
        sc = scanner()
        sc.scan_text(f"{TERM}.md", TERM + "\n")
        self.assertNotIn(TERM, str(sc.hits[0]))

    def test_binary_utf8_and_utf16(self):
        for blob in (b"\x00\x01" + TERM.encode() + b"\x00", b"\x00\x01" + TERM.encode("utf-16-le") + b"\x00"):
            sc = scanner()
            sc.scan_bytes("x.bin", blob)
            self.assertEqual(kinds(sc), ["private-term"], blob)
            self.assertIn("0x", sc.hits[0].line)

    def test_binary_generic_strings(self):
        sc = scanner(())
        sc.scan_bytes("x.exe", b"\x00\x00C:\\Users\\jdoe\\proj\\x.pdb\x00\x00")
        self.assertEqual(kinds(sc), ["user-path"])

    def test_utf16_text_file(self):
        sc = scanner()
        sc.scan_bytes("t.txt", ("hello " + TERM + "\n").encode("utf-16"))
        self.assertEqual(kinds(sc), ["private-term"])

    def test_clean_text(self):
        sc = scanner()
        sc.scan_bytes("ok.md", b"A Whirlwind clone for Andariel.\n")
        self.assertEqual(sc.hits, [])

    def test_zip_members_are_scanned(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("inner.txt", f"hello {TERM}\n")
            zf.writestr(f"{TERM}.txt", "x")
        sc = scanner()
        sc.scan_bytes("a.zip", buf.getvalue())
        inner = [h for h in sc.hits if "a.zip!" in h.where]
        self.assertEqual(len(inner), 2)                      # member content and member name
        self.assertNotIn(TERM, " ".join(str(h) for h in sc.hits))


class PngTests(unittest.TestCase):
    def test_clean_png(self):
        sc = scanner()
        sc.scan_bytes("a.png", make_png())
        self.assertEqual(sc.hits, [])

    def test_text_chunk_flagged_and_scanned(self):
        sc = scanner()
        sc.scan_bytes("a.png", make_png(chunk(b"tEXt", b"Comment\x00made by " + TERM.encode())))
        self.assertEqual(sorted(set(kinds(sc))), ["png-metadata", "private-term"])

    def test_compressed_chunks_are_decoded(self):
        z = chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(TERM.encode()))
        i = chunk(b"iTXt", b"Comment\x00\x01\x00\x00\x00" + zlib.compress(TERM.encode()))
        for c in (z, i):
            sc = scanner()
            sc.scan_bytes("a.png", make_png(c))
            self.assertIn("private-term", kinds(sc), c[:8])

    def test_exif_flagged(self):
        sc = scanner(())
        sc.scan_bytes("a.png", make_png(chunk(b"eXIf", b"MM\x00*")))
        self.assertEqual(kinds(sc), ["png-metadata"])

    def test_strip_png(self):
        dirty = make_png(chunk(b"tEXt", b"Software\x00tool"), chunk(b"eXIf", b"MM\x00*"))
        clean = cp.strip_png(dirty)
        self.assertEqual(clean, make_png())
        self.assertEqual(list(cp.png_text_chunks(clean)), [])
        self.assertEqual(cp.strip_png(b"not a png"), b"not a png")


class AllowTests(unittest.TestCase):
    def make(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.allow"
            p.write_text(text, encoding="utf-8")
            return cp.Allow.load(p)

    def test_path_glob_and_regex(self):
        a = self.make("# c\ndocs/*.md\nsrc/x.py :: 10\\.0\\.0\\.5\n")
        self.assertTrue(a.allows("docs/a.md", "", ""))
        self.assertTrue(a.allows("src/x.py", "10.0.0.5", "ip 10.0.0.5"))
        self.assertFalse(a.allows("src/x.py", "10.0.0.6", "ip 10.0.0.6"))
        self.assertFalse(a.allows("src/y.py", "10.0.0.5", ""))

    def test_allow_never_hides_private_terms(self):
        sc = scanner(allow=self.make("*\n"))
        sc.scan_text("a.md", f"{TERM} and someone@gmail.com\n")
        self.assertEqual(kinds(sc), ["private-term"])

    def test_missing_allow_file(self):
        self.assertEqual(cp.Allow.load(Path("/nope")).rules, [])
        self.assertEqual(cp.Allow.load(None).rules, [])


class RepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "tester@users.noreply.github.com")
        git(self.repo, "config", "core.autocrlf", "false")
        self.terms = Path(self._tmp.name) / "terms.txt"
        self.terms.write_text(f"# banned\n{TERM}\n", encoding="utf-8")

    def commit(self, msg="init", files=None):
        for name, content in (files or {}).items():
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else content.encode())
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)

    def scan(self, *extra):
        return run_main("--root", str(self.repo), "--terms", str(self.terms), *extra)

    def test_clean_repo(self):
        self.commit(files={"a.md": "hello\n"})
        rc, out, _ = self.scan()
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)

    def test_hit_exit_code_and_masking(self):
        self.commit(files={"docs/a.md": f"line1\nabout {TERM}\n", "b.png": make_png(chunk(b"tEXt", b"k\x00v"))})
        rc, out, err = self.scan()
        self.assertEqual(rc, 1)
        self.assertIn("docs/a.md:2: [private-term]", out)
        self.assertIn("b.png:tEXt: [png-metadata]", out)
        self.assertNotIn(TERM, out + err)

    def test_commit_message_scanned(self):
        self.commit(msg=f"fix {TERM} thing", files={"a.md": "ok\n"})
        rc, out, _ = self.scan()
        self.assertEqual(rc, 1)
        self.assertIn("message.1", out)
        self.assertNotIn(TERM, out)
        rc, _, _ = self.scan("--no-history")
        self.assertEqual(rc, 0)

    def test_commit_author_email(self):
        self.commit(files={"a.md": "ok\n"})
        git(self.repo, "config", "user.email", "real.person@gmail.com")
        self.commit(msg="second", files={"b.md": "x\n"})
        rc, out, _ = self.scan()
        self.assertEqual(rc, 1)
        self.assertIn(":author: [email]", out)
        self.assertNotIn("real.person", out)

    def test_missing_terms_file_runs_generic_checks(self):
        self.commit(files={"a.md": "mail me at someone@gmail.com\n"})
        rc, out, err = run_main("--root", str(self.repo), "--terms", str(self.terms) + ".missing")
        self.assertEqual(rc, 1)
        self.assertIn("a.md:1: [email]", out)
        self.assertIn("generic checks only", err)

    def test_missing_terms_file_clean_is_ok(self):
        self.commit(files={"a.md": "fine\n"})
        rc, out, err = run_main("--root", str(self.repo), "--terms", str(self.terms) + ".missing")
        self.assertEqual(rc, 0)
        self.assertIn("generic checks only", err)

    def test_require_terms(self):
        self.commit(files={"a.md": "fine\n"})
        rc, _, err = run_main("--root", str(self.repo), "--terms", str(self.terms) + ".missing", "--require-terms")
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)

    def test_allow_file(self):
        self.commit(files={"a.md": "server at 10.9.8.7\n", "check_public.allow": "a.md :: 10\\.9\\.8\\.7\n"})
        rc, out, _ = self.scan()
        self.assertEqual(rc, 0, out)

    def test_staged_mode_reads_the_index(self):
        self.commit(files={"a.md": "ok\n"})
        (self.repo / "a.md").write_text(f"{TERM}\n")
        git(self.repo, "add", "a.md")
        (self.repo / "a.md").write_text("clean again in the working tree\n")
        rc, out, _ = self.scan("--staged")
        self.assertEqual(rc, 1)
        self.assertIn("a.md:1: [private-term]", out)
        git(self.repo, "reset", "-q")
        rc, _, _ = self.scan("--staged")
        self.assertEqual(rc, 0)

    def test_message_file(self):
        msg = Path(self._tmp.name) / "msg"
        msg.write_text(f"hello {TERM}\n", encoding="utf-8")
        rc, out, _ = run_main("--root", str(self.repo), "--terms", str(self.terms), "--message-file", str(msg))
        self.assertEqual(rc, 1)
        self.assertNotIn(TERM, out)
        msg.write_text("fine\n", encoding="utf-8")
        rc, _, _ = run_main("--root", str(self.repo), "--terms", str(self.terms), "--message-file", str(msg))
        self.assertEqual(rc, 0)

    def test_strip_png_command(self):
        self.commit(files={"a.png": make_png(chunk(b"tEXt", b"k\x00v"))})
        rc, out, _ = run_main("--root", str(self.repo), "--strip-png")
        self.assertEqual(rc, 0)
        self.assertIn("a.png", out)
        self.assertEqual((self.repo / "a.png").read_bytes(), make_png())

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, _ = run_main("--root", d, "--terms", str(self.terms))
        self.assertEqual(rc, 2)


def quiet_install(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return ih.main(argv)


class HookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")

    def test_install_and_remove(self):
        self.assertEqual(quiet_install(["--root", str(self.repo)]), 0)
        for name in ih.HOOKS:
            hook = self.repo / ".git" / "hooks" / name
            self.assertTrue(hook.is_file())
            self.assertNotIn(b"\r\n", hook.read_bytes())
            self.assertTrue(hook.read_text().startswith("#!/bin/sh"))
        self.assertEqual(quiet_install(["--root", str(self.repo)]), 0)      # reinstalling over our own hook is fine
        quiet_install(["--root", str(self.repo), "--remove"])
        self.assertFalse((self.repo / ".git" / "hooks" / "pre-commit").exists())

    def test_foreign_hook_is_kept_without_force(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho mine\n")
        self.assertEqual(quiet_install(["--root", str(self.repo)]), 1)
        self.assertIn("echo mine", hook.read_text())
        self.assertEqual(quiet_install(["--root", str(self.repo), "--force"]), 0)
        self.assertIn("check_public", hook.read_text())

    @unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
    def test_hook_blocks_a_bad_commit(self):
        # a copy of the scanner inside the temp repo, as in a real clone
        (self.repo / "tools").mkdir()
        shutil.copy(ROOT / "tools" / "check_public.py", self.repo / "tools" / "check_public.py")
        terms = Path(self._tmp.name) / "terms.txt"
        terms.write_text(TERM + "\n", encoding="utf-8")
        quiet_install(["--root", str(self.repo)])
        env = dict(os.environ, **GIT_ENV)
        env[cp.ENV_TERMS] = str(terms)

        def commit(msg):
            return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=T", "-c",
                                   "user.email=t@users.noreply.github.com", "commit", "-q", "-m", msg],
                                  capture_output=True, text=True, env=env)

        (self.repo / "ok.md").write_text("fine\n")
        git(self.repo, "add", "ok.md")
        self.assertEqual(commit("fine").returncode, 0)
        (self.repo / "bad.md").write_text(TERM + "\n")
        git(self.repo, "add", "bad.md")
        res = commit("add bad")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn(TERM, res.stdout + res.stderr)
        git(self.repo, "reset", "-q", "bad.md")
        (self.repo / "ok2.md").write_text("fine too\n")
        git(self.repo, "add", "ok2.md")
        self.assertNotEqual(commit("mention " + TERM).returncode, 0)


if __name__ == "__main__":
    unittest.main()
