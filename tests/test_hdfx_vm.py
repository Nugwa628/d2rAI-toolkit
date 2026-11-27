"""Tests of hdkit.fx.cbvm tables, hdkit.fx.vm (the CBVM interpreter) and hdkit.fx.sim (the runtime).

No game files needed: scripts are assembled here and a small effect (an emitter that kicks an event into a sparks
layer) is built byte by byte. With the extracted D2R data present (HDFX_D2R_ROOT or the default path below), Mephisto's
ambient effect is also run end to end.
Run: python -m unittest tests.test_hdfx_vm"""
import os
import struct
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import cbvm as cb  # noqa: E402
from hdkit.fx import routing as rt  # noqa: E402
from hdkit.fx import sim as S  # noqa: E402
from hdkit.fx import vm  # noqa: E402

D2R = Path((os.environ.get("HDFX_D2R_ROOT") or "/nonexistent"))
MEPHISTO = D2R / "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles"
T = {n: i for i, n in enumerate(cb.TYPES)}
T["orientation"] = cb.ORIENTATION


def reg(cls: str, idx: int, typ: str) -> int:
    return T[typ] << 24 | cb.CLASS.index(cls) << 21 | idx


def const(*vals, kind="f") -> bytes:
    v = list(vals)
    return struct.pack("<8f" if kind == "f" else "<8i", *((v * 8)[:8]))


def script(code, consts=(), exts=(), fns=None, kind=None, obj=0) -> cb.Script:
    """exts: (name, type name, word3 (0 stream, 2 context), word4)."""
    descs = [[0, (T[t] - 1) if t != "class" else cb.NONE, 4, w3, w4] for _n, t, w3, w4 in exts]
    return cb.Script(obj, [0, 2, 4, 5, 6], "CPU/CPU_VM", kind, [32, 0, 0, 0, 0, 0, 0, 0, 0], list(code), list(consts),
                     b"", descs, [n for n, *_ in exts], fns)


I = cb.Ins
s, c, u = (lambda i, t="float": reg("s", i, t)), (lambda i, t="float": reg("c", i, t)), (lambda i, t="float": reg("u", i, t))


def fold(code, consts=(), exts=(), fns=None, n=1, streams=None, **kw):
    sc = script(code, consts, exts, fns)
    env = vm.Env(sc, n, **kw)
    env.streams.update(streams or {})
    vm.run(sc, n, env)
    return env


OUT = [("out", "float", 0, 2)]
OUT3 = [("out", "float3", 0, 2)]
OUT4 = [("out", "float4", 0, 2)]
OUTB = [("out", "bool", 0, 2)]


class Tables(unittest.TestCase):
    def test_swizzle_selector(self):
        self.assertEqual(cb.swz_select([0x0C, 0x08, 0x00], 2), [0, 1])          # int3 -> int2: .xy
        self.assertEqual(cb.swz_text([0xBE, 0x04, 0x10], 3), "0x0")             # float -> float3 (0, x, 0)
        self.assertEqual(cb.swz_text([0xD9, 0x6D, 0x11], 4), "111x")            # float -> float4 (1, 1, 1, a)
        self.assertEqual(cb.swz_text([0x88, 0x88, 0xA5], 4), "xyz1")            # float3 -> float4 (xyz, 1)
        self.assertEqual(cb.swz_text([0x03, 0x03, 0x00], 1), "w")

    def test_operator_names(self):
        self.assertEqual(cb.MOP[8:18], ["and", "or", "xor", "not", "cmplt", "cmple", "cmpgt", "cmpge", "cmpeq", "cmpne"])
        self.assertEqual(cb.COMPONENTS[T["float3"]], 3)
        self.assertEqual(cb.COMPONENTS[cb.ORIENTATION], 4)
        self.assertIn("cmple", cb.script_text(script([I(0x70, [s(0, "bool"), s(1), s(2)], [13])])))

    def test_hd_cbvm_is_the_same_module(self):
        from hdkit.fx import cbvm as hd_cbvm
        self.assertIs(hd_cbvm.Ins, cb.Ins)
        self.assertIs(hd_cbvm.scripts, cb.scripts)


