# hdfx shaders: D2R's Uber particle materials, read from the game's own shaders

Lane shaders of the hdfx work (`docs/hdfx.md`). Ground truth for `view/fx.wgsl`: the containers in `data/hd/shaders/`
(SM 5.0 DXBC per permutation), disassembled, plus D2R's material code (property → keyword and constant buffer),
read in the decrypted D2R image (build 93847). Every formula below is from the disassembly, not guessed. Open
points are marked **open**.

Tools: `hdkit/fx/shad.py` (container reader, permutation index, `uber_keywords`). The disassembly (`.asm`/`.glsl` per
permutation, and the permutation survey) was made with vkd3d-shader 2.2 (`vkd3d-compiler`, LGPL-2.1) and SPIRV-Cross
(Apache-2.0), run as outside programs on the reader's output; neither is bundled, and the dumps (game code) are not in
this repository.

## 1. The SHAD container

Chunk = `u32 tag, u32 id, u32 count, i32 flags, u32 header size, u32 data offset, u32 total size` (28 bytes).
`SHAD` holds `TXT[` (string table), `SYM[` (keywords: one `SYM ` chunk per keyword, id = name string, data =
value strings), `SHMD` → `PRM[` (offset table, one `BTCD` per permutation). `BTCD` = 0x24 header + 14 u32
descriptor (`3,0,0,0`, u64 texture slot mask, u64 0, u64 sampler mask, u64 cbuffer mask, DXBC size, 4) + DXBC
(chunks ISGN/OSGN/SHEX; RDEF stripped, so no names: names come from section 2). Permutation index = mixed radix,
**first keyword = least significant digit** (verified on all 23 containers through the resource masks, and on
`UV_DISTORTION` = index 1 adding the distortion textures).

| Container | Keywords (in index order) | Permutations |
|-|-|-|
| `ps_popcorn_uber_mesh_p` | UV_DISTORTION, ALPHA_REMAP, GRADIENT_REMAP, FRESNEL, SOFT_PARTICLES, POPCORN_LIGHTING(0,1,2), COLOR_VARIANT, POPCORN_ADDITIVE, POPCORN_BLEND, POPCORN_BLEND_MASK | 1,536 |
| `vs_popcorn_uber_mesh_v` | UV_DISTORTION, ALPHA_REMAP, GRADIENT_REMAP, FRESNEL, SKINNING_MODE(0,1,4,8), VERTEX_DISPLACEMENT, VERTEX_ALPHA, COLOR_VARIANT, POPCORN_BLEND, POPCORN_BLEND_MASK | 2,048 |
| `ps_popcorn_uber_billboard_p` | UV_MODE(0,1,2), UV_DISTORTION, ALPHA_REMAP, GRADIENT_REMAP, SOFT_PARTICLES, POPCORN_LIGHTING(0,1,2), COLOR_VARIANT, POPCORN_ADDITIVE, POPCORN_MULTIPLICATIVE, POPCORN_BLEND, POPCORN_BLEND_MASK | 4,608 |
| `vs_popcorn_uber_billboard_v` | UV_DISTORTION, ALPHA_REMAP, GRADIENT_REMAP, POPCORN_LIGHTING(0,1), COLOR_VARIANT, UV_MODE(0,1,2), POPCORN_BLEND, POPCORN_BLEND_MASK | 384 |

Ribbons (`Diablo_Uber_Ribbon`) use the billboard containers (no ribbon container exists). Others: `*_uber_mesh_depth_v`,
`*_uber_shadows_*`, `*_uber_overdraw_p` (debug), `*_default_*` (PopcornFX `Default_Billboard`: DRAW_MODE, UV_MODE,
ALPHA_REMAP, POPCORN_LIGHTING, COLORS, SOFT_PARTICLES, POPCORN_ADDITIVE), `*_mesh_*` (`Diablo_Default_Mesh`/`Standard_Mesh`),
`*_liquid_*`, `*_stencil_*`.

## 2. Renderer properties → keywords and material constants (D2R code)

Read in the decrypted D2R image: Uber material reader `0x14102c2d0` (properties → material fields and a flag word),
blend-mode reader in `0x14100e130`, keyword builder `0x14102d770` (→ `(keyword id, value)` lists for the PS and VS
containers), material constant buffer fill `0x141032940`/`0x141033940`. Implemented in `shad.uber_keywords(get, kind)`.
A property that is present without a value reads as 0 (that is how the default of every int/float/bool is stored).

**Blend mode `m`** (also `cb4[0].y`): if the mesh feature `Transparency` is on, from `Transparency.Type`; else if the
billboard/ribbon feature `Transparent` is on, from `Transparent.Type`; else 1.

| Type (PopcornFX enum) | 0 Additive | 1 AdditiveNoAlpha | 2 AlphaBlend | 3 PremultipliedAlpha | 4, 5 (mesh only) |
|-|-|-|-|-|-|
| `m` | 1 | 0 | 3 | 2 | 4 |
| blend state | additive (ONE, ONE; `POPCORN_ADDITIVE=1`) | additive | weighted blended OIT, 2 targets | weighted blended OIT | `m=4`: normal-encode pass |

Corpus (2,206 effects, enabled renderers): billboards `Transparent.Type` 0: 8,735, 2: 4,933, 1: 26, 3: 11; meshes
`Transparency.Type` 0: 1,444, 2: 811, 4: 15, 3: 2. Most effects are additive.

**Keywords**

| Keyword | Value (D2R rule) |
|-|-|
| POPCORN_ADDITIVE | `m` ∈ {0, 1} |
| POPCORN_MULTIPLICATIVE | feature `Multiplicative` on (billboard PS only) |
| POPCORN_BLEND | `Blend` on and (`Blend.TextureBlend` set or `Blend.BlendOp` ≠ 0) |
| POPCORN_BLEND_MASK | `BlendMask` on and (`BlendMask.Mask` set or `BlendMask.MaskOp` ≠ 0 or `BlendMask.Invert`) (the material flag +0x88, set for `Diablo_Smoke*`, swaps `BlendMask.MaskOp` for `AlphaMask.MaskOp` here: section 8.1) |
| UV_DISTORTION, FRESNEL, COLOR_VARIANT, VERTEX_DISPLACEMENT | the feature switch of the same name |
| ALPHA_REMAP / GRADIENT_REMAP | `AlphaRemap` / `GradientRemap` on **and** the renderer input `AlphaRemap.AlphaCursor` / `GradientRemap.GradientCursor` is bound to a particle stream |
| SOFT_PARTICLES | `SoftParticles` on and `SoftParticles.SoftnessDistance` > 0 |
| POPCORN_LIGHTING | 0 unless `FastLit` or `FullLit`; then 2 when the engine's lighting resource exists, else 1 (the VS has 0/1 only: lit → 1). In play it is 2 (**open**: confirm in a capture) |
| UV_MODE (billboard/ribbon) | 1 if ribbon `CorrectDeformation`, else 2 if `Atlas`, else 0 |
| SKINNING_MODE (mesh VS) | from `Animation` (animated model instances); 0 for plain meshes |
| VERTEX_ALPHA (mesh VS) | engine flag 0x4000 (a particle input with flag 0x10), only when SKINNING_MODE = 0 |

**Material constant buffer `cb4`** (16-byte registers; same buffer in PS and VS; mesh layout, billboard differences
noted). Field = material struct offset in D2R.

