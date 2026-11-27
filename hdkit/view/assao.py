"""The game's screen-space ambient occlusion: Intel ASSAO (MIT) at quality 2, as D2R runs it between the opaque pass and
the transparents ("AO" markers). Ported from the game's own shaders (calib captures, lane r5-postpass): half-res depths
deinterleaved 2x2 with a 4-level smart mip, 12 tap pairs, an edge-aware wide blur and an edge-aware apply, multiplied
onto the opaque colour (blend Zero/SrcColor). Inputs at the game's full resolution: view depth z (H, W) and view normals
n (H, W, 3: right, up, towards the camera); orthographic camera (the game's `NDCToViewMul` = the view's extent)."""
import numpy as np

# The game's ASSAOConstants (captured cb, identical in every capture read): radius 3, shadow 4.3 ^ 3 clamped at 1, no
# horizon threshold, detail AO 1, sharpness 0 (edges unbiased), fade-out off at game depths, near-limit 7.37 (always 1).
GAME = {"radius": 3.0, "strength": 4.3, "power": 3.0, "clamp": 1.0, "fade_mul": -1e-5, "fade_add": 11.0,
        "horizon": 0.0, "near_rec": 7.365695, "precision": 0.9992, "detail": 1.0, "inv_sharpness": 0.0,
        "z_mul": 510.0, "z_add": 550.0,   # the camera's linear-depth unpack: z = s * z_mul + z_add (s: the R16 depth)
        "z_centre": 884.0}  # view depth of the ground under the screen centre (frame 4268: 884; calib r3 Mephisto: 881)
# g_samplePatternMain[0:12] (x, y, weight, mip offset) as the game's generate shader holds it
PATTERN = np.array([[.78488064, .56661671, 1.5, -.126083], [.26022232, -.29575172, 1.5, -1.06403],
                    [.10459357, .08372527, 1.11, -2.730563], [-.682868, .04963045, 1.09, -.498827],
                    [-.13570161, -.64190155, 1.25, -.532765], [-.26193795, -.08205118, .67, -1.783245],
                    [-.61177456, .66664219, .71, -.044234], [.43675563, .25119025, .61, -1.167283],
                    [.07884444, .86618668, .64, -.459002], [-.12790935, -.29869005, .6, -1.729424],
                    [-.04031125, .02413622, .6, -4.792042], [.16201244, -.52851415, .79, -1.067055]])


def _rot(p):
    """PatternRotScaleMatrices of pass p (ASSAO's formula; equals the captured values)."""
    out = []
    for i, b in enumerate((0.0, 1.0, 4.0, 3.0, 2.0)):
        a = (p + b / 5.0) * np.pi * 0.5
        s = 1.0 + (p - 1.5 + (b - 2.0) / 5.0) * 0.07
        out.append((s * np.cos(a), -s * np.sin(a), -s * np.sin(a), -s * np.cos(a)))
    return np.array(out)


def _f16(x):
    return np.asarray(x, np.float32).astype(np.float16).astype(np.float64)


def _at(a, y, x):
    return a[np.clip(y, 0, a.shape[0] - 1), np.clip(x, 0, a.shape[1] - 1)]


