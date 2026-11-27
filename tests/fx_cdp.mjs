// Shared driver of the hdfx web editor's headless-Chrome checks (fx_web_browser.mjs on the mock API, fx_web_real.mjs on the real
// tools): starts Chrome with a throw-away profile, talks DevTools over a WebSocket (node only, no packages), offers evaluate/click/
// mouse/screenshot helpers and collects page errors. Usage: `const d = await launch(base, outDir, chromeBin)`, run checks with
// d.check(name, ok, detail), then `await d.finish(failedError)` prints one JSON line {ok, checks, errors, shots} and exits.
import {spawn} from "node:child_process";
import {mkdtempSync, writeFileSync, mkdirSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

export async function launch(base, outDir, chromeBin = process.env.CHROME || "google-chrome") {
mkdirSync(outDir, {recursive: true});
const profile = mkdtempSync(join(tmpdir(), "hdfx-chrome-"));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const checks = [], errors = [], shots = [];
const check = (name, ok, detail = "") => checks.push({name, ok: !!ok, detail: String(detail).slice(0, 300)});

const chrome = spawn(chromeBin, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--disable-background-networking", "--window-size=1560,1040", "--hide-scrollbars", "about:blank"], {stdio: ["ignore", "ignore", "pipe"]});
const wsUrl = await new Promise((res, rej) => {
  let buf = ""; const t = setTimeout(() => rej(new Error("chrome did not start: " + buf.slice(-300))), 20000);
  chrome.stderr.on("data", d => { buf += d; const m = /DevTools listening on (ws:\/\/\S+)/.exec(buf); if (m) { clearTimeout(t); res(m[1]); } });
  chrome.on("exit", c => rej(new Error("chrome exited " + c + " " + buf.slice(-300))));
});
const port = new URL(wsUrl).port;
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let seq = 0; const pending = new Map();
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); }
  else if (m.method === "Runtime.exceptionThrown") errors.push("exception: " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push("console.error: " + m.params.args.map(a => a.value ?? a.description).join(" "));
  else if (m.method === "Log.entryAdded" && m.params.entry.level === "error" && !/favicon|404/.test(m.params.entry.text + m.params.entry.url)) errors.push("log: " + m.params.entry.text + " " + (m.params.entry.url || ""));
});
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, {res, rej}); ws.send(JSON.stringify({id, method, params})); });
const ev = async (expr) => {
  const r = await send("Runtime.evaluate", {expression: expr, awaitPromise: true, returnByValue: true});
  if (r.exceptionDetails) throw new Error("evaluate: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text) + " in " + expr.slice(0, 120));
  return r.result.value;
};
const waitFor = async (expr, what, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(40); } throw new Error("timeout waiting for " + what); };
const idle = () => waitFor(`!fxEditor.Qu.running && fxEditor.Qu.tasks.length === 0 && !fxEditor.refresher.running && !document.getElementById("msg").textContent.startsWith("opening")`, "edits to settle");
async function shot(name) {
  const r = await send("Page.captureScreenshot", {format: "png"});
  const f = join(outDir, name + ".png"); writeFileSync(f, Buffer.from(r.data, "base64")); shots.push(f);
}
const setValue = (sel, v) => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.value = ${JSON.stringify(String(v))}; e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); return true; })()`);
const click = sel => ev(`document.querySelector(${JSON.stringify(sel)}).click()`);
async function mouse(type, x, y, extra = {}) { await send("Input.dispatchMouseEvent", {type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, ...extra}); }
const canvasPoint = (tExpr, vExpr) => ev(`(() => { const e = fxEditor.curveEd, r = e.cv.getBoundingClientRect(); return [r.left + e.sx(${tExpr}) * r.width / e.w, r.top + e.sy(${vExpr}) * r.height / e.h]; })()`);


async function finish(failed) {
  ws.close(); chrome.kill(); await sleep(300);
  try { rmSync(profile, {recursive: true, force: true}); } catch { /* the profile is ours (a temp folder) */ }
  const ok = checks.every(c => c.ok) && !failed;
  console.log(JSON.stringify({ok, checks, errors, shots}));
  process.exit(ok ? 0 : 1);
}
return {send, ev, waitFor, idle, shot, setValue, click, mouse, canvasPoint, check, sleep, finish, errors, outDir, join};
}
