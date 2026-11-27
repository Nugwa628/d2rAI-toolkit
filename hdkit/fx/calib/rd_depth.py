"""Runs inside RenderDoc's own Python (qrenderdoc --python; see rd_extract.py): for each event in args["events"], the depth
target bound at that event, read after it ran, written raw under args["dir"] as depth_<eid>_s<slice>.bin (+ .json with the
format, size, slice and target name). args["slices"]: which array slices (default: the bound first slice only).
args["targets"]: events whose first colour target is written the same way (target_<eid>.bin); args["named"]:
{"eid": E, "match": [substrings]}: every texture whose resource name holds one of them, all array slices, at event E
(tex_<id>_s<slice>.bin), e.g. the spot-light shadow array.
Lane hdfx-r3-shadows2: the game's shadow-map slices (units slice after the effect casters) for the viewer comparison."""
import json
import os
import traceback

import renderdoc as rd

A = json.loads(os.environ["HDKIT_RD_ARGS"])
D = A["dir"]
OUT = {"ok": False, "errors": [], "files": []}


def done():
    with open(A["out"], "w") as f:
        json.dump(OUT, f, indent=1)
    os._exit(0)


def main():
    cap = rd.OpenCaptureFile()
    if cap.OpenFile(A["capture"], "", None) != rd.ResultCode.Succeeded:
        OUT["error"] = "open"
        done()
    res, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
    if res != rd.ResultCode.Succeeded:
        OUT["error"] = "replay: %s" % res
        done()
    texs = {int(t.resourceId): t for t in ctl.GetTextures()}
    names = {int(r.resourceId): r.name for r in ctl.GetResources()}
    if not os.path.isdir(D):
        os.makedirs(D)
    for eid in A["events"]:
        try:
            ctl.SetFrameEvent(int(eid), True)
            pipe = ctl.GetPipelineState()
            dt = pipe.GetDepthTarget()
            r = int(dt.resource)
            td = texs[r]
            first = int(getattr(dt, "firstSlice", 0))
            for s in (A.get("slices") or [first]):
                data = ctl.GetTextureData(dt.resource, rd.Subresource(0, int(s), 0))
                fn = "depth_%d_s%d.bin" % (int(eid), int(s))
                with open(os.path.join(D, fn), "wb") as f:
                    f.write(bytes(data))
                meta = {"eid": int(eid), "slice": int(s), "bound_slice": first, "w": td.width, "h": td.height,
                        "arraysize": td.arraysize, "format": td.format.Name(), "bytes": len(data), "name": names.get(r),
                        "file": fn}
                with open(os.path.join(D, fn[:-4] + ".json"), "w") as f:
                    json.dump(meta, f)
                OUT["files"].append(meta)
        except Exception:  # noqa: BLE001
            OUT["errors"].append({"eid": eid, "error": traceback.format_exc()[-1500:]})
    def dump(rid_, td, s, fn, eid, extra=None):
        data = ctl.GetTextureData(rid_, rd.Subresource(0, int(s), 0))
        with open(os.path.join(D, fn), "wb") as f:
            f.write(bytes(data))
        meta = {"eid": int(eid), "slice": int(s), "w": td.width, "h": td.height, "arraysize": td.arraysize,
                "format": td.format.Name(), "bytes": len(data), "name": names.get(int(rid_)), "file": fn,
                "id": int(rid_)}
        meta.update(extra or {})
        with open(os.path.join(D, fn[:-4] + ".json"), "w") as f:
            json.dump(meta, f)
        OUT["files"].append(meta)
    for eid in A.get("targets") or []:
        try:
            ctl.SetFrameEvent(int(eid), True)
            t = [x for x in ctl.GetPipelineState().GetOutputTargets() if int(x.resource)][0]
            dump(t.resource, texs[int(t.resource)], getattr(t, "firstSlice", 0), "target_%d.bin" % int(eid), eid)
        except Exception:  # noqa: BLE001
            OUT["errors"].append({"target": eid, "error": traceback.format_exc()[-1500:]})
    nm = A.get("named")
    if nm:
        try:
            ctl.SetFrameEvent(int(nm["eid"]), True)
            for r_, td in texs.items():
                n = names.get(r_) or ""
                if any(m.lower() in n.lower() for m in nm["match"]):
                    for s in range(max(1, td.arraysize)):
                        dump(td.resourceId, td, s, "tex_%d_s%d.bin" % (r_, s), nm["eid"])
        except Exception:  # noqa: BLE001
            OUT["errors"].append({"named": nm, "error": traceback.format_exc()[-1500:]})
    OUT["ok"] = True
    done()


try:
    main()
except Exception:  # noqa: BLE001
    OUT["error"] = traceback.format_exc()[-2000:]
    done()
