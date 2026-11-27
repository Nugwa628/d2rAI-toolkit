"""What gets drawn: a unit JSON (or a bare .model) as model entities with their textures and self-lit layers, or a classic
sprite frame as a card.

Unit JSON (D2R `UnitDefinition`): the root entity gives the skeleton file, the state machine, `rotationOverride` and the
root scale (TransformDefinitionComponent); every other entity with a ModelDefinitionComponent is a part, unless the same
entity also has an ObjectEffectDefinitionComponent: then it is a self-lit layer drawn over that model (the Fire Golem
pattern: the skinned model drawn a second time, additive, through a mask in the model's UV layout coloured by a gradient
strip; docs/monster-sprite-to-3d.md). The layer's PopcornFX scripts are not run: of the textures the effect names, the last
one-channel (BC4) texture is taken as its mask and the last RGBA strip (at most 16 rows) as its gradient, and the choice is
reported. A part's `<name>.model` is looked up as `<name>_lod<N>.model` when the plain name does not exist.
Textures: per mesh and per material group (`Part.mesh_textures`, `Part.draw_groups`), as the game binds them for each
group's draw; `Part.textures` is the first mesh's set, the fallback for a map a group does not name.

Card: a classic frame (PNG) on an upright quad that faces the camera, placed as a sprite-card
build step places it: a sprite pixel (px, py) relative to the frame anchor sits at  X = px/16 + b/8, Y = (b - py)/9.798,
Z = -px/16 + b/8  (b = the frame's lowest opaque row), the geometry of the 30-degree camera. Copied relation, not code.
Its material is its own: the frame's colours as albedo, a flat normal, ORM (255, 255, 0, 0); no self-lit layer.
"""
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import gr2
from .vfs import normalise

from ..formats import d2r_texture

CLOCKWISE_8 = (4, 0, 5, 1, 6, 2, 7, 3)    # classic direction of facing key 0..7 (0 = the screen's bottom, clockwise)
MODE_CLIP = {"dt": "death", "nu": "neutral", "wl": "walk", "gh": "gethit", "a1": "attack1", "a2": "attack2",
             "bl": "block", "sc": "cast", "s1": "skill1", "s2": "skill2", "s3": "skill3", "s4": "skill4", "dd": "dead",
             "kb": "knockback", "sq": "sequence", "rn": "run"}
FMT_BC4 = 0x3F
FMT_RGBA8 = 0x1F


@dataclass
class Texture:
    logical: str
    fmt: int
    mips: list            # [(h, w, 4) uint8], largest first
    srgb: bool = False


@dataclass
class Layer:
    name: str
    effect: str
    mask: Texture
    strip: Texture
    picked: dict = field(default_factory=dict)


@dataclass
class Attachment:
    skeleton: gr2.Skeleton
    bone: int
    transform: np.ndarray


@dataclass
class Part:
    name: str
    model: str            # logical path that answered
    meshes: list          # [gr2.Mesh]
    skeleton: object      # gr2.Skeleton or None
    textures: dict        # usage -> Texture: the first mesh's (each usage from the first mesh that names it)
    layers: list = field(default_factory=list)
    mesh_textures: list = field(default_factory=list)   # per mesh, per material group (gr2.Mesh.groups): usage -> Texture
    unit_part: str = ""               # UnitPartComponent.part, used by SpawnObjectEffect
    track_only: bool = False         # posed for an explicitly selected object effect
    attachment: Attachment | None = None

    def draw_groups(self, i: int) -> list:
        """(tri_first, tri_count, textures, params) of each material group of mesh i, in the game's draw order: a mesh's
        groups can name other textures (Mephisto's body_meshShape: flesh body_2, bones, eyes_head_2). A mesh without
        groups (built in code) is one group with the part's textures."""
        m = self.meshes[i]
        gs = getattr(m, "groups", None) or [(0, len(m.tris), m.maps, getattr(m, "params", None) or {})]
        own = self.mesh_textures[i] if i < len(self.mesh_textures) else []
        return [(f, c, {**self.textures, **(own[g] if g < len(own) else {})}, prm)
                for g, (f, c, _maps, prm) in enumerate(gs)]


