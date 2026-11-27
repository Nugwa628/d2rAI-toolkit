"""Serve the browser page and one exported bundle on 127.0.0.1 only, on demand (no other host, no listing, GET/HEAD).

  python -m hdkit.view serve-web <bundle folder> [--port 8765]     then open http://127.0.0.1:8765/

Mounts: /            -> hdkit/view/web/ (index.html, viewer.js, present.wgsl)
        /shaders.wgsl -> hdkit/view/shaders.wgsl (the CLI's own shader file, not a copy)
        /bundle/      -> the bundle folder
`--fx` also serves the hdfx effect tools as JSON endpoints for the web editor (docs/hdfx-mcp.md, "JSON endpoints"):
GET /api/fx/tools (tools, engine, out_root), POST /api/fx/<tool> (JSON body = the tool's arguments), GET /api/fx/file?path=<file below the fx output
folder>; `--fx-out <folder>` is that folder (default HDKIT_FX_OUT, HDKIT_VIEW_OUT, ./hdview-out) and `--fx-roots name=folder`
(repeatable) the roots tools use when a request names none. Without `--fx` those paths answer 403.
A page opened from file:// cannot load modules or fetch files, so the page needs this (or any static server on
127.0.0.1). `--accept-results <folder>` also takes POST /__result/<name> (JSON or PNG, at most 64 MB each) and writes it
there: the headless check uses it; without the option the server is read-only.
"""
import argparse
import contextlib
import http.server
import json
import mimetypes
import os
import re
import sys
import threading
from pathlib import Path
from urllib.parse import unquote

WEB = Path(__file__).with_name("web")
SHADER = Path(__file__).with_name("shaders.wgsl")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".wgsl": "text/plain; charset=utf-8",
         ".json": "application/json", ".png": "image/png", ".bin": "application/octet-stream", ".css": "text/css"}
FX_TYPES = {**TYPES, ".gif": "image/gif", ".apng": "image/apng", ".mp4": "video/mp4", ".particles": "application/octet-stream",
            ".cbvm": "text/plain; charset=utf-8", ".hfx": "text/plain; charset=utf-8"}
FX_MAX_BODY = 8 << 20
RESULT_RE = re.compile(r"^/__result/([A-Za-z0-9_.\-]{1,80})$")


