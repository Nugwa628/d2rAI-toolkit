"""The game's "Universal CLUT" built from game data (lane hdfx-r3-lighting2): D2R's post pass (tone.clut) looks the exposed
scene up in a 65^3 LUT that the compute shader cs_lut_combine_c builds from the vis file's grading LUT
(`gradingLutFile`, a 65^3 RGBA16F volume .dds in data/hd/env/lut). Read from the shader (D2R build 95032,
data/hd/shaders/cs_lut_combine_c.bin, DXBC -> SPIR-V -> GLSL with vkd3d / SPIRV-Cross; permutations FINAL_LUT_COMBINE 0/1):

- FINAL_LUT_COMBINE=0 ("Combined LUT 0/1"): texel-wise a x LUT_a + b x LUT_b (cb value[0].xy): the two vis files of the
  time of day blended (blend_grading).
- FINAL_LUT_COMBINE=1 ("Universal CLUT"): for every texel (r, g, b) = index / 64 as PQ: decode to linear (x 125: 1 = 80
  nits), BT.709 -> AP1, a highlight desaturation towards a wider gamut, a filmic tone scale of the max channel (toe power
  curve, linear middle, shoulder power curve; params a = cb66.y, k = cb67), back to BT.709 (+ a small matrix), PQ
  encode, **the grading LUT sampled there (trilinear)**, PQ decode, an LMS white balance (cb35-37) and blue shift
  (cb47.y), ICtCp with the BT.2390 EETF towards the target peak (cb52.x nits) and a chroma scale, back to BT.709, and the
  display encode (SDR: sRGB with exponent 1 / cb52.z and slope cb49.w; HDR: PQ).

`universal(grading, params)` is that shader in numpy; the default params are the constants of the captured dispatch
(PARAMS). Checks: against the shader itself run on the same constants (lane r3-lighting2's GLSL interpreter, max 0.002 on
an identity and on a random grading LUT), and against the Universal CLUT of the same capture (built from default.dds:
mean 0.00044, max 0.0074, the capture stores half floats). default.dds is an exact identity, so the default build is the
tone curve alone; other grading LUTs (act3_kurast_day_1.act3_day.dds, act2/lut_act2_town01.dds, ui_lobby.dds,
characterCreate_1.characterCreate.dds: the five every vis file uses) are composed in the same place as in the game.
"""
from __future__ import annotations

import numpy as np

N = 65
_M1, _M2 = 78.84375, 0.1593017578125
_C1, _C2, _C3 = 0.8359375, 18.8515625, 18.6875

M709_AP1 = np.array([[0.61319148540496826, 0.33951207995414734, 0.04736633226275444],
                     [0.07020690292119980, 0.91633582115173340, 0.01345001161098480],
                     [0.02061887085437775, 0.10956729203462601, 0.86960673332214355]])
AP1_Y = np.array([0.272228717803955078, 0.674081742763519287, 0.053689517080783844])
M_WIDE = np.array([[1.37041270732879639, -0.32929131388664246, -0.06368276476860046],
                   [-0.08343418687582016, 1.09709095954895020, -0.01086157280951738],
                   [-0.02579325810074806, -0.09862563759088516, 1.20369434356689453]])
M_AP1_709 = np.array([[1.02579927444458008, -0.02005250938236713, -0.00577136781066656],
                      [-0.00223502493463457, 1.00458264350891113, -0.00235231337137520],
                      [-0.00501400325447321, -0.02529338747262955, 1.03044021129608154]])
RGB_LMS = np.array([[0.41210898756980896, 0.52392601966857910, 0.06396479904651642],
                    [0.16674800217151642, 0.72045898437500000, 0.11279299855232239],
                    [0.02416989952325821, 0.07543949782848358, 0.90039098262786865]])
LMS_RGB = np.array([[3.43660998344421387, -2.50644993782043457, 0.06984540075063705],
                    [-0.79132997989654541, 1.98360002040863037, -0.19227099418640137],
                    [-0.02594989910721779, -0.09891369938850403, 1.12486004829406738]])
M_OUT = np.array([[1.66053164005279541, -0.58764261007308960, -0.07284004986286163],
                  [-0.12455356121063232, 1.13290250301361084, -0.00834836345165968],
                  [-0.01815120130777359, -0.10057908296585083, 1.11858057975769043]])

# cb10 of the captured FINAL_LUT_COMBINE=1 dispatch (r3-calibfix r4 frame 4268, "LUT Generation / Create CLUT" eid 9709,
# root constant block 0; Blood Moor morning, SDR): the shader reads exactly these words
PARAMS = {
    "wb": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],   # cb35..37 xyz: LMS white balance (identity)
    "blue_shift": 1.0,                                          # cb47.y (no effect with an identity white balance)
    "srgb_slope": 12.92,                                        # cb49.w
    "hdr_scale": 200.0,                                         # cb50.w (x 0.0125 in HDR)
    "hdr": False,                                               # cb51.w != 0
    "pq_out": False,                                            # cb34.w == 1 (HDR output as PQ)
    "peak_nits": 250.0,                                         # cb52.x: the EETF's target peak
    "gamma": 2.4,                                               # cb52.z
    "tone_a": 125.0,                                            # cb66.y
    "tone_k": [1e-4, 1e-4, 0.77, 0.9],                          # cb67 xyzw
}
PARAMS_SOURCE = "RenderDoc r3-calibfix r4 frame 4268, Create CLUT eid 9709 (SDR, Blood Moor)"


