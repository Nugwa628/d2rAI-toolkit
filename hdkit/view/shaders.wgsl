// hdkit.view: one shader set for the offline renderer (and later the browser page).
// Vertices come posed and skinned in world space (CPU). Colour targets are linear rgba16float, premultiplied alpha;
// the CPU encodes sRGB after the MSAA resolve.
//
// Lit model (uncalibrated, monster3d METHOD rules 1, 10, 13, 15): albedo (sRGB texture), stock D2R normal map (DXT5nm:
// x in alpha, y in green), ORM (R occlusion, G roughness, B metal, A emissive mask: emissive = albedo x (1 - A), as the
// game's ps_models; lanes r4-small, r4-tidy). Diffuse = albedo (1 - metal) (ambient * occlusion + key * N.L); specular =
// GGX key highlight + a rough Fresnel term of the environment colour, F0 = mix(0.04, albedo, metal), so a black dielectric
// still reflects about 4 % (rule 15). The floor at y = 0 hides what lies below it (rule 3).
// Local point lights (up to MAX_POINTS; diffuse only, as the game's map lights carry specularContribution 0): colour x
// power x f(d) x N.L with f(d) = (1 - (d / radius)^2)^attenuation inside the radius (form fitted to the player's light pool
// on the ground of game shots, viewer/p5), world units.
// Depth fog (the vis file's depthFogColor and depthFogDensity, rig.from_vis): lit colour x (1 - f) + fog rgb, with
// fog.rgb = colour x f and f = density, the same over a unit (orthographic camera). It is the floor a black albedo
// still shows in game (monster3d METHOD rule 13). 0 = off.

// Point-light distance term: w >= 0 the map-light form (1 - (d / r)^2)^w; w < 0 D2R's own (effect lights, calib3):
// max(1 - (d / r)^-w, 0)^2 / (1 + 0.0929 d^2) (hdkit.fx.packers.LIGHT_INV_SQ).
fn point_falloff(d: f32, r: f32, w: f32) -> f32 {
  if (w < 0.0) {
    let g = max(1.0 - pow(min(d / max(r, 1e-6), 1.0), -w), 0.0);
    return g * g / (1.0 + 0.092903040349 * d * d);
  }
  let q = clamp(1.0 - (d * d) / max(r * r, 1e-6), 0.0, 1.0);
  return pow(q, w);
}

struct U {
  mvp: mat4x4<f32>,
  view_dir: vec4<f32>,     // xyz: towards the camera
  ambient: vec4<f32>,      // rgb
  key_dir: vec4<f32>,      // xyz: towards the key light
  key_col: vec4<f32>,      // rgb
  env_col: vec4<f32>,      // rgb
  p0: vec4<f32>,           // x: pass (0 lit, 1 albedo, 2 normals, 3 depth, 4 wire, 5 unlit, 6 glow alone, 7-10 ORM r/g/b/a), y: glow gain, z: normal-map green sign, w: alpha cut
  p1: vec4<f32>,           // x: depth of the far end, y: depth of the near end, z: floor clip (1 on), w: specular gain
  p2: vec4<f32>,           // x: number of point lights, y: emissive gain (x (1 - ORM a) x albedo), z: blend (alpha x z; 0 = opaque)
  pts: array<vec4<f32>, 64>,   // per light i: [2i] xyz position, w radius; [2i+1] rgb colour x power, w attenuation
  fog: vec4<f32>,          // rgb: fog colour x f (added), w: f (the lit colour's share lost to fog)
  p3: vec4<f32>,           // terrain: xy world (x, z) of the weight map's corner, zw its size in world units
  p4: vec4<f32>,           // terrain: x world units per layer texture repeat, y number of layers, z height-blend sharpness
  // game unit light (gpu.Light.game, rig "capture"; all 0 = off, the lit model above): ps_models_p of D2R as read from
  // a RenderDoc capture (docs/hdfx.md section 3, "Unit light")
  g0: vec4<f32>,           // x: 1 on, y: number of directional lights (<= 4), z: exposure, w: value curve on
  g1: vec4<f32>,           // value curve poly . (1, V, V^2, V^3) (cb2[18])
  g2: vec4<f32>,           // x: value curve strength (characterBrightnessFactor), y: brightness, z: IBL intensity, w: direct scale
  g3: vec4<f32>,           // x: IBL diffuse weight, y: IBL specular weight
  sh: array<vec4<f32>, 9>, // SH irradiance (world axes): 1, y, z, x, xy, yz, 3z^2 - 1, xz, x^2 - y^2
  gd: array<vec4<f32>, 8>, // per directional light i: [2i] xyz towards the light (world), w diffuse weight; [2i+1] rgb colour x intensity, w specular weight
  // unit colour variant (view/unitvariant.py, lane r3-unitmat): [6k..6k+5] the ColorTransform of KTINT channel k (R, G, B),
  // [18] x: 1 on, y: mask floor (1 under a colour state)
  kt: array<vec4<f32>, 19>,
};