def _inside(root: Path, parts: list):
    if any(p in (".", "..") or "\\" in p or ":" in p for p in parts):
        return None
    p = root.joinpath(*parts)
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def _finite(o):
    """JSON has no NaN or Infinity (Python writes them anyway and the page's JSON.parse refuses them): non-finite floats become
    null. Real effect data has them, e.g. the raw words of a turbulence sampler."""
    if isinstance(o, float):
        return o if o - o == 0 else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "hdkit-web/1"
    bundle: Path | None = Path(".")
    results: Path | None = None
    fx: bool = False
    fx_roots: tuple = ()
    quiet = True

    # ---- hdfx JSON endpoints ----------------------------------------------------------------------------------------
    def _json(self, code, obj):
        self._send(code, json.dumps(_finite(obj), default=str).encode("utf-8"), "application/json")

    def _fx_guard(self) -> bool:
        """False (and a 403 sent) unless --fx is on and the request is from this machine's own page: Host is 127.0.0.1 or
        localhost (DNS rebinding) and an Origin header, if any, names that host too."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        origin = self.headers.get("Origin")
        okorigin = origin is None or (re.fullmatch(r"https?://(127\.0\.0\.1|localhost)(:\d+)?", origin) is not None)
        if not self.fx or host not in ("127.0.0.1", "localhost") or not okorigin:
            self._json(403, {"ok": False, "error": {"code": "forbidden", "message": "hdfx endpoints are off (--fx) or the "
                                                                                   "request is not from this machine's page"}})
            return False
        return True

    def _fx_get(self):
        from ..fx import api
        if not self._fx_guard():
            return
        url, _, query = self.path.partition("?")
        if url == "/api/fx/tools":
            return self._json(200, {"tools": api.describe(), "engine": api.engine(), "out_root": str(api.out_root())})
        if url == "/api/fx/file":
            q = dict(kv.partition("=")[::2] for kv in query.split("&") if kv)
            f = Path(unquote(q.get("path", "")))
            base = api.out_root()
            try:
                f.resolve().relative_to(base)
            except ValueError:
                return self._json(403, {"ok": False, "error": {"code": "forbidden", "message": "only files below %s" % base}})
            if not f.is_file():
                return self._json(404, {"ok": False, "error": {"code": "not_found", "message": str(f)}})
            return self._send(200, f.read_bytes(), FX_TYPES.get(f.suffix.lower(), "application/octet-stream"))
        self._json(404, {"ok": False, "error": {"code": "not_found", "message": url}})

    def _fx_post(self):
        from ..fx import api
        if not self._fx_guard():
            return
        name = unquote(self.path.split("?", 1)[0])[len("/api/fx/"):]
        n = int(self.headers.get("Content-Length") or 0)
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json" or n > FX_MAX_BODY:
            return self._json(400, {"ok": False, "error": {"code": "bad_args", "message": "POST application/json, at most 8 MB"}})
        try:
            args = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(args, dict):
                raise ValueError("the body is one JSON object")
        except ValueError as e:
            return self._json(400, {"ok": False, "error": {"code": "bad_args", "message": "body: %s" % e}})
        if name not in api.TOOLS:
            return self._json(400, {"ok": False, "error": {"code": "bad_args", "message": "no tool %r" % name}})
        if "roots" in api.schema_of(api.TOOLS[name])["properties"] and not args.get("roots") and self.fx_roots:
            args["roots"] = list(self.fx_roots)
        out = args.get("out")
        if isinstance(out, str):
            try:
                Path(out).resolve().relative_to(api.out_root())
            except ValueError:
                return self._json(403, {"ok": False, "error": {"code": "forbidden",
                                                               "message": "out must be below %s" % api.out_root()}})
        self._json(200, api.call(name, **args))

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def resolve(self):
        rel = unquote(self.path.split("?", 1)[0].split("#", 1)[0])
        parts = [p for p in rel.split("/") if p]
        if not parts:
            return WEB / "index.html"
        if parts == ["shaders.wgsl"]:
            return SHADER
        if parts[0] == "bundle":
            return _inside(self.bundle, parts[1:]) if self.bundle else None
        return _inside(WEB, parts)

    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/fx/"):
            return self._fx_get()
        p = self.resolve()
        if p is None:
            return self._send(404, b"not found")
        ctype = TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        try:
            self._send(200, p.read_bytes(), ctype)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_HEAD = do_GET

    def do_POST(self):
        if self.path.startswith("/api/fx/"):
            return self._fx_post()
        m = RESULT_RE.match(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if self.results is None or not m or n > (64 << 20):
            return self._send(403, b"read-only")
        self.results.mkdir(parents=True, exist_ok=True)
        (self.results / m.group(1)).write_bytes(self.rfile.read(n))
        self._send(200, b"ok")


def make_server(bundle, port=0, results=None, quiet=True, fx=False, fx_out=None,
                fx_roots=()) -> http.server.ThreadingHTTPServer:
    if fx_out:
        os.environ["HDKIT_FX_OUT"] = str(Path(fx_out).resolve())     # the tools' output folder (this process only)
    h = type("BoundHandler", (Handler,), {"bundle": Path(bundle) if bundle else None, "results": Path(results) if results else None,
                                          "quiet": quiet, "fx": fx, "fx_roots": tuple(fx_roots)})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), h)
    srv.daemon_threads = True
    return srv


@contextlib.contextmanager
def running(bundle, port=0, results=None):
    """Serve in a background thread; yields the base URL (trailing slash); stops the server on exit."""
    srv = make_server(bundle, port, results)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.view serve-web", description=__doc__.split("\n")[0])
    ap.add_argument("bundle", type=Path, nargs="?", default=None)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--accept-results", default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--fx", action="store_true", help="serve the hdfx effect tools as JSON endpoints (/api/fx/...)")
    ap.add_argument("--fx-out", default=None, help="output folder of the tools (default HDKIT_FX_OUT / HDKIT_VIEW_OUT)")
    ap.add_argument("--fx-roots", action="append", default=[], help="name=folder default roots of the tools (repeatable)")
    a = ap.parse_args(argv)
    if a.bundle is None and not a.fx:
        print("give a bundle folder (export-web) or --fx", file=sys.stderr)
        return 1
    if a.bundle is not None and not (a.bundle / "scene.json").is_file():
        print("%s has no scene.json (run export-web first)" % a.bundle, file=sys.stderr)
        return 1
    srv = make_server(a.bundle, a.port, a.accept_results, not a.verbose, a.fx, a.fx_out, a.fx_roots)
    print("serving %s%s on http://127.0.0.1:%d/ (Ctrl+C stops it)" % (a.bundle, " + hdfx tools" if a.fx else "", srv.server_address[1]), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