@dataclass
class Unit:
    name: str
    parts: list
    clips: dict = field(default_factory=dict)       # name -> gr2.Clip
    root_scale: float = 1.0
    rotation_override: tuple = (0.0, 0.0, 0.0, 1.0)
    notes: list = field(default_factory=list)
    kind: str = "model"
    card: dict | None = None                        # card: {"rgba": (h, w, 4), "quad": 4x3 corners, "uv": 4x2}
    effects: list = field(default_factory=list)     # VfxDefinitionComponent entities: {name, filename, transform 4x4}
    variant: object = None                          # unitvariant.Choice: the model's KTINT recolour and its effects'
    #                                               ColorVariantIndex (lane r3-unitmat); None: no table
    variant_table: object = None                    # unitvariant.VariantTable of the unit JSON, or None
    tracked_fx: list = field(default_factory=list)  # (part name, effect) object effects no mask/strip layer takes: drawn
    #                                               by their ModelInstanceTracking renderers (fx/modeltrack.py, r4-glow)


MIP_DROP = 0   # top mip levels not decoded (a whole-map render far below 1:1 needs none of them; mapview.hd.render sets it)


MAX_TEX_SIDE = None   # top mips larger than this are not decoded either (mapview.hd.render: 2048 below 1:1)


TEXTURE_CACHE_BYTES = 3 << 30
_TEXTURES = {}   # (vfs, path, srgb, MIP_DROP, MAX_TEX_SIDE) -> Texture: the tile models of an atlas share one decode (and one
#                 GPU upload, gpu.Renderer.texture keys on the array); mapview.hd.release() empties it


def read_texture(vfs, logical, srgb=False) -> Texture:
    key = (id(vfs), logical, srgb, MIP_DROP, MAX_TEX_SIDE)
    hit = _TEXTURES.get(key)
    if hit is not None and hit[0] is vfs:
        return hit[1]
    tex = _read_texture(vfs, logical, srgb)
    size = sum(m.nbytes for m in tex.mips)
    if sum(t.nbytes for _v, x in _TEXTURES.values() for t in x.mips) + size > TEXTURE_CACHE_BYTES:
        _TEXTURES.clear()          # long-lived processes (the MCP server): start over rather than grow
    _TEXTURES[key] = (vfs, tex)
    return tex


def _read_texture(vfs, logical, srgb=False) -> Texture:
    fmt, w, h, mips = d2r_texture.parse_texture(vfs.read(logical))
    drop = MIP_DROP
    while MAX_TEX_SIDE and max(w, h) >> drop > MAX_TEX_SIDE:
        drop += 1
    drop = min(drop, max(0, len(mips) - 1))
    arr = [d2r_texture.decode(fmt, max(1, w >> i), max(1, h >> i), m) for i, m in enumerate(mips) if i >= drop]
    return Texture(logical, fmt, arr, srgb)


def _model_path(vfs, logical, lod):
    if vfs.exists(logical):
        return logical
    stem = logical[:-len(".model")] if logical.endswith(".model") else logical
    alt = "%s_lod%d.model" % (stem, lod)
    if vfs.exists(alt):
        return alt
    raise FileNotFoundError(logical)


def _components(ent):
    return {c["type"]: c for c in ent.get("components", [])}


def transform_of(td) -> np.ndarray:
    """4x4 of a TransformDefinitionComponent (position, orientation quaternion x y z w, scale), identity when absent."""
    M = np.eye(4)
    if not td:
        return M
    p, q, s = td.get("position") or {}, td.get("orientation") or {}, td.get("scale") or {}
    R = gr2.qmat([float(q.get(k, d)) for k, d in (("x", 0), ("y", 0), ("z", 0), ("w", 1))])
    M[:3, :3] = R * np.array([float(s.get(k, 1.0)) for k in "xyz"])[None, :]
    M[:3, 3] = [float(p.get(k, 0.0)) for k in "xyz"]
    return M


def vfx_entities(doc) -> list:
    """The effects a unit or scene JSON places: one dict per entity with a VfxDefinitionComponent (name, the effect's
    logical path, its TransformDefinitionComponent as a 4x4, and the component's other settings)."""
    out = []
    for ent in doc.get("entities", []):
        c = _components(ent)
        v = c.get("VfxDefinitionComponent")
        if v is None:
            continue
        out.append({"name": ent.get("name", ""), "filename": v.get("filename", ""),
                    "transform": transform_of(c.get("TransformDefinitionComponent")),
                    "settings": {k: x for k, x in v.items() if k not in ("type", "name", "filename")}})
    return out


