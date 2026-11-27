// hdfx editor, pure logic (no DOM, no network): cubic Hermite curves and key edits, edit-op builders (docs/hdfx.md
// section 1), the undo stack, property widget typing, the layer tree and colour helpers. Run in the browser by fx.js and
// under node by tests/fx_web_logic.test.mjs.

// ---- curves: {times:[t..], values:[[v per dim]..], tangents_in:[[..]..], tangents_out:[[..]..], dim, interp} -----------
// Tangents are slopes (dv/dt). A segment between key a and b (h = tb - ta) is the cubic Hermite with end tangents
// out_a * h and in_b * h; interp "linear" ignores tangents, "step"/"constant" holds the left key.
export function curveDim(c) { return c.dim || (c.values && c.values[0] ? c.values[0].length : 1); }

export function normaliseCurve(c) {
  const dim = curveDim(c);
  const row = v => Array.from({length: dim}, (_, d) => (Array.isArray(v) ? v[d] : v) ?? 0);
  const n = (c.times || []).length;
  return {
    times: [...(c.times || [])], dim, interp: c.interp || "hermite",
    values: Array.from({length: n}, (_, i) => row((c.values || [])[i])),
    tangents_in: Array.from({length: n}, (_, i) => row((c.tangents_in || [])[i])),
    tangents_out: Array.from({length: n}, (_, i) => row((c.tangents_out || [])[i])),
  };
}

function segment(c, i, t, d) {
  const t0 = c.times[i], t1 = c.times[i + 1], h = t1 - t0, v0 = c.values[i][d], v1 = c.values[i + 1][d];
  if (c.interp === "step" || c.interp === "constant") return v0;
  const u = h > 0 ? (t - t0) / h : 0;
  if (c.interp === "linear") return v0 + (v1 - v0) * u;
  const m0 = c.tangents_out[i][d] * h, m1 = c.tangents_in[i + 1][d] * h, u2 = u * u, u3 = u2 * u;
  return (2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * m1;
}

export function evalCurve(c, t) {
  const n = c.times.length, dim = curveDim(c);
  if (!n) return new Array(dim).fill(0);
  if (t <= c.times[0]) return [...c.values[0]];
  if (t >= c.times[n - 1]) return [...c.values[n - 1]];
  let i = 0;
  while (i < n - 2 && t >= c.times[i + 1]) i++;
  return Array.from({length: dim}, (_, d) => segment(c, i, t, d));
}

export function curveRange(c, d = null) {
  let lo = Infinity, hi = -Infinity;
  const dims = d === null ? [...Array(curveDim(c)).keys()] : [d];
  const t0 = c.times[0] ?? 0, t1 = c.times[c.times.length - 1] ?? 1;
  for (let s = 0; s <= 64; s++) {
    const v = evalCurve(c, t0 + (t1 - t0) * s / 64);
    for (const k of dims) { lo = Math.min(lo, v[k]); hi = Math.max(hi, v[k]); }
  }
  for (const r of c.values) for (const k of dims) { lo = Math.min(lo, r[k]); hi = Math.max(hi, r[k]); }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-6) { lo -= 0.5; hi += 0.5; }
  return [lo, hi];
}

// Catmull-Rom style slopes (end keys one-sided); keeps overshoot out of monotone data with the Fritsch-Carlson clamp.
export function autoTangents(c, keys = null) {
  const out = normaliseCurve(c), n = out.times.length, dim = out.dim;
  for (let i = 0; i < n; i++) {
    if (keys && !keys.includes(i)) continue;
    for (let d = 0; d < dim; d++) {
      const a = Math.max(i - 1, 0), b = Math.min(i + 1, n - 1);
      let s = b === a ? 0 : (out.values[b][d] - out.values[a][d]) / (out.times[b] - out.times[a]);
      if (i > 0 && i < n - 1) {
        const l = (out.values[i][d] - out.values[i - 1][d]) / (out.times[i] - out.times[i - 1]);
        const r = (out.values[i + 1][d] - out.values[i][d]) / (out.times[i + 1] - out.times[i]);
        if (l * r <= 0) s = 0;
        else s = Math.sign(s) * Math.min(Math.abs(s), 3 * Math.min(Math.abs(l), Math.abs(r)));
      }
      out.tangents_in[i][d] = out.tangents_out[i][d] = s;
    }
  }
  return out;
}

