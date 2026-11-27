"""hdkit.fx.shad: SHAD container reader, permutation index, Uber keyword rules (synthetic containers, no game files).
A check over the real containers runs when HDFX_SHADERS (or the default research folder) is present."""
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.fx import shad  # noqa: E402

HDR = 28


def chunk(tag: bytes, ident, count: int, flags: int, body: bytes, hdr: int = HDR, doff: int | None = None) -> bytes:
    ident = ident if isinstance(ident, bytes) else struct.pack("<I", ident)
    doff = hdr if doff is None else doff
    return tag + ident + struct.pack("<IiIII", count, flags, hdr, doff, hdr + len(body)) + body


def fake_dxbc(tag: int) -> bytes:
    """Minimal DXBC: header + one ISGN chunk with one float4 TEXCOORD0 in v3; `tag` goes into the checksum."""
    name = b"TEXCOORD\0\0\0\0"
    sig = struct.pack("<II", 1, 8) + struct.pack("<IIIIIBBH", 8 + 24, 0, 0, 3, 3, 0xF, 0xF, 0) + name
    ch = b"ISGN" + struct.pack("<I", len(sig)) + sig
    total = 32 + 4 + len(ch)
    return b"DXBC" + struct.pack("<4I", tag, 0, 0, 0) + struct.pack("<III", 1, total, 1) + struct.pack("<I", 36) + ch


def build(symbols: list[tuple[str, list[str]]], tex=lambda i: 1 << (i % 7)) -> bytes:
    strings = []
    for name, vals in symbols:
        for s in [name, *vals]:
            if s not in strings:
                strings.append(s)
    blob, offs = b"", []
    for s in strings:
        offs.append(len(blob))
        blob += s.encode() + b"\0"
    txt = chunk(b"TXT[", b"TXT[", len(strings), 0, struct.pack(f"<{len(offs)}I", *offs) + blob,
                doff=HDR + 4 * len(offs))
    syms = b"".join(chunk(b"SYM ", strings.index(n), len(v), 4, struct.pack(f"<{len(v)}I",
                          *[strings.index(x) for x in v])) for n, v in symbols)
    sym = chunk(b"SYM[", b"SYM[", len(symbols), -1, syms)
    n = 1
    for _, v in symbols:
        n *= len(v)
    blobs, poffs = b"", []
    for i in range(n):
        dx = fake_dxbc(i)
        desc = struct.pack("<4I", 3, 0, 0, 0) + struct.pack("<QQQQ", tex(i), 0, 0x120, 0x410) + struct.pack(
            "<II", len(dx), 4)
        poffs.append(len(blobs))
        blobs += b"BTCD" + b"BTCD" + struct.pack("<IIIII", 1, 0x14 + len(desc) + len(dx), 0x24, 0x24,
                                                 0x24 + len(desc) + len(dx)) + b"\0" * 8 + desc + dx
    prm = chunk(b"PRM[", b"PRM[", n, -1, struct.pack(f"<{n}I", *poffs) + blobs, doff=HDR + 4 * n)
    shmd = chunk(b"SHMD", 4, 1, -1, prm)
    body = txt + sym + shmd
    return b"SHADSHAD" + struct.pack("<IiIII", 3, -1, HDR, HDR, HDR + len(body)) + body


class ShadTest(unittest.TestCase):
    SYMS = [("UV_MODE", ["0", "1", "2"]), ("FRESNEL", ["0", "1"]), ("SKINNING_MODE", ["0", "1", "4", "8"])]

    def setUp(self):
        self.s = shad.Shad.read(build(self.SYMS))

    def test_header(self):
        self.assertEqual([x.name for x in self.s.symbols], ["UV_MODE", "FRESNEL", "SKINNING_MODE"])
        self.assertEqual(self.s.symbols[2].values, ["0", "1", "4", "8"])
        self.assertEqual(self.s.count, 24)

    def test_index_first_keyword_fastest(self):
        self.assertEqual(self.s.index({}), 0)
        self.assertEqual(self.s.index({"UV_MODE": 1}), 1)
        self.assertEqual(self.s.index({"FRESNEL": True}), 3)
        self.assertEqual(self.s.index({"SKINNING_MODE": "4"}), 12)
        self.assertEqual(self.s.index({"UV_MODE": "2", "FRESNEL": "1", "SKINNING_MODE": "8"}), 23)
        for i in range(self.s.count):
            self.assertEqual(self.s.index(self.s.keywords(i)), i)

    def test_unknown_and_bad_values(self):
        self.assertEqual(self.s.index({"POPCORN_BLEND": "1"}), 0)          # other container's keyword: ignored
        with self.assertRaises(KeyError):
            self.s.index({"POPCORN_BLEND": "1"}, strict=True)
        self.assertEqual(self.s.index({"FRESNEL": "2"}), 3)               # non-zero value it lacks -> "1"
        with self.assertRaises(ValueError):
            self.s.index({"SKINNING_MODE": "3"})

    def test_permutation_blob(self):
        p = self.s.permutation(5)
        self.assertEqual(p.keywords, {"UV_MODE": "2", "FRESNEL": "1", "SKINNING_MODE": "0"})
        self.assertEqual(p.textures, [5])
        self.assertEqual(p.samplers, [5, 8])
        self.assertEqual(p.cbuffers, [4, 10])
        self.assertEqual(p.dxbc[:4], b"DXBC")
        self.assertEqual(struct.unpack_from("<I", p.dxbc, 4)[0], 5)
        self.assertEqual(p.signature(), [{"semantic": "TEXCOORD", "index": 0, "register": 3, "mask": "xyzw",
                                          "sv": 0, "type": "float"}])

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            shad.Shad.read(b"NOPE" * 10)
        b = bytearray(build(self.SYMS))
        b[24:28] = struct.pack("<I", len(b) + 4)
        with self.assertRaises(ValueError):
            shad.Shad.read(bytes(b))


