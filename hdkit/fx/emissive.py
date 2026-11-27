"""Decal Emissive (the decal renderer feature `Emissive`; lane r3-animuv).

D2R draws decals inside the forward shaders of what they land on (no decal shader of its own: ps_terrain's decal loop,
the ps_terrain.0.glsl disassembly, lines 186-470). The decal record's flag 128 switches
the emissive layer on; per pixel:

    e   = 10^(2 + b0 / 63.75) x EmissiveMap.r(uv) x max(N.V, 0.001)^(b1 x 0.25098) x (1 + N.V) / pi
    emissive += albedo x coverage x e            (albedo: the surface albedo after the decal's own albedo blend)

and the pixel's output is (lit + emissive) x cb10[0].x (the exposure), like PhysicalExposure particles.

The record bytes come from the kind-5 class (D2R image 0x14101d866..0x14101d92b, Intensity = particle input
`Emissive.Emissive.Intensity`, Focus = `Emissive.Emissive.Focus`, renderer +0x268 / +0x280):
    b0 = trunc(255 x clamp((log10(Intensity) - 2) x 0.25, 0, 1))
    b1 = trunc(255 x clamp(Focus x 0.125, 0, 1))
and flag 128 only when Intensity > 0 and the renderer has a Decal.EmissiveMap (texture index != 0xffff). So the
intensity covers 100 .. 10^6 in 256 log steps (the common Intensity 1 draws as 100) and the focus exponent is about
8 x Focus (0 .. 64).

Viewer (view/fx.wgsl fs_decal): N = the decal's projection axis (the ground the box lands on), V = the camera direction;
the surface albedo under the decal is not known there, so the decal's own albedo (DiffuseMap x Color) stands in for
the blended one (exact where the decal covers fully). Uniforms (render.FxRenderer.uniforms): xv.y the decoded
intensity, xv.z the focus exponent, xv.w 1 when on; the map is bound in texture slot 5 (render.SLOTS).
"""
from __future__ import annotations

import numpy as np

INTENSITY = "Emissive.Emissive.Intensity"
FOCUS = "Emissive.Emissive.Focus"


def encode(intensity, focus):
    """(b0, b1) record bytes of the game for an intensity and focus (arrays or scalars)."""
    i = np.asarray(intensity, float)
    lg = np.log10(np.maximum(i, 1e-30))
    b0 = np.trunc(255.0 * np.clip((lg - 2.0) * 0.25, 0.0, 1.0))
    b1 = np.trunc(255.0 * np.clip(np.asarray(focus, float) * 0.125, 0.0, 1.0))
    return b0, b1


def decode(b0, b1):
    """(intensity, focus exponent) the shader reads from the bytes."""
    return 10.0 ** (np.asarray(b0, float) * 0.0156862754 + 2.0), np.asarray(b1, float) * 0.2509804


def params(r, intensity, focus) -> np.ndarray:
    """(n, 3) per decal particle: decoded intensity, focus exponent, on (1/0). Off for every particle when the renderer's
    Emissive switch is off or it has no Decal.EmissiveMap; per particle when its Intensity <= 0."""
    i = np.asarray(intensity, float).reshape(-1)
    f = np.broadcast_to(np.asarray(focus, float).reshape(-1), i.shape)
    on = (i > 0) & bool(r.get("Emissive")) & bool(r.get("Decal.EmissiveMap"))
    I, ex = decode(*encode(i, f))
    return np.stack([I, ex, on.astype(float)], axis=1)