def effect_textures(data: bytes) -> list:
    """Texture paths an effect file names, in file order (strings in .particles are length-prefixed, so match lazily)."""
    return [m.group().decode("ascii", "replace") for m in re.finditer(rb"data/hd/[A-Za-z0-9_/.\-]+?\.texture", data)]


def pick_layer_textures(vfs, effect_logical):
    names = effect_textures(vfs.read(effect_logical))
    mask = strip = None
    for n in names:
        if not vfs.exists(n):
            continue
        fmt, w, h, _m = d2r_texture.parse_texture(vfs.read(n))
        if fmt == FMT_BC4:
            mask = n
        elif fmt == FMT_RGBA8 and h <= 16 and w >= 4 * h:
            strip = n
    return names, mask, strip


def load_model_part(vfs, logical, name="model", lod=0) -> Part:
    path = _model_path(vfs, logical, lod)
    meshes, sk = gr2.load_meshes(vfs.read(path))
    tex, per_mesh = {}, []
    for m in meshes:
        own = []
        for maps in [g[2] for g in (getattr(m, "groups", None) or [])] or [m.maps]:
            gt = {}
            for usage, tl in maps.items():
                if not vfs.exists(tl):
                    continue
                try:
                    gt[usage] = read_texture(vfs, tl, srgb=(usage == "AlbedoTexture"))   # cached: shared maps decode once
                except KeyError:    # a format d2r_texture does not decode (terrain maps use 112): drawn without that map
                    continue
                tex.setdefault(usage, gt[usage])
            own.append(gt)
        per_mesh.append(own)
    return Part(name, path, meshes, sk, tex, mesh_textures=per_mesh)


def load_unit(vfs, logical, lod=0, track_effect=None, *, equipment=False) -> Unit:
    if logical.lower().endswith(".model"):
        part = load_model_part(vfs, logical, "model", lod)
        return Unit(Path(logical).stem, [part])
    u = json.loads(vfs.read(logical).decode("utf-8"))
    return _load_definition(vfs, logical, u, lod, track_effect, equipment or bool(track_effect))


