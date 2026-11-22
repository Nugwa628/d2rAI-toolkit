"""Start a stage from the donor alone: the donor unit's own meshes, textures and unit JSON under the new unit's name.

  python -m hdkit.monster3d s0_base --monster <monster.json> <stage dir>

A new monster needs a first stage that the later scripts can change: s4_procbody.py and s4_hdbody.py replace its body,
s5_death.py adds its own death and dead clips, s5_clipfit.py fits its clips, s5_glow.py its self-lit layer. The worked
example got that first stage from an older mesh route; this script makes one from nothing but the donor, so the
hand-built route can start on any vanilla donor. No mesh is re-encoded: each LOD's file keeps the donor's vertex records,
only its maps point at the unit's own texture files.

Writes, like s5_build.py:
  <stage>/overlay/hd/character/enemy/<unit>.json          the donor's unit JSON (root and torso entities), renamed
  <stage>/overlay/<folder>/torso_lod0..4.model            the donor's models, maps renamed
  <stage>/overlay/<folder>/<name>_alb|nrm|orm|...texture  the donor's textures (what its material uses), at 2048 x 2048
  <stage>/overlay/<folder>/animation/combined.animations   the donor's clips, and <name>_state_machine.json with its
                                                          clip paths moved there
  <stage>/skinned_lod<k>.npz                              each LOD's largest mesh (pos, nrm, uv, tris, bone_idx, weights,
                                                          bones) in bind space
  <stage>/build.json                                      unit, donor, u, bounds, model paths, counts
Reads the donor from D2R's extracted files (common.D2R), never writes there.
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

from hdkit.formats import d2r_mesh, d2r_texture
from hdkit.monster3d import common
from hdkit.view import gr2

ATLAS = 2048            # the atlas size the later steps paint into
FLAT = {"alb": (128, 128, 128, 255), "nrm": (255, 128, 0, 128), "orm": (255, 209, 0, 255), "sss": (0, 0, 0, 255),
        "msk": (0, 0, 0, 255)}
KINDS = {"AlbedoTexture": "alb", "NormalTexture": "nrm", "ORMTexture": "orm", "ThicknessTexture": "sss", "MaskTexture": "msk"}


def donor_file(logical: str) -> Path:
    """A D2R logical path ("data/hd/...") in the extract (case-insensitive on the last part, as the game's are)."""
    rel = logical[5:] if logical.lower().startswith("data/") else logical
    p = common.D2R / rel
    if p.is_file():
        return p
    if p.parent.is_dir():
        for q in p.parent.iterdir():
            if q.name.lower() == p.name.lower():
                return q
    raise FileNotFoundError("%s is not in %s" % (logical, common.D2R))


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if not a:
        raise SystemExit(__doc__)
    m = common.need_monster()
    stage = Path(a[0])
    name, donor, folder, unit_name = common.NAME, m["donor"], common.FOLDER, common.UNIT
    donor_dir = common.donor_dir()
    out = stage / "overlay"
    if (out / folder).exists():
        raise SystemExit("%s exists: pick a new stage" % (out / folder))
    (out / folder).mkdir(parents=True)
    keep = set(m.get("keep") or ())
    report = {"unit": unit_name, "donor": donor, "from": "donor", "lods": [], "files": {}}
    textures = {}           # usage -> donor logical path
    bounds = None
    for k in range(5):
        src = common.D2R / donor_dir / ("torso_lod%d.model" % k)
        g, tree = d2r_mesh.load_model(src)
        for mesh in tree["Meshes"]:
            maps = mesh["MaterialBindings"][0]["Material"]["Maps"] if mesh["MaterialBindings"] else []
            ren = {}
            for mp in maps:
                tex = mp["Map"].get("Texture")
                if tex and mp["Usage"] in KINDS and mp["Usage"] not in keep:
                    textures.setdefault(mp["Usage"], tex["FromFileName"])
                    ren[mp["Usage"]] = "%s/%s_%s.texture" % ("data/" + folder, name, KINDS[mp["Usage"]])
            if ren:
                d2r_mesh.set_maps(tree, mesh["Name"], ren)
        dest = out / folder / ("torso_lod%d.model" % k)
        size = d2r_mesh.write_model(g, tree, dest)
        meshes, _sk = gr2.load_meshes(src.read_bytes())
        body = max(meshes, key=lambda me: len(me.pos))
        joints = body.joints if body.joints is not None else np.zeros((len(body.pos), 4), int)
        weights = body.weights if body.weights is not None else np.tile([1.0, 0, 0, 0], (len(body.pos), 1))
        np.savez(stage / ("skinned_lod%d.npz" % k), pos=body.pos, nrm=body.nrm, uv=body.uv, tris=body.tris,
                 bone_idx=joints, weights=weights, bones=np.array(body.bones))
        if k == 0:
            bounds = (body.pos.min(0).tolist(), body.pos.max(0).tolist())
        report["lods"].append({"lod": k, "vertices": int(len(body.pos)), "triangles": int(len(body.tris)),
                               "bones": len(body.bones), "bytes": size, "mesh": body.name})
        print("lod %d: %s, %d vertices, %d triangles" % (k, body.name, len(body.pos), len(body.tris)), flush=True)
    for usage, logical in textures.items():
        dest = out / folder / ("%s_%s.texture" % (name, KINDS[usage]))
        try:
            src = donor_file(logical)
            fmt, w, h, mips = d2r_texture.read_texture(src)
            if w == h == ATLAS or fmt not in d2r_texture.BCN:
                shutil.copyfile(src, dest)
            else:
                # the hand-built steps paint into a 2048 atlas (free-texel masks, rectangles and strips in 2048 texels):
                # the donor's map resampled to that size, the same UV layout
                from PIL import Image
                px = d2r_texture.decode(fmt, w, h, mips[0])
                px = np.asarray(Image.fromarray(px).resize((ATLAS, ATLAS), Image.LANCZOS))
                d2r_texture.write_bc3(dest, px, int(np.log2(ATLAS)) + 1, linear=fmt != d2r_texture.FMT_BC3)
            report["files"][dest.name] = {"from": logical, "px": [w, h], "bytes": dest.stat().st_size}
        except FileNotFoundError:
            # not in the extract: a flat map of that kind (the values s5_build.py writes), so the stage still loads
            px = np.zeros((ATLAS, ATLAS, 4), np.uint8)
            px[...] = FLAT[KINDS[usage]]
            d2r_texture.write_bc3(dest, px, int(np.log2(ATLAS)) + 1, linear=KINDS[usage] != "alb")
            report["files"][dest.name] = {"from": "flat (%s not in the extract)" % logical, "bytes": dest.stat().st_size}
            print("flat %s: %s is not in the extract" % (dest.name, logical), flush=True)

    unit = json.loads((common.D2R / (donor_dir + ".json")).read_text(encoding="utf-8"))
    model = "data/" + folder + "/torso.model"
    unit["name"] = unit_name
    unit["entities"] = [e for e in unit["entities"] if e["name"] in ("entity_root", "entity_torso")]
    root, torso = unit["entities"]
    for c in torso["components"]:
        if c["type"] == "ModelDefinitionComponent":
            c["filename"] = model
    base = next((c["scale"]["x"] for c in root["components"] if c["type"] == "TransformDefinitionComponent"), 1.0)
    dep = unit["dependencies"]
    dep["models"] = [{"path": model}] + [p for p in dep["models"] if "/%s/" % donor not in p["path"]]
    dep["textures"] = [t for t in dep["textures"] if "/%s/" % donor not in t["path"]] + [
        {"path": "data/%s/%s_%s.texture" % (folder, name, KINDS[u])} for u in textures]
    dep["json"] = [j for j in dep["json"] if "/items/" not in j["path"]]
    # the donor's clips and state machine, copied into the unit's folder with every clip path moved (as s5_death.py
    # writes them), so later steps change the unit's own copy and never the donor's
    (out / folder / "animation").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(common.D2R / donor_dir / "animation" / "combined.animations",
                    out / folder / "animation" / "combined.animations")
    sm = (common.D2R / donor_dir / ("%s_state_machine.json" % donor)).read_text(encoding="utf-8")
    sm = sm.replace("data/%s/animation/" % donor_dir, "data/%s/animation/" % folder)
    sm_path = "data/%s/%s_state_machine.json" % (folder, name)
    (out / folder / ("%s_state_machine.json" % name)).write_text(sm, encoding="utf-8")
    for e in unit["entities"]:
        for c in e["components"]:
            if "state_machine_filename" in c:
                c["state_machine_filename"] = sm_path
    dep["json"] = [j for j in dep["json"] if not j["path"].endswith("_state_machine.json")] + [{"path": sm_path}]
    report["state_machine"] = sm_path
    dest = out / common.UNIT_JSON
    dest.write_text(json.dumps(unit, separators=(",", ":")), encoding="utf-8")
    report.update(bounds=bounds, u=base, root_scale=base, torso_scale=1.0, model=model,
                  donor_model="data/" + donor_dir + "/torso.model", maps=sorted(textures))
    (stage / "build.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("unit", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