export function addKey(c, time) {
  const out = normaliseCurve(c);
  const v = evalCurve(out, time);
  let i = out.times.findIndex(t => t > time);
  if (i < 0) i = out.times.length;
  if (out.times.some(t => Math.abs(t - time) < 1e-6)) return {curve: out, index: out.times.findIndex(t => Math.abs(t - time) < 1e-6)};
  const eps = 1e-3, slope = v.map((_, d) => (evalCurve(out, time + eps)[d] - evalCurve(out, time - eps)[d]) / (2 * eps));
  out.times.splice(i, 0, time);
  out.values.splice(i, 0, v);
  out.tangents_in.splice(i, 0, slope);
  out.tangents_out.splice(i, 0, [...slope]);
  return {curve: out, index: i};
}

export function removeKey(c, i) {
  if (c.times.length <= 1) return normaliseCurve(c);
  const out = normaliseCurve(c);
  for (const k of ["times", "values", "tangents_in", "tangents_out"]) out[k].splice(i, 1);
  return out;
}

// Move key i to (time, value) in dimension d (value null = leave all values); time stays between its neighbours.
export function moveKey(c, i, time, d = null, value = null) {
  const out = normaliseCurve(c), n = out.times.length;
  const lo = i > 0 ? out.times[i - 1] + 1e-4 : -Infinity, hi = i < n - 1 ? out.times[i + 1] - 1e-4 : Infinity;
  out.times[i] = Math.min(Math.max(time, lo), hi);
  if (d !== null && value !== null) out.values[i][d] = value;
  return out;
}

// Handle position for key i, dim d: ("in"|"out") at horizontal distance `len` (time units) along the tangent slope.
export function handlePos(c, i, d, which, len) {
  const s = (which === "in" ? c.tangents_in : c.tangents_out)[i][d], dir = which === "in" ? -1 : 1;
  return [c.times[i] + dir * len, c.values[i][d] + dir * len * s];
}

// Set a tangent from a dragged handle position; `mirror` keeps the other side equal (smooth key).
export function setTangentFromHandle(c, i, d, which, ht, hv, mirror = true) {
  const out = normaliseCurve(c);
  const dt = which === "in" ? out.times[i] - ht : ht - out.times[i];
  if (dt < 1e-6) return out;
  const s = (which === "in" ? out.values[i][d] - hv : hv - out.values[i][d]) / dt;
  (which === "in" ? out.tangents_in : out.tangents_out)[i][d] = s;
  if (mirror) (which === "in" ? out.tangents_out : out.tangents_in)[i][d] = s;
  return out;
}

// ---- edit ops (docs/hdfx.md section 1, payloads in docs/hdfx-mcp.md fx_edit) -------------------------------------------------
export const op = {
  prop: (renderer, name, value) => ({op: "prop", renderer, name, value}),
  texture: (renderer, name, path) => ({op: "texture", renderer, name, path}),
  enable: (renderer, on) => ({op: "enable", renderer, on: !!on}),
  tint: (stream, rgba) => ({op: "color", stream, rgba}),              // multiplies every script's float4 stream
  layerColor: (layer, index, value) => ({op: "color", layer, index, value}),         // a layer's colour constant (Layer.colors)
  colorShift: (layer, shift) => ({op: "color", layer, shift}),                         // shift {hue (deg), sat, val (multipliers)}
  curve: (sampler, c) => ({op: "curve", sampler, times: c.times, values: c.values,
                           tangents_in: c.tangents_in, tangents_out: c.tangents_out}),
  constant: (script, index, values, kind = "f") => ({op: "const", script, index, values, kind}),
  script: (script, text, language = "auto") => ({op: "script", script, text, language}),
  copyLayer: (from, layer, as) => as ? {op: "copy_layer", from, layer, as} : {op: "copy_layer", from, layer},
  removeLayer: layer => ({op: "remove_layer", layer}),
};

