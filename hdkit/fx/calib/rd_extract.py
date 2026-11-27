"""Runs inside RenderDoc's own Python (qrenderdoc --python; Python 3.8, no numpy, no hdkit): open a capture and write,
under args["dir"]:

  actions.json        every draw: event id, name, marker path, counts, shaders, blend, targets (the frame's draw list)
  draws/<eid>/draw.json + *.bin   for the chosen draws (args["events"], or args["auto"]: blended draws, at most
                      args["max_draws"]): vertex inputs, vertex/index buffer bytes, push constants, constant blocks
                      (bytes + RenderDoc's decoded variables), push-constant words that point into buffers (buffer device
                      addresses: vkd3d-proton passes root CBVs that way) with the bytes there, bound textures, blend and
                      depth state, viewport, shader ids and entry points
  shaders/<id>.spv / .txt   each used shader's bytes and RenderDoc's disassembly
  textures/<id>.png   bound textures (mip 0) up to args["tex_max"] px, and the colour target after each chosen draw

args["out"] gets {"ok", "draws", "chosen", "errors"}. Decoding the bytes into arrays is draws.py's job (host Python)."""
import json
import os
import struct
import time
import traceback

import renderdoc as rd

A = json.loads(os.environ["HDKIT_RD_ARGS"])
D = A["dir"]
OUT = {"ok": False, "errors": [], "chosen": []}
MAXB = int(A.get("max_bytes", 8 << 20))


def done():
    with open(A["out"], "w") as f:
        json.dump(OUT, f, indent=1)
    os._exit(0)


def err(where):
    OUT["errors"].append({"where": where, "error": traceback.format_exc()[-1500:]})


def rid(r):
    return str(int(r)) if r is not None else None


def ename(e):
    return str(e).split(".")[-1]


def fmt(f):
    try:
        return {"type": ename(f.type), "comp": ename(f.compType), "count": f.compCount, "width": f.compByteWidth,
                "bgra": bool(f.BGRAOrder()), "name": f.Name()}
    except Exception:  # noqa: BLE001
        return {"name": str(f)}


def mkdir(p):
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def write(path, data):
    with open(path, "wb") as f:
        f.write(bytes(data))


def var_json(v, depth=0):
    """A ShaderVariable -> {name, rows, cols, type, f, u} or {name, members}."""
    o = {"name": v.name}
    if len(v.members):
        o["members"] = [var_json(m, depth + 1) for m in v.members] if depth < 6 else "..."
        return o
    n = max(1, v.rows) * max(1, v.columns)
    o.update(rows=v.rows, cols=v.columns, type=ename(v.type), f=list(v.value.f32v[:n]), u=list(v.value.u32v[:n]))
    return o


def walk(actions, path, sd, out):
    for a in actions:
        name = a.GetName(sd)
        if len(a.children):
            walk(a.children, path + [name], sd, out)
        if a.flags & rd.ActionFlags.Drawcall:
            out.append((a, path, name))


def blends(pipe):
    out = []
    for b in pipe.GetColorBlends():
        cb, ab = b.colorBlend, b.alphaBlend
        out.append({"enabled": bool(b.enabled), "write_mask": b.writeMask,
                    "color": [ename(cb.source), ename(cb.destination), ename(cb.operation)],
                    "alpha": [ename(ab.source), ename(ab.destination), ename(ab.operation)],
                    "logic": bool(b.logicOperationEnabled)})
    return out