def identity() -> np.ndarray:
    """The identity grading LUT (default.dds is exactly this: texel (r, g, b) holds index / 64)."""
    g = np.arange(N, dtype=np.float32) / 64.0
    b_, g_, r_ = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r_, g_, b_], -1)


def pq_decode(x):
    p = np.abs(np.asarray(x, np.float64)) ** (1.0 / _M1)
    return (np.maximum(p - _C1, 0.0) / (_C2 - _C3 * p)) ** (1.0 / _M2)


def pq_encode(y):
    p = np.abs(np.asarray(y, np.float64)) ** _M2
    return ((_C1 + _C2 * p) / (1.0 + _C3 * p)) ** _M1


def read_grading_lut(data: bytes) -> np.ndarray:
    """A grading LUT .dds (65^3 RGBA16F volume, D3DFMT 113) -> (65, 65, 65, 3) float32 indexed [b, g, r]."""
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS")
    h, w, _pitch, d = np.frombuffer(data, "<u4", 4, 12)
    fourcc = int(np.frombuffer(data, "<u4", 1, 84)[0])
    if fourcc != 113 or (h, w, d) != (N, N, N):
        raise ValueError("grading LUT: want a 65^3 RGBA16F volume, got %dx%dx%d fourcc %d" % (w, h, d, fourcc))
    return np.frombuffer(data, np.float16, N * N * N * 4, 128).reshape(N, N, N, 4)[..., :3].astype(np.float32)


def blend_grading(a, b=None, w: float = 0.0) -> np.ndarray:
    """FINAL_LUT_COMBINE=0: (1 - w) a + w b (the two vis files of the time of day)."""
    a = np.asarray(a, np.float32)
    return a if b is None or w <= 0.0 else (1.0 - w) * a + w * np.asarray(b, np.float32)


def _sample3(lut, uvw):
    from scipy.ndimage import map_coordinates
    c = np.clip(np.asarray(uvw, np.float64) * N - 0.5, 0.0, N - 1.0)
    return np.stack([map_coordinates(lut[..., k], [c[..., 2], c[..., 1], c[..., 0]], order=1, mode="nearest")
                     for k in range(3)], -1)


def _tone(m, a, k):
    """The filmic tone scale of the max channel m (cb66.y = a, cb67 = k): toe A m^B below a kx, a line of slope s up to
    a kz, a shoulder a - D (a - m)^C above."""
    kx, ky, kz, kw = (float(v) for v in k)
    with np.errstate(all="ignore"):
        s = (kw - ky) / (kz - kx)
        B = kx * s / ky if a * ky > 0 else 0.0
        lnA = (np.log(a * ky) - B * np.log(a * kx)) if a * kx > 0 else 1.0
        toe = np.where(m > 0, np.exp(B * np.log(np.maximum(m, 1e-30)) + lnA), 0.0)
        lin = s * m + (a * ky - a * kx * s)
        C = s * (1 - kz) / (1 - kw) if kw < 1 else s * a * (1 - kz)
        lnD = (np.log(a * (1 - kw)) - C * np.log(a * (1 - kz))) if kw < 1 else -C * np.log(a * (1 - kz))
        sh = a - np.exp(C * np.log(np.maximum(a - m, 0.0)) + lnD)
        sh = sh if (kz < 1 and kw < 1) else np.full_like(m, a * kw)
        out = np.where(m > a * kx, lin, toe)
        return np.where(m > a * kz, sh, out)


def _eetf(I, maxl):
    ks = 1.5 * maxl - 0.5
    Ic = np.minimum(I, 1.0)
    with np.errstate(all="ignore"):
        t = (I - ks) / (1.0 - ks)
    h = (2 * t ** 3 - 3 * t ** 2 + 1) * ks + (1.0 - ks) * (t + t ** 3 - 2 * t ** 2) + (-2 * t ** 3 + 3 * t ** 2) * maxl
    E = np.where(Ic >= ks, h, Ic)
    E = E + (1.0 - E) ** 4 * 7.3095662855848786e-07
    E = np.where(I >= 1.0, maxl, E)
    return np.where(0.0 >= I, 7.3095662855848786e-07, E)