def _load_definition(vfs, logical, u, lod, track_effect, equipment=False):
    unit = Unit(u.get("name", Path(logical).stem), [])
    unit.effects = vfx_entities(u)
    item = normalise(logical).startswith("hd/items/")
    entities = [(ent, _components(ent)) for ent in u.get("entities", [])]
    root_fx = [c["ObjectEffectDefinitionComponent"]["filename"] for _, c in entities
               if item and "UnitRootComponent" in c and "ModelDefinitionComponent" not in c
               and "ObjectEffectDefinitionComponent" in c]
    skeleton_file, layers_for, fx_parts = None, {}, []
    for ent, c in entities:
        name = ent.get("name", "part")
        if "UnitRootComponent" in c:
            ro = c["UnitRootComponent"].get("rotationOverride") or {}
            unit.rotation_override = tuple(float(ro.get(k, d)) for k, d in (("x", 0), ("y", 0), ("z", 0), ("w", 1)))
            if "SkeletonDefinitionComponent" in c:
                skeleton_file = c["SkeletonDefinitionComponent"]["filename"]
            td = c.get("TransformDefinitionComponent")
            if td:
                s = td.get("scale", {})
                unit.root_scale = float(s.get("y", 1.0))
                if abs(float(s.get("x", 1)) - float(s.get("z", 1))) > 1e-6 or abs(float(s.get("x", 1)) - unit.root_scale) > 1e-6:
                    unit.notes.append("root scale not uniform; y used: %s" % s)
            if not item:
                continue
        md = c.get("ModelDefinitionComponent")
        if md is None:
            continue
        if not item and "ObjectEffectDefinitionComponent" in c:
            layers_for.setdefault(md["filename"].lower(), []).append((ent.get("name", ""), c["ObjectEffectDefinitionComponent"]["filename"]))
            fx_parts.append((ent, md, "UnitPartComponent" in c))
            continue
        try:
            part = load_model_part(vfs, md["filename"], name, lod)
        except FileNotFoundError:          # a unit may name a mode's model that does not ship (braziers: special1)
            unit.notes.append("%s: model %s not found; part skipped" % (ent.get("name", "part"), md["filename"]))
            continue
        part.model_key = md["filename"].lower()
        part.unit_part = c.get("UnitPartComponent", {}).get("part", "")
        unit.parts.append(part)
        if item:
            unit.tracked_fx.extend((name, effect) for effect in root_fx)
            if "ObjectEffectDefinitionComponent" in c:
                unit.tracked_fx.append((name, c["ObjectEffectDefinitionComponent"]["filename"]))
    if not unit.parts and fx_parts:
        # lane r3-shapes: a unit whose every model entity also carries an object effect (Wraith: torso, l_torso, r_torso;
        # colbarbwhirl) is drawn from those models (UnitPartComponent ones first): the effect is the glow over its own
        # body. Units with plain parts keep skipping them (Fire Golem / Pain Worm shells draw only through the effect).
        # entities named *shell* (Izual's ghost: topWingsShell, torsoShell) are the effect's shell meshes, not the body
        body = [x for x in fx_parts if "shell" not in str(x[0].get("name", "")).lower()] or fx_parts
        want = [x for x in body if x[2]] or body
        for ent, md, _p in want:
            try:
                part = load_model_part(vfs, md["filename"], ent.get("name", "part"), lod)
            except FileNotFoundError:
                unit.notes.append("%s: model %s not found; part skipped" % (ent.get("name", "part"), md["filename"]))
                continue
            part.model_key = md["filename"].lower()
            part.unit_part = _components(ent).get("UnitPartComponent", {}).get("part", "")
            unit.parts.append(part)
        unit.notes.append("every model entity has an object effect: %d drawn as the body" % len(unit.parts))
    if track_effect:
        track_effect = normalise(track_effect)
        for ent, md, _ in fx_parts:
            key = md["filename"].lower()
            if normalise(_components(ent)["ObjectEffectDefinitionComponent"]["filename"]) != track_effect:
                continue
            if any(p.model_key == key for p in unit.parts):
                continue
            try:
                part = load_model_part(vfs, md["filename"], ent.get("name", "part"), lod)
            except FileNotFoundError:
                continue
            part.model_key, part.track_only = key, True
            unit.parts.append(part)
    for part in unit.parts:
        for lname, eff in layers_for.get(getattr(part, "model_key", ""), []):
            if track_effect and normalise(eff) == track_effect:
                unit.tracked_fx.append((part.name, eff))
                continue
            if part.track_only:
                continue
            names, mask, strip = pick_layer_textures(vfs, eff)
            if mask is None or strip is None:
                unit.notes.append("layer %s: no mask/strip found among %d textures; skipped" % (lname, len(names)))
                if names:
                    unit.tracked_fx.append((part.name, eff))
                continue
            part.layers.append(Layer(lname, eff, read_texture(vfs, mask), read_texture(vfs, strip, srgb=True),
                                     {"mask": mask, "strip": strip, "named": len(names)}))
    # skeleton: the model's own when it has one (its inverse world transforms match its vertices), else the unit's;
    # a model skeleton that is a cut-down copy of the unit's file is re-parented on it (gr2.Skeleton.rebased: the game
    # poses on the file's hierarchy, e.g. Mephisto's cog that carries his hover height)
    full = None
    if skeleton_file and vfs.exists(skeleton_file):
        _g, t = gr2.load_tree(vfs.read(skeleton_file))
        full = gr2.Skeleton(t["Skeletons"][0])
    for part in unit.parts:
        if part.skeleton is None and full is not None:
            part.skeleton = full
            unit.notes.append("%s: skeleton from %s" % (part.name, skeleton_file))
        elif part.skeleton is not None and full is not None:
            rb = part.skeleton.rebased(full)
            if rb is not None:
                part.skeleton = rb
                unit.notes.append("%s: model skeleton re-parented on %s" % (part.name, skeleton_file))
    # clips: <model folder>/animation/combined.animations, as D2R lays a unit out
    for part in unit.parts:
        anim = str(Path(part.model).parent / "animation" / "combined.animations").replace("\\", "/")
        if vfs.exists(anim) and part.skeleton is not None:
            unit.clips = gr2.load_clips(vfs.read(anim), part.skeleton)
            break
    from . import unitvariant                       # the game's default pick: level<TransLvl 0> (lane r3-unitmat)
    unit.variant_table = unitvariant.load_table(vfs, u)
    if unit.variant_table is not None:
        unit.variant = unitvariant.choose(unit.variant_table)
    if equipment and not item:
        skeleton = full or next((p.skeleton for p in unit.parts if p.skeleton is not None), None)
        if skeleton is not None:
            _load_equipment(vfs, unit, entities, skeleton, lod, track_effect)
    return unit


