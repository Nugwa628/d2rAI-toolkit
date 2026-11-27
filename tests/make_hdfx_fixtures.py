"""Builds the synthetic particle files of tests/fixtures/hdfx/ (no game files): the same layout as hdkit.formats.hd_particles
(header, class table, objects, string table) and hdkit.fx.cbvm (CCompilerBlobCache scripts), in the shapes the coverage
survey reads. Run: python tests/make_hdfx_fixtures.py   (rewrites the folder; test_hdfx_coverage checks it is current)."""
import struct
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from hdkit.fx import cbvm as hc  # noqa: E402
from hdkit.formats import hd_particles as hp  # noqa: E402

FIXTURES = TOOLS / "tests" / "fixtures" / "hdfx"
MATERIAL = "vfx/materials/{}.pkma"
ON, OFF = hp.ON, hp.OFF


def reg(cls: str, idx: int, typ: str) -> int:
    """A register word as hdkit.fx.cbvm reads it: type in bits 24-29, class in bits 21-22, index in the low bits."""
    return hc.TYPES.index(typ) << 24 | hc.CLASS.index(cls) << 21 | idx


S = lambda c: reg("s", c, "float")  # noqa: E731  (a scalar register)
F4 = lambda c: reg("s", c, "float4")  # noqa: E731


def u32(*v) -> bytes:
    return struct.pack(f"<{len(v)}I", *v)


