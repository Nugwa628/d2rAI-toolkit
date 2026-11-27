// Headless-Chrome check of the hdfx web editor on the MOCK API (hdkit/view/fxmock.py: three effect graphs, curves, scripts):
// drives the page with real mouse/keyboard events and writes screenshots.
//   node tests/fx_web_browser.mjs <base url> <out folder> [chrome binary]
// Prints one JSON line {ok, checks:[{name, ok, detail}], errors:[...], shots:[...]}; exit 1 when a check fails.
import {launch} from "./fx_cdp.mjs";
const [base, outDir, chromeBin] = process.argv.slice(2);
const {send, ev, waitFor, idle, shot, setValue, click, mouse, canvasPoint, check, sleep, finish, errors, join} = await launch(base, outDir, chromeBin);
let failed = null;
try {
  await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
  await send("Emulation.setDeviceMetricsOverride", {width: 1560, height: 1040, deviceScaleFactor: 1, mobile: false});
  await send("Page.navigate", {url: base + "fx.html"});
  await waitFor(`document.body.dataset.ready`, "page ready", 15000);
  check("page ready", await ev(`document.body.dataset.ready === "1"`), await ev(`document.getElementById("msg").textContent`));
  await idle();

  // browser + tree
  check("effect list", await ev(`document.querySelectorAll("#results div").length >= 3`));
  check("layer tree", await ev(`["layer:Embers", "layer:Glow", "layer:Sparks", "renderer:0", "sampler:50", "attr:MonsterFade", "script:12"].every(id => document.querySelector('#tree [data-id="' + id + '"]'))`));
  await waitFor(`document.getElementById("pv").naturalWidth > 0`, "first preview");
  check("preview image", await ev(`document.getElementById("pv").naturalWidth > 100`));
  await shot("01-open");

  // inspector: a float property with typed widgets, coalesced drag = one version
  await click('#tree [data-id="renderer:0"]');
  check("inspector widgets", await ev(`document.querySelectorAll("#insp .prop").length >= 8 && !!document.querySelector('#insp input[type=color]') && !!document.querySelector('#insp input[type=range]') && !!document.querySelector('#insp .sw')`));
  const opSel = `#insp .prop:has(span[title^="Opacity"]) input[type=number]`;
  for (const v of [0.7, 0.55, 0.3]) await setValue(opSel, v);
  await idle();
  const s1 = await ev(`fxEditor.Qu.session`);
  check("edit makes a session, drag coalesces", s1 && s1.version === 1 && s1.versions === 2, JSON.stringify(s1));
  check("model follows the edit", await ev(`fxEditor.S.model.renderers[0].props.Opacity.value === 0.3`));
  check("undo enabled", await ev(`!document.getElementById("undoBtn").disabled && document.getElementById("redoBtn").disabled`));
  await click("#undoBtn"); await idle();
  check("undo restores", await ev(`fxEditor.S.model.renderers[0].props.Opacity.value === 0.85 && !document.getElementById("redoBtn").disabled`));
  await click("#redoBtn"); await idle();
  check("redo reapplies", await ev(`fxEditor.S.model.renderers[0].props.Opacity.value === 0.3`));
  await ev(`document.querySelector('#insp .sw input').click()`); await idle();      // enable switch of renderer 0
  check("enable switch edits", await ev(`fxEditor.S.model.renderers[0].enabled === false && document.querySelector('#tree [data-id="renderer:0"]').classList.contains("off")`));
  await ev(`fxEditor.undo()`); await idle();
  await shot("02-inspector");

  // curve editor: drag a key with the mouse, add a key by double-click, delete it
  await click('#tree [data-id="sampler:50"]'); await idle();
  check("curve tab opens", await ev(`document.getElementById("pane-curve").classList.contains("on") && document.getElementById("curveSel").value === "50"`));
  check("samplers of one name on two layers are told apart", await ev(`document.querySelectorAll("#curveSel option").length === 4 && [...document.querySelectorAll("#curveSel option")].filter(o => o.textContent.includes("SizeOverLife")).length === 2`));
  const before = await ev(`JSON.stringify(fxEditor.S.model.samplers[0].data.values)`);
  const [kx, ky] = await canvasPoint(`fxEditor.curveEd.curve.times[1]`, `fxEditor.curveEd.curve.values[1][0]`);
  await mouse("mousePressed", kx, ky); await mouse("mouseMoved", kx + 20, ky - 25); await mouse("mouseMoved", kx + 40, ky - 40); await mouse("mouseReleased", kx + 40, ky - 40);
  await idle();
  const after = await ev(`JSON.stringify(fxEditor.S.model.samplers[0].data.values)`);
  check("dragging a key edits the curve", before !== after, before + " -> " + after);
  check("curve drag left one history version", await ev(`fxEditor.Qu.session.version === 2`), await ev(`fxEditor.Qu.session.version`));
  check("key table shows the selected key", await ev(`document.querySelectorAll("#keyTable input").length >= 4`), await ev(`"sel=" + fxEditor.curveEd.sel + " inputs=" + document.querySelectorAll("#keyTable input").length`));
  const n0 = await ev(`fxEditor.curveEd.curve.times.length`);
  const [ax, ay] = await canvasPoint(`(fxEditor.curveEd.curve.times[2] + fxEditor.curveEd.curve.times[3]) / 2`, `0.4`);
  await send("Input.dispatchMouseEvent", {type: "mousePressed", x: ax, y: ay, button: "left", buttons: 1, clickCount: 2});
  await send("Input.dispatchMouseEvent", {type: "mouseReleased", x: ax, y: ay, button: "left", clickCount: 2});
  await idle();
  check("double-click adds a key", await ev(`fxEditor.S.model.samplers[0].data.times.length === ${n0 + 1}`), await ev(`fxEditor.S.model.samplers[0].data.times.length`));
  await click("#keyDel"); await idle();
  check("delete removes the key", await ev(`fxEditor.S.model.samplers[0].data.times.length === ${n0}`));
  await setValue("#curveSel", "51"); await ev(`document.getElementById("curveSel").dispatchEvent(new Event("change"))`);
  check("multi-dim curve", await ev(`fxEditor.curveEd.curve.dim === 3 && document.querySelectorAll("#dimChips .dimchip").length === 3`));
  await shot("03-curve");

  // layer colour constants and the hue shift
  await click('#tree [data-id="layer:Embers"]');
  check("layer colour widgets", await ev(`document.querySelectorAll("#insp input[type=color]").length === 2`));
  const cv0 = await ev(`fxEditor.Qu.session.version`);
  await setValue('#insp .prop:has(span[title="width 3"]) input[type=color]', "#00ff00"); await idle();
  await setValue('#insp .prop:has(span[title="width 3"]) input[type=color]', "#0000ff"); await idle();
  check("colour pick edits the constant (one version)", (await ev(`fxEditor.Qu.session.version`)) === cv0 + 1 && (await ev(`JSON.stringify(fxEditor.S.model.layers[0].colors[0].value)`)).startsWith("[0,0,"), await ev(`JSON.stringify(fxEditor.S.model.layers[0].colors[0].value)`));
  await ev(`[...document.querySelectorAll("#insp button")].find(b => b.textContent === "Apply").click()`); await idle();
  check("hue shift applies", (await ev(`fxEditor.Qu.session.version`)) === cv0 + 2, await ev(`document.getElementById("msg").textContent`));
  await ev(`fxEditor.undo()`); await idle(); await ev(`fxEditor.undo()`); await idle();
  check("undo back to the first colours", await ev(`JSON.stringify(fxEditor.S.model.layers[0].colors[0].value) === "[1,1.25,1]"`), await ev(`JSON.stringify(fxEditor.S.model.layers[0].colors[0].value)`));
  await shot("03b-layer-colours");

  // script: read, constants, bad text is refused with its line, good text applies
  await click('#tree [data-id="script:12"]'); await idle();
  await waitFor(`document.getElementById("script").value.includes(".const")`, "script text");
  check("script listing + constants table", await ev(`document.getElementById("consts").querySelectorAll("input").length >= 8`));
  await setValue("#consts table tr:first-child td:nth-child(2) input", 0.9); await idle();
  check("constant edit applies", (await ev(`fxEditor.Qu.session.version`)) >= 3 && (await ev(`fxEditor.Qu.session.version`)) <= 4, await ev(`fxEditor.Qu.session.version`));
  const text = await ev(`document.getElementById("script").value`);
  await ev(`document.getElementById("script").value = ${JSON.stringify(text.replace("mul r1", "frobnicate r1"))}`);
  await click("#scriptValidate"); await waitFor(`document.getElementById("scriptDiag").textContent.length > 0`, "script diagnostics");
  check("validate shows the bad line", await ev(`/line \\d+/.test(document.getElementById("scriptDiag").textContent) && document.querySelector("#scriptDiag .diag.error") !== null`), await ev(`document.getElementById("scriptDiag").textContent`));
  await ev(`document.getElementById("script").value = ${JSON.stringify(text)}`);
  await click("#scriptValidate"); await waitFor(`document.getElementById("scriptDiag").textContent.includes("valid")`, "valid script");
  check("validate accepts the good text", await ev(`document.getElementById("scriptDiag").textContent.includes("valid")`), await ev(`document.getElementById("scriptDiag").textContent`));
  await shot("04-script");

  // timeline / seed / context
  await setValue("#time", 1.5); await idle(); await sleep(300);
  check("scrub sets t", await ev(`fxEditor.S.t === 1.5 && document.getElementById("timeV").textContent.startsWith("1.50")`));
  await setValue("#seed", 7); await sleep(300);
  check("seed", await ev(`fxEditor.S.seed === 7`));
  await setValue("#ctxKind", "bone"); await ev(`document.getElementById("ctxKind").dispatchEvent(new Event("change"))`);
  check("context picker fields", await ev(`!!document.getElementById("cx-unit") && !!document.getElementById("cx-bone")`));
  await click("#play"); await waitFor(`fxEditor.S.t !== 1.5`, "play to advance t", 4000); await click("#play");
  check("play advances t", await ev(`fxEditor.S.t !== 1.5 && !fxEditor.S.playing`), await ev(`fxEditor.S.t`));
  await click("#gifBtn"); await waitFor(`document.getElementById("msg").textContent.startsWith("clip rendered")`, "clip", 40000);
  check("clip render", await ev(`document.getElementById("pv").src.includes("gif")`), await ev(`document.getElementById("pv").src`));

  // compose: copy a layer from another effect, then remove it, then history
  await setValue("#cmpSrc", "hd/vfx/particles/mock/vfx_mock_smoke.particles"); await click("#cmpLoad");
  await waitFor(`document.querySelectorAll("#cmpLayers div").length >= 1`, "compose layers");
  await ev(`document.querySelector("#cmpLayers div").click()`); await setValue("#cmpAs", "Plume2"); await click("#cmpCopy"); await idle();
  check("copy layer", await ev(`fxEditor.S.model.layers.some(l => l.name === "Plume2") && !!document.querySelector('#tree [data-id="layer:Plume2"]')`));
  await click('#tree [data-id="layer:Plume2"]'); await click("#rmLayer"); await idle();
  check("remove layer", await ev(`!fxEditor.S.model.layers.some(l => l.name === "Plume2")`));
  await click('#tabs button[data-tab="hist"]'); await sleep(300);
  check("history lists versions", await ev(`document.querySelectorAll("#histOut .diag").length >= 5`), await ev(`document.getElementById("histOut").textContent`));
  await click('#tabs button[data-tab="check"]'); await click("#valBtn"); await sleep(300);
  check("validate effect", await ev(`document.getElementById("valOut").textContent.length > 0`));
  await shot("05-history");
  await click('#tabs button[data-tab="curve"]');

  // a refused edit shows its reason and changes nothing
  const v0 = await ev(`fxEditor.Qu.session.version`);
  await ev(`fxEditor.edit({op: "prop", renderer: 0, name: "NoSuchProperty", value: 1})`); await idle();
  check("refused edit is reported", await ev(`document.getElementById("msg").className === "bad"`), await ev(`document.getElementById("msg").textContent`));
  check("refused edit changes nothing", (await ev(`fxEditor.Qu.session.version`)) === v0);

  // keyboard undo
  await send("Input.dispatchKeyEvent", {type: "keyDown", key: "z", code: "KeyZ", modifiers: 2, windowsVirtualKeyCode: 90});
  await send("Input.dispatchKeyEvent", {type: "keyUp", key: "z", code: "KeyZ", modifiers: 2, windowsVirtualKeyCode: 90}); await idle();
  check("Ctrl+Z undoes", await ev(`fxEditor.Qu.session.version === ${v0 - 1}`), await ev(`fxEditor.Qu.session.version`));

  // export into a mod folder (below the out folder when the page is told)
  const modDir = join(outDir, "mod"); await setValue("#modDir", modDir); await click("#exportBtn"); await waitFor(`document.getElementById("msg").textContent.startsWith("exported")`, "export");
  check("export", await ev(`document.getElementById("msg").className === "ok"`), await ev(`document.getElementById("msg").textContent`));
  await click('#tree [data-id="renderer:2"]'); await ev(`fxEditor.showTab("curve")`); await sleep(300);
  await shot("06-final");
  await send("Emulation.setDeviceMetricsOverride", {width: 900, height: 1500, deviceScaleFactor: 1, mobile: false}); await sleep(300);
  check("narrow layout: one column, no horizontal scroll", await ev(`document.documentElement.scrollWidth <= window.innerWidth + 2`), await ev(`document.documentElement.scrollWidth + " vs " + window.innerWidth`));
  await shot("07-narrow");
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (e) { failed = e; check("run", false, e.stack || e.message); await shot("zz-failure").catch(() => {}); }
finally { /* the driver closes Chrome in finish() */ }
await finish(failed);
