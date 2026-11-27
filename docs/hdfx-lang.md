# hdfx-lang: a readable script language for D2R's particle scripts (`.hfx` → CBVM)

Lane lang, 2026-11-24. Contract in `docs/hdfx.md` section 4. Code: `hdkit/fx/lang/`. CLI:
`python -m hdkit.fx.lang compile|decompile|check ...`.

D2R's `.particles` files carry every particle script (spawner, particle spawn, particle evolve) compiled for PopcornFX's
CPU virtual machine (CBVM; `hdkit.fx.cbvm` disassembles and reassembles it, knowledge/hd-effects.md).
hdfx-lang is a small HLSL-like language in the style of PopcornFX's own script language (same names: `self.*`,
`effect.*`, `scene.*`, `rand()`, `select()`, `lerp()`, sampler methods, `.xyz1` swizzles), compiled straight to CBVM
with the existing assembler, and decompiled from CBVM for reading. Agents write new behaviour with it; the decompiler
turns any of the corpus's scripts into source that compiles back to a script with the same behaviour.

```
compile(src, effect=None, base=None, bindings=None) -> hd_cbvm.Script     # hdkit.fx.lang (api: compile(src, effect=))
decompile(script, bindings=None) -> str
check(script) -> list[str]           # decompile -> compile -> same behaviour (empty = ok)
```

## 1. What the VM is (the parts the language maps to)

Found in D2R.exe 93847 (`d2r93847_dump.exe`, RVA = file offset) and checked over all 136,951 scripts of the 2,206
extracted `.particles`:

- **Straight-line code.** No jumps: a script is `prlg`, instructions, `eplg`. Every instruction runs for every particle
  of the batch (SIMD over 8 lanes). Conditions are data: `sel` (select) and masks.
- **Registers** have a class: `c` constant (the constant pool, 32 bytes each: the value repeated over 8 lanes, a float3
  packed `xyzxyzxy`), `q` (written `?` by `hd_cbvm.py`; loaded from scope-2 externals: spawner/scene scalars), `u`
  uniform (scope 3: attributes, effect state; one value per batch), `s` per particle (scope 4). A value's class is the
  highest of its inputs' (`c < q < u < s`). Types: the VM table (`float`..`float4`, `int`..`int4`, `bool`..`bool4`,
  `class` = a context pointer, type 37 = `orientation`, a quaternion). A register read under another type is a bit cast.
- **Header** (9 words, all derived on writing): h0 instruction count, h1 code bytes, h3/h5 constant bytes/count, h6/h7/h8
  the number of `q`/`u`/`s` registers; h2 and h4 are 0.
- **`mop` sub-ops** (the name table next to the opcode names in D2R.exe): 0 add, 1 sub, 2 mul, 3 div, 4 mod, 5 neg,
  6 sll, 7 sar, 8 and, 9 or, 10 xor, 11 not, 12 lt, 13 le, 14 gt, 15 ge, 16 eq, 17 ne. Compares give a mask
  (`bool` = all bits set); `and`/`or`/`xor`/`not` are bitwise, on bools or ints.
- **`mfn3`**: 0 `lerp(a, b, t)` = `(b - a) * t + a`, 1 `clamp(x, lo, hi)` = `min(max(x, lo), hi)`, 2 `within(x, lo,
  hi)` = 1 when `lo <= x <= hi` else 0 (1.0/0.0 for floats). Names and kernels confirmed in D2R.exe
  (docs/hdfx-vm-re.md); `within` is not used by any D2R script.
