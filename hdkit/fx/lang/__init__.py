"""hdfx-lang: a readable script language for D2R's particle scripts (`.hfx` -> PopcornFX CBVM). Spec:
docs/hdfx-lang.md; contract: docs/hdfx.md section 4.

  compile(src, effect=None, base=None, bindings=None) -> hd_cbvm.Script
  decompile(script, bindings=None) -> str
  check(script) -> list[str]            # decompile -> compile -> same behaviour? (empty list = yes)
"""
from __future__ import annotations

from .compiler import compile_source
from .decompiler import decompile_script, Unsupported
from .equiv import diff
from .isa import cb
from .syntax import LangError

__all__ = ["compile", "decompile", "check", "bindings_from_effect", "LangError", "Unsupported"]


def bindings_from_effect(effect, layer=None) -> dict:
    """Names for externals from a `graph.Effect` (duck typed; the format lane's dataclasses): attributes by name,
    and, when a field or sampler carries the external it binds to (`ext`/`stream`/`external`), those too."""
    out = {}
    if effect is None:
        return out
    for a in getattr(effect, "attributes", []) or []:
        n = getattr(a, "name", None)
        if n:
            out[n] = (f"__a_{n}", getattr(a, "type", None) or None)
    layers = getattr(effect, "layers", []) or []
    for ly in layers:
        if layer is not None and getattr(ly, "name", None) != layer:
            continue
        for f in getattr(ly, "fields", []) or []:
            ext = getattr(f, "ext", None) or getattr(f, "stream", None) or getattr(f, "external", None)
            if ext and getattr(f, "name", None):
                out[f"self.{f.name}"] = (str(ext), getattr(f, "type", None) or None)
    for sm in getattr(effect, "samplers", []) or []:
        ext = getattr(sm, "ext", None) or getattr(sm, "external", None)
        if ext and getattr(sm, "name", None):
            out[sm.name] = (str(ext), None)
    for k, (ext, t) in list(out.items()):
        if t is not None and t not in ("float", "float2", "float3", "float4", "int", "int2", "int3", "int4", "bool",
                                        "bool2", "bool3", "bool4", "orientation"):
            out[k] = (ext, None)
    return out


def compile(src: str, effect=None, base: cb.Script | None = None, bindings: dict | None = None) -> cb.Script:  # noqa: A001
    """`.hfx` source -> a hd_cbvm.Script (object number 0: the caller sets `.obj`). `base`: the script being replaced
    (its externals, kind and order are kept); `effect`/`bindings`: names for the effect's fields and samplers."""
    b = bindings_from_effect(effect)
    b.update(bindings or {})
    return compile_source(src, base, b)


def decompile(script: cb.Script, bindings: dict | None = None) -> str:
    """Readable `.hfx` source for a script (raises Unsupported for the few constructs it cannot express)."""
    return decompile_script(script, bindings)


def check(script: cb.Script) -> list[str]:
    """Round trip: decompile, compile, compare behaviour (equiv.diff); also that the result assembles byte for byte."""
    try:
        src = decompile(script)
    except LangError as e:
        return [f"decompile: {e}"]
    try:
        s2 = compile(src)
    except LangError as e:
        return [f"compile: {e}"]
    s2.obj = script.obj
    out = diff(script, s2)
    code = cb.encode(s2.code)
    if cb.encode(cb.decode(code)) != code:
        out.append("does not reassemble")
    return out
