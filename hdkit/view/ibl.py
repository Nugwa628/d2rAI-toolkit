"""The game's image-based light: a biome's `iblSettings.cubemapFilename` (`data/hd/env/ibl/<biome>_CUBEMAP.ibls`, a DDS
cube, BC6H unsigned float, 256^2, 9 mips) and the SH irradiance the game's shaders read from it (root CBV 6 floats 4..39,
basis 1, y, z, x, xy, yz, 3z^2 - 1, xz, x^2 - y^2 in world axes; docs/hdfx.md "Unit light", lane unitlight).

  cube = read_cube(bytes)                 # {"size", "mips", "faces": [[block bytes per mip] x 6], "format"}
  faces = decode_cube(cube, mip=2)        # (6, n, n, 3) float radiance (GPU: wgpu's BC6H sampler; GPU-gated callers)
  sh = irradiance_sh(faces)               # (9, 3): the game's coefficients (lane hdfx-r3-lighting, checked against the
                                          # capture's CBV 6 of Blood Moor and the Pit, see fx/lighting.py)

The cube's face order and texel directions are D3D's (+x, -x, +y, -y, +z, -z; face (u, v) as the D3D docs), and its axes
are the world axes the shaders use: projecting mip 0 gives the captured CBV 6 numbers to 0.004 summed over all 27 (act 1
outdoors, calib3 g4 terrain draw 15460; act 1 caves, calib5 s3 draw 8331); every other axis permutation or sign is at
least 1.7 off. So the game's SH is the plain irradiance projection of the cube's top mip, nothing fitted.

Table: `python -m hdkit.view.ibl --root casc --out hdkit/fx/ibl_sh.json` decodes every cube the biomes name (GPU-gated)
and writes {cube logical path: {"sh": 9 x 3, "biomes": [...]}}; `fx.lighting` reads it so renders need no GPU decode.
"""
import struct

import numpy as np

DXGI_BC6H_UF16 = 95
AXIS_SIGN = (1.0, 1.0, 1.0)        # cube direction -> world direction, per axis (identity, checked on two captures)
BASIS = ("1", "y", "z", "x", "xy", "yz", "3z2-1", "xz", "x2-y2")