class KeywordTest(unittest.TestCase):
    @staticmethod
    def get(d):
        return lambda name: d.get(name)

    def test_blend_mode(self):
        g = self.get
        self.assertEqual(shad.blend_mode(g({"Transparency": True})), 1)                       # Additive
        self.assertEqual(shad.blend_mode(g({"Transparency": True, "Transparency.Type": 1})), 0)
        self.assertEqual(shad.blend_mode(g({"Transparency": True, "Transparency.Type": 2})), 3)
        self.assertEqual(shad.blend_mode(g({"Transparency": True, "Transparency.Type": 3})), 2)
        self.assertEqual(shad.blend_mode(g({"Transparency": True, "Transparency.Type": 4})), 4)
        self.assertEqual(shad.blend_mode(g({"Transparent": True, "Transparent.Type": 2})), 3)
        self.assertEqual(shad.blend_mode(g({"Transparent": True, "Transparent.Type": 9})), 1)

    def test_mephisto_like_mesh(self):
        props = {"Transparency": True, "Opaque": True, "Blend": True, "Blend.TextureBlend": "noise.texture",
                 "Blend.BlendOp": 1, "BlendMask": True, "BlendMask.Mask": "gauss.texture", "AlphaMask": True,
                 "Fresnel": True, "VertexDisplacement": True, "SoftParticles": True,
                 "SoftParticles.SoftnessDistance": 1.5}
        kw = shad.uber_keywords(self.get(props), "mesh")
        on = {k for k, v in kw.items() if v != "0"}
        self.assertEqual(on, {"POPCORN_ADDITIVE", "POPCORN_BLEND", "POPCORN_BLEND_MASK", "FRESNEL",
                              "VERTEX_DISPLACEMENT", "SOFT_PARTICLES", "MESH"})
        s = shad.Shad.read(build([("FRESNEL", ["0", "1"]), ("SOFT_PARTICLES", ["0", "1"]),
                                  ("POPCORN_LIGHTING", ["0", "1", "2"]), ("POPCORN_ADDITIVE", ["0", "1"])]))
        self.assertEqual(s.index(kw), 1 + 2 + 0 + 12)

    def test_billboard_rules(self):
        g = self.get
        kw = shad.uber_keywords(g({"Transparent": True, "Transparent.Type": 2, "Atlas": True, "FastLit": True,
                                   "SoftParticles": True, "Blend": True}), "billboard", lit=1)
        self.assertEqual((kw["UV_MODE"], kw["POPCORN_ADDITIVE"], kw["POPCORN_LIGHTING"]), ("2", "0", "1"))
        self.assertEqual(kw["SOFT_PARTICLES"], "0")     # no softness distance
        self.assertEqual(kw["POPCORN_BLEND"], "0")      # Blend on but no blend texture and BlendOp 0
        kw = shad.uber_keywords(g({"Transparent": True, "CorrectDeformation": True, "Atlas": True}), "ribbon")
        self.assertEqual(kw["UV_MODE"], "1")
        kw = shad.uber_keywords(g({"AlphaRemap": True, "GradientRemap": True}), "billboard",
                                inputs={"GradientRemap.GradientCursor"})
        self.assertEqual((kw["ALPHA_REMAP"], kw["GRADIENT_REMAP"]), ("0", "1"))


ROOT = Path((os.environ.get("HDFX_SHADERS") or "/nonexistent"))


@unittest.skipUnless((ROOT / "ps_popcorn_uber_mesh_p.bin").exists(), "game shader containers not present")
class RealContainers(unittest.TestCase):
    def test_uber_mesh(self):
        s = shad.Shad.read(ROOT / "ps_popcorn_uber_mesh_p.bin")
        self.assertEqual(s.count, 1536)
        p = s.permutation(s.index({"UV_DISTORTION": 1}))
        self.assertEqual(p.index, 1)
        self.assertIn(14, p.textures)                    # the distortion texture slot appears
        self.assertNotIn(14, s.permutation(0).textures)
        self.assertEqual(s.permutation(1535).dxbc[:4], b"DXBC")


if __name__ == "__main__":
    unittest.main()
