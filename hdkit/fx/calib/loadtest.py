"""Save -> in-game load test, the general part (docs/hdfx.md section 7): an edit made through the MCP tools, shown in the game
and in the viewer, and a picture that puts the two next to each other.

  mcp_recolour(...)   drive the stdio MCP server (`python -m hdkit.view.mcp_server`) end to end: fx_inspect,
                      fx_edit (a colour op on every colour stream), fx_validate, fx_render before/after, fx_export into a mod
                      folder. Returns where the edited file landed and the whole transcript.
  logical_of_file     the logical game path of an exported file (<mod>/data/<logical>)
  lay_files           copy {logical: file} under a mod's data folder (the game folder only), with sha256 of every file; the
                      site runner calls it from game_ctl.PRE_LAUNCH, after the whole-slot snapshot, so the run's restore
                      takes the files out again
  shot_ages           effect age (s) of every shot of a calib bench folder: (harness frame - frame at the spawn) / 25
  tint_stats          how far a frame moved from its empty twin and which way (green excess), to compare game and viewer
  build_pair          pair.jpg (rows: in-game edited | viewer edited | in-game unedited at the same effect age) and a GIF

No game folder, slot, mod name or project path is named here: the host project's site runner passes them in.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np

FPS = 25.0                                  # the game's frame rate: effect age = frames / FPS (docs/hdfx.md section 7)


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------------------ files
def logical_of_file(path) -> str:
    """The logical game path of `path`: what follows the last `data` folder of an exported file
    (<mod>/data/hd/vfx/x.particles -> hd/vfx/x.particles), else the `logical` of an edit session folder (session.json)."""
    p = Path(path)
    if p.is_dir() and (p / "session.json").is_file():
        meta = json.loads((p / "session.json").read_text(encoding="utf-8"))
        if meta.get("logical"):
            return meta["logical"]
    parts = p.resolve().parts
    if "data" in parts[:-1]:
        i = len(parts) - 2 - parts[:-1][::-1].index("data")
        return "/".join(parts[i + 1:])
    raise ValueError("no logical path for %s: it is not under a `data` folder (pass the logical path)" % path)


def lay_files(files: dict, data_root, base) -> list:
    """Copy {logical path: source file} to <data_root>/<logical>; every destination must stay inside `base` (the game
    folder). Returns [{logical, dest, sha256, before}] (`before`: sha256 of the file that was there, or None)."""
    data_root, base = Path(os.path.realpath(data_root)), Path(os.path.realpath(base))
    if not data_root.is_dir():
        raise RuntimeError("no mod data folder at %s" % data_root)
    done = []
    for logical, src in files.items():
        dest = Path(os.path.realpath(data_root / logical))
        if base not in dest.parents or ".." in Path(logical).parts or Path(logical).is_absolute():
            raise RuntimeError("destination outside the game folder: %s" % dest)
        before = sha256(dest) if dest.is_file() else None
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        done.append({"logical": logical, "dest": str(dest), "sha256": sha256(dest), "before": before})
    return done


# ------------------------------------------------------------------------------------------------------------ MCP
async def _mcp_calls(tools_dir, env: dict, calls: list) -> list:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "hdkit.view.mcp_server"], cwd=str(tools_dir),
                                   env={**os.environ, **env, "PYTHONPATH": str(tools_dir)})
    out = []
    async with stdio_client(params) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            out.append({"tool": "list_tools", "args": {}, "result": names})
            for fn in calls:                        # a call may depend on the one before: fn(previous results) -> (tool, args)
                tool, args = fn(out)
                res = await s.call_tool(tool, args)
                text = next((c.text for c in res.content if c.type == "text"), "{}")
                out.append({"tool": tool, "args": args, "result": json.loads(text),
                            "images": sum(1 for c in res.content if c.type == "image")})
    return out


def _mcp_run(tools_dir, out_dir, roots, calls) -> list:
    env = {"HDKIT_FX_OUT": str(Path(out_dir) / "mcp-out"), "HDKIT_VIEW_ROOTS": os.pathsep.join(roots)}
    tr = asyncio.run(_mcp_calls(tools_dir, env, calls))
    for t in tr[1:]:
        if not t["result"].get("ok", True) and not t["result"].get("refused"):
            raise RuntimeError("MCP %s failed: %s" % (t["tool"], t["result"].get("error")))
    return tr


def mcp_recolour(logical: str, rgb, out_dir, tools_dir, roots=("casc",)) -> dict:
    """Open `logical` over `roots`, tint every colour stream to `rgb` (the colour op multiplies the grey level the effect
    computes: the smoke becomes that colour), validate, and export the edited file to <out_dir>/mod/data/<logical>, all
    through the stdio MCP server. Returns {file, logical, sha256, session, streams, validate, transcript}; the transcript
    is also written to <out_dir>/mcp-transcript.json. No GPU: the viewer renders come later (`mcp_renders`)."""
    out_dir = Path(out_dir)
    mod, sess = out_dir / "mod", out_dir / "session"
    mod.mkdir(parents=True, exist_ok=True)
    roots = list(roots)
    rgba = [float(x) for x in rgb]
    calls = [
        lambda t: ("fx_inspect", {"path": logical, "roots": roots, "graph": False}),
        lambda t: ("fx_edit", {"path": logical, "roots": roots, "out": str(sess), "validate": False,
                               "ops": [{"op": "color", "stream": s, "rgba": rgba}
                                       for s in t[-1]["result"]["colour_streams"]]}),
        lambda t: ("fx_validate", {"path": str(sess), "roots": roots}),
        lambda t: ("fx_export", {"path": str(sess), "mod_dir": str(mod), "roots": roots}),
    ]
    tr = _mcp_run(tools_dir, out_dir, roots, calls)
    (out_dir / "mcp-transcript.json").write_text(json.dumps(tr, indent=1, default=str), encoding="utf-8")
    inspect, _edit, val, ex = (t["result"] for t in tr[1:])
    return {"file": ex["written"][0]["file"], "logical": ex["written"][0]["logical"], "sha256": ex["written"][0]["sha256"],
            "session": str(sess), "mod": str(mod), "streams": inspect["colour_streams"], "validate": val["ok"],
            "export_missing": ex.get("missing"), "transcript": tr}


def mcp_renders(logical: str, session, out_dir, tools_dir, roots=("casc",), t: float = 3.0, size: str = "640x480",
                wait_s: float = 900.0) -> list:
    """fx_render through the stdio MCP server: the effect as it is in the game files, then the edit session, at effect time
    `t`, alone on the background (the viewer's check of an edit before any game run). Waits up to `wait_s` for the GPU gate.
    Returns [before PNG path, after PNG path] (None where the gate stayed busy)."""
    out_dir = Path(out_dir)
    roots = list(roots)
    calls = [lambda tr, p=p, name=name: ("fx_render", {"path": p, "roots": roots, "t": t, "size": size,
                                                       "out": str(out_dir / name), "wait_s": wait_s})
             for p, name in ((logical, "render-before"), (str(session), "render-after"))]
    tr = _mcp_run(tools_dir, out_dir, roots, calls)
    (out_dir / "mcp-renders.json").write_text(json.dumps(tr, indent=1, default=str), encoding="utf-8")
    return [(t_["result"].get("files") or [None])[0] for t_ in tr[1:]]


# ------------------------------------------------------------------------------------------------------------ pictures
def shot_ages(bench: dict) -> list:
    """Effect age in seconds of every shot of a bench.json: the game's frame counter at the shot (interpolated over `clock`)
    minus the counter at the spawn, over FPS."""
    clk = np.array(bench["clock"], float)
    f0 = float(bench["frames_at_spawn"])
    return [float((np.interp(s["t"], clk[:, 0], clk[:, 1]) - f0) / FPS) for s in bench["shots"]]


def tint_stats(frame, twin, thr: int = 30) -> dict:
    """Where `frame` (RGB image or array) differs from its empty `twin` by more than `thr` (summed channels), how many pixels,
    the mean signed change per channel there, and the green excess of the frame over those pixels: mean of G - (R + B) / 2."""
    a = np.asarray(frame.convert("RGB") if hasattr(frame, "convert") else frame, float)
    b = np.asarray(twin.convert("RGB") if hasattr(twin, "convert") else twin, float)
    d = a - b
    m = np.abs(d).sum(axis=2) > thr
    n = int(m.sum())
    if not n:
        return {"pixels": 0, "mean_delta": [0.0, 0.0, 0.0], "green_excess": 0.0}
    px = a[m]
    return {"pixels": n, "mean_delta": [round(float(v), 2) for v in d[m].mean(axis=0)],
            "green_excess": round(float((px[:, 1] - (px[:, 0] + px[:, 2]) / 2).mean()), 2)}


def _nearest(ages, want, ok):
    best = None
    for i, a in enumerate(ages):
        if ok[i] and (best is None or abs(a - want) < abs(ages[best] - want)):
            best = i
    return best


def _gif(frames, path, limit=4_500_000):
    """Save `frames` (full-size panels) as a GIF under `limit` bytes: every 2nd frame at half size, then fewer and smaller."""
    path = Path(path)
    for stride, scale in ((2, 0.5), (3, 0.5), (3, 0.4), (4, 0.4), (5, 0.33), (6, 0.3)):
        small = [f.resize((int(f.width * scale), int(f.height * scale))) for f in frames[::stride]]
        small[0].save(path, save_all=True, append_images=small[1:], duration=100 * stride, loop=0, optimize=True)
        if path.stat().st_size <= limit:
            break
    return str(path)


def build_pair(edit_dir, ref_dir, viewer_dir, crop, out_jpg, out_gif=None, rows: int = 4, label=("in-game edited",
               "viewer edited", "in-game unedited"), min_age: float = 0.0, viewer_ref_dir=None) -> dict:
    """pair.jpg: one row per time, [in-game edited | viewer edited | in-game unedited at the same effect age], all cropped
    to `crop` (x0,y0,x1,y1 of the game window). edit_dir / ref_dir: calib bench folders; viewer_dir: the folder of
    `python -m hdkit.view fx ... --out` (metrics.json frames, one per shot of edit_dir, in order). Also a GIF over the
    series (under 5 MB). With `viewer_ref_dir` (the viewer's render of the unedited effect at the same times) a second
    picture `<out_jpg stem>4.jpg` adds it as a fourth column, and its tint numbers join the rows. Returns the numbers:
    per row the ages and the tint stats of the images."""
    from PIL import Image, ImageDraw
    edit_dir, ref_dir, viewer_dir = Path(edit_dir), Path(ref_dir), Path(viewer_dir)
    eb = json.loads((edit_dir / "bench.json").read_text(encoding="utf-8"))
    rb = json.loads((ref_dir / "bench.json").read_text(encoding="utf-8"))
    meta = json.loads((viewer_dir / "metrics.json").read_text(encoding="utf-8"))
    meta0 = json.loads((Path(viewer_ref_dir) / "metrics.json").read_text(encoding="utf-8")) if viewer_ref_dir else None
    x0, y0, x1, y1 = crop
    W, H = x1 - x0, y1 - y0
    ea, ra = shot_ages(eb), shot_ages(rb)
    r_ok = [not s.get("dup") and a >= 0 for s, a in zip(rb["shots"], ra)]
    e_ok = [not s.get("dup") and a >= min_age for s, a in zip(eb["shots"], ea)]
    idx = [i for i in range(min(len(eb["shots"]), len(meta["frames"]))) if e_ok[i]]
    twin_e = Image.open(edit_dir / eb["twin"]).convert("RGB").crop(crop)
    twin_r = Image.open(ref_dir / rb["twin"]).convert("RGB").crop(crop)

    def panel(i, four=False):
        j = _nearest(ra, ea[i], r_ok)
        g = Image.open(edit_dir / eb["shots"][i]["file"]).convert("RGB").crop(crop)
        v = Image.open(meta["frames"][i]["png"]).convert("RGB")
        r = Image.open(ref_dir / rb["shots"][j]["file"]).convert("RGB").crop(crop)
        ims, names = [g, v, r], list(label)
        v0 = Image.open(meta0["frames"][i]["png"]).convert("RGB") if meta0 else None
        if four and v0 is not None:
            ims, names = ims + [v0], names + ["viewer unedited"]
        p = Image.new("RGB", (len(ims) * (W + 6) - 6, H), (20, 20, 20))
        for k, im in enumerate(ims):
            p.paste(im, (k * (W + 6), 0))
            ImageDraw.Draw(p).text((k * (W + 6) + 6, 6), names[k], fill=(255, 255, 0))
        ImageDraw.Draw(p).text((6, 20), "effect age %.2f s (ref shot at %.2f s)" % (ea[i], ra[j]), fill=(255, 255, 0))
        st = {"age": round(ea[i], 3), "ref_age": round(ra[j], 3), "game_edit": tint_stats(g, twin_e),
              "viewer_edit": tint_stats(v, twin_e), "game_ref": tint_stats(r, twin_r)}
        if v0 is not None:
            st["viewer_ref"] = tint_stats(v0, twin_e)
        return p, st

    def sheet_of(pick, four):
        panels = [panel(i, four)[0] for i in pick]
        sheet = Image.new("RGB", (panels[0].width, len(panels) * (H + 4)), (0, 0, 0))
        for k, p in enumerate(panels):
            sheet.paste(p, (0, k * (H + 4)))
        return sheet

    if not idx:
        raise RuntimeError("no usable shot in %s" % edit_dir)
    pick = sorted({idx[int(round(k * (len(idx) - 1) / max(1, rows - 1)))] for k in range(min(rows, len(idx)))})
    stats = [panel(i)[1] for i in pick]
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    sheet = sheet_of(pick, False)
    sheet.save(out_jpg, quality=86)
    jpg4 = None
    if meta0 is not None:
        jpg4 = Path(out_jpg).with_name(Path(out_jpg).stem + "4.jpg")
        sheet_of(pick, True).save(jpg4, quality=86)
    gif = _gif([panel(i)[0] for i in idx], out_gif) if out_gif else None
    return {"jpg": str(out_jpg), "jpg4": str(jpg4) if jpg4 else None, "gif": gif, "rows": stats, "size": list(sheet.size)}