@group(0) @binding(0) var<uniform> u: U;
@group(0) @binding(1) var samp: sampler;
@group(0) @binding(2) var t_alb: texture_2d<f32>;
@group(0) @binding(3) var t_nrm: texture_2d<f32>;
@group(0) @binding(4) var t_orm: texture_2d<f32>;
@group(0) @binding(5) var t_mask: texture_2d<f32>;
@group(0) @binding(6) var t_strip: texture_2d<f32>;

struct VIn {
  @location(0) pos: vec3<f32>,
  @location(1) nrm: vec3<f32>,
  @location(2) tan: vec4<f32>,
  @location(3) uv: vec2<f32>,
  @location(4) bary: vec3<f32>,
};

struct VOut {
  @builtin(position) clip: vec4<f32>,
  @location(0) wpos: vec3<f32>,
  @location(1) nrm: vec3<f32>,
  @location(2) tan: vec4<f32>,
  @location(3) uv: vec2<f32>,
  @location(4) bary: vec3<f32>,
};

@vertex
fn vs(v: VIn) -> VOut {
  var o: VOut;
  o.clip = u.mvp * vec4<f32>(v.pos, 1.0);
  o.wpos = v.pos;
  o.nrm = v.nrm;
  o.tan = v.tan;
  o.uv = v.uv;
  o.bary = v.bary;
  return o;
}

const PI: f32 = 3.14159265;

fn shading_normal(i: VOut, front: bool, s: vec4<f32>) -> vec3<f32> {
  var ng = normalize(i.nrm);
  if (!front) { ng = -ng; }
  let t = i.tan.xyz - ng * dot(ng, i.tan.xyz);
  if (dot(t, t) < 1e-8) { return ng; }
  let tn = normalize(t);
  let bn = cross(ng, tn) * select(1.0, -1.0, i.tan.w < 0.0);
  let x = s.a * 2.0 - 1.0;
  let y = (s.g * 2.0 - 1.0) * u.p0.z;
  let z = sqrt(max(0.0, 1.0 - x * x - y * y));
  return normalize(tn * x + bn * y + ng * z);
}

fn ggx(n: vec3<f32>, v: vec3<f32>, l: vec3<f32>, rough: f32, f0: vec3<f32>) -> vec3<f32> {
  let h = normalize(v + l);
  let nl = max(dot(n, l), 0.0);
  let nv = max(dot(n, v), 1e-4);
  let nh = max(dot(n, h), 0.0);
  let a = max(rough * rough, 0.02);
  let a2 = a * a;
  let d = a2 / (PI * pow(nh * nh * (a2 - 1.0) + 1.0, 2.0));
  let k = a / 2.0;
  let g = (nl / (nl * (1.0 - k) + k)) * (nv / (nv * (1.0 - k) + k));
  let f = f0 + (vec3<f32>(1.0) - f0) * pow(1.0 - max(dot(h, v), 0.0), 5.0);
  return d * g * f / max(4.0 * nl * nv, 1e-4) * nl;
}

