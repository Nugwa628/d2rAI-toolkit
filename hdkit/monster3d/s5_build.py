"""The build step: skin the LOD set to a stock skeleton and write the HD unit (models, textures, unit JSON) as an overlay.

  python -m hdkit.monster3d.s5_build --monster <monster.json> <mesh_lods.npz> <albedo.png> <overlay dir> [--gain 1.25]
        [--part part.png] [--body-rough 0.82] [--cap-rough 0.82] [--body-gain 1.0] [--normal normal.png]
        [--weapon weapon.npz [--weapon-length 7.0] [--weapon-bone BONE]]
        [--strip 0.125 --corpse-albedo pale.png [--corpse-gain 1.0]] [--cap-scale 1.0]

What differs per monster comes from the monster config (hdkit.monster3d.common): the donor unit, the rig profile of
hdkit.sprite3d.rig_fit (capsule weights, segments carried to the donor's bind pose), the height K in game units, and:
- rigid_part [c, m] with part_bone: everything above the plane y = c + m z of the upright mesh (height 1) is one rigid part
  (a cap, a shell, a shield) and follows one bone alone, so the walk cannot tear it. part_bone is a spare child bone of the
  head that no body vertex uses (so a death clip can move the part on its own, s5_death.py); without part_bone the part
  is carried with the head. --part (a part mask in the atlas layout) and --body-rough give the body another roughness
  than the part in the ORM map (0.82 = the flat default; --cap-rough the part's), and --body-gain scales the body's
  albedo alone (after --gain), to set its level against classic without touching the part. --normal takes a tangent-space
  normal map in the albedo's UV layout instead of the flat one (the vertex tangents follow the UVs).
- keep: donor material maps left as they are (a shared all-black tint mask, say).
- --weapon: an optional rigid weapon, a separate piece of the mesh (told from the body by its uv: the bottom quarter of the
  atlas, v > 0.75) bound to one bone. The donor's own weapon attach bone (config `weapon_attach`, e.g. R_weapon_attach) is
  often not in the skeleton copy inside the torso model, so a mesh cannot be bound to it: the weapon is placed where that
  bone holds a weapon in the bind pose (along its own y axis, as D2R's own weapon models are) and bound to --weapon-bone
  (config `weapon_bone`, e.g. the right hand bone). weapon.npz holds `pos` (the piece in the upright frame) and `local`
  (the same vertices in the weapon's own frame, grip at 0, length 1).
--strip F with --corpse-albedo: room in the atlas for s5_death.py's corpse shell. Every UV's u is scaled by (1 - F) and every
map squeezed the same way (common.squash_u), and the top of the right strip (F x F of the atlas) takes a miniature of the
whole corpse albedo (in the old layout, times --corpse-gain), so a copy of the body can show other colours than the live body
with the same material. The normal map is flat there and the ORM the body's. s5_glow.py takes the same --strip.
--cap-scale S: the rigid part scaled by S about the centre of its base (x, z: its mean; y: its lowest point), after the
split, so it sits on the body as before (a generated part can come out wider on screen than classic's).
The final albedo (after --strip and the miniature) is also saved as albedo_final.png beside build.json, for previews.
The overlay is laid out like a mod's data folder:
  hd/character/enemy/<unit>.json                                       (common.UNIT_JSON)
  <folder>/torso_lod0..4.model, <name>_alb|nrm|orm|sss|msk.texture     (common.FOLDER; the maps the donor's material has)
The index files (hd/character/monsters.json, hd/model_lod_desc.bin) depend on the installed mod and are added when the
overlay is installed (hdkit.formats.model_lod_desc, hdkit.signoff prep).
Writes <overlay>/../build.json (counts, sizes, bounds).
"""
import json
import sys
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from hdkit.formats import d2r_mesh, d2r_texture, skel_info
from hdkit.monster3d import common
from hdkit.sprite3d import render_soft, rig_fit

