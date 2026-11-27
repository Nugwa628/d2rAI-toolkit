"""What each D2R HD particle effect needs, and how much of that the viewer supports (hdfx survey).

For every `.particles` file under a root: its renderers (kind, material, the switches that are on), its sampler kinds, and
the CBVM opcodes and engine calls of its scripts. The SUPPORTED_* sets below say what the render, VM and sampler lanes can
do so far (empty: nothing yet). A file is `drawn` when every need is supported, `partly` when some are, else `not`;
`empty` when it needs nothing (no layer, renderer, sampler or script reachable: the game draws nothing for it either).
Objects the effect root does not reach (baker leftovers) are not counted. A mesh-less ModelInstanceTracking renderer
needs an indexed unit or object with a previewable model binding to count as drawn.
`uses` counts the distinct files under the root and the `--refs` folders that name the effect's path (`hd/vfx/particles/...`).

  python -m hdkit.fx.coverage <root> --out <dir> [--refs <folder or file> ...] [--fired <firedby.json>] 

Writes <dir>/coverage.json (every row, the summary, the supported sets) and <dir>/coverage.md (the ranked table).
Reads: `hdkit.formats.hd_particles` for renderers and properties, `hdkit.fx.cbvm` for scripts.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

from hdkit.formats import hd_particles as hp
from hdkit.fx import cbvm as hc

# ---------------------------------------------------------------- supported sets (the lanes fill these in)
SUPPORTED_RENDER_KINDS: set[str] = set()     # billboard, ribbon, mesh, light, decal          (render lane)
SUPPORTED_RENDER_FEATURES: set[str] = set()  # renderer switches that are on, by property name  (render lane)
SUPPORTED_MATERIALS: set[str] = set()        # material file names, e.g. Diablo_Uber_Mesh.pkma (render lane)
SUPPORTED_OPCODES: set[str] = set()          # CBVM instruction names as hd_cbvm.OPS spells them (vm lane)
SUPPORTED_CALLS: set[str] = set()            # engine call names as the scripts' function tables spell them (vm lane)
SUPPORTED_SAMPLERS: set[str] = set()         # curve, shape, turbulence, eventstream, text (samplers lane)

def _from_lanes() -> None:
    """Fill the sets from the lanes that have landed: the VM runs every opcode of fx/cbvm.py and the calls in
    fx/vm.ENGINE; fx/samplers.py and fx/sim.py cover curve, shape, turbulence and event-stream samplers; fx/render.py
    lists the renderer kinds, materials and features it draws (SUPPORTED_KINDS/MATERIALS/FEATURES)."""
    try:
        from . import cbvm, vm
        SUPPORTED_OPCODES.update(cbvm.OPS.values())
        SUPPORTED_CALLS.update(k for k in vm.ENGINE           # a `doubtful:` model is a guess, not support
                               if not str(vm.NOTES.get(k, "")).startswith("doubtful"))
        SUPPORTED_SAMPLERS.update(("curve", "shape", "turbulence", "eventstream"))
    except Exception:  # noqa: BLE001  (a lane missing: its sets stay empty)
        pass
    try:                                     # the render lane's renderer (fx/render.py, lane mats): what it draws
        from . import render
        SUPPORTED_RENDER_KINDS.update(render.SUPPORTED_KINDS)
        SUPPORTED_RENDER_FEATURES.update(render.SUPPORTED_FEATURES)
        SUPPORTED_MATERIALS.update(render.SUPPORTED_MATERIALS)
    except Exception:  # noqa: BLE001
        pass
    try:                                     # lane r3-misc: what the game itself leaves undrawn or ignores (fx/gamekinds.py)
        from . import gamekinds
        SUPPORTED_RENDER_KINDS.update(gamekinds.NO_MATERIAL_KINDS)
        SUPPORTED_RENDER_FEATURES.update(gamekinds.GAME_IGNORES)
        SUPPORTED_MATERIALS.update(gamekinds.NO_MATERIAL)
    except Exception:  # noqa: BLE001
        pass


_from_lanes()

SAMPLER_CLASSES = {
    "CParticleNodeSamplerData_Curve": "curve",
    "CParticleNodeSamplerData_Shape": "shape",
    "CParticleNodeSamplerData_Turbulence": "turbulence",
    "CParticleNodeSamplerData_EventStream": "eventstream",
    "CParticleNodeSamplerData_Text": "text",
}
NOT_A_FEATURE = {"EnableRendering"}     # the layer's own on/off switch, not a feature
TEXT_EXT = {".json", ".tsv", ".txt", ".md", ".py"}
PATH_RE = re.compile(rb"hd/vfx/particles/[A-Za-z0-9_./\-]+?\.particles")


# ---------------------------------------------------------------- one file
def kind_of(r: hp.Renderer) -> str:
    """The renderer kind; kind 5 is a light, or a decal when its material is one (hd_particles only knows 'light');
    kinds 3 and 6 by name (triangle, sound: fx/gamekinds.KIND_IDS)."""
    if r.kind == "light" and "decal" in r.material.lower():
        return "decal"
    if r.kind in ("3", "6"):
        return {"3": "triangle", "6": "sound"}[r.kind]
    return r.kind


def features_of(r: hp.Renderer) -> list[str]:
    return sorted(n for n, p in r.props.items() if p.value == hp.ON and n not in NOT_A_FEATURE)


def _orphans(data: bytes) -> set:
    """Objects (1-based) the effect root does not reach: nodes the baker left behind, which the game never loads into
    a layer (e.g. the Text sampler of vfx_yellowball_glow_light: only its own output pin refers to it)."""
    try:
        from . import pfile
        pf = pfile.PFile.read(data)
        if not pf.objects or pf.obj(1).cls != "CParticleEffect":     # no effect root (a synthetic file): count all
            return set()
        return set(pf.unreachable())
    except Exception:  # noqa: BLE001  (a file pfile cannot read: count everything, as before)
        return set()


def survey_file(path: Path, rel: str) -> dict:
    """The needs of one file. Raises ValueError when the file is not a particle file (the caller records it)."""
    pf = hp.ParticleFile(path.read_bytes())
    rs = pf.renderers()
    orphans = _orphans(bytes(pf.raw))
    samplers = collections.Counter(SAMPLER_CLASSES[c] for i, c in ((i, pf.cls(i)) for i in range(len(pf.objects)))
                                   if c in SAMPLER_CLASSES and i + 1 not in orphans)
    ops, calls, n_scripts = collections.Counter(), collections.Counter(), 0
    for s in hc.scripts(pf):
        n_scripts += 1
        ops.update(i.name for i in s.code)
        for i in s.code:
            if i.op == hc.CODE["call"] and s.fn_names and i.imm[2] < len(s.fn_names):
                calls[s.fn_names[i.imm[2]]] += 1
    tracking = any(r.kind == "mesh" and r.enabled and r.obj + 1 not in orphans
                   and "ModelInstanceTracking" in r.props and r.props["ModelInstanceTracking"].value == hp.ON
                   and not ("General.Mesh" in r.props and pf.prop_string(r.props["General.Mesh"])) for r in rs)
    return {
        **({"requires_tracked_model": True} if tracking else {}),
        "path": rel,
        "bytes": path.stat().st_size,
        "renderers": len(rs),
        "render": {
            "kinds": dict(collections.Counter(kind_of(r) for r in rs)),
            "materials": dict(collections.Counter(r.material for r in rs)),
            "features": dict(collections.Counter(f for r in rs for f in features_of(r))),
        },
        "samplers": dict(samplers),
        "scripts": n_scripts,
        "vm": {"opcodes": dict(ops), "calls": dict(calls)},
    }


def needs(e: dict) -> set[tuple[str, str]]:
    r, v = e["render"], e["vm"]
    return ({("kind", k) for k in r["kinds"]} | {("material", m) for m in r["materials"]}
            | {("feature", f) for f in r["features"]} | {("sampler", s) for s in e["samplers"]}
            | {("op", o) for o in v["opcodes"]} | {("call", c) for c in v["calls"]})


def supported() -> set[tuple[str, str]]:
    """The SUPPORTED_* sets as needs-keys; read at call time, so a lane (or a test) may change them."""
    return ({("kind", x) for x in SUPPORTED_RENDER_KINDS} | {("feature", x) for x in SUPPORTED_RENDER_FEATURES}
            | {("material", x) for x in SUPPORTED_MATERIALS} | {("sampler", x) for x in SUPPORTED_SAMPLERS}
            | {("op", x) for x in SUPPORTED_OPCODES} | {("call", x) for x in SUPPORTED_CALLS})


def assess(e: dict, fired=None) -> dict:
    """Adds `needs` (a count), `missing` ('group:name' strings still unsupported) and `status`."""
    need = needs(e)
    got = need & supported()
    binding = None
    if e.get("requires_tracked_model"):
        e["tracking_contexts"] = fired.tracking_contexts(e["path"]) if fired else []
        if not e["tracking_contexts"]:
            got.discard(("feature", "ModelInstanceTracking"))
            binding = fired.effects.get(e["path"].lower()) if fired else None
    e["needs"] = len(need)
    e["missing"] = sorted(f"{k}:{v}" for k, v in need - got)
    e["status"] = ("empty" if not need else "not") if not got else ("drawn" if got == need else "partly")
    if binding and binding.get("status") == "unreferenced":
        e["status"] = "unreferenced"
    return e


# ---------------------------------------------------------------- references (the ranking)
def count_refs(sources: list) -> collections.Counter:
    """Distinct files that name each particle path (`hd/vfx/particles/...`), over folders and files of `sources`.
    Only text files (json, tsv, txt, md, py) are read; each file counts once per path it names."""
    counts: collections.Counter = collections.Counter()
    for src in sources:
        p = Path(src)
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in TEXT_EXT]
        for f in files:
            for m in set(PATH_RE.findall(f.read_bytes())):
                counts[m.decode("ascii")] += 1
    return counts


# ---------------------------------------------------------------- the report
def run(root, refs=(), progress=None, fired=None) -> dict:
    """Surveys every `.particles` under `root`. `refs`: extra folders or files naming effects (the mod's data, docs).
    The root's own json files count as references too. `fired`: a `firedby.json` (lane r3-bind3): each row gets
    `fired_by` {status, summary} and the report a `fired` count by status."""
    root = Path(root)
    t0 = time.time()
    rows, errors = [], []
    from .firedby import FiredBy
    fb = FiredBy.load(fired) if fired else None
    files = sorted(root.rglob("*.particles"))
    for k, f in enumerate(files, 1):
        rel = f.relative_to(root).as_posix()
        try:
            rows.append(assess(survey_file(f, rel), fb))
        except (ValueError, IndexError, KeyError, OSError) as exc:
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
        if progress and k % 200 == 0:
            progress(f"{k}/{len(files)} files")
    uses = count_refs([root, *refs])
    for e in rows:
        e["uses"] = uses.get(e["path"], 0)
    if fb:
        for e in rows:
            f = fb.of(e["path"])
            e["fired_by"] = {"status": f["status"] if f else "unknown", "summary": fb.summary(e["path"])}
    rows.sort(key=lambda e: (-e["uses"], e["path"]))
    status = collections.Counter(e["status"] for e in rows)
    return {
        "root": str(root),
        "refs": [str(x) for x in [root, *refs]],
        "files": len(files),
        "surveyed": len(rows),
        "errors": errors,
        "status": {k: status.get(k, 0) for k in ("drawn", "partly", "not", "empty", "unreferenced")},
        "supported": {
            "render_kinds": sorted(SUPPORTED_RENDER_KINDS), "render_features": sorted(SUPPORTED_RENDER_FEATURES),
            "materials": sorted(SUPPORTED_MATERIALS), "opcodes": sorted(SUPPORTED_OPCODES),
            "calls": sorted(SUPPORTED_CALLS), "samplers": sorted(SUPPORTED_SAMPLERS)},
        "seconds": round(time.time() - t0, 1),
        "rows": rows,
        **({"fired": dict(collections.Counter(e["fired_by"]["status"] for e in rows)), "fired_index": str(fired)}
           if fb else {}),
    }


def _cell(d: dict, n: int = 99) -> str:
    items = [f"{k} {v}" if v != 1 else k for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))]
    if len(items) > n:
        items = items[:n] + [f"+{len(items) - n}"]
    return ", ".join(items) or "-"


def markdown(rep: dict, top: int = 40) -> str:
    rows = rep["rows"]
    out = [f"# HD particle effects: coverage ({rep['surveyed']} of {rep['files']} files)", "",
           f"Root `{rep['root']}`. Uses: distinct files naming the effect's path, in {len(rep['refs'])} sources "
           f"(the root itself first). Status: drawn {rep['status']['drawn']}, partly {rep['status']['partly']}, "
           f"not {rep['status']['not']}, empty (nothing to draw) {rep['status'].get('empty', 0)}, "
           f"unreferenced {rep['status'].get('unreferenced', 0)}. Supported sets (empty = nothing yet): "
           + ("; ".join(f"{k} {len(v)}" for k, v in rep['supported'].items()) or "none") + ".", ""]
    if rep["errors"]:
        out += [f"Not particle files or not readable ({len(rep['errors'])}):", ""]
        out += [f"- `{e['path']}`: {e['error']}" for e in rep["errors"]] + [""]
    head = ["path", "uses", "status", "renderers (kinds)", "materials", "features on", "samplers", "scripts",
            "opcodes", "calls", "missing"]

    def line(e: dict) -> str:
        r = e["render"]
        cells = [f"`{e['path']}`", str(e["uses"]), e["status"], _cell(r["kinds"]), _cell(r["materials"]),
                 _cell(r["features"]), _cell(e["samplers"]), str(e["scripts"]), _cell(e["vm"]["opcodes"], 6),
                 _cell(e["vm"]["calls"], 6), str(len(e["missing"]))]
        return "| " + " | ".join(cells) + " |"
    out += ["## Most used", "", "| " + " | ".join(head) + " |", "|" + "-|" * len(head)]
    out += [line(e) for e in rows[:top]] + [""]
    out += ["## All files (most used first)", "", "| " + " | ".join(head) + " |", "|" + "-|" * len(head)]
    out += [line(e) for e in rows] + [""]
    if rep.get("fired"):
        out += fired_section(rep)
    return "\n".join(out)


def fired_section(rep: dict) -> list:
    """What fires each effect (lane r3-bind3's index): counts by status, coverage status x fired status, every effect."""
    rows = rep["rows"]
    st = rep["fired"]
    cross = collections.Counter((e["status"], e["fired_by"]["status"]) for e in rows if "fired_by" in e)
    out = ["## What fires each effect", "",
           "Index `%s`. Fired status: %s." % (rep.get("fired_index"), ", ".join(
               "%s %d" % (k, st.get(k, 0)) for k in ("bound", "exe", "orphan", "manifest", "unreferenced", "unknown"))), "",
           "| coverage status | bound | exe | orphan | manifest | unreferenced |", "|-|-|-|-|-|-|"]
    for c in ("drawn", "partly", "not", "empty", "unreferenced"):
        out.append("| %s | %s |" % (c, " | ".join(str(cross.get((c, f), 0))
                                                 for f in ("bound", "exe", "orphan", "manifest", "unreferenced"))))
    out += ["", "| path | coverage | fired by |", "|-|-|-|"]
    out += ["| `%s` | %s | %s |" % (e["path"], e["status"], e["fired_by"]["summary"].replace("|", "/"))
            for e in sorted(rows, key=lambda e: e["path"]) if "fired_by" in e]
    return out + [""]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-effect needs of D2R's HD particle files and their coverage.")
    ap.add_argument("root", help="folder holding hd/vfx/particles/ (e.g. the extracted D2R data folder)")
    ap.add_argument("--out", required=True, help="folder for coverage.json and coverage.md")
    ap.add_argument("--refs", nargs="*", default=[], help="folders or files naming effects (the mod's json, docs)")
    ap.add_argument("--fired", help="firedby.json of hdkit.fx.firedby: adds what fires each effect")
    a = ap.parse_args(argv)
    rep = run(a.root, a.refs, progress=lambda s: print(s, file=sys.stderr, flush=True), fired=a.fired)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    (out / "coverage.md").write_text(markdown(rep), encoding="utf-8")
    print(f"surveyed {rep['surveyed']} of {rep['files']} files in {rep['seconds']} s; errors {len(rep['errors'])}; "
          f"status {rep['status']}; wrote {out / 'coverage.json'} and coverage.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