def main():
    cap = rd.OpenCaptureFile()
    res = cap.OpenFile(A["capture"], "", None)
    if res != rd.ResultCode.Succeeded:
        OUT["error"] = "open: %s" % res
        done()
    res, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
    if res != rd.ResultCode.Succeeded:
        OUT["error"] = "replay: %s" % res
        done()
    OUT["api"] = ename(ctl.GetAPIProperties().pipelineType)
    sd = ctl.GetStructuredFile()
    res_names = {int(r.resourceId): r.name for r in ctl.GetResources()}
    buffers = ctl.GetBuffers()
    bufs = {int(b.resourceId): b for b in buffers}
    by_addr = sorted([(b.gpuAddress, b.length, int(b.resourceId)) for b in buffers if b.gpuAddress])
    texs = {int(t.resourceId): t for t in ctl.GetTextures()}
    draws = []
    walk(ctl.GetRootActions(), [], sd, draws)
    mkdir(D)
    shaders_dir, tex_dir = mkdir(os.path.join(D, "shaders")), mkdir(os.path.join(D, "textures"))
    rows = []
    state = A.get("list_state", "all")        # "all" | "none" | [first eid, last eid]: draws whose pipeline is read
    prog = os.path.join(D, "progress.txt")
    t_start = time.time()
    for k, (a, path, name) in enumerate(draws):
        row = {"eid": a.eventId, "name": name, "path": path, "indices": a.numIndices, "instances": a.numInstances,
               "index_offset": a.indexOffset, "base_vertex": a.baseVertex, "vertex_offset": a.vertexOffset,
               "instance_offset": a.instanceOffset, "indexed": bool(a.flags & rd.ActionFlags.Indexed)}
        if k % 25 == 0:
            with open(prog, "w") as f:
                f.write("%d / %d draws, %.1f s\n" % (k, len(draws), time.time() - t_start))
        if state == "none" or (isinstance(state, list) and not state[0] <= a.eventId <= state[1]):
            rows.append(row)
            continue
        try:
            ctl.SetFrameEvent(a.eventId, False)
            pipe = ctl.GetPipelineState()
            row["vs"] = rid(pipe.GetShader(rd.ShaderStage.Vertex))
            row["ps"] = rid(pipe.GetShader(rd.ShaderStage.Pixel))
            bl = blends(pipe)
            row["blend"] = bl[0] if bl else None
            row["targets"] = [rid(t.resource) for t in pipe.GetOutputTargets() if int(t.resource)]
            row["topology"] = ename(pipe.GetPrimitiveTopology())
            if A.get("list_stencil"):            # lane r3-aurastencil: who writes / tests which stencil bits
                ds = ctl.GetVulkanPipelineState().depthStencil
                ff = ds.frontFace
                row["stencil"] = [bool(ds.stencilTestEnable), ename(ff.function), ename(ff.passOperation),
                                  int(ff.reference), int(ff.compareMask), int(ff.writeMask)]
                row["depth"] = [bool(ds.depthTestEnable), bool(ds.depthWriteEnable), ename(ds.depthFunction)]
        except Exception:  # noqa: BLE001
            err("list %d" % a.eventId)
        rows.append(row)
    OUT["draws"] = len(rows)
    with open(os.path.join(D, "actions.json"), "w") as f:
        json.dump({"api": OUT["api"], "draws": rows}, f, indent=0)

    chosen = [int(e) for e in A.get("events") or []]
    if not chosen and A.get("auto"):
        chosen = [r["eid"] for r in rows if (r.get("blend") or {}).get("enabled")][:int(A.get("max_draws", 40))]
    OUT["chosen"] = chosen
    seen_shaders, seen_tex = set(), set()
    tex_max = int(A.get("tex_max", 1024))
    for eid in chosen:
        dd = mkdir(os.path.join(D, "draws", str(eid)))
        info = {"eid": eid}
        try:
            ctl.SetFrameEvent(eid, True)
            pipe = ctl.GetPipelineState()
            act = next(x for x, _p, _n in draws if x.eventId == eid)
            info.update(name=act.GetName(sd), indices=act.numIndices, instances=act.numInstances,
                        index_offset=act.indexOffset, base_vertex=act.baseVertex, vertex_offset=act.vertexOffset,
                        instance_offset=act.instanceOffset, indexed=bool(act.flags & rd.ActionFlags.Indexed),
                        topology=ename(pipe.GetPrimitiveTopology()), blend=blends(pipe))
            # vertex inputs and buffers
            info["inputs"] = [{"name": v.name, "buffer": v.vertexBuffer, "offset": v.byteOffset,
                               "per_instance": bool(v.perInstance), "rate": v.instanceRate, "format": fmt(v.format),
                               "used": bool(v.used)} for v in pipe.GetVertexInputs()]
            vbs = []
            for i, vb in enumerate(pipe.GetVBuffers()):
                o = {"slot": i, "id": rid(vb.resourceId), "offset": vb.byteOffset, "stride": vb.byteStride,
                     "size": vb.byteSize}
                if int(vb.resourceId):
                    n = min(int(vb.byteSize) if vb.byteSize and vb.byteSize < (1 << 62) else MAXB, MAXB)
                    data = ctl.GetBufferData(vb.resourceId, vb.byteOffset, n)
                    write(os.path.join(dd, "vb%d.bin" % i), data)
                    o["file"], o["bytes"] = "vb%d.bin" % i, len(data)
                vbs.append(o)
            info["vbuffers"] = vbs
            ib = pipe.GetIBuffer()
            if int(ib.resourceId):
                n = min(int(ib.byteSize) if ib.byteSize and ib.byteSize < (1 << 62) else MAXB, MAXB)
                data = ctl.GetBufferData(ib.resourceId, ib.byteOffset, n)
                write(os.path.join(dd, "ib.bin"), data)
                info["ibuffer"] = {"id": rid(ib.resourceId), "offset": ib.byteOffset, "stride": ib.byteStride,
                                   "size": ib.byteSize, "file": "ib.bin", "bytes": len(data)}
            # push constants and the buffers their 64-bit words point into
            vk = ctl.GetVulkanPipelineState()
            if vk is not None:
                pc = bytes(vk.pushconsts)
                write(os.path.join(dd, "push.bin"), pc)
                info["push"] = {"file": "push.bin", "bytes": len(pc)}
                ptrs = []
                for off in range(0, len(pc) - 7, 4):
                    va = struct.unpack_from("<Q", pc, off)[0]
                    if va < (1 << 16):
                        continue
                    for base, length, bid in by_addr:
                        if base <= va < base + length:
                            n = min(int(A.get("bda_bytes", 4096)), base + length - va)
                            data = ctl.GetBufferData(bufs[bid].resourceId,
                                                     va - base, n)
                            fn = "bda_%d.bin" % off
                            write(os.path.join(dd, fn), data)
                            ptrs.append({"push_offset": off, "address": va, "buffer": str(bid),
                                         "buffer_name": res_names.get(bid, ""), "offset": va - base,
                                         "file": fn, "bytes": len(data)})
                            break
                info["push_pointers"] = ptrs
                try:
                    info["vk"] = {"pipeline": rid(vk.graphics.pipelineResourceId),
                                  "depth": {"test": bool(vk.depthStencil.depthTestEnable),
                                            "write": bool(vk.depthStencil.depthWriteEnable),
                                            "func": ename(vk.depthStencil.depthFunction)},
                                  "cull": ename(vk.rasterizer.cullMode),
                                  "viewports": [[v.vp.x, v.vp.y, v.vp.width, v.vp.height, v.vp.minDepth,
                                                 v.vp.maxDepth] for v in vk.viewportScissor.viewportScissors]}
                except Exception:  # noqa: BLE001
                    err("vk state %d" % eid)
                try:                     # stencil (D2R's "Stencil VFX" pass and the effects that test it)
                    ds = vk.depthStencil

                    def face(f):
                        return {"func": ename(f.function), "pass": ename(f.passOperation),
                                "fail": ename(f.failOperation), "depth_fail": ename(f.depthFailOperation),
                                "ref": int(f.reference), "compare_mask": int(f.compareMask),
                                "write_mask": int(f.writeMask)}
                    info["vk"]["stencil"] = {"test": bool(ds.stencilTestEnable), "front": face(ds.frontFace),
                                             "back": face(ds.backFace)}
                    info["vk"]["depth"]["bounds"] = bool(getattr(ds, "depthBoundsEnable", False))
                except Exception:  # noqa: BLE001
                    err("vk stencil %d" % eid)
                try:                     # depth bias and clamp (the shadow passes' casters; lane r3-shadows)
                    ra = vk.rasterizer
                    info["vk"]["raster"] = {k: (bool(getattr(ra, a)) if k in ("bias", "depth_clamp", "front_ccw")
                                                else float(getattr(ra, a)))
                                            for k, a in (("bias", "depthBiasEnable"), ("bias_constant", "depthBias"),
                                                         ("bias_clamp", "depthBiasClamp"),
                                                         ("bias_slope", "slopeScaledDepthBias"),
                                                         ("depth_clamp", "depthClampEnable"), ("front_ccw", "frontCCW"))
                                            if hasattr(ra, a)}
                except Exception:  # noqa: BLE001
                    err("vk raster %d" % eid)
            # shaders, constant blocks, textures per stage
            stages = {}
            for st in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
                sid = pipe.GetShader(st)
                if not int(sid):
                    continue
                s = {"id": rid(sid), "entry": pipe.GetShaderEntryPoint(st), "cbuffers": [], "textures": []}
                refl = pipe.GetShaderReflection(st)
                if refl is not None and int(sid) not in seen_shaders:
                    seen_shaders.add(int(sid))
                    write(os.path.join(shaders_dir, "%d.spv" % int(sid)), refl.rawBytes)
                    try:
                        target = ctl.GetDisassemblyTargets(True)[0]
                        txt = ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(), refl, target)
                        with open(os.path.join(shaders_dir, "%d.txt" % int(sid)), "w") as f:
                            f.write(txt)
                    except Exception:  # noqa: BLE001
                        err("disasm %d" % int(sid))
                if refl is not None:
                    s["debug_name"] = res_names.get(int(sid), "")
                    s["cbuffer_decl"] = [{"name": c.name, "size": c.byteSize, "vars": len(c.variables)}
                                         for c in refl.constantBlocks]
                for k, ud in enumerate(pipe.GetConstantBlocks(st, True)):
                    d = ud.descriptor
                    c = {"index": ud.access.index, "array": ud.access.arrayElement, "id": rid(d.resource),
                         "offset": d.byteOffset, "size": d.byteSize}
                    if int(d.resource):
                        n = min(int(d.byteSize) or 65536, 65536)
                        data = ctl.GetBufferData(d.resource, d.byteOffset, n)
                        fn = "%s_cb%d.bin" % (ename(st)[0].lower(), k)
                        write(os.path.join(dd, fn), data)
                        c.update(file=fn, bytes=len(data), buffer_name=res_names.get(int(d.resource), ""))
                        try:
                            if refl is not None and ud.access.index < len(refl.constantBlocks):
                                vs = ctl.GetCBufferVariableContents(pipe.GetGraphicsPipelineObject(), sid, st,
                                                                   pipe.GetShaderEntryPoint(st), ud.access.index,
                                                                   d.resource, d.byteOffset, d.byteSize)
                                c["vars"] = [var_json(v) for v in vs]
                        except Exception:  # noqa: BLE001
                            err("cbvars %d" % eid)
                    s["cbuffers"].append(c)
                for ud in pipe.GetReadOnlyResources(st, True):
                    d = ud.descriptor
                    t = {"index": ud.access.index, "array": ud.access.arrayElement, "id": rid(d.resource),
                         "type": ename(d.type), "name": res_names.get(int(d.resource), "")}
                    td = texs.get(int(d.resource))
                    bd = bufs.get(int(d.resource))
                    if bd is not None:     # a buffer SRV (structured / typed): the particle streams live in one
                        off = int(d.byteOffset)
                        n = int(d.byteSize) if d.byteSize and d.byteSize < (1 << 62) else int(bd.length) - off
                        n = max(0, min(n, MAXB))
                        data = ctl.GetBufferData(d.resource, off, n)
                        fn = "%s_t%d_%d.bin" % (ename(st)[0].lower(), ud.access.index, ud.access.arrayElement)
                        write(os.path.join(dd, fn), data)
                        t.update(buffer=True, offset=off, size=int(d.byteSize), length=int(bd.length), file=fn,
                                 bytes=len(data), elem=getattr(d, "elementByteSize", 0), fmt=fmt(d.format))
                    if td is not None:
                        t.update(w=td.width, h=td.height, mips=td.mips, format=fmt(td.format), dim=td.dimension)
                        if int(d.resource) not in seen_tex and max(td.width, td.height) <= tex_max:
                            seen_tex.add(int(d.resource))
                            try:
                                ts = rd.TextureSave()
                                ts.resourceId = d.resource
                                ts.mip = 0
                                ts.destType = rd.FileType.PNG
                                ts.alpha = rd.AlphaMapping.Preserve
                                ctl.SaveTexture(ts, os.path.join(tex_dir, "%d.png" % int(d.resource)))
                                t["png"] = "textures/%d.png" % int(d.resource)
                            except Exception:  # noqa: BLE001
                                err("tex %d" % int(d.resource))
                    s["textures"].append(t)
                # storage buffers (dxil-spirv binds ByteAddressBuffer SRVs as non-writable SSBOs: RenderDoc lists them
                # with the read-write resources); the particle streams (`t4`) come this way
                s["buffers"] = []
                try:
                    for ud in pipe.GetReadWriteResources(st, True):
                        d = ud.descriptor
                        bd = bufs.get(int(d.resource))
                        o = {"index": ud.access.index, "array": ud.access.arrayElement, "id": rid(d.resource),
                             "type": ename(d.type), "name": res_names.get(int(d.resource), "")}
                        if bd is not None:
                            off = int(d.byteOffset)
                            n = int(d.byteSize) if d.byteSize and d.byteSize < (1 << 62) else int(bd.length) - off
                            n = max(0, min(n, MAXB))
                            data = ctl.GetBufferData(d.resource, off, n)
                            fn = "%s_u%d_%d.bin" % (ename(st)[0].lower(), ud.access.index, ud.access.arrayElement)
                            write(os.path.join(dd, fn), data)
                            o.update(offset=off, size=int(d.byteSize), length=int(bd.length), file=fn, bytes=len(data))
                        s["buffers"].append(o)
                except Exception:  # noqa: BLE001
                    err("rw %d" % eid)
                s["samplers"] = []
                try:
                    for ud in pipe.GetSamplers(st, True):
                        sm = ud.sampler
                        s["samplers"].append({"index": ud.access.index, "array": ud.access.arrayElement,
                                              "address": [ename(sm.addressU), ename(sm.addressV), ename(sm.addressW)],
                                              "filter": [ename(sm.filter.minify), ename(sm.filter.magnify),
                                                         ename(sm.filter.mip), ename(sm.filter.filter)],
                                              "aniso": sm.maxAnisotropy, "mip_bias": sm.mipBias,
                                              "lod": [sm.minLOD, sm.maxLOD], "compare": ename(sm.compareFunction)})
                except Exception:  # noqa: BLE001
                    err("samplers %d" % eid)
                stages[ename(st)] = s
            info["stages"] = stages
            # the colour target after this draw
            tg = [t for t in pipe.GetOutputTargets() if int(t.resource)]
            if tg and A.get("save_targets", True):
                ts = rd.TextureSave()
                ts.resourceId = tg[0].resource
                ts.mip = 0
                ts.destType = rd.FileType.PNG
                ts.alpha = rd.AlphaMapping.Discard
                ctl.SaveTexture(ts, os.path.join(dd, "target.png"))
                info["target"] = {"id": rid(tg[0].resource), "png": "target.png",
                                  "name": res_names.get(int(tg[0].resource), "")}
        except Exception:  # noqa: BLE001
            err("draw %d" % eid)
        with open(os.path.join(dd, "draw.json"), "w") as f:
            json.dump(info, f, indent=1)
    ctl.Shutdown()
    cap.Shutdown()
    OUT["ok"] = True


try:
    main()
except Exception:  # noqa: BLE001
    err("main")
done()
