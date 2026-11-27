// Headless-Chrome check of the hdfx web editor on the REAL tools: `python -m hdkit.view.webserve --fx --fx-roots d2r=<data folder>`
// (hdkit/fx/api.py behind /api/fx/*), a game effect (Mephisto's ambient smoke by default). What the build offers today is checked
// (list, inspect, property / texture / enable / tint / const / script edits, script validate, history, undo, export); features that need
// a lane that has not landed (effect graph, simulator, renderer) must say so in the page instead of failing.
//   node tests/fx_web_real.mjs <base url> <out folder> [effect path] [chrome binary]
import {mkdirSync} from "node:fs";
import {launch} from "./fx_cdp.mjs";
const [base, outDir, effect = "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles", chromeBin] = process.argv.slice(2);
const {ev, waitFor, idle, shot, setValue, click, mouse, canvasPoint, check, sleep, finish, errors, join} = await launch(base, outDir, chromeBin);
const TEX = "data/hd/vfx/textures/noise/T_FX_gaussian_noise_01.texture";
const q = x => JSON.stringify(x);

let failed = null;
try {
  await ev(`location.href = ${JSON.stringify(base + "fx.html?open=" + encodeURIComponent(effect))}`);
  await waitFor(`document.body.dataset.ready`, "page ready", 20000);
  await idle();
  check("page ready on the real API", await ev(`document.body.dataset.ready === "1" && document.getElementById("badge").textContent === "fx API"`), await ev(`document.getElementById("msg").textContent`));
  check("effects listed", await ev(`document.querySelectorAll("#results div").length >= 1`));
  const n = await ev(`fxEditor.S.model.renderers.length`);
  const hasGraph = await ev(`fxEditor.S.model.hasGraph`);
  check("model: renderers, and layers from the graph when the format lane is present", n >= 1 && (hasGraph ? await ev(`fxEditor.S.model.layers.length >= 1 && fxEditor.S.model.layers[0].flat !== true`) : await ev(`fxEditor.S.model.layers.length === 1 && fxEditor.S.model.layers[0].flat === true`)), `renderers ${n}, graph ${hasGraph}`);
  check("tree: renderers and scripts", await ev(`!!document.querySelector('#tree [data-id="renderer:0"]') && document.querySelectorAll('#tree [data-id^="script:"]').length >= 1`));
  check("info shows the engine seams", await ev(`document.getElementById("info").textContent.includes(${q(hasGraph ? "+graph" : "-graph")})`));
  const simOn = await ev(`fxEditor.S.model.engine.sim && fxEditor.S.model.engine.render`);
  if (!simOn) {
    await waitFor(`document.getElementById("pvNote").textContent.length > 0`, "preview note");
    check("preview says which lane it needs", await ev(`/lane|simulator|render/i.test(document.getElementById("pvNote").textContent)`), await ev(`document.getElementById("pvNote").textContent`));
  }
  if (!hasGraph) check("curve tab explains the missing graph", await ev(`document.getElementById("curveNote").textContent.includes("graph")`));
  await shot("real-01-open");

  // effect graph (lane format): layers, curve samplers, layer colours, compose, remove
  if (hasGraph) {
    check("layer tree from the graph", await ev(`document.querySelectorAll('#tree [data-id^="layer:"]').length === fxEditor.S.model.layers.length`), await ev(`fxEditor.S.model.layers.length`));
    check("samplers listed by object number", await ev(`fxEditor.S.model.samplers.every(s => document.querySelector('#tree [data-id="sampler:' + s.obj + '"]'))`));
    const cur = await ev(`(fxEditor.S.model.samplers.find(s => s.kind === "curve") || {}).obj ?? null`);
    if (cur !== null) {
      await click(`#tree [data-id="sampler:${cur}"]`); await idle();
      check("curve editor shows the curve", await ev(`fxEditor.curveEd.curve && fxEditor.curveEd.curve.times.length >= 1 && document.getElementById("curveSel").value === ${q(String(cur))}`));
      const k0 = await ev(`JSON.stringify(fxEditor.S.model.samplers.find(s => s.obj === ${cur}).data.values)`), v0 = await ev(`fxEditor.Qu.session ? fxEditor.Qu.session.version : 0`);
      const [kx, ky] = await canvasPoint(`fxEditor.curveEd.curve.times[0]`, `fxEditor.curveEd.curve.values[0][0]`);
      await mouse("mousePressed", kx, ky); await mouse("mouseMoved", kx + 3, ky - 30); await mouse("mouseMoved", kx + 4, ky - 50); await mouse("mouseReleased", kx + 4, ky - 50); await idle();
      const k1 = await ev(`JSON.stringify(fxEditor.S.model.samplers.find(s => s.obj === ${cur}).data.values)`), v1 = await ev(`fxEditor.Qu.session.version`);
      check("dragging a key edits the real curve (one version)", k0 !== k1 && v1 === v0 + 1, `${v0} -> ${v1} ${await ev(`document.getElementById("msg").textContent`)}`);
      await shot("real-02a-curve");
      await ev(`fxEditor.undo()`); await idle();
      check("undo restores the curve", (await ev(`JSON.stringify(fxEditor.S.model.samplers.find(s => s.obj === ${cur}).data.values)`)) === k0);
    }
    const lc = await ev(`(() => { const l = fxEditor.S.model.layers.find(l => (l.colors || []).length); return l ? {name: l.name, n: l.colors.length, w: l.colors[0].width} : null; })()`);
    if (lc) {
      await click(`#tree [data-id="layer:${lc.name}"]`);
      check("layer colour widgets", await ev(`document.querySelectorAll("#insp .prop span[title^='width']").length === ${lc.n}`), JSON.stringify(lc));
      if (lc.w === 3 || lc.w === 4) {
        const c0 = await ev(`fxEditor.Qu.session ? fxEditor.Qu.session.version : 0`);
        await setValue(`#insp .prop:has(span[title="width ${lc.w}"]) input[type=color]`, "#3060ff"); await idle();
        check("layer colour edit (real color op)", (await ev(`fxEditor.Qu.session.version`)) === c0 + 1, await ev(`document.getElementById("msg").textContent`));
        await ev(`fxEditor.undo()`); await idle();
      }
    }
    const ln = await ev(`fxEditor.S.model.layers.length`);
    await setValue("#cmpSrc", effect); await click("#cmpLoad"); await waitFor(`document.querySelectorAll("#cmpLayers div").length >= 1`, "compose layers");
    await ev(`document.querySelector("#cmpLayers div").click()`); await setValue("#cmpAs", "WebCopy"); await click("#cmpCopy"); await idle();
    const copied = await ev(`fxEditor.S.model.layers.length === ${ln + 1} && fxEditor.S.model.layers.some(l => l.name === "WebCopy")`), cmsg = await ev(`document.getElementById("msg").className + ": " + document.getElementById("msg").textContent`);
    // a game effect copies its layer; a tiny synthetic file may be refused by the format lane: then the reason must be shown and nothing changed
    check("copy layer (real copy_layer): done, or refused with the reason shown", copied || (cmsg.startsWith("bad") && (await ev(`fxEditor.S.model.layers.length`)) === ln), `${ln} layers -> ${await ev(`fxEditor.S.model.layers.length`)}: ${cmsg}`);
    if (copied) {
      await click('#tree [data-id="layer:WebCopy"]'); await click("#rmLayer"); await idle();
      check("remove layer (real remove_layer)", await ev(`fxEditor.S.model.layers.length === ${ln}`), await ev(`document.getElementById("msg").textContent`));
    }
    await shot("real-02b-graph");
  }

  // inspector: groups, a typed number property, a texture, enable
  const pick = await ev(`(() => { const e = Object.entries(fxEditor.S.model.renderers[0].props), n = e.find(([k, p]) => (p.type === "int" || p.type === "float") && p.value !== undefined), t = e.find(([k, p]) => p.type === "texture");
    return {num: n && n[0], numType: n && n[1].type, tex: t && t[0], script: Object.keys(fxEditor.S.model.scripts)[0], nprops: e.length}; })()`);
  await click('#tree [data-id="renderer:0"]');
  check("property groups", await ev(`document.querySelectorAll("#insp details.grp").length >= 1`), JSON.stringify(pick));
  if (pick.num) {
    await setValue("#propFilter", pick.num); await sleep(100);
    check("property filter", await ev(`document.querySelectorAll("#insp .prop").length >= 3`), await ev(`document.querySelectorAll("#insp .prop").length`));
    const pv0 = await ev(`fxEditor.Qu.session ? fxEditor.Qu.session.version : 0`);
    await setValue(`#insp .prop:has(span[title^=${q(pick.num + " (")}]) input[type=number]`, 2); await idle();
    const s1 = await ev(`fxEditor.Qu.session`);
    check("number property edit goes to a session of the page's own", s1 && s1.version === pv0 + 1 && s1.out.includes("/web-"), JSON.stringify(s1) + " " + await ev(`document.getElementById("msg").textContent`));
    check("inspect shows the new value", await ev(`fxEditor.S.model.renderers[0].props[${q(pick.num)}].value === 2`));
  }
  if (pick.tex) {
    await setValue("#propFilter", pick.tex); await sleep(100);
    await setValue(`#insp .prop:has(span[title^=${q(pick.tex + " (")}]) input[type=text]`, TEX); await idle();
    check("texture edit", await ev(`fxEditor.S.model.renderers[0].props[${q(pick.tex)}].file === ${q(TEX)}`), await ev(`document.getElementById("msg").textContent`));
  }
  await setValue("#propFilter", ""); await sleep(100);
  await ev(`document.querySelector('#insp .sw input').click()`); await idle();
  check("enable switch", await ev(`fxEditor.S.model.renderers[0].enabled === false`), await ev(`document.getElementById("msg").textContent`));
  await ev(`fxEditor.undo()`); await idle();
  check("undo of the switch", await ev(`fxEditor.S.model.renderers[0].enabled === true`));
  await shot("real-02-inspector");

  // tint: a multiplicative op, one version per pick (coalesced)
  const v0 = await ev(`fxEditor.Qu.session.version`);
  await setValue("#tintColor", "#40c0ff"); await idle(); await setValue("#tintColor", "#40ff80"); await idle();
  const tint = await ev(`fxEditor.Qu.session.version`);
  const tmsg = await ev(`document.getElementById("msg").textContent`);
  check("tint: one version for repeated picks, or the API's reason", tint === v0 + 1 || (tint === v0 && /no script stores/.test(tmsg)), `version ${v0} -> ${tint}: ${tmsg}`);

  // script: listing, constants, bad text refused with a line, good text valid
  await click(`#tree [data-id="script:${pick.script}"]`); await idle();
  await waitFor(`document.getElementById("script").value.length > 50`, "script text");
  check("script listing", await ev(`document.getElementById("script").value.includes(".backend")`));
  const hasConst = await ev(`/^\\.const \\d+ [fi] /m.test(document.getElementById("script").value)`), nIn = await ev(`document.querySelectorAll("#consts input").length`);
  check("constants table matches the listing", hasConst === (nIn > 0), `listing has editable constants: ${hasConst}, inputs: ${nIn}`);
  if (nIn > 0) {
    const c0 = await ev(`fxEditor.Qu.session.version`);
    await setValue("#consts table tr:first-child td:nth-child(2) input", 0.25); await idle();
    check("constant edit is one version", (await ev(`fxEditor.Qu.session.version`)) === c0 + 1, await ev(`document.getElementById("msg").textContent`));
  }
  const text = await ev(`document.getElementById("script").value`);
  const lines = text.split("\n"), at = lines.findIndex(l => /^\s*[a-z]/i.test(l) && !l.startsWith(".") && !l.startsWith(";"));
  const bad = lines.map((l, i) => i === at ? "bogus_op " + l : l).join("\n");
  await ev(`document.getElementById("script").value = ${JSON.stringify(bad)}`);
  await click("#scriptValidate"); await waitFor(`document.getElementById("scriptDiag").textContent.length > 0`, "script diagnostics");
  check("bad script is refused", await ev(`!!document.querySelector("#scriptDiag .diag.error")`), await ev(`document.getElementById("scriptDiag").textContent`));
  const ver = await ev(`fxEditor.Qu.session.version`);
  await ev(`document.getElementById("script").value = ${JSON.stringify(text)}`);
  await click("#scriptValidate"); await waitFor(`document.getElementById("scriptDiag").textContent.includes("valid")`, "valid script", 20000);
  check("unchanged script validates", await ev(`document.getElementById("scriptDiag").textContent.includes("valid")`), await ev(`document.getElementById("scriptDiag").textContent`));
  check("validate left the edit session alone", (await ev(`fxEditor.Qu.session.version`)) === ver);
  await shot("real-03-script");

  // validate, history, export
  await click('#tabs button[data-tab="check"]'); await click("#valBtn"); await waitFor(`document.getElementById("valOut").textContent.length > 0`, "validate");
  check("validate effect", await ev(`document.getElementById("valOut").textContent.length > 0`), await ev(`document.getElementById("valOut").textContent`));
  await click('#tabs button[data-tab="hist"]'); await sleep(400);
  check("history", await ev(`document.querySelectorAll("#histOut .diag").length >= 2`), await ev(`document.getElementById("histOut").textContent`));
  await ev(`fxEditor.edit({op: "prop", renderer: 0, name: "NoSuchProperty", value: 1})`); await idle();
  check("refused edit is reported", await ev(`document.getElementById("msg").className === "bad"`), await ev(`document.getElementById("msg").textContent`));
  const mod = join(outDir, "mod-real"); mkdirSync(mod, {recursive: true}); await ev(`document.getElementById("msg").textContent = ""; document.getElementById("msg").className = ""`); await setValue("#modDir", mod); await click("#exportBtn");
  await waitFor(`document.getElementById("msg").textContent.length > 0`, "export", 20000);
  check("export to a mod folder", await ev(`document.getElementById("msg").textContent.startsWith("exported")`), await ev(`document.getElementById("msg").textContent`));
  await shot("real-04-history");
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (e) { failed = e; check("run", false, e.stack || e.message); await shot("real-zz-failure").catch(() => {}); }
await finish(failed);
