"""hdfx samplers: curves (cubic Hermite), shapes, procedural turbulence, rand/vrand, script noise (exact kernels in fx/noise.py).

Ports of AallynReed/PopcornFX-Web (MIT, @d22b8fe64e595d432fad69facd016a98e3563a0e; `src/engine/curves.js`,
`turbulence.js`, `sim.js` rand/vrand), vectorised over particles with numpy. Credit: hdkit/THIRD_PARTY.md.

Conventions: points/vectors are float arrays of shape (N, k); scalars broadcast; samplers return float32 (N, k).
Engine-call wrappers (`ENGINE_CALLS`, `register`) take `(ctx, sampler, *args)`; see docs/hdfx.md "Samplers API".

Data layout (D2R `.particles`, sparse: only fields that differ from the default are stored; payloads are the raw bytes of
a field, arrays without their count):
  CParticleNodeSamplerData_Curve   17 times f32[n], 18 values f32[n*dim], 19 tangents f32[2*n*dim] (absent = linear),
                                   12/13 min/max limits f32[4], 10 value type (dim) i32, 11 interpolator i32.
  CParticleNodeSamplerData_Shape   11 shape type i32 (0 box 1 sphere 2 ellipsoid 3 cylinder 4 capsule 5 cone 6 mesh),
                                   10 sample dimensionality i32 (3 = volume, else surface), 12 box dims, 13 radius,
                                   14 inner radius, 16 height, 17 hemisphere, 18 non-uniform scale, 20 mesh scale,
                                   27/28 inherit translate/rotate (bool), 29 position, 30 euler degrees.
  CParticleNodeSamplerData_Turbulence  see TURB_FIELDS (ids 10..31, read from D2R.exe's property registration, see there).
"""
from __future__ import annotations

import math
import struct
from functools import lru_cache

import numpy as np

from .noise import fast_noise_float, noise_float, noise_float3

F32 = np.float32
F64 = np.float64


def _f(x):
    return np.float32(x)


def _arr(x, k=None, dtype=F64):
    """Array of shape (N, k) from a scalar, (N,), (N, k) or list; k broadcasts a scalar/column."""
    a = np.asarray(x, dtype=dtype)
    if a.ndim == 0:
        a = a.reshape(1, 1)
    elif a.ndim == 1:
        a = a.reshape(1, -1) if (k is not None and k > 1 and len(a) == k) else a.reshape(-1, 1)
    if k is not None and a.shape[1] != k:
        a = np.broadcast_to(a[:, :1], (a.shape[0], k)) if a.shape[1] == 1 else a[:, :k]
    return a


def _vec(x):
    """rand/vrand argument: a plain 1-D sequence of 2..4 numbers is ONE vector (1, k); (N, k) arrays are per particle."""
    a = np.asarray(x, dtype=F64)
    if a.ndim == 1 and 2 <= len(a) <= 4:
        return a.reshape(1, -1)
    return _arr(a)


def _floats(b):
    """f32 values from raw field bytes (or any sequence)."""
    if isinstance(b, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(b), dtype="<f4").astype(F64)
    return np.asarray(b, dtype=F64).ravel()


def _int(b, default=None):
    if b is None:
        return default
    return struct.unpack("<i", bytes(b)[:4])[0] if isinstance(b, (bytes, bytearray, memoryview)) else int(b)


def _flt(b, default=None):
    if b is None:
        return default
    return struct.unpack("<f", bytes(b)[:4])[0] if isinstance(b, (bytes, bytearray, memoryview)) else float(b)


def _bool(b, default=False):
    if b is None:
        return default
    return bool(bytes(b)[0]) if isinstance(b, (bytes, bytearray, memoryview)) else bool(b)


# ------------------------------------------------------------------------------------------------------ curves