def read_cube(data: bytes) -> dict:
    """A DDS cube map (DX10 header) split into faces and mips of raw block bytes."""
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS file")
    h = struct.unpack("<31I", data[4:128])
    height, width, mips = h[2], h[3], max(1, h[6])
    if data[84:88] != b"DX10":
        raise ValueError("no DX10 header")
    fmt, _dim, misc, arr, _ = struct.unpack("<5I", data[128:148])
    if not misc & 4:
        raise ValueError("not a cube map")
    if fmt != DXGI_BC6H_UF16:
        raise ValueError("DXGI format %d (BC6H unsigned only)" % fmt)
    off = 148
    faces = []
    for _f in range(6 * max(1, arr)):
        levels = []
        for m in range(mips):
            w, hh = max(1, width >> m), max(1, height >> m)
            n = ((w + 3) // 4) * ((hh + 3) // 4) * 16
            levels.append(data[off:off + n])
            off += n
        faces.append(levels)
    if off != len(data):
        raise ValueError("DDS size %d, layout wants %d" % (len(data), off))
    return {"size": width, "mips": mips, "faces": faces[:6], "format": fmt}


DECODE_WGSL = """
@group(0) @binding(0) var t: texture_2d_array<f32>;
@group(0) @binding(1) var<storage, read_write> o: array<f32>;
@group(0) @binding(2) var<uniform> u: vec4<u32>;   // n, mip
@compute @workgroup_size(8, 8, 1) fn main(@builtin(global_invocation_id) g: vec3<u32>) {
  let n = u.x;
  if (g.x >= n || g.y >= n) { return; }
  let c = textureLoad(t, vec2<i32>(i32(g.x), i32(g.y)), i32(g.z), i32(u.y));
  let i = ((g.z * n + g.y) * n + g.x) * 3u;
  o[i] = c.r; o[i + 1u] = c.g; o[i + 2u] = c.b;
}
"""


def decode_cube(cube: dict, mip: int = 2, device=None) -> np.ndarray:
    """(6, n, n, 3) float32 radiance of mip `mip`, decoded by the GPU (wgpu feature texture-compression-bc). The caller
    owns the GPU gate (hdkit.gpugate)."""
    import wgpu
    if device is None:
        ad = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        if "texture-compression-bc" not in ad.features:
            raise RuntimeError("this GPU has no BC texture support")
        device = ad.request_device_sync(required_features=["texture-compression-bc"])
    S = cube["size"]
    n = max(1, S >> mip)
    nb = max(4, n)
    U = wgpu.TextureUsage
    tex = device.create_texture(size=(nb, nb, 6), format="bc6h-rgb-ufloat", usage=U.TEXTURE_BINDING | U.COPY_DST,
                                mip_level_count=1)
    for f in range(6):
        blk = cube["faces"][f][mip]
        device.queue.write_texture({"texture": tex, "mip_level": 0, "origin": (0, 0, f)}, blk,
                                   {"offset": 0, "bytes_per_row": (nb // 4) * 16, "rows_per_image": nb // 4}, (nb, nb, 1))
    out = device.create_buffer(size=6 * n * n * 3 * 4, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC)
    ub = device.create_buffer_with_data(data=np.array([n, 0, 0, 0], np.uint32).tobytes(), usage=wgpu.BufferUsage.UNIFORM)
    pipe = device.create_compute_pipeline(layout="auto", compute={
        "module": device.create_shader_module(code=DECODE_WGSL), "entry_point": "main"})
    bg = device.create_bind_group(layout=pipe.get_bind_group_layout(0), entries=[
        {"binding": 0, "resource": tex.create_view(dimension="2d-array")},
        {"binding": 1, "resource": {"buffer": out, "offset": 0, "size": out.size}},
        {"binding": 2, "resource": {"buffer": ub, "offset": 0, "size": 16}}])
    enc = device.create_command_encoder()
    cp = enc.begin_compute_pass()
    cp.set_pipeline(pipe)
    cp.set_bind_group(0, bg)
    cp.dispatch_workgroups((n + 7) // 8, (n + 7) // 8, 6)
    cp.end()
    device.queue.submit([enc.finish()])
    return np.frombuffer(device.queue.read_buffer(out), np.float32).reshape(6, n, n, 3).copy()


def face_directions(n: int) -> tuple:
    """(dirs (6, n, n, 3) unit vectors in cube axes, solid angle (6, n, n)) of every texel centre, D3D cube layout."""
    c = (np.arange(n) + 0.5) / n * 2.0 - 1.0
    s, t = np.meshgrid(c, c)                          # s: u to the right, t: v down
    one = np.ones_like(s)
    faces = [np.stack(v, -1) for v in (
        (one, -t, -s), (-one, -t, s), (s, one, t), (s, -one, -t), (s, -t, one), (-s, -t, -one))]
    d = np.stack(faces)
    r2 = (d * d).sum(-1)
    e = np.linspace(-1.0, 1.0, n + 1)                 # texel solid angle, exact: the area-element integral over its square
    A = np.arctan2(np.outer(e, e), np.sqrt(np.add.outer(e * e, e * e) + 1.0))
    dw = A[1:, 1:] - A[:-1, 1:] - A[1:, :-1] + A[:-1, :-1]
    return d / np.sqrt(r2)[..., None], np.broadcast_to(dw, d.shape[:-1]).copy()


def basis(n) -> np.ndarray:
    """(..., 9) the game's SH irradiance basis at unit normals n (..., 3), world axes."""
    n = np.asarray(n, float)
    x, y, z = n[..., 0], n[..., 1], n[..., 2]
    return np.stack([np.ones_like(x), y, z, x, x * y, y * z, 3 * z * z - 1, x * z, x * x - y * y], -1)


def irradiance_sh(faces, axis_sign=AXIS_SIGN) -> np.ndarray:
    """(9, 3) coefficients c with E(n) = basis(n) . c = the cosine-weighted integral of the cube's radiance over the
    hemisphere round n (no 1 / pi: the shaders divide by pi), from (6, n, n, 3) radiance. Exact for the band-limited
    (l <= 2) part, which is all the 9 terms carry: Ramamoorthi & Hanrahan's A_l = pi, 2 pi / 3, pi / 4."""
    L = np.asarray(faces, float)
    d, dw = face_directions(L.shape[1])
    d = d * np.asarray(axis_sign, float)
    x, y, z = d[..., 0], d[..., 1], d[..., 2]
    Y = np.stack([0.282095 * np.ones_like(x), 0.488603 * y, 0.488603 * z, 0.488603 * x, 1.092548 * x * y,
                  1.092548 * y * z, 0.315392 * (3 * z * z - 1), 1.092548 * x * z, 0.546274 * (x * x - y * y)], -1)
    w = (Y * dw[..., None]).reshape(-1, 9)
    Llm = w.T @ L.reshape(-1, 3)                     # (9, 3) radiance SH
    A = np.array([np.pi] + [2 * np.pi / 3] * 3 + [np.pi / 4] * 5)
    k = np.array([0.282095] + [0.488603] * 3 + [1.092548, 1.092548, 0.315392, 1.092548, 0.546274])
    return Llm * (A * k)[:, None]


def irradiance(sh, n) -> np.ndarray:
    """E(n) (..., 3) from (9, 3) coefficients."""
    return basis(n) @ np.asarray(sh, float)


def biome_cubes(fs, names) -> dict:
    """{cube logical path: [biome names]} of the biomes `names` that exist under data/hd/env/biome."""
    import json
    out = {}
    for b in names:
        try:
            d = json.loads(fs.read("data/hd/env/biome/%s.json" % b).decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        c = (d.get("iblSettings") or {}).get("cubemapFilename")
        if c:
            out.setdefault(c.replace("\\", "/").lower(), []).append(b)
    return out


def biome_names(fs) -> list:
    """Biome names: every biome file the roots list, plus the prefixes of the vis files (biomes share their names)."""
    import re
    names = {h.logical.rsplit("/", 1)[-1][:-5] for h in fs.walk("data/hd/env/biome", ".json")}
    for h in fs.walk("data/hd/env/vis", ".json"):
        names.add(re.sub(r"_(dawn1|dawn2|day|dusk1|dusk2|night|desecrated|vis)$", "", h.logical.rsplit("/", 1)[-1][:-5]))
    return sorted(names)


def build_table(fs, names=None, mip: int = 0) -> dict:
    """{cube: {"sh": (9, 3) list, "biomes": [...], "mean": rgb}} for every cube the biomes name (GPU decode)."""
    cubes = biome_cubes(fs, names or biome_names(fs))
    out = {}
    for c, bs in sorted(cubes.items()):
        try:
            cube = read_cube(fs.read(c))
        except (OSError, ValueError) as e:
            out[c] = {"error": str(e), "biomes": bs}
            continue
        faces = decode_cube(cube, mip)
        out[c] = {"sh": np.round(irradiance_sh(faces), 5).tolist(), "biomes": bs, "mip": mip,
                  "mean_radiance": np.round(faces.reshape(-1, 3).mean(0), 5).tolist()}
    return out


def main(argv=None):
    import argparse
    import json
    from . import vfs
    ap = argparse.ArgumentParser(description="SH irradiance of every biome's IBL cube (GPU decode; run behind hdkit.gpugate)")
    ap.add_argument("--root", action="append", default=[], help="vfs root spec (casc, name=folder); first wins")
    ap.add_argument("--biome", action="append", default=[], help="biome names (default: every biome the roots list "
                                                                  "plus the vis file prefixes)")
    ap.add_argument("--extra", default="", help="comma list of more biome names to try")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    fs = vfs.Layered(vfs.parse_roots(a.root or ["casc"]))
    names = a.biome or biome_names(fs)
    names = sorted(set(names) | {x for x in a.extra.split(",") if x})
    t = build_table(fs, names)
    from pathlib import Path
    Path(a.out).write_text(json.dumps({"source": "hdkit.view.ibl build_table (mip 0, D3D cube axes = world)",
                                       "basis": list(BASIS), "cubes": t}, indent=1) + "\n", encoding="utf-8")
    print(len(t), "cubes ->", a.out)


if __name__ == "__main__":
    main()
