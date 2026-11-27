// Pure-JS logic of the hdfx web editor (hdkit/view/web/fxlogic.js): curves, ops, edit queue, widgets, model.
// Run: node --test tests/fx_web_logic.test.mjs   (tests/test_hdfx_web.py runs it when node is present)
import test from "node:test";
import assert from "node:assert/strict";
import * as L from "../hdkit/view/web/fxlogic.js";

const near = (a, b, e = 1e-6) => assert.ok(Math.abs(a - b) <= e, `${a} != ${b}`);
const C1 = () => ({times: [0, 1, 2], values: [[0], [1], [0]], tangents_in: [[0], [0], [0]], tangents_out: [[0], [0], [0]], dim: 1});

test("hermite: keys are hit exactly, flat tangents ease, linear and step", () => {
  const c = C1();
  near(L.evalCurve(c, 0)[0], 0); near(L.evalCurve(c, 1)[0], 1); near(L.evalCurve(c, 2)[0], 0);
  near(L.evalCurve(c, 0.5)[0], 0.5);                                  // smoothstep midpoint
  assert.ok(L.evalCurve(c, 0.25)[0] < 0.25);                          // eased in
  near(L.evalCurve(c, -3)[0], 0); near(L.evalCurve(c, 9)[0], 0);      // clamped outside
  near(L.evalCurve({...c, interp: "linear"}, 0.25)[0], 0.25);
  near(L.evalCurve({...c, interp: "step"}, 0.99)[0], 0);
  const s = {times: [0, 2], values: [[0], [2]], tangents_in: [[1], [1]], tangents_out: [[1], [1]], dim: 1};
  near(L.evalCurve(s, 1)[0], 1);                                      // slope 1 on a line of slope 1 stays the line
});

test("multi-dim curves evaluate each dimension", () => {
  const c = {times: [0, 1], values: [[0, 10, 5], [1, 20, 5]], dim: 3, interp: "linear"};
  const n = L.normaliseCurve(c);
  assert.equal(n.tangents_in[0].length, 3);
  const v = L.evalCurve(n, 0.5);
  near(v[0], 0.5); near(v[1], 15); near(v[2], 5);
});

test("addKey keeps the curve's shape and slope; removeKey keeps one key", () => {
  const c = L.autoTangents(C1());
  const {curve, index} = L.addKey(c, 0.7);
  assert.equal(index, 1); assert.equal(curve.times.length, 4);
  near(L.evalCurve(curve, 0.7)[0], L.evalCurve(c, 0.7)[0], 1e-6);
  near(L.evalCurve(curve, 0.5)[0], L.evalCurve(c, 0.5)[0], 5e-3);     // inserted key does not bend the curve
  assert.equal(L.addKey(c, 1).index, 1);                              // a key at that time exists: no duplicate
  assert.equal(L.addKey(c, 1).curve.times.length, 3);
  const one = {times: [0], values: [[1]], dim: 1};
  assert.equal(L.removeKey(one, 0).times.length, 1);
  assert.deepEqual(L.removeKey(C1(), 1).times, [0, 2]);
});

test("moveKey stays between its neighbours; value only for the moved dimension", () => {
  const c = {times: [0, 1, 2], values: [[0, 0], [1, 1], [2, 2]], dim: 2};
  const m = L.moveKey(c, 1, 5, 1, 9);
  assert.ok(m.times[1] < 2 && m.times[1] > 1.9); assert.deepEqual(m.values[1], [1, 9]);
  assert.ok(L.moveKey(c, 1, -5).times[1] > 0);
  assert.equal(L.moveKey(c, 0, -5).times[0], -5);                     // the first key may go anywhere left
});

test("autoTangents: no overshoot at extremes, slopes on a ramp", () => {
  const t = L.autoTangents(C1());
  assert.equal(t.tangents_out[1][0], 0);                              // peak: flat
  const ramp = L.autoTangents({times: [0, 1, 2, 3], values: [[0], [1], [2], [3]], dim: 1});
  for (const i of [0, 1, 2, 3]) near(ramp.tangents_out[i][0], 1);
  assert.deepEqual(ramp.tangents_in, ramp.tangents_out);
});

