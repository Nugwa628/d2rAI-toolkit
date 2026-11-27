"""Field layout of the 26 object classes of D2R's baked PopcornFX effects (`.particles`, PopcornFX 2.9.5 bakes).

Every object is a u16 field count, then (u16 field id, value) pairs; fields that hold their default are left out, and a
value carries no type tag. `SIZES` gives each class's field sizes: a positive number is a fixed-size value, a negative
one a u32 count followed by that many elements of |n| bytes. Ported from Reimagined D2R Mod Studio's `ParticleFile.cs`
(MIT, see ../THIRD_PARTY.md); it decodes every object of the 2,206 files of D2R's `hd/vfx` without a failure.

`KINDS` says what a field means for editing: which values are object references (1-based object numbers, renumbered when
objects are added or removed), string-table indices (remapped when strings are merged), localized strings (8-byte
elements: language code, string index) or plain data. The reference kinds were checked over the whole corpus: every
`obj`/`objs` value names an object of the class in `TARGET` (tests/test_hdfx_format.py, corpus check).
"""
from __future__ import annotations

_SIZES = {
    "CParticleEffect": "1:a4 3:4 10:1 11:4",
    "CParticleAttributeList": "0:a4 1:a4",
    "CParticleAttributeDeclaration": "0:4 1:4 2:a8 3:a8 4:4 5:1 6:1 7:1 14:16 21:16 25:4 32:16 44:16 50:16 51:1",
    "CParticleAttributeSamplerDeclaration": "0:4 1:4 3:a8 5:4 6:4",
    "CLayerGraphCompileCache": "0:4 2:a4 3:a4 4:a4 6:16",
    "CLayerGraphCompileCache_EntrySlot": "0:4 1:4",
    "CLayerGraphCompileCache_EventSlot": "0:4 1:4 2:a4",
    "CLayerGraphCompileCache_LayerSlot": "0:4 1:a4 2:a4",
    "CLayerCompileCache": "0:a16 1:a16 2:a4 3:a4 4:a4 5:a4 6:a4 7:a4 8:a4 9:a4 10:a4 11:4 12:4 13:1 14:1 15:1 "
                          "16:4 17:4 18:4 19:16 20:4 21:4 22:4 23:4 24:4 25:4 26:4 27:4 28:4 29:4 30:4 31:4",
    "CLayerCompileCacheField": "0:4 1:4 2:4 3:4 4:4 5:4",
    "CLayerCompileCacheSampler": "0:4 1:4 2:4 3:4 4:4",
    "CLayerCompileCacheAttrib": "0:4 1:a8 2:4 3:4 4:4 5:4 6:16 7:16 8:16 9:16 10:16",
    "CLayerCompileCacheAttribSampler": "0:4 1:a8 2:4 4:4 5:4",
    "CLayerCompileCacheEvent": "0:4 1:4 2:a4 3:4",
    "CLayerCompileCacheEventPayload": "0:4 1:4 2:4 3:4 4:4 5:4",
    "CLayerCompileCacheRenderer": "0:4 1:a4 2:a4 3:4 4:4",
    "CLayerCompileCacheRendererProperty": "0:4 1:4 2:16 3:4",
    "CLayerCompileCacheRendererParticleInput": "0:4 1:4 2:4 3:4",
    "CCompilerBlobCache": "0:4 1:4 2:a4 3:a4 4:a4 5:a4 6:a4",
    "CParticleNodePinIn": "0:4 1:4 2:1 5:4 7:16 8:16 9:4 10:4",
    "CParticleNodePinOut": "0:4 1:4 4:4 5:4",
    "CParticleNodeSamplerData_Curve": "0:a8 1:a8 3:4 7:8 9:4 10:4 11:4 12:16 13:16 14:4 16:1 17:a4 18:a4 19:a4",
    "CParticleNodeSamplerData_Shape": "0:a8 1:a8 3:4 6:a4 7:8 9:4 10:4 11:4 12:12 13:4 14:4 16:4 17:1 18:12 19:4 20:12 "
                                      "27:1 28:1 29:12 30:12",
    "CParticleNodeSamplerData_Turbulence": "0:a8 1:a8 3:4 7:8 9:4 10:4 11:4 19:4 20:4 21:4 22:4 23:4 24:4 25:4 "
                                           "26:4 27:4 28:4 29:4 30:1",
    "CParticleNodeSamplerData_EventStream": "0:a8 1:a8 3:4 7:8 9:4 10:4 11:a4",
    "CParticleNodeSamplerData_Text": "0:a8 1:a8 3:4 6:a4 7:8 9:4 10:4 11:4",
}
SIZES: dict[str, dict[int, int]] = {
    cls: {int(f): (-int(s[1:]) if s[0] == "a" else int(s)) for f, s in (x.split(":") for x in spec.split())}
    for cls, spec in _SIZES.items()}