// ---- game unit light (u.g0.x = 1) ----------------------------------------------------------------------------------------
// ps_models_p of D2R (RenderDoc capture, SPIR-V through SPIRV-Cross; docs/hdfx.md section 3): ORM r occlusion, g roughness,
// b metal; F0 = mix(0.04, albedo, metal), alpha = roughness^2. Per light: N.L x colour x (diffuse weight x albedo x occlusion
// x (1 - metal) x (1 - F) / pi + specular weight x D x F x Vis x (1 + multiscatter)), GGX D, Schlick F, Hammon's Vis;
// local lights fall off as max(1 - (d / radius)^e, 0)^2 / (1 + 0.0929 d^2) (point_falloff, atten -e; map-light points
// with atten >= 0 keep their own form). Ambient: SH irradiance x occlusion / pi,
// diffuse x albedo x (1 - metal) x (1 - Fenv (1 + ms)) and specular Fenv x (occlusion x env + irradiance x ms), times the IBL
// intensity. Then x exposure, the unit value curve, / exposure (the viewer's target is scene light).
// Shadow maps (hdkit/view/shadow.py): directional light k x shd_light(k), then the direct sum x G (shd_global); the CLI
// swaps the stub block below for the real maps (bind group 1); this file alone (the browser page) has none.
// Not here: the split-sum LUT and the specular cube (Karis's analytic fit and the SH at R
// stand in), the screen-space AO and the specular-AA roughness widening.
// @shadow-stub-begin
fn shd_global(wpos: vec3<f32>, n: vec3<f32>) -> f32 { return 1.0; }
fn shd_light(j: i32, wpos: vec3<f32>, n: vec3<f32>, g: f32) -> f32 { return 1.0; }
fn shd_local(lpos: vec3<f32>, wpos: vec3<f32>, n: vec3<f32>, shadowed: f32) -> f32 { return 1.0; }
// @shadow-stub-end

fn sh_eval(n: vec3<f32>) -> vec3<f32> {
  let x = n.x; let y = n.y; let z = n.z;
  return u.sh[0].rgb + u.sh[1].rgb * y + u.sh[2].rgb * z + u.sh[3].rgb * x + u.sh[4].rgb * (x * y) + u.sh[5].rgb * (y * z)
       + u.sh[6].rgb * (3.0 * z * z - 1.0) + u.sh[7].rgb * (x * z) + u.sh[8].rgb * (x * x - y * y);
}

fn g_light(n: vec3<f32>, v: vec3<f32>, l: vec3<f32>, base: vec3<f32>, f0: vec3<f32>, a: f32, ms: vec3<f32>,
           dw: f32, sw: f32) -> vec3<f32> {
  let h = normalize(v + l);
  let nh = clamp(dot(n, h), 0.0, 1.0);
  let vh = clamp(dot(h, v), 0.0, 1.0);
  let nl = clamp(dot(n, l), 0.0, 1.0);
  let nv = abs(dot(n, v)) + 0.001;
  let t = a * nh;
  let dd = a / (t * t + 1.0 - nh * nh);
  let d = dd * dd / PI;
  let x = 1.0 - vh;
  let x5 = x * x * x * x * x;
  let f = f0 * (1.0 - x5) + vec3<f32>(x5);
  let g2 = 2.0 * nv * nl;
  let vis = 0.5 / (a * (nv + nl - g2) + g2);
  let spec = d * f * vis;
  return nl * (dw * base * (vec3<f32>(1.0) - f) / PI + sw * (spec + ms * spec));
}