test("tangent handles: position and drag set the slope, mirrored or not", () => {
  const c = {times: [0, 1], values: [[0], [1]], tangents_in: [[0], [0]], tangents_out: [[0], [0]], dim: 1};
  assert.deepEqual(L.handlePos(c, 0, 0, "out", 0.5), [0.5, 0]);
  assert.deepEqual(L.handlePos(c, 1, 0, "in", 0.5), [0.5, 1]);
  const d = L.setTangentFromHandle(c, 0, 0, "out", 0.5, 1);           // 1 up over 0.5: slope 2
  near(d.tangents_out[0][0], 2); near(d.tangents_in[0][0], 2);
  const e = L.setTangentFromHandle(c, 0, 0, "out", 0.5, 1, false);
  near(e.tangents_out[0][0], 2); near(e.tangents_in[0][0], 0);
  assert.equal(L.setTangentFromHandle(c, 0, 0, "out", -1, 1).tangents_out[0][0], 0);   // a handle dragged behind its key is ignored
});

test("curveRange pads flat curves", () => {
  const [lo, hi] = L.curveRange({times: [0, 1], values: [[2], [2]], dim: 1, interp: "linear"});
  assert.ok(hi - lo > 0.5);
});

test("op builders match docs/hdfx-mcp.md fx_edit payloads", () => {
  assert.deepEqual(L.op.prop(1, "Opacity", 0.5), {op: "prop", renderer: 1, name: "Opacity", value: 0.5});
  assert.deepEqual(L.op.texture("layer:A", "Diffuse", "x.texture"), {op: "texture", renderer: "layer:A", name: "Diffuse", path: "x.texture"});
  assert.deepEqual(L.op.enable(0, 0), {op: "enable", renderer: 0, on: false});
  assert.deepEqual(L.op.tint("Color", [1, 0, 0, 1]), {op: "color", stream: "Color", rgba: [1, 0, 0, 1]});
  assert.deepEqual(L.op.constant(12, 3, [0.5]), {op: "const", script: 12, index: 3, values: [0.5], kind: "f"});
  assert.deepEqual(L.op.script(12, "x", "hfx"), {op: "script", script: 12, text: "x", language: "hfx"});
  assert.deepEqual(L.op.copyLayer("a.particles", "Glow"), {op: "copy_layer", from: "a.particles", layer: "Glow"});
  assert.deepEqual(L.op.copyLayer("a.particles", "Glow", "G2").as, "G2");
  assert.deepEqual(L.op.removeLayer("Glow"), {op: "remove_layer", layer: "Glow"});
  assert.deepEqual(L.op.layerColor("Embers", 2, [1, 0, 0]), {op: "color", layer: "Embers", index: 2, value: [1, 0, 0]});
  assert.deepEqual(L.op.colorShift("Embers", {hue: 90}), {op: "color", layer: "Embers", shift: {hue: 90}});
  assert.notEqual(L.opKey(L.op.layerColor("A", 2, [1])), L.opKey(L.op.layerColor("A", 4, [1])));
  assert.equal(L.opKey(L.op.layerColor("A", 2, [1])), L.opKey(L.op.layerColor("A", 2, [0])));
  assert.notEqual(L.opKey(L.op.tint("Color", [1])), L.opKey(L.op.colorShift("A", {})));
  const c = C1(), o = L.op.curve("Size", c);
  assert.deepEqual([o.op, o.sampler, o.times], ["curve", "Size", [0, 1, 2]]);
  assert.equal(L.opKey(L.op.prop(1, "A", 1)), L.opKey(L.op.prop(1, "A", 2)));
  assert.notEqual(L.opKey(L.op.prop(1, "A", 1)), L.opKey(L.op.prop(2, "A", 1)));
  assert.equal(L.opKey(L.op.removeLayer("x")), null);
});

// a fake of the server's session tools: one version per fx_edit, fx_session undo/redo/goto, an op named "bad" fails
function fakeServer() {
  const s = {calls: [], versions: [{label: "open", ops: []}], cursor: 0, out: "/o/edit/x_1"};
  const info = () => ({out: s.out, version: s.cursor, versions: s.versions.length, can_undo: s.cursor > 0, can_redo: s.cursor < s.versions.length - 1});
  s.call = async (tool, a) => {
    s.calls.push([tool, a]);
    await new Promise(r => setTimeout(r, 1));
    if (tool === "fx_edit") {
      if (a.ops.some(o => o.op === "bad")) throw new Error("op 0 (bad): refused");
      s.versions.splice(s.cursor + 1); s.versions.push({label: a.ops.map(o => o.op).join(","), ops: a.ops}); s.cursor = s.versions.length - 1;
      return {ok: true, session: info()};
    }
    if (tool === "fx_session") {
      const t = {undo: s.cursor - 1, redo: s.cursor + 1, goto: a.version}[a.action];
      if (t < 0 || t >= s.versions.length) throw new Error("nothing to " + a.action);
      s.cursor = t; return {ok: true, session: info()};
    }
    throw new Error("no tool " + tool);
  };
  return s;
}

