// hdfx effect editor page. Talks only to the server's JSON endpoints (POST api/fx/<tool>, docs/hdfx-mcp.md "JSON endpoints"),
// i.e. the same functions as the MCP tools (hdkit/fx/api.py). Frames are server-rendered PNG/GIF (fx_render); a live WebGPU
// canvas can replace `Preview.show` later. Edits go to the effect's edit session (fx_edit / fx_session: one history version
// per edit, undo/redo = fx_session), queued and coalesced by fxlogic.js's EditQueue. Pure logic and its tests: fxlogic.js.
import * as L from "./fxlogic.js";
import {CurveEditor, DIM_COLORS} from "./fxcurve.js";

const $ = id => document.getElementById(id);
const Q = new URLSearchParams(location.search);
const CH = n => String.fromCharCode(n);
const TRI_OPEN = CH(0x25be), TRI_SHUT = CH(0x25b8), DOT = CH(0xb7), SQUARE = CH(0x25a0);
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, ""); else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
function say(s, kind = "") { const m = $("msg"); m.textContent = s; m.className = kind; m.title = s; }

// ---- API ---------------------------------------------------------------------------------------------------------------
async function api(tool, args = {}) {
  const r = await fetch("api/fx/" + tool, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(args)});
  let j; try { j = await r.json(); } catch { throw new Error(tool + ": HTTP " + r.status); }
  if (!j.ok) { const e = new Error(j.error?.message || tool + " failed"); e.code = j.error?.code; e.detail = j.error; throw e; }
  return j;
}
const fileUrl = f => "api/fx/file?path=" + encodeURIComponent(f);

// ---- state -------------------------------------------------------------------------------------------------------------
const S = {
  tools: {}, outRoot: null, src: null, model: null, sel: null, t: 0, seed: 0, playing: false, duration: 5, rebuild: false,
  curveObj: null, scriptId: null, compose: null, composeLayer: null, composePath: null, sid: Math.random().toString(36).slice(2, 8),
};
const Qu = new L.EditQueue(api, () => refresher.call());
const baseName = p => String(p).split(/[\\/]/).pop();

async function edit(o, {coalesce = false, rebuild = false} = {}) {
  if (!S.src) return;
  S.rebuild = S.rebuild || rebuild; paintTopButtons();
  try { await Qu.edit(o, coalesce); say("edited " + o.op, "ok"); }
  catch (e) { say(e.message, "bad"); S.rebuild = true; await refresher.call(); }
}
// the rebuild flag is set before the queue runs: the repaint after the queue drains reads it
async function undo() { S.rebuild = true; try { await Qu.undo(); say("undone"); } catch (e) { say(e.message, "bad"); } }
async function redo() { S.rebuild = true; try { await Qu.redo(); say("redone"); } catch (e) { say(e.message, "bad"); } }

// Re-read the effect from the session after edits settle, repaint, re-render.
const refresher = new L.Latest(async () => {
  if (!S.src) return;
  const rb = S.rebuild; S.rebuild = false;
  S.model = L.toModel(await api("fx_inspect", {path: Qu.target}));
  if (S.model.session) Qu.session = S.model.session;
  S.duration = L.effectDuration(S.model); $("time").max = S.duration;
  paintTree(); paintInfo(); paintTopButtons(); paintHistory(); paintStreams();
  if (rb) { paintInspector(); paintCurveSel(); paintScriptSel(); if (S.scriptId != null && $("pane-script").classList.contains("on")) readScript(); }
  if ($("live").checked) Preview.render();
});
refresher.onError = e => say(e.message, "bad");

