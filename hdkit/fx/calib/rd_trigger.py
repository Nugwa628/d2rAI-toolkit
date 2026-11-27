"""Runs inside RenderDoc's own Python (qrenderdoc --python; Python 3.8, no numpy, no hdkit): connect to the target whose
process command line (/proc/<pid>/cmdline) contains args["target"] (under Wine every process is "wine64_preloader" to
RenderDoc, and a target's API is empty until its messages are read) and capture args["frames"]
frames (default 1), args["count"] times (default 1). Writes {"ok", "targets", "captures": [{path, frame, bytes, api}]}
to args["out"]."""
import json
import os
import time

import renderdoc as rd

A = json.loads(os.environ["HDKIT_RD_ARGS"])
OUT = {"ok": False, "targets": [], "captures": [], "log": []}


def done():
    with open(A["out"], "w") as f:
        json.dump(OUT, f, indent=1)
    os._exit(0)


def find():
    want = (A.get("target") or "").lower()
    end = time.time() + float(A.get("find_secs", 20))
    while time.time() < end:
        ident, seen = 0, []
        while True:
            ident = rd.EnumerateRemoteTargets("localhost", ident)
            if ident == 0:
                break
            tc = rd.CreateTargetControl("localhost", ident, "hdkit-calib", False)
            if tc is None:
                seen.append({"ident": ident, "busy": True})
                continue
            info = {"ident": ident, "target": tc.GetTarget(), "pid": tc.GetPID(), "api": tc.GetAPI()}
            tc.Shutdown()
            try:
                with open("/proc/%d/cmdline" % info["pid"], "rb") as f:
                    info["cmdline"] = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            except OSError:
                info["cmdline"] = ""
            seen.append(info)
            if want in (info["target"] + " " + info["cmdline"]).lower():
                OUT["targets"] = seen
                return info
        OUT["targets"] = seen
        time.sleep(1.0)
    return None


try:
    t = find()
    if t is None:
        OUT["error"] = "no capture target named %r" % A.get("target")
        done()
    tc = rd.CreateTargetControl("localhost", t["ident"], "hdkit-calib", True)
    if tc is None:
        OUT["error"] = "connect failed"
        done()
    want = int(A.get("count", 1))
    for i in range(want):
        tc.TriggerCapture(int(A.get("frames", 1)))
        end = time.time() + float(A.get("wait_secs", 120))
        got = False
        while time.time() < end and not got:
            msg = tc.ReceiveMessage(None)
            if msg.type == rd.TargetControlMessageType.NewCapture:
                c = msg.newCapture
                OUT["captures"].append({"path": c.path, "frame": c.frameNumber, "bytes": c.byteSize, "api": c.api,
                                        "id": c.captureId})
                got = True
            elif msg.type == rd.TargetControlMessageType.Disconnected:
                OUT["error"] = "target disconnected"
                break
            elif msg.type not in (rd.TargetControlMessageType.Noop, rd.TargetControlMessageType.Busy):
                OUT["log"].append(str(msg.type))
        if not got:
            break
    tc.Shutdown()
    OUT["ok"] = len(OUT["captures"]) == want
except Exception as e:  # noqa: BLE001 (the answer file must be written)
    OUT["error"] = repr(e)
done()
