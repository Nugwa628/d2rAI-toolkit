"""ColorVariant: the game's colour variants of one effect (the COLOR_VARIANT permutation of the Uber and default-mesh shaders).

What the game does (lane r3-colorvariant, read from D2R's shaders and D2R.exe; docs/hdfx-shaders.md section 4.2 "Colour
variant" and section 14):

- **Which variant**: every effect has the attribute `ColorVariantIndex` (int, 0..255); its scripts copy it into the
  renderer input `ColorVariant.TransformIndex` of each particle (lane vm's sim does this already). The engine sets the
  attribute when it starts the effect on a unit (D2R.exe 0xeabd20 / 0xeabfc0, "ColorVariantIndex"): **37 + the unit's
  colour shift** when the unit has one, else a default from the effect's own request (0..7 in the cases seen: no tint).
  The colour shift is the one of the unit's strongest state (states.txt `colorshift`, by `colorpri`; 1..128; a shift over
  128 is a unit-variant custom entry and gives no particle tint), e.g. cold/freeze 108 -> 145, poison 104 -> 141, red
  100 -> 137, revive 73 -> 110. The index must be <= 165 (the engine asserts it).
- **The table** (shader buffer t6, 158 entries of 32 bytes, built at 0x1019270): entries 0..29 are
  `data/hd/global/palette/randtransforms.json` (30 ColorTransforms), entries 30..157 `data/hd/global/palette/
  transforms.json` (128, the colour-shift table: colour shift c uses entry c - 1, as the unit model's own recolour at
  0xee9100 does). Each entry is the transform's `colorAdjustment` (float4) then its `colorTint` (float4).
- **The maths** (PS, after Color x the Uber grey and the gradient remap / Fresnel, before the exposure; default mesh: on
  the diffuse map's rgb before Color and the lighting): index <= 7 -> nothing; else e = table[index - 8],
  `v = pow(c, e.colorAdjustment.w)`, `v = lerp(v, v * e.colorTint.rgb, e.colorTint.w)`,
  `c = lerp(c, v, ColorVariant.VariantContribution)`. The shader reads only adjustment.w (an exponent) and the tint; the
  hue/saturation/value parts are for unit models.

Viewer: `index_for(...)` picks an index the game's way (a state name, a colour shift, a randtransforms entry or the
index itself), passed to the sim as the attribute `ColorVariantIndex` (CLI `--color-variant`, `--attr`, MCP/web `attrs`).
The renderer packs each particle's (exponent, tint) into the vertex floats `misc.y` (shared with the AtlasAlphaOnly cell
index: `pack_misc_y`) and `dist.z`, and the renderer's VariantContribution into the uniform fx.wgsl `xv.x` (float 256). The table comes from the game's own files through the renderer's
vfs (add `--root casc`, or any root holding `hd/global/palette/`); without it variants draw untinted, with a note.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

RAND_PATH = "data/hd/global/palette/randtransforms.json"
SHIFT_PATH = "data/hd/global/palette/transforms.json"
FIRST = 8            # indices 0..7: no variant
RAND_COUNT = 30      # table entries 0..29 (the engine loads exactly this many)
SHIFT_COUNT = 128    # table entries 30..157
SHIFT_BASE = 37      # ColorVariantIndex = SHIFT_BASE + colour shift
MAX_INDEX = FIRST + RAND_COUNT + SHIFT_COUNT - 1   # 165
ATTR = "ColorVariantIndex"
INPUT = "ColorVariant.TransformIndex"

# D2R's states with a colour shift (states.txt `colorshift`, build 3.x): handy names for the viewer. A mod's states.txt
# may differ; pass `colorshift=` for another value.
STATE_SHIFTS = {"freeze": 108, "cold": 108, "holywindcold": 108, "blue": 108, "poison": 104, "red": 100, "revive": 73,
                "sigillethargy": 103, "sigilrancor": 106, "sigildeath": 107}


@dataclass
class Entry:
    index: int           # ColorVariantIndex
    name: str            # the ColorTransform's name in its file
    source: str          # "rand" or "shift"
    slot: int            # position in its file
    adjust: tuple        # colorAdjustment (x, y, z, w); the particle shaders use w as the exponent
    tint: tuple          # colorTint (r, g, b, amount)

    @property
    def gamma(self) -> float:
        return float(self.adjust[3])

    def identity(self) -> bool:
        return abs(self.gamma - 1.0) < 1e-6 and (self.tint[3] == 0.0 or all(abs(t - 1.0) < 1e-6 for t in self.tint[:3]))

    def to_json(self) -> dict:
        return {"index": self.index, "name": self.name, "source": self.source, "slot": self.slot,
                "gamma": self.gamma, "tint": list(self.tint), "identity": self.identity()}


def _vec(d, k, dflt) -> tuple:
    v = d.get(k) or {}
    return tuple(float(v.get(c, x)) for c, x in zip("xyzw", dflt))


def _entries(doc, count: int, source: str, first: int) -> list:
    ts = (doc or {}).get("transforms") or []
    out = []
    for i in range(count):
        t = ts[i] if i < len(ts) else {}
        out.append(Entry(first + i, str(t.get("name", "%s_%03d" % (source, i))), source, i,
                         _vec(t, "colorAdjustment", (0.0, 0.0, 0.0, 1.0)), _vec(t, "colorTint", (0.0, 0.0, 0.0, 0.0))))
    return out


class Table:
    """The engine's variant table: ColorVariantIndex -> Entry (indices FIRST..MAX_INDEX)."""

    def __init__(self, rand_doc=None, shift_doc=None, source: str = ""):
        self.entries = _entries(rand_doc, RAND_COUNT, "rand", FIRST) + \
            _entries(shift_doc, SHIFT_COUNT, "shift", FIRST + RAND_COUNT)
        self.loaded = rand_doc is not None and shift_doc is not None
        self.source = source

    @classmethod
    def load(cls, vfs) -> "Table":
        """From the game's two palette files through a vfs (`exists`, `read`); missing files give identity entries."""
        docs = []
        for p in (RAND_PATH, SHIFT_PATH):
            try:
                docs.append(json.loads(vfs.read(p).decode("utf-8-sig")) if vfs is not None and vfs.exists(p) else None)
            except (OSError, ValueError):
                docs.append(None)
        return cls(docs[0], docs[1], source="vfs")

    def entry(self, index) -> Entry | None:
        """The entry the shader uses for this TransformIndex (float -> uint as the VS does: negative -> 0), or None."""
        k = _uint(index) - FIRST
        if k < 0 or k >= len(self.entries):
            return None
        return self.entries[k]

    def params(self, indices) -> tuple:
        """Per particle (exponent, tint r, g, b, amount) arrays for TransformIndex values; exponent 0 = no variant."""
        idx = np.atleast_1d(np.asarray(indices, np.float64)).reshape(-1)
        g = np.zeros(len(idx), np.float32)
        tint = np.zeros((len(idx), 4), np.float32)
        tint[:, :3] = 1.0
        for v in np.unique(idx):
            e = self.entry(v)
            if e is None:
                continue
            m = idx == v
            g[m] = max(e.gamma, 1e-6)
            tint[m] = e.tint
        return g, tint

    def choices(self) -> list:
        """Every index with its entry (for the viewer, MCP and web editor pickers); index 0 = none."""
        out = [{"index": 0, "name": "none", "source": "", "slot": -1, "gamma": 1.0, "tint": [0, 0, 0, 0], "identity": True}]
        return out + [e.to_json() for e in self.entries]


