"""Texture mip bias of D2R's PopcornFX materials (feature `EnableMipBiasing`, property `General.MipBias`, renderer input
`General.DrawResolution`): what the game puts in cb4[6].y and which of a material's samples it biases.

Read in D2R's decrypted image (lane r3-mipbias, docs/hdfx-shaders.md section 2.1):
  - every material class's property reader stores `General.MipBias` (float, 0 when absent) at material +0x1ec without
    looking at any feature switch (Uber 0x14102cf08, Default 0x141014335, Liquid 0x1410206a1, Default mesh 0x14102862d,
    Stencil 0x141038527). The string "EnableMipBiasing" is not in the binary: the switch is an editor one only;
  - the renderer's `General.DrawResolution` input must be a constant int field 0..2 (popcorn_impl.cpp logs "non-constant
    draw resolution" / "invalid draw resolution" otherwise) and lands at +0x1f0 (0x14100e130);
  - 0x14100fc70, called by every material's constant-buffer fill (cb4 +0x64 = cb4[6].y; cb4[6].z = the draw resolution):
        k    = clamp(DrawResolution - TransparencyQuality + 3, 0, 2)          (settings +0x8e98, "Transparency Quality")
        bias = MipBias - k - (0.5 if AntiAliasing >= 3 else 0)                  (settings +0x8e54: 3+ = SMAA T2x, temporal)
    k is the transparency down-scale step of the draw (0 full resolution); -k keeps the texture detail of a full-size draw;
  - the PS adds the global cb10[13].y (0 in every capture) and biases only some maps (MAPS below; the others sample at the
    hardware's own LOD); normal maps of liquid and default meshes always take cb10[13].y - 0.5.
Checked against every captured effect draw (calib r3, calib2 g4/g5/g7, calib3 g2, calib4 g3, calib5 s3, polish rd3: 50
draws, visual profile Transparency Quality 4, Anti Aliasing 3): DrawResolution 0 and 1 -> -0.5, 2 -> -1.5, as above.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DRAW_RESOLUTION = "General.DrawResolution"
MIP_BIAS = "General.MipBias"

# Which material maps the game biases, per material family (fx/shad.material_family; game texture registers in brackets):
# "material" = cb10[13].y + cb4[6].y, "normal" = cb10[13].y - 0.5, absent = not biased.
MAPS = {
    "uber": {"base": "material", "blend": "material", "blendmask": "material", "alphamask": "material",   # t10-t13
             "alpharemap": "material", "fresnel": "material"},                                            # t16, t19
    # not biased in the Uber PS: UVDistortion maps (t14, t15), GradientRemap (t17); lit: NormalMap t20 by cb10[13].y +
    # cb10[66].w (an engine value; 0 here)
    "liquid": {"base": "material", "mask": "material", "alpharemap": "material", "normal": "normal"},    # t10 t11 t18 t12
    # not biased: distortion t16, colour map t14, speckle t13
    "dmesh": {"diffuse": "material", "normal": "normal", "fresnel": "material"},                         # t10 t11 t14
    # not biased: RoughMetalMap t12
    "stencil": {"mask": "material", "alpharemap": "material"},                                           # t10, t11
    "default": {"diffuse": "material"},                                                                  # t10
}


@dataclass(frozen=True)
class Quality:
    """The game settings the bias depends on (D2R's Settings.json keys "Transparency Quality", "Anti Aliasing") and the
    global bias cb10[13].y. The default is the slots' "visual" profile (4, 3), the one every capture was made at; env
    HDFX_TRANSPARENCY_QUALITY / HDFX_ANTI_ALIASING override it (default_quality)."""
    transparency: int = 4
    anti_aliasing: int = 3
    global_bias: float = 0.0

    @classmethod
    def of(cls, v) -> "Quality":
        """A Quality from a Quality, None (default_quality), a dict with the Settings.json keys or short names, or a
        (transparency, anti_aliasing) pair."""
        if isinstance(v, cls):
            return v
        if v is None:
            return default_quality()
        if isinstance(v, dict):
            d = default_quality()
            return cls(int(v.get("Transparency Quality", v.get("transparency", d.transparency))),
                       int(v.get("Anti Aliasing", v.get("anti_aliasing", d.anti_aliasing))),
                       float(v.get("global_bias", d.global_bias)))
        t, a = v[:2]
        return cls(int(t), int(a))


def default_quality() -> Quality:
    def env(name, dflt):
        try:
            return int(os.environ.get(name, "") or dflt)
        except ValueError:
            return dflt
    return Quality(env("HDFX_TRANSPARENCY_QUALITY", 4), env("HDFX_ANTI_ALIASING", 3))


def downscale(draw_resolution: int, quality: Quality | None = None) -> int:
    """k: the draw's transparency down-scale step at these settings (0 full resolution, 1, 2)."""
    q = Quality.of(quality)
    return max(0, min(2, int(draw_resolution) - q.transparency + 3))


def material_bias(mip_bias: float, draw_resolution: int, quality: Quality | None = None) -> float:
    """cb4[6].y: General.MipBias - k - 0.5 with temporal anti-aliasing (0x14100fc70)."""
    q = Quality.of(quality)
    b = float(mip_bias) - downscale(draw_resolution, q)
    return b - 0.5 if q.anti_aliasing >= 3 else b


def sample_biases(mip_bias: float, draw_resolution: int, quality: Quality | None = None) -> tuple[float, float, float]:
    """What the PS adds to its samples: (material maps cb10[13].y + cb4[6].y, normal maps cb10[13].y - 0.5, the global
    cb10[13].y alone)."""
    q = Quality.of(quality)
    g = q.global_bias
    return g + material_bias(mip_bias, draw_resolution, q), g - 0.5, g


def draw_resolution(r, notes: list | None = None) -> int:
    """A renderer's General.DrawResolution: its constant input (rspec Input.const), 0 when absent. A stream-fed or out of
    range value is what the game logs as an error; the viewer then takes 0 and says so in `notes`."""
    inp = (getattr(r, "inputs", None) or {}).get(DRAW_RESOLUTION)
    if inp is None:
        return 0
    c = getattr(inp, "const", None)
    if c is None:
        if notes is not None:
            notes.append("General.DrawResolution fed by a stream (game: 'non-constant draw resolution'): 0 assumed")
        return 0
    v = int(round(float(c[0] if isinstance(c, (tuple, list)) else c)))
    if not 0 <= v <= 2:
        if notes is not None:
            notes.append(f"General.DrawResolution {v} out of 0..2 (game: 'invalid draw resolution'): 0 assumed")
        return 0
    return v