class Opcodes(unittest.TestCase):
    """Constant-folded scripts against known results."""

    def out(self, code, consts, exts=OUT, **kw):
        return fold(code, consts, exts, **kw).streams["out"]

    def test_arithmetic(self):
        k = [const(2.0), const(3.0), const(0.5)]
        self.assertEqual(self.out([I(0x75, [s(0), c(0), c(1), c(2)]), I(0x6A, [s(0)], [0])], k)[0, 0], 6.5)   # madd
        self.assertEqual(self.out([I(0x72, [s(0), c(0), c(1)]), I(0x6A, [s(0)], [0])], k)[0, 0], -1.0)        # sub
        self.assertEqual(self.out([I(0x74, [s(0), c(1), c(0)]), I(0x6A, [s(0)], [0])], k)[0, 0], 1.5)         # div
        self.assertEqual(self.out([I(0x70, [s(0), c(0), cb.NONE], [5]),
                                   I(0x6A, [s(0)], [0])], k)[0, 0], -2.0)                                  # mop.neg

    def test_compare_logic_select(self):
        k = [const(2.0), const(3.0), const(10.0), const(20.0)]
        code = [I(0x70, [s(0, "bool"), c(0), c(1)], [12]),       # 2 < 3
                I(0x70, [s(1, "bool"), c(1), c(0)], [13]),       # 3 <= 2
                I(0x70, [s(2, "bool"), s(0, "bool"), s(1, "bool")], [9]),   # or
                I(0x70, [s(3, "bool"), s(1, "bool"), cb.NONE], [11]),       # not
                I(0x70, [s(2, "bool"), s(2, "bool"), s(3, "bool")], [8]),   # and
                I(0x7A, [s(4), c(2), c(3), s(2, "bool")]),       # sel: m ? b : a
                I(0x6A, [s(4)], [0])]
        self.assertEqual(self.out(code, k)[0, 0], 20.0)
        code[5] = I(0x7A, [s(4), c(2), c(3), s(1, "bool")])
        self.assertEqual(self.out(code, k)[0, 0], 10.0)

    def test_swizzle_vct_dup_cvt(self):
        k = [const(1.0, 2.0, 3.0), const(7.0), const(2.7)]
        code = [I(0x6E, [s(0, "float4"), c(0, "float3")], [0x88, 0x88, 0xA5]), I(0x6A, [s(0, "float4")], [0])]
        np.testing.assert_array_equal(self.out(code, k, OUT4)[0], [1, 2, 3, 1])
        code = [I(0x6E, [s(0, "float3"), c(1)], [0xBE, 0x04, 0x10]), I(0x6A, [s(0, "float3")], [0])]
        np.testing.assert_array_equal(self.out(code, k, OUT3)[0], [0, 7, 0])
        code = [I(0x6D, [s(0, "float4"), c(0, "float3"), c(1)]), I(0x6A, [s(0, "float4")], [0])]
        np.testing.assert_array_equal(self.out(code, k, OUT4)[0], [1, 2, 3, 7])
        code = [I(0x6F, [s(0, "float3"), c(1)]), I(0x6A, [s(0, "float3")], [0])]
        np.testing.assert_array_equal(self.out(code, k, OUT3)[0], [7, 7, 7])
        code = [I(0x6C, [s(0, "int"), c(2)]), I(0x6A, [s(0, "int")], [0])]
        self.assertEqual(self.out(code, k, [("out", "int", 0, 2)])[0, 0], 2)

    def test_math(self):
        k = [const(3.0, 4.0, 0.0), const(1.0, 0.0, 0.0), const(0.0, 1.0, 0.0), const(-3.0), const(-1.0), const(1.0),
             const(0.25)]
        m = cb.MATH.index
        code = [I(0x77, [s(0), c(0, "float3")], [m("length")]), I(0x6A, [s(0)], [0])]
        self.assertAlmostEqual(float(self.out(code, k)[0, 0]), 5.0, places=5)
        code = [I(0x78, [s(0, "float3"), c(1, "float3"), c(2, "float3")], [m("cross")]), I(0x6A, [s(0, "float3")], [0])]
        np.testing.assert_array_equal(self.out(code, k, OUT3)[0], [0, 0, 1])
        code = [I(0x78, [s(0), c(0, "float3"), c(1, "float3")], [m("dot")]), I(0x6A, [s(0)], [0])]
        self.assertEqual(self.out(code, k)[0, 0], 3.0)
        code = [I(0x79, [s(0), c(3), c(4), c(5)], [1]), I(0x6A, [s(0)], [0])]       # clamp(-3, -1, 1)
        self.assertEqual(self.out(code, k)[0, 0], -1.0)
        code = [I(0x79, [s(0, "float3"), c(1, "float3"), c(0, "float3"), c(6)], [0]), I(0x6A, [s(0, "float3")], [0])]
        np.testing.assert_allclose(self.out(code, k, OUT3)[0], [1.5, 1.0, 0.0])    # lerp(a, b, 0.25)

    def test_streams_vectorised(self):
        x = np.arange(5, dtype=np.float32).reshape(-1, 1)
        env = fold([I(0x69, [s(0)], [0]), I(0x73, [s(0), s(0), s(0)]), I(0x6A, [s(0)], [0]), I(0x6B, [], [1])],
                   [], [("x", "float", 0, 3), ("z", "float3", 0, 6)], n=5, streams={"x": x})
        np.testing.assert_array_equal(env.streams["x"][:, 0], [0, 1, 4, 9, 16])
        self.assertEqual(env.streams["z"].shape, (5, 3))

    def test_unknown_call_is_a_counted_stub(self):
        env = fold([I(0x7B, [s(0, "float3")], [6, -1, 0], []), I(0x6A, [s(0, "float3")], [0])], [], OUT3,
                   fns=["nobody.knows_float3"])
        self.assertEqual(env.stats["stub_calls"]["nobody.knows_float3"], 1)
        np.testing.assert_array_equal(env.streams["out"][0], [0, 0, 0])

    def test_generate_fires_on_the_first_frame_then_by_period(self):
        st = np.array([[np.float32(0.99999).view(np.int32), 0]], np.int32)

        class C:
            n, env, name, bound, dst_type = 1, vm.Env(), "generate", None, T["int3"]
        out = vm.ENGINE["generate_int2_float_float_float_float_pCtxS"](C, st, np.float32([[0.05]]), np.float32([[0.1]]),
                                                                         np.float32([[0]]), np.float32([[1]]))
        self.assertEqual(out[0, 2], 1)                    # 0.99999 + 0.5 -> one event now
        out2 = vm.ENGINE["generate_int2_float_float_float_float_pCtxS"](C, out[:, :2], np.float32([[0.05]]),
                                                                          np.float32([[0.1]]), 0, 1)
        self.assertEqual(out2[0, 2], 0)                   # 0.49999 + 0.5 -> not yet
        out3 = vm.ENGINE["generate_int2_float_float_float_float_pCtxS"](C, out2[:, :2], np.float32([[0.05]]),
                                                                          np.float32([[0.1]]), 0, 1)
        self.assertEqual(out3[0, 2], 1)                   # 0.99999 + 0.5 -> the next one
        self.assertEqual(out3[0, 1], 2)                   # total in state.y


# ------------------------------------------------------------------------------------------------ synthetic effect
CLASSES = ["CParticleEffect", "CLayerGraphCompileCache", "CLayerGraphCompileCache_LayerSlot",
           "CLayerGraphCompileCache_EventSlot", "CLayerCompileCache", "CCompilerBlobCache", "CLayerCompileCacheField",
           "CLayerCompileCacheEvent", "CLayerCompileCacheEventPayload", "CLayerCompileCacheRenderer",
           "CLayerCompileCacheRendererParticleInput"]