def _uint(v) -> int:
    """The VS's float -> uint: NaN and negatives -> 0, truncation."""
    f = float(v)
    if not np.isfinite(f) or f <= 0:
        return 0
    return int(min(f, 4294967295.0))


def index_for(value=None, colorshift=None, rand=None) -> int:
    """ColorVariantIndex for a viewer choice: `value` an int index, a state name of STATE_SHIFTS, "shift:<c>",
    "rand:<k>" or "none"; `colorshift` a states.txt colour shift (1..128); `rand` a randtransforms entry (0..29)."""
    if value is not None and not isinstance(value, (int, float)):
        s = str(value).strip().lower()
        if s in ("", "none", "0"):
            return 0
        if s.startswith("shift:"):
            colorshift = int(s[6:])
        elif s.startswith("rand:"):
            rand = int(s[5:])
        elif s in STATE_SHIFTS:
            colorshift = STATE_SHIFTS[s]
        else:
            try:
                value = int(s)
            except ValueError:
                raise ValueError("colour variant %r: an index, a state (%s), shift:<c> or rand:<k>"
                                 % (value, ", ".join(sorted(STATE_SHIFTS)))) from None
    if colorshift is not None:
        c = int(colorshift)
        return SHIFT_BASE + c if 0 < c <= SHIFT_COUNT else 0     # over 128: a unit's custom entry, no particle tint
    if rand is not None:
        r = int(rand)
        if not 0 <= r < RAND_COUNT:
            raise ValueError("randtransforms entry %d: 0..%d" % (r, RAND_COUNT - 1))
        return FIRST + r
    v = int(value or 0)
    if not 0 <= v <= MAX_INDEX:
        raise ValueError("ColorVariantIndex %d: 0..%d (the engine asserts <= %d)" % (v, MAX_INDEX, MAX_INDEX))
    return v