| Register | Value |
|-|-|
| `cb4[0].x` | UV mode (same rule as UV_MODE) |
| `cb4[0].y` | blend mode `m` |
| `cb4[0].z` | material flag +0x88: 1 for `Diablo_Smoke*` materials (D2R sets it from the material name, section 8.1), else 0 |
| `cb4[0].w` | `Exposure.PhysicalExposure` (bool) |
| `cb4[1]` | `Blend.BlendOp`, `BlendMask.MaskOp`, `AlphaMask.MaskOp`, `AlphaMask.AlphaOnly` |
| `cb4[2].xy` | `BlendMask.Invert`, `AlphaMask.Invert`; `.zw` 0 |
| `cb4[3]` (mesh) | `Fresnel.BlendOp`, `Fresnel.ApplyToColor`, `Fresnel.ApplyToAlpha`, `Fresnel.DoubleSided` |
| `cb4[4]` (billboard) | `Atlas.Blending`, `AlphaMask.AtlasAlphaOnly`, `Atlas.SubDiv` (2 × int) |
| `cb4[5]` | `.x` 0.001 for some draw resolutions, else 0 (VS pushes vertices along the normal by it); `.y` 1 / `SoftnessDistance` (0 = off); `.z` `NormalWrap.WrapFactor`; `.w` 3 |
| `cb4[6]` | `.x` `AlphaRemap.Dissolve` (bool); `.y` material mip bias (section 2.1); `.z` `General.DrawResolution` (constant input 0..2) |
| `cb4[7]` | `.x` `ColorVariant.VariantContribution`; `.y` `Atlas.Flipbook` (billboard); `.zw` engine |
| `cb4[9..18]` (VS) | byte/element offsets of each particle stream in the instance buffer `t4` (section 3.2) |

Ops (`Blend.BlendOp`, `BlendMask.MaskOp`, `AlphaMask.MaskOp`, `Fresnel.BlendOp`), on two values `a`, `b`:
`0 → a*b`, `1 → a+b`, `2 → min(a, max(a-b, 0))` (subtract, clamped to [0, a]), `3 → a + a*b`. Fresnel's op 2 is plain
`a - b` (no clamp) and its op 3 is `a*b + a`. `*.Invert`: `x → 1 - x`. `*.Sample` (`BaseSample`, `BlendSample`, `Sample`):
picks one of two engine sampler states (1 in 97% of renderers; **open**: which is wrap, which clamp; assume 1 = wrap).

Samplers bias the material maps (not all: section 2.1) by `cb10[13].y + cb4[6].y` (global + material mip bias).

### 2.1 Mip bias (lane r3-mipbias, `hdkit/fx/mipbias.py`)

`EnableMipBiasing` is an editor switch only: the string is not in D2R's image. Every material reader stores
`General.MipBias` (0 when absent) at material +0x1ec unconditionally (Uber 0x14102cf08, Default 0x141014335, Liquid
0x1410206a1, Default mesh 0x14102862d, Stencil 0x141038527). The renderer input `General.DrawResolution` must be a constant
int field 0..2 (0x14100e130 logs "non-constant" / "invalid draw resolution" otherwise) and lands at +0x1f0. Every
material's cb4 fill calls 0x14100fc70 for `cb4[6].y`:

    k    = clamp(DrawResolution - TransparencyQuality + 3, 0, 2)      settings +0x8e98 ("Transparency Quality")
    y    = General.MipBias - k - (0.5 if AntiAliasing >= 3 else 0)      settings +0x8e54 (debug panel: None, FXAA,
                                                                        SMAA 1x, SMAA T2x, T2x Upsample 2x / 4x)

`k` is the draw's transparency down-scale step (0 full size). Checked on all 50 captured effect draws (calib r3, calib2
g4/g5/g7, calib3 g2, calib4 g3, calib5 s3, polish rd3; visual profile TQ 4, AA 3): resolution 0 and 1 give -0.5, 2 gives
-1.5 (Frost Nova's ChunkyCloud smoke, Fire Bolt's soft flame). Mephisto's skulls draw is -0.5 as well (the old
"0 for the skulls" reading of calib r3 was wrong: its cb4[6] is (0, -0.5, 0)). The test copies' "test" profile (TQ 0, AA 0)
gives -2 for every draw.

Which samples take it (the PS adds the global `cb10[13].y`, 0 in every capture):

| Family | `cb10[13].y + cb4[6].y` | `cb10[13].y - 0.5` | unbiased |
|-|-|-|-|
| Uber (billboard/ribbon/mesh) | t10-t13 base/blend/masks, t16 alpha remap/dissolve, t19 Fresnel gradient | - | t14/t15 UV distortion, t17 gradient remap; lit: t20 normal by `cb10[13].y + cb10[66].w` |
| Liquid | t10 base, t11 mask, t18 alpha remap/dissolve | t12 normal | t16 distortion, t14 colour map, t13 speckle |
| Default mesh | t10 diffuse, t14 Fresnel gradient | t11 normal | t12 RoughMetal |
| Stencil | t10 mask (t11 remap: `cb4[6].y` alone) | - | - |
| Default billboard | t10 diffuse | - | - |

Not modelled: the down-scaled transparency target itself (draws with k > 0 are drawn at full size in the viewer, only
their bias follows the game).

## 3. Uber mesh (`Diablo_Uber_Mesh`): Mephisto's ambient renderers