class FileBuilder:
    def __init__(self):
        self.strings = list(CLASSES)
        self.objs: list[tuple[int, bytes]] = []

    def s(self, text: str) -> int:
        if text not in self.strings:
            self.strings.append(text)
        return self.strings.index(text)

    def obj(self, cls: str, fields: list[tuple[int, bytes]]) -> int:
        body = struct.pack("<H", len(fields)) + b"".join(struct.pack("<H", k) + v for k, v in fields)
        self.objs.append((CLASSES.index(cls), body))
        return len(self.objs)        # 1-based

    def reserve(self) -> int:
        self.objs.append(None)
        return len(self.objs)

    def put(self, ref: int, cls: str, fields):
        body = struct.pack("<H", len(fields)) + b"".join(struct.pack("<H", k) + v for k, v in fields)
        self.objs[ref - 1] = (CLASSES.index(cls), body)

    def blob(self, sc: cb.Script) -> int:
        code = cb.encode(sc.code)
        head = [32, len(code), 0, 32 * len(sc.consts), 0, len(sc.consts), 0, 0, 0]
        prog = struct.pack("<9I", *head) + code + b"".join(sc.consts)
        prog += b"\0" * (-len(prog) % 4)
        arr = lambda ws: struct.pack("<I", len(ws)) + struct.pack(f"<{len(ws)}I", *ws)  # noqa: E731
        f = [(0, struct.pack("<I", self.s(sc.backend)))]
        if sc.kind is not None:
            f.append((1, struct.pack("<I", sc.kind)))
        f += [(2, struct.pack("<I", len(prog) // 4) + prog), (4, arr([w for d in sc.ext_desc for w in d])),
              (5, arr([self.s(n) for n in sc.ext_names])), (6, arr([self.s(n) for n in sc.fn_names or []]))]
        return self.obj("CCompilerBlobCache", f)

    def data(self) -> bytes:
        body = b"".join(struct.pack("<I", 5 + len(b)) + b"\x20" + struct.pack("<I", k) + b for k, b in self.objs)
        classes = b"".join(struct.pack("<II", i, sum(1 for k, _b in self.objs if k == i)) for i in range(len(CLASSES)))
        stro = 0x1C + len(classes) + len(body)
        head = struct.pack("<7I", 0xCA000B11, 0x01050902, 0x1F40, len(self.objs), len(CLASSES), stro, 0)
        tail = struct.pack("<I", len(self.strings)) + b"".join(bytes([len(x)]) + x.encode() for x in self.strings)
        return head + classes + body + tail


def arr32(ws):
    return struct.pack("<I", len(ws)) + struct.pack(f"<{len(ws)}I", *ws)


def u32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


CTX = [("ParticleContextS", "class", 2, 1), ("ParticleContextI", "class", 2, 1), ("RandContext", "class", 2, 1)]


def emitter_scripts():
    """Root: immortal while the effect runs; every 0.1 s it kicks __e_Child with a random position in the payload."""
    spawn = script([I(0x6B, [], [0]), I(0x6A, [c(0, "int2")], [1])],
                   [const(np.float32(0.99999).view(np.int32), 0, kind="i")],
                   [("self.invLife", "float", 0, 6), ("0", "int2", 0, 2)])
    ex = [("self.invLife", "float", 0, 3), ("0", "int2", 0, 3), ("effect.dt", "float", 2, 1),
          ("__e_Child", "class", 2, 7)] + CTX
    fns = ["effect.isRunning_pCtxI", "self.kill_bool_pCtxS", "generate_int2_float_float_float_float_pCtxS",
           "particleEvent:initPayload_int_int3_pCtxS", "rand_float3_float3_RandCtx",
           "buildPayloadElement_int3_float3_float3_int_pCtxS", "particleEvent:appendPayload_int_int_int_pCtxS",
           "particleEvent:kick_int_RandCtx_pCtxS"]
    k = [const(0.1), const(0.0), const(1.0), const(-1.0), const(0, kind="i"), const(256, kind="i")]
    ctxS, ctxI, rnd = reg("s", 9, "class"), reg("u", 9, "class"), reg("s", 10, "class")
    code = [I(0x69, [ctxI], [5]), I(0x69, [ctxS], [4]), I(0x69, [rnd], [6]),
            I(0x7B, [u(0, "bool")], [6, -1, 0], [(8, ctxI)]),
            I(0x70, [u(1, "bool"), u(0, "bool"), cb.NONE], [11]),
            I(0x7B, [cb.NONE], [0, -1, 1], [(0x18, u(1, "bool")), (8, ctxS)]),
            I(0x69, [s(0, "int2")], [1]), I(0x69, [s(1)], [2]),
            I(0x7B, [s(2, "int3")], [6, -1, 2], [(0x18, s(0, "int2")), (0x1A, s(1)), (0xA, c(0)), (0xA, c(1)),
                                                  (0xA, c(2)), (8, ctxS)]),
            I(0x7B, [s(3, "int")], [6, 3, 3], [(0, c(4, "int")), (0x18, s(2, "int3")), (8, ctxS)]),
            I(0x7B, [s(4, "float3")], [6, -1, 4], [(8, c(3, "float3")), (8, c(2, "float3")), (8, rnd)]),
            I(0x7B, [s(5, "int")], [6, -1, 5], [(0x18, s(2, "int3")), (0x18, s(4, "float3")), (0x18, s(4, "float3")),
                                                (0, c(5, "int")), (8, ctxS)]),
            I(0x7B, [s(6, "int")], [6, 3, 6], [(0x18, s(3, "int")), (0, c(4, "int")), (0x18, s(5, "int")), (8, ctxS)]),
            I(0x7B, [cb.NONE], [0, 3, 7], [(0x18, s(6, "int")), (8, rnd), (8, ctxS)]),
            I(0x6E, [s(7, "int2"), s(2, "int3")], [0x0C, 0x08, 0x00]), I(0x6A, [s(7, "int2")], [1])]
    return spawn, script(code, k, ex, fns, kind=4)


def spark_scripts(eid: int = 0):
    """Sparks: 0.5 s of life, start at the payload's position (element `eid`), move with a random velocity."""
    ctxS, rnd = reg("s", 9, "class"), reg("s", 10, "class")
    ex = [("self.invLife", "float", 0, 2), ("0", "float3", 0, 2), ("1", "float3", 0, 2)] + CTX
    fns = ["extractPayloadElementF3_int_pCtxS", "rand_float3_float3_RandCtx"]
    k = [const(2.0), const(eid, kind="i"), const(-1.0), const(1.0)]
    spawn = script([I(0x69, [ctxS], [3]), I(0x69, [rnd], [5]), I(0x6A, [c(0)], [0]),
                    I(0x7B, [s(0, "float3")], [6, -1, 0], [(0, c(1, "int")), (8, ctxS)]), I(0x6A, [s(0, "float3")], [1]),
                    I(0x7B, [s(1, "float3")], [6, -1, 1], [(8, c(2, "float3")), (8, c(3, "float3")), (8, rnd)]),
                    I(0x6A, [s(1, "float3")], [2])], k, ex, fns)
    ex2 = [("0", "float3", 0, 3), ("1", "float3", 0, 1), ("scene.dt", "float", 2, 1)]
    evolve = script([I(0x69, [s(0, "float3")], [0]), I(0x69, [s(1, "float3")], [1]), I(0x69, [u(0)], [2]),
                     I(0x75, [s(0, "float3"), s(1, "float3"), u(0), s(0, "float3")]), I(0x6A, [s(0, "float3")], [0])],
                    [], ex2, [], kind=4)
    return spawn, evolve


def synthetic_effect() -> bytes:
    b = FileBuilder()
    effect = b.reserve()
    graph = b.reserve()
    layers = []
    for name, (sp, ev) in (("Root", emitter_scripts()), ("Sparks", spark_scripts())):
        refs = [b.blob(sp), b.blob(ev)]
        layers.append(b.obj("CLayerCompileCache", [(9, arr32(refs)), (20, u32(b.s(name)))]))
    slot0 = b.obj("CLayerGraphCompileCache_LayerSlot", [(0, u32(layers[0])), (1, arr32([0])), (2, arr32([1]))])
    slot1 = b.obj("CLayerGraphCompileCache_LayerSlot", [(0, u32(layers[1])), (1, arr32([1])), (2, arr32([]))])
    ev0 = b.obj("CLayerGraphCompileCache_EventSlot", [(1, u32(-1)), (2, arr32([0]))])
    ev1 = b.obj("CLayerGraphCompileCache_EventSlot", [(0, u32(b.s("__e_Child"))), (2, arr32([1]))])
    b.put(graph, "CLayerGraphCompileCache", [(0, u32(b.s("PC"))), (3, arr32([slot0, slot1])), (4, arr32([ev0, ev1]))])
    b.put(effect, "CParticleEffect", [(1, arr32([graph]))])
    return b.data()


class Runtime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synthetic_effect()

    def test_routing(self):
        r = rt.load(self.data)
        self.assertEqual([sl.name for sl in r.slots], ["Root", "Sparks"])
        self.assertEqual(r.entry(), [0])
        self.assertEqual(r.event_for(0, "__e_Child").targets, [1])
        self.assertEqual(r.slots[1].evolve.kind, 4)

    def test_emits_moves_and_kills(self):
        sim = S.Sim(self.data, seed=3)
        sim.step(1.0)
        st = sim.state()
        self.assertEqual(st.layers["Root"].count, 1)
        sparks = st.layers["Sparks"]
        self.assertEqual(sim.stats["spawned"]["Sparks"], 10)           # frame 1, then every 3rd frame (0.1 s)
        self.assertTrue(4 <= sparks.count <= 6)                       # each lives 0.5 s
        self.assertTrue((sparks.fields["self.lifeRatio"] < 1).all())
        self.assertFalse(sim.stats["stub_calls"])
        self.assertFalse(sim.stats["errors"])
        self.assertGreater(float(np.abs(sparks.fields["0"]).max()), 0.0)

    def test_determinism_per_seed(self):
        a, b, c2 = (S.Sim(self.data, seed=k) for k in (7, 7, 8))
        for x in (a, b, c2):
            x.step(0.8)
        np.testing.assert_array_equal(a.state().layers["Sparks"].fields["0"], b.state().layers["Sparks"].fields["0"])
        self.assertFalse(np.array_equal(a.state().layers["Sparks"].fields["0"], c2.state().layers["Sparks"].fields["0"]))
        a.seek(0.8)
        np.testing.assert_array_equal(a.state().layers["Sparks"].fields["0"], b.state().layers["Sparks"].fields["0"])

    def test_stop(self):
        sim = S.Sim(self.data)
        sim.step(0.5)
        sim.stop()
        sim.step(0.7)
        self.assertEqual(sim.state().layers["Root"].count, 0)
        self.assertEqual(sim.state().layers["Sparks"].count, 0)

    def test_summary_json(self):
        import json
        sim = S.Sim(self.data)
        sim.step(0.3)
        json.dumps(S.summary(sim), default=str)


def named_effect() -> bytes:
    """Root kicks __e_Child with one payload element "Pos"; two spark layers read their RootEvent lists: SparksA
    ["Unrelated", "Pos"] reading entry 1 (the position), SparksB the same list reading entry 0 (absent: zeros).
    SparksA has field objects (a colour and a size from the constant pool) and a billboard renderer. Root's second
    event __e_Spawn (flags 0: no script fires it) targets SparksB and never spawns."""
    b = FileBuilder()
    effect, graph = b.reserve(), b.reserve()
    pay = lambda name, t, size: b.obj("CLayerCompileCacheEventPayload", [  # noqa: E731
        (0, u32(b.s(name))), (1, u32(t)), (2, u32(size))])
    p_pos = pay("Pos", 33, 12)
    ev_child = b.obj("CLayerCompileCacheEvent", [(0, u32(b.s("__e_Child"))), (1, u32(1)), (2, arr32([p_pos])),
                                                 (3, u32(12))])
    ev_spawn = b.obj("CLayerCompileCacheEvent", [(0, u32(b.s("__e_Spawn")))])
    sp, ev = emitter_scripts()
    root = b.obj("CLayerCompileCache", [(5, arr32([ev_child, ev_spawn])), (9, arr32([b.blob(sp), b.blob(ev)])),
                                        (20, u32(b.s("Root")))])
    pool = b"".join(struct.pack("<8f", *v) for v in ((0,) * 8, (1, .5, .25, 1) * 2, (.5,) * 8))

    def field(fid, t, size, row=None):
        f = [(0, u32(fid | 0x80000000)), (1, u32(t)), (2, u32(size)), (3, u32(5 if row is not None else 1))]
        return b.obj("CLayerCompileCacheField", f + ([(4, u32(row))] if row is not None else []))
    layers = []
    for name, eid in (("SparksA", 1), ("SparksB", 0)):
        rootev = b.obj("CLayerCompileCacheEvent", [(2, arr32([pay("Unrelated", 33, 12), pay("Pos", 33, 12)])),
                                                   (3, u32(24))])
        flds = [field(0, 31, 4), field(1, 31, 4), field(7, 33, 12), field(8, 33, 12), field(9, 34, 16, 1),
                field(10, 31, 4, 2)]
        inp = lambda k, fi, nm=None: b.obj("CLayerCompileCacheRendererParticleInput", [  # noqa: E731
            (0, u32(k)), (1, u32(fi))] + ([(2, u32(b.s(nm)))] if nm else []))
        rend = b.obj("CLayerCompileCacheRenderer", [(0, u32(0)), (1, arr32([inp(0, 2), inp(1, 5),
                                                                             inp(0, 4, "Color.Color")]))])
        sp2, ev2 = spark_scripts(eid)
        layers.append(b.obj("CLayerCompileCache", [(0, struct.pack("<I", len(pool) // 16) + pool), (2, arr32(flds)),
                                                   (8, arr32([rend])), (9, arr32([b.blob(sp2), b.blob(ev2)])),
                                                   (11, u32(rootev)), (20, u32(b.s(name)))]))
    s0 = b.obj("CLayerGraphCompileCache_LayerSlot", [(0, u32(root)), (1, arr32([0])), (2, arr32([1, 2]))])
    s1 = b.obj("CLayerGraphCompileCache_LayerSlot", [(0, u32(layers[0])), (1, arr32([1])), (2, arr32([]))])
    s2 = b.obj("CLayerGraphCompileCache_LayerSlot", [(0, u32(layers[1])), (1, arr32([1, 2])), (2, arr32([]))])
    e0 = b.obj("CLayerGraphCompileCache_EventSlot", [(1, u32(-1)), (2, arr32([0]))])
    e1 = b.obj("CLayerGraphCompileCache_EventSlot", [(0, u32(b.s("__e_Child"))), (2, arr32([1, 2]))])
    e2 = b.obj("CLayerGraphCompileCache_EventSlot", [(0, u32(b.s("__e_Spawn"))), (2, arr32([2]))])
    b.put(graph, "CLayerGraphCompileCache", [(0, u32(b.s("PC"))), (3, arr32([s0, s1, s2])), (4, arr32([e0, e1, e2]))])
    b.put(effect, "CParticleEffect", [(1, arr32([graph]))])
    return b.data()


class GraphRuntime(unittest.TestCase):
    """Routing through fx/graph: payloads by name, field streams and defaults, renderer inputs, dead events."""

    @classmethod
    def setUpClass(cls):
        cls.data = named_effect()
        cls.sim = S.Sim(cls.data, seed=2)
        cls.sim.step(0.5)
        cls.st = cls.sim.state()

    def test_routing_reads_payload_lists_and_flags(self):
        r = rt.load(self.data)
        self.assertEqual(r.event_for(0, "__e_Child").payload, ["Pos"])
        self.assertEqual(r.slots[1].reads, ["Unrelated", "Pos"])
        self.assertEqual([e.name for e in r.dead_events()], ["__e_Spawn"])
        self.assertEqual(r.slots[1].fields["2"], cb.TYPES.index("float4"))

    def test_payload_by_name(self):
        a, b = self.st.layers["SparksA"], self.st.layers["SparksB"]
        self.assertGreater(a.count, 0)
        self.assertEqual(a.count, b.count)            # __e_Spawn never fires: SparksB only gets __e_Child
        self.assertGreater(float(np.abs(a.fields["0"]).max()), 0.0)
        self.assertGreater(self.sim.stats["payload_absent"]["Unrelated"], 0)
        self.assertFalse(self.sim.stats["doubtful"])

    def test_field_defaults_and_renderer_inputs(self):
        a = self.st.layers["SparksA"]
        ri = a.renderer_inputs[0]
        self.assertEqual(set(ri), {"Position", "Size", "Color.Color"})
        np.testing.assert_allclose(ri["Color.Color"][0], [1, .5, .25, 1])
        np.testing.assert_allclose(ri["Size"][:, 0], 0.5)
        np.testing.assert_array_equal(ri["Position"], a.fields["0"])

    def test_input_names(self):
        self.assertEqual(S.input_name("billboard", "input1"), "Size")
        self.assertEqual(S.input_name("ribbon", "input1"), "Width")
        self.assertEqual(S.input_name("mesh", "input1"), "Scale")
        self.assertEqual(S.input_name("mesh", "input14"), "Orientation")
        self.assertEqual(S.input_name("ribbon", "input10"), "ParentID")
        self.assertEqual(S.input_name("light", "Light.Color"), "Light.Color")


class EngineCalls(unittest.TestCase):
    """generate / trigger / xform / idivmi as D2R.exe does them."""

    class Env:
        def __init__(self, n=1):
            self.n, self.emissions, self.transform = n, [], np.eye(4, dtype=np.float32)

    def call(self, name, n, *args):
        C = vm.CallCtx.__new__(vm.CallCtx)
        C.env, C.n, C.name, C.dst_type, C.ins = self.Env(n), n, name, 0, None
        return C, vm.ENGINE[name](C, *args)

    def test_generate_counts_and_child_times(self):
        st = np.array([[np.float32(0.5).view(np.int32), 3]], np.int32)
        C, out = self.call("generate_int2_float_float_float_float_pCtxS", 1, st, np.float32([[2.0]]),
                           np.float32([[1.0]]), np.float32([[0.0]]), np.float32([[1.0]]))
        self.assertEqual(out[0, 2], 2)                                   # 0.5 + 2 -> 2 children, 0.5 kept
        self.assertAlmostEqual(float(out[:, :1].view(np.float32)[0, 0]), 0.5)
        self.assertEqual(out[0, 1], 5)
        info = C.env.emissions[-1][1]
        np.testing.assert_allclose(vm.child_fracs(info, np.array([2])), [0.25, 0.75])   # (k + 1 - 0.5) / 2
        C, out = self.call("generate_int2_float_float_float_float_pCtxS", 1, st, np.float32([[2.0]]),
                           np.float32([[0.0]]), np.float32([[0.0]]), np.float32([[1.0]]))
        self.assertEqual(out[0, 2], 0)                                   # divisor 0: nothing

    def test_trigger_time_is_clamped(self):
        C, out = self.call("trigger_bool_float_pCtxS", 3, np.array([[1], [0], [1]], bool),
                           np.float32([[0.25], [0.5], [2.0]]))
        self.assertEqual(out[:, 2].tolist(), [1, 0, 1])
        np.testing.assert_allclose(vm.child_fracs(C.env.emissions[-1][1], out[:, 2]), [0.25, 1.0])

    def test_xform_mask(self):
        T = np.eye(4, dtype=np.float32)
        T[:3, :3] = [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]             # 90 degrees about y
        T[:3, 3] = [10, 0, 0]
        v = np.array([1, 0, 0], np.float32)
        app = lambda m: m[:3, :3] @ v + m[:3, 3]  # noqa: E731
        np.testing.assert_allclose(app(vm.xform_matrix(19, T)), [10, 0, -1], atol=1e-6)   # l2w point
        np.testing.assert_allclose(app(vm.xform_matrix(17, T)), [11, 0, 0], atol=1e-6)    # translation only
        np.testing.assert_allclose(app(vm.xform_matrix(18, T)), [0, 0, -1], atol=1e-6)    # direction
        np.testing.assert_allclose(app(vm.xform_matrix(67, T)), [0, 0, -9], atol=1e-6)    # w2l point
        np.testing.assert_allclose(app(vm.xform_matrix(11, T)), v, atol=1e-6)            # w2w, static effect
        np.testing.assert_allclose(app(vm.xform_matrix(3 | 2 << 3 | 3 << 5, T)), v, atol=1e-6)  # local -> local

    def test_idivmi_is_division_by_a_constant(self):
        magic = struct.unpack("<f", struct.pack("<I", 0x51EB851F))[0]
        sc = script([I(0x69, [s(0, "int")], [0]), I(0x76, [s(1, "int"), s(0, "int"), c(0, "int"), c(1, "int"),
                                                           c(2, "int")]), I(0x6A, [s(1, "int")], [1])],
                    [const(100, kind="i"), const(magic), const(5, kind="i")],
                    [("a", "int", 0, 2), ("q", "int", 0, 2)])
        env = vm.Env(sc, 4)
        env.streams["a"] = np.array([[0], [99], [250], [12345]], np.int32)
        vm.run(sc, 4, env)
        self.assertEqual(env.streams["q"][:, 0].tolist(), [0, 0, 2, 123])
        self.assertEqual([(x * 0x51EB851F) >> 37 for x in (0, 99, 250, 12345)], [0, 0, 2, 123])

    def test_evolve_tiers(self):
        self.assertEqual(rt.pick_evolve({4: "b", 5: "n", 3: "m", 1: "f"}), "f")
        self.assertEqual(rt.pick_evolve({4: "b", 5: "n", 3: "m"}), "m")
        self.assertEqual(rt.pick_evolve({4: "b", 5: "n"}, "base"), "b")
        self.assertEqual(rt.pick_evolve({5: "n"}), "n")

    def test_light_hooks(self):
        C, out = self.call("_pksi_vvKillLightImmediate_SI_int2_pCtxS", 2, np.zeros((2, 2), np.int32))
        self.assertEqual(out.tolist(), [[0], [0]])      # a query: the game has not dropped the light


class LangBehaviour(unittest.TestCase):
    def test_roundtrip_behaves_the_same(self):
        try:
            from hdkit.fx import vmcheck
            from hdkit.fx import lang  # noqa: F401
        except ImportError:
            self.skipTest("fx/lang not present")
        for sc in (*emitter_scripts(), *spark_scripts(1)):
            b = vmcheck.roundtrip(sc)
            if isinstance(b, str):
                continue
            self.assertEqual(vmcheck.script_behaviour(sc, b), [])


@unittest.skipUnless(MEPHISTO.exists(), "extracted D2R data not present")
class Mephisto(unittest.TestCase):
    def test_all_scripts_run_without_stubs(self):
        data = MEPHISTO.read_bytes()
        ss = cb.scripts(cb.hp.ParticleFile(data))
        self.assertEqual(len(ss), 39)
        st = vm.new_stats()
        for sc in ss:
            vm.run(sc, 4, vm.Env(sc, 4, stats=st))
        self.assertFalse(st["stub_calls"])
        self.assertEqual(len(st["calls"]), 11)   # event stream generate/duration, payload init/kick, effect age/
        # isRunning/isRenderingEnabled, self.kill, xform l2w, rand, trigger

    def test_effect_runs(self):
        sim = S.Sim(MEPHISTO)
        sim.step(2.0)
        n = {k: b.count for k, b in sim.state().layers.items()}
        self.assertEqual(n["CylinderInf"], 1)
        self.assertEqual(n["CylinderSkullsInf"], 1)
        self.assertEqual(n["GroundDiscInf"], 1)
        self.assertEqual(n["Root"], 0)
        self.assertFalse(sim.stats["stub_calls"])
        self.assertFalse(sim.stats["errors"])
        f = sim.state().layers["CylinderInf"].fields
        np.testing.assert_allclose(f["0"][0], [1.0, 1.25, 1.0])     # stream default from the layer's constant pool


class D2RKernels(unittest.TestCase):
    """Semantics read from D2R.exe's kernels (docs/hdfx-vm-re.md): one test per confirmed behaviour."""

    class Env:
        def __init__(self, n=1):
            self.n, self.emissions, self.handles = n, [], []
            self.transform = np.eye(4, dtype=np.float32)
            self.prev_transform, self.velocity = None, np.zeros(3, np.float32)
            self.dt, self.ground_y = 1 / 30, 0.0
            self.rng = np.random.default_rng(1)

        def per_particle(self, v):
            return v

    def call(self, name, n, *args, env=None, dst="float"):
        C = vm.CallCtx.__new__(vm.CallCtx)
        C.env, C.n, C.name, C.dst_type, C.ins = env or self.Env(n), n, name, T[dst], None
        return vm.ENGINE[name](C, *args)

    def out(self, code, consts, exts=OUT):
        return fold(code, consts, exts).streams["out"]

    def test_sincos_is_sin_then_cos(self):          # 0x16e3210 cases 7 and 0x26
        m = cb.MATH.index
        for fn in ("sincos", "fast_sincos"):
            code = [I(0x77, [s(0, "float2"), c(0)], [m(fn)]), I(0x6A, [s(0, "float2")], [0])]
            np.testing.assert_allclose(self.out(code, [const(np.pi / 2)], [("out", "float2", 0, 2)])[0], [1, 0],
                                       atol=1e-6)

    def test_mfn3_within_lerp_clamp(self):           # 0x17073b0: names lerp, clamp, within (0x2a05ad8)
        self.assertEqual(cb.MFN3, ["lerp", "clamp", "within"])
        k = [const(1.0), const(3.0), const(2.0), const(0.0), const(0.25)]
        w = lambda a, lo, hi: float(self.out([I(0x79, [s(0), c(a), c(lo), c(hi)], [2]), I(0x6A, [s(0)], [0])], k)[0, 0])  # noqa: E731
        self.assertEqual(w(2, 0, 1), 1.0)            # 1 <= 2 <= 3
        self.assertEqual(w(0, 0, 1), 1.0)            # inclusive low end
        self.assertEqual(w(1, 0, 1), 1.0)            # inclusive high end
        self.assertEqual(w(3, 0, 1), 0.0)            # 0 outside
        clamp = self.out([I(0x79, [s(0), c(2), c(1), c(0)], [1]), I(0x6A, [s(0)], [0])], k)[0, 0]
        self.assertEqual(clamp, 1.0)                 # min(max(2, 3), 1): lo > hi gives hi
        lerp = self.out([I(0x79, [s(0), c(0), c(1), c(4)], [0]), I(0x6A, [s(0)], [0])], k)[0, 0]
        self.assertEqual(lerp, 1.5)                  # (3 - 1) * 0.25 + 1
        within_i = vm.MATH3["within"](np.int32([[2], [5]]), np.int32([[1]]), np.int32([[3]]))
        self.assertEqual(within_i.dtype, np.int32)
        self.assertEqual(within_i[:, 0].tolist(), [1, 0])

    def test_select_is_m_then_b(self):                # 0x1709ad0: (~m & a) | (m & b)
        k = [const(1.0, 2.0), const(5.0, 6.0)]
        code = [I(0x70, [s(0, "bool2"), c(0, "float2"), c(0, "float2")], [16]),     # all true
                I(0x7A, [s(1, "float2"), c(0, "float2"), c(1, "float2"), s(0, "bool2")]), I(0x6A, [s(1, "float2")], [0])]
        np.testing.assert_array_equal(self.out(code, k, [("out", "float2", 0, 2)])[0], [5, 6])

    def test_vct_copies_raw_lanes(self):              # 0x16d5530: 32-bit lane copy, no conversion
        p = vm.vct_part(np.float32([[1.0]]), "i")
        self.assertEqual(int(p[0, 0]), 0x3F800000)
        np.testing.assert_array_equal(vm.vct_part(np.int32([[3, 4]]), "i"), [[3, 4]])

    def test_vrand_radius(self):                      # 0xbf03c0
        u = np.float32([[0.0], [0.125], [1.0]])
        np.testing.assert_allclose(vm.vrand_radius(np.float32(0), np.float32(2), u)[:, 0], [0, 1, 2], atol=1e-6)
        np.testing.assert_allclose(vm.vrand_radius(np.float32(2), np.float32(0), u)[:, 0], [0, 1, 2], atol=1e-6)
        np.testing.assert_allclose(vm.vrand_radius(np.float32(-1), np.float32(0), u)[:, 0], [0, 0, 0])
        r = vm.vrand_radius(np.float32(1), np.float32(2), np.float32([[0.5]]))[0, 0]
        self.assertAlmostEqual(float(r), 1 + 0.5 ** 0.5, places=5)               # exponent (1 + 1/2) / 3
        v = self.call("vrand_float_float_RandCtx", 500, np.float32([[3.0]]), np.float32([[1.0]]), dst="float3")
        ln = np.linalg.norm(v, axis=1)
        self.assertTrue(ln.min() >= 1 - 1e-5 and ln.max() <= 3 + 1e-5)

    def test_orientation_axes(self):                  # frame 0: side +x, up +y, forward +z (0xbecf80..)
        q = np.float32([[0, 0, 0, 1]])
        np.testing.assert_allclose(self.call("orientation_axisForward_orientation", 1, q, dst="float3"), [[0, 0, 1]])
        np.testing.assert_allclose(self.call("orientation_axisUp_orientation", 1, q, dst="float3"), [[0, 1, 0]])

    def test_scene_orientation_u_f(self):             # 0xbf3330: u from +y, f from EAxis Forward = -z
        d = np.float32([[1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 0, 0]])
        q = self.call("scene.orientation_f_norm_float3", 4, d, dst="orientation")
        fwd = vm.qrot(q, np.broadcast_to(np.float32([[0, 0, -1]]), (4, 3)))
        np.testing.assert_allclose(fwd[:3], [[1, 0, 0], [0, 0, 1], [0, 0, -1]], atol=1e-6)
        np.testing.assert_allclose(q[3], [0, 0, 0, 1], atol=1e-7)                 # zero direction: identity
        np.testing.assert_allclose(q[1], [0, -1, 0, 0], atol=1e-6)                # opposite: 180 degrees about Down
        q = self.call("scene.orientation_u_norm_float3", 2, np.float32([[0, 0, 1], [0, -1, 0]]), dst="orientation")
        np.testing.assert_allclose(vm.qrot(q[:1], np.float32([[0, 1, 0]])), [[0, 0, 1]], atol=1e-6)
        np.testing.assert_allclose(q[1], [1, 0, 0, 0], atol=1e-6)                 # opposite: about Right

    def test_scene_orientation_fu(self):              # 0xbf3c90: rows S, U, F
        q = self.call("scene.orientation_fu_norm_float3_float3", 1, np.float32([[2, 0, 0]]), np.float32([[0, 1, 1]]),
                      dst="orientation")
        np.testing.assert_allclose(vm.qrot(q, np.float32([[0, 0, 1]])), [[1, 0, 0]], atol=1e-6)
        np.testing.assert_allclose(vm.qrot(q, np.float32([[0, 1, 0]])), [[0, 2 ** -0.5, 2 ** -0.5]], atol=1e-6)

    def test_effect_axis_forward_is_minus_z(self):    # 0xbf53f0: EAxis Forward, sign -1
        env = self.Env()
        np.testing.assert_allclose(self.call("effect.axisForward_pCtxI", 1, env=env, dst="float3"), [[0, 0, -1]])
        np.testing.assert_allclose(self.call("effect.axisUp_pCtxI", 1, env=env, dst="float3"), [[0, 1, 0]])

    def test_dxformdt_translation_is_the_velocity(self):   # 0xc02200: translation row = instance velocity
        env = self.Env()
        env.prev_transform = np.eye(4, dtype=np.float32)
        env.transform = np.eye(4, dtype=np.float32)
        env.transform[:3, 3] = [5, 0, 0]                                         # a teleport, not the velocity
        env.velocity = np.float32([0, 0, 2])
        out = self.call("dxformdt_l2w_f_masked_float3_int_pCtxS", 1, np.float32([[1, 0, 0]]), np.int32([[19]]),
                        env=env, dst="float3")
        np.testing.assert_allclose(out, [[0, 0, 2]], atol=1e-6)

    def test_intersect_returns_normal_and_distance(self):  # 0xbf18d0 -> 0x7e80c0 (lane r3-intersect)
        env = self.Env(3)
        out = self.call("scene.intersect_float3_float3_float_bool_int_SceneCtx", 3,
                        np.float32([[0, 0.5, 0], [0, 0.5, 0], [0, 2, 0]]),
                        np.float32([[0, -1, 0], [0, 1, 0], [0, -1, 0]]), np.float32([[5]]), env=env, dst="float4")
        np.testing.assert_allclose(out[0], [0, 1, 0, 0])                          # hit: normal, distance 0
        np.testing.assert_array_equal(out[1], [1, 0, 0, np.inf])                 # no hit
        np.testing.assert_array_equal(out[2], [1, 0, 0, np.inf])                 # the game's ray is origin -> origin + d

    def test_payload_hermite_and_arguments(self):     # 0xc58ca0: (v0, v1, t0, t1), mode = eid & 0xff
        v0, v1 = np.float32([[0, 0, 0]]), np.float32([[1, 0, 0]])
        t0, t1 = np.float32([[0, 1, 0]]), np.float32([[0, -1, 0]])
        f = np.float32([[0.0], [0.5], [1.0]])
        h = S.hermite(np.repeat(v0, 3, 0), np.repeat(v1, 3, 0), np.repeat(t0, 3, 0), np.repeat(t1, 3, 0), f)
        np.testing.assert_allclose(h[[0, 2]], [[0, 0, 0], [1, 0, 0]], atol=1e-6)
        np.testing.assert_allclose(h[1], [0.5, 0.25, 0], atol=1e-6)              # tangents +y then -y bulge the curve
        env = self.Env()
        hd = self.call("buildPayloadElementD_int3_float3_float3_float3_float3_int_pCtxS", 1, np.int32([[0, 0, 1]]),
                       v0, v1, t0, t1, np.int32([[0x103]]), vm.Ctx("c"), env=env, dst="int")
        e = env.handles[int(hd[0, 0])]
        self.assertEqual(e[1], 0x103)
        np.testing.assert_array_equal(e[2], v0)
        np.testing.assert_array_equal(e[3], v1)
        np.testing.assert_array_equal(e[4], t0)


F32 = np.float32


class NoiseKernels(unittest.TestCase):
    """noise_float3 (0xc623e0), noise_float (0xc616a0), fast.noise_float (0xb3d5b0): the engine's own noise, bit-exact.
    Pinned values come from running the kernels' machine code in an x86 emulator; the hand traces below follow the
    disassembly (docs/hdfx-vm-re.md)."""
    from hdkit.fx import noise as N

    P3 = np.float32([[0.3, 0.7, 0.1], [12.34, -5.6, 7.89], [100.5, 0.25, -33.3], [-1.5, 2.25, 0.5], [0.5, 0.5, 0.5],
                     [-0.01, 0.02, 0.03]])
    V3 = [0x3E233211, 0x3E1168BC, 0xBEACD2FD, 0xBF20409C, 0x0, 0x3DA84A18]
    X1 = np.float32([0.0, 0.25, 0.5, 0.75, 1.5, -0.3, 7.7, -12.9])
    V1_WIDE = [0x0, 0x3F197885, 0x3EDFFFE4, 0x3C2CD082, 0x3E3FFFE8, 0xBF0E8076, 0x3D4E02C4, 0xBE68AFFE]
    V1_SCALAR = [0x0, 0x3F197274, 0x3EDFF70B, 0x3C2CC9AE, 0x3E3FF852, 0xBF0E7AD5, 0x3D4DFAA3, 0xBE68A6CD]
    VFAST = [0xBF4AEFE8, 0xBF24F6B8, 0xBD9C25F0, 0x3EFBDA78, 0x3DAFF300, 0xBF3EC810, 0x3F4F014F, 0xBF3951BF]

    @staticmethod
    def bits(a):
        return [int(v) for v in np.asarray(a, np.float32).ravel().view(np.uint32)]

    def test_pinned_values(self):
        N = self.N
        self.assertEqual(self.bits(N.noise_float3(self.P3)), self.V3)
        self.assertEqual(self.bits(N.noise_float(self.X1, scalar=False)), self.V1_WIDE)
        self.assertEqual(self.bits(N.noise_float(self.X1, scalar=True)), self.V1_SCALAR)
        self.assertEqual(self.bits(N.fast_noise_float(self.X1)), self.VFAST)

    def test_hand_trace_3d(self):
        # the origin: corner 0 has d = -0.6 but a zero offset (zero dot product); the other corners lie beyond sqrt(0.6)
        # (corner 1 of sort order 0 at (-5/6, 1/6, 1/6): d = 0.1495 > 0 -> kernel 0), so the sum is exactly 0.
        # (0.5, 0.5, 0.5): the same, from the other side of the cell.
        np.testing.assert_array_equal(self.N.noise_float3(np.float32([[0, 0, 0], [0.5, 0.5, 0.5]])), [[0], [0]])
        # one corner alone, p = (0.3, 0.7, 0.1): skew s = 1.1 -> cell (0, 1, 0), unskewed origin (0, 0.8333, 0), offsets
        # (0.3, -0.1333, 0.1), which sort y < z < x; kernel = (0.6 - 0.1181)^4; all fits the pinned value
        self.assertAlmostEqual(float(self.N.noise_float3(self.P3[:1])[0, 0]), 0.15937068, places=6)

    def test_hand_trace_1d(self):
        # x = 0.5: f = 0.5, f - 1 = -0.5, kernel (1 - 0.25)^4 = 0.31640625; perm[0] = 151 -> gradient (151 & 7) + 1 = 8
        # (bit 3 clear), perm[1] = 160 -> 1; (1 * -0.5 * k + 8 * 0.5 * k) = 1.107421875; the wide path scales by 32/81,
        # the scalar one (the tail of an array, constants) by 0.395
        N = self.N
        self.assertAlmostEqual(float(N.noise_float(F32([0.5]), scalar=True)[0, 0]), 1.107421875 * 0.395, places=6)
        self.assertAlmostEqual(float(N.noise_float(F32([0.5]), scalar=False)[0, 0]), 1.107421875 * 32 / 81, places=5)
        # a lone value is the scalar path; eight values go wide in two groups of four; 6 = 4 wide + 2 scalar
        x = np.linspace(-3, 3, 6).astype(F32)
        a = N.noise_float(x)
        np.testing.assert_array_equal(a[:4], N.noise_float(x, scalar=False)[:4])
        np.testing.assert_array_equal(a[4:], N.noise_float(x, scalar=True)[4:])
        np.testing.assert_array_equal(N.noise_float(F32([0.3])), N.noise_float(F32([0.3]), scalar=True))

    def test_hand_trace_fast(self):
        N = self.N
        T = N.fast_table()
        self.assertEqual(T.shape, (256,))
        self.assertEqual(self.bits(T[:4]), [0x3F5420D0, 0x3F1E9B8C, 0x3E1B5AD0, 0x3E8B6B58])   # SFMT 0xcafe4269, (-1, 1)
        perm = N._FPERM
        # on a lattice point the result is the table entry of its permutation value; half way (quintic 0.5) the mean
        self.assertEqual(float(N.fast_noise_float(F32([0.0]))[0, 0]), float(T[perm[0]]))
        self.assertEqual(float(N.fast_noise_float(F32([256.0]))[0, 0]), float(T[perm[0]]))     # period 256
        self.assertAlmostEqual(float(N.fast_noise_float(F32([0.5]))[0, 0]), (float(T[perm[0]]) + float(T[perm[1]])) / 2, places=6)
        self.assertEqual(float(N.fast_noise_float(F32([-0.5]))[0, 0]), float(N.fast_noise_float(F32([255.5]))[0, 0]))

    def test_matches_a_scalar_rewrite_of_the_disassembly(self):
        """3D simplex noise rewritten corner by corner, in scalar fp32, as the SSE code does it."""
        N = self.N
        c23, c24, m1 = F32(8388608), F32(16777216), F32(-1)
        P = [int(v) for v in N._PERM]

        def floor(v):
            r = ((v + c23) - c24) + c23
            return r + (m1 if v < r else F32(0))

        def one(px, py, pz):
            sk = ((py + px) + pz) * N._THIRD
            i = [floor(sk + c) for c in (px, py, pz)]
            t = [c - ic for c, ic in zip((px, py, pz), i)]
            g = ((i[1] + i[0]) + i[2]) * N._SIXTH
            o = [g + tc for tc in t]
            m = int(o[0] < o[1]) | int(o[1] < o[2]) << 1 | int(o[2] < o[0]) << 2
            a, b, c = (int(v) & 255 for v in i)
            cs = []
            for k in range(4):
                if k == 0:
                    ia, ja, ka = a, b, c
                elif k == 3:
                    ia, ja, ka = a + 1, b + 1, c + 1
                else:
                    off = [int(v) for v in N._OFFS[m][k - 1]]
                    ia, ja, ka = a + off[0], b + off[1], c + off[2]
                h = P[P[P[ka] + ja] + ia] & 15 if k != 3 else P[P[P[c + 1] + 1 + b] + 1 + a] & 15
                X, Y, Z = (o[ax] + N._GRAD[m][ax][k] for ax in range(3))
                d = ((X * X + N._NEG06) + Y * Y) + Z * Z
                tt = F32(0) if F32(0) < d else d
                t4 = (tt * tt) * (tt * tt)
                u = X if h < 8 else Y
                v = Y if h < 4 else (X if h in (12, 14) else Z)
                u = -u if h & 1 else u
                v = -v if h & 2 else v
                cs.append((v + u) * t4)
            return ((cs[2] + cs[0]) + (cs[3] + cs[1])) * F32(32)

        rng = np.random.default_rng(3)
        pts = np.concatenate([rng.uniform(-8, 8, (300, 3)), rng.integers(-3, 4, (30, 3))]).astype(F32)
        ref = np.array([one(*p) for p in pts], dtype=F32)
        np.testing.assert_array_equal(N.noise_float3(pts)[:, 0].view(np.uint32), ref.view(np.uint32))

    def test_range_and_continuity(self):
        N = self.N
        rng = np.random.default_rng(5)
        p = rng.uniform(-50, 50, (20000, 3)).astype(F32)
        v3 = N.noise_float3(p)
        self.assertEqual(v3.shape, (20000, 1))
        self.assertTrue(np.abs(v3).max() <= 1.0 and np.abs(v3).max() > 0.85 and 0.2 < v3.std() < 0.5)
        x = rng.uniform(-50, 50, 20000).astype(F32)
        for fn in (N.noise_float, N.fast_noise_float):
            v = fn(x)
            self.assertTrue(np.abs(v).max() <= 1.0 and v.std() > 0.2, fn.__name__)
        # continuity: tiny steps move the value by at most (Lipschitz bound) * step
        e = F32(1e-4)
        for k in range(3):
            q = p.copy()
            q[:, k] += e
            self.assertLess(np.abs(N.noise_float3(q) - v3).max(), 5e-3)               # |gradient| <= ~40
        for fn in (N.noise_float, N.fast_noise_float):
            self.assertLess(np.abs(fn(x + e) - fn(x)).max(), 5e-3, fn.__name__)
        # the 3D noise is periodic in the permutation: shifting by 256 cells changes nothing
        sh = N.noise_float3(p[:200] + F32(256))
        np.testing.assert_allclose(sh, v3[:200], atol=2e-3)                        # (fp32 rounding at the larger magnitude)

    def test_engine_calls_are_the_kernels(self):
        for name in ("noise_float", "fast.noise_float", "noise_float3"):
            self.assertIn("confirmed", vm.NOTES[name])
            self.assertFalse(vm.NOTES[name].startswith("doubtful"))
        env = D2RKernels.Env(6)
        C = vm.CallCtx.__new__(vm.CallCtx)
        C.env, C.n, C.name, C.dst_type, C.ins = env, 6, "noise_float3", T["float"], None
        np.testing.assert_array_equal(vm.ENGINE["noise_float3"](C, self.P3), self.N.noise_float3(self.P3))
        C.name = "fast.noise_float"
        np.testing.assert_array_equal(vm.ENGINE["fast.noise_float"](C, self.X1[:, None]), self.N.fast_noise_float(self.X1))
        C.name = "noise_float"
        np.testing.assert_array_equal(vm.ENGINE["noise_float"](C, self.X1[:, None]), self.N.noise_float(self.X1))

    def test_tables_match_the_game_image(self):
        img = Path((os.environ.get("HDFX_D2R_IMAGE") or "/nonexistent"))
        if not img.exists():
            self.skipTest("decrypted D2R image not present")
        from hdkit.fx import noise_dump as dump
        from hdkit.fx import noise_data as ND
        data = img.read_bytes()
        for name, (rva, n, _note) in dump.TABLES.items():
            self.assertEqual(getattr(ND, name), data[rva:rva + n], name)
        perm = np.frombuffer(ND.PERM, np.uint8)
        np.testing.assert_array_equal(perm[:256], perm[256:512])
        np.testing.assert_array_equal(np.frombuffer(ND.FPERM, "<u4"), perm[:257])


if __name__ == "__main__":
    unittest.main()