def contribution(get, on: bool) -> float:
    """cb4[7].x: ColorVariant.VariantContribution when the COLOR_VARIANT permutation is on, else 0 (no variant)."""
    if not on:
        return 0.0
    v = get("ColorVariant.VariantContribution")
    if isinstance(v, (tuple, list)):
        v = v[0] if v else 1.0
    return float(1.0 if v is None else v)


def apply(rgb, gamma, tint, contrib) -> np.ndarray:
    """The shader's maths on (n, 3) linear colours (reference for tests and CPU paths): gamma 0 = no variant."""
    c = np.asarray(rgb, np.float64)
    g = np.asarray(gamma, np.float64).reshape(-1, 1) if np.ndim(gamma) else np.float64(gamma)
    t = np.asarray(tint, np.float64).reshape(-1, 4)
    on = (g > 0) & (float(contrib) > 0)
    v = np.power(np.abs(c), np.where(on, g, 1.0))
    v = v + t[:, 3:4] * (v * t[:, :3] - v)
    out = c + float(contrib) * (v - c)
    return np.where(on, out, c)


# ---- vertex packing (packers' free floats: misc.y = exponent, dist.z = effective tint packed 8:8:8) --------------------

def pack_tint(tint) -> np.ndarray:
    """(n, 4) tints -> one float per particle: lerp(1, tint.rgb, amount) quantised to 8 bits per channel, packed
    r + 256 g + 65536 b (exact in f32). lerp(v, v * t, w) = v * lerp(1, t, w), so the amount folds into the tint."""
    t = np.asarray(tint, np.float64).reshape(-1, 4)
    eff = np.clip(1.0 + t[:, 3:4] * (t[:, :3] - 1.0), 0.0, 1.0)
    q = np.rint(eff * 255.0).astype(np.int64)
    return (q[:, 0] + 256 * q[:, 1] + 65536 * q[:, 2]).astype(np.float32)


def unpack_tint(p) -> np.ndarray:
    """pack_tint's inverse (what fx.wgsl `cv_tint` does): (n, 3) effective tints."""
    q = np.asarray(p, np.float64).astype(np.int64).reshape(-1)
    return np.stack([q % 256, (q // 256) % 256, q // 65536], 1) / 255.0


# misc.y is shared with AlphaMask.AtlasAlphaOnly's atlas cell index (packers.pack_billboards adds cell +
# packers.MISC_Y_CELL_BIAS): the exponent goes in as 4096 x round(exponent x 256) (exponents 0 .. 15.99 in 1/256 steps;
# exact in f32), and fx.wgsl vs_fx splits them again (integration r3).
EXP_STEPS = 256.0
CELL_SPAN = 4096.0           # packers.MISC_Y_SPAN
CELL_BIAS = 2048.0           # packers.MISC_Y_CELL_BIAS


def pack_misc_y(g) -> np.ndarray:
    """Colour variant exponents -> their part of the vertex float misc.y (see above)."""
    q = np.rint(np.clip(np.asarray(g, np.float64), 0.0, (CELL_SPAN - 1) / EXP_STEPS) * EXP_STEPS)
    return (q * CELL_SPAN).astype(np.float32)


def unpack_misc_y(v) -> tuple:
    """misc.y -> (exponent, atlas cell), as fx.wgsl vs_fx does (the cell is meaningful only with AtlasAlphaOnly)."""
    v = np.asarray(v, np.float64)
    q = np.floor(v / CELL_SPAN)
    return q / EXP_STEPS, v - q * CELL_SPAN - CELL_BIAS


MISC_Y = 1           # column offsets inside packers' particle block (misc starts at 0, dist at 32)
DIST_Z = 2


def fill_block(pblock: np.ndarray, inputs: dict, n: int, table: "Table | None", on: bool) -> int:
    """Writes the per-particle variant into a packers.particle_block (in place); returns how many particles got one."""
    if not on or table is None or not n:
        return 0
    from . import packers as pk
    raw = (inputs or {}).get(INPUT)
    if raw is None:
        return 0
    idx = np.asarray(raw, np.float64).reshape(-1)
    idx = np.full(n, idx[0]) if len(idx) in (0, 1) or len(idx) != n else idx
    if not len(idx):
        return 0
    g, tint = table.params(idx)
    m0, d0 = pk.OFF["misc"][0] - 10, pk.OFF["dist"][0] - 10
    pblock[:, m0 + MISC_Y] = pack_misc_y(g)
    pblock[:, d0 + DIST_Z] = np.where(g > 0, pack_tint(tint), 0.0)
    return int(np.count_nonzero(g))
