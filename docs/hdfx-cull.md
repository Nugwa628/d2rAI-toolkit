# hdfx: back-face culling (lane r3-cull)

Code: `hdkit/fx/cull.py` (rule, front face), used by `fx/render.py` (`Material.cull`, the pipeline's `cull_mode` and
`front_face`). Tests: `tests/test_hdfx_cull.py`.

## What the game does (read in the decrypted D2R image)

Every material object holds a cull mode byte at **+0x60**: 0 none, 2 back (the captures show Vulkan `NoCull` / `Back`).

1. Base constructor 0x141013490 (all seven classes): +0x60 = 0.
2. Blend-mode reader 0x14100e130, called first by every class reader (Default 0x141013d30, light/decal 0x14101c4e0,
   Liquid 0x14101fd70, Default_Mesh 0x141027db0, Uber 0x14102c2d0, Standard 0x141035660, Stencil 0x1410382b0), sets it
   from the renderer kind and `General.BillboardingMode` (getter 0x141728a80, name bound at 0x14172b309):
   - billboard (kind 0): mode 5 (PlaneAligned) → 0; modes 0-4, absent or out of range → 2;
   - ribbon (kind 1): modes 1, 2 (NormalAxisAligned, SideAxisAligned) → 0; mode 0, absent or other → 2;
   - mesh, light, decal: untouched (0).
3. The Uber reader (`Diablo_Uber*`, `Diablo_Smoke*`; 0x14102d03b) and the Liquid reader (`Diablo_Liquid*`; 0x1410205ed)
   then set +0x60 = 0 and, when the `BackfaceCulling` switch is on, 2. So for these classes the switch alone decides,
   whatever the geometry. No other class reads `BackfaceCulling` (the getter 0x1410087a0 has those two callers only).

So camera-facing billboards and view-aligned ribbons of the Default classes are culled (harmless: they face the camera),
PlaneAligned billboards and axis ribbons never unless an Uber/Liquid material switches it on, and meshes only with the
switch (547 Uber_Mesh + 10 Liquid_Mesh renderers in the 95032 corpus).

## Front face, from the captures (calib2 g7, frame 5563, Fire Bolt)

- eid 18103: Fire Bolt's `Default_Billboard` sparks (mode 2, AxisAligned), cull **Back**, visible in the draw's target at
  the projected positions. Every quad's right-hand normal `cross(b - a, c - a)` (index order) points at the eye.
- eid 17881: the Uber mesh `fx_mesh_fireballVertPaint` (`BackfaceCulling` on), cull **Back**; all 377 triangles wind like
  their stored normals (outward), the instance matrix has a positive determinant, and the viewer's model loader gives the
  same triangle order.
- The Uber billboards of the same effect (no switch) draw with NoCull, as rule 3 says.

Rule: the game keeps a triangle whose right-hand world normal faces the camera. The viewer works in the game's world
(calib2: Fire Bolt's instance matrices equal the sim's), so `cull.front_face(M)` only picks the wgpu winding (NDC) for the
viewer's matrix; the GPU test renders a PlaneAligned billboard turned away and towards the camera to check it end to end.

## Packer fix

`packers.billboard_corners` modes 2 (AxisAligned) and 4 (capsule body) built the side vector as `cross(dir, eye→P)`,
which made the quads face away (the game would cull them all under rule 2). PopcornFX-Web's transcription of
`CAxialBillboarderQuad` and the game's sparks (eid 18103: side along corner 0→1, normal towards the eye) both give
`side = cross(dir, P→eye)`. Modes 0, 1, 3 and view-aligned ribbons already faced the camera; PlaneAligned faces `+Axis2`.

## Open

- Ribbons in the axis modes (1, 2) with `BackfaceCulling` on (13 renderers): their winding follows PopcornFX-Web's
  ribbon transcription (side, a+ a- b+ / a- b- b+), not checked against a capture.
- The view-aligned ribbon's per-point tangent can flip single quads at sharp turns (both here and, as far as the
  transcription goes, in the runtime); not compared with a capture.
