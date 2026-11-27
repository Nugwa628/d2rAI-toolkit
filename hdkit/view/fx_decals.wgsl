// All FX decals of a frame in one pass, as D2R's ps_terrain walks its clustered decal list (lane r3-decal2; the CPU
// reference is hdkit/fx/decal.py `blend`, docs/hdfx-shaders.md 13). Appended to view/fx.wgsl (uses FxU u, the
// samplers, t_depth, t3 (plate), light_scene, PI_INV).
// The game: for each decal in list order, w = alpha x edge fades [x angle] [x threshold]; the surface material (normal,
// occlusion, roughness, metalness, albedo) is lerped towards the decal's by w, emissive += albedo' x w x k; the walk
// stops once the summed w reaches 1; the terrain lighting then runs ONCE on the mixed material.
// The viewer keeps no G-buffer: the ground's albedo A is unknown, so albedo = A x cA + aD (cA = product of (1 - w_a),
// aD the decals' part) and, diffuse light being linear in albedo, out = dst x k + add with k = cA D' / D_g,
// add = aD D' + S' - k S_g + E (fs_decals_mul writes k with dst x src blending, fs_decals_add the rest; over a game
// shot fs_decals_add writes plate x (k - 1) + add). Receivers: only the ground (D2R: the terrain and model draws with
// cb2[20].x bit 16; units are not relit): a pixel takes decals only when its world y is within hdr.z of hdr.y (hdr.w 1).
//
// drecs: [0] header (n decals, receiver plane y, tolerance, receivers on), then 8 vec4 per decal:
//   0-2 world -> box rows; 3 box X (world) | flags; 4 box Y (projection axis) | edge fade;
//   5 cos min, cos max, threshold, sharpness (hard^2 x 64); 6 emissive intensity, emissive power, slice albedo, slice normal;
//   7 slice ORM, slice emissive, -, -

@group(1) @binding(0) var<storage, read> drecs: array<vec4<f32>>;
@group(1) @binding(1) var ta_alb: texture_2d_array<f32>;
@group(1) @binding(2) var ta_nrm: texture_2d_array<f32>;
@group(1) @binding(3) var ta_orm: texture_2d_array<f32>;
@group(1) @binding(4) var ta_em: texture_2d_array<f32>;

struct DPos { @builtin(position) clip: vec4<f32>, };

@vertex
fn vs_decals(@builtin(vertex_index) vi: u32) -> DPos {
  var o: DPos;
  let x = f32((vi << 1u) & 2u) * 2.0 - 1.0;
  let y = f32(vi & 2u) * 2.0 - 1.0;
  o.clip = vec4<f32>(x, y, 0.5, 1.0);
  return o;
}

struct DecalsOut { ok: bool, k: vec3<f32>, add: vec3<f32>, };

