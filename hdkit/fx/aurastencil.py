"""EnableAuraStencil: D2R's stencil test on Uber draws (lane r3-aurastencil, docs/hdfx-shaders.md section 12.1).

What the game does (read in the decrypted D2R image, build 93847; checked in RenderDoc, see UNIT_* below):
  - the Uber material reader 0x14102c2d0 reads the renderer feature `EnableAuraStencil` (getter 0x141008a50) into the
    material byte +0x95; nothing else reads the property;
  - the Uber pipeline builder 0x14102e500 starts the depth-stencil state as "stencil off" (0x07ffff00 / 0x07000000) and,
    when +0x95 is set, replaces it with 0x06003001 and clears the back-face compare: stencil test ON, compare mask 0x30,
    write mask 0, front compare GREATER_OR_EQUAL (Vulkan enum 6), fail/depth-fail/pass KEEP, back compare NEVER.
    The block's layout (enable, compare mask, write mask, front func, front fail/depth-fail/pass, back func, back ops)
    is the same as the `Diablo_Stencil` class's (0x141038c20: 01 00 04 07 = test on, write 0x04, ALWAYS, pass REPLACE,
    as RenderDoc shows that draw: Always/Replace, write mask 4, back Never) and the grass-visibility pass's.
So an aura-stencil draw changes nothing in the stencil; it is drawn only where (ref & 0x30) >= (stencil & 0x30).
The frame (RenderDoc, lane r3-aurastencil run a2, frame 9038: Thorns on the character, a Fallen in the ring, Blood Moor):
  - Depth Prepass / Models: every unit model writes the stencil (Always/Replace, ref 0x30): bodies, heads, armour and
    hair of the player and of monsters with write mask 0x10 (61 draws), held weapons and shields (the player's staff,
    the Fallen's scimitar, club and shields) with write mask 0x20 (9 draws); the Transparent pass's unit draws (hair)
    write 0x10 too. World models (bushes, grass, houses, trees) write nothing;
  - the aura draw (Additive / Billboards / Vfx `FX_thorns_overlay`, eid 17917): GreaterEqual, ref 0, compare mask 0x30,
    write mask 0, depth Greater without write, additive. With ref 0 it passes only where stencil & 0x30 == 0: the aura
    is hidden wherever a unit (either bit) is the surface, whether the aura lies in front of that unit or behind it;
  - corpses test GreaterEqual ref 0 mask 0x34 the same way (not an effect: noted only).

The viewer (render.FxOverlay): when a frame has aura-stencil draws, a depth24plus-stencil8 copy of the scene depth is
made (`copy`), the unit draws of the base scene (gpu.Draw.light_class "unit") mark it with UNIT_REF (`mark`), and the
aura-stencil draws are drawn against it with the game's test (`pipe`); every other draw keeps the scene's depth32float
target. Order between draws is kept (runs of consecutive draws share a pass). Difference: the game marks a unit pixel
when the unit passes the depth test at its prepass draw (a wall drawn later does not clear it); the viewer marks the unit
pixels that are the nearest surface of the finished scene.
"""
from __future__ import annotations

import os

DS_FORMAT = "depth24plus-stencil8"
# The game's state for EnableAuraStencil (0x14102e500): compare mask, write mask, front compare, back compare.
COMPARE_MASK = 0x30
WRITE_MASK = 0x00
FRONT = "greater-equal"
BACK = "never"
# From the RenderDoc capture (r3-aurastencil a2): the aura draws' reference, and what unit models write (bodies 0x10,
# held weapons 0x20; the viewer marks every unit draw 0x10: both bits hide an aura draw the same way).
AURA_REF = 0x00
UNIT_REF = 0x10
UNIT_WRITE_MASK = 0x30
UNIT_CLASSES = ("unit",)          # gpu.Draw.light_class of the base scene's draws that mark the stencil

FEATURE = "EnableAuraStencil"
ENV_OFF = "HDKIT_FX_AURA_STENCIL"      # "0": draw aura-stencil draws like any other (before/after pictures)

_COPY_WGSL = """
@group(0) @binding(0) var zt: %s;
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
  let p = vec2<f32>(f32((i << 1u) & 2u), f32(i & 2u));
  return vec4<f32>(p * 2.0 - 1.0, 0.0, 1.0);
}
struct O { @builtin(frag_depth) z: f32 };
@fragment fn fs(@builtin(position) q: vec4<f32>%s) -> O {
  var o: O;
  o.z = textureLoad(zt, vec2<i32>(q.xy), %s);
  return o;
}
"""

_MARK_WGSL = """
struct U { mvp: mat4x4<f32> };
@group(0) @binding(0) var<uniform> u: U;
@vertex fn vs(@location(0) pos: vec3<f32>) -> @builtin(position) vec4<f32> {
  return u.mvp * vec4<f32>(pos, 1.0);
}
"""


def enabled(r_or_get) -> bool:
    """Whether a renderer (rspec.RSpec or a property getter) has EnableAuraStencil switched on."""
    get = r_or_get if callable(r_or_get) else r_or_get.props.get
    v = get(FEATURE)
    if isinstance(v, (tuple, list)):
        v = v[0] if v else 0
    return bool(v)


def applies(material) -> bool:
    """Only D2R's Uber class reads the property (Diablo_Uber*, and Diablo_Smoke*, which is the Uber class too)."""
    if os.environ.get(ENV_OFF, "1") == "0":
        return False
    return bool(getattr(material, "aura_stencil", False)) and material.family in ("uber", "smoke")


def stencil_state(ref: int = AURA_REF) -> dict:
    """The aura draws' wgpu depth_stencil extras (the reference is set on the pass: set_stencil_reference)."""
    face = {"compare": FRONT, "fail_op": "keep", "depth_fail_op": "keep", "pass_op": "keep"}
    return {"format": DS_FORMAT, "stencil_front": face,
            "stencil_back": dict(face, compare=BACK), "stencil_read_mask": COMPARE_MASK, "stencil_write_mask": WRITE_MASK}