// What an op overwrites: a following op with the same key replaces it in the history when pushed with coalesce (a drag).
export function opKey(o) {
  switch (o.op) {
    case "prop": case "texture": return [o.op, o.renderer, o.name].join("|");
    case "enable": return "enable|" + o.renderer;
    case "color": return o.stream ? "color|" + o.stream : o.shift ? "colorshift|" + o.layer : ["color", o.layer, o.index].join("|");
    case "curve": return "curve|" + o.sampler;
    case "const": return ["const", o.script, o.index].join("|");
    case "script": return "script|" + o.script;
    default: return null;
  }
}

// Edits go to the server's edit session (fx_edit / fx_session, docs/hdfx-mcp.md "Sessions"): one history version per edit,
// undo/redo = fx_session. Calls run one at a time. A coalescing edit (same key as the edit just made, nothing since) first
// undoes that version and then edits, so a drag leaves one version, not hundreds; a queued edit with the same key that has
// not started yet is replaced by the newer one. `call(tool, args)` returns the tool's result or throws an Error.
export class EditQueue {
  constructor(call, onChange = () => {}) {
    this.call = call; this.onChange = onChange; this.src = null; this.session = null;
    this.lastKey = null; this.lastVersion = -1; this.tasks = []; this.running = false; this.error = null;
  }
  // out: the session folder the first edit creates (default: the server's own per-effect folder, shared with MCP clients)
  reset(src, session = null, out = null) { this.src = src; this.session = session; this.out = out; this.lastKey = null; this.lastVersion = -1; this.tasks = []; }
  get target() { return this.session ? this.session.out : this.src; }
  get canUndo() { return !!(this.session && this.session.can_undo); }
  get canRedo() { return !!(this.session && this.session.can_redo); }
  get dirty() { return !!(this.session && this.session.version > 0); }
  _enqueue(task) {
    return new Promise((resolve, reject) => {
      task.resolvers = [{resolve, reject}];
      const tail = this.tasks[this.tasks.length - 1];
      if (task.kind === "edit" && task.coalesce && tail && tail.kind === "edit" && tail.opKey && tail.opKey === task.opKey) {
        tail.resolvers.push(...task.resolvers); tail.ops = task.ops; return;   // the newest op wins
      }
      this.tasks.push(task); if (!this.running) this._run();
    });
  }
  edit(o, coalesce = false) { return this._enqueue({kind: "edit", ops: [o], opKey: opKey(o), coalesce}); }
  undo() { return this._enqueue({kind: "session", action: "undo"}); }
  redo() { return this._enqueue({kind: "session", action: "redo"}); }
  goto(v) { return this._enqueue({kind: "session", action: "goto", version: v}); }
  async _run() {
    this.running = true;
    while (this.tasks.length) {
      const t = this.tasks.shift();
      try {
        const r = t.kind === "edit" ? await this._edit(t) : await this._session(t);
        t.resolvers.forEach(x => x.resolve(r));
      } catch (e) { this.error = e; t.resolvers.forEach(x => x.reject(e)); }
      if (!this.tasks.length) this.onChange(this.session);
    }
    this.running = false;
  }
  async _session(t) {
    const args = {out: this.session.out, action: t.action};
    if (t.version != null) args.version = t.version;
    const r = await this.call("fx_session", args);
    this.session = r.session; this.lastKey = null; return r;
  }
  async _edit(t) {
    let undone = false;
    const again = t.coalesce && t.opKey && this.session && t.opKey === this.lastKey && this.session.version === this.lastVersion && this.session.can_undo;
    if (again) { this.session = (await this.call("fx_session", {out: this.session.out, action: "undo"})).session; undone = true; }
    try {
      const args = {path: this.target, ops: t.ops};
      if (!this.session && this.out) args.out = this.out;
      const r = await this.call("fx_edit", args);
      this.session = r.session; this.lastKey = t.opKey; this.lastVersion = r.session.version; return r;
    } catch (e) {
      if (undone) { try { this.session = (await this.call("fx_session", {out: this.session.out, action: "redo"})).session; } catch { /* leave it */ } }
      throw e;
    }
  }
}