D2R = common.D2R
KINDS = {"AlbedoTexture": "alb", "NormalTexture": "nrm", "ORMTexture": "orm", "ThicknessTexture": "sss", "MaskTexture": "msk"}


def split_cap(pos, nrm, uv, tris, c, m):
    """The rigid part (above y = c + m z) can come off in the death clip, so no triangle may join it to the body: a triangle with corners on both sides
    goes to the side most of its corners are on, and its other corners are copied for it (same place, normal and uv)."""
    cap = pos[:, 1] > c + m * pos[:, 2]
    side = cap[tris]
    on_cap = side.sum(1) >= 2
    mixed = side.any(1) & ~side.all(1)
    pos, nrm, uv, tris = list(pos), list(nrm), list(uv), tris.copy()
    copies = {}
    for f in np.nonzero(mixed)[0]:
        for k in range(3):
            v = tris[f, k]
            if cap[v] != on_cap[f]:
                key = (v, bool(on_cap[f]))
                if key not in copies:
                    copies[key] = len(pos)
                    pos.append(pos[v])
                    cap = np.append(cap, on_cap[f])
                    nrm.append(nrm[v])
                    uv.append(uv[v])
                tris[f, k] = copies[key]
    return np.array(pos), np.array(nrm), np.array(uv), tris, cap


FLAT_NRM = (255, 128, 0, 128)


def dxt5nm(src):
    """A tangent-space RGB normal map (+y = +v, v down the rows) as D2R's DXT5nm layout: (255, 255 - G, 0, R)."""
    out = np.zeros(src.shape[:2] + (4,), np.uint8)
    out[..., 0] = 255
    out[..., 1] = 255 - src[..., 1]     # y down the rows -> y up
    out[..., 3] = src[..., 0]           # x -> alpha
    return out


def write_torso(k, donor_dir, P, N, U, T, I, Wt, names, out, folder, stem, keep=()):
    """torso_lod<k>.model from the donor's: its body mesh replaced, every map pointed at the unit's own textures.
    Returns (bytes, the maps of the material, the usages written)."""
    gr2, tree = d2r_mesh.load_model(D2R / donor_dir / ("torso_lod%d.model" % k))
    # the body mesh has another name in the lower LODs: the one with the most vertices
    donor_mesh = max(tree["Meshes"], key=lambda m: len(m["PrimaryVertexData"]["Vertices"].obj or []))["Name"]
    d2r_mesh.replace_with_mesh(tree, donor_mesh, P, N, U, T, bone_idx=I, weights=Wt, bones=names)
    maps = next(m for m in tree["Meshes"] if m["Name"] == donor_mesh)["MaterialBindings"][0]["Material"]["Maps"]
    used = [mp["Usage"] for mp in maps if mp["Map"].get("Texture") and mp["Usage"] in KINDS and mp["Usage"] not in keep]
    d2r_mesh.set_maps(tree, donor_mesh, {u: "%s_%s.texture" % (stem, KINDS[u]) for u in used})
    size = d2r_mesh.write_model(gr2, tree, out / folder / ("torso_lod%d.model" % k))
    return size, maps, used


def monster_cfg() -> dict:
    """The build settings of the monster config: donor, profile, K, cap (the rigid part plane), cap_bone, keep."""
    m = common.need_monster()
    cfg = {"donor": m["donor"], "profile": m["profile"], "K": float(m["K"]), "keep": set(m.get("keep") or ())}
    if m.get("rigid_part"):
        cfg["cap"] = tuple(float(x) for x in m["rigid_part"])
        if m.get("part_bone"):
            cfg["cap_bone"] = m["part_bone"]
    return cfg


