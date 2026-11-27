"""hdfx-lang (hdkit/fx/lang): lexer/parser, compiler, decompiler, equivalence, CLI. No game files needed; the
Mephisto and corpus checks run when the extracted D2R `hd/vfx` folder is present (HDFX_D2R_ROOT or the default)."""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.fx import cbvm as cb  # noqa: E402
from hdkit.fx import lang  # noqa: E402
from hdkit.fx.lang import LangError  # noqa: E402
from hdkit.fx.lang.equiv import diff  # noqa: E402
from hdkit.fx.lang.isa import swz_decode, swz_encode, float_text, f32  # noqa: E402
from hdkit.fx.lang.syntax import parse  # noqa: E402

ROOT = Path((os.environ.get("HDFX_D2R_ROOT") or "/nonexistent"))
MEPH = ROOT / "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles"

SPIRAL = """
extern float3 self.Position = "0";
extern float3 self.Velocity = "1";
extern float  self.Life = "2";
float t = rand(0, 1) * 6.2831853 * 4;
float2 sc = sincos(t);
float r = 0.2 + t * 0.05;
self.Position = effect.position() + float3(sc.y * r, t * 0.1, sc.x * r);
self.Velocity = normalize(float3(sc.y, 0.2, sc.x)) * 1.5;
self.Life = rand(0.8, 1.6);
"""
COLOUR = """
#kind 4
extern curve4 ColorCurve = "__sampler_0";
extern float4 self.Color = "5";
float4 c = ColorCurve.sample(self.lifeRatio);
self.Color = float4(c.xyz, c.w * saturate(2 - 2 * self.lifeRatio));
"""
BOUNCE = """
#kind 4
extern float3 self.Position = "16";
extern float3 self.Velocity = "17";
extern float4 self.Color = "1A";
float3 v = self.Velocity + float3(0, -9.8, 0) * effect.dt;
float3 p = self.Position + v * effect.dt;
bool hit = p.y < 0;
self.Velocity = hit ? v * float3(0.7, -0.5, 0.7) : v;
self.Position = select(p, p * float3(1, 0, 1), hit);
self.Color = self.Color.xyz1 * float4(1, 1, 1, 1 - self.lifeRatio);
"""


def ops(s: cb.Script) -> list[str]:
    return [i.name for i in s.code]


def roundtrip_bytes(s: cb.Script) -> bool:
    code = cb.encode(s.code)
    return cb.encode(cb.decode(code)) == code


class TestIsa(unittest.TestCase):
    def test_swizzle_encoding_matches_game(self):
        # (selector, bytes) pairs found in D2R's scripts
        for sel, imm in [("x", [0, 0, 0]), ("y", [1, 1, 0]), ("w", [3, 3, 0]), ("xy", [12, 8, 0]),
                         ("xyz", [120, 136, 0]), ("www", [171, 219, 0]), ("0x0", [190, 4, 16]),
                         ("111x", [217, 109, 17]), ("xyz1", [136, 136, 165]), ("x00y", [130, 32, 50]),
                         ("0x", [10, 4, 0])]:
            self.assertEqual(swz_encode(sel), imm, sel)
            self.assertEqual(swz_decode(imm, len(sel)), sel)

    def test_float_text_round_trips(self):
        for x in (0.1, 6.2831853, -90.0, 1e-7, 0.017453294, 3.4e38):
            v = f32(x)
            self.assertEqual(f32(float(float_text(v))), v)
        self.assertEqual(float_text(f32(90.0)), "90.0")


class TestParse(unittest.TestCase):
    def test_parse_examples(self):
        for src in (SPIRAL, COLOUR, BOUNCE):
            self.assertTrue(parse(src))

    def test_digit_swizzle_lexes(self):
        s = lang.compile('extern float self.a = "0"; extern float4 self.b = "1"; self.b = self.a.111x;')
        self.assertIn("swz", ops(s))

    def test_errors_have_lines(self):
        with self.assertRaises(LangError) as cm:
            lang.compile('extern float self.a = "0";\nself.a = 1 +;\n')
        self.assertIn("line 2", str(cm.exception))