class Curve:
    """Keyframed curve of 1..4 components over time `times` (strictly increasing, D2R: normalised 0..1).

    mode "hermite": cubic Hermite; tangents[(2*i + 0/1) * dim + k] = in/out tangent of key i, component k, **already
    multiplied by the duration of the segment they belong to** (out of key i: segment i->i+1; in of key i: segment
    i-1->i), as in the file. mode "linear": tangents unused (a file curve without field 19 is linear). mode "step":
    holds each key's value until the next key. wrap: "clamp" (default), "repeat", "mirror" outside [t0, tn].
    `min_limits`/`max_limits` clamp the result per component (inf = unbounded). A curve with fewer than 2 keys or
    inconsistent array sizes is *invalid* and samples zeros, as the engine does.
    """

    def __init__(self, times, values, tangents=None, dim=None, mode=None, wrap="clamp", min_limits=None, max_limits=None):
        self.times = _floats(times)
        n = len(self.times)
        v = _floats(values)
        self.dim = int(dim) if dim else (len(v) // n if n else 1)
        self.values = v
        self.tangents = None if tangents is None or len(np.asarray(tangents).ravel()) == 0 else _floats(tangents)
        self.mode = mode or ("hermite" if self.tangents is not None else "linear")
        self.wrap = wrap
        self.min_limits = None if min_limits is None else _floats(min_limits)
        self.max_limits = None if max_limits is None else _floats(max_limits)
        d = self.dim
        self.V = self.T = self._cum = None
        self.interp_code = None
        self.valid = (n > 1 and len(v) == n * d and (self.tangents is None or len(self.tangents) == 2 * n * d)
                      and bool(np.all(np.diff(self.times) >= 0)))
        if self.valid:
            self.V = v.reshape(n, d)
            if self.mode == "hermite" and self.tangents is None:
                self.mode = "linear"
            self.T = self.tangents.reshape(n, 2, d) if self.tangents is not None else None

    # ---- construction
    @classmethod
    def from_fields(cls, f, wrap="clamp"):
        """From raw field bytes {17: times, 18: values, 19: tangents, 12: min, 13: max, 10: value type, 11: interp}."""
        t = _floats(f.get(17, b""))
        v = _floats(f.get(18, b""))
        dim = len(v) // len(t) if len(t) and len(v) % len(t) == 0 else None
        c = cls(t, v, f.get(19), dim=dim, wrap=wrap, min_limits=f.get(12), max_limits=f.get(13))
        c.interp_code = _int(f.get(11))
        return c

    @classmethod
    def from_json(cls, d):
        """From graph `Sampler.data` for kind "curve": {times, values[[..]], tangents_in[[..]], tangents_out[[..]], dim,
        interp}. Tangents are the file's (segment-scaled) ones; see from_slopes for per-time slopes."""
        t = np.asarray(d["times"], dtype=F64)
        v = np.asarray(d["values"], dtype=F64).reshape(len(t), -1)
        ti, to = d.get("tangents_in"), d.get("tangents_out")
        tan = None
        if ti is not None and to is not None and len(ti):
            tan = np.stack([np.asarray(ti, dtype=F64).reshape(len(t), -1), np.asarray(to, dtype=F64).reshape(len(t), -1)], 1).ravel()
        return cls(t, v.ravel(), tan, dim=v.shape[1], mode=d.get("interp"), wrap=d.get("wrap", "clamp"),
                   min_limits=d.get("min_limits"), max_limits=d.get("max_limits"))

    @classmethod
    def from_slopes(cls, times, values, slopes_in=None, slopes_out=None, **kw):
        """Hermite curve from per-unit-time slopes (n, dim); stores them segment-scaled like the file. Default slope:
        finite difference (Catmull-Rom style); the file's unused end tangents hold the raw slope."""
        t = np.asarray(times, dtype=F64)
        v = np.asarray(values, dtype=F64).reshape(len(t), -1)
        n = len(t)
        if slopes_out is None:
            slopes_out = np.gradient(v, t, axis=0) if n > 1 else np.zeros_like(v)
        so = np.asarray(slopes_out, dtype=F64).reshape(n, -1)
        si = so if slopes_in is None else np.asarray(slopes_in, dtype=F64).reshape(n, -1)
        dts = np.diff(t)
        tan = np.zeros((n, 2, v.shape[1]))
        tan[:, 0] = si
        tan[:, 1] = so
        tan[1:, 0] = si[1:] * dts[:, None]        # in of key i: segment i-1 -> i
        tan[:-1, 1] = so[:-1] * dts[:, None]      # out of key i: segment i -> i+1
        return cls(t, v.ravel(), tan.ravel(), dim=v.shape[1], mode="hermite", **kw)

    def to_json(self):
        n = len(self.times)
        d = {"times": self.times.tolist(), "values": self.V.tolist() if self.valid else [], "dim": self.dim, "interp": self.mode}
        if self.T is not None:
            d["tangents_in"], d["tangents_out"] = self.T[:, 0].tolist(), self.T[:, 1].tolist()
        return d

    def to_fields(self):
        """Raw field bytes for 17/18/19 (the edit op `curve`); other fields are the caller's."""
        f = {17: self.times.astype("<f4").tobytes(), 18: self.values.astype("<f4").tobytes()}
        if self.tangents is not None and self.mode == "hermite":
            f[19] = self.tangents.astype("<f4").tobytes()
        return f

    # ---- evaluation
    def _wrap(self, x):
        t0, t1 = self.times[0], self.times[-1]
        span = t1 - t0
        if self.wrap == "clamp" or not span > 0:
            return x
        if self.wrap == "repeat":
            return t0 + np.mod(x - t0, span)
        m = np.mod(x - t0, 2 * span)                      # mirror
        return t0 + np.where(m > span, 2 * span - m, m)

    def _segments(self, x):
        """(i, u, dt, left, right): segment index, local 0..1 position, dt and masks for x <= t0 / x >= tn."""
        t = self.times
        n = len(t)
        i = np.clip(np.searchsorted(t, x, side="right" if self.mode == "step" else "left") - 1, 0, n - 2)
        dt = t[i + 1] - t[i]
        dt = np.where(dt == 0, 1e-9, dt)
        u = (x - t[i]) / dt
        return i, u, dt, x <= t[0], x >= t[-1]

    def sample(self, x, dtype=F32):
        """Values at times x (any shape broadcastable to (N,)) -> (N, dim)."""
        x = _arr(x)[:, 0]
        if not self.valid:
            return np.zeros((len(x), self.dim), dtype=dtype)
        x = self._wrap(x)
        i, u, dt, lo, hi = self._segments(x)
        p0, p1 = self.V[i], self.V[i + 1]
        if self.mode == "step":
            out = p0.copy()
        elif self.mode == "linear":
            out = p0 + (p1 - p0) * u[:, None]
        else:
            u2 = u * u
            u3 = u2 * u
            h00, h10, h01, h11 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u, -2 * u3 + 3 * u2, u3 - u2
            out = (h00[:, None] * p0 + h10[:, None] * self.T[i, 1] + h01[:, None] * p1 + h11[:, None] * self.T[i + 1, 0])
        out = np.where(lo[:, None], self.V[0], np.where(hi[:, None], self.V[-1], out))
        return self._limit(out).astype(dtype)

    def _limit(self, out):
        for lim, op in ((self.min_limits, np.maximum), (self.max_limits, np.minimum)):
            if lim is None:
                continue
            for k in range(min(self.dim, len(lim))):
                if math.isfinite(lim[k]):
                    out[:, k] = op(out[:, k], lim[k])
        return out

    def derive(self, x, dtype=F32):
        """d value / d time at x (analytic), zero outside the key range and for step curves -> (N, dim)."""
        x = _arr(x)[:, 0]
        if not self.valid:
            return np.zeros((len(x), self.dim), dtype=dtype)
        x = self._wrap(x)
        i, u, dt, lo, hi = self._segments(x)
        p0, p1 = self.V[i], self.V[i + 1]
        if self.mode == "step":
            d = np.zeros_like(p0)
        elif self.mode == "linear":
            d = (p1 - p0) / dt[:, None]
        else:
            u2 = u * u
            d00, d10, d01, d11 = 6 * u2 - 6 * u, 3 * u2 - 4 * u + 1, -6 * u2 + 6 * u, 3 * u2 - 2 * u
            d = (d00[:, None] * p0 + d10[:, None] * self.T[i, 1] + d01[:, None] * p1 + d11[:, None] * self.T[i + 1, 0]) / dt[:, None]
        return np.where((lo | hi)[:, None], 0.0, d).astype(dtype)

    def _cumulative(self):
        """Integral of every component from t0 to each key (exact for the cubic pieces: Simpson per segment)."""
        if self._cum is None:
            t = self.times
            seg = np.zeros((len(t), self.dim))
            for s in range(len(t) - 1):
                a, b = t[s], t[s + 1]
                xs = np.array([a, 0.5 * (a + b), b])
                f = self._raw(xs)
                seg[s + 1] = (b - a) / 6 * (f[0] + 4 * f[1] + f[2])
            self._cum = np.cumsum(seg, axis=0)
        return self._cum

    def _raw(self, x):
        lim, self.min_limits, self.max_limits = (self.min_limits, self.max_limits), None, None
        try:
            return self.sample(x, dtype=F64)
        finally:
            self.min_limits, self.max_limits = lim

    def _cdf(self, x):
        t = self.times
        x = np.clip(x, t[0], t[-1])
        i = np.clip(np.searchsorted(t, x, side="left") - 1, 0, len(t) - 2)
        a, b = t[i], x
        f = [self._raw(a), self._raw(0.5 * (a + b)), self._raw(b)]
        return self._cumulative()[i] + (b - a)[:, None] / 6 * (f[0] + 4 * f[1] + f[2])

    def integrate(self, a, b, dtype=F32):
        """Integral from a to b (arrays), the curve counted only over its key range (zero outside), unclamped."""
        a, b = _arr(a)[:, 0], _arr(b)[:, 0]
        if not self.valid:
            return np.zeros((max(len(a), len(b)), self.dim), dtype=dtype)
        a, b = np.broadcast_arrays(a, b)
        return (self._cdf(b) - self._cdf(a)).astype(dtype)

    def key_sum(self, a, b):
        """Sum of the first component of the keys whose time lies in [a, b] (spawn bursts)."""
        m = (self.times >= a) & (self.times <= b)
        return float(self.V[m, 0].sum()) if self.valid else 0.0


class DoubleCurve:
    """Two curves blended by a 0..1 selector; curve 0's limits clamp both (as PopcornFX's double curve)."""

    def __init__(self, c0: Curve, c1: Curve):
        self.c0, self.c1, self.dim = c0, c1, c0.dim

    def sample(self, x, sel, dtype=F32):
        a, b = self.c0.sample(x, F64), self.c1.sample(x, F64)
        for c in (a, b):
            self.c0._limit(c)
        s = _arr(sel)[:, :1]
        return (a + (b - a) * s).astype(dtype)


# ------------------------------------------------------------------------------------------------------ random

class RandCtx:
    """`rand`/`vrand` of the particle scripts (uniform, not bit-exact with the engine). Deterministic per seed."""

    def __init__(self, seed=0):
        self.g = np.random.Generator(np.random.PCG64(seed))

    def uniform(self, n, k=1):
        return self.g.random((n, k))

    def rand(self, lo, hi, n=None):
        """rand(lo, hi): uniform in [lo, hi) per component; lo/hi (N, k) or scalars; n = particle count if both scalar."""
        lo, hi = _vec(lo), _vec(hi)
        k = max(lo.shape[1], hi.shape[1])
        n = max(lo.shape[0], hi.shape[0], n or 1)
        return (lo + self.uniform(n, k) * (hi - lo)).astype(F32)

    def vrand(self, lo=-1.0, hi=1.0, n=None):
        """vrand(lo, hi): random direction with radius between |lo| and |hi| (vrand(-k, k) lies ON the sphere of radius
        k, vrand(0, k) fills the ball) -> (N, 3); no arguments = unit sphere surface."""
        lo, hi = _arr(lo)[:, 0], _arr(hi)[:, 0]
        n = max(len(lo), len(hi), n or 1)
        a, b = np.minimum(lo, hi), np.maximum(lo, hi)
        p = np.where(b == 0, 0.0, (a / np.where(b == 0, 1, b) + 1) / 3)
        u = self.uniform(n, 3)
        r = a + (b - a) * np.power(u[:, 0], p)
        y = r - 2 * u[:, 1] * r
        rho = np.sqrt(np.maximum(r * r - y * y, 0))
        t = 2 * np.pi * u[:, 2]
        return np.stack([rho * np.cos(t), y, rho * np.sin(t)], 1).astype(F32)


# ------------------------------------------------------------------------------------------------------ shapes

SHAPE_TYPES = {0: "box", 1: "sphere", 2: "ellipsoid", 3: "cylinder", 4: "capsule", 5: "cone", 6: "mesh"}


def euler_matrix(deg):
    """3x3 (row-major) for euler degrees applied X, then Y, then Z (M = Rz Ry Rx), as PopcornFX-Web."""
    r = np.radians(np.asarray(deg, dtype=F64))
    cx, sx, cy, sy, cz, sz = np.cos(r[0]), np.sin(r[0]), np.cos(r[1]), np.sin(r[1]), np.cos(r[2]), np.sin(r[2])
    return np.array([[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
                     [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
                     [-sy, sx * cy, cx * cy]])


def _vnorm(v):
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.where(n == 0, 1, n)


def _sphere(u, R, Ri, volume):
    z = u[:, 0] * 2 - 1
    a = u[:, 1] * 2 * np.pi
    s = np.sqrt(np.maximum(0, 1 - z * z))
    d = np.stack([s * np.cos(a), s * np.sin(a), z], 1)
    if volume and R > Ri:
        rr = Ri + (R - Ri) * np.power(u[:, 2], (1 + Ri / R) / 3)
    else:
        rr = np.where(u[:, 2] < Ri * Ri / (R * R + Ri * Ri), Ri, R) if Ri > 0 else np.full(len(u), R)
    return d * rr[:, None], d


def _cylinder(u, R, Ri, H, volume):
    a = u[:, 0] * 2 * np.pi
    c, s = np.cos(a), np.sin(a)
    y = H * u[:, 1] - H * 0.5
    sign = np.ones(len(u))
    if volume and R > Ri:
        rr = Ri + (R - Ri) * np.power(u[:, 2], (1 + Ri / R) / 2)
    elif Ri > 0:
        inner = u[:, 2] < Ri / (R + Ri)
        rr = np.where(inner, Ri, R)
        sign = np.where(inner, -1.0, 1.0)
    else:
        rr = np.full(len(u), R)
    return np.stack([c * rr, y, s * rr], 1), np.stack([c * sign, np.zeros(len(u)), s * sign], 1)


class ShapeSampler:
    """Emission shape: box, sphere, ellipsoid, cylinder (Y axis, centred), capsule, cone (apex +Height, base y=0), mesh.

    Every sample is a pure function of three uniforms u in [0,1)^3 (a "pcoords"), so sample_position(pc)/
    sample_normal(pc) agree on one point; `rand` draws them when no pcoords are given. `volume` samples the inside,
    else the surface. Fields are the object's, in the shape's local space; then euler rotation and position are applied
    (`rotate`/`translate` off = the file's inherit flags). A `collection` picks a sub-shape by weight.
    """

    def __init__(self, type="box", box=(0.5, 0.5, 0.5), radius=1.0, inner=0.0, height=0.0, position=(0, 0, 0), euler=(0, 0, 0),
                 scale=None, hemisphere=False, volume=False, translate=True, rotate=True, mesh_scale=(1, 1, 1),
                 mesh_mode="fast", subs=None):
        self.type = type
        self.box = np.asarray(box, dtype=F64)
        R, Ri = float(radius), float(inner)
        self.radius, self.inner = (Ri, R) if R < Ri else (R, Ri)
        self.height = float(height)
        self.position = np.asarray(position, dtype=F64)
        self.euler = np.asarray(euler, dtype=F64)
        self.rot = euler_matrix(self.euler) if np.any(self.euler) else None
        self.scale = np.asarray(scale, dtype=F64) if (scale is not None and type == "ellipsoid") else None
        self.hemisphere = bool(hemisphere) and type == "ellipsoid"
        self.volume, self.translate, self.rotate = volume, translate, rotate
        self.mesh_scale = np.asarray(mesh_scale, dtype=F64)
        self.mesh_mode = mesh_mode
        self.mesh = None
        self.subs = subs                                   # [(ShapeSampler, weight)] for a collection
        if subs:
            self.type = "collection"
            self._w = np.array([max(0.0, float(w)) for _, w in subs])

    @classmethod
    def from_fields(cls, f):
        """From raw field bytes of CParticleNodeSamplerData_Shape (ids in the module docstring)."""
        v3 = lambda k, d: tuple(_floats(f[k])[:3]) if k in f else d
        t = SHAPE_TYPES.get(_int(f.get(11), 0), "box")
        return cls(type=t, box=v3(12, (0.5, 0.5, 0.5)), radius=_flt(f.get(13), 1.0), inner=_flt(f.get(14), 0.0),
                   height=_flt(f.get(16), 0.0), hemisphere=_bool(f.get(17)), scale=v3(18, None), mesh_scale=v3(20, (1, 1, 1)),
                   translate=_bool(f.get(27), True), rotate=_bool(f.get(28), True), position=v3(29, (0, 0, 0)),
                   euler=v3(30, (0, 0, 0)), volume=_int(f.get(10), 0) == 3)

    @classmethod
    def from_json(cls, d):
        d = dict(d)
        subs = [(cls.from_json(s), s.get("weight", 1.0)) for s in d.pop("subs", [])]
        d.pop("kind", None)
        return cls(subs=subs or None, **d)

    def set_mesh(self, positions, indices, normals=None):
        """Mesh surface for type "mesh": positions (V,3), triangle indices (3T,), optional normals (V,3)."""
        P = np.asarray(positions, dtype=F64).reshape(-1, 3)
        I = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
        N = None if normals is None else np.asarray(normals, dtype=F64).reshape(-1, 3)
        Q = P * self.mesh_scale
        area = 0.5 * np.linalg.norm(np.cross(Q[I[:, 1]] - Q[I[:, 0]], Q[I[:, 2]] - Q[I[:, 0]]), axis=1)
        self.mesh = (P, I, N, np.cumsum(area))
        return self

    def sample_pcoords(self, n, rand: RandCtx):
        return rand.uniform(n, 3)

    def _local(self, u):
        """(position, normal) in local space for uniforms u (N, 3)."""
        R, Ri, H, N = self.radius, self.inner, self.height, len(u)
        t = self.type
        if t == "collection":
            if not len(self._w) or not self._w.sum() > 0:
                return np.tile(self.position, (N, 1)), np.tile([0.0, 1, 0], (N, 1))
            cw = np.cumsum(self._w)
            x = u[:, 0] * cw[-1]
            k = np.minimum(np.searchsorted(cw, x, side="right"), len(cw) - 1)
            p, n = np.zeros((N, 3)), np.zeros((N, 3))
            for j, (sub, _) in enumerate(self.subs):
                m = k == j
                if m.any():
                    lo = cw[j] - self._w[j]
                    u0 = np.minimum((x[m] - lo) / self._w[j], 1 - 1e-7) if self._w[j] > 0 else np.zeros(m.sum())
                    sub.volume = self.volume
                    p[m], n[m] = sub._world(np.stack([u0, u[m, 1], u[m, 2]], 1))
            return p, n
        if t == "box":
            d = self.box
            if self.volume:
                return (u - 0.5) * d, np.tile([0.0, 1, 0], (N, 1))
            ax, ay, az = d[1] * d[2], d[0] * d[2], d[0] * d[1]
            tot = 2 * (ax + ay + az) or 1.0
            x = u[:, 0] * tot
            axis = np.where(x < 2 * ax, 0, np.where(x < 2 * ax + 2 * ay, 1, 2))
            x = np.where(axis == 0, x, np.where(axis == 1, x - 2 * ax, x - 2 * ax - 2 * ay))
            a = np.array([ax, ay, az])[axis]
            sgn = np.where(x < a, 0.5, -0.5)
            p, n = np.zeros((N, 3)), np.zeros((N, 3))
            o = np.array([[1, 2], [0, 2], [0, 1]])
            rows = np.arange(N)
            p[rows, axis] = sgn * d[axis]
            n[rows, axis] = np.sign(sgn)
            p[rows, o[axis, 0]] = (u[:, 1] - 0.5) * d[o[axis, 0]]
            p[rows, o[axis, 1]] = (u[:, 2] - 0.5) * d[o[axis, 1]]
            return p, n
        if t == "cylinder":
            return _cylinder(u, R, Ri, H, self.volume)
        if t == "capsule":
            vol = self.volume and R > Ri
            sph = 4 / 3 * (R ** 3 - Ri ** 3) if vol else 4 * (R * R + Ri * Ri)
            cyl = (R * R - Ri * Ri) * H if vol else 2 * (R + Ri) * H
            ps = sph / ((sph + cyl) or 1)
            m = u[:, 0] < ps
            p, n = np.zeros((N, 3)), np.zeros((N, 3))
            if m.any():
                uu = np.stack([u[m, 0] / ps, u[m, 1], u[m, 2]], 1)
                pp, nn = _sphere(uu, R, Ri, self.volume)
                pp[:, 1] += np.where(pp[:, 1] < 0, -H, H) * 0.5
                p[m], n[m] = pp, nn
            if (~m).any():
                uu = np.stack([(u[~m, 0] - ps) / (1 - ps), u[~m, 1], u[~m, 2]], 1)
                p[~m], n[~m] = _cylinder(uu, R, Ri, H, self.volume)
            return p, n
        if t == "cone":
            ang = u[:, 1] * 2 * np.pi
            c, s = np.cos(ang), np.sin(ang)
            if self.volume:
                sc = np.cbrt(u[:, 0])
                rr = R * sc * np.sqrt(u[:, 2])
            else:
                sc = np.sqrt(u[:, 0])
                rr = R * sc
            p = np.stack([c * rr, (1 - sc) * H, s * rr], 1)
            return p, _vnorm(np.stack([c * H, np.full(N, R), s * H], 1))
        if t == "mesh":
            if self.mesh is None:
                return np.zeros((N, 3)), np.tile([0.0, 1, 0], (N, 1))
            P, I, Nrm, cum = self.mesh
            T = len(I)
            if self.mesh_mode == "uniform" and cum[-1] > 0:
                tri = np.minimum(np.searchsorted(cum, u[:, 0] * cum[-1], side="right"), T - 1)
            else:
                tri = np.minimum(T - 1, np.floor(u[:, 0] * T).astype(np.int64))      # "Fast": by index, not by area
            s = np.sqrt(u[:, 1])
            a, b = 1 - s, s * u[:, 2]
            c = s - b
            i0, i1, i2 = I[tri, 0], I[tri, 1], I[tri, 2]
            S = self.mesh_scale
            p = (a[:, None] * P[i0] + c[:, None] * P[i1] + b[:, None] * P[i2]) * S
            if Nrm is not None:
                n = _vnorm((a[:, None] * Nrm[i0] + c[:, None] * Nrm[i1] + b[:, None] * Nrm[i2]) / np.where(S == 0, 1, S))
            else:
                n = _vnorm(np.cross(P[i1] - P[i0], P[i2] - P[i0]))
            return p, n
        p, n = _sphere(u, R, Ri, self.volume)                  # sphere, ellipsoid
        if self.hemisphere:
            neg = p[:, 1] < 0
            p[neg, 1], n[neg, 1] = -p[neg, 1], -n[neg, 1]
        if self.scale is not None:
            p = p * self.scale
            n = _vnorm(n / np.where(self.scale == 0, 1, self.scale))
        return p, n

    def _world(self, u):
        p, n = self._local(u)
        if self.rotate and self.rot is not None:
            p, n = p @ self.rot.T, n @ self.rot.T
        if self.translate:
            p = p + self.position
        return p, n

    @staticmethod
    def _pc(pc):
        return np.clip(_arr(pc, 3), 0.0, 1 - 1e-7)

    def sample_position(self, rand: RandCtx = None, n=1, pcoords=None, dtype=F32):
        u = self._pc(pcoords) if pcoords is not None else rand.uniform(n, 3)
        return self._world(u)[0].astype(dtype)

    def sample_normal(self, rand: RandCtx = None, n=1, pcoords=None, dtype=F32):
        u = self._pc(pcoords) if pcoords is not None else rand.uniform(n, 3)
        return self._world(u)[1].astype(dtype)

    def sample(self, rand: RandCtx = None, n=1, pcoords=None):
        """(position, normal) for one set of uniforms: both agree on the same points."""
        u = self._pc(pcoords) if pcoords is not None else rand.uniform(n, 3)
        p, nrm = self._world(u)
        return p.astype(F32), nrm.astype(F32)

    def to_json(self):
        d = dict(kind="shape", type=self.type, box=self.box.tolist(), radius=self.radius, inner=self.inner, height=self.height,
                 position=self.position.tolist(), euler=self.euler.tolist(), hemisphere=self.hemisphere, volume=self.volume,
                 translate=self.translate, rotate=self.rotate, mesh_scale=self.mesh_scale.tolist(), mesh_mode=self.mesh_mode)
        if self.scale is not None:
            d["scale"] = self.scale.tolist()
        if self.subs:
            d["type"] = "box"
            d["subs"] = [dict(s.to_json(), weight=w) for s, w in self.subs]
        return d

    def center(self):
        return self.position.astype(F32)


# ------------------------------------------------------------------------------------------------------ turbulence
# PopcornFX procedural turbulence: Perlin-permutation value noise over an SFMT-seeded lattice whose values rotate
# with time, evaluated in fp32 in the engine's SSE operation order. Port of PopcornFX-Web `turbulence.js` (itself read
# from the 1.13 runtime); numpy-vectorised over sample points; every operation is rounded to float32 in that order.

_P0 = [151, 160, 137, 91, 90, 15, 131, 13, 201, 95, 96, 53, 194, 233, 7, 225, 140, 36, 103, 30, 69, 142, 8, 99, 37, 240, 21, 10, 23,
       190, 6, 148, 247, 120, 234, 75, 0, 26, 197, 62, 94, 252, 219, 203, 117, 35, 11, 32, 57, 177, 33, 88, 237, 149, 56, 87, 174, 20,
       125, 136, 171, 168, 68, 175, 74, 165, 71, 134, 139, 48, 27, 166, 77, 146, 158, 231, 83, 111, 229, 122, 60, 211, 133, 230, 220,
       105, 92, 41, 55, 46, 245, 40, 244, 102, 143, 54, 65, 25, 63, 161, 1, 216, 80, 73, 209, 76, 132, 187, 208, 89, 18, 169, 200, 196,
       135, 130, 116, 188, 159, 86, 164, 100, 109, 198, 173, 186, 3, 64, 52, 217, 226, 250, 124, 123, 5, 202, 38, 147, 118, 126, 255,
       82, 85, 212, 207, 206, 59, 227, 47, 16, 58, 17, 182, 189, 28, 42, 223, 183, 170, 213, 119, 248, 152, 2, 44, 154, 163, 70, 221,
       153, 101, 155, 167, 43, 172, 9, 129, 22, 39, 253, 19, 98, 108, 110, 79, 113, 224, 232, 178, 185, 112, 104, 218, 246, 97, 228,
       251, 34, 242, 193, 238, 210, 144, 12, 191, 179, 162, 241, 81, 51, 145, 235, 249, 14, 239, 107, 49, 192, 214, 31, 181, 199, 106,
       157, 184, 84, 204, 176, 115, 121, 50, 45, 127, 4, 150, 254, 138, 236, 205, 93, 222, 114, 67, 29, 24, 72, 243, 141, 128, 195, 78,
       66, 215, 61, 156, 180]
PERM = np.array([_P0[i & 255] for i in range(512)] + [151], dtype=np.int64)

PI, TWO_PI, HALF_PI, INV_TWO_PI = _f(3.1415927), _f(6.2831855), _f(1.5707964), _f(0.15915494)
OFF1 = np.array([31.416, 47.853, 12.793], dtype=F32)
OFF2 = np.array([-233.145, -113.408, -185.31], dtype=F32)
FD_H, FD_INV = _f(1e-4), _f(5000.0)
DEFAULT_SEED = 0x4269CAFE


def sfmt_fill(seed, counts_ranges):
    """SFMT19937 (seed init + regen, no period certification) filled the way the engine does: a sequence of
    (n, min, max) float32 arrays from the raw state words (mantissa trick), 4-aligned reads."""
    s = [seed & 0xFFFFFFFF]
    for i in range(1, 624):
        p = s[i - 1]
        s.append((((p ^ (p >> 30)) * 0x6C078965) + i) & 0xFFFFFFFF)
    M = (0xDFFFFFEF, 0xDDFECB7F, 0xBFFAFFFF, 0xBFFFFFF6)
    st = {"idx": 624}
    mask = 0xFFFFFFFF

    def regen():
        r1, r2 = 154 * 4, 155 * 4
        for i in range(156):
            a, b = i * 4, ((i + 122) % 156) * 4
            a0, a1, a2, a3 = s[a:a + 4]
            c0, c1, c2, c3 = s[r1:r1 + 4]
            x = ((a0 << 8) & mask, ((a1 << 8) | (a0 >> 24)) & mask, ((a2 << 8) | (a1 >> 24)) & mask, ((a3 << 8) | (a2 >> 24)) & mask)
            y = ((c0 >> 8) | (c1 << 24)) & mask, ((c1 >> 8) | (c2 << 24)) & mask, ((c2 >> 8) | (c3 << 24)) & mask, c3 >> 8
            o = [(s[a + k] ^ x[k] ^ ((s[b + k] >> 11) & M[k]) ^ y[k] ^ ((s[r2 + k] << 18) & mask)) & mask for k in range(4)]
            s[a:a + 4] = o
            r1, r2 = r2, a

    outs = []
    for n, lo, hi in counts_ranges:
        rng_ = _f(_f(hi) - _f(lo))
        base = _f(_f(lo) - rng_)
        out = np.empty(n, dtype=F32)
        w = 0
        while w < n:
            start = (st["idx"] + 3) & ~3
            avail = 624 - start
            if avail < 1:
                regen()
                start, avail = 0, 624
            take = min(avail, n - w)
            bits = (np.array(s[start:start + take], dtype=np.uint32) & 0x7FFFFF) | 0x3F800000
            out[w:w + take] = (bits.view(F32) * rng_) + base
            w += take
            st["idx"] = start + take
        outs.append(out)
    return outs


def _cvtt(x):
    ok = (x >= -2147483648.0) & (x < 2147483648.0)
    return np.where(ok, np.trunc(np.where(ok, x, 0)), -2147483648.0)


def fast_sin(x):
    """Engine fast sine (FUN_1804de760), fp32."""
    x = np.asarray(x, dtype=F32)
    a = x * INV_TWO_PI
    fr = np.abs((a.astype(F64) - _cvtt(a.astype(F64))).astype(F32))
    y = (fr + fr) - F32(1)
    y = np.where(np.signbit(a), -y, y).astype(F32)
    A = y * F32(-4)
    A = A - np.abs(y) * A
    return ((np.abs(A) * A - A) * _f(0.22400817) + A).astype(F32)


def fast_asin(x):
    """Engine fast asin (FUN_1804d93a0), fp32."""
    x = np.asarray(x, dtype=F32)
    ax = np.minimum(np.abs(x), F32(1))
    s = np.sqrt(F32(1) - ax).astype(F32)
    p = ax * _f(-0.0043601324) + _f(0.019467467)
    for c in (-0.045130268, 0.08797309, -0.21453293):
        p = p * ax + _f(c)
    p = p * ax + HALF_PI
    r = s * p
    r = np.where(np.signbit(x), PI - r, r).astype(F32)
    return (HALF_PI - r).astype(F32)


@lru_cache(maxsize=64)
def _lattice(seed):
    """(V0 lattice values, base angles, angular speeds at the default time variation) for a seed."""
    V0, speed = sfmt_fill(seed, [(256, -1, 1), (256, float(_f(TWO_PI - _f(_f(0.2) * PI))), float(_f(_f(_f(0.2) * PI) + TWO_PI)))])
    base = fast_asin(V0).copy()
    base[1::2] = base[1::2] + PI
    return V0, base.astype(F32), speed


@lru_cache(maxsize=64)
def _lattice_d2r(seed):
    """D2R.exe 2.9's lattice (0xb3c530 / 0xb3ef70 / 0xc44600): the same SFMT draws, then the odd entries negated (xor
    with the mask (0, 0x80000000) x 2 at 0x1cc1ac0) and base = fast_asin of the negated values (no + pi)."""
    V0, _, speed = _lattice(seed)
    V = V0.copy()
    V[1::2] = -V[1::2]
    return V, fast_asin(V).astype(F32), speed


class RotatableNoise:
    """CRotatableFastNoise3: 256 lattice values rotating with time, `V` (513, duplicated tail) for the current phase.
    lattice "d2r" (default): as D2R.exe 2.9 builds it (`_lattice_d2r`): value i at phase p is fast_sin(p x speed_i +
    base_i), phase 0 the built lattice. "web113": PopcornFX-Web 1.13's (odd bases + pi, phase 0 = the raw draws)."""

    def __init__(self, seed=DEFAULT_SEED, time_random_variation=0.5, lattice="d2r"):
        V0, self.base, speed = (_lattice_d2r if lattice == "d2r" else _lattice)(int(seed) & 0xFFFFFFFF)
        self.speed = speed.copy()
        self.V0 = V0
        self.trv = _f(0.2)
        self.uniform = False
        self.time = _f(0)
        self.V = self._dup(V0)
        v = _f(time_random_variation)
        if v < 1e-5:
            self.uniform = True
        else:
            old = _f(self.trv * PI)
            self.trv = v
            vp = _f(v * PI)
            low_new, k, low_old = _f(TWO_PI - vp), _f(vp / old), _f(TWO_PI - old)
            self.speed = (((self.speed - low_old) * k) + low_new).astype(F32)

    @staticmethod
    def _dup(v256):
        V = np.empty(513, dtype=F32)
        V[:256], V[256:512], V[512] = v256, v256, v256[0]
        return V

    def set_time(self, phase):
        phase = _f(phase)
        if phase == self.time:
            return
        self.time = phase
        if phase == 0:                         # phase 0 is the raw lattice, whatever was sampled before (pure in time)
            v = self.V0
        elif not self.uniform:
            v = fast_sin(self.speed * phase + self.base)
        else:
            v = fast_sin(self.base + _f(phase * TWO_PI))
        self.V = self._dup(v)


def _cell(x):
    x = np.asarray(x, dtype=F32)
    ti = _cvtt(x.astype(F64))
    lt = x.astype(F64) < ti
    i = (ti.astype(np.int64) + np.where(lt, -1, 0)) & 255
    fr = (np.where(lt, 1.0, 0.0) + (x.astype(F64) - ti).astype(F32).astype(F64)).astype(F32)
    return i, fr


def _fade_s(t, k):                       # FUN_1807e99d0 / e84c0 / eb180
    if k == 0:
        return t
    if k == 1:
        return (t * t) * (F32(3) - (t + t))
    return ((t * t) * t) * ((t * ((t * F32(6)) - F32(15))) + F32(10))


def _fade_4(t, k):                       # FUN_1807e2410 / e2240 / e25a0
    if k == 0:
        return t
    if k == 1:
        return ((F32(3) - (t + t)) * t) * t
    return ((t * t) * t) * ((((t * F32(6)) - F32(15)) * t) + F32(10))


def _corners(V, X, Y, Z):
    A = PERM[X] + Y
    B = PERM[X + 1] + Y
    AA, AB, BA, BB = PERM[A] + Z, PERM[A + 1] + Z, PERM[B] + Z, PERM[B + 1] + Z
    return [V[AA], V[BA], V[AB], V[BB], V[AA + 1], V[BA + 1], V[AB + 1], V[BB + 1]]    # c000 c100 c010 c110 c001 c101 c011 c111


def _lerp(a, b, t):
    return ((b - a) * t) + a


def _trilerp(c, u, v, w):
    x00, x01 = _lerp(c[0], c[1], u), _lerp(c[4], c[5], u)
    y0 = _lerp(x00, _lerp(c[2], c[3], u), v)
    y1 = _lerp(x01, _lerp(c[6], c[7], u), v)
    return _lerp(y0, y1, w)


def noise1(V, x, y, z, k=1):
    """Scalar lattice noise at fp32 coordinates (arrays) with interpolator k (0 linear, 1 cubic, 2 quintic)."""
    (ix, fx), (iy, fy), (iz, fz) = _cell(x), _cell(y), _cell(z)
    return _trilerp(_corners(V, ix, iy, iz), _fade_s(fx, k), _fade_s(fy, k), _fade_s(fz, k))


def noise4(V, pts, k=1):
    """Four points evaluated in the lattice cell of pts[0] (the finite-difference curl); pts: list of (x, y, z)."""
    p0 = pts[0]
    (ix, fx), (iy, fy), (iz, fz) = _cell(p0[0]), _cell(p0[1]), _cell(p0[2])
    c = _corners(V, ix, iy, iz)
    out = []
    for p in pts:
        lx, ly, lz = (p[0] - p0[0]) + fx, (p[1] - p0[1]) + fy, (p[2] - p0[2]) + fz
        out.append(_trilerp(c, _fade_4(lx, k), _fade_4(ly, k), _fade_4(lz, k)))
    return out


def grad1(V, x, y, z, k=1):
    """Analytic gradient of noise1 w.r.t. its coordinates (FUN_1807e3140 family) -> (dx, dy, dz)."""
    (ix, fx), (iy, fy), (iz, fz) = _cell(x), _cell(y), _cell(z)
    c000, c100, c010, c110, c001, c101, c011, c111 = _corners(V, ix, iy, iz)

    def fu(t):
        if k == 0:
            return t
        if k == 1:
            return ((F32(3) - (t + t)) * t) * t
        return (((((t * F32(6)) - F32(15)) * t) + F32(10)) * (t * t)) * t

    def df(t):
        if k == 0:
            return F32(1)
        if k == 1:
            s = t * F32(6)
            return s - (s * t)
        t2 = t * t
        return (((t2 - (t + t)) * t2) * F32(30)) + (t2 * F32(30))

    u, v, w = fu(fx), fu(fy), fu(fz)
    k1, k2, k3 = c100 - c000, c010 - c000, c001 - c000
    t14, t16, t15 = c011 - c001, c110 - c010, c101 - c100
    kxy, kyz, kxz = t16 - k1, t14 - k2, t15 - k3
    m = (((c000 - c111) + t16) + t14) + t15
    dx = ((((kxz * w) - ((m * w) * v)) + (v * kxy)) + k1) * df(fx)
    dy = ((((kxy * u) - ((m * u) * w)) + (w * kyz)) + k2) * df(fy)
    dz = ((((kyz * v) - ((m * v) * u)) + (u * kxz)) + k3) * df(fz)
    return dx, dy, dz


# Field ids of CParticleNodeSamplerData_Turbulence -> (parameter, type), read from D2R.exe (lane vmfix): the class
# registers its properties at 0xb0ba80 in field order after the base fields (0..9): GlobalScale 10 (+0x17c), Strength
# 11 (+0x180), DataSource 12, VectorFieldResource 13, WrapSide/Vertical/Depth/Time 14..17, Filtering 18, Wavelength 19
# (+0x198), Octaves 20, Lacunarity 21, Gain 22, Interpolator 23 (+0x1a8), TimeScale 24 (+0x1ac), TimeBase 25 (+0x1b0),
# TimeRandomVariation 26 (+0x1b4), FlowFactor 27, DivergenceFactor 28, InitialSeed 29, FastFakeFlow 30 (bool),
# GainMultiplier 31 (+0x1c8). The sampler reads them at those offsets (0xb0e480 octaves, 0xba7710 phase =
# t x [+0x1ac] + [+0x1b0], 0xb3edf0 time variation [+0x1b4]). Ids 12..18 (external .fga fields) are not used here.
TURB_FIELDS = {10: ("global_scale", "f"), 11: ("strength", "f"), 19: ("wavelength", "f"), 20: ("octaves", "i"),
               21: ("lacunarity", "f"), 22: ("gain", "f"), 23: ("interpolator", "i"), 24: ("time_scale", "f"),
               25: ("time_base", "f"), 26: ("time_random_variation", "f"), 27: ("flow_factor", "f"),
               28: ("divergence_factor", "f"), 29: ("initial_seed", "i"), 30: ("fast_fake_flow", "b"),
               31: ("gain_multiplier", "f")}
TURB_DEFAULTS = dict(global_scale=1.0, strength=0.1, wavelength=0.5, octaves=2, lacunarity=0.5, gain=0.5, interpolator=1,
                     time_scale=0.0, time_base=0.0, time_random_variation=0.5, flow_factor=1.0, divergence_factor=0.0,
                     initial_seed=DEFAULT_SEED, fast_fake_flow=False, gain_multiplier=1.0, default_field="curl",
                     lattice="d2r")
INTERPOLATORS = {"linear": 0, "cubic": 1, "quintic": 2}


class TurbulenceSampler:
    """Procedural turbulence vector field (curl by default, or potential). Parameters default to PopcornFX 1.13's."""

    def __init__(self, **params):
        p = dict(TURB_DEFAULTS)
        p.update({k: v for k, v in params.items() if v is not None})
        if isinstance(p["interpolator"], str):
            p["interpolator"] = INTERPOLATORS[p["interpolator"].lower()]
        self.p = p
        self.interp = int(p["interpolator"])
        self.noise = RotatableNoise(int(p["initial_seed"]), p["time_random_variation"], p["lattice"])
        self._octaves()

    @classmethod
    def from_fields(cls, f, field_map=None, **over):
        params = {}
        for fid, (name, ty) in (field_map or TURB_FIELDS).items():
            if fid in f:
                params[name] = {"f": _flt, "i": _int, "b": _bool}[ty](f[fid])
        params.update(over)
        return cls(**params)

    @classmethod
    def from_json(cls, d):
        return cls(**{k: v for k, v in d.items() if k != "kind"})

    def _octaves(self):
        p = self.p
        N = max(0, int(p["octaves"]))
        wl = _f(_f(p["global_scale"]) * _f(p["wavelength"]))
        freq0 = _f(F32(1) / wl) if wl != 0 else _f(np.inf)
        try:
            lac_min = _f(math.pow(float(_f(freq0 * _f(1e-7))), float(_f(1 / N)))) if N else _f(0)
        except (OverflowError, ValueError, ZeroDivisionError):
            lac_min = _f(0)
        lac = lac_min if _f(p["lacunarity"]) <= lac_min else _f(p["lacunarity"])
        amp = _f(_f(p["global_scale"]) * _f(p["strength"]))
        gain = _f(_f(p["gain_multiplier"]) * _f(p["gain"]))
        self.oct = []
        if lac == 1 or gain == 0:
            s = _f(0)
            for _ in range(N):
                s = _f(s + amp)
                amp = _f(amp * gain)
            if N:
                self.oct.append((freq0, s))
        else:
            for _ in range(min(N, 24)):
                self.oct.append((_f(F32(1) / wl), amp))
                wl = _f(wl * lac)
                amp = _f(amp * gain)

    def _set_time(self, t):
        self.noise.set_time(_f(_f(_f(t) * _f(self.p["time_scale"])) + _f(self.p["time_base"])))

    @staticmethod
    def _pos(pos):
        a = _arr(pos, 3, F32).astype(F32)
        return a[:, 0], a[:, 1], a[:, 2]

    def _potential(self, x, y, z):
        V, k = self.noise.V, self.interp
        out = []
        for off in (None, OFF1, OFF2):
            acc = np.zeros(len(x), dtype=F32)
            for fq, amp in self.oct:
                q = [x * fq, y * fq, z * fq]
                if off is not None:
                    q = [q[0] + off[0], q[1] + off[1], q[2] + off[2]]
                acc = acc + noise1(V, *q, k) * amp
            out.append(acc)
        return out

    def _curl_analytic(self, x, y, z):
        V, k = self.noise.V, self.interp
        G = [[np.zeros(len(x), dtype=F32) for _ in range(3)] for _ in range(3)]
        for fq, amp in self.oct:
            q = (x * fq, y * fq, z * fq)
            for s, off in enumerate((None, OFF1, OFF2)):
                qs = q if off is None else (q[0] + off[0], q[1] + off[1], q[2] + off[2])
                g = grad1(V, *qs, k)
                for a in range(3):
                    G[s][a] = G[s][a] + g[a] * amp
        d2 = lambda a, b: (a - b) + (a - b)
        return [d2(G[2][1], G[1][2]), d2(G[0][2], G[2][0]), d2(G[1][0], G[0][1])]

    def _curl_fd(self, x, y, z):
        V, k, h = self.noise.V, self.interp, FD_H
        Z3 = (F32(0), F32(0), F32(0))
        deltas = {"x": ((0, 0, h), (0, 0, -h), (0, h, 0), (0, -h, 0)), "y": ((h, 0, 0), (-h, 0, 0), (0, 0, h), (0, 0, -h)),
                  "z": ((0, h, 0), (0, -h, 0), (h, 0, 0), (-h, 0, 0))}
        accs = {}
        for key, off in (("x", Z3), ("y", OFF1), ("z", OFF2)):
            s = [np.zeros(len(x), dtype=F32) for _ in range(4)]
            for fq, amp in self.oct:
                pts = [(fq * (x + dx) + off[0], fq * (y + dy) + off[1], fq * (z + dz) + off[2]) for dx, dy, dz in deltas[key]]
                n = noise4(V, pts, k)
                s = [s[i] + n[i] * amp for i in range(4)]
            accs[key] = s
        X, Y, Zs = accs["x"], accs["y"], accs["z"]
        return [((Zs[0] - Zs[1]) + (Y[3] - Y[2])) * FD_INV, ((Zs[3] - Zs[2]) + (X[0] - X[1])) * FD_INV,
                ((Y[0] - Y[1]) + (X[3] - X[2])) * FD_INV]

    def _fake_flow(self, x, y, z):
        V, k, e = self.noise.V, self.interp, _f(FD_H * 2)
        P4 = [(x, y, z), (x + e, y, z), (x, y + e, z), (x, y, z + e)]
        s = [np.zeros(len(x), dtype=F32) for _ in range(4)]
        for fq, amp in self.oct:
            n = noise4(V, [((p[0] * fq) + OFF1[0], (p[1] * fq) + OFF1[1], (p[2] * fq) + OFF1[2]) for p in P4], k)
            s = [s[i] + n[i] * amp for i in range(4)]
        return [FD_INV * (s[1] - s[0]), FD_INV * (s[2] - s[0]), FD_INV * (s[3] - s[0])]

    def _each_time(self, pos, time, fn):
        x, y, z = self._pos(pos)
        n = len(x)
        t = np.broadcast_to(_arr(0.0 if time is None else time)[:, 0], (n,)) if n else np.zeros(0)
        out = np.zeros((n, 3), dtype=F32)
        for tv in np.unique(t):
            m = t == tv
            self._set_time(tv)
            r = fn(x[m], y[m], z[m])
            out[m] = np.stack(r, 1)
        return out

    def sample_curl(self, pos, time=0.0):
        """Curl field at pos (N, 3), time (scalar or (N,)) -> (N, 3) float32 (descriptor mode 1)."""
        p = self.p
        F, D = _f(p["flow_factor"]), _f(p["divergence_factor"])

        def fn(x, y, z):
            if p["fast_fake_flow"]:
                return self._fake_flow(x, y, z)
            if F == 1 and D == 0:
                return self._curl_analytic(x, y, z)
            A, B = self._curl_fd(x, y, z), self._potential(x, y, z)
            return [(A[i] * F) + ((B[i] - A[i]) * D) for i in range(3)]
        return self._each_time(pos, time, fn)

    def sample_potential(self, pos, time=0.0):
        return self._each_time(pos, time, self._potential)

    def sample(self, pos, time=0.0):
        return self.sample_curl(pos, time) if self.p["default_field"] != "potential" else self.sample_potential(pos, time)


# ------------------------------------------------------------------------------------------------------ script noise
# `noise(x)`, `noise(float3)`, `fast.noise(x)` script builtins: the engine's own kernels, bit-exact (fx/noise.py).

def value_noise(p, dtype=F32):
    """`noise(p)`: a column -> 1D simplex noise (noise_float), three columns -> 3D simplex noise (noise_float3)."""
    a = _arr(p, None, F32).astype(F32)
    out = noise_float3(a) if a.shape[1] >= 3 else noise_float(a[:, 0])
    return out.astype(dtype)


# ------------------------------------------------------------------------------------------------------ factory, engine calls

def make(kind, source):
    """Sampler from graph data: kind "curve"|"shape"|"turbulence"; source = JSON dict or raw field bytes {id: bytes}."""
    raw = bool(source) and all(isinstance(k, int) for k in source)
    cls = {"curve": Curve, "shape": ShapeSampler, "turbulence": TurbulenceSampler}[kind]
    return cls.from_fields(source) if raw else cls.from_json(source)


_default_rand = RandCtx(0)


def _rand(ctx):
    r = getattr(ctx, "rand", None) or getattr(ctx, "rng", None)
    return r if hasattr(r, "vrand") else (ctx if hasattr(ctx, "vrand") else _default_rand)


def _count(ctx, *args):
    n = getattr(ctx, "count", None) or getattr(ctx, "n", None)
    return int(n) if n else max([np.atleast_1d(a).shape[0] for a in args if a is not None] + [1])


def _s(x):
    return getattr(x, "sampler", x)


def _curve_sample(ctx, sampler, t):
    return _s(sampler).sample(t)


def _curve_derive(ctx, sampler, t):
    return _s(sampler).derive(t)


def _vf_sample(ctx, sampler, pos, time=None):
    """`sample(pos)` / `sample(pos, t)`. A turbulence sampler ignores t: D2R.exe's sample_float3_float entry (0xba7320 ->
    vtable +0x10 = 0xba76f0) replaces the time array by the scene time ((float) of the double at SceneCtx +0x108), the
    one `sample(pos)` uses (0xba6f70); ctx.time is that scene time."""
    smp = _s(sampler)
    if isinstance(smp, TurbulenceSampler):
        time = None
    return smp.sample(pos, getattr(ctx, "time", 0.0) if time is None else time)


def _shape_position(ctx, sampler, pcoords=None):
    return _s(sampler).sample_position(_rand(ctx), _count(ctx, pcoords), pcoords=pcoords)


def _shape_normal(ctx, sampler, pcoords=None):
    return _s(sampler).sample_normal(_rand(ctx), _count(ctx, pcoords), pcoords=pcoords)


def _shape_pcoords(ctx, sampler):
    return _s(sampler).sample_pcoords(_count(ctx), _rand(ctx)).astype(F32)


def _rand_call(ctx, lo, hi):
    return _rand(ctx).rand(lo, hi, _count(ctx, lo, hi))


def _vrand_call(ctx, lo=-1.0, hi=1.0):
    return _rand(ctx).vrand(lo, hi, _count(ctx, lo, hi))


ENGINE_CALLS = {f"samplerCurve{d}:sample_float": _curve_sample for d in (1, 2, 3, 4)}
ENGINE_CALLS.update({f"samplerCurve{d}:derive_float_pCtxI": _curve_derive for d in (1, 2, 3, 4)})
ENGINE_CALLS.update({
    "samplerVectorField:sample_float3_float_SceneCtx": _vf_sample,
    "samplerVectorField:sample_float3_SceneCtx": _vf_sample,
    "samplerShape:samplePosition_RandCtx": _shape_position,
    "samplerShape:samplePosition_int3": _shape_position,
    "samplerShape:sampleNormal_RandCtx": _shape_normal,
    "samplerShape:sampleNormal_int3": _shape_normal,
    "samplerShape:samplePCoords_RandCtx": _shape_pcoords,
    "rand_float_float_RandCtx": _rand_call,
    "rand_float3_float3_RandCtx": _rand_call,
    "rand_float4_float4_RandCtx": _rand_call,
    "vrand_float_float_RandCtx": _vrand_call,
    "vrand_RandCtx": _vrand_call,
    "noise_float": lambda ctx, x: noise_float(_arr(x, None, F32)[:, 0]),
    "noise_float3": lambda ctx, p: noise_float3(_arr(p, None, F32)),
    "fast.noise_float": lambda ctx, x: fast_noise_float(_arr(x, None, F32)[:, 0]),
})


def register(engine: dict):
    """Install the sampler/rand/noise calls into a vm engine registry (`vm.ENGINE`); returns it."""
    engine.update(ENGINE_CALLS)
    return engine
