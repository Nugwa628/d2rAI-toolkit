"""A unit's colour variant (lane hdfx-r3-unitmat): which entry of the unit's VariantTransformTable the game uses, the
KTINT recolour of its model (D2R `ps_models_p`), and the `ColorVariantIndex` the engine gives the effects the unit starts.

What the game does (RenderDoc s2 capture of `swarm1`, frame 3505; shader `ps_models_p` disassembly,
lines 128-232; D2R.exe 0xee9100 / 0xeabd20):

- A unit JSON names its table in a `VariantDefinitionComponent` (`<unit>_variant.json`, 260 in the game data): 8
  `VariantColorTransform` entries, each 3 `ColorTransform`s (one per KTINT mask channel R, G, B). 249 tables are ordered
  cold, poison, level0 .. level4, misc.
- The model draw gets the chosen entry's 3 transforms in root CBV cb7 floats 0..71 (24 floats each, in the JSON's order:
  colorAdjustment, hueAdjustment0, hueAdjustment1, satAdjustment0, satAdjustment1, colorTint) and a mask floor in cb7[82].
  swarm1 (monstats TransLvl 0) draws with `level0` (entry 2): cb7[0..3] = (0.34, 0, -0.43, 1), cb7[24..27] = (0, 0,
  -0.76, 1), cb7[48..51] = (0.37, 0.48, -0.37, 1), cb7[82] = 0.
- Its effect `vfx_swarm1_core` gets `ColorVariantIndex` 2 too: the "Swarm" layer's colour switch (index 2 ->
  (0.0016, 0.131, 0.020) sRGB) is exactly the colour the capture's billboard VS outputs (0.0001, 0.0156, 0.0015 linear).
  The effects switch on the raw index 0..7, so the index is the entry's position: **2 + TransLvl**. The viewer used 0
  (the attribute's default, entry `cold`): the swarm drew grey x 1.42 x 4 (a white cloud) instead of near black.
- A colour state (states.txt colorshift c, 1..128) replaces all three channels with `hd/global/palette/transforms.json`
  entry c - 1 and sets the mask floor to 1 (the whole model is tinted); its effects get index 37 + c (lane colorvariant).
- No table: identity transforms (the unit draws its albedo as it is).

The recolour per transform (HSV of the linear albedo; the shader's maths, branch for branch): hue + adjust.x and six
hue-band shifts (hueAdjustment0.xyz, hueAdjustment1.xyz; bands 30 degrees wide round 0, 60, ... 300), saturation x (1 +
adjust.y) then x (1 + band weight x satAdjustment), value x (1 + adjust.z), back to RGB, clamp, ^ adjust.w, then
lerp(c, c x tint.rgb, tint.w). The three results are mixed by the KTINT mask (max(mask.rgb, floor), normalised when the
sum is over 1); the rest of the weight keeps the albedo.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FIELDS = ("colorAdjustment", "hueAdjustment0", "hueAdjustment1", "satAdjustment0", "satAdjustment1", "colorTint")
IDENTITY = np.array([0.0, 0.0, 0.0, 1.0] + [0.0] * 20, np.float32)
TRANSFORMS_JSON = "data/hd/global/palette/transforms.json"
LEVEL0 = 2                 # position of `level0` in the stock table order (cold, poison, level0 .. level4, misc)
PACK_FLOATS = 76           # gpu uniform block: 72 transform floats, then (on, mask floor, 0, 0)


def transform_floats(ct: dict) -> np.ndarray:
    """The 24 floats of a ColorTransform (cb7 layout)."""
    out = []
    for f in FIELDS:
        v = ct.get(f) or {}
        out += [float(v.get(k, 0.0)) for k in "xyzw"]
    return np.asarray(out, np.float32)


@dataclass
class VariantTable:
    path: str
    names: list                                   # entry names, in file order
    entries: list                                 # per entry: (3, 24) float32 (R, G, B mask channel transforms)

    @classmethod
    def from_doc(cls, path: str, doc: dict) -> "VariantTable":
        names, entries = [], []
        for e in doc.get("entries", []):
            ts = [transform_floats(t) for t in (e.get("transforms") or [])[:3]]
            ts += [IDENTITY] * (3 - len(ts))
            names.append(str(e.get("name", "")))
            entries.append(np.stack(ts))
        return cls(path, names, entries)

    def index_of(self, which) -> int:
        """An entry by position (int or digits) or by name (case-insensitive)."""
        if isinstance(which, (int, np.integer)) or str(which).strip().lstrip("-").isdigit():
            i = int(which)
            if not 0 <= i < len(self.entries):
                raise ValueError("variant %d: the table has %d entries" % (i, len(self.entries)))
            return i
        low = [n.lower() for n in self.names]
        if str(which).lower() in low:
            return low.index(str(which).lower())
        raise ValueError("variant %r not in %s" % (which, self.names))


@dataclass
class Choice:
    """What the game binds for one unit: the model's cb7 transforms and mask floor, and its effects' ColorVariantIndex."""
    floats: np.ndarray = field(default_factory=lambda: np.tile(IDENTITY, 3))    # (72,)
    floor: float = 0.0
    effect_index: int | None = None               # None: leave the effects' ColorVariantIndex alone
    label: str = "none"
    table: str = ""

    def pack(self) -> np.ndarray:
        u = np.zeros(PACK_FLOATS, np.float32)
        u[:72] = self.floats
        u[72:74] = (1.0, self.floor)
        return u

    def identity(self) -> bool:
        return self.floor == 0.0 and np.array_equal(self.floats, np.tile(IDENTITY, 3))

    def as_dict(self) -> dict:
        return {"label": self.label, "table": self.table, "effect_index": self.effect_index, "floor": self.floor,
                "identity": self.identity()}


def table_path(doc: dict) -> str | None:
    """The VariantDefinitionComponent filename of a unit JSON (any entity), else None."""
    for ent in doc.get("entities", []) or []:
        for c in ent.get("components", []) or []:
            if c.get("type") == "VariantDefinitionComponent" and c.get("filename"):
                return str(c["filename"])
    return None


def load_table(vfs, doc: dict) -> VariantTable | None:
    p = table_path(doc)
    if not p or not vfs.exists(p):
        return None
    return VariantTable.from_doc(p, json.loads(vfs.read(p).decode("utf-8")))


def global_transform(vfs, shift: int) -> np.ndarray:
    """transforms.json entry shift - 1 (a colour state's shift, 1..128), 24 floats."""
    d = json.loads(vfs.read(TRANSFORMS_JSON).decode("utf-8"))
    ts = d.get("transforms") or []
    if not 1 <= int(shift) <= len(ts):
        raise ValueError("colour shift %d: transforms.json has %d entries" % (shift, len(ts)))
    return transform_floats(ts[int(shift) - 1])


def level_index(trans_lvl: int | None, table: VariantTable | None = None) -> int:
    """The entry the game uses for a monster of monstats TransLvl t: position 2 + t (clamped to the table)."""
    i = LEVEL0 + max(0, int(trans_lvl or 0))
    n = len(table.entries) if table is not None else 8
    return min(i, n - 1)


def choose(table: VariantTable | None, variant=None, trans_lvl: int | None = None, shift: int | None = None,
           vfs=None) -> Choice:
    """The game's pick: a colour shift (state) wins, then an explicit entry, then TransLvl (default 0: `level0`)."""
    if shift:
        if vfs is None:
            raise ValueError("a colour shift needs the game files (transforms.json)")
        t = global_transform(vfs, int(shift))
        return Choice(np.tile(t, 3), 1.0, 37 + int(shift), "shift %d" % int(shift), TRANSFORMS_JSON)
    if table is None or not table.entries:
        return Choice(label="none (no variant table)")
    i = table.index_of(variant) if variant is not None else level_index(trans_lvl, table)
    return Choice(table.entries[i].reshape(-1).astype(np.float32), 0.0, i, "%d %s" % (i, table.names[i]), table.path)


def monster_trans_lvl(tables_dir, monster: str) -> int:
    """monstats.txt TransLvl of a monster id (0 when empty)."""
    p = Path(tables_dir) / "monstats.txt"
    with open(p, encoding="latin-1", newline="") as f:
        rows = csv.reader(f, delimiter="\t")
        head = next(rows)
        i, k = head.index("Id"), head.index("TransLvl")
        for r in rows:
            if len(r) > max(i, k) and r[i].lower() == monster.lower():
                return int(r[k]) if r[k].strip().lstrip("-").isdigit() else 0
    raise KeyError("monster %s not in %s" % (monster, p))


def effect_attrs(choice: Choice | None, attrs: dict | None) -> dict | None:
    """Sim attrs for an effect the unit starts: ColorVariantIndex = the choice's index unless the caller set it."""
    if choice is None or choice.effect_index is None:
        return attrs
    out = dict(attrs or {})
    if "ColorVariantIndex" not in out:
        out["ColorVariantIndex"] = [int(choice.effect_index), 0, 0, 0]
    return out


# ---- the shader's maths (numpy; the reference for shaders.wgsl kt_apply) ------------------------------------------------
def _clamp(x):
    return np.clip(x, 0.0, 1.0)


def apply_one(c: np.ndarray, t: np.ndarray) -> np.ndarray:
    """One ColorTransform (24 floats) on linear rgb (..., 3), as ps_models_p."""
    c = np.asarray(c, np.float64)
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mx = np.maximum(b, np.maximum(r, g))
    d = mx - np.minimum(b, np.minimum(r, g))
    has = d != 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(mx != 0.0, d / np.where(mx != 0.0, mx, 1.0), 0.0)
        dd = np.where(has, d, 1.0)
        half = d * 0.5
        dr = ((mx - r) / 6.0 + half) / dd
        dg = ((mx - g) / 6.0 + half) / dd
        db = ((mx - b) / 6.0 + half) / dd
    h = np.where(r == mx, db - dg, np.where(g == mx, dr - db + 1.0 / 3.0, np.where(b == mx, dg - dr + 2.0 / 3.0, 0.0)))
    h = np.where(h < 0.0, h + 1.0, h)
    h = np.where(h > 1.0, h - 1.0, h)                  # _422 (band weights)
    h0 = np.where(has, h, 0.0)                         # _423
    sat = s * (1.0 + t[1])
    val = mx * (1.0 + t[2])
    x0 = np.mod(h0 + 0.125, 1.0)
    w = [_clamp(3.0 - 12.0 * x0) * np.minimum(12.0 * x0, 1.0)]
    for a_, b_ in ((3.5, 0.5), (5.5, 2.5), (7.5, 4.5), (9.5, 6.5), (11.5, 8.5)):
        w.append(np.where(has, _clamp(a_ - 12.0 * h) * _clamp(12.0 * h - b_), _clamp(a_) * _clamp(-b_)))
    for wk, k in zip(w, (12, 13, 14, 16, 17, 18)):
        sat = sat + wk * t[k] * sat
    hue = np.mod(h0 + t[0], 1.0)
    for wk, k in zip(w, (4, 5, 6, 8, 9, 10)):
        hue = np.mod(hue + t[k] * wk, 1.0)
    sf = np.where(has, sat, 0.0)
    ok = has & (sat != 0.0)
    i = np.floor(hue * 6.0)
    f = hue * 6.0 - i
    p = val * (1.0 - sf)
    q = val * (1.0 - sf * f)
    u = val * (1.0 - sf * (1.0 - f))
    R = np.where(ok & (i != 0), np.where(i == 1, q, np.where((i == 2) | (i == 3), p, np.where(i == 4, u, val))), val)
    G = np.where(ok, np.where(i == 0, u, np.where((i == 1) | (i == 2), val, np.where(i == 3, q, p))), val)
    B = np.where(ok, np.where((i == 0) | (i == 1), p, np.where(i == 2, u, np.where((i == 4) | (i == 3), val, q))), val)
    with np.errstate(divide="ignore"):
        out = np.stack([np.exp2(np.log2(_clamp(x)) * t[3]) for x in (R, G, B)], -1)
    tint, tw = np.asarray(t[20:23], np.float64), float(t[23])
    return out + tw * (out * tint - out)


def apply(albedo: np.ndarray, mask: np.ndarray, floats72: np.ndarray, floor: float = 0.0) -> np.ndarray:
    """The recoloured linear albedo (..., 3) from the KTINT mask rgb (..., 3) and the 3 transforms."""
    a = np.asarray(albedo, np.float64)[..., :3]
    m = np.maximum(np.asarray(mask, np.float64)[..., :3], floor)
    sm = m.sum(-1, keepdims=True)
    m = np.where(sm > 1.0, m / np.where(sm > 1.0, sm, 1.0), m)
    rest = 1.0 - m.sum(-1, keepdims=True)
    f = np.asarray(floats72, np.float64).reshape(3, 24)
    out = a * rest
    for k in range(3):
        out = out + m[..., k:k + 1] * apply_one(a, f[k])
    return out