def _load_equipment(vfs, unit, entities, skeleton, lod, track_effect):
    """Add rigid equipment with authored object effects. Pose it on the wearer's attachment bone."""
    from ..fx.context import _bone_index, _euler_xf

    for ent, components in entities:
        name = ent.get("name", "part")
        att = components.get("UnitAttachmentComponent", {})
        path, bone = att.get("filename", ""), att.get("overrideBoneName")
        if not normalise(path).startswith("hd/items/") or not bone or att.get("orientTowardBoneName"):
            continue
        if not vfs.exists(path):
            continue
        doc = json.loads(vfs.read(path).decode("utf-8"))
        if not any("ObjectEffectDefinitionComponent" in _components(ent) for ent in doc.get("entities", [])):
            continue
        try:
            bone_index = _bone_index(skeleton, bone)
        except KeyError as exc:
            unit.notes.append(f"{name}: {exc.args[0]}")
            continue
        equipment = _load_definition(vfs, path, doc, lod, track_effect)
        tr = att.get("attachmentTransform") or {}
        xf = _euler_xf(tr.get("translation"), tr.get("rotation"))
        xf[:3, :3] *= [float((tr.get("scale") or {}).get(axis, 1)) for axis in "xyz"]
        attachment = Attachment(skeleton, bone_index, xf)
        for part in equipment.parts:
            part.name = f"{name}:{part.name}"
            part.unit_part = att.get("part") or components.get("UnitPartComponent", {}).get("part", "")
            part.attachment = attachment
            unit.parts.append(part)
        unit.tracked_fx.extend((f"{name}:{part}", effect) for part, effect in equipment.tracked_fx)
        unit.notes.extend(f"{name}: {note}" for note in equipment.notes)


# ---- classic card ---------------------------------------------------------------------------------------------------------------

def card_corners(x0, y0, x1, y1, b):
    """World corners (top-left, top-right, bottom-right, bottom-left) of a card covering sprite pixels x0..x1, y0..y1
    (relative to the anchor; y down) whose lowest opaque row is b. See the module note."""
    def P(px, py):
        return [px / 16.0 + b / 8.0, (b - py) / (8.0 * math.sqrt(1.5)), -px / 16.0 + b / 8.0]
    return np.array([P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)], float)


def load_card(frames_dir, mode="nu", key=0, frame=0) -> Unit:
    """A classic frame as a card: frames_dir/<mode>/d<dir>_f<frame>.png with frames_dir/frames.json (box per mode,
    the PNG is that box). `key` is the facing key; the classic direction is CLOCKWISE_8[key]."""
    from PIL import Image
    fd = Path(frames_dir)
    meta = json.loads((fd / "frames.json").read_text(encoding="utf-8"))[mode]
    d = CLOCKWISE_8[int(key) % 8]
    f = int(frame) % int(meta["frames"])
    rgba = np.array(Image.open(fd / mode / ("d%d_f%02d.png" % (d, f))).convert("RGBA"))
    x0, y0 = meta["box"][0], meta["box"][1]
    h, w = rgba.shape[:2]
    rows = np.nonzero((rgba[..., 3] > 0).any(1))[0]
    b = (y0 + int(rows.max()) + 1) if len(rows) else (y0 + h)
    quad = card_corners(x0, y0, x0 + w, y0 + h, b)
    unit = Unit("card:%s/d%d_f%02d" % (mode, d, f), [], kind="card",
                card={"rgba": rgba, "quad": quad, "uv": np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float),
                      "dir": d, "frame": f, "mode": mode, "bottom": b})
    return unit