def _mips(z, C):
    """The 4-level 'smart' depth mip of one half-res slice (Prepare Depths) of the stored depth s (not z, as the game
    does it: the falloff then barely bites, a near-plain average)."""
    out = [z]
    for _ in range(3):
        a = out[-1]
        q = np.stack([_at(a, *np.mgrid[0:(a.shape[0] + 1) // 2, 0:(a.shape[1] + 1) // 2] * 2 + np.array(o)[:, None, None])
                      for o in ((0, 0), (0, 1), (1, 0), (1, 1))])
        lo = q.min(0)
        r = (np.clip(np.abs(lo) * C["near_rec"], 0, 1) * 0.8 + 0.2) * C["radius"]
        w = np.maximum((q - lo) ** 2 * (-1.0 / r ** 2) + 1.0, 0.0)
        out.append(_f16((w * q).sum(0) / w.sum(0)))
    return out


def _edges(v):
    b = np.rint(v * 255.0).astype(np.int64)
    return [np.clip(((b >> s) & 3) / 3.0, 0, 1) for s in (6, 4, 2, 0)]      # L R T B


def generate(m, nf, p, ext, C, off=(0, 0), full=None):
    """(ao, packed edges) of pass p (full-res offset (p & 1, p >> 1)) from its depth mips `m` and the full-res normals;
    `off` (multiples of 16): this window's top-left in the `full` (W, H) image."""
    h, w = m[0].shape
    W, H = full or nf.shape[1::-1]
    Y, X = np.mgrid[0:h, 0:w]
    GX, GY = X + off[0] // 2, Y + off[1] // 2              # half-res pixel in the full image
    ox, oy = p & 1, p >> 1
    c, L, R, T, B = m[0], _at(m[0], Y, X - 1), _at(m[0], Y, X + 1), _at(m[0], Y - 1, X), _at(m[0], Y + 1, X)
    u, v = (2 * GX + 0.5) / W, (2 * GY + 0.5) / H        # every pass at pass 0's UV offset, as the game sets it
    Mx, My, Ax, Ay = ext[0], -ext[1], -ext[0] / 2, ext[1] / 2
    px, py, pw, ph = Mx * u + Ax, My * v + Ay, Mx / W, -My / H
    r = (np.clip(np.sqrt(px * px + py * py + c * c) * C["near_rec"], 0, 1) * 0.8 + 0.2) * C["radius"]
    look, nr2 = r * 0.85 / pw, -1.0 / (r * r)
    rot = _rot(p)[(2 * GY + GX) % 5] * look[..., None]
    P = np.stack([px, py, c], -1) * C["precision"]
    nx, ny = P[..., 0] / P[..., 2], P[..., 1] / P[..., 2]
    dl = [q - P[..., 2] for q in (L, R, T, B)]
    nbr = [np.stack(t, -1) for t in ((nx * dl[0] - pw, ny * dl[0], dl[0]), (nx * dl[1] + pw, ny * dl[1], dl[1]),
                                     (nx * dl[2], ny * dl[2] + ph, dl[2]), (nx * dl[3], ny * dl[3] - ph, dl[3]))]
    sl = (np.abs(dl[0] + dl[1]), np.abs(dl[2] + dl[3]))
    e = [np.clip(1.3 - np.minimum(np.abs(d), sl[i // 2]) / (c * 0.04), 0, 1) for i, d in enumerate(dl)]
    fy, fx = 2 * Y + oy, 2 * X + ox
    n = _at(nf, fy, fx) * np.array([1.0, 1.0, -1.0])

    def occ(d):
        d2 = (d * d).sum(-1)
        return np.maximum(d2 * nr2 + 1, 0) * np.maximum((n * d).sum(-1) / np.sqrt(np.maximum(d2, 1e-30)) - C["horizon"], 0)
    ne = [np.clip((n * (_at(nf, fy + dy, fx + dx) * np.array([1.0, 1.0, -1.0]))).sum(-1) + 0.1, 0, 1)
          for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2))]
    obs = C["detail"] * sum(e[i] * np.maximum((nbr[i] ** 2).sum(-1) * nr2 * 4 + 1, 0) *
                            np.maximum((n * nbr[i]).sum(-1) / np.sqrt((nbr[i] ** 2).sum(-1) + 1e-30) - C["horizon"], 0)
                            for i in range(4))
    wsum = 0.0
    nxy = np.hypot(n[..., 0], n[..., 1]) + 1e-30
    dx_, dy_, tilt = n[..., 0] / nxy, -n[..., 1] / nxy, nxy * 0.4
    vd = -P / np.linalg.norm(P, axis=-1, keepdims=True)
    mip0 = np.log2(look) - 4.3
    for pt in PATTERN:
        sx = np.rint(rot[..., 0] * pt[0] + rot[..., 1] * pt[1])
        sy = np.rint(rot[..., 2] * pt[0] + rot[..., 3] * pt[1])
        t = -tilt * (-sx * dx_ - sy * dy_)
        lv = np.clip(np.ceil(mip0 + pt[3] + 0.5) - 1, 0, 3).astype(int)
        for ax, ay in ((sx, sy), (np.rint(t * dx_ - sx), np.rint(t * dy_ - sy))):
            tx, ty = X + 0.25 + ax, Y + 0.25 + ay
            zs = np.zeros_like(c)
            for k in range(4):
                s = lv == k
                zs[s] = _at(m[k], np.floor(ty[s] / 2 ** k).astype(int), np.floor(tx[s] / 2 ** k).astype(int))
            d = np.stack([Mx * (2 * (tx + off[0] // 2) / W) + Ax - P[..., 0], My * (2 * (ty + off[1] // 2) / H) + Ay - P[..., 1],
                          zs - P[..., 2]], -1)
            wm = np.clip(np.maximum((d * vd).sum(-1), 0) * (-1.0 / C["radius"]) + 2.0, 0, 1) * 0.8 + 0.2
            obs = obs + occ(d) * wm * pt[2]
            wsum = wsum + wm * pt[2]
    fade = (1 - (np.maximum(1 - e[0] * ne[0] - e[1] * ne[1], 0) * 0.35 + np.maximum(1 - e[2] * ne[2] - e[3] * ne[3], 0) * 0.35))
    fade = fade * np.clip(P[..., 2] * C["fade_mul"] + C["fade_add"], 0, 1)
    ao = np.clip(1 - np.minimum(obs / wsum * C["strength"], C["clamp"]) * fade, 0, 1) ** C["power"]
    pk = sum(np.rint(e[i] * ne[i] * 3.05).astype(int) << s for i, s in enumerate((6, 4, 2, 0)))
    return np.rint(ao * 255) / 255, pk / 255.0


def blur(a, pk, C):
    """The wide smart blur (one pass): neighbours 2 half-res texels away, weighted by both sides' edges, centre 0.8."""
    h, w = a.shape
    Y, X = np.mgrid[0:h, 0:w]
    e = [np.clip(x + C["inv_sharpness"], 0, 1) for x in _edges(pk)]
    num, den = a * 0.8, 0.8
    for (dx, dy), i, j in (((-2, 0), 0, 1), ((2, 0), 1, 0), ((0, -2), 2, 3), ((0, 2), 3, 2)):
        wt = e[i] * np.clip(_edges(_at(pk, Y + dy, X + dx))[j] + C["inv_sharpness"], 0, 1)
        num, den = num + wt * _at(a, Y + dy, X + dx), den + wt
    return np.rint(num / den * 255) / 255


def _bilin(a, tx, ty):
    """Bilinear clamp-to-edge sample of a at texel coordinates (texel centres at i + 0.5)."""
    x0, y0 = np.floor(tx - 0.5), np.floor(ty - 0.5)
    fx, fy = tx - 0.5 - x0, ty - 0.5 - y0
    x0, y0 = x0.astype(int), y0.astype(int)
    return ((_at(a, y0, x0) * (1 - fx) + _at(a, y0, x0 + 1) * fx) * (1 - fy) +
            (_at(a, y0 + 1, x0) * (1 - fx) + _at(a, y0 + 1, x0 + 1) * fx) * fy)


def apply(F, E, C, shape):
    """Full-res AO from the four blurred slices F[s] (s = mx + 2 my) and their edges E[s]: the edge-aware apply."""
    H, W = shape
    y, x = np.mgrid[0:H, 0:W]
    mx, my = x & 1, y & 1
    out = np.zeros((H, W))
    for s in range(4):
        q = (mx + 2 * my) == s
        yq, xq, mq, nq = y[q], x[q], mx[q], my[q]
        el, er, et, eb = (np.clip(t + C["inv_sharpness"], 0, 1) for t in _edges(E[s][yq >> 1, xq >> 1]))
        hx, vy = (xq + mq + er - el) / 2.0, (yq + nq + eb - et) / 2.0
        aoh = _bilin(F[s ^ 1], hx, (yq + 1 - nq) / 2.0)
        aov = _bilin(F[s ^ 2], (xq + 1 - mq) / 2.0, vy)
        aod = _bilin(F[s ^ 3], hx, vy)
        wh, wv = (el + er) / 2, (et + eb) / 2
        out[q] = (F[s][yq >> 1, xq >> 1] + aoh * wh + aov * wv + aod * (wh + wv) / 2) / (1 + wh + wv + (wh + wv) / 2)
    return out


def ssao(z, n, ext, C=None, box=None):
    """The game's AO factor (H, W) for view depth z (H, W; larger = farther; the game's absolute view depth) and
    view normals n (H, W, 3); ext = (width, height) of the view in world units. H and W even. `box` = (x0, y0, x1, y1):
    only the pixels there are needed; the work is cropped to it plus margin() (the farthest tap; 1 elsewhere)."""
    C = dict(GAME, **(C or {}))
    H, W = z.shape
    if box is not None:
        mg = margin(ext[0] / W, C)
        x0, y0 = max(0, (box[0] - mg) // 16 * 16), max(0, (box[1] - mg) // 16 * 16)
        x1, y1 = min(W, -(-(box[2] + mg) // 2) * 2), min(H, -(-(box[3] + mg) // 2) * 2)
        out = np.ones((H, W))
        out[y0:y1, x0:x1] = _ssao(z[y0:y1, x0:x1], n[y0:y1, x0:x1], ext, C, (x0, y0), (W, H))
        return out
    return _ssao(z, n, ext, C, (0, 0), (W, H))


def margin(pw, C):
    """Full-res px a pixel's AO reaches: the lookup radius 0.85 r / pw in half-res texels (x 2 full-res px), x 1.6 for
    the pattern's rotation scale (1.08) and the mirrored taps' tilt (0.4), plus the mip blocks and blur (32)."""
    return int(2 * 1.6 * 0.85 * C["radius"] / pw) + 32


def _ssao(z, n, ext, C, off, full):
    H, W = z.shape
    F, E = [], []
    for p in range(4):
        m = [q * C["z_mul"] + C["z_add"] for q in _mips(_f16((z[p >> 1::2, p & 1::2] - C["z_add"]) / C["z_mul"]), C)]
        a, pk = generate(m, n, p, ext, C, off, full)
        F.append(blur(a, pk, C))
        E.append(pk)
    return apply(F, E, C, (H, W))
