"""D2R's script noise builtins, ported bit-exactly from the game's SSE kernels (fp32, numpy-vectorised).

    noise_float3(p)       3D simplex gradient noise, one float in about [-1, 1]            D2R.exe 0xbec8c0 -> 0xc623e0
    noise_float(x)        1D simplex noise                                                 D2R.exe 0xbec680 -> 0xc616a0
    fast_noise_float(x)   1D value noise, quintic-interpolated over a seeded random table  D2R.exe 0xbead00 -> 0xb3d5b0

Tables are dumped from the decrypted image into `noise_data.py` (`python -m hdkit.fx.noise_dump`). Every float operation is
done in the engine's order and rounded to float32 (no fused multiply-add: the kernels are plain SSE `mulps/addps`).
The kernels were checked against the machine code itself in an x86 emulator (see tests/test_hdfx_vm.py for the pinned
values); lane noise's `docs/hdfx-vm-re.md` has the trace.

`noise_float` has two code paths in the engine: a 4-wide one (aligned dense arrays, scale 32/81) and a scalar one (the
tail of an array, unaligned or constant inputs, scale 0.395) that differ in the last bits and by 1.4e-4 relative; here
`noise_float(x, scalar=...)` picks per element like the engine does for a dense array: groups of four go wide, the last
`n % 4` and a constant (one-row) input go scalar.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import noise_data as ND

F32 = np.float32
_I = np.int64

_PERM = np.frombuffer(ND.PERM, dtype=np.uint8).astype(_I)             # 513 entries
_FPERM = np.frombuffer(ND.FPERM, dtype="<u4").astype(_I)              # 257 entries
_OFFS = np.frombuffer(ND.OFFS, dtype="<i4").reshape(8, 2, 4).astype(_I)
_GRAD = np.frombuffer(ND.GRAD, dtype="<f4").reshape(8, 3, 4).astype(F32)

_C23, _C24, _M1, _ZERO, _ONE = F32(8388608.0), F32(16777216.0), F32(-1.0), F32(0.0), F32(1.0)


def _bits(u):
    return np.array([u], dtype=np.uint32).view(F32)[0]


_THIRD, _SIXTH = _bits(0x3EAAAAAB), _bits(0x3E2AAAAB)                    # 0x1d8c1e0, 0x1d8efe0
_NEG06 = _bits(0xBF19999A)                                               # 0x1ddb010 (-0.6f)
_SCALE3 = F32(32.0)                                                      # 0x1d62270
_SCALE1_WIDE = _bits(0x3ECA456F)                                         # 0x1ddaf80 (32/81)
_SCALE1_SCALAR = _bits(0x3ECA3D71)                                       # 0x1ddae90 (0.395)
_F6, _F10, _FM15 = F32(6.0), F32(10.0), F32(-15.0)
FAST_SEED = 0xCAFE4269   # `mov edx, 0xcafe4269` at 0xa5130b seeds the game's RNG right before the table is drawn


def _f32(x):
    return np.asarray(x, dtype=F32)


def _cvtt(x):
    """cvttps2dq: truncate to int32, out of range and NaN -> 0x80000000."""
    ok = (x >= -2147483648.0) & (x < 2147483648.0)
    return np.where(ok, np.trunc(np.where(ok, x, 0)), -2147483648.0).astype(_I)


def _floor(x):
    """The engine's floor: round via the 2^23 trick, then step down where it rounded up."""
    r = ((x + _C23) - _C24) + _C23
    return r + np.where(x < r, _M1, _ZERO)


def _signed(v, flip):
    """v with its sign bit toggled where `flip` (xorps with a shifted sign mask)."""
    return (v.view(np.uint32) ^ (flip.astype(np.uint32) << np.uint32(31))).view(F32)


