"""Start the hdfx effect editor: a local server (127.0.0.1 only) for the page `fx.html` and the tools' JSON endpoints.

  python -m hdkit.view fx [--port 8766] [--out <folder>] [--roots name=folder ...]      the real tools (hdkit.fx.api)
  python -m hdkit.view fx --mock                                                          the stand-in (hdkit/view/fxmock.py)

then open http://127.0.0.1:8766/fx.html. `--roots` (repeatable, default HDKIT_VIEW_ROOTS) are the game folders effects are read
from, e.g. d2r=extract/d2r/data; `--out` is where edit sessions and renders go
(default HDKIT_FX_OUT, HDKIT_VIEW_OUT, ./hdview-out). The page and the endpoints: docs/hdfx-web.md and docs/hdfx-mcp.md.
"""
import argparse
import sys

from . import webserve


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.view fx", description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--out", default=None, help="folder for edit sessions and renders")
    ap.add_argument("--roots", action="append", default=[], help="name=folder game roots (repeatable; default HDKIT_VIEW_ROOTS)")
    ap.add_argument("--mock", action="store_true", help="serve the built-in stand-in effects instead of the real tools")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.mock:
        from . import fxmock
        import tempfile
        out = a.out or tempfile.mkdtemp(prefix="hdfx-mock-")
        srv = fxmock.make_server(out, a.port, quiet=not a.verbose)
    else:
        srv = webserve.make_server(None, a.port, None, not a.verbose, True, a.out, a.roots)
    print("hdfx editor on http://127.0.0.1:%d/fx.html%s (Ctrl+C stops it)" % (srv.server_address[1], "  [mock effects]" if a.mock else ""), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
