"""Lossless reader/writer of D2R's baked PopcornFX effects (`.particles`), with structural edits.

Layout (little endian; after Reimagined D2R Mod Studio's `ParticleFile.cs`, MIT, and hdkit.formats.hd_particles):

    header   u32 magic 0xCA000B11, u32 version (0x01050902 = PopcornFX 2.9.5), u32 build (8000), u32 object count,
             u32 class count, u32 offset of the string table, u32 reserved (0); then (u32 class string, u32 count)
             per class
    objects  u32 size, then `size` bytes: a marker byte (0x20), u32 class (string index), u16 field count, then
             per field a u16 field id and its value (sizes from `schema.SIZES`; arrays are a u32 count and the elements)
    strings  u32 count, then per string a u8 length and the bytes (latin-1); the table ends the file

Objects refer to each other by number (1 = the first object) and to strings by index; `schema.FIELDS` says which fields
are which, so objects can be added, removed (references renumbered) and copied between files (strings merged).
`PFile.read(b).data() == b` for every file of the corpus (`python -m hdkit.fx roundtrip <root>`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import schema

MAGIC = 0xCA000B11
NONE = schema.NONE


@dataclass
class PObj:
    """One object: its class and its fields in file order. A field value is the raw bytes of a fixed-size value, or for
    an array field the elements' bytes without the count (`elem` > 0). Objects of a class the schema lacks keep their
    payload in `raw` and have no fields."""
    cls: str
    fields: list[list] = field(default_factory=list)   # [fid, bytes, elem] (elem 0 = fixed-size value)
    marker: int = 0x20
    raw: bytes | None = None

    # ------------------------------------------------------------- access
    def _f(self, fid: int):
        for f in self.fields:
            if f[0] == fid:
                return f
        return None

    def has(self, fid: int) -> bool:
        return self._f(fid) is not None

    def get(self, fid: int, default: bytes | None = None) -> bytes | None:
        f = self._f(fid)
        return default if f is None else f[1]

    def u32(self, fid: int, default: int | None = 0) -> int | None:
        b = self.get(fid)
        if b is None:
            return default
        return b[0] if len(b) == 1 else struct.unpack_from("<I", b)[0]

    def f32(self, fid: int, default: float = 0.0) -> float:
        b = self.get(fid)
        return default if b is None else struct.unpack_from("<f", b)[0]

    def vec(self, fid: int, fmt: str = "f") -> list:
        """A 16-byte value as 4 floats ("f") or ints ("i"); zeros when absent (its default)."""
        b = self.get(fid)
        return list(struct.unpack(f"<4{fmt}", b)) if b is not None and len(b) == 16 else [0] * 4

    def arr(self, fid: int, fmt: str = "I") -> list:
        """An array field as u32s ("I"), floats ("f") or ints ("i"); [] when absent."""
        b = self.get(fid)
        return [] if b is None else list(struct.unpack(f"<{len(b) // 4}{fmt}", b))

    def elems(self, fid: int) -> list[bytes]:
        f = self._f(fid)
        if f is None:
            return []
        e = f[2] or len(f[1])
        return [f[1][i:i + e] for i in range(0, len(f[1]), e)]

    def loc(self, fid: int) -> list[tuple[int, int]]:
        """A localized-string array: (language code, string index) pairs."""
        b = self.get(fid)
        return [] if b is None else [struct.unpack_from("<II", b, i) for i in range(0, len(b), 8)]

    # ------------------------------------------------------------- edits
    def set(self, fid: int, value: bytes | None, elem: int = 0) -> None:
        """Set a field (kept in id order; a new one is inserted at its place); None removes it (its default)."""
        if value is None:
            self.fields = [f for f in self.fields if f[0] != fid]
            return
        size = schema.SIZES.get(self.cls, {}).get(fid)
        if size is not None:
            if size > 0 and len(value) != size:
                raise ValueError(f"{self.cls} field {fid} holds {size} bytes, not {len(value)}")
            if size < 0:
                elem = -size
                if len(value) % elem:
                    raise ValueError(f"{self.cls} field {fid}: {len(value)} bytes are not whole {elem}-byte elements")
        f = self._f(fid)
        if f is not None:
            f[1], f[2] = bytes(value), elem
            return
        k = next((i for i, g in enumerate(self.fields) if g[0] > fid), len(self.fields))
        self.fields.insert(k, [fid, bytes(value), elem])

    def set_u32(self, fid: int, v: int) -> None:
        size = schema.SIZES.get(self.cls, {}).get(fid, 4)
        self.set(fid, bytes([v]) if size == 1 else struct.pack("<I", v & 0xFFFFFFFF))

    def set_arr(self, fid: int, values, fmt: str = "I") -> None:
        values = list(values)
        self.set(fid, struct.pack(f"<{len(values)}{fmt}", *values), 4)

    def set_vec(self, fid: int, values, fmt: str = "f") -> None:
        v = (list(values) + [0] * 4)[:4]
        self.set(fid, struct.pack(f"<4{fmt}", *v))

    # ------------------------------------------------------------- bytes
    @classmethod
    def decode(cls_, name: str, payload: bytes, marker: int = 0x20) -> "PObj":
        sizes = schema.SIZES.get(name)
        if sizes is None:
            return cls_(name, [], marker, bytes(payload))
        n = struct.unpack_from("<H", payload, 0)[0]
        q, out = 2, []
        for _ in range(n):
            fid = struct.unpack_from("<H", payload, q)[0]
            q += 2
            if fid not in sizes:
                raise ValueError(f"{name}: unknown field {fid}")
            sz = sizes[fid]
            if sz > 0:
                out.append([fid, bytes(payload[q:q + sz]), 0])
                q += sz
            else:
                c = struct.unpack_from("<I", payload, q)[0]
                out.append([fid, bytes(payload[q + 4:q + 4 - sz * c]), -sz])
                q += 4 - sz * c
            if q > len(payload):
                raise ValueError(f"{name}: field {fid} overruns the object")
        if q != len(payload):
            raise ValueError(f"{name}: {len(payload) - q} bytes after the last field")
        return cls_(name, out, marker)

    def encode(self) -> bytes:
        if self.raw is not None:
            return self.raw
        out = [struct.pack("<H", len(self.fields))]
        for fid, v, elem in self.fields:
            out.append(struct.pack("<H", fid))
            if elem:
                out.append(struct.pack("<I", len(v) // elem))
            out.append(v)
        return b"".join(out)

    def refs(self) -> list[tuple[int, int, int]]:
        """(field id, element index, referenced object number) of every object reference."""
        out = []
        for fid, _v, _e in self.fields:
            k = schema.kind(self.cls, fid)
            if k in schema.REF_KINDS:
                for i, r in enumerate(self.arr(fid)):
                    out.append((fid, i, r))
        return out


class PFile:
    """A `.particles` file: header, class table, objects (1-based numbers in references; `objects[n - 1]`), strings."""

    def __init__(self, version: int = 0x01050902, build: int = 8000, reserved: int = 0):
        self.version, self.build, self.reserved = version, build, reserved
        self.classes: list[int] = []        # string index of each class, in table order
        self.objects: list[PObj] = []
        self.strings: list[str] = []

    # ------------------------------------------------------------- reading/writing
    @classmethod
    def read(cls, b: bytes) -> "PFile":
        if len(b) < 28:
            raise ValueError(f"too short for a baked PopcornFX effect ({len(b)} bytes)")
        magic, ver, build, nobj, ncls, so, res = struct.unpack_from("<7I", b, 0)
        if magic != MAGIC:
            raise ValueError(f"not a D2R particle file (magic {magic:#x})")
        if not 28 + 8 * ncls <= so <= len(b) - 4:
            raise ValueError("bad header (string table offset)")
        pf = cls(ver, build, res)
        counts = []
        for i in range(ncls):
            c, n = struct.unpack_from("<II", b, 28 + 8 * i)
            pf.classes.append(c)
            counts.append(n)
        n = struct.unpack_from("<I", b, so)[0]
        p = so + 4
        for _ in range(n):
            if p >= len(b) or p + 1 + b[p] > len(b):
                raise ValueError("truncated string table")
            pf.strings.append(b[p + 1:p + 1 + b[p]].decode("latin-1"))
            p += 1 + b[p]
        if p != len(b):
            raise ValueError(f"{len(b) - p} bytes after the string table")
        p = 28 + 8 * ncls
        while p < so:
            size = struct.unpack_from("<I", b, p)[0]
            if size < 5 or p + 4 + size > so:
                raise ValueError(f"object {len(pf.objects) + 1} overruns the object area")
            ci = struct.unpack_from("<I", b, p + 5)[0]
            if ci >= len(pf.strings):
                raise ValueError(f"object {len(pf.objects) + 1} names class {ci}, outside the string table")
            pf.objects.append(PObj.decode(pf.strings[ci], b[p + 9:p + 4 + size], b[p + 4]))
            p += 4 + size
        if p != so or len(pf.objects) != nobj:
            raise ValueError("object area does not match the header")
        if counts != pf._counts():
            raise ValueError("class table counts do not match the objects")
        return pf

    def _counts(self) -> list[int]:
        names = [self.strings[c] for c in self.classes]
        return [sum(1 for o in self.objects if o.cls == n) for n in names]

    def data(self) -> bytes:
        """The file's bytes (class counts and offsets recomputed; unchanged files come back byte for byte)."""
        names = [self.strings[c] for c in self.classes]
        for o in self.objects:
            if o.cls not in names:
                self.classes.append(self.intern(o.cls))
                names.append(o.cls)
        body = []
        for o in self.objects:
            p = o.encode()
            body.append(struct.pack("<IBI", 5 + len(p), o.marker, self.intern(o.cls)) + p)
        table = b"".join(struct.pack("<II", c, n) for c, n in zip(self.classes, self._counts()))
        body_b = b"".join(body)
        so = 28 + len(table) + len(body_b)
        strs = [struct.pack("<I", len(self.strings))]
        for s in self.strings:
            e = s.encode("latin-1")
            if len(e) > 255:
                raise ValueError(f"string longer than 255 bytes: {s[:60]}...")
            strs.append(bytes([len(e)]) + e)
        head = struct.pack("<7I", MAGIC, self.version, self.build, len(self.objects), len(self.classes), so,
                           self.reserved)
        return head + table + body_b + b"".join(strs)

    # ------------------------------------------------------------- lookup
    def obj(self, ref: int) -> PObj:
        """The object a 1-based reference names."""
        if not 1 <= ref <= len(self.objects):
            raise IndexError(f"no object {ref} (file has {len(self.objects)})")
        return self.objects[ref - 1]

    def find(self, cls: str) -> list[int]:
        """1-based numbers of the objects of a class (a prefix ending in '_' matches a family, e.g. sampler data)."""
        return [i + 1 for i, o in enumerate(self.objects)
                if o.cls == cls or (cls.endswith("_") and o.cls.startswith(cls))]

    def string(self, i: int | None) -> str | None:
        return None if i is None or i == NONE or i >= len(self.strings) else self.strings[i]

    def str_field(self, o: PObj, fid: int) -> str | None:
        return self.string(o.u32(fid, None)) if o.has(fid) else None

    def loc_text(self, o: PObj, fid: int) -> str | None:
        """The first non-empty text of a localized-string field."""
        for _lang, i in o.loc(fid):
            s = self.string(i)
            if s:
                return s
        return None

    # ------------------------------------------------------------- strings
    def intern(self, s: str) -> int:
        """The index of string `s`, added at the end when the table lacks it."""
        if len(s.encode("latin-1")) > 255:
            raise ValueError(f"string longer than 255 bytes: {s[:60]}...")
        try:
            return self.strings.index(s)
        except ValueError:
            self.strings.append(s)
            return len(self.strings) - 1

    def replace_string(self, old: str, new: str) -> int:
        """Every table entry `old` becomes `new` (every user of it changes). Returns how many entries changed."""
        n = 0
        for k, s in enumerate(self.strings):
            if s == old:
                self.strings[k] = new
                n += 1
        return n

    def set_str(self, o: PObj, fid: int, s: str) -> None:
        o.set_u32(fid, self.intern(s))

    # ------------------------------------------------------------- structure
    def referrers(self, ref: int) -> list[tuple[int, int, int]]:
        """(object number, field id, element index) of every reference to object `ref`."""
        return [(i + 1, fid, k) for i, o in enumerate(self.objects) for fid, k, r in o.refs() if r == ref]

    def closure(self, refs) -> list[int]:
        """The objects `refs` name and everything they reference, in first-visit (depth-first) order."""
        seen, out, stack = set(), [], list(reversed(list(refs)))
        while stack:
            r = stack.pop()
            if r in seen or not 1 <= r <= len(self.objects):
                continue
            seen.add(r)
            out.append(r)
            stack.extend(reversed([x for _f, _k, x in self.obj(r).refs()]))
        return out

    def unreachable(self, roots=(1,)) -> list[int]:
        """Objects not reachable from `roots` (object 1 is the CParticleEffect)."""
        keep = set(self.closure(roots))
        return [i for i in range(1, len(self.objects) + 1) if i not in keep]

    def add(self, o: PObj) -> int:
        """Append an object; returns its number."""
        self.objects.append(o)
        return len(self.objects)

    def new(self, cls: str, **fields) -> PObj:
        """A new object of a class (not yet added): fields by schema name; ints, strings (interned for string fields),
        lists (arrays) or bytes."""
        o = PObj(cls)
        for n, v in fields.items():
            fid = schema.field_id(cls, n)
            k = schema.kind(cls, fid)
            if isinstance(v, bytes):
                o.set(fid, v)
            elif isinstance(v, str):
                o.set_u32(fid, self.intern(v))
            elif isinstance(v, (list, tuple)):
                if k == "strs":
                    v = [x if isinstance(x, int) else self.intern(x) for x in v]
                o.set_arr(fid, v, "f" if k == "f32s" else "I")
            else:
                o.set_u32(fid, int(v))
        return o

    def remove(self, refs, drop_dangling: bool = True) -> dict[int, int]:
        """Remove objects and renumber every reference. A reference to a removed object is dropped from arrays; a single
        reference to one raises unless `drop_dangling` (then the field is removed). Returns {old number: new number}."""
        gone = set(refs)
        mapping, n = {}, 0
        for i in range(1, len(self.objects) + 1):
            if i not in gone:
                n += 1
                mapping[i] = n
        keep = []
        for i, o in enumerate(self.objects, 1):
            if i in gone:
                continue
            for fid, v, _e in list(o.fields):
                k = schema.kind(o.cls, fid)
                if k == "objs":
                    o.set_arr(fid, [mapping[r] for r in o.arr(fid) if r in mapping])
                elif k == "obj":
                    r = o.u32(fid)
                    if r in mapping:
                        o.set_u32(fid, mapping[r])
                    elif drop_dangling:
                        o.set(fid, None)
                    else:
                        raise ValueError(f"object {i} ({o.cls}) field {fid} refers to removed object {r}")
            keep.append(o)
        self.objects = keep
        names = {o.cls for o in keep}
        self.classes = [c for c in self.classes if self.strings[c] in names]
        return mapping

    def copy_from(self, other: "PFile", refs) -> dict[int, int]:
        """Copy objects of `other` (and everything they reference) to the end of this file, strings merged and
        references renumbered. Returns {other's number: new number}; objects are copied once per call."""
        order = other.closure(refs)
        base = len(self.objects)
        mapping = {r: base + k + 1 for k, r in enumerate(order)}
        for r in order:
            src = other.obj(r)
            o = PObj(src.cls, [list(f) for f in src.fields], src.marker, src.raw)
            for f in o.fields:
                fid = f[0]
                k = schema.kind(o.cls, fid)
                if k == "objs":
                    o.set_arr(fid, [mapping.get(x, x) for x in o.arr(fid)])
                elif k == "obj":
                    o.set_u32(fid, mapping.get(o.u32(fid), o.u32(fid)))
                elif k in ("str", "strs"):
                    vals = [x if x == NONE else self.intern(other.strings[x]) for x in o.arr(fid)]
                    if k == "str":
                        o.set_u32(fid, vals[0])
                    else:
                        o.set_arr(fid, vals)
                elif k == "loc":
                    pairs = [(lang, self.intern(other.strings[s])) for lang, s in o.loc(fid)]
                    o.set(fid, b"".join(struct.pack("<II", *p) for p in pairs), 8)
            self.objects.append(o)
        return mapping

    def check(self) -> list[str]:
        """Problems: references out of range or to an object of the wrong class, strings out of range."""
        bad = []
        for i, o in enumerate(self.objects, 1):
            for fid, _v, _e in o.fields:
                k = schema.kind(o.cls, fid)
                if k in schema.REF_KINDS:
                    want = schema.TARGET.get((o.cls, fid))
                    for r in o.arr(fid):
                        if not 1 <= r <= len(self.objects):
                            bad.append(f"object {i} {o.cls}.{fid}: reference {r} out of range")
                        elif want and not self.objects[r - 1].cls.startswith(want):
                            bad.append(f"object {i} {o.cls}.{fid}: object {r} is {self.objects[r - 1].cls}, not {want}")
                elif k in ("str", "strs"):
                    for s in o.arr(fid):
                        if s != NONE and s >= len(self.strings):
                            bad.append(f"object {i} {o.cls}.{fid}: string {s} out of range")
                elif k == "loc":
                    for _l, s in o.loc(fid):
                        if s >= len(self.strings):
                            bad.append(f"object {i} {o.cls}.{fid}: string {s} out of range")
        return bad
