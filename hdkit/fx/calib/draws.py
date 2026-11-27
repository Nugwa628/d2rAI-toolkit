"""Decode what rd_extract.py wrote (host Python, numpy): per chosen draw, draws/<eid>/draw.npz with

  idx                    the draw's indices (indexed draws), base vertex applied
  v_<input name>         per-vertex attributes (rows 0..max index), decoded to float32 / int as the format says
  i_<input name>         per-instance attributes (rows instance_offset .. + instances)
  push_u32, push_f32     the push constants (vkd3d-proton: root constants and root descriptor addresses)
  <stage>_cb<k>_f32      constant block bytes as float32 rows of 4 (and _u32)
  bda<off>_f32           the bytes a push-constant word points to (a root CBV), as float32 rows of 4 (and _u32)
  <stage>_t<i>_<a>_f32   a buffer SRV's bytes (the particle streams), flat float32 (and _u32)

and a summary (counts, the first rows of every constant block) returned as JSON-able data."""
import json
from pathlib import Path

import numpy as np

_DT = {("Float", 4): "<f4", ("Float", 2): "<f2", ("UInt", 4): "<u4", ("UInt", 2): "<u2", ("UInt", 1): "u1",
       ("SInt", 4): "<i4", ("SInt", 2): "<i2", ("SInt", 1): "i1", ("UNorm", 1): "u1", ("UNorm", 2): "<u2",
       ("SNorm", 1): "i1", ("SNorm", 2): "<i2", ("UScaled", 1): "u1", ("SScaled", 1): "i1"}