fn decals_eval(clip: vec4<f32>) -> DecalsOut {
  var o: DecalsOut;
  o.ok = false;
  let px = vec2<i32>(clip.xy);
  let zs = textureLoad(t_depth, px, 0).r;
  let ndc = vec2<f32>(clip.x / u.vp.x * 2.0 - 1.0, 1.0 - clip.y / u.vp.y * 2.0);
  let wh = u.inv_mvp * vec4<f32>(ndc, zs, 1.0);
  let wp = wh.xyz / wh.w;
  // derivatives in uniform control flow: the surface normal from the depth and the world step per pixel
  let dx = dpdx(wp);
  let dy = dpdy(wp);
  var ng = cross(dy, dx);
  let v = normalize(u.cam_w.xyz);
  if (dot(ng, ng) < 1e-20) { ng = vec3<f32>(0.0, 1.0, 0.0); }
  ng = normalize(ng);
  if (dot(ng, v) < 0.0) { ng = -ng; }
  let hdr = drecs[0];
  if (zs >= 1.0) { return o; }
  if (hdr.w > 0.5 && abs(wp.y - hdr.y) > hdr.z) { return o; }
  let nd = i32(hdr.x + 0.5);
  let rg = u.ops.w;
  var n = ng;
  var ao = 1.0;
  var rough = rg;
  var metal = 0.0;
  var cA = 1.0;
  var aD = vec3<f32>(0.0);
  var em = vec3<f32>(0.0);
  var sw = 0.0;
  var hit = false;
  for (var i = 0; i < nd; i = i + 1) {
    let b = 1 + 8 * i;
    let r0 = drecs[b];
    let r1 = drecs[b + 1];
    let r2 = drecs[b + 2];
    let lp = vec3<f32>(dot(r0, vec4<f32>(wp, 1.0)), dot(r1, vec4<f32>(wp, 1.0)), dot(r2, vec4<f32>(wp, 1.0)));
    if (any(abs(lp) > vec3<f32>(0.5))) { continue; }
    let xa = drecs[b + 3];
    let ya = drecs[b + 4];
    let pr = drecs[b + 5];
    let es = drecs[b + 6];
    let sl = drecs[b + 7];
    let fl = u32(xa.w + 0.5);
    let uv = vec2<f32>(lp.x + 0.5, lp.z + 0.5);
    let gx = vec2<f32>(dot(r0.xyz, dx), dot(r2.xyz, dx));
    let gy = vec2<f32>(dot(r0.xyz, dy), dot(r2.xyz, dy));
    let alb = textureSampleGrad(ta_alb, s_clamp, uv, i32(es.z + 0.5), gx, gy);
    let orm = textureSampleGrad(ta_orm, s_clamp, uv, i32(sl.x + 0.5), gx, gy);
    // weight: albedo alpha x xz edge fade x angle cut x threshold
    var w = alb.a;
    let e = ya.w;
    if (e > 0.0) {
      let f = vec2<f32>(1.0) - clamp((abs(lp.xz) - vec2<f32>(0.5 * (1.0 - e))) / (0.5 * e), vec2<f32>(0.0), vec2<f32>(1.0));
      w = w * f.x * f.y;
    }
    if ((fl & 1u) != 0u) {
      let c = dot(ng, ya.xyz);
      w = w * select(0.0, 1.0, pr.x < c && c < pr.y);
    }
    if ((fl & 64u) != 0u) { w = w * clamp((orm.a - pr.z) * pr.w + 0.5, 0.0, 1.0); }
    hit = true;
    if ((fl & 8u) != 0u) { ao = mix(ao, orm.r, w); }
    if ((fl & 16u) != 0u) { rough = mix(rough, orm.g, w); }
    if ((fl & 32u) != 0u) { metal = mix(metal, orm.b, w); }
    if ((fl & 4u) != 0u) {
      let nm = textureSampleGrad(ta_nrm, s_clamp, uv, i32(es.w + 0.5), gx, gy);
      let x = nm.a * 2.0 - 1.0;
      let y = nm.g * 2.0 - 1.0;
      let z = sqrt(1.0 - min(x * x + y * y, 1.0));
      let d = normalize(x * xa.xyz + y * cross(xa.xyz, ya.xyz) + z * ya.xyz);
      n = normalize(mix(n, d, w));
    }
    if ((fl & 2u) != 0u) {
      aD = mix(aD, alb.rgb, w);
      cA = cA * (1.0 - w);
    }
    if ((fl & 128u) != 0u) {
      // E += albedo' x w x intensity x mask x max(N.V, 0.001)^power x (N.V + 1) / pi (the ground's own albedo part is
      // unknown here: the decals' part aD stands for albedo')
      let mask = textureSampleGrad(ta_em, s_clamp, uv, i32(sl.y + 0.5), gx, gy).r;
      let nv = dot(n, v);
      em = em + aD * w * es.x * mask * pow(max(nv, 0.001), es.y) * (nv + 1.0) * PI_INV;
    }
    sw = sw + w;
    if (sw >= 1.0) { break; }
  }
  if (!hit) { return o; }
  rough = max(rough, 0.04);
  let s1 = light_scene(vec3<f32>(0.0), n, v, wp, rough, metal, ao);
  let d1 = light_scene(vec3<f32>(1.0), n, v, wp, rough, metal, ao) - s1;
  let sg = light_scene(vec3<f32>(0.0), ng, v, wp, rg, 0.0, 1.0);
  let dg = light_scene(vec3<f32>(1.0), ng, v, wp, rg, 0.0, 1.0) - sg;
  o.k = cA * d1 / max(dg, vec3<f32>(1e-6));
  var sc = vec3<f32>(1.0);
  if (u.inv.x > 0.5) { sc = u.inv.yzw / max(dg, vec3<f32>(1e-6)); }
  var add = (aD * d1 + s1 - o.k * sg) * sc * u.lit.z + em * u.ops.z;
  if (u.inv.x > 0.5) {
    let pl = textureSampleLevel(t3, s_clamp, clip.xy / u.vp.xy, 0.0).rgb;
    add = add + pl * (o.k - vec3<f32>(1.0));
  }
  o.add = add;
  o.ok = true;
  return o;
}

@fragment
fn fs_decals_mul(i: DPos) -> @location(0) vec4<f32> {
  let o = decals_eval(i.clip);
  if (!o.ok || u.inv.x > 0.5) { discard; }
  return vec4<f32>(o.k, 1.0);
}

@fragment
fn fs_decals_add(i: DPos) -> @location(0) vec4<f32> {
  let o = decals_eval(i.clip);
  if (!o.ok) { discard; }
  return vec4<f32>(o.add, 0.0);
}
