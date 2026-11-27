"""Back-face culling of effect draws, by D2R's own rule (lane r3-cull; docs/hdfx-cull.md).

Every D2R material keeps a cull mode at +0x60 of the material object (0 none, 2 back; the base constructor 0x141013490
sets 0). The blend-mode reader 0x14100e130, which all seven material classes call first, sets it from the renderer's
geometry: billboards (renderer kind 0) cull back unless `General.BillboardingMode` is 5 (PlaneAligned), ribbons (kind 1)
cull back unless the mode is 1 or 2 (NormalAxisAligned, SideAxisAligned); meshes, lights and decals keep 0. The Uber
reader 0x14102c2d0 (Diablo_Uber*, Diablo_Smoke*) and the Liquid reader 0x14101fd70 (Diablo_Liquid*) then overwrite it:
2 when `BackfaceCulling` is on, else 0. Captures (calib2 g7, frame 5563): Fire Bolt's Uber mesh (BackfaceCulling on)
and its Default_Billboard sparks (mode 0) are drawn with cull Back; the Uber billboards without it with NoCull.

Front face: the captured sparks are camera-facing quads whose right-hand normal cross(b - a, c - a) points at the eye,
and they are drawn under cull Back, so the game keeps a triangle when its right-hand world normal faces the camera
(Fire Bolt's mesh: the same winding as its stored normals, outward). The viewer uses the game's world and the model
files' triangle order, so `front_face(M)` only has to find which screen winding that is for the viewer's matrix.
"""
import numpy as np

UBER_LIQUID = ("diablo_uber", "diablo_smoke", "diablo_liquid")   # the classes whose reader reads BackfaceCulling
PLANAR = 5                                                    # billboard mode that is not culled by default
RIBBON_AXIAL = (1, 2)                                         # ribbon modes that are not culled by default


def _v(x):
    if isinstance(x, (tuple, list)):
        return x[0] if x else 0
    return 0 if x is None else x


def _on(x) -> bool:
    if isinstance(x, (bytes, bytearray)):
        return any(x)
    try:
        return bool(_v(x))
    except (TypeError, ValueError):
        return bool(x)


def cull_mode(kind: str, material: str, get) -> str:
    """'back' or 'none' for a renderer: `kind` billboard/ribbon/mesh/light/decal, `material` its .pkma path, `get` the
    property getter (name -> value; a switch reads truthy when on)."""
    mode = 0
    m = int(_v(get("General.BillboardingMode")) or 0)
    if kind == "billboard":
        mode = 0 if m == PLANAR else 2
    elif kind == "ribbon":
        mode = 0 if m in RIBBON_AXIAL else 2
    name = (material or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if kind in ("billboard", "ribbon", "mesh") and name.startswith(UBER_LIQUID):
        mode = 2 if _on(get("BackfaceCulling")) else 0
    return "back" if mode == 2 else "none"


def towards_camera(M) -> np.ndarray:
    """World direction towards the camera for a world -> clip matrix M (column vectors, depth 0 nearest)."""
    Mi = np.linalg.inv(np.asarray(M, float))
    a = Mi @ np.array([0.0, 0.0, 0.0, 1.0])
    b = Mi @ np.array([0.0, 0.0, 1.0, 1.0])
    d = a[:3] / a[3] - b[:3] / b[3]
    return d / np.linalg.norm(d)


def ndc_winding(M, tri) -> float:
    """Signed area (NDC, y up; > 0 counter-clockwise) of a world triangle (3, 3) under M."""
    P = np.c_[np.asarray(tri, float), np.ones(3)] @ np.asarray(M, float).T
    q = P[:, :2] / P[:, 3:4]
    return float((q[1, 0] - q[0, 0]) * (q[2, 1] - q[0, 1]) - (q[2, 0] - q[0, 0]) * (q[1, 1] - q[0, 1]))


def front_face(M) -> str:
    """wgpu `front_face` ('ccw' or 'cw', NDC winding) under which a triangle whose right-hand world normal faces the
    camera is front-facing, as in the game."""
    w = towards_camera(M)
    e1 = np.cross(w, [0.0, 1.0, 0.0] if abs(w[1]) < 0.9 else [1.0, 0.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(w, e1)                      # e1 x e2 = w
    centre = np.asarray(np.linalg.inv(np.asarray(M, float)) @ np.array([0.0, 0.0, 0.5, 1.0]))
    o = centre[:3] / centre[3]
    return "ccw" if ndc_winding(M, [o, o + e1, o + e2]) > 0 else "cw"


def facing(tris, eye_dir) -> np.ndarray:
    """(n,) bool: the triangles (n, 3, 3) the game keeps under cull back for an orthographic camera looking along
    -eye_dir (right-hand normal towards the camera). For tests and reports."""
    T = np.asarray(tris, float)
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    return n @ np.asarray(eye_dir, float) > 0