class TestCompile(unittest.TestCase):
    def test_examples_compile_and_assemble(self):
        for src in (SPIRAL, COLOUR, BOUNCE):
            s = lang.compile(src)
            self.assertTrue(roundtrip_bytes(s))
            self.assertEqual(s.code[0].name, "prlg")
            self.assertEqual(s.code[-1].name, "eplg")
            self.assertEqual(s.head[0], len(s.code))
            sreg = max([(r & 0x1FFFFF) + 1 for i in s.code for r in i.regs + [r for _k, r in i.args]
                        if r != cb.NONE and (r >> 21) & 3 == 3] + [0])
            self.assertEqual(s.head[8], sreg)
            self.assertEqual(s.ext_names[-2:], [None, "EffectID"])
            # the listing text round trips through hd_cbvm too
            self.assertEqual(cb.script_text(cb.script_parse(cb.script_text(s), 0)), cb.script_text(s))

    def test_spiral_uses_engine_calls(self):
        s = lang.compile(SPIRAL)
        self.assertEqual(s.fn_names, ["rand_float_float_RandCtx", "effect.position_pCtxI"])
        self.assertIn("RandContext", s.ext_names)
        self.assertIn("ParticleContextI", s.ext_names)
        self.assertIn("madd", ops(s))            # 0.2 + t * 0.05
        self.assertIn("mfn1", ops(s))            # sincos, normalize
        self.assertIsNone(s.kind)

    def test_sampler_method(self):
        s = lang.compile(COLOUR)
        call = next(i for i in s.code if i.name == "call")
        self.assertEqual(s.fn_names[call.imm[2]], "samplerCurve4:sample_float")
        self.assertEqual(s.ext_names[call.imm[1]], "__sampler_0")
        self.assertEqual(s.ext_desc[call.imm[1]][1:], [0xFFFFFFFF, 1, 2, 1])
        self.assertEqual(s.kind, 4)

    def test_if_becomes_select(self):
        s = lang.compile('#kind 4\nextern float self.a = "0";\n'
                         'if (self.a > 1) { self.a = 0.5; } else { self.a = self.a * 2; }\n')
        self.assertIn("sel", ops(s))
        self.assertEqual(ops(s).count("st"), 1)

    def test_side_effects_refused_in_if(self):
        with self.assertRaises(LangError):
            lang.compile('extern float self.a = "0";\nif (self.a > 1) { self.kill(true); }\n')

    def test_constants_fold_and_dedupe(self):
        s = lang.compile('extern float self.a = "0"; self.a = self.a * (2 * 3) + 6;')
        self.assertEqual(ops(s), ["prlg", "ld", "madd", "st", "eplg"])
        self.assertEqual(len(s.consts), 1)
        self.assertEqual(struct.unpack("<f", s.consts[0][:4])[0], 6.0)

    def test_within_compiles_to_mfn3_2(self):
        s = lang.compile('extern float self.a = "0"; self.a = within(self.a, 0, 1);')
        self.assertIn("mfn3", ops(s))
        self.assertEqual(next(i.imm[0] for i in s.code if i.name == "mfn3"), 2)
        self.assertIn("within(", lang.decompile(s))

    def test_parenthesised_product_is_not_fused(self):
        s = lang.compile('extern float self.a = "0"; self.a = (self.a * 3) + 1;')
        self.assertEqual(ops(s), ["prlg", "ld", "mul", "add", "st", "eplg"])

    def test_zero_store_is_stz(self):
        s = lang.compile('extern float4 self.c = "0"; self.c = float4(0);')
        self.assertEqual(ops(s), ["prlg", "stz", "eplg"])

    def test_types_checked(self):
        for bad in ('extern float self.a = "0"; self.a = float3(1, 2, 3);',
                    'extern float3 self.a = "0"; self.a = self.a.xy;',
                    'extern float self.a = "0"; self.a = nosuch(1);',
                    'extern float self.a = "0"; self.a = rand(float3(0), 1, 2);'):
            with self.assertRaises(LangError, msg=bad):
                lang.compile(bad)

    def test_overloads_pick_exact_first(self):
        s = lang.compile('extern int self.i = "0"; extern int3 self.t = "1";'
                         'self.i = buildPayloadElement(self.t, 0, 0, 768);')
        self.assertIn("buildPayloadElement_int3_int_int_int_pCtxS", s.fn_names)
        s = lang.compile('extern float4 self.c = "0"; self.c = rand(float4(-1), float4(1));')
        self.assertIn("rand_float4_float4_RandCtx", s.fn_names)

    def test_immediate_args_need_constants(self):
        with self.assertRaises(LangError):
            lang.compile('extern float3 self.p = "0"; extern int self.m = "1";'
                         'self.p = xform_l2w_f_masked(self.p, self.m);')
        s = lang.compile('extern float3 self.p = "0"; self.p = xform_l2w_f_masked(self.p, 17);')
        call = next(i for i in s.code if i.name == "call")
        self.assertEqual([k for k, _r in call.args], [0x18, 0, 8])

    def test_base_keeps_table(self):
        base = lang.compile(BOUNCE)
        s = lang.compile('extern float3 self.Position = "16";\nself.Position = self.Position * 2;', base=base)
        self.assertEqual(s.ext_names, base.ext_names)
        self.assertEqual(s.kind, 4)

    def test_bindings(self):
        s = lang.compile("self.Size = self.Size * 2.0;", bindings={"self.Size": ("9", "float")})
        self.assertIn("9", s.ext_names)