- **`swz`**: three bytes. Bits 0-11 of `imm1 | (imm2 >> 4) << 8` are three bits per destination component, a selector
  `x y z w 0 1` (0-5); `imm0 | (imm2 & 15) << 8` is the same selectors as a base-6 number plus 0, 6, 42, 258 for 1-4
  components (the VM's fast path). Both are computed from the swizzle text; all 95 forms in the corpus check.
- **`sel d, a, b, m`** = `m ? b : a` (PopcornFX's `select(a, b, m)`), per component when `m` is a `boolN`.
- **`madd d, a, b, c`** = `a * b + c`. **`dup`** broadcasts a scalar. **`vct`** builds a vector from parts
  (`float+float(+float(+float))`, `float3+float`). **`cvt`** converts (`int↔float`, `bool→int/float`).
- **`!` flag** (bit 23) appears only on constants whose value is zero (an optimiser hint); the compiler never sets it.
- **Externals** (fields 4/5): one entry per name the script reads or writes, with a descriptor
  `[0, type-1 (or -1 for a context/sampler), scope, kind, access]`: scope 4 per particle, 3 uniform, 2 spawner/scene,
  1 sampler; kind 0 a stream, 2 an input; access bits 1 read, 2 write (also seen: 6, 7, 13 on writes and events). The
  table ends with an empty entry and `EffectID`. `ld`/`st`/`stz` address it by index.
- **Engine calls** (`call`, names in field 6): mangled `name_<param types>[_<contexts>]` (e.g.
  `rand_float_float_RandCtx`, `xform_l2w_f_masked_float3_int_pCtxS`, `samplerCurve3:sample_float`). Contexts
  (`pCtxS`, `pCtxI`, `RandCtx`, `SceneCtx`) are the externals `ParticleContextS`, `ParticleContextI`, `RandContext`,
  `SceneContext`, loaded and passed last. Methods (`samplerCurve1:`, `samplerShape:`, `samplerVectorField:`,
  `samplerEventStream:`, `particleEvent:`) carry the object's external index in the call (`call.F.<ext>`). Flags: 0 no
  result, 6 a `u`/`s` result, 4 a `q` result. Argument kind byte: 8 a vector/by-reference value, +16 if the register is
  `u`/`s`, +2 if it is a scalar float; 0 for "immediate int" parameters (masks, payload indices) and `q` contexts. The
  101 functions D2R uses, with result types, are in `fx/lang/fns.py` (generated from the corpus).
- `idivmi d, a, k, magic, shift` (4 uses in the whole corpus: integer division by the constant `k` through a magic
  multiply, `(a * magic) >> (32 + shift)`, lane vm2) decompiles to `a / k`; it compiles back to an int `div`, which
  `equiv` treats as the same.

## 2. Source text

```hfx
// Particle evolve: gravity, a ground bounce and a fade over life.
#kind 4
extern float3 self.Position = "16";      // stream "16" of the layer (or `self.Position` through bindings)
extern float3 self.Velocity = "17";
extern float4 self.Color = "1A";

float3 v = self.Velocity + float3(0, -9.8, 0) * effect.dt;
float3 p = self.Position + v * effect.dt;
bool hit = p.y < 0;
self.Velocity = hit ? v * float3(0.7, -0.5, 0.7) : v;
self.Position = select(p, p * float3(1, 0, 1), hit);
self.Color = self.Color.xyz1 * float4(1, 1, 1, 1 - self.lifeRatio);
```

Lexical: `//` and `/* */` comments; identifiers `[A-Za-z_][A-Za-z0-9_]*`, dotted names are one name when they denote an
external or a namespace call (`self.lifeRatio`, `effect.dt`, `effect.age()`); numbers `1`, `0x1F` (int), `1.0`, `.5`,
`1e-3`, `2f` (float); `true`, `false`, `pi`, `inf`, `nan`; strings `"..."` only in `extern` and `__call`.

Grammar (EBNF; `{}` repeat, `[]` optional):

```
script     = { pragma | extern | stmt } ;
pragma     = "#kind" (int | "none") | "#fns" { string } | "#order" { int } ;
extern     = "extern" ekind name [ "=" string ] [ "[" int int int "]" ] ";" ;
ekind      = vtype | "ctx" | "curve1" | "curve2" | "curve3" | "curve4" | "shape" | "vfield"
           | "eventstream" | "event" | "none" ;
vtype      = ("float"|"int"|"bool") ["2"|"3"|"4"] | "orientation" ;
stmt       = vtype ident "=" expr ";"                      (local)
           | lvalue ("="|"+="|"-="|"*="|"/=") expr ";"      (local, external, swizzle of either: v.xz = ...)
           | expr ";"                                       (a call with side effects: self.kill(c), ev.trigger(...))
           | "if" "(" expr ")" block [ "else" (block | stmt) ] ;
block      = "{" { stmt } "}" ;
expr       = ternary ;  ternary = or [ "?" expr ":" ternary ] ;
or = and {"||" and} ; and = bor {"&&" bor} ; bor = bxor {"|" bxor} ; bxor = band {"^" band} ; band = eq {"&" eq} ;
eq = rel {("=="|"!=") rel} ; rel = shift {("<"|"<="|">"|">=") shift} ; shift = add {("<<"|">>") add} ;
add = mul {("+"|"-") mul} ; mul = unary {("*"|"/"|"%") unary} ;
unary = ("-"|"!"|"~") unary | postfix ;
postfix = primary { "." swizzle | "." ident "(" args ")" } ;
primary = number | name | name "(" args ")" | vtype "(" args ")" | "__call" "<" vtype ">" "(" string {"," expr} ")"
        | "(" expr ")" ;
```

`if`/`else` has no jump to compile to: both sides are computed and every local or external assigned in them is merged
with `sel` (`x = c ? x_then : x_else`). Calls with side effects (`self.kill`, events, `_pksi_*`, `rand`) are refused
inside `if`; pass the condition instead (`self.kill(c)`). There are no loops and no user functions.

## 3. Types and conversions

`float float2 float3 float4 int int2 int3 int4 bool bool2 bool3 bool4 orientation`. Arithmetic needs equal types; a
scalar operand is broadcast to the other side's vector (`dup`), and an int literal in a float expression is a float.
Casts: `float(i)`, `int(f)` (truncate), `float(b)`/`int(b)` (`cvt`); `asint(x)`, `asfloat(x)`, `asbool(x)` reinterpret the
bits (no instruction; `asorientation(v4)` too). Vector constructors: `float3(x)` (broadcast, `dup`), `float3(a, b, c)`,
`float4(v3, a)`, `orientation(x, y, z, w)` (`vct`). Swizzles: `xyzw` or `rgba` letters plus the digits `0` and `1`
(`v.xyz1`, `a.111x`, `a.0x0`), on scalars too: one `swz`.

## 4. Operators and builtins → CBVM

| Source | CBVM |
|-|-|
| `a + b`, `a - b`, `a * b`, `a / b` (float), `a + b`, `a - b`, `a / b` (int) | `add sub mul div` |
| `a * b + c`, `c + a * b` (float; not when the product is in parentheses) | `madd` |
| `-a`, `a % b`, `a << n`, `a >> n` | `mop.neg mod sll sar` |
| `a * b` (int) | `mop.mul` (not seen in the corpus) |
| `&& \|\| ! & \| ^ ~` | `mop.and or not and or xor not` |
| `< <= > >= == !=` | `mop.lt le gt ge eq ne` → `bool`/`boolN` |
| `c ? a : b`, `select(b_false, a_true, c)` | `sel` |
| `float(x) int(x)` ... | `cvt` |
| `float3(x)` / `float3(a,b,c)` / `v.zyx` | `dup` / `vct` / `swz` |
| `sqrt rsqrt cbrt length normalize sin cos sincos tan asin acos atan exp exp2 log log2 rcp abs sign ceil floor frac fracSign saturate isfinite isinf all any` and `fast_sqrt` ... `fast_rcp` | `mfn1.<name>` (`sincos(x)` → `float2` `(sin, cos)`, confirmed in D2R.exe) |
| `atan2 step discretize min max dot cross` | `mfn2.<name>` |
| `lerp(a, b, t)`, `clamp(x, lo, hi)`, `within(x, lo, hi)` | `mfn3.0`, `mfn3.1`, `mfn3.2` |
| `pow(a, b)` | `exp2(b * log2(a))` |
| engine functions | `call` (section 5) |

Literals fold at compile time (float32 arithmetic); a value of constants only becomes one pool constant.

## 5. Engine functions, externals and bindings

Call an engine function by its name without the type suffix; the argument types pick the overload (first exact types,
then an int literal for a float, then a scalar literal broadcast to a vector; in table order) and the compiler adds
the context arguments: `rand(0, 1)` → `rand_float_float_RandCtx`, `rand(float3(-1), float3(1))` →
`rand_float3_float3_RandCtx`, `effect.age()`, `effect.position()`, `effect.isRunning()`, `self.kill(c)`,
`xform_l2w_f_masked(p, 17)`, `noise(x)`, `fast.noise(x)`, `hsv2rgb(c)`, `view.position(0)`, `scene.intersect(p, d, len,
true, 0)`, `rotate(v, q)`, `buildPayloadElement(...)`, `_pksi_vvUpdateLight_SI(id, on)` ... (all 101 in `fns.py`, each
with its result type). Methods on sampler and event externals: `Curve.sample(t)` (`samplerCurveN:sample_float`, N from
the extern kind `curveN`), `Curve.derive(t)`, `Shape.samplePosition()`, `Field.sample(p, s)`, `Stream.generate(...)`,
`Stream.duration()`, `Ev.initPayload(...)`, `Ev.kick(i)`, `Ev.appendPayload(...)`. Any other function:
`__call<float3>("mangled_name", args...)` (contexts still added from the name's suffix; a method's object goes first).

Names in expressions resolve in this order: locals, `extern` aliases, the bindings, then built-in externals
(`self.lifeRatio`, `self.invLife`, `effect.dt`, `scene.dt`, `scene.time`, `EffectID`, the four contexts) and finally a
stream named after the field (`self.X` → external `X`, its type from the first assignment). Default descriptors of
externals the compiler adds: stream `[4 0 access]` (1 read, 2 write, 3 both), `effect.dt`/`scene.*` `[4 2 1]`,
`ParticleContextS`/`RandContext` `[4 2 1]`, `ParticleContextI` `[3 2 1]`, `SceneContext` `[2 2 1]`, attribute `[3 2 1]`,
sampler `[1 2 1]`, event `[2 2 7]`; the table ends with the empty entry and `EffectID [4 2 1]`.

**Binding to an effect.** A layer's particle fields are externals named by hex field ids (`"16"`, `"1A"`); attributes
are `__a_<Name>`, samplers `__sampler_<n>`, events `__e_<Name>`. `compile(..., base=script)` (the script being
replaced: what `fx_script_write` does) keeps `base`'s whole external table, descriptors and order (D2R binds it by name
and by the layer's other compiled objects), appends any new external before the empty entry, and copies `.kind` and
`.order`. `bindings={"self.Position": "16", "Color": "__sampler_2", ...}` names externals (the graph lane's
`Effect` gives them: `bindings_from_effect(effect, layer)` reads `Layer.fields`, `Effect.attributes`, `Effect.samplers`
by duck typing). `effect=` is accepted for that call. Without a base, the compiler builds a fresh table.

**Script kinds** (`#kind`): none = spawn (particle init, also the spawner's init), 4 and 5 = evolve (D2R keeps two
identical copies: write both), 3 = a layer-level evolve, 1/2 rare. Default: none.

## 6. Decompiler

`decompile(script)` gives source that `compile` turns back into a script with the same behaviour: the same externals
(declared with `extern` in table order, with descriptors), the same `#kind`, `#fns` and `#order`, the same sequence of
stores and side-effecting calls with the same value trees (registers, scheduling and constant order may differ). Values
used once are inlined; values used more often, and any whose inlining would move an impure call past another effect or
a load past a store of the same external, become locals `t<N>`; impure calls whose result is unused stay statements.
`madd` reads `a * b + c`, a separate `mul` then `add` reads `(a * b) + c`, `sel` reads `c ? b : a`, a swizzle with
constants reads `.111x`, a register read under another type `asint(x)`. An overload the surface name would not pick
(e.g. `samplerCurve1:derive_float` vs `..._pCtxI`) is written `__call<T>("mangled", obj, args)`. Externals keep
generic names (`self._1A`, `sampler3`, attributes by their name) unless `bindings` names them.

`check(script)` runs the round trip and compares the two scripts as dataflow (`fx/lang/equiv.py`): the externals
table, the kind, and the ordered list of effects (stores, calls with side effects) with each value as a tree of
operations, constants (by their first n components: the game packs float3 constants both `xyzxyzxy` and `xyzxxyzx`),
loads (a load after a store of the same external is the stored tree) and pure calls; constant sub-trees fold.

## 7. Limits

No loops, branches, user functions or recursion (the VM has none). Register indices up to 2^21, at most 32,767
externals, 65,535 functions. `orientation` supports only engine functions (no arithmetic). Behaviour of int `*`
(`mop.mul`), int vectors in `vct` (a raw 32-bit lane copy in D2R.exe) and `mfn3.2` are read from D2R.exe's kernels
but no game script uses them. A written script must pass `hd_cbvm` (decode → encode byte for byte) and is checked in game (a test copy of
the game, testing/README.md) before shipping.

## 8. Examples

Spiral spawner (particle spawn script): position on a rising spiral, outward velocity, random life.

```hfx
extern float3 self.Position = "0";
extern float3 self.Velocity = "1";
extern float  self.Life = "2";
float t = rand(0, 1) * 6.2831853 * 4;          // four turns
float2 sc = sincos(t);
float r = 0.2 + t * 0.05;
self.Position = effect.position() + float3(sc.y * r, t * 0.1, sc.x * r);
self.Velocity = normalize(float3(sc.y, 0.2, sc.x)) * 1.5;
self.Life = rand(0.8, 1.6);
```

Colour over life (evolve): a curve sampler gives the colour, alpha fades out.

```hfx
#kind 4
extern curve4 ColorCurve = "__sampler_0";
extern float4 self.Color = "5";
float4 c = ColorCurve.sample(self.lifeRatio);
self.Color = float4(c.xyz, c.w * saturate(2 - 2 * self.lifeRatio));
```

Gravity and bounce (evolve): the example of section 2.
