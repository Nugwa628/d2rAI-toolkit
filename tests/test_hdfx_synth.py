"""Synthetic `.particles` objects for the hdfx tests (no game files): a property, a renderer, the string table and a small
two-renderer file in the layout hdkit.formats.hd_particles reads. Also a test of that reader on the file."""
import struct
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.formats import hd_particles as hp  # noqa: E402

GRAD = "data/hd/vfx/textures/gradient/T_FX_flame_gradient_02.texture"
MESH = "data/hd/vfx/meshes/fx_mesh_ring.model"
STRINGS = [hp.PROPERTY, hp.RENDERER, "EnableRendering", "GradientRemap.GradientMap", GRAD,
           "vfx/materials/Diablo_Uber.pkma", "Transparent.CameraSortOffset", "General.Mesh", MESH]


def obj(cls: int, fields: list[tuple[int, bytes]]) -> bytes:
    body = b"\x20" + struct.pack("<IH", cls, len(fields)) + b"".join(struct.pack("<H", f) + v for f, v in fields)
    return struct.pack("<I", len(body)) + body


def u32(*v) -> bytes:
    return struct.pack(f"<{len(v)}I", *v)


def prop(name: int, typ: int, value: bytes | None = None, string: int | None = None) -> bytes:
    f = [(0, u32(name)), (1, u32(typ))]
    if value is not None:
        f.append((2, value))
    if string is not None:
        f.append((3, u32(string)))
    return obj(0, f)


def renderer(kind: int | None, refs: list[int]) -> bytes:
    f = [] if kind is None else [(0, u32(kind))]
    return obj(1, f + [(2, u32(len(refs), *refs)), (4, u32(5))])


def particle_file() -> bytes:
    """A mesh renderer (switch on, a gradient, a mesh, a float) and a billboard renderer (switch on, the gradient)."""
    objs = [prop(2, hp.T_BOOL, hp.ON), prop(3, hp.T_TEXTURE, string=4), prop(6, hp.T_FLOAT, struct.pack("<4f", 0, 0, 0, 0)),
            prop(7, hp.T_MESH, string=8), renderer(2, [1, 2, 3, 4]),
            prop(2, hp.T_BOOL, hp.ON), prop(3, hp.T_TEXTURE, string=4), renderer(None, [6, 7])]
    body = b"".join(objs)
    classes = u32(0, 6, 1, 2)
    stro = 0x1C + len(classes) + len(body)
    head = u32(hp.MAGIC, 0x01050902, 0x1F40, len(objs), 2, stro, 0)
    tail = u32(len(STRINGS)) + b"".join(bytes([len(s)]) + s.encode() for s in STRINGS)
    return head + classes + body + tail


class Reading(unittest.TestCase):
    def test_round_trip_and_renderers(self):
        data = particle_file()
        pf = hp.ParticleFile(data)
        self.assertEqual(pf.data(), data)
        rs = pf.renderers()
        self.assertEqual([r.kind for r in rs], ["mesh", "billboard"])
        self.assertTrue(all(r.enabled for r in rs))
        self.assertEqual(pf.prop_string(rs[0].props["General.Mesh"]), MESH)
        self.assertEqual(rs[1].material, "vfx/materials/Diablo_Uber.pkma")
        self.assertEqual(pf.textures(), [GRAD, MESH])


if __name__ == "__main__":
    unittest.main()