// ---- constants of a CBVM listing (`.const <k> f|i v0..v7`; `x <hex>` ones are left to the listing) ----------------------------------
export function parseConsts(text) {
  const out = [];
  for (const line of String(text).split("\n")) {
    const m = /^\.const\s+(\d+)\s+([fix])\s+(.*?)\s*$/.exec(line.trim());
    if (!m || m[2] === "x") continue;
    out.push({index: +m[1], kind: m[2], values: m[3].split(/\s+/).map(Number)});
  }
  return out;
}

// ---- property widgets --------------------------------------------------------------------------------------------------
// prop = {type, value, file}. type is what fx_inspect says: bool|int|float|texture|mesh|? (the graph adds vec/colour types).
// Returns {kind, ...} to pick the editor; "raw" = shown, not editable (no known type).
export function widgetFor(name, prop) {
  const t = String(prop.type || "").toLowerCase(), v = prop.value, n = Array.isArray(v) ? v.length : 1;
  if (/tex|mesh|image|sampler2d|path/.test(t) || (prop.file != null && v === undefined)) return {kind: "texture"};
  if (/bool|feature/.test(t) || typeof v === "boolean") return {kind: "bool"};
  if (/colou?r|rgba?/.test(t) || (/colou?r|tint/i.test(name) && Array.isArray(v) && (n === 3 || n === 4))) return {kind: "color", n};
  if (/enum|choice/.test(t) || (prop.options && prop.options.length)) return {kind: "enum", options: prop.options || []};
  if (Array.isArray(v)) return {kind: "vec", n};
  if (t === "?" || t === "" || typeof v === "string") return {kind: "raw"};
  if (/int|uint|u32|i32/.test(t) && !/float/.test(t)) return {kind: "int", step: 1};
  return {kind: "float", step: stepFor(v)};
}
function stepFor(v) { const a = Math.abs(Number(v) || 0); return a >= 100 ? 1 : a >= 10 ? 0.1 : a >= 1 ? 0.01 : 0.001; }

export function parseNumber(text, kind = "float") {
  const x = Number(String(text).trim().replace(",", "."));
  if (!isFinite(x) || String(text).trim() === "") return null;
  return kind === "int" ? Math.round(x) : x;
}

// ---- colours: float rgba (0..1, may exceed 1 for HDR) <-> picker hex + intensity --------------------------------------
const clamp01 = x => Math.min(1, Math.max(0, x));
export function rgbaToPicker(rgba) {
  const peak = Math.max(1, rgba[0], rgba[1], rgba[2]);
  const hex = "#" + [0, 1, 2].map(i => Math.round(clamp01(rgba[i] / peak) * 255).toString(16).padStart(2, "0")).join("");
  return {hex, intensity: peak, alpha: rgba.length > 3 ? rgba[3] : 1};
}
export function pickerToRgba(hex, intensity, alpha, n = 4) {
  const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255 * intensity);
  const r = c.map(x => Math.round(x * 1e5) / 1e5);
  return n === 3 ? r : [...r, alpha];
}