# ---------------------------------------------------------------------------------------------- noise_float3
def noise_float3(p):
    """(n, 3) points -> (n, 1) simplex noise, as 0xc623e0 (all four simplex corners evaluated at once, summed pairwise)."""
    p = _f32(p)
    if p.ndim == 1:
        p = p.reshape(1, -1)
    p = p[:, :3] if p.shape[1] >= 3 else np.pad(p, ((0, 0), (0, 3 - p.shape[1])))
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    with np.errstate(all="ignore"):
        sk = ((y + x) + z) * _THIRD
        i = [_floor(sk + c) for c in (x, y, z)]
        t = [c - ic for c, ic in zip((x, y, z), i)]
        g = (((i[1] + i[0]) + i[2]) * _SIXTH)
        o = [g + tc for tc in t]                                             # position in the simplex cell
        m = ((o[0] < o[1]).astype(_I)) | ((o[1] < o[2]).astype(_I) << 1) | ((o[2] < o[0]).astype(_I) << 2)
        ii, jj, kk = (_cvtt(ic) & 255 for ic in i)
        off = _OFFS[m]                                                       # (n, 2, 4)
        P = _PERM
        h = np.empty((len(x), 4), dtype=_I)
        h[:, 0] = P[P[P[kk] + jj] + ii]
        for c in (1, 2):
            a, b, k = off[:, c - 1, 0], off[:, c - 1, 1], off[:, c - 1, 2]
            h[:, c] = P[P[P[kk + k] + jj + b] + ii + a]
        h[:, 3] = P[P[P[kk + 1] + 1 + jj] + 1 + ii]
        h &= 15
        gr = _GRAD[m]                                                        # (n, 3, 4)
        X, Y, Z = (o[a][:, None] + gr[:, a, :] for a in range(3))
        d = ((X * X + _NEG06) + Y * Y) + Z * Z
        tt = np.where(_ZERO < d, _ZERO, d)
        t2 = tt * tt
        t4 = t2 * t2
        u = np.where(h < 8, X, Y)
        v = np.where(h < 4, Y, np.where((h == 12) | (h == 14), X, Z))
        u = _signed(u.astype(F32), h & 1)
        v = _signed(v.astype(F32), (h >> 1) & 1)
        c = (v + u) * t4
        r = ((c[:, 2] + c[:, 0]) + (c[:, 3] + c[:, 1])) * _SCALE3
    return r.astype(F32)[:, None]


# ---------------------------------------------------------------------------------------------- noise_float
def _grad1(b):
    """Gradient of a permutation byte: (b & 7) + 1, negated when bit 3 is set."""
    return _signed(((b & 7).astype(F32) + _ONE).astype(F32), (b >> 3) & 1)


def _noise1_cell(x):
    i = _floor(x)
    f = x - i
    idx = _cvtt(i) & 255
    return f, f - _ONE, idx


def _noise_float_wide(x):
    f, f1, idx = _noise1_cell(x)
    t0 = _ONE - f * f
    t0 = t0 * t0
    w0 = (t0 * t0) * f
    t1 = _ONE - f1 * f1
    t1 = t1 * t1
    w1 = (t1 * t1) * f1
    return (_grad1(_PERM[idx + 1]) * w1 + _grad1(_PERM[idx]) * w0) * _SCALE1_WIDE


def _noise_float_scalar(x):
    f, f1, idx = _noise1_cell(x)
    t0 = _ONE - f * f
    w0 = ((f * t0) * t0) * (t0 * t0)
    t1 = _ONE - f1 * f1
    w1 = ((t1 * f1) * t1) * (t1 * t1)
    return (w1 * _grad1(_PERM[idx + 1]) + w0 * _grad1(_PERM[idx])) * _SCALE1_SCALAR


def noise_float(x, scalar=None):
    """(n,) or (n, 1) -> (n, 1) 1D simplex noise as 0xc616a0. `scalar`: None (engine split: groups of four wide, the
    tail and constant inputs scalar), True/False to force one path."""
    x = _f32(x).reshape(-1)
    with np.errstate(all="ignore"):
        if scalar is None:
            cut = 0 if len(x) == 1 else len(x) - len(x) % 4
            r = np.concatenate([_noise_float_wide(x[:cut]), _noise_float_scalar(x[cut:])]) if len(x) else x
        else:
            r = (_noise_float_scalar if scalar else _noise_float_wide)(x)
    return r.astype(F32)[:, None]


# ---------------------------------------------------------------------------------------------- fast.noise_float
@lru_cache(maxsize=1)
def fast_table():
    """256 floats in [-1, 1) the game draws at start-up: SFMT19937 seeded 0xcafe4269 (0xa5130b), range (-1, 1)
    through the RNG's float fill (0xc06830), the same generator as lane samplers' turbulence lattice."""
    from .samplers import sfmt_fill
    return sfmt_fill(FAST_SEED, [(256, -1.0, 1.0)])[0].astype(F32)


def fast_noise_float(x):
    """(n,) or (n, 1) -> (n, 1) value noise as 0xb3d5b0: table[perm[i]] and table[perm[i + 1]] blended by the
    quintic 6f^5 - 15f^4 + 10f^3 (the 4-wide and scalar paths are the same arithmetic)."""
    x = _f32(x).reshape(-1)
    T = fast_table()
    with np.errstate(all="ignore"):
        xi = _cvtt(x)
        xf = xi.astype(F32)
        mask = x < xf
        f = (x - xf) + np.where(mask, _ONE, _ZERO)
        idx = (xi + np.where(mask, -1, 0)) & 255
        a, b = T[_FPERM[idx]], T[_FPERM[idx + 1]]
        s = ((f * _F6 + _FM15) * f + _F10) * ((f * f) * f)
        r = (b - a) * s + a
    return r.astype(F32)[:, None]