test("EditQueue: the first edit targets the source, later ones the session folder", async () => {
  const sv = fakeServer(), q = new L.EditQueue(sv.call); q.reset("hd/vfx/a.particles");
  assert.equal(q.canUndo, false);
  await q.edit(L.op.prop(0, "A", 1));
  assert.equal(sv.calls[0][1].path, "hd/vfx/a.particles");
  await q.edit(L.op.prop(0, "B", 1));
  assert.equal(sv.calls[1][1].path, "/o/edit/x_1");
  assert.equal(q.session.version, 2); assert.equal(q.canUndo, true); assert.equal(q.canRedo, false);
});

test("EditQueue: a chosen session folder goes with the first edit only", async () => {
  const sv = fakeServer(), q = new L.EditQueue(sv.call); q.reset("src", null, "/o/edit/mine");
  await q.edit(L.op.enable(0, false)); await q.edit(L.op.enable(1, false));
  assert.equal(sv.calls[0][1].out, "/o/edit/mine"); assert.equal(sv.calls[1][1].out, undefined);
  q.reset("src2", {out: "/o/edit/x", version: 0, versions: 1}, "/o/edit/ignored");        // an opened session keeps its own folder
  await q.edit(L.op.enable(2, false));
  assert.equal(sv.calls[2][1].path, "/o/edit/x"); assert.equal(sv.calls[2][1].out, undefined);
});

test("EditQueue: a drag (same key, coalesce) leaves one version", async () => {
  const sv = fakeServer(), q = new L.EditQueue(sv.call); q.reset("src");
  await q.edit(L.op.prop(0, "A", 1), true);
  for (const v of [2, 3, 4]) await q.edit(L.op.prop(0, "A", v), true);
  assert.equal(sv.versions.length, 2);                                // open + one edit
  assert.equal(sv.versions[1].ops[0].value, 4);
  await q.edit(L.op.prop(0, "B", 9), true);                           // another key: a new version
  assert.equal(sv.versions.length, 3);
  await q.edit(L.op.prop(0, "B", 9), false);                          // not coalesced: a new version even for the same key
  assert.equal(sv.versions.length, 4);
});

test("EditQueue: queued same-key edits that did not start are replaced by the newest", async () => {
  const sv = fakeServer(), q = new L.EditQueue(sv.call); q.reset("src");
  const ps = [1, 2, 3, 4, 5].map(v => q.edit(L.op.prop(0, "A", v), true));
  await Promise.all(ps);
  const edits = sv.calls.filter(c => c[0] === "fx_edit");
  assert.ok(edits.length <= 3, "calls: " + edits.length);             // the first runs at once, the rest collapse
  assert.equal(sv.versions[sv.cursor].ops[0].value, 5);
  assert.equal(sv.versions.length, 2);
});

test("EditQueue: undo, redo, goto; a new edit after undo drops the redo versions", async () => {
  const sv = fakeServer(), q = new L.EditQueue(sv.call); q.reset("src");
  await q.edit(L.op.enable(0, false)); await q.edit(L.op.enable(1, false)); await q.edit(L.op.enable(2, false));
  await q.undo(); assert.equal(q.session.version, 2); assert.equal(q.canRedo, true);
  await q.redo(); assert.equal(q.session.version, 3);
  await q.goto(1); assert.equal(q.session.version, 1);
  await q.edit(L.op.enable(3, true)); assert.equal(sv.versions.length, 3); assert.equal(q.canRedo, false);
  await assert.rejects(q.redo(), /nothing to redo/);
});