// ---- effect browser / open ----------------------------------------------------------------------------------------------
async function search() {
  const args = {pattern: $("search").value.trim() || null, info: true, limit: 300};
  if ($("kindSel").value) args.kind = $("kindSel").value;
  if ($("texSearch").value.trim()) args.texture = $("texSearch").value.trim();
  try {
    const res = await api("fx_list", args), items = res.items || [];
    const box = $("results"); box.replaceChildren();
    for (const it of items) {
      const kinds = it.kinds ? Object.entries(it.kinds).map(([k, n]) => k[0] + n).join(" ") : "";
      box.append(el("div", {title: it.path + (kinds ? "\n" + kinds : ""), class: it.path === S.src ? "sel" : "", text: it.path.replace(/^hd\/vfx\/particles\//, "") + (kinds ? "  [" + kinds + "]" : ""), onclick: () => openEffect(it.path)}));
    }
    $("resInfo").textContent = `${res.count} effect(s)` + (res.truncated ? `, first ${items.length} shown` : "");
    $("recent").replaceChildren(...items.slice(0, 200).map(i => el("option", {value: i.path})));
  } catch (e) { say(e.message, "bad"); }
}

async function openEffect(path) {
  say("opening " + path + "...");
  try {
    const res = await api("fx_inspect", {path});
    S.src = path; S.model = L.toModel(res); S.opened = (S.opened || 0) + 1;
    const stem = baseName(path).split(".")[0].replace(/[^A-Za-z0-9_-]/g, "_");
    Qu.reset(path, res.session || null, S.outRoot ? `${S.outRoot}/edit/web-${S.sid}-${S.opened}-${stem}` : null);
    S.duration = L.effectDuration(S.model); $("time").max = S.duration; S.t = 0; $("seed").value = S.seed = 0;
    $("openPath").value = path; S.sel = null; S.scriptId = null; S.compose = null;
    const first = (S.model.samplers || []).find(s => s.kind === "curve"); S.curveObj = first ? first.obj : null;
    paintTree(); paintInfo(); paintTopButtons(); paintHistory(); paintStreams(); paintInspector(); paintCurveSel(); paintScriptSel(); paintTimeLabel();
    document.title = "hdfx: " + baseName(path);
    say("opened " + baseName(path), "ok");
    Preview.render();
  } catch (e) { say(e.message, "bad"); }
}

// ---- layers tree ----------------------------------------------------------------------------------------------------------
const collapsed = new Set();
function paintTree() {
  const host = $("tree"); host.replaceChildren();
  if (!S.model) return;
  const walk = (n, depth) => {
    const open = !collapsed.has(n.id), kids = n.children || [];
    const row = el("div", {class: `n kind-${n.kind}${S.sel === n.id ? " sel" : ""}${n.enabled === false ? " off" : ""}`, style: `padding-left:${depth * 14 + 4}px`, "data-id": n.id,
      onclick: () => { if (kids.length && S.sel === n.id) { open ? collapsed.add(n.id) : collapsed.delete(n.id); } select(n.id); }},
      el("span", {class: "k", text: kids.length ? (open ? TRI_OPEN : TRI_SHUT) : DOT}), n.label);
    host.append(row);
    if (open) kids.forEach(k => walk(k, depth + 1));
  };
  L.layerTree(S.model).forEach(n => walk(n, 0));
}

function select(id) {
  S.sel = id; paintTree(); paintInspector();
  const [kind, ...rest] = id.split(":"), a = rest.join(":");
  if (kind === "sampler") { const s = L.findSampler(S.model, a); if (s && s.kind === "curve") { S.curveObj = s.obj; paintCurveSel(); showTab("curve"); } }
  if (kind === "script") { S.scriptId = +a; paintScriptSel(); readScript(); showTab("script"); }
  $("rmLayer").disabled = !(kind === "layer" && S.model.hasGraph);
}

function paintStreams() {
  const names = new Set(["Color"]);
  for (const sc of Object.values(S.model?.scripts || {})) for (const n of sc.externals || []) if (/^[A-Za-z_]\w*$/.test(n)) names.add(n);
  $("streams").replaceChildren(...[...names].sort().map(n => el("option", {value: n})));
}
function paintInfo() {
  const m = S.model; if (!m) return;
  const rn = m.layers.reduce((n, l) => n + (l.renderers || []).length, 0);
  $("info").textContent = [`path    ${m.path || m.file}`, `format  ${m.version ?? "?"}  objects ${m.objects ?? "?"}`,
    `layers ${m.hasGraph ? m.layers.length : "n/a (no graph yet)"}  renderers ${rn}  samplers ${m.samplers.length}  attributes ${m.attributes.length}`,
    `scripts ${Object.keys(m.scripts).length}  textures ${m.textures.length}  duration ~${S.duration.toFixed(2)} s`,
    `engine  ` + Object.entries(m.engine).map(([k, v]) => (v ? "+" : "-") + k).join(" ")].join("\n");
}

// ---- inspector ----------------------------------------------------------------------------------------------------------------
function row(k, v) { return el("div", {class: "prop"}, el("span", {text: k}), el("div", {class: "w", text: v})); }
function paintInspector() {
  const host = $("insp"), m = S.model; host.replaceChildren();
  if (!m || !S.sel) { host.textContent = "select a layer, renderer or sampler"; $("inspTitle").textContent = "Inspector"; return; }
  const [kind, ...rest] = S.sel.split(":"), name = rest.join(":");
  $("inspTitle").textContent = `Inspector: ${kind}`;
  if (kind === "renderer") return paintRenderer(host, name);
  if (kind === "layer") {
    const l = L.findLayer(m, name); if (!l) return;
    host.append(row("name", l.name), row("object", "#" + l.obj), row("life", JSON.stringify(l.life ?? null)), row("spawn", JSON.stringify(l.spawn ?? null)),
      row("renderers", String((l.renderers || []).length)), row("children", (l.children || []).join(", ") || "-"));
    if (m.hasGraph) {
      host.append(el("div", {class: "small", text: (l.colors || []).length ? "colour constants (op color: layer, index, value)" : "no colour constants"}));
      for (const c of l.colors || []) host.append(colorConstant(l, c));
      if ((l.colors || []).length) host.append(shiftRow(l));
      host.append(el("div", {class: "row"}, el("button", {text: "Remove layer", onclick: () => $("rmLayer").click()})));
    }
    return;
  }
  if (kind === "sampler") {
    const s = L.findSampler(m, name); if (!s) return;
    host.append(row("name", s.name), row("kind", s.kind));
    if (s.kind === "curve") host.append(el("button", {text: "Edit in curve editor", onclick: () => { S.curveObj = s.obj; paintCurveSel(); showTab("curve"); }}));
    else host.append(el("pre", {text: JSON.stringify(s.data, null, 1)}));
    return;
  }
  if (kind === "attr") {
    const a = m.attributes.find(x => x.name === name); if (a) host.append(...["name", "type", "default", "min", "max"].map(k => row(k, JSON.stringify(a[k] ?? null))));
    return;
  }
  if (kind === "script") {
    const sc = m.scripts[name] || {}; host.append(row("script id", "#" + name), row("backend", sc.backend || "?"), row("instructions", String(sc.instructions ?? "?")),
      row("constants", String(sc.constants ?? "?")), row("externals", (sc.externals || []).join(", ") || "-"), row("calls", (sc.calls || []).join(", ") || "-"));
    return;
  }
  host.textContent = S.sel;
}

// One colour constant of a layer: width 3/4 = colour picker + HDR multiplier (+ alpha), other widths = number fields.
function colorConstant(l, c) {
  const box = el("div", {class: "w"}), wrap = el("div", {class: "prop"}, el("span", {text: `colour ${c.index}`, title: `width ${c.width}`}), box);
  const push = v => { c.value = v; edit(L.op.layerColor(l.name, c.index, v), {coalesce: true}); };
  if (c.width === 3 || c.width === 4) {
    const pk = L.rgbaToPicker(c.value), pick = el("input", {type: "color", value: pk.hex});
    const k = num(pk.intensity, 0.1, () => upd(), 52), a = c.width === 4 ? num(pk.alpha, 0.01, () => upd(), 52) : null;
    const upd = () => push(L.pickerToRgba(pick.value, L.parseNumber(k.value) ?? 1, a ? (L.parseNumber(a.value) ?? 1) : 1, c.width));
    pick.addEventListener("input", upd);
    box.append(pick, el("span", {class: "small", text: "x"}), k, a ? el("span", {class: "small", text: "a"}) : null, a);
  } else c.value.forEach((x, i) => box.append(num(x, "any", y => { const v = [...c.value]; v[i] = y; push(v); }, 54)));
  return wrap;
}
function shiftRow(l) {
  const h = num(0, 1, () => {}, 54), sa = num(1, 0.05, () => {}, 54), va = num(1, 0.05, () => {}, 54);
  return el("div", {class: "prop"}, el("span", {text: "shift all", title: "hue (degrees), saturation and value multipliers of every colour of the layer"}),
    el("div", {class: "w"}, "h", h, "s", sa, "v", va, el("button", {text: "Apply", onclick: () => edit(L.op.colorShift(l.name, {hue: +h.value || 0, sat: +sa.value || 1, val: +va.value || 1}), {rebuild: true})})));
}

function paintRenderer(host, index) {
  const hit = L.findRenderer(S.model, index); if (!hit) { host.textContent = "renderer gone"; return; }
  const r = hit.renderer, sel = +r.index;
  host.append(el("div", {class: "prop"}, el("span", {text: "enabled"}), el("div", {class: "w"}, toggle(r.enabled !== false, on => edit(L.op.enable(sel, on), {rebuild: true})), el("span", {class: "small", text: `${r.kind} #${r.index}`}))));
  host.append(row("material", r.material || "-"));
  const props = Object.entries(r.props || {});
  if (!props.length) return host.append(el("div", {class: "small", text: "no properties"}));
  // many properties (a Diablo_Uber renderer has ~100): grouped by the part before the dot, with a filter
  const filter = el("input", {type: "text", id: "propFilter", placeholder: "filter " + props.length + " properties...", value: S.propFilter || ""}), body = el("div");
  const draw = () => {
    S.propFilter = filter.value; body.replaceChildren();
    const q = filter.value.trim().toLowerCase(), groups = new Map();
    for (const [name, p] of props) {
      if (q && !name.toLowerCase().includes(q)) continue;
      const g = name.includes(".") ? name.split(".")[0] : "switches";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push([name, p]);
    }
    let k = 0;
    for (const [g, list] of groups) {
      const d = el("details", {class: "grp"}, el("summary", {text: `${g} (${list.length})`}));
      if (q || list.length <= 8 || k++ < 2) d.open = true;
      list.forEach(([name, p]) => d.append(propWidget(sel, name, p)));
      body.append(d);
    }
    if (!groups.size) body.append(el("div", {class: "small", text: "no property matches"}));
  };
  filter.addEventListener("input", draw); draw();
  host.append(el("div", {class: "row"}, filter), body);
}

function toggle(on, fn) {
  const i = el("input", {type: "checkbox"}); i.checked = on; i.addEventListener("change", () => fn(i.checked));
  return el("label", {class: "sw"}, i, el("i"));
}
function num(value, step, fn, w = 62) {
  const i = el("input", {type: "number", step: step ?? "any", style: `width:${w}px`, placeholder: "-"}); i.value = value == null ? "" : L.fmt(value, 6);
  i.addEventListener("input", () => { const x = L.parseNumber(i.value); if (x !== null) fn(x); });
  return i;
}
function propWidget(sel, name, p) {
  const w = L.widgetFor(name, p), box = el("div", {class: "w"}), wrap = el("div", {class: "prop"}, el("span", {text: name, title: name + " (" + p.type + ")"}), box);
  const setProp = (v, co = true) => edit(L.op.prop(sel, name, v), {coalesce: co});
  switch (w.kind) {
    case "bool": box.append(toggle(!!p.value, on => setProp(on, false))); break;
    case "int": case "float": {
      if (p.value == null) {          // not stored in the file: the material's default applies, and fx_edit changes stored values only
        box.append(el("span", {class: "small", text: "default (not stored)", title: "this property has no stored value: the material's default applies; the API edits stored values only"}));
        break;
      }
      const v0 = p.value;
      const rng = el("input", {type: "range", min: Math.min(0, v0 * 2), max: Math.max(1, v0 * 2 || 1), step: w.step, value: v0});
      const n = num(p.value, w.step, x => { p.value = x; rng.value = x; setProp(x); });
      rng.addEventListener("input", () => { n.value = rng.value; const x = L.parseNumber(rng.value, w.kind); p.value = x; setProp(x); });
      box.append(n, rng); break;
    }
    case "vec": {
      const v = [...p.value];
      v.forEach((x, i) => box.append(num(x, "any", y => { v[i] = y; p.value = [...v]; setProp([...v]); }, 54)));
      break;
    }
    case "color": {
      const pk = L.rgbaToPicker(p.value), c = el("input", {type: "color", value: pk.hex});
      const inten = num(pk.intensity, 0.1, () => upd(), 52), a = w.n === 4 ? num(pk.alpha, 0.01, () => upd(), 52) : null;
      const upd = () => { const v = L.pickerToRgba(c.value, L.parseNumber(inten.value) ?? 1, a ? (L.parseNumber(a.value) ?? 1) : 1, w.n); p.value = v; setProp(v); };
      c.addEventListener("input", upd);
      box.append(c, el("span", {class: "small", text: "x"}), inten, a ? el("span", {class: "small", text: "a"}) : null, a);
      break;
    }
    case "texture": {
      const t = el("input", {type: "text", list: "recent-tex", value: p.file ?? ""});
      t.addEventListener("change", () => { p.file = t.value; edit(L.op.texture(sel, name, t.value)); });
      box.append(t); break;
    }
    case "enum": {
      const s = el("select", {}, ...w.options.map(o => el("option", {value: o, text: o}))); s.value = p.value;
      s.addEventListener("change", () => setProp(s.value, false)); box.append(s); break;
    }
    default: box.append(el("span", {class: "small", text: String(p.value ?? p.file ?? "(raw)").slice(0, 40), title: String(p.value ?? "")}));
  }
  return wrap;
}

// Tint (op "color": multiply every script's float4 stream by an rgba), the one colour edit the API has today.
(function tintUi() {
  const c = $("tintColor"), k = $("tintK"), a = $("tintA"), stream = $("tintStream");
  const go = () => edit(L.op.tint(stream.value || "Color", L.pickerToRgba(c.value, +k.value || 1, +a.value, 4)), {coalesce: true});
  c.addEventListener("input", go); k.addEventListener("change", go); a.addEventListener("change", go);
})();

// ---- curve editor -------------------------------------------------------------------------------------------------------------
const curveEd = new CurveEditor($("cv"), c => {
  if (S.curveObj == null) return;
  edit(L.op.curve(S.curveObj, c), {coalesce: true}); paintKeyTable();
}, i => { $("keyDel").disabled = i < 0; paintKeyTable(); });

function paintCurveSel() {
  const sel = $("curveSel"); sel.replaceChildren();
  const curves = (S.model?.samplers || []).filter(s => s.kind === "curve");
  curves.forEach(s => sel.append(el("option", {value: s.obj, text: L.samplerLabel(s)})));
  if (!curves.length) { curveEd.setCurve(null); $("dimChips").replaceChildren(); $("keyTable").replaceChildren(); $("curveNote").textContent = S.model && !S.model.hasGraph ? "Curves come with the effect graph (lane format); this build has none yet." : "This effect has no curve samplers."; return; }
  $("curveNote").textContent = "";
  if (!curves.some(s => String(s.obj) === String(S.curveObj))) S.curveObj = curves[0].obj;
  sel.value = S.curveObj; loadCurve(true);
}
const dimName = d => (curveEd.curve.dim >= 3 && /colou?r|rgb/i.test(L.findSampler(S.model, S.curveObj)?.name || "") ? "rgba" : "xyzw")[d] ?? d;
function loadCurve(fit) {
  const s = L.findSampler(S.model, S.curveObj); if (!s) return;
  curveEd.sel = -1; curveEd.setCurve(s.data, fit);
  $("interp").value = curveEd.curve.interp; $("keyDel").disabled = true;
  const chips = $("dimChips"); chips.replaceChildren();
  for (let d = 0; d < curveEd.curve.dim; d++) {
    const c = el("span", {class: "dimchip" + (curveEd.dimOn[d] ? "" : " off"), style: `color:${DIM_COLORS[d % DIM_COLORS.length]}`, text: SQUARE + " " + dimName(d)});
    c.addEventListener("click", () => { curveEd.dimOn[d] = !curveEd.dimOn[d]; c.classList.toggle("off"); curveEd.draw(); });
    chips.append(c);
  }
  paintKeyTable();
}
function paintKeyTable() {
  const host = $("keyTable"), c = curveEd.curve, i = curveEd.sel; host.replaceChildren();
  if (!c || i < 0) return;
  const commit = n => { curveEd.curve = n; curveEd.draw(); edit(L.op.curve(S.curveObj, n), {coalesce: true}); };
  const t = el("table", {class: "k"});
  t.append(el("tr", {}, el("th", {text: `key ${i}`}), el("td", {}, num(c.times[i], "any", x => commit(L.moveKey(c, i, x)), 58)), el("th", {text: "time"})));
  for (const [label, field] of [["value", "values"], ["in", "tangents_in"], ["out", "tangents_out"]])
    t.append(el("tr", {}, el("th", {text: label}), ...c[field][i].map((_, d) => el("td", {}, num(c[field][i][d], "any", x => { const n = L.normaliseCurve(c); n[field][i][d] = x; commit(n); }, 58)))));
  host.append(t);
}
$("curveSel").addEventListener("change", e => { S.curveObj = +e.target.value; loadCurve(true); });
$("interp").addEventListener("change", e => curveEd.setInterp(e.target.value));
$("curveFit").addEventListener("click", () => { curveEd.fit(); curveEd.draw(); });
$("curveAuto").addEventListener("click", () => curveEd.autoTangents());
$("keyDel").addEventListener("click", () => curveEd.deleteKey());

// ---- script view / editor -----------------------------------------------------------------------------------------------------
function paintScriptSel() {
  const sel = $("scriptSel"); sel.replaceChildren();
  const ids = Object.keys(S.model?.scripts || {});
  ids.forEach(o => sel.append(el("option", {value: o, text: `#${o} ${S.model.scripts[o].instructions} ins`})));
  if (S.scriptId != null && ids.includes(String(S.scriptId))) sel.value = String(S.scriptId); else if (ids.length) S.scriptId = +ids[0];
}
async function readScript() {
  if (S.scriptId == null || !S.src) return;
  try {
    const r = await api("fx_script_read", {path: Qu.target, script: S.scriptId, language: $("scriptFmt").value});
    $("script").value = r.text ?? ""; $("scriptDiag").replaceChildren(); paintConsts(r.language === "cbvm" ? L.parseConsts(r.text) : []);
  } catch (e) { $("script").value = ""; paintDiag("scriptDiag", L.errorDiag(e)); $("consts").replaceChildren(); }
}
function paintConsts(consts) {
  const host = $("consts"); host.replaceChildren();
  if (!consts.length) return;
  host.append(el("div", {class: "small", text: "constants: edit a value to apply it as a const op"}));
  const t = el("table", {class: "k"});
  for (const c of consts) t.append(el("tr", {}, el("th", {text: "c" + c.index + " " + c.kind}), ...c.values.map((v, k) => el("td", {}, num(v, "any", x => {
    c.values[k] = c.kind === "i" ? Math.round(x) : x; edit(L.op.constant(S.scriptId, c.index, [...c.values], c.kind), {coalesce: true});
  }, 64)))));
  host.append(t);
}
function paintDiag(id, list, okText = "") {
  const host = $(id); host.replaceChildren();
  if (!list.length && okText) host.append(el("div", {class: "diag okk", text: okText}));
  for (const d of list) host.append(el("div", {class: "diag " + d.level, "data-line": d.line ?? false, text: `${d.level}${d.line != null ? " line " + d.line : ""}: ${d.message}`,
    onclick: () => d.line != null && jumpLine(d.line)}));
}
function jumpLine(n) {
  const ta = $("script"), lines = ta.value.split("\n"); let a = 0;
  for (let i = 0; i < Math.min(n - 1, lines.length); i++) a += lines[i].length + 1;
  ta.focus(); ta.setSelectionRange(a, a + (lines[n - 1] || "").length);
}
// Check the text without touching the edit session: write it into a scratch session of the *source* (fx_script_write, own `out`).
async function validateScript() {
  if (!S.outRoot) { paintDiag("scriptDiag", [{level: "info", message: "no scratch folder known (server did not report out_root): Apply checks the text and rolls back on an error"}]); return true; }
  try {
    const r = await api("fx_script_write", {path: S.src, script: S.scriptId, source: $("script").value, language: $("scriptFmt").value === "hfx" ? "hfx" : "cbvm",
                                            out: `${S.outRoot}/edit/web-${S.sid}-check`});
    const d = L.diagnostics(r.validate), errs = d.filter(x => x.level === "error"), warns = d.filter(x => x.level === "warn");
    // warnings are about the whole effect (files that do not resolve in the roots), not the script: first few only
    const shown = errs.length ? errs : [{level: "info", message: `script is valid (${r.instructions} instructions)`}, ...warns.slice(0, 3),
      ...(warns.length > 3 ? [{level: "info", message: `... and ${warns.length - 3} more warning(s): see the Validate tab`}] : [])];
    paintDiag("scriptDiag", shown); return !errs.length;
  } catch (e) { paintDiag("scriptDiag", L.errorDiag(e)); return false; }
}
$("scriptSel").addEventListener("change", e => { S.scriptId = +e.target.value; readScript(); });
$("scriptFmt").addEventListener("change", readScript);
$("scriptRead").addEventListener("click", readScript);
$("scriptValidate").addEventListener("click", validateScript);
$("scriptApply").addEventListener("click", async () => {
  if (S.scriptId == null) return;
  if (!(await validateScript())) return say("fix the script errors first", "bad");
  await edit(L.op.script(S.scriptId, $("script").value, $("scriptFmt").value === "hfx" ? "hfx" : "cbvm"), {rebuild: true});
  paintDiag("scriptDiag", [], "applied");
});

// ---- validate / coverage / history -------------------------------------------------------------------------------------------------
$("valBtn").addEventListener("click", async () => {
  if (!S.src) return;
  try { paintDiag("valOut", L.diagnostics(await api("fx_validate", {path: Qu.target})), "no problems found"); }
  catch (e) { paintDiag("valOut", L.errorDiag(e)); }
});
$("covBtn").addEventListener("click", async () => {
  try { $("covOut").textContent = JSON.stringify(await api("fx_coverage", {pattern: S.model.path || S.src, limit: 5}), null, 1); } catch (e) { $("covOut").textContent = e.message; }
});
async function paintHistory() {
  const host = $("histOut"), s = Qu.session; host.replaceChildren();
  if (!s) { host.append(el("div", {class: "small", text: "no edits yet (the first edit makes a working copy of the effect)"})); return; }
  host.append(el("div", {class: "small", text: `session ${s.out}  (open this folder to resume it)`}));
  try {
    const h = (await api("fx_session", {out: s.out, action: "history"})).history || [];
    for (const v of h) host.append(el("div", {class: "diag" + (v.current ? " okk" : ""), "data-line": true, text: `${v.current ? ">" : " "} v${v.version}  ${v.label}`,
      onclick: async () => { S.rebuild = true; try { await Qu.goto(v.version); } catch (e) { say(e.message, "bad"); } }}));
  } catch (e) { host.append(el("div", {class: "diag error", text: e.message})); }
}

// ---- compose ----------------------------------------------------------------------------------------------------------------------
$("cmpLoad").addEventListener("click", async () => {
  const p = $("cmpSrc").value.trim(); if (!p) return;
  try {
    S.compose = L.toModel(await api("fx_inspect", {path: p})); S.composePath = p; S.composeLayer = null; $("cmpCopy").disabled = true;
    const box = $("cmpLayers"); box.replaceChildren(...S.compose.layers.map(l => el("div", {text: `${l.name}  (${(l.renderers || []).length} renderers)`,
      onclick: ev => { S.composeLayer = l.name; $("cmpAs").value = l.name; [...box.children].forEach(c => c.classList.remove("sel")); ev.currentTarget.classList.add("sel"); $("cmpCopy").disabled = !S.src || !S.compose.hasGraph; }})));
    if (!S.compose.hasGraph) box.append(el("div", {class: "small", text: "no effect graph in this build: layers cannot be copied yet"}));
  } catch (e) { say(e.message, "bad"); }
});
$("cmpCopy").addEventListener("click", async () => {
  if (!S.composeLayer) return;
  await edit(L.op.copyLayer(S.composePath, S.composeLayer, $("cmpAs").value.trim() || null), {rebuild: true});
});
$("rmLayer").addEventListener("click", async () => {
  if (!S.sel?.startsWith("layer:")) return;
  const name = S.sel.slice(6); S.sel = null; $("rmLayer").disabled = true;
  await edit(L.op.removeLayer(name), {rebuild: true});
});

// ---- preview / timeline / context ----------------------------------------------------------------------------------------------------
function camSpec() {
  const c = {preset: $("cam").value}, num1 = id => { const v = $(id).value.trim(); return v === "" ? null : +v; };
  for (const [k, id] of [["az", "camAz"], ["el", "camEl"], ["dist", "camDist"]]) { const v = num1(id); if (v !== null) c[k] = v; }
  return c;
}
const CTX = {
  unit: [["unit", "text", "hd/character/enemy/wraith1.json"], ["clip", "text", "neutral"], ["t0", "number", 0], ["rate", "number", 0]],
  bone: [["unit", "text", "hd/character/enemy/mephisto.json"], ["bone", "text", "root"]],
  missile: [["fx", "number", -4], ["fz", "number", 0], ["tx", "number", 4], ["tz", "number", 0], ["speed", "number", 12], ["hit", "text", "hit"], ["explode", "text", "explode"]],
  skill: [["unit", "text", "hd/character/player/sorceress.json"], ["frame", "number", 12]],
  scene: [["map", "text", "act1/cathedrl/cathy3"], ["x", "number", 0], ["y", "number", 0]],
};
function ctxSpec() {
  const k = $("ctxKind").value; if (!k) return null;
  const v = id => { const e = $("cx-" + id); return e ? (e.type === "number" ? +e.value : e.value) : undefined; };
  switch (k) {
    // the effect on every bone the unit binds it to (lane r3-shapes' fxbones, ported at the r3 integration)
    case "unit": return {kind: k, unit: v("unit"), clip: v("clip") || null, t0: v("t0"), rate: v("rate")};
    case "bone": return {kind: k, unit: v("unit"), bone: v("bone")};
    case "missile": return {kind: k, path: {from: [v("fx"), 0, v("fz")], to: [v("tx"), 0, v("tz")]}, speed: v("speed"), events: {hit: v("hit"), explode: v("explode")}};
    case "skill": return {kind: k, unit: v("unit"), cast_frame: v("frame")};
    case "scene": return {kind: k, map: v("map"), xy: [v("x"), v("y")]};
  }
  return null;
}
// the game's colour variant (fx_color_variants; lane r3-colorvariant): states first, then every palette entry that tints
function attrsSpec() { const v = $("cvar")?.value; return v ? {ColorVariantIndex: +v} : null; }
async function loadVariants() {
  const sel = $("cvar"); if (!sel) return;
  try {
    const r = await api("fx_color_variants", {});
    for (const [name, idx] of Object.entries(r.states || {})) sel.append(el("option", {value: idx}, `${name} (${idx})`));
    for (const c of r.choices || []) if (c.index && !c.identity) sel.append(el("option", {value: c.index}, `${c.index} ${c.name}`));
    if (!r.table_found) sel.title += " (palette not found in the roots: add a casc root)";
  } catch (e) { sel.title += " (" + e.message + ")"; }
  sel.addEventListener("change", () => Preview.render());
}
loadVariants();
const Preview = {
  n: 0,
  busy: new L.Latest(async () => {
    if (!S.src) return;
    const t0 = performance.now(), r = await api("fx_render", {path: Qu.target, t: S.t, seed: S.seed, cam: camSpec(), context: ctxSpec(), attrs: attrsSpec(), size: $("size").value, format: "png"});
    Preview.show(r, performance.now() - t0);
  }),
  render() { return this.busy.call(); },
  // swap this for a live WebGPU canvas later; `r` is the fx_render answer ({files: [png]})
  show(r, ms) {
    const f = (r.files || [])[0];
    if (r.refused) return this.note("render refused: " + (r.why || "GPU busy"));
    if (f) { $("pv").style.display = ""; $("pv").src = fileUrl(f) + "&v=" + (++this.n); this.note(""); }
    $("pvStat").textContent = `${Math.round(ms)} ms`; $("pvinfo").textContent = `t=${S.t.toFixed(2)} s  seed ${S.seed}` + (r.stats ? "  " + Object.entries(r.stats).slice(0, 3).map(([k, v]) => k + "=" + JSON.stringify(v)).join(" ") : "");
  },
  note(s) { $("pvNote").textContent = s; },
};
Preview.busy.onError = e => {
  const ni = e.code === "not_implemented";
  Preview.note(ni ? "Preview needs the simulator and renderer lanes (" + (e.detail?.needs || "lane render") + "): " + e.message : "render: " + e.message);
  if (ni) $("pv").style.display = "none";
};
function paintCtx() {
  const host = $("ctxFields"); host.replaceChildren();
  for (const [id, type, def] of CTX[$("ctxKind").value] || []) {
    const i = el("input", {id: "cx-" + id, type, value: def, size: type === "text" ? 16 : 4, step: "any", title: id, placeholder: id});
    i.addEventListener("change", () => Preview.render()); host.append(el("label", {}, id, i));
  }
  Preview.render();
}
function paintTimeLabel() { $("timeV").textContent = S.t.toFixed(2) + " s"; $("time").value = S.t; }
function setTime(t) { S.t = Math.min(Math.max(0, t), S.duration); paintTimeLabel(); Preview.render(); }
async function playLoop() {
  let last = performance.now();
  while (S.playing) {
    const now = performance.now(), dt = (now - last) / 1000 * +$("speed").value; last = now;
    S.t += dt; if (S.t > S.duration) S.t = 0;
    paintTimeLabel(); await Preview.busy.run().catch(() => {}); await new Promise(r => setTimeout(r, 15));
  }
}
$("play").addEventListener("click", () => { S.playing = !S.playing; $("play").textContent = S.playing ? "Pause" : "Play"; $("play").classList.toggle("on", S.playing); if (S.playing) playLoop(); });
$("time").addEventListener("input", e => setTime(+e.target.value));
$("stepBack").addEventListener("click", () => setTime(S.t - 1 / 30));
$("stepFwd").addEventListener("click", () => setTime(S.t + 1 / 30));
$("seed").addEventListener("change", e => { S.seed = Math.max(0, Math.floor(+e.target.value) || 0); Preview.render(); });
for (const id of ["size", "cam", "camAz", "camEl", "camDist"]) $(id).addEventListener("change", () => Preview.render());
$("ctxKind").addEventListener("change", paintCtx);
$("gifBtn").addEventListener("click", async () => {
  if (!S.src) return; say("rendering clip...");
  try {
    const r = await api("fx_render", {path: Qu.target, clip: {t0: 0, t1: Math.min(S.duration, 6), fps: 12}, seed: S.seed, cam: camSpec(), context: ctxSpec(), attrs: attrsSpec(), size: $("size").value, format: "gif"});
    if (r.files?.[0]) { $("pv").style.display = ""; $("pv").src = fileUrl(r.files[0]) + "&v=" + (++Preview.n); } say("clip rendered (" + r.frames + " frames)", "ok");
  } catch (e) { say(e.message, "bad"); }
});

// ---- top bar: export / undo ------------------------------------------------------------------------------------------------
function paintTopButtons() {
  $("undoBtn").disabled = !Qu.canUndo; $("redoBtn").disabled = !Qu.canRedo; $("exportBtn").disabled = !S.src;
  $("dirty").textContent = Qu.session ? `v${Qu.session.version}/${Qu.session.versions - 1}` + (Qu.dirty ? " edited" : "") : "";
}
$("undoBtn").addEventListener("click", undo); $("redoBtn").addEventListener("click", redo);
$("openBtn").addEventListener("click", () => $("openPath").value.trim() && openEffect($("openPath").value.trim()));
$("openPath").addEventListener("keydown", e => e.key === "Enter" && $("openBtn").click());
$("searchBtn").addEventListener("click", search);
for (const id of ["search", "texSearch"]) $(id).addEventListener("keydown", e => e.key === "Enter" && search());
$("kindSel").addEventListener("change", search);
$("exportBtn").addEventListener("click", async () => {
  const mod_dir = $("modDir").value.trim(); if (!mod_dir) return say("enter the mod folder (the one that holds data/) to export to", "bad");
  try {
    const r = await api("fx_export", {path: Qu.target, mod_dir});
    const w = (r.written || [])[0];
    say(`exported ${w ? w.logical : ""}${r.missing && r.missing.length ? " (missing files: " + r.missing.length + ")" : ""}`, r.missing && r.missing.length ? "bad" : "ok");
  } catch (e) { say(e.message, "bad"); }
});
$("newBtn").addEventListener("click", async () => {
  const template = prompt("effect to start from (path), empty = scaffold", S.src || ""); if (template === null) return;
  try { const r = await api("fx_create", template ? {template} : {}); await openEffect(r.session.out); } catch (e) { say(e.message, "bad"); }
});

// ---- tabs / keys -----------------------------------------------------------------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".pane").forEach(p => p.classList.toggle("on", p.id === "pane-" + name));
  if (name === "curve") curveEd.draw();
}
document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
addEventListener("keydown", e => {
  const tag = (e.target.tagName || "").toLowerCase(), typing = ["input", "textarea", "select"].includes(tag);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
  else if (e.key === " " && !typing) { e.preventDefault(); $("play").click(); }
});

// ---- start --------------------------------------------------------------------------------------------------------------------------------
(async function start() {
  try {
    const info = await (await fetch("api/fx/tools")).json();
    if (!info.tools) throw new Error("the server has no hdfx endpoints: start it with  python -m hdkit.view serve-web --fx  (or python -m hdkit.view.fxmock)");
    S.tools = info.tools; S.outRoot = info.out_root || null;
    const mock = !!(info.engine && info.engine.mock);
    $("badge").textContent = mock ? "mock API" : "fx API"; $("badge").className = mock ? "mock" : "";
    $("badge").title = mock ? "answers come from hdkit/view/fxmock.py (a stand-in with the same contract), not the game's files" : "";
    await search();
    const open = Q.get("open") || info.default_effect || "";
    if (open) await openEffect(open);
    if (Q.get("sel")) select(Q.get("sel"));
    if (Q.get("t")) setTime(+Q.get("t"));
    if (Q.get("tab")) showTab(Q.get("tab"));
    if (Q.get("ctx")) { $("ctxKind").value = Q.get("ctx"); paintCtx(); }
    document.body.dataset.ready = "1";
  } catch (e) { say("start: " + e.message, "bad"); document.body.dataset.ready = "error"; }
})();
window.fxEditor = {S, Qu, edit, undo, redo, select, openEffect, api, curveEd, showTab, refresher};   // for the headless checks