def universal(grading=None, params=None, rgb=None) -> np.ndarray:
    """cs_lut_combine_c FINAL_LUT_COMBINE=1: the Universal CLUT (65, 65, 65, 3) [b, g, r] of a grading LUT ((65, 65,
    65, 3) [b, g, r]; None: identity) with the shader's constants `params` (PARAMS). `rgb` (..., 3) PQ coordinates
    instead of the grid (tests)."""
    P = dict(PARAMS, **(params or {}))
    if rgb is None:
        g = np.arange(N) / 64.0
        b_, g_, r_ = np.meshgrid(g, g, g, indexing="ij")
        x = np.stack([r_, g_, b_], -1)
    else:
        x = np.asarray(rgb, np.float64)
    x = np.minimum(x, 1.0)
    L = pq_decode(x) * 125.0
    r1 = L @ M709_AP1.T
    Y = r1 @ AP1_Y
    y = np.maximum(Y, 1e-5)
    c = r1 / y[..., None] - 1.0
    w = (1.0 - np.exp2(-4.0 * Y * Y)) * (1.0 - np.exp2(-4.0 * (c * c).sum(-1)))
    v = r1 + w[..., None] * (r1 @ M_WIDE.T - r1)
    m = v.max(-1)
    with np.errstate(all="ignore"):
        n = np.where((m > 0)[..., None], v / m[..., None], 0.0)
    v = n * _tone(m, float(P["tone_a"]), P["tone_k"])[..., None]
    q = pq_encode((v @ M_AP1_709.T) * 0.008)
    # the game samples the grading LUT even when it is default.dds (an identity): texel centres hold index / 64, so the
    # lookup maps u to (65 u - 0.5) / 64 there; skipping it is 0.3 off on saturated colours
    s = _sample3(identity() if grading is None else np.asarray(grading, np.float32), q)
    L = pq_decode(s) * 125.0
    lms = L @ RGB_LMS.T
    W = np.asarray(P["wb"], np.float64)                      # rows: cb35, cb36, cb37 (xyz)
    wb = lms[..., 0:1] * W[0] + lms[..., 1:2] * W[1] + lms[..., 2:3] * W[2]
    d = L - wb @ LMS_RGB.T
    d = np.stack([np.zeros_like(d[..., 0]), d[..., 1] + 0.7 * d[..., 0], d[..., 2] + 0.7 * d[..., 0]], -1)
    o = L + float(P["blue_shift"]) * d
    if P["hdr"]:
        o = o * float(P["hdr_scale"]) * 0.0125
    maxl = float(pq_encode(float(P["peak_nits"]) * 1e-4))
    t = pq_encode((o @ RGB_LMS.T) * 0.008)
    I = 0.5 * (t[..., 0] + t[..., 1])
    Ct = 1.61376953125 * t[..., 0] - 3.323486328125 * t[..., 1] + 1.709716796875 * t[..., 2]
    Cp = 4.378173828125 * t[..., 0] - 4.24560546875 * t[..., 1] - 0.132568359375 * t[..., 2]
    I2 = _eetf(I, maxl)
    with np.errstate(all="ignore"):
        z, x_ = I / I2, I2 / I
    a = np.where(np.isnan(z), x_, np.where(np.isnan(x_), z, np.minimum(x_, z)))
    k = a ** 4 * np.abs(a)
    Ct, Cp = Ct * k, Cp * k
    lp = np.stack([I2 + 0.008609036915004253 * Ct + 0.11102962493896484 * Cp,
                   I2 - 0.008609036915004253 * Ct - 0.11102962493896484 * Cp,
                   I2 + 0.560031354427337646 * Ct - 0.32062718272209168 * Cp], -1)
    rgb_o = (pq_decode(lp) * 125.0) @ LMS_RGB.T
    o = np.maximum(rgb_o @ M_OUT.T, 0.0)
    if P["hdr"]:
        return (pq_encode(o * 0.008) if P["pq_out"] else o).astype(np.float32)
    with np.errstate(all="ignore"):
        enc = np.where(o < 0.0031308, o * float(P["srgb_slope"]),
                       1.055 * np.power(o, 1.0 / float(P["gamma"])) - 0.055)
    return enc.astype(np.float32)


def for_vis(fs, lut_a: str, lut_b: str | None = None, w: float = 0.0, params=None) -> np.ndarray:
    """The Universal CLUT of a vis file's grading LUT (gradingLutFile), or of two blended by w (FINAL_LUT_COMBINE=0).
    In the r4 capture the two combine passes had weights (0, 0) then (0, 1): the final grading LUT was the current vis
    file's alone (no time-of-day blend of LUTs seen), so callers pass one file."""
    a = read_grading_lut(fs.read(lut_a))
    b = read_grading_lut(fs.read(lut_b)) if lut_b and lut_b != lut_a and w > 0 else None
    return universal(blend_grading(a, b, w), params)


_BUILT = {}


def register(fs, lut: str, params=None) -> str:
    """Builds the Universal CLUT of grading LUT `lut` (logical .dds) once per process and registers it with tone.clut
    under the key 'game:<lut>' (a rig's post["clut"]); returns the key."""
    from . import tone
    key = "game:%s" % lut.replace("\\", "/").lower()
    if key not in tone._CLUTS:
        tone._CLUTS[key] = for_vis(fs, lut, params=params)
    return key