CLASSES = list(SIZES)

# What a field holds: "obj"/"objs" object reference(s) (1-based), "str"/"strs" string index(es) (0xFFFFFFFF = none),
# "loc" localized strings (u32 language code, u32 string index), anything else is data (not touched by renumbering).
# Field names follow the mod studio where it names them, else what the corpus shows; "?" = meaning not known.
_KINDS = {
    "CParticleEffect": {1: ("graphs", "objs"), 3: ("attributes", "obj")},
    "CParticleAttributeList": {0: ("attributes", "objs"), 1: ("samplers", "objs")},
    "CParticleAttributeDeclaration": {0: ("name", "str"), 1: ("type", "u32"), 2: ("category", "loc"),
                                      3: ("description", "loc"), 14: ("default", "f4"), 21: ("default_int", "i4"),
                                      32: ("min", "f4"), 44: ("max", "f4"), 50: ("max_int", "i4")},
    "CParticleAttributeSamplerDeclaration": {0: ("name", "str"), 1: ("type", "u32"), 3: ("description", "loc"),
                                             5: ("node", "obj")},
    "CLayerGraphCompileCache": {0: ("platform", "str"), 2: ("entries", "objs"), 3: ("layers", "objs"),
                                4: ("events", "objs")},
    "CLayerGraphCompileCache_EntrySlot": {0: ("name", "str"), 1: ("layer_slot", "u32")},
    "CLayerGraphCompileCache_EventSlot": {0: ("name", "str"), 1: ("source", "u32"), 2: ("targets", "u32s")},
    "CLayerGraphCompileCache_LayerSlot": {0: ("layer", "obj"), 1: ("inputs", "u32s"), 2: ("outputs", "u32s")},
    "CLayerCompileCache": {0: ("consts", "a16"), 1: ("const_ranges", "a16"), 2: ("fields", "objs"),
                           3: ("attribs", "objs"), 4: ("attrib_samplers", "objs"), 5: ("events", "objs"),
                           7: ("samplers", "objs"), 8: ("renderers", "objs"), 9: ("scripts", "objs"),
                           11: ("spawn_event", "obj"), 20: ("name", "str")},
    "CLayerCompileCacheField": {0: ("id", "u32"), 1: ("type", "u32"), 2: ("size", "u32"), 3: ("flags", "u32"),
                                4: ("?", "u32"), 5: ("?", "u32")},
    "CLayerCompileCacheSampler": {0: ("name", "str"), 1: ("data", "obj"), 3: ("type", "u32"), 4: ("flags", "u32")},
    "CLayerCompileCacheAttrib": {0: ("name", "str"), 1: ("description", "loc"), 2: ("type", "u32")},
    "CLayerCompileCacheAttribSampler": {0: ("name", "str"), 1: ("description", "loc"), 2: ("node", "obj"),
                                        4: ("type", "u32")},
    "CLayerCompileCacheEvent": {0: ("name", "str"), 2: ("payloads", "objs")},
    "CLayerCompileCacheEventPayload": {0: ("name", "str"), 1: ("type", "u32"), 2: ("size", "u32")},
    "CLayerCompileCacheRenderer": {0: ("kind", "u32"), 1: ("inputs", "objs"), 2: ("props", "objs"), 3: ("hash", "u32"),
                                   4: ("material", "str")},
    "CLayerCompileCacheRendererProperty": {0: ("name", "str"), 1: ("type", "u32"), 2: ("value", "raw"),
                                           3: ("path", "str")},
    "CLayerCompileCacheRendererParticleInput": {0: ("input", "u32"), 1: ("field", "u32"), 2: ("name", "str"),
                                                3: ("type", "u32")},
    "CCompilerBlobCache": {0: ("backend", "str"), 1: ("kind", "u32"), 2: ("program", "u32s"),
                           4: ("ext_desc", "u32s"), 5: ("ext_names", "strs"), 6: ("calls", "strs")},
    "CParticleNodePinIn": {0: ("name", "str"), 1: ("type", "u32"), 5: ("type2", "u32"), 10: ("path", "str")},
    "CParticleNodePinOut": {0: ("name", "str"), 1: ("type", "u32"), 4: ("owner", "obj")},
    "CParticleNodeSamplerData_Curve": {0: ("name", "loc"), 1: ("description", "loc"), 17: ("times", "f32s"),
                                       18: ("values", "f32s"), 19: ("tangents", "f32s")},
    "CParticleNodeSamplerData_Shape": {0: ("name", "loc"), 1: ("description", "loc"), 6: ("pins", "objs"),
                                       19: ("mesh", "str")},
    "CParticleNodeSamplerData_Turbulence": {0: ("name", "loc"), 1: ("description", "loc")},
    "CParticleNodeSamplerData_EventStream": {0: ("name", "loc"), 1: ("description", "loc"), 11: ("data", "f32s")},
    "CParticleNodeSamplerData_Text": {0: ("name", "loc"), 1: ("description", "loc"), 6: ("pins", "objs")},
}
FIELDS: dict[str, dict[int, tuple[str, str]]] = _KINDS
REF_KINDS = ("obj", "objs")
STR_KINDS = ("str", "strs", "loc")
NONE = 0xFFFFFFFF