def passes(stencil_value: int, ref: int = AURA_REF) -> bool:
    """The game's test for one pixel (CPU form of the pipeline state, for tests and reports)."""
    return (ref & COMPARE_MASK) >= (stencil_value & COMPARE_MASK)


class AuraStencil:
    """GPU objects of the aura stencil for one FxRenderer (made on first use)."""

    def __init__(self, fxr):
        self.fxr = fxr
        g = fxr.gpu
        dev = g.device
        T = g.wgpu.ShaderStage
        ms = g.msaa > 1
        self.bgl = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.FRAGMENT, "texture": {"sample_type": "depth", "view_dimension": "2d",
                                                                 "multisampled": ms}}])
        code = _COPY_WGSL % (("texture_depth_multisampled_2d", ", @builtin(sample_index) s: u32", "i32(s)") if ms
                             else ("texture_depth_2d", "", "0"))
        cm = dev.create_shader_module(code=code)
        st_all = {"compare": "always", "fail_op": "keep", "depth_fail_op": "keep", "pass_op": "replace"}
        self.p_copy = dev.create_render_pipeline(
            layout=dev.create_pipeline_layout(bind_group_layouts=[self.bgl]),
            vertex={"module": cm, "entry_point": "vs"}, primitive={"topology": "triangle-list"},
            depth_stencil={"format": DS_FORMAT, "depth_write_enabled": True, "depth_compare": "always",
                           "stencil_front": dict(st_all, pass_op="keep"), "stencil_back": dict(st_all, pass_op="keep")},
            multisample={"count": g.msaa},
            fragment={"module": cm, "entry_point": "fs", "targets": []})
        from ..view import gpu as G
        mm = dev.create_shader_module(code=_MARK_WGSL)
        vb = [{"array_stride": G.FLOATS * 4, "step_mode": "vertex",
               "attributes": [{"format": g.wgpu.VertexFormat.float32x3, "offset": 0, "shader_location": 0}]}]
        self.p_mark = dev.create_render_pipeline(
            layout=dev.create_pipeline_layout(bind_group_layouts=[g.bgl]),
            vertex={"module": mm, "entry_point": "vs", "buffers": vb},
            primitive={"topology": "triangle-list", "cull_mode": "none"},
            depth_stencil={"format": DS_FORMAT, "depth_write_enabled": False, "depth_compare": "less-equal",
                           "stencil_front": st_all, "stencil_back": st_all,
                           "stencil_read_mask": 0xFF, "stencil_write_mask": UNIT_WRITE_MASK},
            multisample={"count": g.msaa})
        self._ds = {}

    def target(self, w, h):
        key = (w, h)
        if key not in self._ds:
            U = self.fxr.gpu.wgpu.TextureUsage
            if len(self._ds) > 2:
                self._ds.clear()
            self._ds[key] = self.fxr.gpu.device.create_texture(size=(w, h, 1), format=DS_FORMAT,
                                                               sample_count=self.fxr.gpu.msaa,
                                                               usage=U.RENDER_ATTACHMENT)
        return self._ds[key]

    def build(self, enc, dep, size, units) -> object:
        """The stencil target of this frame: the scene depth `dep` copied, stencil 0, then the `units` (gpu.Draw, their
        uniforms already written for this tile) mark UNIT_REF where they are the nearest surface."""
        w, h = size
        ds = self.target(w, h)
        dev = self.fxr.gpu.device
        bg = dev.create_bind_group(layout=self.bgl, entries=[{"binding": 0, "resource": dep.create_view(aspect="depth-only")}])
        rp = enc.begin_render_pass(color_attachments=[], depth_stencil_attachment={
            "view": ds.create_view(), "depth_load_op": "clear", "depth_store_op": "store", "depth_clear_value": 1.0,
            "stencil_load_op": "clear", "stencil_store_op": "store", "stencil_clear_value": 0})
        rp.set_pipeline(self.p_copy)
        rp.set_bind_group(0, bg)
        rp.draw(3)
        if units:
            rp.set_pipeline(self.p_mark)
            rp.set_stencil_reference(UNIT_REF)
            for dr in units:
                rp.set_bind_group(0, dr.bind)
                rp.set_vertex_buffer(0, dr.vbuf)
                rp.draw(dr.count)
        rp.end()
        return ds

    def pipe(self, d):
        """The aura draw's pipeline: FxRenderer._pipe's (entry, blend) with the game's stencil state."""
        m = d.material
        return self.fxr._pipe(d.entry, -1 if m.multiplicative else m.blend, (getattr(m, "cull", "none"),
                              getattr(self.fxr, "front", "ccw")), stencil=stencil_state())


def units_of(prep) -> list:
    """The base scene's draws that mark the stencil (units, not terrain or world scenery)."""
    if prep is None:
        return []
    return [dr for dr in prep.draws if not getattr(dr, "terrain", None) and getattr(dr, "light_class", "unit") in UNIT_CLASSES]


def runs(draws) -> list:
    """Consecutive draws grouped by whether they take the aura stencil: [(aura, [draws])], order kept."""
    out = []
    for d in draws:
        a = applies(d.material)
        if out and out[-1][0] == a:
            out[-1][1].append(d)
        else:
            out.append((a, [d]))
    return out


def summary(draws) -> dict:
    n = sum(1 for d in draws if applies(d.material))
    return {"aura_draws": n, "ref": AURA_REF, "compare_mask": COMPARE_MASK, "unit_ref": UNIT_REF,
            "hidden_over_units": not passes(UNIT_REF, AURA_REF)}