class TestRoundTrip(unittest.TestCase):
    def test_examples_round_trip(self):
        for src in (SPIRAL, COLOUR, BOUNCE):
            s = lang.compile(src)
            back = lang.compile(lang.decompile(s))
            self.assertEqual(diff(s, back), [], lang.decompile(s))

    def test_diff_sees_a_change(self):
        a = lang.compile(BOUNCE)
        b = lang.compile(BOUNCE.replace("-9.8", "-9.7"))
        self.assertTrue(diff(a, b))

    def test_api_seam(self):
        # docs/hdfx-mcp.md "Seams": compile(src, effect=None) -> Script; decompile(Script) -> str
        s = lang.compile(COLOUR, effect=None)
        self.assertIsInstance(s, cb.Script)
        self.assertIsInstance(lang.decompile(s), str)


@unittest.skipUnless(MEPH.is_file(), "D2R hd/vfx extract not present")
class TestGameScripts(unittest.TestCase):
    def test_mephisto_all_scripts(self):
        from hdkit.formats import hd_particles as hp
        ss = cb.scripts(hp.ParticleFile(MEPH.read_bytes()))
        self.assertEqual(len(ss), 39)
        for s in ss:
            self.assertEqual(lang.check(s), [], f"object {s.obj + 1}")

    def test_cli_compile_into(self):
        from hdkit.formats import hd_particles as hp
        from hdkit.fx.lang.__main__ import main
        with tempfile.TemporaryDirectory() as d:
            src = Path(d, "s.hfx")
            ss = {s.obj + 1: s for s in cb.scripts(hp.ParticleFile(MEPH.read_bytes()))}
            src.write_text(lang.decompile(ss[148]).replace("* 0.5 +", "* 0.25 +"), encoding="utf-8")
            out = Path(d, "o.particles")
            self.assertEqual(main(["compile", str(src), "--into", str(MEPH), "--obj", "148", "--obj", "149",
                                   "-o", str(out)]), 0)
            new = {s.obj + 1: s for s in cb.scripts(hp.ParticleFile(out.read_bytes()))}
            self.assertTrue(diff(ss[148], new[148]))          # the edit is there
            self.assertEqual(diff(new[148], new[149]), [])     # both evolve copies
            self.assertEqual(diff(ss[147], new[147]), [])      # the others untouched

    def test_corpus_sample(self):
        from hdkit.formats import hd_particles as hp
        files = sorted(ROOT.rglob("*.particles"))[::97]
        bad = []
        for f in files:
            if f.stat().st_size:
                for s in cb.scripts(hp.ParticleFile(f.read_bytes())):
                    r = lang.check(s)
                    if r and "idivmi" not in r[0]:
                        bad.append((f.name, s.obj + 1, r[0]))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