test("EditQueue: a refused edit changes nothing, and a coalesced one that fails restores the version it undid", async () => {
  const sv = fakeServer(), q = new L.EditQueue(sv.call); q.reset("src");
  await q.edit(L.op.prop(0, "A", 1), true);
  await assert.rejects(q.edit({op: "bad"}), /refused/);
  assert.equal(sv.versions.length, 2); assert.equal(q.session.version, 1);
  const real = sv.call; let n = 0;
  q.call = async (tool, a) => (tool === "fx_edit" && ++n === 1 ? (() => { throw new Error("nope"); })() : real(tool, a));
  await assert.rejects(q.edit(L.op.prop(0, "A", 2), true), /nope/);   // undo ran, edit failed, redo put the old version back
  assert.equal(sv.cursor, 1); assert.equal(sv.versions[1].ops[0].value, 1);
});

test("EditQueue: onChange fires once when the queue drains", async () => {
  const sv = fakeServer(); let n = 0; const q = new L.EditQueue(sv.call, () => n++); q.reset("src");
  await Promise.all([q.edit(L.op.enable(0, false)), q.edit(L.op.enable(1, false)), q.edit(L.op.enable(2, false))]);
  await new Promise(r => setTimeout(r, 5));
  assert.equal(n, 1);
});

test("Latest: calls during a run collapse into one follow-up with the newest args; errors reach the waiters", async () => {
  const seen = []; const l = new L.Latest(async x => { seen.push(x); await new Promise(r => setTimeout(r, 3)); if (x === "bad") throw new Error("boom"); });
  await Promise.all([l.run(1), l.run(2), l.run(3), l.run(4)]);
  assert.deepEqual(seen, [1, 4]);
  await assert.rejects(l.run("bad"), /boom/);
  await l.run("ok");
  assert.equal(seen.at(-1), "ok");
});

test("parseConsts reads f and i lines, skips raw ones", () => {
  const text = "; hd\n.order 0\n.const 0 f 1.0 0.5 0.25 1.0 1.0 0.5 0.25 1.0\n.const 1 i 3 3 3 3 3 3 3 3\n.const 2 x 00ff\n\nmov r0, ext:A\n";
  const cs = L.parseConsts(text);
  assert.equal(cs.length, 2);
  assert.deepEqual([cs[0].index, cs[0].kind, cs[0].values.length, cs[0].values[1]], [0, "f", 8, 0.5]);
  assert.deepEqual([cs[1].kind, cs[1].values[0]], ["i", 3]);
});

test("widgetFor picks the editor from the inspected type", () => {
  const k = (n, p) => L.widgetFor(n, p).kind;
  assert.equal(k("A", {type: "bool", value: true}), "bool");
  assert.equal(k("A", {type: "int", value: 2}), "int");
  assert.equal(k("A", {type: "float", value: 0.5}), "float");
  assert.equal(k("A", {type: "float", value: 2}), "float");           // a whole-number float stays a float
  assert.equal(k("Diffuse", {type: "texture", file: "x"}), "texture");
  assert.equal(k("Mesh", {type: "mesh", file: "x.gr2"}), "texture");
  assert.equal(k("A", {type: "?", value: "0a0b"}), "raw");
  assert.equal(k("Transparency", {type: "feature", value: true}), "bool");
  assert.equal(k("Tint", {type: "vec4", value: [1, 1, 1, 1]}), "color");
  assert.equal(k("Offset", {type: "vec3", value: [0, 1, 2]}), "vec");
  assert.equal(k("Mode", {type: "enum", value: "a", options: ["a", "b"]}), "enum");
  assert.equal(L.parseNumber("1,5"), 1.5); assert.equal(L.parseNumber("x"), null); assert.equal(L.parseNumber("2.6", "int"), 3); assert.equal(L.parseNumber(""), null);
});

test("colour picker round trip, HDR intensity, alpha", () => {
  const p = L.rgbaToPicker([2, 1, 0.5, 0.25]);
  assert.equal(p.intensity, 2); assert.equal(p.hex, "#ff8040"); assert.equal(p.alpha, 0.25);
  const back = L.pickerToRgba(p.hex, p.intensity, p.alpha);
  near(back[0], 2, 1e-4); near(back[1], 1.00392, 1e-3); near(back[3], 0.25);
  assert.equal(L.pickerToRgba("#ffffff", 1, 1, 3).length, 3);
});

const INSPECT = {ok: true, path: "hd/vfx/a.particles", file: "/x/a.particles", version: "0x1", objects: 5,
  renderers: [{index: 0, object: 4, kind: "billboard", material: "vfx/materials/Uber.pkma", enabled: true, props: {Opacity: {type: "float", value: 1}, Diffuse: {type: "texture", file: "t.texture"}}},
              {index: 1, object: 5, kind: "ribbon", material: "", enabled: false, props: {}}],
  scripts: [{script: 12, backend: "CPU/CPU_VM", kind: 2, instructions: 7, constants: 2, externals: ["Color"], engine_calls: ["rand"]}], textures: ["t.texture"], graph: null, engine: {graph: false}};