def arr(words: bytes) -> bytes:
    return u32(len(words) // 4) + words


class Builder:
    """Strings and objects of one file; objects are numbered from 1, as the renderers refer to them."""

    def __init__(self):
        self.strings: list[str] = []
        self.objs: list[tuple[str, bytes]] = []

    def s(self, text: str) -> int:
        if text not in self.strings:
            self.strings.append(text)
        return self.strings.index(text)

    def obj(self, cls: str, fields: list[tuple[int, bytes]]) -> int:
        body = b"\x20" + struct.pack("<IH", self.s(cls), len(fields)) + b"".join(
            struct.pack("<H", f) + v for f, v in fields)
        self.objs.append((cls, struct.pack("<I", len(body)) + body))
        return len(self.objs)

    def prop(self, name: str, on: bool | None, typ: int = 1) -> int:
        """A renderer property: a switch (on / off) or, with on None, a property with no stored value."""
        f = [(0, u32(self.s(name))), (1, u32(typ))]
        if on is not None:
            f.append((2, ON if on else OFF))
        return self.obj(hp.PROPERTY, f)

    def renderer(self, kind: int | None, material: str, props: list[int]) -> int:
        f = [] if kind is None else [(0, u32(kind))]
        return self.obj(hp.RENDERER, f + [(2, u32(len(props), *props)), (4, u32(self.s(MATERIAL.format(material))))])

    def script(self, code: list, fns: list[str] | None) -> int:
        """A CCompilerBlobCache: backend, program (header, code, no constants), two externals, and its function names."""
        c = hc.encode(code)
        prog = u32(3, len(c), 0, 0, 0, 0, 0, 0, 0) + c
        prog += b"\0" * (-len(prog) % 4)
        names = [self.s("Age"), self.s("Color")]
        f = [(0, u32(self.s("CPU/CPU_VM"))), (1, u32(4)), (2, arr(prog)),
             (4, arr(u32(*range(10)))), (5, arr(u32(*names)))]
        if fns is not None:
            f.append((6, arr(u32(*[self.s(n) for n in fns]))))
        return self.obj(hc.BLOB, f)

    def sampler(self, cls: str) -> int:
        return self.obj(cls, [])

    def data(self) -> bytes:
        classes = list(dict.fromkeys(cls for cls, _ in self.objs))
        body = b"".join(b for _, b in self.objs)
        table = b"".join(u32(self.strings.index(c), sum(1 for x, _ in self.objs if x == c)) for c in classes)
        stro = 0x1C + len(table) + len(body)
        head = u32(hp.MAGIC, 0x01050902, 0x1F40, len(self.objs), len(classes), stro, 0)
        tail = u32(len(self.strings)) + b"".join(bytes([len(t)]) + t.encode("latin-1") for t in self.strings)
        return head + table + body + tail


# CBVM scripts (a spawn, an evolve with an engine call, and an empty one): the opcode names of the survey.
SPAWN = [hc.Ins(0x69, [S(0)], [1]), hc.Ins(0x7B, [S(1)], [6, -1, 0], [(8, S(0))]),
         hc.Ins(0x71, [S(2), S(0), S(1)]), hc.Ins(0x6A, [S(2)], [0]), hc.Ins(0x7D)]
EVOLVE = [hc.Ins(0x69, [F4(0)], [1]), hc.Ins(0x7B, [F4(1)], [6, -1, 1], [(8, F4(0))]),
          hc.Ins(0x7B, [F4(3)], [6, -1, 0], [(8, F4(1))]), hc.Ins(0x73, [F4(2), F4(1), F4(0)]),
          hc.Ins(0x6A, [F4(2)], [0]), hc.Ins(0x7D)]
PLAIN = [hc.Ins(0x69, [S(0)], [1]), hc.Ins(0x73, [S(1), S(0), S(0)]), hc.Ins(0x6A, [S(1)], [0]), hc.Ins(0x7D)]


def billboard_blend() -> bytes:
    """One billboard on Diablo_Uber: Blend on, AlphaMask off; a curve sampler; a spawn script calling `rand`."""
    b = Builder()
    props = [b.prop("EnableRendering", True), b.prop("Blend", True), b.prop("AlphaMask", False)]
    b.renderer(0, "Diablo_Uber", props)
    b.sampler("CParticleNodeSamplerData_Curve")
    b.script(SPAWN, ["rand"])
    return b.data()


def mesh_uber() -> bytes:
    """One mesh on Diablo_Uber_Mesh: Blend, Fresnel and SoftParticles on, a float that is not a switch; a turbulence
    sampler; an evolve script calling `xform.l2w` and `effect.age`."""
    b = Builder()
    props = [b.prop("EnableRendering", True), b.prop("Blend", True), b.prop("Fresnel", True),
             b.prop("SoftParticles", True), b.prop("Exposure", None, typ=7)]
    b.renderer(2, "Diablo_Uber_Mesh", props)
    b.sampler("CParticleNodeSamplerData_Turbulence")
    b.script(EVOLVE, ["xform.l2w", "effect.age"])
    return b.data()


def light_decal() -> bytes:
    """A light (kind 5, Diablo_Light) and a decal (kind 5, Diablo_Decal, switched off); curve and shape samplers; a
    script with an empty function table."""
    b = Builder()
    b.renderer(5, "Diablo_Light", [b.prop("EnableRendering", True)])
    b.renderer(5, "Diablo_Decal", [b.prop("EnableRendering", False), b.prop("Blend", True)])
    b.sampler("CParticleNodeSamplerData_Curve")
    b.sampler("CParticleNodeSamplerData_Shape")
    b.script(PLAIN, [])
    return b.data()


def no_renderers() -> bytes:
    """No renderer at all (a layer that only spawns events): a text sampler and a script with no calls."""
    b = Builder()
    b.sampler("CParticleNodeSamplerData_Text")
    b.script(PLAIN, None)
    return b.data()


def bad_magic() -> bytes:
    """Not a particle file: the survey records it as an error and goes on."""
    return b"NOPE" + bytes(60)


# the folder mirrors a root's layout (hd/vfx/particles/...), so the survey's reference match sees the same paths
SUB = "hd/vfx/particles/synth/"
BUILDERS = {SUB + "billboard_blend.particles": billboard_blend, SUB + "mesh_uber.particles": mesh_uber,
            SUB + "light_decal.particles": light_decal, SUB + "no_renderers.particles": no_renderers,
            SUB + "bad_magic.particles": bad_magic}


def build() -> dict[str, bytes]:
    return {name: fn() for name, fn in BUILDERS.items()}


def main() -> int:
    for name, data in build().items():
        (FIXTURES / name).parent.mkdir(parents=True, exist_ok=True)
        (FIXTURES / name).write_bytes(data)
        print(f"{FIXTURES / name} {len(data)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
