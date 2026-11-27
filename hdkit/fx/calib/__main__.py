"""python -m hdkit.fx.calib <command>   (RenderDoc: --renderdoc DIR or env HDKIT_RENDERDOC; --home: qrenderdoc's HOME)

  env     --work DIR --capfile PATH [--log PATH]      the game-environment variables that load the capture layer (JSON)
  trigger --out FILE [--target NAME] [--count N]      capture the next frame(s) of a running target (prints the answer)
  extract CAPTURE --dir DIR [--events E,..|--auto] [--max-draws N] [--tex-max PX]   draw data of a capture
  decode  DIR [--events E,..]                         draws/<eid>/*.bin -> draws/<eid>/draw.npz (+ summary)
  compare --render PNG --shot PNG --twin PNG --feet X,Y [--render-feet X,Y] [--out DIR]   viewer render vs game shot
  unitlight DRAW_DIR --game-view GV.json --out RIG.json   the game's unit light of a ps_models_p draw as a viewer rig
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import rdoc


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["unitlight"]:
        from . import unitlight
        return unitlight.main(args[1:])
    ap = argparse.ArgumentParser(prog="hdkit.fx.calib")
    ap.add_argument("--renderdoc", default=os.environ.get("HDKIT_RENDERDOC"))
    ap.add_argument("--home", default=os.environ.get("HDKIT_RENDERDOC_HOME"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env")
    e.add_argument("--work", required=True)
    e.add_argument("--capfile", required=True)
    e.add_argument("--log")
    t = sub.add_parser("trigger")
    t.add_argument("--out", required=True)
    t.add_argument("--target", default="")
    t.add_argument("--count", type=int, default=1)
    t.add_argument("--frames", type=int, default=1)
    x = sub.add_parser("extract")
    x.add_argument("capture")
    x.add_argument("--dir", required=True)
    x.add_argument("--events", default="")
    x.add_argument("--auto", action="store_true")
    x.add_argument("--list-state", default="all", help="all | none | FIRST,LAST: draws whose pipeline state is listed")
    x.add_argument("--max-draws", type=int, default=40)
    x.add_argument("--list-stencil", action="store_true", help="also list each draw's stencil/depth state in actions.json")
    x.add_argument("--tex-max", type=int, default=1024)
    x.add_argument("--timeout", type=float, default=1800)
    d = sub.add_parser("decode")
    d.add_argument("dir")
    d.add_argument("--events", default="")
    c = sub.add_parser("compare")
    c.add_argument("--render", required=True)
    c.add_argument("--shot", required=True)
    c.add_argument("--twin")
    c.add_argument("--feet", required=True)
    c.add_argument("--render-feet")
    c.add_argument("--out")
    a = ap.parse_args(argv)

    def home():
        if not a.home:
            raise SystemExit("--home (or HDKIT_RENDERDOC_HOME): a folder of our own for qrenderdoc's settings")
        return Path(a.home)

    if a.cmd == "env":
        print(json.dumps(rdoc.game_env(rdoc.find_root(a.renderdoc), Path(a.work), a.capfile, a.log)))
    elif a.cmd == "trigger":
        ans = rdoc.run_script(a.renderdoc, rdoc.TRIGGER, {"out": a.out, "target": a.target, "count": a.count,
                                                           "frames": a.frames}, home(), timeout=300)
        print(json.dumps(ans))
        return 0 if ans.get("ok") else 1
    elif a.cmd == "extract":
        out = Path(a.dir)
        out.mkdir(parents=True, exist_ok=True)
        args = {"out": str(out / "extract.json"), "capture": str(Path(a.capture).resolve()), "dir": str(out),
                "events": [int(v) for v in a.events.split(",") if v.strip()], "auto": a.auto,
                "max_draws": a.max_draws, "tex_max": a.tex_max,
                "list_stencil": a.list_stencil, "list_state": a.list_state if a.list_state in ("all", "none") else [int(v) for v in a.list_state.split(",")]}
        ans = rdoc.run_script(a.renderdoc, rdoc.EXTRACT, args, home(), timeout=a.timeout, log=out / "qrenderdoc.log")
        print(json.dumps({k: v for k, v in ans.items() if k != "errors"}, indent=1))
        for er in ans.get("errors", [])[:10]:
            print("error at %s: %s" % (er["where"], er["error"].strip().splitlines()[-1]), file=sys.stderr)
        return 0 if ans.get("ok") else 1
    elif a.cmd == "decode":
        from . import draws
        res = draws.decode_dir(Path(a.dir), [int(v) for v in a.events.split(",") if v.strip()] or None)
        print(json.dumps(res, indent=1))
    elif a.cmd == "compare":
        from . import compare
        res = compare.compare_files(a.render, a.shot, a.twin, [float(v) for v in a.feet.split(",")],
                                    [float(v) for v in a.render_feet.split(",")] if a.render_feet else None, a.out)
        print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