test("toModel without a graph: one pseudo layer with every renderer, scripts by id", () => {
  const m = L.toModel(INSPECT);
  assert.equal(m.hasGraph, false); assert.equal(m.layers.length, 1); assert.equal(m.layers[0].flat, true);
  assert.equal(m.layers[0].renderers.length, 2); assert.equal(m.scripts[12].instructions, 7);
  assert.equal(L.findRenderer(m, 1).renderer.kind, "ribbon");
  const tree = L.layerTree(m);
  assert.deepEqual(tree.map(n => n.kind), ["group", "group", "group", "group"]);
  assert.deepEqual(tree[0].children[0].children.map(n => n.id), ["renderer:0", "renderer:1"]);
  assert.equal(tree[3].children[0].id, "script:12");
  assert.equal(tree[0].children[0].children[1].enabled, false);
  assert.equal(L.effectDuration(m), 5);
});

test("toModel with a graph: layers, samplers (keyed by object number), attributes; a script's graph object is its id", () => {
  const g = {layers: [{obj: 3, name: "Embers", spawn_script: 10, evolve_scripts: [11], fields: [{name: "Position", type: "float3", size: 12}],
                       renderers: [{index: 0, kind: "billboard", material: "m", enabled: true, props: {Diffuse: {type: "texture", string: "s.texture"}}}], children: ["Sparks"], life: [1, 2.5], spawn: {duration: 4}},
                      {obj: 8, name: "Sparks", spawn_script: null, evolve_scripts: [], fields: [], renderers: [], children: [], life: 1, spawn: null}],
             samplers: [{obj: 50, name: "__sampler_0", layer: "Embers", kind: "curve", data: C1()}, {obj: 51, name: "__sampler_0", layer: "Sparks", kind: "curve", data: C1()}],
             attributes: [{name: "MonsterFade", type: "float"}], entry: [3], scripts: {10: {kind: "spawn", layer: "Embers"}, 11: {kind: "evolve", layer: "Embers"}}};
  const m = L.toModel({...INSPECT, graph: g, scripts: [{script: 10, instructions: 3, constants: 0, externals: [], engine_calls: []}, {script: 11, instructions: 4, constants: 0, externals: [], engine_calls: []}]});
  assert.equal(m.hasGraph, true); assert.equal(m.layers.length, 2);
  assert.equal(m.layers[0].renderers[0].props.Diffuse.file, "s.texture");           // graph "string" -> file
  const tree = L.layerTree(m), embers = tree[0].children[0];
  assert.deepEqual(embers.children.map(n => n.id), ["script:10", "script:11", "renderer:0", "fields:Embers", "child:Embers:Sparks"]);
  assert.deepEqual(tree[1].children.map(n => n.id), ["sampler:50", "sampler:51"]);                 // same name, told apart by object number
  assert.equal(tree[1].children[1].label, "Sparks/__sampler_0 #51 (curve x1)");
  assert.equal(tree[2].children[0].id, "attr:MonsterFade"); assert.equal(m.scripts[10].role, "spawn");
  assert.equal(L.effectDuration(m), 6.5);                                           // longest life + spawn duration
  assert.equal(L.findLayer(m, "Sparks").obj, 8); assert.equal(L.findSampler(m, 51).layer, "Sparks"); assert.equal(L.findSampler(m, "50").obj, 50); assert.equal(L.findSampler(m, "nope"), null);
});

test("diagnostics: validate answers and tool errors", () => {
  const d = L.diagnostics({ok: false, errors: [{check: "renderers", message: "none enabled", where: "r0"}], warnings: [{check: "files", message: "missing"}, "plain"]});
  assert.deepEqual(d.map(x => x.level), ["error", "warn", "warn"]);
  assert.equal(d[0].message, "renderers: none enabled");
  assert.equal(L.lineOf("line 7: unknown instruction"), 7); assert.equal(L.lineOf("nothing"), null);
  assert.equal(L.errorDiag(new Error("line 3: bad"))[0].line, 3);
  assert.deepEqual(L.diagnostics(null), []);
});