// ---- the editor's model of an effect: fx_inspect's answer, with or without the effect graph -----------------------------------------
// fx_inspect gives renderers (index, kind, material, enabled, props{name:{type,value,file}}) and scripts (1-based `script` id) for
// every effect, and `graph` (docs/hdfx.md section 1: layers, samplers, attributes, entry; object numbers 0-based) once lane
// format has landed. Without a graph the model has one pseudo layer holding all renderers (`flat: true`), no samplers.
const normProp = p => ({...p, file: p.file ?? p.string ?? null});
export function toModel(res) {
  const g = res.graph || null, scripts = {};
  for (const s of res.scripts || []) scripts[s.script] = {id: s.script, kind: s.kind, backend: s.backend, instructions: s.instructions, constants: s.constants,
                                                          externals: s.externals || [], calls: s.engine_calls || s.calls || []};
  const flat = (res.renderers || []).map(r => ({...r, props: Object.fromEntries(Object.entries(r.props || {}).map(([k, v]) => [k, normProp(v)]))}));
  let layers, samplers = [], attributes = [], entry = [];
  if (g) {
    for (const [k, sr] of Object.entries(g.scripts || {})) if (scripts[k]) Object.assign(scripts[k], {role: sr.kind, layer: sr.layer});
    layers = (g.layers || []).map(l => ({...l, renderers: (l.renderers || []).map(r => ({...r, props: Object.fromEntries(Object.entries(r.props || {}).map(([k, v]) => [k, normProp(v)]))}))}));
    samplers = g.samplers || []; attributes = g.attributes || []; entry = g.entry || [];
  } else layers = [{obj: -1, name: "(all renderers)", flat: true, spawn_script: null, evolve_scripts: [], renderers: flat, children: [], life: null, spawn: null}];
  return {hasGraph: !!g, path: res.path, file: res.file, version: res.version, objects: res.objects, classes: res.classes || {}, layers, samplers, attributes, entry,
          scripts, renderers: flat, textures: res.textures || [], engine: res.engine || {}, session: res.session || null};
}
export const scriptId = obj => obj;         // a graph script's `obj` is the id fx_script_read and the script/const ops use (listing "object N")

// Tree nodes {id, kind, label, children, enabled?}; ids: "layer:<name>", "renderer:<index>", "sampler:<name>", "script:<id>",
// "attr:<name>", "field:<layer>:<name>".
export function layerTree(m) {
  const sc = (id, role) => ({id: "script:" + id, kind: "script", label: `${role} script #${id}${m.scripts[id] ? ` (${m.scripts[id].instructions} ins)` : ""}`});
  const layers = (m.layers || []).map(l => {
    const kids = [];
    if (l.spawn_script != null) kids.push(sc(scriptId(l.spawn_script), "spawn"));
    (l.evolve_scripts || []).forEach(o => kids.push(sc(scriptId(o), "evolve")));
    (l.renderers || []).forEach(r => kids.push({id: "renderer:" + r.index, kind: "renderer", enabled: r.enabled !== false,
      label: `${r.kind || "renderer"} #${r.index}${r.material ? " " + shortName(r.material) : ""}`}));
    if ((l.fields || []).length) kids.push({id: "fields:" + l.name, kind: "fields", label: `fields (${l.fields.length})`,
      children: l.fields.map(f => ({id: `field:${l.name}:${f.name}`, kind: "field", label: `${f.name} : ${f.type}${f.size ? "x" + f.size : ""}`}))});
    (l.children || []).forEach(c => kids.push({id: "child:" + l.name + ":" + c, kind: "child", label: "triggers " + c}));
    return {id: "layer:" + l.name, kind: "layer", label: l.name, children: kids};
  });
  const samplers = (m.samplers || []).map(s => ({id: "sampler:" + s.obj, kind: "sampler", label: samplerLabel(s)}));
  const attrs = (m.attributes || []).map(a => ({id: "attr:" + a.name, kind: "attr", label: `${a.name} : ${a.type}`}));
  const scripts = Object.values(m.scripts).map(s => ({id: "script:" + s.id, kind: "script", label: `#${s.id} ${s.role || ""} ${s.instructions} ins, ${s.externals.length} ext${s.layer ? " (" + s.layer + ")" : ""}`}));
  return [{id: "layers", kind: "group", label: `layers (${m.hasGraph ? layers.length : "no graph yet"})`, children: layers},
          {id: "samplers", kind: "group", label: `samplers (${samplers.length})`, children: samplers},
          {id: "attrs", kind: "group", label: `attributes (${attrs.length})`, children: attrs},
          {id: "scripts", kind: "group", label: `scripts (${scripts.length})`, children: scripts}];
}
function shortName(p) { return String(p).split(/[\\/]/).pop().replace(/\.[a-z]+$/i, ""); }