Mephisto (`vfx_mephisto_ambient.particles`) renderers 0,1,3-6 (cylinder skirt): PS **#1368** = FRESNEL,
SOFT_PARTICLES, POPCORN_ADDITIVE, POPCORN_BLEND, POPCORN_BLEND_MASK; VS **#1608** = FRESNEL, VERTEX_DISPLACEMENT,
POPCORN_BLEND, POPCORN_BLEND_MASK. Renderers 2, 7 (polar disc): UV_DISTORTION instead of FRESNEL/VERTEX_DISPLACEMENT
(PS #1361, VS #1537). `m` = 1 (Transparency on, Type 0 = Additive).

### 3.1 Vertex shader

Vertex inputs (the FX `.model` mesh): `v0.xyzw` position with a shared exponent: **`pos = v0.xyz * exp(13 * v0.w)`**,
`v1.xyz` normal, `v2.xyzw` tangent (w = sign), `v3.xy` uv; `SV_InstanceID` = particle index.

1. Vertex displacement (VERTEX_DISPLACEMENT): `uv' = uv * DispUV.xy + DispUV.zw` (stream
   `VertexDisplacement.DisplacementUVScaleAndOffset`), `d = DisplacementMap.r(uv', lod 0) * 2 - 1`,
   `pos += normal * d * DisplacementDistance` (stream). Then `pos += normal * cb4[5].x`.
2. World/view: instance matrix (stream, 4 float4 rows `M`) times the view matrix `V = cb3[5..8]`:
   `p_view = pos.x*(M0·V) + pos.y*(M1·V) + pos.z*(M2·V) + (M3·V)` (row vectors), `clip = p_view * P`, `P = cb3[13..16]`.
   Outputs `p_view.xyz` (TEXCOORD12) for the PS: view space, right-handed, −z forward.
3. Normal and tangent go through the same `M·V` rows (no inverse transpose; uniform scale assumed).
4. Colour: stream `Color.Color` float4, **rgb converted sRGB → linear** (exact sRGB curve), `alpha = Color.a * MonsterFade.Fade`.
5. Exposure scalar (TEXCOORD13): `Exposure.ExposureMultiplier * (PhysicalExposure ? PhysicalIntensity / π * cb10[0].x : 1)`.
6. Pass-through streams: Blend `BaseUVScaleAndOffset` (T1), `BlendUVScaleAndOffset` (T2), `BaseContribution`,
   `BlendContribution`, `BlendDistortIntensity`, `MaskDistortIntensity` (T3.xyzw), `BlendMaskUVScaleAndOffset` (T4),
   `AlphaMaskUVScaleAndOffset` (T5), `BlendDistortUVScaleAndOffset`, `MaskDistortUVScaleAndOffset` (T6, T7),
   `AlphaCursor`.xy + `GradientCursor`.xy (T8), `DissolveUVScaleAndOffset` (T9), `FresnelCursor` (T10).
   Which ones exist depends on the keywords (the TEXCOORD numbers shift; match by meaning, not number).

### 3.2 Per-instance data (what the render lane packs per particle)

All from the particle streams the renderer's inputs name (`CLayerCompileCacheRendererParticleInput`); in D2R a raw
buffer `t4` with one region per stream, located by `cb4[9..18]`. A WGSL port can use one instance struct:

| Input | Type | Used by |
|-|-|-|
| transform (`ModelInstanceTracking` / position+orientation+scale) | mat4 | always |
| `Color.Color` | float4 (sRGB rgb) | always |
| `MonsterFade.Fade` | float | always (alpha ×) |
| `Exposure.ExposureMultiplier`, `Exposure.PhysicalIntensity` | float | always (rgb ×) |
| `Blend.BaseUVScaleAndOffset`, `Blend.BlendUVScaleAndOffset`, `BlendMask.BlendMaskUVScaleAndOffset`, `AlphaMask.AlphaMaskUVScaleAndOffset` | float4 | always |
| `Blend.BaseContribution`, `Blend.BlendContribution` | float | always |
| `UVDistortion.BlendDistortUVScaleAndOffset`, `.MaskDistortUVScaleAndOffset` (float4), `.BlendDistortIntensity`, `.MaskDistortIntensity` (float) | | UV_DISTORTION |
| `AlphaRemap.AlphaCursor` (float2), `AlphaRemap.DissolveUVScaleAndOffset` (float4) | | ALPHA_REMAP |
| `GradientRemap.GradientCursor` | float2 | GRADIENT_REMAP |
| `Fresnel.FresnelCursor` | float | FRESNEL |
| `VertexDisplacement.DisplacementUVScaleAndOffset` (float4), `.DisplacementDistance` (float) | | VERTEX_DISPLACEMENT |

### 3.3 Pixel shader, in order

Textures: t10 `Blend.TextureBase` (s5), t11 `Blend.TextureBlend` (s6), t12 `BlendMask.Mask` (s7), t13 `AlphaMask.Mask`
(s8), t14 `UVDistortion.BlendDistort`, t15 `UVDistortion.MaskDistort` (s0), t16 `AlphaRemap.AlphaMap` / dissolve map
(s9), t17 `GradientRemap.GradientMap` (s10), t19 `Fresnel.Gradient` (s1), t38 scene depth (s3). Only the **red
channel** of the base, blend and mask textures is used (distortion maps: rg; Fresnel and gradient maps: rgba). `uv` = mesh uv.

1. **UV distortion** (UV_DISTORTION): `uvB = uv + (BlendDistort.rg(uv*BDuv.xy+BDuv.zw)*2-1) * BlendDistortIntensity`,
   `uvM = uv + (MaskDistort.rg(uv*MDuv.xy+MDuv.zw)*2-1) * MaskDistortIntensity`, both clamped to [0, 0.99]. Without
   it `uvB = uvM = uv`. Base/blend sample at `uvB`, masks and the dissolve map at `uvM`.
2. **Blend**: `a = Base.r(uvB*BaseUV.xy+BaseUV.zw) * BaseContribution`, `b = TextureBlend.r(uvB*BlendUV...) *
   BlendContribution`, `g = op(Blend.BlendOp, a, b)`. The colour is **grey**: `c.rgb = g`.
3. **Blend mask / alpha mask**: `bm = BlendMask.r(uvM*BMuv...)`, inverted if `BlendMask.Invert`; `am = AlphaMask.r(uvM*AMuv...)`,
   inverted if `AlphaMask.Invert`. If `cb4[0].z == 1`: `alpha = op(AlphaMask.MaskOp, bm, am)` (rgb stays `g`). Else:
   `c.rgb = op(BlendMask.MaskOp, g, bm)`, `alpha = AlphaOnly ? am : op(AlphaMask.MaskOp, c.r, am)`.
4. **Alpha remap** (ALPHA_REMAP): if `Dissolve`: `f = sat(Dissolve.r(uvM*DissUV...) - AlphaCursor.x)`, `c.r *= f`,
   `alpha = sat(alpha * f)`; else `alpha = AlphaMap.r(alpha + AlphaCursor.x, AlphaCursor.y)`.
5. **Fresnel** (FRESNEL): `n = normalize(normal)`, `v = normalize(p_view)`, `f = dot(-v, n)`; DoubleSided: `f = |f|`;
   `G = Fresnel.Gradient(1 - f, FresnelCursor)`, rgb sRGB → linear. `r = op(Fresnel.BlendOp, c, G)` per channel (rgba);
   `c.rgb = ApplyToColor ? r.rgb : c.rgb`, `c.a = ApplyToAlpha ? r.a : c.a`. Then `c = max(c, 0)`.
6. **Gradient remap** (GRADIENT_REMAP): `c.rgb = srgb_to_linear(GradientMap(c.r + GradientCursor.x, GradientCursor.y).rgb)`.
   (Without it the colour stays grey; the particle colour gives the hue.)
7. `c *= Color` (linear rgb, alpha incl. MonsterFade); `c.rgb *= exposure` (3.1 step 5).
8. **Soft particles** (SOFT_PARTICLES, `cb4[5].y > 0`): `zs = depth(t38 at fragcoord * cb3[0].zw) * cb10[53].x + cb10[53].z`
   (linear scene distance), `soft = sat((zs + p_view.z) * cb4[5].y)`; `alpha *= soft`.
9. `m == 4`: `discard` if `alpha < 0.001`.
10. **Output** (POPCORN_ADDITIVE=1, one target, blend ONE/ONE): `m == 0`: `rgb`; `m == 1`: `rgb * alpha`; alpha written 0.
    (POPCORN_ADDITIVE=0, two targets, weighted blended OIT, weight `w = max(0, 1e6 / (1e5 - p_view.z³))`:
    `m == 3` (AlphaBlend): `o0 = (rgb*alpha*w, alpha*w)`, `o1 = (1,1,1, alpha)`; `m == 2` (premultiplied):
    `o0 = (rgb*w, alpha*w)`, `o1 = (1,1,1, alpha)`. `m == 4`: `o0 = (octahedral(n)*0.5+0.5, 1, 1)`, a normal pass, not colour.)

For a viewer without OIT: additive = `src*1 + dst`; AlphaBlend ≈ `src.rgb*a + dst*(1-a)` sorted back to front (the
OIT resolve is `accum.rgb / accum.a` over `dst * reveal`; **open**: the exact resolve shader and RT1 blend state).

**Mephisto, step by step** (renderer 0): `g = smokey.r * BaseContribution + plasma.r * BlendContribution` (BlendOp 1 = add),
`c.rgb = g * gaussian.r` (BlendMask.MaskOp 0 = mul), `alpha = c.r * White` (AlphaMask mul), Fresnel `ApplyToAlpha` with
op 0: `alpha *= Gradient_02.a(1-|n·v|, cursor)`, colour × particle colour, × exposure, soft fade over 1.5 m,
additive `rgb * alpha`. Vertex displacement by gaussian noise along the normal.

### 3.4 Lighting (POPCORN_LIGHTING = 1/2)

Lit permutations add a normal map (t20, rg → z = sqrt(1 - x² - y²), tangent frame), a light grid (structured buffers
t4/t5, cb1 lights), image-based light (t34 cube, t37 BRDF LUT, 3D textures t45-t48), GGX-style specular. 2,041
billboard, 90 ribbon and 382 mesh renderers (of 13,705 / 1,548 / 2,272) are lit; Mephisto is not. Read in section 10.

## 4. Uber billboard and ribbon (`Diablo_Uber`, `Diablo_Uber_Ribbon`)

Same material code, same `cb4` (billboard extras: `cb4[4]` atlas, `cb4[7].y` `Atlas.Flipbook`), same ops. Permutations:
PS #3744 base additive, #3746 atlas, #3745 ribbon UV, #576
multiplicative, #432 colour variant, #3647 everything; VS #288, #352 atlas, #320 ribbon, #295, #359.

### 4.1 Vertex shader: vertices come pre-expanded

D2R runs PopcornFX's billboarder/ribboner on the CPU (or a compute pass): the VS gets one vertex per quad corner,
**already in world space**: `v0.xyz` position, `v1.xy` uv (atlas frame A), `v2.xy` uv frame B (atlas), `v3`, `v4`
ribbon UV-correction data, `SV_VertexID`. So the render lane's packer does the billboarding itself (camera-facing quad
per `General.BillboardingMode`, size, rotation; ribbons: strips along the particle chain) — PopcornFX-Web's billboarder
(MIT) has those modes. The VS then only does:

- particle index from the vertex id: `cb4[7].z == 0` (billboards): `vid/4` for the first `cb4[8].x` particles (quads),
  then `(vid - 4*cb4[8].x)/6 + cb4[8].x` (6-vertex geometry); `== 1` (ribbons): `min((vid+2)/4, cb4[7].w - 1)`; else `vid`.
- `p_view = v0 * V` (cb3[5..8]), `clip = p_view * P` (cb3[13..16]); `p_view.xyz` → TEXCOORD13 (soft particles, OIT weight).
- the same per-particle streams as the mesh (3.2), minus transform/Fresnel/displacement; `Color` sRGB → linear,
  alpha × `MonsterFade.Fade`, exposure scalar as 3.1 step 5.
- **UV_MODE 0**: `uv = v1.xy` (both sets). **UV_MODE 1** (ribbon `CorrectDeformation`): corner `c` = (0,0),(0,1),(1,0),(1,1)
  by `vid & 3`; `t = (c.x + c.y < 1) ? c / v4.xy : 1 - (1 - c) / v4.zw`; `uv = t * v3.xy + v3.zw` (v3 = uv rect,
  v4 = the ribboner's deformation factors; PopcornFX's standard correct-deformation). **UV_MODE 2** (atlas): `uv0 = v1.xy`
  (frame `floor(TexID)`), `uv1 = v2.xy` (next frame), blend `fract(Atlas.TextureID)`; with `AlphaMask.AtlasAlphaOnly`
  (`cb4[4].y`) the atlas applies to the alpha mask only and every other texture gets the cell-local uv
  `sat((uv - cellOrigin) * SubDiv)` (cell from `TexID` and `Atlas.SubDiv` = `cb4[4].zw`). Exactly (VS #352, lane r3-atlasalpha):
  cell `g = TexID mod N` (N = SubDiv.x x SubDiv.y, raw TexID, sign rule `s = TexID x N >= 0 ? N : -N`, `g = floor(fract(TexID
  / s) x s)`; no clamp, unlike the CPU billboarder's rect), origin `(g mod SubDiv.x, floor(g / SubDiv.y)) / SubDiv` (the row
  divides by SubDiv.y: the same for every AtlasAlphaOnly renderer, all 2 x 2 or 1 x 1), `TEXCOORD0 = (sat((v1 - o) x SubDiv),
  sat((v2 - o) x SubDiv))` (frame B with frame A's origin), `TEXCOORD1 = (v1, v2)` raw. The PS samples base, blend, blend
  mask and both distortion maps at TEXCOORD0, the alpha mask (t13) at TEXCOORD1. Without the flag both carry the raw uvs.
  UV distortion (PS #3647): base/blend `sat99(T0.xy + db x BlendDistortIntensity)`, blend mask `sat99(T0.xy + dm x
  MaskDistortIntensity)`, alpha mask `sat99(T1.xy + dm x MaskDistortIntensity)`; frame B (`.zw`) is not distorted, only
  clamped to [0, 0.99].

### 4.2 Pixel shader, in order

Same as the mesh PS (3.3) steps 1-4 and 6-10 with these differences:

- No Fresnel, no vertex displacement. `uv` = the VS uv (per UV_MODE).
- **Early discard**: `discard` if `Color.a < 1/1023`; after the masks, `discard` if `alpha * Color.a < 1/1023`.
- **Atlas** (UV_MODE 2): if `Atlas.Flipbook` (`cb4[7].y == 1`) the atlas uv is used as is, else `uv * UVScaleAndOffset`
  per texture as usual; if `Atlas.Blending == 1` (`cb4[4].x`) every texture is sampled for both frames and mixed
  `lerp(s(uv0), s(uv1), fract(TexID))`. (No motion-vector blending in the Uber PS; `Atlas.Blending` is 0/1 in the corpus.)
- **Colour variant** (COLOR_VARIANT): per-particle `ColorVariant.TransformIndex`; index ≤ 7 = none; else entry
  `k = index - 8` of an engine table (structured buffer t6, 32 bytes: float4 at +12 = (gamma, tint.rgb), float at +28 = w):
  `v = pow(c, gamma)`, `v = lerp(v, v * tint, w)`, `c.rgb = lerp(c.rgb, v, ColorVariant.VariantContribution)`
  (the table and what picks the index: section 14).
- **Multiplicative** (POPCORN_MULTIPLICATIVE, OIT path): as AlphaBlend/Premultiplied plus `o1.rgb = lerp(1, c.rgb, alpha)`,
  a multiply factor for what is behind. Viewer approximation: `dst *= lerp(1, c.rgb, alpha)`.
- Output as 3.3 step 10 (`m` 0/1 additive; 2/3 OIT); no `m == 4` path.
- Base-only permutations (POPCORN_BLEND=0): `g = Base.r * BaseContribution`; POPCORN_BLEND_MASK=0: no blend-mask
  texture, `alpha = AlphaOnly ? am : op(AlphaMask.MaskOp, g, am)`.

### 4.3 Standard PopcornFX meaning (cross-check)

The PopcornFX Library shaders (war3-pfx-boilerplate, read only) agree where the features overlap: AlphaRemap =
`AlphaMap(alpha, cursor).r`, soft particles = `sat((sceneDepth - fragDepth) / SoftnessDistance)`, atlas blending 1 =
`mix(frame0, frame1, fract(atlasID))`, Transparent.Type enum order. D2R's own additions: Blend/BlendMask/AlphaMask ops
(grey noise compositing), UV distortion, gradient remap with a cursor, MonsterFade, Exposure, ColorVariant, OIT.

## 5. Other materials

- **PopcornFX `Default_Billboard`** (263 renderers): `ps/vs_popcorn_default_*`. DRAW_MODE 1: `c = Diffuse.DiffuseMap(uv) *
  Color` (alpha = texture alpha, or its red channel when `m == 0`); DRAW_MODE 2: grey 0.4627 × Color (no texture);
  DRAW_MODE 0: flat grey (debug). Output as the Uber additive path. **open**: the DRAW_MODE/COLORS rules were not read in
  D2R's code (assume DRAW_MODE 1 when a diffuse map is set, COLORS = colour input bound).
- **Light** (`Diablo_Light`, 688) and **Decal** (`Diablo_Decal`, 111): no PopcornFX shader; they feed the engine's light
  grid and deferred decals (the `_pksi_vv*` hooks). Draw as viewer point lights / projected quads.
- **Diablo_Default_Mesh** (section 9), **Standard_Mesh** (11), **Liquid** (8), **Smoke** (8.1), **stencil** (12), lit
  Uber (10). **shadows** and **depth** containers: readable with the same tool, not annotated.

## 6. Which permutations are used

A survey over the 2,206 effects (enabled renderers):

| Material | Renderers | PS permutations used | Top PS permutations (count) |
|-|-|-|-|
| Diablo_Uber | 13,705 | 142 of 4,608 | #3744 additive+blend+mask (2,084), #3756 +gradient (1,823), #3768 +soft (852), #3468 gradient, OIT (811), #3780 (608), #3456 OIT (503) |
| Diablo_Uber_Mesh | 2,272 | 79 of 1,536 | #1375 all but lighting (277), #1240 Fresnel+soft+lit, OIT (247), #1351 (190), #1354 (113) |
| Diablo_Uber_Ribbon | 1,548 | 71 | #3744 (241), #3477 distortion+remaps, OIT (207), #3768 (118), #3757 ribbon UV+gradient (72) |

POPCORN_BLEND and POPCORN_BLEND_MASK are on in almost every renderer (the materials always name a blend texture and
mask), so the WGSL can treat them as always on with neutral inputs. Lit: 2,041 billboard, 90 ribbon, 382 mesh.

## 7. Open points, and what the render lane needs

Open: the OIT resolve and RT1 blend states (exact composite), the material flag `+0x88` (`cb4[0].z`), which `*.Sample`
value is wrap/clamp, POPCORN_LIGHTING 1 vs 2 in play, the colour-variant table, `cb10[53]` depth linearisation and
`cb10[0].x` exposure values (a capture answers all of these; `docs/hdfx.md` section 7).

For `view/fx.wgsl`: one Uber fragment function with the steps of 3.3/4.2 behind keyword `if`s (override constants);
blend modes: additive (`m` 0/1, ONE/ONE, rgb × alpha for `m` 1), alpha blend (`m` 3, `src*a + dst*(1-a)`, sorted),
premultiplied (`m` 2), multiplicative; per-instance inputs of 3.2; sRGB→linear on Color and on the Fresnel/gradient
maps (noise/mask textures are linear, red channel); billboards/ribbons expanded on the CPU (4.1).

## 8. The other materials: how D2R picks them (lane mats)

Read from the liquid, lit, mesh and stencil shaders, a corpus survey of every material's properties and the permutations
used by every PopcornFX material, and the decrypted image (strings, rip-relative references, annotated disassembly with
the property-name getters resolved through the name tables built at 0x141007a10 and 0x141728e00).

**Material class by name** (0x140ec3b60; renderer kinds 0-2 test the material path with a substring compare, in this
order; kind 5 is the light/decal class 0x14101b0b0):

| Name contains | Class (constructor, vtable) | Shaders | Implemented as (`shad.material_family`) |
|-|-|-|-|
| `Default_Billboard`, `Default_Ribbon` | 0x141013640 | `popcorn_default_*` | `default` |
| `Diablo_Uber` | Uber 0x14102b8e0 | `popcorn_uber_*` | `uber` |
| `Diablo_Smoke` | **the Uber class**, then flag +0x88 = 1 | `popcorn_uber_*` | `smoke` |
| `Diablo_Liquid` | 0x14101f590 | `popcorn_liquid_*` | `liquid` |
| `Default_Mesh` (`Diablo_Default_Mesh`) | 0x1410269a0 | `popcorn_mesh_*` | `dmesh` |
| `Diablo_Standard` | 0x141035510 | none: it emits an animated model instance (`ModelInstanceTracking`) to the engine's model renderer | `standard` |
| `Diablo_Stencil` | 0x141037ce0 | `popcorn_stencil_*` | `stencil` |
| anything else (`Distortion_Billboard`, `Default_Sound`, `Default_Decal`, `Default_Triangle`) | none | — | not drawn |

Vtable slots (same layout in every class): 6 the property reader (properties → a u16 flag word at +0x200 and fields),
4 a hash of the material state, the function after it the keyword/shader builder (keyword lists → containers), 5/8 the
constant-buffer fills. **Keyword ids** (the keyword table 0x140d90450, id = the count register + the offset): DRAW_MODE 0,
UV_MODE 1, ALPHA_REMAP 2, GRADIENT_REMAP 3, BLEND_MODE 4, UV_DISTORTION 5, MESH 6, POPCORN_ADDITIVE 7, POPCORN_MULTIPLICATIVE 8,
POPCORN_LIGHTING 9, POPCORN_BLEND 10, POPCORN_BLEND_MASK 11, COLORS 12, VERTEX_DISPLACEMENT 13, VERTEX_ALPHA 14, FRESNEL 15,
SOFT_PARTICLES 16, COLOR_VARIANT 18, ALPHA_THRESHOLD 30, ALPHA_BLEND 32, LIQUID 34, LIQUID_COLOR_MAP 36. Implemented in
`shad.material_keywords(get, kind, material, inputs)`; a permutation survey uses it for every material.

Corpus (permutation survey, enabled renderers of the 2,206 files): Liquid 804 billboards (PS #2 UV_DISTORTION 415,
#10 +ALPHA_REMAP 193, #0 183, #16 LIQUID_COLOR_MAP 7), Liquid_Mesh 24, Liquid_Ribbon 26; Default_Mesh 1,139 (#2 lit opaque
378, #14 lit alpha-blend soft 268, #21 Fresnel additive unlit 231, #6 lit alpha-blend 152); Smoke 156 + Smoke_Mesh 37;
Standard_Mesh 716; stencil 47.

### 8.1 Smoke (`Diablo_Smoke`, `Diablo_Smoke_Mesh`)

Not a material of its own: the Uber class with **flag +0x88 = 1** (`cb4[0].z`), which resolves the open point of section 2.
With it the PS takes the other mask path (3.3 step 3): `alpha = op(AlphaMask.MaskOp, blendMask, alphaMask)` and the colour
stays the blend result `g` (the blend mask does not touch the colour). Corpus Smoke uses `AlphaMask.Invert` + MaskOp 2 (32
of 52: `alpha = min(bm, max(bm - (1 - am), 0))`) and FastLit + NormalWrap 0.5 + NormalBend 0.5, soft particles 3 m, atlas 2x2:
a lit, soft, alpha-blended noise puff (section 10 for the lighting).

### 8.2 Liquid (`Diablo_Liquid`, `_Mesh`, `_Ribbon`)

**Reader** (0x14101fd90): textures `Liquid.LiquidBase`, `LiquidMask`, `SpeckleMap`, `NormalMap` (+0x238..0x250);
`Liquid.LiquidBaseInvert` (+0x259), `LiquidBaseOp` (+0x258), `Liquid.HeadlightContribution` (float2 at +0x284/0x288: the
corpus holds (1.0, 0.002), stored as float bits; the property reader shows them as ints), `LiquidColorMap` (flag 0x400,
map, `ColorMapInvert` +0x269, `ColorMapOp` +0x268), `UVDistortion` (flag 0x10), `GradientRemap` (flag 4), `AlphaRemap` (flag
0x200 when the cursor input is bound; `AlphaRemap.Dissolve` +0x89), `BackfaceCulling` (cull mode 2), `NormalWrap.WrapFactor`
(+0x8c → `cb4[5].z`), `CorrectDeformation` (flag 1). An atlas is refused ("Liquid doesn't currently support Atlas").
**Keywords** (0x141021570): VS `ALPHA_REMAP, UV_DISTORTION, GRADIENT_REMAP, LIQUID_COLOR_MAP, MESH, FRESNEL,
VERTEX_DISPLACEMENT, VERTEX_ALPHA, UV_MODE`; mesh PS `ALPHA_REMAP, UV_DISTORTION, GRADIENT_REMAP, LIQUID_COLOR_MAP, FRESNEL,
POPCORN_ADDITIVE`; billboard PS `UV_MODE` instead of FRESNEL. POPCORN_ADDITIVE = the blend state is additive.

**Per-particle inputs** (VS, `vs_popcorn_liquid_v`; **read from a capture by lane calib5**: Vulkan VS of calib5 s3
d2r_frame10340 eid 8331, the cave mana shrine's bubbles, SPIR-V through SPIRV-Cross;
each stream's values matched against the renderer's inputs, and the streams lie in the particle buffer in the
renderer's input order): T2 = scalars at cb4[10].z, [10].w, [11].x, [11].y = `Roughness`, `NormalIntensity`,
`SpeckleIntensity`, `CubemapIntensity`; T3 = cb4[11].z, [11].w, [12].x, [12].y = `AlphaThreshold`, `LiquidHardness`,
**`EdgeControl`, `EdgeHardness`**; T4 = float4 index cb4[12].z = `LiquidBaseUVScaleAndOffset`; T5 / T6 = float3 at
cb4[13].x / [13].y = **`EdgeColor` / `CenterColor`** (both sRGB → linear); T8 = float4 index cb4[10].y =
`UVDistortion.MaskDistortUVScaleAndOffset`, T9.y = scalar cb4[14].y = `UVDistortion.MaskDistortIntensity` (the Blend*
distortion pair is not read); the particle index is the quad (vertex / 4); the buffer is the bindless SRV of push
dword 21; `AlphaCursor`, `DissolveUVScaleAndOffset`, `ColorMapUVScaleAndOffset` (not in that capture). Billboard vertices come with a normal and tangent
from the CPU billboarder; the VS flips both when the normal faces away from the camera.

**PS, in order** (`ps_popcorn_liquid_billboard_p` #26, mesh #21; textures t10 base, t11 mask, t12 normal (DXT5nm, bias
-0.5), t13 speckle, t14 colour map, t16 distortion, t18 alpha/dissolve map, t34 cube, t37 BRDF LUT):
1. `uvD = sat99(uv + (Distort.rg(uv*T8.xy+T8.zw)*2-1) * T9.y)` (UV_DISTORTION: the mask-distortion pair), `uvB = uvD * BaseUV.xy + BaseUV.zw`.
2. `p = LiquidBase.r(uvB) * LiquidMask.r(uvD)`; `q = Invert ? 1 - p : p`; `L = op(LiquidBaseOp, p, q)` (the section 2 ops).
3. `alpha = sat((L - AlphaThreshold) * LiquidHardness² * 64 + 0.5)` (a hard-edged threshold).
4. Edge ramp `e = min(sat(1 - p)^T3.z, 1)`; colour `= T5 * mix(e, T6, T3.w)` (Invert: `T6 * T5`), i.e. with the
   stream names (calib5) `e = sat(1 - p)^EdgeControl`, `colour = EdgeColor * mix(e, CenterColor, EdgeHardness)`.
   LIQUID_COLOR_MAP: `cm = ColorMap(uv*CMUV)` (inverted by ColorMapInvert), `cm' = mix(e, cm, T3.w)`,
   `colour = op(ColorMapOp, T5, cm')` (operands by analogy with the plain path: no colour-map capture yet).
5. ALPHA_REMAP: Dissolve: `f = sat(Dissolve.r(uvD*DissUV) - cursor.x)`, `rgba = sat(rgba * f)`; else `alpha = AlphaMap(alpha + cursor.x, cursor.y)`.
6. Normal: `n = normalize(T * x*NI + B * y + N * z*NI)` from the DXT5nm sample (NormalIntensity scales x and z, as written).
7. Roughness `(1 - p) * Roughness`; F0 0.04; scene lighting (section 10) with the wrapped diffuse; ambient SH and the IBL cube
   (`CubemapIntensity` scales the speckle term below; the cube itself uses the engine's `cb0[0].w`).
8. **Headlight**: a white light along the view direction with a normal bent towards the viewer, `HeadlightContribution.x`
   × diffuse `albedo (1 - F)/π × wrapped N.V` + `.y` × GGX specular, added **after** the exposure multiply (display units).
9. Speckle: `SpeckleMap.r(reflect(...).xy) * SpeckleIntensity * p * CubemapIntensity` added to the colour.
10. Output as the Uber OIT (AlphaBlend in the whole corpus): `o0 = (rgb*alpha*w, alpha*w)`.

Speckle vector (calib5): `r = normalize(reflect(-cb[2].xyz, n_ts))` with cb = the root CBV at push offset 40 (row 1 the
camera position, row 2 the camera's forward axis in world space, (-0.612, -0.5, -0.612) in the capture) and `n_ts`
the tangent-space normal (x NI, y, z NI) before the TBN: the world to-camera vector reflected about a tangent-space
normal, as written; the map is sampled at `r.xy`.

## 9. Default mesh (`Diablo_Default_Mesh`, `popcorn_mesh_p/v`)

**Reader** (0x141027db0): `Diffuse.DiffuseMap`, `Diffuse.Color`, `Lit.NormalMap`, `Lit.RoughMetalMap` (`Lit` flag 4),
`VertexDisplacement` (8), `Fresnel` (0x10; ApplyToColor/ApplyToAlpha/BlendOp), `Exposure` (0x20, PhysicalExposure 0x40),
SoftParticles, MipBias, ColorVariant; `Animation.*` (skinned FX models). `Lit.Roughness`/`Metalness`/`LitMaskMap` are not
read by the shader. **Keywords** (0x141029090): PS `FRESNEL, POPCORN_LIGHTING (= Lit), ALPHA_BLEND (= blend mode m != 4),
SOFT_PARTICLES, POPCORN_ADDITIVE, COLOR_VARIANT`; VS `SKINNING_MODE, COLORS, VERTEX_DISPLACEMENT, VERTEX_ALPHA, FRESNEL,
COLOR_VARIANT`.

**Lit PS** (#2): `albedo = DiffuseMap(uv*UV)` (rgba), RoughMetalMap = (R occlusion, G roughness, B metal, A **1 - emissive**),
normal from `Lit.NormalMap` (DXT5nm); F0 `mix(0.04, albedo, metal)`; GGX lights of the light grid + SH ambient + IBL;
`emissive = albedo * (1 - A)` added; `out = (lit + emissive) * exposure * Color`. ALPHA_BLEND=0 (`Transparency.Type` 4: 170
of 506 in the corpus) is **opaque**: `discard` when `Color.a * albedo.a < 0.001`, o1 = the octahedral normal and roughness
(a G-buffer-like target). ALPHA_BLEND=1: the Uber OIT outputs with soft particles.
**Unlit PS** (#21): `c = DiffuseMap * Color`; FRESNEL: `G = Gradient(1 - |n.z_view|, FresnelCursor)` (sRGB → linear),
`r = fop(Fresnel.BlendOp, c, G)`, `s = (ApplyToColor ? r.rgb : c.rgb, ApplyToAlpha ? r.a : c.a)`, **then `s * c`** (the
shader multiplies by the colour again); `rgb *= exposure`; additive output (`m` 0: rgb, 1: rgb × alpha).

## 10. Lit Uber and Smoke (POPCORN_LIGHTING)

`ps_popcorn_uber_billboard_p` #3578 (Smoke, `barricade_door_death` renderer 1) and #3599 (lit fog): after the Uber colour
(3.3 steps 1-7; the colour is the albedo) and alpha (with the early discards and soft particles):
- normal map t20 at the mesh/billboard uv (DXT5nm; `Lit.NormalMap` when set, else the engine's flat map), tangent frame
  from the billboarder's normal/tangent (billboards) or the mesh; **roughness 1** (plus the screen-space normal variance);
- direct lights: cb1 key light(s) and the light-grid lights (t4/t5/t8: point lights with `(1 - (d/r)^steep)²/(1 +
  0.093 d²)` falloff, spot lights), GGX specular (F0 0.04) and **wrapped diffuse** `NormalWrap.WrapFactor + (1 - wrap) ×
  sat(N.L)` (`cb4[5].z`);
- ambient: L2 spherical harmonics (cb0[1..9]) × albedo/π × `cb0[0].z`, IBL cube t34 × split-sum BRDF (t37) × `cb0[0].w`;
- the sum × `cb10[0].x` (exposure), then the Uber output rules.
`NormalBend.NormalBendingFactor` is not in the shaders: the CPU billboarder bends the corner normals (viewer:
`packers.bend_normals`, PopcornFX's meaning, **GUESS**). FullLit vs FastLit select the same permutation (POPCORN_LIGHTING 2).

## 11. Standard mesh (`Diablo_Standard_Mesh`)

No PopcornFX shader: the class (0x141035510) emits each particle as an animated model instance
(`ModelInstanceTracking.AnimatedModelInstanceIds`) that the engine's own model renderer draws with the `.model`'s materials
(albedo, normal, ORM), plus `Shadows`. Viewer: the model's own maps, lit like a unit, opaque (**GUESS**: the model pass
ignores `Transparency.Type`); `Animation` (skinned) models are drawn in bind pose.

## 12. Stencil (`Diablo_Stencil`, `_Stencil_Mesh`)

`ps_popcorn_stencil_p` (ALPHA_REMAP 0/1) has **no colour output**: `m = AlphaThreshold.Mask.r(uv * MaskUV)` (inverted by
`AlphaThreshold.Invert`, `cb4[2].y`), with ALPHA_REMAP `a = AlphaThreshold.AlphaRemap(m + cursor.x, cursor.y)`;
`discard` when `a < 0.001`. The VS: billboard or mesh (`MESH`, `SKINNING_MODE`), streams
`AlphaThresholdMaskUVScaleAndOffset` and `AlphaThresholdRemapCursor`. So the draw only marks the stencil (holes and cracks:
`vfx_chaos_abyss_hole`, `vfx_expansion_eruption_crack`, `vfx_nihlathakboss_death_eruption_crack`). **Game state** (calib2,
RenderDoc capture g5 frame 7238, `vfx_chaos_abyss_hole` cast in Blood Moor): the stencil draw runs FIRST, in the Depth
Prepass (`Depth Prepass/Stencil VFX`, eid 11841, before every model): depth test and write off, stencil front
`Always/Replace` ref 4, write mask **0x04**, cull back, target Velocity (no blend). The ground's prepass then skips the mark:
`Terrain` depth prepass (13875) stencil `Equal 0`, compare mask 0x24; ground decorators (mud, grass clumps, 13739) mask
0x34; ordinary models (11877) have no stencil test. The Forward pass draws with depth `Equal` (and stencil mask 0x02), so
terrain is absent inside the mark and the depth there stays at the clear (reverse Z, `Greater`). The hole's own
billboards (Transparent/Additive, 18339.., 18737) test depth `Greater` without stencil, so what they draw below the ground
shows through the cut. The hole quad sits at y = -1.82 (4 x 4 units). PS 33948 confirms the formula above (mask r,
inverted by a cb flag, remap at (m + cursor.x, cursor.y), demote when `< 0.001`). The viewer's far-depth approach matches
this order, but in the viewer the ground cut applies to the terrain under the 2D background only.

### 12.1 `EnableAuraStencil` (Uber class; lane r3-aurastencil)

Not a material of its own: a renderer feature (43 effects: paladin aura rings, cast overlays, some deaths) that only the
Uber reader `0x14102c2d0` reads (getter `0x141008a50` → material byte +0x95). The Uber pipeline builder `0x14102e500`
then swaps its depth-stencil block from "stencil off" (`0x07ffff00`, `0x07000000`) to `0x06003001` with the back compare
cleared: **stencil test on, compare mask 0x30, write mask 0, front GreaterOrEqual, ops Keep, back Never** (block layout
as the `Diablo_Stencil` class's `01 00 04 07` = Always/Replace write 4, which RenderDoc confirms, and the grass pass's).
**Game state** (RenderDoc, lane r3-aurastencil run `a2`, frame 9038, Thorns aura with a Fallen in the ring, Blood Moor):
the Depth Prepass draws every unit model with Always/Replace ref 0x30, write mask **0x10** for bodies, heads, armour (player
and monsters; the Transparent pass's hair too) and **0x20** for held weapons and shields; world models write nothing.
The aura draw (`Additive/Billboards/Vfx FX_thorns_overlay`, eid 17917) tests GreaterEqual **ref 0** mask 0x30: it is
drawn only where no unit is the surface, whether the aura is in front of or behind that unit (depth Greater, no write).
Corpses use the same test (mask 0x34). Viewer: `fx/aurastencil.py` (a depth24plus-stencil8 copy of the scene depth, the
unit draws of the base scene mark 0x10, aura draws drawn against it with the game's state).

## 13. Lights and decals (kind 5)

The kind-5 class reader (0x14101c4e0) reads the decal layers (`Decal.DiffuseMap/NormalMap/OrmMap/EmissiveMap`,
`ApplyToAlbedo/Normal/Occlusion/Roughness/Metalness` at +0x258..0x25c, `Emissive` flag 4) and nothing of the light's
colour or range: those are particle inputs consumed by the `_pksi_vv*` hooks (lane vm's `SimState.lights`).

**Decals as the game draws them** (lane r3-decal; code and CPU reference `hdkit/fx/decal.py`, tests
`tests/test_hdfx_decal.py`). There is no decal shader: `ps_terrain` (and `ps_models_p` for draws with cb2[20].x bit 16)
walks a clustered decal list (t4 per-tile count `& 63`, t5 indices, t8 seven float4 per decal) and blends the surface
*material* before lighting it.

| t8 row | what (packer 0x101cbf0) |
|-|-|
| 0-2 | view -> box ([-0.5, 0.5]^3) of S(`Decal.DecalSize`, full extents) R(Y = -`Decal.Direction`, X = norm(cross(Y, (Y.x, 0, Y.z))) or (1, 0, 0), Z = X x Y) T(`Position`) x view (inverted) |
| 3.x | byte 0 `EdgeFade` x 255 (xz fade, fraction of the half size); bytes 1/2 cos(`FadeOutAngle`) / cos(`FadeInAngle`) x 127 (signed; test min < N.Y < max, only when the angles differ) |
| 3.y | byte 0 `ThresholdValue` x 255, byte 1 `ThresholdHardness` x 255, byte 2 (box-height fade) never written |
| 3.w | byte 0 (log10(`Emissive.Emissive.Intensity`) - 2) / 4 x 255, byte 1 `Emissive.Emissive.Focus` / 8 x 255; bits 16..: 1 angle, 2 albedo, 4 normal, 8 occlusion, 16 roughness, 32 metalness (switch and slice), 64 threshold (value > 0 and an ORM map), 128 emissive |
| 4, 5 | box X and Y in view space (tangent; projection axis = the normal map's z) |
| 6 | slice ids (u16 pairs): albedo `$RGB` (t30) / alpha `$A`, normal `$NRM` (t31, x in .w, y in .y) / ORM `$A` (threshold), ORM `$R` / `$G`, `$B` / emissive `$R` (t32, one channel each) |

Per pixel, in list order: w = alpha x edge fades [x angle] [x sat((orm.a - thr) x hard^2 x 64 + 0.5)]; albedo, normal
(normalize(mix(N, normalize(nx X + ny (X x Y) + nz Y), w))), occlusion (from 1), roughness, metalness (from 0) each mix
towards the decal by w; the walk stops when the summed w reaches 1. Emissive: E += albedo' x w x 10^(2 + 4 e) x mask x
max(N'.V, 0.001)^(64 f) x (N'.V + 1) / pi, added to the lit colour before x cb10[0].x. Then the usual lighting
(roughness >= 0.04, F0 = mix(0.04, albedo, metal)). `Decal.OpaqueColor`, `OpaqueNormal`, `ThresholdAlphaSelect` and the
`DecalWeight` input are read neither by the class reader nor by the packer: no effect in the game. Renderer kind 4
(`Default_Decal.pkma`): the class picker 0xec3b60 builds classes for kinds 0-2 and 5 only, so it is never drawn.

Viewer (no G-buffer): each decal relights what is under it. Diffuse light is linear in albedo, so with D = light per unit
albedo, S = specular + environment: out = dst k + add, k = (1 - w_albedo) D'/D_ground, add = w_albedo albedo D' + S' -
k S_ground + E (`fs_decal_mul` with dst x src blending, then `fs_decal_add`; ground roughness `decal.GROUND_ROUGHNESS`
0.9, normal from the depth). Over a game shot (`render(bg=)`, added after the effect pass) the shot's scene light is bound
to t3 and `fs_decal_add` writes plate x (k - 1) + add. Since lane r3-decal2 every decal of a frame is taken in ONE
pass as the game does (`fx/decal_pass.py`, `view/fx_decals.wgsl`): records in list order (`Record.seq`, effect by effect)
in a storage buffer and the maps in texture arrays, the material walk mixes normal, occlusion, roughness, metalness and
albedo (as A x cA + aD, A the unknown ground albedo) and stops once the summed weight reaches 1, then the lighting runs
once: out = dst k + add, k = cA D'/D_g, add = aD D' + S' - k S_g + E. Receivers: only pixels on the ground plane (render
`floor`, else y 0; `RECEIVER_TOL` 0.25 units), so units standing in a box are not relit; `decal_pass.RECEIVERS = False`
gives the old behaviour. Not modelled: model draws that carry the bit (the viewer has no such flag), a non-flat ground
(the plane test), and the ground albedo inside the emissive term (the decals' part aD stands for albedo').

**Spawn time and place** (lane r3-decal2, `fx/timeline_fx.py`): most monster pools are SpawnVFX events of the death
timeline (`combined.timelines`: `vfx_bloodlord1_bloodpool` at clip time 0.5 s of the 1.33 s death clip, bone `root`,
attachToDeadBody), so they start part-way through the death and lie on the corpse's bone. Growth: many pools grow by
`asfloat(asint(effect.dt) & asint(cond))` (a bool is a 32-bit all-ones mask); the VM read registers under a new type by
numeric conversion (dt -> 0) and froze them (clawviper, vilechild); `vm.as_lanes` keeps the bits, as the game's executor.

## 14. Colour variants (COLOR_VARIANT; lane r3-colorvariant)

Implemented in `hdkit/fx/colorvariant.py` (render hook `FxRenderer._color_variant`, WGSL `color_variant`). Read from the
shaders (`ps_popcorn_uber_billboard_p` #144, `ps_popcorn_uber_mesh_p` #96/#108, `ps_popcorn_mesh_p` #32/#34, VS
`vs_popcorn_uber_billboard_v` #16) and D2R.exe (the 6ab3782c dump, decompiled):

- **Per particle index.** The VS reads the particle stream of `ColorVariant.TransformIndex` (cb4[15].w / [16] offsets),
  `max(x, 0)` then float -> uint, flat to the PS (TEXCOORD12). The effect's scripts copy the effect attribute
  `ColorVariantIndex` (int, 0..255) into it (every one of the 61 effects; lane vm's sim does this as it is).
- **Where it comes from.** 0xeabd20 / 0xeabfc0 (D2Render popcorn_manager) set `ColorVariantIndex` on every effect a unit
  starts: the request's own value (its +0xc, else +8), but **37 + c** when the unit's particle colour-shift component
  (looked up by 0x71b2b0) holds c != 0; an index > 0xa5 (165) asserts. That component is written by 0x76c230 from
  0x140341c80, the unit's current colour shift (states.txt `colorshift` of its strongest coloured state, `colorpri`),
  as c when c <= 128, else 0 (shifts over 128 go to the unit variant's `customColorShiftEntries`, a model-only tint).
  D2R 3.x states: freeze/cold/holywindcold/blue 108 -> 145, poison 104 -> 141, red 100 -> 137, revive 73 -> 110,
  sigillethargy 103 -> 140, sigilrancor 106 -> 143, sigildeath 107 -> 144 (a mod's states.txt can have more). **Open**: the
  request's own value is 0..7 (no tint) in everything traced; whether any caller passes 8..37 (the randtransforms
  part, classic's random unique-monster colours) was not found.
- **The table** (t6, built at 0x1019270, 0x9e = 158 entries x 32 bytes): entries 0..29 from
  `data/hd/global/palette/randtransforms.json` (loaded with count 0x1e), entries 30..157 from
  `data/hd/global/palette/transforms.json` (count 0x80); each entry = the ColorTransform's `colorAdjustment` (16 bytes)
  then its `colorTint` (16 bytes, the CPU struct's +0x50). The unit model's own colour shift (0xee9100) reads the same
  `transforms.json` entry c - 1, so particle index 37 + c and the model agree: ColdFrozen (107), Poison (103), Seal_*
  (102, 105, 106), entry 99 red, 72 revive.
- **PS maths**: index <= 7 nothing; e = t6[index - 8]; `v = exp2(log2(|c|) * e.adjust.w)`, `v = v + e.tint.a (v *
  e.tint.rgb - v)`, `c = c (1 - k) + v k`, k = `ColorVariant.VariantContribution` (cb4[7].x). Uber billboard/ribbon/mesh:
  c = the colour after Color x the grey, the alpha remap, Fresnel and the gradient remap, before the exposure scalar.
  Default mesh: c = the diffuse map's rgb, before Color, Fresnel and the lighting. Only `adjust.w` and the tint are used
  (the hue/saturation/value parts are for unit models).
- **Viewer** (no storage buffer: wgpu's 16 vertex attributes are full): the exponent goes in vertex `misc.y`, shared with
  the AtlasAlphaOnly cell index since the r3 integration: misc.y = (cell + 2048) + 4096 x round(exponent x 256)
  (`colorvariant.pack_misc_y`, exponent 0 = no variant); the tint with its amount folded in (`lerp(1, tint, a)`,
  quantised 8 bits a channel, packed r + 256 g + 65536 b) in `dist.z`; the VS hands both to the PS in the flat VOut `cv`
  (location 15); the contribution is the uniform `xv.x` (float 256). The table comes through the renderer's vfs
  (`--root casc`); without it every entry is identity and a note says so.