# Value types of fields, payloads, attributes and externals (the VM type table of hd_cbvm.TYPES, minus one).
# 36 (16 bytes) is past the VM table: the mesh renderer's orientation input, so taken for a quaternion.
_VM = ["void", "bool", "bool2", "bool3", "bool4", "ubyte", "ubyte2", "ubyte3", "ubyte4", "byte", "byte2", "byte3",
       "byte4", "uword", "uword2", "uword3", "uword4", "word", "word2", "word3", "word4", "uint", "uint2", "uint3",
       "uint4", "int", "int2", "int3", "int4", "u64", "i64", "float", "float2", "float3", "float4", "double", "quaternion"]
TYPES = dict(enumerate(_VM))
# A layer field (particle stream) with id N is the scripts' external named f"{N - STREAM_BASE:X}" (all 41,935 layers).
STREAM_BASE = 7


def type_name(t: int | None) -> str | None:
    return None if t is None else TYPES.get(t, f"type{t}")


# Classes an object reference may point at (checked by the corpus test).
TARGET = {
    ("CParticleEffect", 1): "CLayerGraphCompileCache",
    ("CParticleEffect", 3): "CParticleAttributeList",
    ("CParticleAttributeList", 0): "CParticleAttributeDeclaration",
    ("CParticleAttributeList", 1): "CParticleAttributeSamplerDeclaration",
    ("CLayerGraphCompileCache", 2): "CLayerGraphCompileCache_EntrySlot",
    ("CLayerGraphCompileCache", 3): "CLayerGraphCompileCache_LayerSlot",
    ("CLayerGraphCompileCache", 4): "CLayerGraphCompileCache_EventSlot",
    ("CLayerGraphCompileCache_LayerSlot", 0): "CLayerCompileCache",
    ("CLayerCompileCache", 2): "CLayerCompileCacheField",
    ("CLayerCompileCache", 3): "CLayerCompileCacheAttrib",
    ("CLayerCompileCache", 4): "CLayerCompileCacheAttribSampler",
    ("CLayerCompileCache", 5): "CLayerCompileCacheEvent",
    ("CLayerCompileCache", 7): "CLayerCompileCacheSampler",
    ("CLayerCompileCache", 8): "CLayerCompileCacheRenderer",
    ("CLayerCompileCache", 9): "CCompilerBlobCache",
    ("CLayerCompileCache", 11): "CLayerCompileCacheEvent",
    ("CLayerCompileCacheSampler", 1): "CParticleNodeSamplerData_",
    ("CLayerCompileCacheEvent", 2): "CLayerCompileCacheEventPayload",
    ("CLayerCompileCacheRenderer", 1): "CLayerCompileCacheRendererParticleInput",
    ("CLayerCompileCacheRenderer", 2): "CLayerCompileCacheRendererProperty",
    ("CParticleNodeSamplerData_Shape", 6): "CParticleNodePin",
    ("CParticleNodeSamplerData_Text", 6): "CParticleNodePin",
    ("CParticleNodePinOut", 4): "CParticleNodeSamplerData_",
    ("CParticleAttributeSamplerDeclaration", 5): "CParticleNodeSamplerData_",
    ("CLayerCompileCacheAttribSampler", 2): "CParticleNodeSamplerData_",
}


def kind(cls: str, fid: int) -> str:
    """The edit kind of a field ("obj", "objs", "str", "strs", "loc" or a data kind)."""
    return FIELDS.get(cls, {}).get(fid, ("?", "raw"))[1]


def name(cls: str, fid: int) -> str:
    n = FIELDS.get(cls, {}).get(fid, ("?", ""))[0]
    return str(fid) if n == "?" else n


def field_id(cls: str, fname: str) -> int:
    """A field id from its name (or its number as text)."""
    if fname.isdigit():
        return int(fname)
    for fid, (n, _k) in FIELDS.get(cls, {}).items():
        if n == fname:
            return fid
    raise KeyError(f"{cls} has no field {fname!r}")