export function findRenderer(m, index) {
  for (const l of m.layers || []) for (const r of l.renderers || []) if (String(r.index) === String(index)) return {layer: l, renderer: r};
  return null;
}
// Sampler names repeat across layers ("__sampler_0" in every layer): the object number is the key (and the `curve` op's `sampler`).
export const samplerLabel = s => `${s.layer ? s.layer + "/" : ""}${s.name || s.label || s.kind} #${s.obj}${s.kind === "curve" ? ` (curve x${curveDim(s.data)})` : ` (${s.kind})`}`;
export function findSampler(m, obj) { return (m.samplers || []).find(s => String(s.obj) === String(obj)) || null; }
export function findLayer(m, name) { return (m.layers || []).find(l => l.name === name) || null; }

// Longest known life/duration among layers (seconds) for the timeline's range; falls back to 5 s.
export function effectDuration(m) {
  let d = 0;
  for (const l of m.layers || []) {
    const life = Array.isArray(l.life) ? Math.max(...l.life) : Number(l.life) || 0;
    const sp = l.spawn && Number(l.spawn.duration) || 0;
    d = Math.max(d, life + sp);
  }
  return d > 0 ? Math.min(Math.max(d, 1), 60) : 5;
}

// Diagnostics as a flat list [{level, message, line, where}] from fx_validate ({errors:[{check, message, where}], warnings}) or a
// thrown tool error (message may name "line N").
export function diagnostics(res) {
  if (!res) return [];
  const one = (e, level) => typeof e === "string" ? {level, message: e, line: lineOf(e)} :
    {level, message: (e.check ? e.check + ": " : "") + (e.message || e.msg || JSON.stringify(e)), line: e.line ?? lineOf(e.message || ""), where: e.where || null};
  return [...(res.errors || []).map(e => one(e, "error")), ...(res.warnings || []).map(e => one(e, "warn"))];
}
export function lineOf(text) { const m = /\bline (\d+)/i.exec(String(text)); return m ? +m[1] : null; }
export function errorDiag(e) { return [{level: "error", message: e.message || String(e), line: lineOf(e.message || ""), where: null}]; }

// ---- request coalescing: one run at a time, calls made meanwhile collapse into one follow-up run with the newest args ------
// run() resolves when a run that started after this call finished (or rejects with its error); call() is fire and forget.
export class Latest {
  constructor(fn) { this.fn = fn; this.running = false; this.pending = null; this.waiters = []; }
  run(...args) {
    return new Promise((resolve, reject) => {
      this.waiters.push({resolve, reject}); this.pending = args;
      if (!this.running) this._loop();
    });
  }
  call(...args) { return this.run(...args).catch(e => { this.onError ? this.onError(e) : console.error(e); }); }
  async _loop() {
    this.running = true;
    while (this.pending) {
      const a = this.pending, ws = this.waiters; this.pending = null; this.waiters = [];
      try { await this.fn(...a); ws.forEach(w => w.resolve()); } catch (e) { ws.forEach(w => w.reject(e)); }
    }
    this.running = false;
  }
}

export const fmt = (x, n = 4) => (Math.abs(x) >= 1000 || x === 0 ? String(Math.round(x * 100) / 100) : String(+x.toPrecision(n)));
