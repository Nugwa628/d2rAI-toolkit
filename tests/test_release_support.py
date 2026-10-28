"""Shared helpers for tests/test_release_*.py: the release/ folder on sys.path, a tiny mod folder to package, a base test
class with a server layout (srv/mymod + srv/incoming), a local HTTP server with Range support, and the fake sftp / rclone
commands in tests/release_fakes/. No network. 7-Zip-dependent classes are skipped when the machine has no 7-Zip."""
import contextlib
import email.utils
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
RELEASE_DIR = HERE.parent / "release"
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

import dl_ingest  # noqa: E402
import package_release as pr  # noqa: E402
import publish_release as pub  # noqa: E402
import release_config  # noqa: E402

FAKES = HERE / "release_fakes"
FAKE_SFTP = [sys.executable, str(FAKES / "fake_sftp.py")]
SEVEN = pr.find_7z()
needs_7z = unittest.skipUnless(SEVEN, "no 7-Zip on this machine")

BASE_FILES = {"global/excel/armor.txt": b"armor table\n", "global/excel/weapons.txt": b"weapons table\n",
              "global/excel/copy-of-weapons.txt": b"weapons table\n"}  # same bytes twice: one object


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def make_mod(root: Path, folder: str, data_files: dict, version="1.0.0") -> Path:
    """A small finished mod folder: MyMod.mpq/modinfo.json, MyMod.mpq/data/<data_files>, a player-editable loader config,
    a plugin metadata file that names the version, and an empty d2rloader/patches folder."""
    mod = root / folder
    shutil.rmtree(mod, ignore_errors=True)
    (mod / f"{folder}.mpq" / "data").mkdir(parents=True)
    (mod / f"{folder}.mpq" / "modinfo.json").write_text(json.dumps({"name": folder, "savepath": f"{folder}/"}), encoding="utf-8")
    for rel, body in data_files.items():
        p = mod / f"{folder}.mpq" / "data" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    (mod / "d2rloader" / "config").mkdir(parents=True)
    (mod / "d2rloader" / "config" / "d2rloader.toml").write_text('[d2rloader]\ndefault_mod = ""\n', encoding="utf-8")
    (mod / "d2rloader" / "metadata.json").write_text(json.dumps({"modVersion": version}), encoding="utf-8")
    (mod / "d2rloader" / "patches").mkdir()
    return mod


class ReleaseBase(unittest.TestCase):
    """A server layout in a temp folder: srv/mymod (the site root) and srv/incoming; a config with the defaults."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cfg = release_config.defaults()
        self.seven = SEVEN
        for p in (mock.patch.object(pub, "find_7z", return_value=self.seven), mock.patch.object(pub, "RETRY_WAIT", 0)):
            p.start()
            self.addCleanup(p.stop)
        pub.configure(self.cfg)
        self.srv = self.tmp / "srv"  # /srv/mod-files on the server
        self.site = self.srv / "mymod"
        self.incoming = self.srv / "incoming"
        self.site.mkdir(parents=True)
        self.incoming.mkdir()

    def make_dist(self, version, files=None, complete=True, downloader=None):
        """A real package_release output (7z, manifest, notes) for a tiny mod folder."""
        src = make_mod(self.tmp / f"src-{version}", "MyMod", BASE_FILES if files is None else files, version)
        out = self.tmp / f"dist-{version}"
        shutil.rmtree(out, ignore_errors=True)
        mod = pr.stage(src, out / "stage" / "MyMod")
        seven = out / pr.archive_name(self.cfg, version, complete)
        pr.write_7z(mod, seven, self.seven)
        man = pr.manifest(self.cfg, version, pr.file_list(mod), seven, complete, pr.empty_dirs(mod))
        (out / "release-manifest.json").write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
        (out / f"RELEASE_NOTES-{version}.md").write_bytes(f"notes {version}\n".encode())
        shutil.rmtree(out / "stage")
        return out

    def ingest(self, expect=0, channels=None):
        with mock.patch("builtins.print"):
            rc = dl_ingest.Ingest(self.srv, "mymod", sevenzip=self.seven, **({"channels": channels} if channels else {})).run()
        self.assertEqual(rc, expect)

    def publish(self, version, channel="test", target=None, files=None, ingest=True, dist=None, **kw):
        target = target or pub.DirTarget(self.site)
        info = pub.publish(target, dist or self.make_dist(version, files), version, channel, log=lambda *_: None, **kw)
        if ingest:
            self.ingest()
        return info

    def channel(self, name):
        return json.loads((self.site / "channels" / f"{name}.json").read_text("utf-8"))

    def ready(self, version):
        return json.loads((self.incoming / version / "READY").read_text("utf-8"))

    def make_exe(self, name="launcher.exe", body=b"MZ downloader"):
        exe = self.tmp / name
        exe.write_bytes(body)
        return exe


# ---------------------------------------------------------------------------------------------------- a Range-capable server
class _Handler(BaseHTTPRequestHandler):
    root: Path = Path(".")

    def log_message(self, *args):
        pass

    def _serve(self, head: bool):
        from urllib.parse import unquote, urlsplit
        raw = unquote(urlsplit(self.path).path)
        parts = [p for p in raw.split("/") if p]
        p = self.root.joinpath(*parts) if parts else None
        if p is None or raw.endswith("/") or any(x in (".", "..") or x.startswith(".") for x in parts) or not p.is_file() \
                or self.root.resolve() not in p.resolve().parents:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = p.read_bytes()
        st = p.stat()
        etag = '"%x-%x"' % (st.st_size, st.st_mtime_ns)
        cache = "public, max-age=31536000, immutable" if parts[0] == "objects" else "no-cache"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        status, body, extra = 200, data, {}
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            if a == "":
                start, end = max(0, len(data) - int(b)), len(data) - 1
            else:
                start, end = int(a), min(int(b) if b else len(data) - 1, len(data) - 1)
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status, body = 206, data[start:end + 1]
            extra["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache)
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)


@contextlib.contextmanager
def serving(root: Path):
    """A local HTTP server for `root` (Range, ETag, Cache-Control like the real host) -> its base URL."""
    handler = type("H", (_Handler,), {"root": Path(root)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(5)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def env_patch(**kw):
    p = mock.patch.dict(os.environ, kw)
    return p