fn game_shade(n: vec3<f32>, v: vec3<f32>, wpos: vec3<f32>, alb: vec3<f32>, orm: vec4<f32>) -> vec3<f32> {
  let ao = orm.r;
  let rough = max(orm.g, 0.04);
  let metal = orm.b;
  let a = rough * rough;
  let f0 = mix(vec3<f32>(0.04), alb, metal);
  let base = alb * ao * (1.0 - metal);
  let nv = max(dot(n, v), 0.0);
  // split-sum terms (Karis's analytic fit of the LUT the game samples at (N.V, alpha))
  let c0 = vec4<f32>(-1.0, -0.0275, -0.572, 0.022);
  let c1 = vec4<f32>(1.0, 0.0425, 1.04, -0.04);
  let r4 = rough * c0 + c1;
  let a004 = min(r4.x * r4.x, exp2(-9.28 * nv)) * r4.x + r4.y;
  let ab = vec2<f32>(-1.04, 1.04) * a004 + r4.zw;
  let e = 1.0 - (ab.x + ab.y);
  let fp = f0 + (vec3<f32>(1.0) - f0) * 0.047619;
  let ms = e * fp / (vec3<f32>(1.0) - fp * e);
  var direct = vec3<f32>(0.0);
  let sg = shd_global(wpos, n);
  let nd = i32(u.g0.y + 0.5);
  for (var k = 0; k < nd; k = k + 1) {
    let dl = u.gd[2 * k];
    let dc = u.gd[2 * k + 1];
    direct = direct + dc.rgb * g_light(n, v, normalize(dl.xyz), base, f0, a, ms, dl.w, dc.w) * shd_light(k, wpos, n, sg);
  }
  let npt = i32(u.p2.x + 0.5);
  for (var k = 0; k < npt; k = k + 1) {
    let pa = u.pts[2 * k];
    let pb = u.pts[2 * k + 1];
    let dv = pa.xyz - wpos;
    let d = max(length(dv), 1e-4);
    direct = direct + pb.rgb * point_falloff(d, pa.w, pb.w) * g_light(n, v, dv / d, base, f0, a, ms, 1.0, 1.0)
             * shd_local(pa.xyz, wpos, n, 1.0);
  }
  let x = 1.0 - nv;
  let fenv = (f0 + (max(f0, vec3<f32>(1.0 - rough)) - f0) * (x * x * x * x * x)) * ab.x + vec3<f32>(ab.y);
  let irr = max(sh_eval(n), vec3<f32>(0.0)) * ao / PI;
  let rr = reflect(-v, n);
  let env = max(sh_eval(rr), vec3<f32>(0.0)) / PI;
  let ibl = (fenv * ao * env + irr * fenv * ms) * u.g3.y + irr * (1.0 - metal) * alb * (vec3<f32>(1.0) - (ms * fenv + fenv)) * u.g3.x;
  let col = direct * (u.g2.w * sg) + ibl * u.g2.z;
  if (u.g0.w < 0.5) { return col; }
  // the unit value curve (ps_models_p end, cb2[18..19]): exposed colour to HSV, V -> (V + s poly(sat V)) (1 + b)
  let ex = col * u.g0.z;
  let vmax = max(ex.r, max(ex.g, ex.b));
  if (vmax <= 0.0) { return col; }
  let vs = clamp(vmax, 0.0, 1.0);
  let nvv = (vmax + u.g2.x * dot(u.g1, vec4<f32>(1.0, vs, vs * vs, vs * vs * vs))) * (1.0 + u.g2.y);
  return col * (nvv / vmax);
}

