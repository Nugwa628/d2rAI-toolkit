"""Read D2R's `SHAD` shader containers (`data/hd/shaders/*.bin`): keyword table, permutations, DXBC blobs.

Layout (little endian), found by reading D2R's 23 PopcornFX shaders (notes: docs/hdfx-shaders.md):

    chunk     u32 tag, u32 id, u32 count, i32 flags, u32 header size, u32 data offset, u32 total size (28 bytes);
              children (count of them) follow the header back to back unless the chunk has an offset table
    SHAD      id 'SHAD', count 3: TXT[, SYM[, SHMD
    TXT[      id 'TXT[', count n: n u32 offsets at +header size, NUL-terminated strings at +data offset
    SYM[      id 'SYM[', count n: n `SYM ` chunks: id = string index of the keyword, count = number of values,
              data = that many u32 string indices of its values ("0", "1", "2", "4", "8")
    SHMD      count 1: PRM[
    PRM[      count n (the permutation count = product of the keywords' value counts): n u32 offsets (from the
              chunk's data offset), each to a BTCD chunk
    BTCD      header size 0x24 (two more u32, zero), then a 0x38-byte descriptor of 14 u32: 3, 0, 0, 0,
              u64 texture slot mask (bit i = t<i>), u64 0 (UAVs?), u64 sampler mask (s<i>), u64 constant buffer mask
              (cb<i>), u32 DXBC size, u32 4; then the DXBC container (SM 5.0, chunks ISGN/OSGN/SHEX, RDEF stripped)

Permutation index = mixed radix over the keywords in table order, the first keyword the least significant digit
(checked over the resource masks of every container: toggling one keyword changes the same slots everywhere).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CHUNK = struct.Struct("<4sIIiIII")


@dataclass
class Symbol:
    name: str
    values: list[str]


@dataclass
class Permutation:
    index: int
    keywords: dict[str, str]
    textures: list[int]          # t slots
    samplers: list[int]          # s slots
    cbuffers: list[int]          # cb slots
    dxbc: bytes
    descriptor: tuple = ()       # the 14 u32 of the BTCD descriptor

    def signature(self, which: str = "ISGN") -> list[dict]:
        return dxbc_signature(self.dxbc, which)


def _bits(v: int) -> list[int]:
    return [i for i in range(64) if v >> i & 1]


def _chunk(b: bytes, o: int):
    tag, ident, count, flags, hdr, doff, size = CHUNK.unpack_from(b, o)
    return tag.decode("latin-1"), ident, count, flags, hdr, doff, size


@dataclass
class Shad:
    data: bytes
    strings: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)   # absolute file offset of each BTCD chunk

    @classmethod
    def read(cls, src) -> "Shad":
        b = Path(src).read_bytes() if isinstance(src, (str, Path)) else bytes(src)
        tag, ident, count, _f, hdr, _d, size = _chunk(b, 0)
        if tag != "SHAD" or b[4:8] != b"SHAD":
            raise ValueError("not a SHAD container")
        if size != len(b):
            raise ValueError(f"SHAD size {size} != file size {len(b)}")
        s = cls(b)
        o = hdr
        for _ in range(count):
            t, cid, n, _f, chdr, cdoff, csize = _chunk(b, o)
            if t == "TXT[":
                offs = struct.unpack_from(f"<{n}I", b, o + chdr)
                for x in offs:
                    p = o + cdoff + x
                    s.strings.append(b[p:b.index(b"\0", p)].decode("latin-1"))
            elif t == "SYM[":
                p = o + chdr
                for _ in range(n):
                    st, name, nv, _f2, shdr, sdoff, ssize = _chunk(b, p)
                    if st != "SYM ":
                        raise ValueError(f"expected SYM chunk at {p:#x}, got {st!r}")
                    vals = struct.unpack_from(f"<{nv}I", b, p + sdoff)
                    s.symbols.append(Symbol(s.strings[name], [s.strings[v] for v in vals]))
                    p += ssize
            elif t == "SHMD":
                pt, _pid, pn, _f2, phdr, pdoff, _ps = _chunk(b, o + chdr)
                if pt != "PRM[":
                    raise ValueError(f"expected PRM[ in SHMD, got {pt!r}")
                base = o + chdr
                s.offsets = [base + pdoff + x for x in struct.unpack_from(f"<{pn}I", b, base + phdr)]
            else:
                raise ValueError(f"unknown chunk {t!r} at {o:#x}")
            o += csize
        if len(s.offsets) != s.count:
            raise ValueError(f"{len(s.offsets)} permutations, keywords give {s.count}")
        return s

    @property
    def count(self) -> int:
        n = 1
        for sym in self.symbols:
            n *= len(sym.values)
        return n

    def keywords(self, index: int) -> dict[str, str]:
        if not 0 <= index < self.count:
            raise IndexError(index)
        out = {}
        for sym in self.symbols:
            index, d = divmod(index, len(sym.values))
            out[sym.name] = sym.values[d]
        return out

    def index(self, keywords: dict | None = None, strict: bool = False) -> int:
        """Permutation index of a keyword set; absent keywords take their first value ("0"). Keywords the container
        does not have are ignored unless `strict` (a renderer's switches are shared by the PS and VS containers).
        In a 0/1 keyword a non-zero value becomes "1" (POPCORN_LIGHTING 2 in the 0/1 billboard VS)."""
        kw = {k: str(int(v)) if isinstance(v, bool) else str(v) for k, v in (keywords or {}).items()}
        names = {s.name for s in self.symbols}
        extra = set(kw) - names
        if strict and extra:
            raise KeyError(f"keywords not in this container: {sorted(extra)}")
        idx, mul = 0, 1
        for sym in self.symbols:
            v = kw.get(sym.name, sym.values[0])
            if v not in sym.values and v != "0" and sym.values == ["0", "1"] and not strict:
                v = "1"
            if v not in sym.values:
                raise ValueError(f"{sym.name}={v} not in {sym.values}")
            idx += mul * sym.values.index(v)
            mul *= len(sym.values)
        return idx

    def permutation(self, index: int) -> Permutation:
        b, o = self.data, self.offsets[index]
        tag, _i, _n, _f, hdr, _d, size = _chunk(b, o)
        if tag != "BTCD":
            raise ValueError(f"permutation {index}: expected BTCD at {o:#x}, got {tag!r}")
        d = struct.unpack_from("<14I", b, o + hdr)
        n = d[12]
        dx = b[o + hdr + 0x38:o + hdr + 0x38 + n]
        if dx[:4] != b"DXBC" or struct.unpack_from("<I", dx, 0x18)[0] != n:
            raise ValueError(f"permutation {index}: bad DXBC blob")
        return Permutation(index, self.keywords(index), _bits(d[4] | d[5] << 32), _bits(d[8] | d[9] << 32),
                           _bits(d[10] | d[11] << 32), dx, d)


def dxbc_chunks(dx: bytes) -> dict[str, bytes]:
    n = struct.unpack_from("<I", dx, 0x1C)[0]
    out = {}
    for o in struct.unpack_from(f"<{n}I", dx, 0x20):
        tag, size = dx[o:o + 4].decode("latin-1"), struct.unpack_from("<I", dx, o + 4)[0]
        out[tag] = dx[o + 8:o + 8 + size]
    return out


def dxbc_signature(dx: bytes, which: str = "ISGN") -> list[dict]:
    """Input/output signature: semantic name and index, register, component mask (as letters), type."""
    c = dxbc_chunks(dx).get(which)
    if c is None:
        return []
    n = struct.unpack_from("<I", c, 0)[0]
    out = []
    for i in range(n):
        name_o, sidx, sv, ctype, reg, mask, rw = struct.unpack_from("<IIIIIBB", c, 8 + 24 * i)
        out.append({"semantic": c[name_o:c.index(b"\0", name_o)].decode(), "index": sidx, "register": reg,
                    "mask": "".join("xyzw"[k] for k in range(4) if mask >> k & 1), "sv": sv,
                    "type": {1: "uint", 2: "int", 3: "float"}.get(ctype, ctype)})
    return out


# ------------------------------------------------------------------ renderer properties -> keywords
# Read from D2R's own material code (build 93847, decrypted image): the Uber material reader fills a flag word and
# fields from the renderer properties, the keyword builder turns them into (keyword, value) pairs per container
# (docs/hdfx-shaders.md section 2). `get(name)` returns a property's value: bool for a feature switch, int/float for
# a value, str for a texture path, None when the property is absent (absent value = 0).

TYPE_TO_BLEND = {0: 1, 1: 0, 2: 3, 3: 2}   # PopcornFX Type (Additive, AdditiveNoAlpha, AlphaBlend, Premultiplied)


def blend_mode(get: Callable, kind: str = "billboard") -> int:
    """D2R's internal blend mode (material cb4[0].y): 0 additive without alpha, 1 additive (rgb * alpha),
    2 premultiplied, 3 alpha blend (both weighted-blended OIT), 4 mesh-only mode (Transparency.Type 4/5)."""
    if get("Transparency"):
        t = int(get("Transparency.Type") or 0)
        return TYPE_TO_BLEND.get(t, 4)
    if get("Transparent"):
        return TYPE_TO_BLEND.get(int(get("Transparent.Type") or 0), 1)
    return 1


def uv_mode(get: Callable) -> int:
    """UV_MODE and material cb4[0].x: 1 ribbon CorrectDeformation, 2 Atlas, else 0."""
    return 1 if get("CorrectDeformation") else 2 if get("Atlas") else 0


def uber_keywords(get: Callable, kind: str, lit: int = 2, skinning: int = 0, inputs: set | None = None,
                  smoke: bool = False) -> dict:
    """Keyword switches of a Diablo_Uber / _Mesh / _Ribbon renderer (all keywords of all Uber containers; each
    container picks its own). `kind`: billboard, ribbon or mesh. `lit`: POPCORN_LIGHTING value when FastLit or FullLit
    is on (1 or 2: chosen by the engine from a lighting resource, see notes). `inputs`: renderer particle inputs
    bound (AlphaRemap.AlphaCursor, GradientRemap.GradientCursor); None = assume bound. `smoke`: the material flag
    +0x88 (Diablo_Smoke*), which makes POPCORN_BLEND_MASK test AlphaMask.MaskOp instead of BlendMask.MaskOp."""
    def has(name):
        return inputs is None or name in inputs
    m = blend_mode(get, kind)
    blend = bool(get("Blend")) and (bool(get("Blend.TextureBlend")) or int(get("Blend.BlendOp") or 0) != 0)
    mask_op = "AlphaMask.MaskOp" if smoke else "BlendMask.MaskOp"
    bmask = bool(get("BlendMask")) and (bool(get("BlendMask.Mask")) or int(get(mask_op) or 0) != 0
                                        or bool(get("BlendMask.Invert")))
    soft = bool(get("SoftParticles")) and float(get("SoftParticles.SoftnessDistance") or 0) > 0
    kw = {
        "UV_MODE": uv_mode(get) if kind != "mesh" else 0,
        "UV_DISTORTION": bool(get("UVDistortion")),
        "ALPHA_REMAP": bool(get("AlphaRemap")) and has("AlphaRemap.AlphaCursor"),
        "GRADIENT_REMAP": bool(get("GradientRemap")) and has("GradientRemap.GradientCursor"),
        "SOFT_PARTICLES": soft,
        "FRESNEL": bool(get("Fresnel")),
        "POPCORN_LIGHTING": lit if (get("FastLit") or get("FullLit")) else 0,
        "COLOR_VARIANT": bool(get("ColorVariant")),
        "POPCORN_ADDITIVE": m in (0, 1),
        "POPCORN_MULTIPLICATIVE": bool(get("Multiplicative")),
        "POPCORN_BLEND": blend,
        "POPCORN_BLEND_MASK": bmask,
        "VERTEX_DISPLACEMENT": bool(get("VertexDisplacement")),
        "SKINNING_MODE": skinning,
        "VERTEX_ALPHA": False,   # set by the engine from the particle inputs (flag 0x4000), mesh only, no skinning
        "MESH": kind == "mesh",
    }
    return {k: str(int(v)) for k, v in kw.items()}


# ------------------------------------------------------------------ the other D2R materials (docs/hdfx-shaders.md 8-12)
# D2R picks the material class by the material file's name (0x140ec3b60: substring tests in this order; kind 5 renderers
# are lights/decals): Default_Billboard/Default_Ribbon -> PopcornFX default class, Diablo_Uber* and Diablo_Smoke* -> the
# Uber class (Smoke sets the Uber flag +0x88, cb4[0].z = 1), Diablo_Liquid* -> liquid, *Default_Mesh -> default mesh,
# Diablo_Standard* -> the engine's own model draw (no PopcornFX shader), Diablo_Stencil* -> stencil.
FAMILY_RULES = (("Default_Billboard", "default"), ("Default_Ribbon", "default"), ("Diablo_Uber", "uber"),
                ("Diablo_Smoke", "smoke"), ("Diablo_Liquid", "liquid"), ("Default_Mesh", "dmesh"),
                ("Diablo_Standard", "standard"), ("Diablo_Stencil", "stencil"))

# family -> (pixel container, vertex container) by renderer kind ("mesh" or not)
CONTAINERS = {
    ("uber", False): ("ps_popcorn_uber_billboard_p", "vs_popcorn_uber_billboard_v"),
    ("uber", True): ("ps_popcorn_uber_mesh_p", "vs_popcorn_uber_mesh_v"),
    ("liquid", False): ("ps_popcorn_liquid_billboard_p", "vs_popcorn_liquid_v"),
    ("liquid", True): ("ps_popcorn_liquid_mesh_p", "vs_popcorn_liquid_v"),
    ("dmesh", True): ("ps_popcorn_mesh_p", "vs_popcorn_mesh_v"),
    ("default", False): ("ps_popcorn_default_p", "vs_popcorn_default_v"),
    ("stencil", False): ("ps_popcorn_stencil_p", "vs_popcorn_stencil_v"),
    ("stencil", True): ("ps_popcorn_stencil_p", "vs_popcorn_stencil_v"),
}


def material_family(material: str, kind: str = "billboard") -> str | None:
    """D2R's material class of a renderer: uber, smoke (Uber with flag +0x88), liquid, dmesh, standard, stencil,
    default, light, decal; None when D2R has no class for it (Distortion_Billboard, Default_Sound: not drawn)."""
    if kind in ("light", "decal"):
        return kind
    for key, fam in FAMILY_RULES:
        if key in (material or ""):
            return fam
    return None


def containers(family: str, kind: str):
    """(PS, VS) container names of a family and renderer kind, or None (standard, light, decal: no PopcornFX shader)."""
    fam = "uber" if family == "smoke" else family
    return CONTAINERS.get((fam, kind == "mesh"))


def material_keywords(get: Callable, kind: str, material: str, inputs: set | None = None, lit: int = 2) -> dict:
    """Keyword switches of any D2R particle renderer (every keyword of the family's containers). Uber and Smoke use
    uber_keywords. Liquid: the liquid reader's flag word (0x14101fd90; keyword list 0x141021570). Default mesh:
    0x141027db0 / 0x141029090 (ALPHA_BLEND = blend mode != 4, POPCORN_LIGHTING = Lit). Default billboard and stencil:
    the standard PopcornFX meaning of their features (their keyword code was not read; marked so in the notes)."""
    def has(name):
        return inputs is None or name in inputs
    fam = material_family(material, kind)
    if fam in ("uber", "smoke"):
        return uber_keywords(get, kind, lit=lit, inputs=inputs, smoke=fam == "smoke")
    m = blend_mode(get, kind)
    soft = bool(get("SoftParticles")) and float(get("SoftParticles.SoftnessDistance") or 0) > 0
    if fam == "liquid":
        kw = {"UV_MODE": bool(get("CorrectDeformation")) and kind != "mesh",
              "UV_DISTORTION": bool(get("UVDistortion")),
              "GRADIENT_REMAP": bool(get("GradientRemap")) and has("GradientRemap.GradientCursor"),
              "ALPHA_REMAP": bool(get("AlphaRemap")) and has("AlphaRemap.AlphaCursor"),
              "LIQUID_COLOR_MAP": bool(get("LiquidColorMap")),
              "FRESNEL": bool(get("Fresnel")) and kind == "mesh",
              "VERTEX_DISPLACEMENT": bool(get("VertexDisplacement")) and kind == "mesh",
              "VERTEX_ALPHA": False, "MESH": kind == "mesh", "POPCORN_ADDITIVE": m in (0, 1)}
    elif fam == "dmesh":
        kw = {"FRESNEL": bool(get("Fresnel")), "POPCORN_LIGHTING": bool(get("Lit")), "ALPHA_BLEND": m != 4,
              "SOFT_PARTICLES": soft, "POPCORN_ADDITIVE": m in (0, 1), "COLOR_VARIANT": bool(get("ColorVariant")),
              "SKINNING_MODE": 0, "COLORS": has("Diffuse.Color"), "VERTEX_DISPLACEMENT": bool(get("VertexDisplacement")),
              "VERTEX_ALPHA": False}
    elif fam == "default":
        kw = {"DRAW_MODE": 1 if get("Diffuse.DiffuseMap") else 2, "UV_MODE": uv_mode(get),
              "ALPHA_REMAP": bool(get("AlphaRemap")), "POPCORN_LIGHTING": bool(get("LegacyLit") or get("Lit")),
              "COLORS": True, "SOFT_PARTICLES": soft, "POPCORN_ADDITIVE": m in (0, 1)}
    elif fam == "stencil":
        kw = {"ALPHA_REMAP": bool(get("AlphaThreshold")), "MESH": kind == "mesh", "SKINNING_MODE": 0}
    else:
        kw = {}
    return {k: str(int(v)) for k, v in kw.items()}