def main():
    a = sys.argv[1:]
    opt = lambda n, d: a[a.index(n) + 1] if n in a else d  # noqa: E731
    cfg = monster_cfg()
    m = common.MONSTER
    name, donor, K = common.NAME, cfg["donor"], cfg["K"]
    unit_name, folder = common.UNIT, common.FOLDER
    model, donor_dir = "data/" + folder + "/torso.model", "hd/character/enemy/" + donor
    lods, out = np.load(a[0]), Path(a[2])
    weapon_len = float(opt("--weapon-length", str(m.get("weapon_length", 7.0))))
    weapon_bone = opt("--weapon-bone", m.get("weapon_bone", "R_hand_bind_jnt"))
    weapon = np.load(opt("--weapon", "")) if "--weapon" in a else None
    gain = float(opt("--gain", "1.25"))
    profile = rig_fit.PROFILES[cfg["profile"]]
    bones = skel_info.bones(D2R / donor_dir / "skeleton" / (donor + ".skeleton"))
    if weapon is not None:
        attach = m.get("weapon_attach", "R_weapon_attach")
        world = np.array(next(b for b in bones if b["name"] == attach)["world"])
        rot = world[:3, :3] / np.linalg.norm(world[:3, :3], axis=1, keepdims=True)
        # weapon: upright-frame position -> its local frame (both are affine images of the generated piece)
        A, *_ = np.linalg.lstsq(np.hstack([weapon["pos"], np.ones((len(weapon["pos"]), 1))]), weapon["local"], rcond=None)
    report = {"unit": unit_name, "donor": donor, "u": profile["u"], "lods": [], "files": {}}
    if weapon is not None:
        report["weapon_bone"] = weapon_bone
    stem = "data/" + folder + "/" + name
    bounds = used = None
    strip = float(opt("--strip", "0")) if "--corpse-albedo" in a else 0.0
    for k in range(5):
        s = "" if k == 0 else "_%d" % k
        pos, nrm, uv, tris = lods["pos" + s], lods["nrm" + s], lods["uv" + s].copy(), lods["tris" + s]
        uv[:, 0] *= 1 - strip
        if "cap" in cfg and cfg.get("cap_bone"):
            pos, nrm, uv, tris, cap_mask = split_cap(pos, nrm, uv, tris, *cfg["cap"])
        else:
            cap_mask = None
        cap_scale = float(opt("--cap-scale", "1.0"))
        if cap_mask is not None and cap_scale != 1.0:
            base = np.array([pos[cap_mask, 0].mean(), pos[cap_mask, 1].min(), pos[cap_mask, 2].mean()])
            pos = pos.copy()
            pos[cap_mask] = base + cap_scale * (pos[cap_mask] - base)
            report["cap_scale"] = cap_scale
        is_weapon = (uv[:, 1] > 0.75) if weapon is not None else np.zeros(len(pos), bool)
        fa = is_weapon[tris]
        if (fa.any(1) & ~fa.all(1)).any():
            raise SystemExit("lod %d: a triangle joins the body and the weapon" % k)
        bi = np.nonzero(~is_weapon)[0]
        ai = np.nonzero(is_weapon)[0]
        remap = -np.ones(len(pos), int)
        remap[bi] = np.arange(len(bi))
        tb = remap[tris[~fa.all(1)]]
        rigid = None
        if "cap" in cfg:
            c, m = cfg["cap"]
            rigid = {"head": cap_mask[bi] if cap_mask is not None else pos[bi, 1] > c + m * pos[bi, 2]}
        bp, bn, bidx, bw, names, inst, cols = rig_fit.fit(profile, bones, pos[bi] * K, nrm[bi], tb, rigid=rigid)
        names = list(names)
        if rigid and cfg.get("cap_bone"):
            # the cap was carried with the head (same place in the bind pose); it is bound to its own bone instead
            names.append(cfg["cap_bone"])
            bidx = bidx.copy()
            bidx[rigid["head"], 0] = len(names) - 1
        P, N, I, Wt, U = bp, bn, bidx, bw, uv[bi]
        ta = np.zeros((0, 3), int)
        if weapon is not None:
            local = np.hstack([pos[ai], np.ones((len(ai), 1))]) @ A
            ap = (local * weapon_len) @ rot + world[3, :3]
            remap[ai] = len(bi) + np.arange(len(ai))
            ta = remap[tris[fa.all(1)]] - len(bi)
            names.append(weapon_bone)
            P = np.vstack([bp, ap])
            N = np.vstack([bn, render_soft.vertex_normals(ap, ta)])
            I = np.vstack([bidx, np.tile([len(names) - 1, 0, 0, 0], (len(ai), 1))])
            Wt = np.vstack([bw, np.tile([1.0, 0, 0, 0], (len(ai), 1))])
            U = np.vstack([uv[bi], uv[ai]])
        T = remap[tris]
        size, maps, used = write_torso(k, donor_dir, P, N, U, T, I, Wt, names, out, folder, stem, cfg.get("keep", ()))
        dest = out / folder / ("torso_lod%d.model" % k)
        # every LOD's skinned mesh, for s5_death.py (its preview and the corpse shell, which rewrites the models)
        np.savez(out.parent / ("skinned_lod%d.npz" % k), pos=P, nrm=N, uv=U, tris=T, bone_idx=I, weights=Wt, bones=np.array(names))
        if k == 0:
            bounds = (P.min(0).tolist(), P.max(0).tolist())
            report["maps"] = [(mp["Usage"], mp["Map"]["Texture"]["FromFileName"]) for mp in maps if mp["Map"].get("Texture")]
            if rigid:
                report["rigid_head_vertices"] = int(rigid["head"].sum())
        report["lods"].append({"lod": k, "vertices": len(P), "triangles": len(T), "weapon_triangles": int(len(ta)),
                               "bones": len(names), "bytes": size, "deflated": len(zlib.compress(dest.read_bytes(), 6))})
        print("lod %d: %d vertices, %d triangles, %d bones, %d bytes" % (k, len(P), len(T), len(names), size), flush=True)
    report["bounds"] = bounds

    # textures: the albedo (BC3 with mips) and flat maps; the ORM map takes
    # the body's roughness from --body-rough where --part says "not the part"
    alb = np.asarray(Image.open(a[1]).convert("RGB")).astype(np.float32) * gain
    body_gain = float(opt("--body-gain", "1.0"))
    if "--part" in a and body_gain != 1.0:
        part_px = np.asarray(Image.open(opt("--part", "")).convert("L").resize(alb.shape[1::-1], Image.BILINEAR))
        alb *= (body_gain + (1 - body_gain) * part_px.astype(np.float32) / 255)[..., None]
        report["body_gain"] = body_gain
    alb = np.dstack([np.clip(alb, 0, 255), np.full(alb.shape[:2], 255, np.float32)]).astype(np.uint8)
    if strip:
        alb = common.squash_u(alb, strip)
        pale = np.asarray(Image.open(opt("--corpse-albedo", "")).convert("RGB")).astype(np.float32) * float(opt("--corpse-gain", "1.0"))
        h, w = alb.shape[:2]
        mw, mh = w - int(round(w * (1 - strip))), int(round(h * strip))
        mini = np.asarray(Image.fromarray(np.clip(pale, 0, 255).astype(np.uint8)).resize((mw, mh), Image.LANCZOS))
        alb[:, w - mw:, :3] = mini.reshape(-1, 3).mean(0).astype(np.uint8)
        alb[:mh, w - mw:, :3] = mini
        alb[..., 3] = 255
        report.update(strip=strip, corpse_albedo=opt("--corpse-albedo", ""), corpse_gain=float(opt("--corpse-gain", "1.0")),
                      corpse_mini_px=[mw, mh])
    Image.fromarray(alb).save(out.parent / "albedo_final.png")
    flat = np.zeros((256, 256, 4), np.uint8)
    nrm, orm, sss, msk = flat.copy(), flat.copy(), flat.copy(), flat.copy()
    # D2R's normal maps are DXT5nm (format 61): red 255, green = y, blue 0, alpha = x, 128 = 0. Their y points UP the
    # texture (against the atlas rows) with tangents like d2r_mesh.tangents (checked on a vanilla donor: the stored tangents and
    # handedness agree with it, and the curl of (x, y) is smallest with y up). A flat map is (255, 128, 0, 128).
    nrm[...] = FLAT_NRM
    if "--normal" in a:     # a tangent-space normal map in the albedo's UV layout, RGB, +y = +v (v down)
        nrm = common.squash_u(dxt5nm(np.asarray(Image.open(opt("--normal", "")).convert("RGB"))), strip, FLAT_NRM)
        report["normal_map"] = opt("--normal", "")
        report["normal_encoding"] = "DXT5nm (255, 255-G, 0, R) of the RGB map"
    orm[...] = (255, 209, 0, 255)
    sss[..., 3] = msk[..., 3] = 255
    rough, cap_rough = float(opt("--body-rough", "0.82")), float(opt("--cap-rough", "0.82"))
    if "--part" in a and (rough != 0.82 or cap_rough != 0.82):
        part = common.squash_u(np.asarray(Image.open(opt("--part", "")).convert("L")), strip)
        part = np.asarray(Image.fromarray(part).resize((1024, 1024), Image.BILINEAR)).astype(np.float32) / 255
        orm = np.zeros((1024, 1024, 4), np.uint8)
        orm[...] = (255, 0, 0, 255)
        orm[..., 1] = np.round(255 * (cap_rough * part + rough * (1 - part)))
        report["cap_roughness"] = cap_rough
        report["body_roughness"] = rough
    pix = {"alb": (alb, False), "nrm": (nrm, True), "orm": (orm, True), "sss": (sss, True), "msk": (msk, True)}
    for usage in used:
        kind = KINDS[usage]
        px, linear = pix[kind]
        dest = out / folder / ("%s_%s.texture" % (name, kind))
        d2r_texture.write_bc3(dest, px, int(np.log2(px.shape[0])) + 1, linear=linear)
        report["files"][dest.name] = {"px": list(px.shape[:2]), "bytes": dest.stat().st_size,
                                      "deflated": len(zlib.compress(dest.read_bytes(), 6))}

    # unit: the donor's, root and torso only (the donor's weapon parts are gone: a weapon is in the torso mesh). The torso
    # is scaled so that root scale x torso scale = the profile's u.
    unit = json.loads((D2R / (donor_dir + ".json")).read_text(encoding="utf-8"))
    unit["name"] = unit_name
    unit["entities"] = [e for e in unit["entities"] if e["name"] in ("entity_root", "entity_torso")]
    root, torso = unit["entities"]
    for c in torso["components"]:
        if c["type"] == "ModelDefinitionComponent":
            c["filename"] = model
    base = next((c["scale"]["x"] for c in root["components"] if c["type"] == "TransformDefinitionComponent"), 1.0)
    u = profile["u"] / base
    torso["components"].append({"type": "TransformDefinitionComponent", "name": "entity_torso_TransformDefinition",
                                "position": {"x": 0.0, "y": 0.0, "z": 0.0}, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                                "scale": {"x": u, "y": u, "z": u}, "inheritOnlyPosition": False})
    dep = unit["dependencies"]
    dep["models"] = [{"path": model}] + [m for m in dep["models"] if "/%s/" % donor not in m["path"]]
    dep["textures"] = [t for t in dep["textures"] if "/%s/" % donor not in t["path"]] + [
        {"path": "%s_%s.texture" % (stem, KINDS[usage])} for usage in used]
    dep["json"] = [j for j in dep["json"] if "/items/" not in j["path"]]
    dest = out / "hd/character/enemy" / (unit_name + ".json")
    dest.write_text(json.dumps(unit, separators=(",", ":")), encoding="utf-8")
    for p in sorted((out / folder).glob("*.model")):
        report["files"][p.name] = {"bytes": p.stat().st_size, "deflated": len(zlib.compress(p.read_bytes(), 6))}
    report["model"], report["donor_model"] = model, "data/" + donor_dir + "/torso.model"
    report["root_scale"], report["torso_scale"] = base, u
    (out.parent / "build.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("unit", dest, "maps", report.get("maps"))


if __name__ == "__main__":
    main()