// One ColorTransform of D2R's ps_models_p on linear rgb (unitvariant.apply_one is the reference): HSV, hue + shift and
// six band shifts, saturation and value scales, back to RGB, ^ gamma, then the tint.
fn kt_one(c: vec3<f32>, b: i32) -> vec3<f32> {
  let a = u.kt[b]; let h0a = u.kt[b + 1]; let h1a = u.kt[b + 2]; let s0a = u.kt[b + 3]; let s1a = u.kt[b + 4]; let ti = u.kt[b + 5];
  let mx = max(c.b, max(c.r, c.g));
  let d = mx - min(c.b, min(c.r, c.g));
  let has = d != 0.0;
  let dd = select(1.0, d, has);
  let s = select(0.0, d / max(mx, 1e-30), mx != 0.0);
  let dr = ((mx - c.r) / 6.0 + d * 0.5) / dd;
  let dg = ((mx - c.g) / 6.0 + d * 0.5) / dd;
  let db = ((mx - c.b) / 6.0 + d * 0.5) / dd;
  var h = 0.0;
  if (c.r == mx) { h = db - dg; } else if (c.g == mx) { h = dr - db + 1.0 / 3.0; } else if (c.b == mx) { h = dg - dr + 2.0 / 3.0; }
  if (h < 0.0) { h = h + 1.0; }
  if (h > 1.0) { h = h - 1.0; }
  let hz = select(0.0, h, has);
  let x0 = fract(hz + 0.125);
  let w0 = clamp(3.0 - 12.0 * x0, 0.0, 1.0) * min(12.0 * x0, 1.0);
  let hh = select(0.0, h, has) * 12.0;
  let w1 = clamp(3.5 - hh, 0.0, 1.0) * clamp(hh - 0.5, 0.0, 1.0);
  let w2 = clamp(5.5 - hh, 0.0, 1.0) * clamp(hh - 2.5, 0.0, 1.0);
  let w3 = clamp(7.5 - hh, 0.0, 1.0) * clamp(hh - 4.5, 0.0, 1.0);
  let w4 = clamp(9.5 - hh, 0.0, 1.0) * clamp(hh - 6.5, 0.0, 1.0);
  let w5 = clamp(11.5 - hh, 0.0, 1.0) * clamp(hh - 8.5, 0.0, 1.0);
  var sat = s * (1.0 + a.y);
  sat = sat + w0 * s0a.x * sat; sat = sat + w1 * s0a.y * sat; sat = sat + w2 * s0a.z * sat;
  sat = sat + w3 * s1a.x * sat; sat = sat + w4 * s1a.y * sat; sat = sat + w5 * s1a.z * sat;
  var hue = fract(hz + a.x);
  hue = fract(hue + h0a.x * w0); hue = fract(hue + h0a.y * w1); hue = fract(hue + h0a.z * w2);
  hue = fract(hue + h1a.x * w3); hue = fract(hue + h1a.y * w4); hue = fract(hue + h1a.z * w5);
  let v = mx * (1.0 + a.z);
  let sf = select(0.0, sat, has);
  let ok = has && sat != 0.0;
  let i = floor(hue * 6.0);
  let f = hue * 6.0 - i;
  let p = v * (1.0 - sf);
  let q = v * (1.0 - sf * f);
  let t = v * (1.0 - sf * (1.0 - f));
  var o = vec3<f32>(v);
  if (ok) {
    if (i == 0.0) { o = vec3<f32>(v, t, p); } else if (i == 1.0) { o = vec3<f32>(q, v, p); }
    else if (i == 2.0) { o = vec3<f32>(p, v, t); } else if (i == 3.0) { o = vec3<f32>(p, q, v); }
    else if (i == 4.0) { o = vec3<f32>(t, p, v); } else { o = vec3<f32>(v, p, q); }
  }
  o = pow(clamp(o, vec3<f32>(0.0), vec3<f32>(1.0)), vec3<f32>(a.w));
  return o + ti.w * (o * ti.rgb - o);
}

// The KTINT recolour of a unit's albedo: the three channel transforms mixed by max(mask, floor) (normalised over 1).
fn kt_apply(c: vec3<f32>, mask: vec3<f32>) -> vec3<f32> {
  if (u.kt[18].x < 0.5) { return c; }
  var m = max(mask, vec3<f32>(u.kt[18].y));
  let sm = m.r + m.g + m.b;
  if (sm > 1.0) { m = m / sm; }
  let rest = 1.0 - (m.r + m.g + m.b);
  return c * rest + m.r * kt_one(c, 0) + m.g * kt_one(c, 6) + m.b * kt_one(c, 12);
}