def decode_attr(data: bytes, stride: int, offset: int, fmt: dict, first: int, count: int) -> np.ndarray:
    comp, w, n = fmt.get("comp"), int(fmt.get("width") or 4), int(fmt.get("count") or 1)
    dt = np.dtype(_DT.get((comp, w), "<f4" if w == 4 else "u1"))
    if count <= 0 or stride <= 0:
        return np.zeros((0, n), dt)
    buf = np.frombuffer(data, np.uint8)
    need = (first + count - 1) * stride + offset + n * w
    if need > len(buf):
        count = max(0, (len(buf) - offset - n * w) // stride + 1 - first)
    rows = np.stack([buf[(first + i) * stride + offset:(first + i) * stride + offset + n * w] for i in range(count)]) \
        if count > 0 else np.zeros((0, n * w), np.uint8)
    a = rows.copy().view(dt).reshape(count, n)
    if comp == "UNorm":
        a = a.astype(np.float32) / float(np.iinfo(dt).max)
    elif comp == "SNorm":
        a = np.maximum(a.astype(np.float32) / float(np.iinfo(dt).max), -1.0)
    if fmt.get("bgra") and n >= 3:
        a = a[:, [2, 1, 0] + list(range(3, n))]
    return a


def _rows4(b: bytes):
    n = len(b) // 16 * 16
    return np.frombuffer(b[:n], "<f4").reshape(-1, 4), np.frombuffer(b[:n], "<u4").reshape(-1, 4)


def decode_draw(dd: Path) -> dict:
    info = json.loads((dd / "draw.json").read_text(encoding="utf-8"))
    arrays, summary = {}, {"eid": info.get("eid"), "name": info.get("name"), "indices": info.get("indices"),
                           "instances": info.get("instances"), "attrs": {}, "cbuffers": {}}
    vbs = {v["slot"]: v for v in info.get("vbuffers", [])}
    nverts = int(info.get("indices") or 0)
    first_v = int(info.get("vertex_offset") or 0)
    ib = info.get("ibuffer")
    if info.get("indexed") and ib and (dd / ib["file"]).is_file():
        dt = "<u2" if int(ib.get("stride") or 2) == 2 else "<u4"
        allidx = np.frombuffer((dd / ib["file"]).read_bytes(), dt)
        o = int(info.get("index_offset") or 0)
        idx = allidx[o:o + nverts].astype(np.int64) + int(info.get("base_vertex") or 0)
        arrays["idx"] = idx
        first_v, nverts = 0, int(idx.max()) + 1 if len(idx) else 0
    for inp in info.get("inputs", []):
        vb = vbs.get(inp["buffer"])
        if not vb or not vb.get("file") or not inp.get("used", True):
            continue
        data = (dd / vb["file"]).read_bytes()
        if inp["per_instance"]:
            first, count, key = int(info.get("instance_offset") or 0), int(info.get("instances") or 1), "i_"
        else:
            first, count, key = first_v, nverts, "v_"
        a = decode_attr(data, int(vb["stride"]), int(inp["offset"]), inp["format"], first, count)
        name = key + "".join(c if c.isalnum() else "_" for c in inp["name"])
        arrays[name] = a
        with np.errstate(all="ignore"):
            summary["attrs"][name] = {"shape": list(a.shape), "format": inp["format"].get("name"),
                                      "min": a.min(0).tolist() if len(a) else None,
                                      "max": a.max(0).tolist() if len(a) else None}
    if info.get("push") and (dd / info["push"]["file"]).is_file():
        pc = (dd / info["push"]["file"]).read_bytes()
        n = len(pc) // 4 * 4
        arrays["push_u32"] = np.frombuffer(pc[:n], "<u4")
        arrays["push_f32"] = np.frombuffer(pc[:n], "<f4")
    for stage, s in (info.get("stages") or {}).items():
        for k, c in enumerate(s.get("cbuffers", [])):
            if c.get("file") and (dd / c["file"]).is_file():
                f, u = _rows4((dd / c["file"]).read_bytes())
                key = "%s_cb%d" % (stage[0].lower(), k)
                arrays[key + "_f32"], arrays[key + "_u32"] = f, u
                summary["cbuffers"][key] = {"index": c.get("index"), "bytes": c.get("bytes"),
                                            "rows": np.round(f[:16], 5).tolist()}
        for t in s.get("textures", []):
            if t.get("buffer") and t.get("file") and (dd / t["file"]).is_file():
                b = (dd / t["file"]).read_bytes()
                n = len(b) // 4 * 4
                key = "%s_t%d_%d" % (stage[0].lower(), t["index"], t.get("array", 0))
                arrays[key + "_f32"], arrays[key + "_u32"] = np.frombuffer(b[:n], "<f4"), np.frombuffer(b[:n], "<u4")
                summary["cbuffers"][key] = {"buffer": t.get("name"), "bytes": len(b), "elem": t.get("elem")}
    for p in info.get("push_pointers", []):
        f, u = _rows4((dd / p["file"]).read_bytes())
        key = "bda%d" % p["push_offset"]
        arrays[key + "_f32"], arrays[key + "_u32"] = f, u
        summary["cbuffers"][key] = {"buffer": p.get("buffer_name") or p.get("buffer"), "offset": p.get("offset"),
                                    "rows": np.round(f[:16], 5).tolist()}
    # D2R's root signature (dxil-spirv): the VS material constants cb4 are the root CBV at push offset 24; with a raw
    # buffer bound to the VS (t4) the per-instance particle streams are decoded too
    cb4 = next((p for p in info.get("push_pointers", []) if p["push_offset"] == 24), None)
    t4 = next((b for b in ((info.get("stages") or {}).get("Vertex") or {}).get("buffers", []) if b.get("file")), None)
    if cb4 and t4 and (dd / t4["file"]).is_file():
        st = uber_streams((dd / cb4["file"]).read_bytes(), (dd / t4["file"]).read_bytes(), int(info.get("instances") or 1))
        for k, v in st.items():
            arrays["s_" + "".join(ch if ch.isalnum() else "_" for ch in k)] = v
        summary["streams"] = {k: np.round(v, 5).tolist() for k, v in st.items()}
    np.savez_compressed(dd / "draw.npz", **arrays)
    summary["npz"] = str(dd / "draw.npz")
    return summary


# Uber mesh / billboard VS (D2R, read from the captured SPIR-V of uber mesh VS #1736): per-instance streams in the raw
# buffer t4, addressed by the material constants cb4 (16-byte rows). Float4 streams: cb4[9].xyzw, cb4[16].z, cb4[17].w
# are float4 indices (+ instance); scalar streams: cb4[13].zw, cb4[16].w, cb4[17].xy, cb4[18].xy are byte offsets
# (+ 4 * instance); cb4[17].z is the byte offset of a float4x4 per instance (row-major, translation in row 3).
# 0xffffffff = stream absent.
F4_SLOTS = {"cb4[9].x": (9, 0), "cb4[9].y": (9, 1), "cb4[9].z": (9, 2), "cb4[9].w": (9, 3), "cb4[16].z": (16, 2),
            "cb4[17].w": (17, 3)}
SCALAR_SLOTS = {"cb4[13].z": (13, 2), "cb4[13].w": (13, 3), "cb4[16].w": (16, 3), "cb4[17].x": (17, 0),
                "cb4[17].y": (17, 1), "cb4[18].x": (18, 0), "cb4[18].y": (18, 1)}


def uber_streams(cb4: bytes, t4: bytes, instances: int) -> dict:
    """{"matrix": (n, 4, 4), "<slot>": (n, 4) or (n,)} of one uber draw (see the table above)."""
    u = np.frombuffer(cb4[:19 * 16], "<u4").reshape(-1, 4)
    out = {}
    mo = int(u[17][2])
    if mo != 0xFFFFFFFF and mo + 64 * instances <= len(t4):
        out["matrix"] = np.frombuffer(t4[mo:mo + 64 * instances], "<f4").reshape(instances, 4, 4)
    for name, (r, c) in F4_SLOTS.items():
        i = int(u[r][c])
        if i != 0xFFFFFFFF and (i + instances) * 16 <= len(t4):
            out[name] = np.frombuffer(t4[i * 16:(i + instances) * 16], "<f4").reshape(instances, 4)
    for name, (r, c) in SCALAR_SLOTS.items():
        o = int(u[r][c])
        if o not in (0, 0xFFFFFFFF) and o + 4 * instances <= len(t4):
            out[name] = np.frombuffer(t4[o:o + 4 * instances], "<f4")
    return out


def decode_dir(d: Path, events=None) -> dict:
    d = Path(d)
    out = {}
    for dd in sorted((d / "draws").glob("*")):
        if not (dd / "draw.json").is_file() or (events and int(dd.name) not in events):
            continue
        out[dd.name] = decode_draw(dd)
    (d / "decoded.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