@fragment
fn fs_main(i: VOut, @builtin(front_facing) front: bool) -> @location(0) vec4<f32> {
  // every sample and derivative first: they need uniform control flow
  let alb0 = textureSample(t_alb, samp, i.uv);
  let nmap = textureSample(t_nrm, samp, i.uv);
  let orm = textureSample(t_orm, samp, i.uv);
  let mask4 = textureSample(t_mask, samp, i.uv);
  let emask = 1.0 - orm.a;          // the game's ps_models: albedo x (1 - ORM alpha) x EmissiveIntensity (no ORM: 1)
  let w = fwidth(i.bary);
  let alb = vec4<f32>(kt_apply(alb0.rgb, mask4.rgb), alb0.a);   // the unit variant's KTINT recolour (off: as sampled)
  if (u.p1.z > 0.5 && i.wpos.y < 0.0) { discard; }
  if (alb.a < u.p0.w) { discard; }
  let pass_id = i32(u.p0.x + 0.5);
  if (pass_id == 11) { return vec4<f32>(vec3<f32>(clamp(i.wpos.y / 20.0, 0.0, 1.0)), 1.0); }   // height above the ground
  if (pass_id == 1 || pass_id == 5) { return vec4<f32>(alb.rgb, 1.0); }
  if (pass_id == 6) { return vec4<f32>(0.0, 0.0, 0.0, 1.0); }      // glow alone: the body is black but still hides
  if (pass_id >= 7 && pass_id <= 10) {                               // one ORM channel as grey (data, not colour)
    var ch = array<f32, 4>(orm.r, orm.g, orm.b, orm.a);
    return vec4<f32>(vec3<f32>(ch[pass_id - 7]), 1.0);
  }
  let n = shading_normal(i, front, nmap);
  if (pass_id == 2) { return vec4<f32>(n * 0.5 + vec3<f32>(0.5), 1.0); }
  if (pass_id == 3) {
    let d = (dot(i.wpos, u.view_dir.xyz) - u.p1.x) / max(u.p1.y - u.p1.x, 1e-6);
    return vec4<f32>(vec3<f32>(clamp(d, 0.0, 1.0)), 1.0);
  }
  if (pass_id == 4) {
    let e = smoothstep(vec3<f32>(0.0), w * 1.2, i.bary);
    let edge = 1.0 - min(min(e.x, e.y), e.z);
    return vec4<f32>(mix(alb.rgb * 0.35, vec3<f32>(1.0, 0.85, 0.2), edge), 1.0);
  }
  if (u.g0.x > 0.5) {
    let gc = game_shade(n, normalize(u.view_dir.xyz), i.wpos, alb.rgb, orm);
    let emis_g = alb.rgb * emask * u.p2.y;
    if (u.p2.z > 0.0) {
      let a = clamp(alb.a * u.p2.z, 0.0, 1.0);
      return vec4<f32>((gc + emis_g) * a, a);
    }
    return vec4<f32>(gc + emis_g, 1.0);
  }
  let ao = orm.r;
  let rough = clamp(orm.g, 0.04, 1.0);
  let metal = orm.b;
  let v = normalize(u.view_dir.xyz);
  let l = normalize(u.key_dir.xyz);
  let ksg = shd_global(i.wpos, n);
  let nl = max(dot(n, l), 0.0) * shd_light(0, i.wpos, n, ksg) * ksg;
  let f0 = mix(vec3<f32>(0.04), alb.rgb, metal);
  var local = vec3<f32>(0.0);
  let npt = i32(u.p2.x + 0.5);
  for (var k = 0; k < npt; k = k + 1) {
    let a = u.pts[2 * k];
    let b = u.pts[2 * k + 1];
    let dv = a.xyz - i.wpos;
    let d = length(dv);
    local = local + b.rgb * point_falloff(d, a.w, b.w) * max(dot(n, dv / max(d, 1e-4)), 0.0) * shd_local(a.xyz, i.wpos, n, 1.0);
  }
  let diffuse = alb.rgb * (1.0 - metal) * (u.ambient.rgb * ao + u.key_col.rgb * nl + local);
  let spec_key = ggx(n, v, l, rough, f0) * u.key_col.rgb * u.p1.w * shd_light(0, i.wpos, n, ksg) * ksg;
  let nv = max(dot(n, v), 0.0);
  let fr = f0 + (max(vec3<f32>(1.0 - rough), f0) - f0) * pow(1.0 - nv, 5.0);
  let env = fr * u.env_col.rgb * ao;
  let emis = alb.rgb * emask * u.p2.y;
  let col = (diffuse + spec_key + env + emis) * (1.0 - u.fog.w) + u.fog.rgb;
  if (u.p2.z > 0.0) {                     // alpha-blended material (AlphaIntensity): premultiplied
    let a = clamp(alb.a * u.p2.z, 0.0, 1.0);
    return vec4<f32>(col * a, a);
  }
  return vec4<f32>(col, 1.0);
}

// The ground: a splat of up to 16 terrain layers (texture arrays), weights from a map over the level in world (x, z)
// (4 rgba layers = 16 weights), each layer's albedo alpha used as its height for the blend (D2R's height blend,
// approximated: weight x (height + 0.5)^sharpness). Lit like fs_main.
@group(0) @binding(7) var t_lalb: texture_2d_array<f32>;
@group(0) @binding(8) var t_lnrm: texture_2d_array<f32>;
@group(0) @binding(9) var t_lorm: texture_2d_array<f32>;
@group(0) @binding(10) var t_wts: texture_2d_array<f32>;

@fragment
fn fs_terrain(i: VOut, @builtin(front_facing) front: bool) -> @location(0) vec4<f32> {
  let wuv = (i.wpos.xz - u.p3.xy) / u.p3.zw;
  let luv = i.wpos.xz / u.p4.x;
  let dx = dpdx(luv);
  let dy = dpdy(luv);
  var w: array<f32, 16>;
  for (var q = 0; q < 4; q = q + 1) {
    let s = textureSampleLevel(t_wts, samp, wuv, q, 0.0);
    w[4 * q] = s.r; w[4 * q + 1] = s.g; w[4 * q + 2] = s.b; w[4 * q + 3] = s.a;
  }
  let nl_ = i32(u.p4.y + 0.5);
  var alb = vec3<f32>(0.0);
  var nm = vec4<f32>(0.0);
  var orm = vec4<f32>(0.0);
  var tot = 0.0;
  for (var k = 0; k < nl_; k = k + 1) {
    if (w[k] < 0.004) { continue; }
    let a = textureSampleGrad(t_lalb, samp, luv, k, dx, dy);
    let hw = w[k] * pow(a.a + 0.5, u.p4.z);
    alb = alb + a.rgb * hw;
    nm = nm + textureSampleGrad(t_lnrm, samp, luv, k, dx, dy) * hw;
    orm = orm + textureSampleGrad(t_lorm, samp, luv, k, dx, dy) * hw;
    tot = tot + hw;
  }
  if (tot <= 0.0) { discard; }
  alb = alb / tot; nm = nm / tot; orm = orm / tot;
  let pass_id = i32(u.p0.x + 0.5);
  if (pass_id == 11) { return vec4<f32>(vec3<f32>(clamp(i.wpos.y / 20.0, 0.0, 1.0)), 1.0); }
  if (pass_id == 1 || pass_id == 5) { return vec4<f32>(alb, 1.0); }
  let n = shading_normal(i, front, nm);
  if (pass_id == 2) { return vec4<f32>(n * 0.5 + vec3<f32>(0.5), 1.0); }
  let ao = orm.r;
  let rough = clamp(orm.g, 0.04, 1.0);
  let v = normalize(u.view_dir.xyz);
  let l = normalize(u.key_dir.xyz);
  let tsg = shd_global(i.wpos, n);
  let tsk = shd_light(0, i.wpos, n, tsg) * tsg;
  let nl = max(dot(n, l), 0.0) * tsk;
  let f0 = vec3<f32>(0.04);
  var local = vec3<f32>(0.0);
  let npt = i32(u.p2.x + 0.5);
  for (var k = 0; k < npt; k = k + 1) {
    let a = u.pts[2 * k];
    let b = u.pts[2 * k + 1];
    let dv = a.xyz - i.wpos;
    let d = length(dv);
    local = local + b.rgb * point_falloff(d, a.w, b.w) * max(dot(n, dv / max(d, 1e-4)), 0.0) * shd_local(a.xyz, i.wpos, n, 1.0);
  }
  let diffuse = alb * (u.ambient.rgb * ao + u.key_col.rgb * nl + local);
  let spec_key = ggx(n, v, l, rough, f0) * u.key_col.rgb * u.p1.w * tsk;
  let nv = max(dot(n, v), 0.0);
  let fr = f0 + (max(vec3<f32>(1.0 - rough), f0) - f0) * pow(1.0 - nv, 5.0);
  let env = fr * u.env_col.rgb * ao;
  return vec4<f32>((diffuse + spec_key + env) * (1.0 - u.fog.w) + u.fog.rgb, 1.0);
}

// The self-lit layer: the same skinned model again, additive, mask (one channel, model UVs) times its gradient strip.
// Alpha 0: the layer adds light, not coverage.
@fragment
fn fs_glow(i: VOut) -> @location(0) vec4<f32> {
  let m = textureSample(t_mask, samp, i.uv).r;
  let c = textureSample(t_strip, samp, vec2<f32>(clamp(m, 0.002, 0.998), 0.5)).rgb;
  if (u.p1.z > 0.5 && i.wpos.y < 0.0) { discard; }
  return vec4<f32>(c * m * u.p0.y, 0.0);
}
