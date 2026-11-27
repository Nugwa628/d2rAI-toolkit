// hdfx editor: the curve editor canvas (Hermite keys with tangent handles, multi-dimensional). The maths is in fxlogic.js.
import * as L from "./fxlogic.js";

export const DIM_COLORS = ["#ff7a6b", "#7fd48a", "#6fb7ff", "#e0b050", "#b9a4ff", "#f0a0b8"];
const KEY_R = 5, HANDLE_R = 4, HANDLE_PX = 46, PAD = {l: 44, r: 12, t: 10, b: 20};

export class CurveEditor {
  // onChange(curve, final): called while dragging (final=false) and on release (true); onSelect(keyIndex)
  constructor(canvas, onChange, onSelect) {
    this.cv = canvas; this.ctx = canvas.getContext("2d"); this.onChange = onChange; this.onSelect = onSelect;
    this.curve = null; this.sel = -1; this.dimOn = []; this.drag = null; this.dim = 0;
    this.view = {t0: 0, t1: 1, v0: 0, v1: 1};
    canvas.addEventListener("pointerdown", e => this.down(e));
    canvas.addEventListener("pointermove", e => this.move(e));
    canvas.addEventListener("pointerup", e => this.up(e));
    canvas.addEventListener("pointercancel", e => this.up(e));
    canvas.addEventListener("dblclick", e => this.dbl(e));
    canvas.addEventListener("wheel", e => this.wheel(e), {passive: false});
    canvas.tabIndex = 0;
    canvas.addEventListener("keydown", e => { if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); this.deleteKey(); } });
  }
  setCurve(c, fit = true) {
    this.curve = c ? L.normaliseCurve(c) : null;
    if (this.curve) { this.dimOn = Array.from({length: this.curve.dim}, (_, i) => this.dimOn[i] ?? true); if (this.sel >= this.curve.times.length) this.sel = -1; }
    if (fit) this.fit();
    this.draw();
  }
  fit() {
    const c = this.curve; if (!c || !c.times.length) { this.view = {t0: 0, t1: 1, v0: 0, v1: 1}; return; }
    const [lo, hi] = L.curveRange(c), t0 = c.times[0], t1 = c.times[c.times.length - 1], dt = (t1 - t0) || 1, dv = hi - lo;
    this.view = {t0: t0 - dt * 0.06, t1: t1 + dt * 0.06, v0: lo - dv * 0.1, v1: hi + dv * 0.1};
  }
  get w() { return this.cv.width; } get h() { return this.cv.height; }
  sx(t) { const v = this.view; return PAD.l + (t - v.t0) / (v.t1 - v.t0) * (this.w - PAD.l - PAD.r); }
  sy(x) { const v = this.view; return this.h - PAD.b - (x - v.v0) / (v.v1 - v.v0) * (this.h - PAD.t - PAD.b); }
  tx(px) { const v = this.view; return v.t0 + (px - PAD.l) / (this.w - PAD.l - PAD.r) * (v.t1 - v.t0); }
  vy(py) { const v = this.view; return v.v0 + (this.h - PAD.b - py) / (this.h - PAD.t - PAD.b) * (v.v1 - v.v0); }
  pos(e) { const r = this.cv.getBoundingClientRect(); return [(e.clientX - r.left) * this.w / r.width, (e.clientY - r.top) * this.h / r.height]; }
  handleLen() { return HANDLE_PX / (this.w - PAD.l - PAD.r) * (this.view.t1 - this.view.t0); }

  hit(px, py) {
    const c = this.curve; if (!c) return null;
    if (this.sel >= 0) {
      for (let d = 0; d < c.dim; d++) {
        if (!this.dimOn[d]) continue;
        for (const which of ["in", "out"]) {
          const [ht, hv] = L.handlePos(c, this.sel, d, which, this.handleLen());
          if (Math.hypot(this.sx(ht) - px, this.sy(hv) - py) <= HANDLE_R + 3) return {type: "handle", i: this.sel, d, which};
        }
      }
    }
    let best = null;
    for (let i = 0; i < c.times.length; i++) for (let d = 0; d < c.dim; d++) {
      if (!this.dimOn[d]) continue;
      const dist = Math.hypot(this.sx(c.times[i]) - px, this.sy(c.values[i][d]) - py);
      if (dist <= KEY_R + 3 && (!best || dist < best.dist)) best = {type: "key", i, d, dist};
    }
    return best;
  }

  down(e) {
    if (!this.curve) return;
    this.cv.setPointerCapture(e.pointerId); this.cv.focus();
    const [px, py] = this.pos(e);
    if (e.shiftKey || e.button === 1) { this.drag = {type: "pan", px, py, view: {...this.view}}; return; }
    const h = this.hit(px, py);
    if (!h) { this.select(-1); this.draw(); return; }
    if (h.type === "key") { this.select(h.i); this.dim = h.d; this.drag = {type: "key", i: h.i, d: h.d}; }
    else this.drag = h;
    this.draw();
  }
  move(e) {
    if (!this.drag || !this.curve) return;
    const [px, py] = this.pos(e), d = this.drag;
    if (d.type === "pan") {
      const dt = (px - d.px) / (this.w - PAD.l - PAD.r) * (d.view.t1 - d.view.t0), dv = (py - d.py) / (this.h - PAD.t - PAD.b) * (d.view.v1 - d.view.v0);
      this.view = {t0: d.view.t0 - dt, t1: d.view.t1 - dt, v0: d.view.v0 + dv, v1: d.view.v1 + dv};
      return this.draw();
    }
    if (d.type === "key") this.curve = L.moveKey(this.curve, d.i, this.tx(px), d.d, this.vy(py));
    else this.curve = L.setTangentFromHandle(this.curve, d.i, d.d, d.which, this.tx(px), this.vy(py), !e.altKey);
    d.moved = true;
    this.draw();
    this.onChange(this.curve, false);
  }
  up(e) {
    const d = this.drag; this.drag = null;
    if (d && d.moved) this.onChange(this.curve, true);
  }
  dbl(e) {
    if (!this.curve) return;
    const [px, py] = this.pos(e);
    if (this.hit(px, py)) return;
    const {curve, index} = L.addKey(this.curve, this.tx(px));
    this.curve = curve; this.select(index); this.draw();
    this.onChange(this.curve, true);
  }
  wheel(e) {
    e.preventDefault();
    const [px, py] = this.pos(e), k = e.deltaY > 0 ? 1.15 : 1 / 1.15, v = this.view, t = this.tx(px), x = this.vy(py);
    if (e.ctrlKey) this.view = {...v, v0: x + (v.v0 - x) * k, v1: x + (v.v1 - x) * k};
    else this.view = {t0: t + (v.t0 - t) * k, t1: t + (v.t1 - t) * k, v0: x + (v.v0 - x) * k, v1: x + (v.v1 - x) * k};
    this.draw();
  }
  select(i) { this.sel = i; this.onSelect && this.onSelect(i); }
  deleteKey() {
    if (!this.curve || this.sel < 0 || this.curve.times.length <= 1) return;
    this.curve = L.removeKey(this.curve, this.sel); this.select(-1); this.draw(); this.onChange(this.curve, true);
  }
  setInterp(v) { if (!this.curve) return; this.curve.interp = v; this.draw(); this.onChange(this.curve, true); }
  autoTangents() {
    if (!this.curve) return;
    this.curve = L.autoTangents(this.curve, this.sel >= 0 ? [this.sel] : null); this.draw(); this.onChange(this.curve, true);
  }

  draw() {
    const g = this.ctx, w = this.w, h = this.h, c = this.curve, v = this.view;
    g.clearRect(0, 0, w, h); g.fillStyle = "#14161a"; g.fillRect(0, 0, w, h);
    g.font = "10px ui-monospace, monospace"; g.textBaseline = "middle";
    const grid = (lo, hi, n) => { const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw))), s = [1, 2, 5, 10].map(m => m * p).find(m => m >= raw); const out = []; for (let x = Math.ceil(lo / s) * s; x <= hi; x += s) out.push(x); return out; };
    g.strokeStyle = "#262a31"; g.fillStyle = "#6b717c"; g.lineWidth = 1;
    for (const x of grid(v.v0, v.v1, 5)) { const y = this.sy(x); g.beginPath(); g.moveTo(PAD.l, y); g.lineTo(w - PAD.r, y); g.stroke(); g.textAlign = "right"; g.fillText(L.fmt(x, 3), PAD.l - 4, y); }
    for (const t of grid(v.t0, v.t1, 8)) { const x = this.sx(t); g.beginPath(); g.moveTo(x, PAD.t); g.lineTo(x, h - PAD.b); g.stroke(); g.textAlign = "center"; g.fillText(L.fmt(t, 3), x, h - 8); }
    if (!c) { g.fillStyle = "#8a909b"; g.textAlign = "center"; g.fillText("no curve sampler selected", w / 2, h / 2); return; }
    g.save(); g.beginPath(); g.rect(PAD.l, PAD.t, w - PAD.l - PAD.r, h - PAD.t - PAD.b); g.clip();
    for (let d = 0; d < c.dim; d++) {
      if (!this.dimOn[d]) continue;
      g.strokeStyle = DIM_COLORS[d % DIM_COLORS.length]; g.lineWidth = 1.6; g.beginPath();
      const steps = Math.max(80, Math.round(w - PAD.l - PAD.r));
      for (let s = 0; s <= steps; s++) {
        const t = v.t0 + (v.t1 - v.t0) * s / steps, y = this.sy(L.evalCurve(c, t)[d]), x = this.sx(t);
        s ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.stroke();
    }
    for (let d = 0; d < c.dim; d++) {
      if (!this.dimOn[d]) continue;
      const col = DIM_COLORS[d % DIM_COLORS.length];
      for (let i = 0; i < c.times.length; i++) {
        const x = this.sx(c.times[i]), y = this.sy(c.values[i][d]), on = i === this.sel;
        if (on && c.interp === "hermite") {
          g.strokeStyle = col; g.lineWidth = 1; g.fillStyle = "#14161a";
          for (const which of ["in", "out"]) {
            const [ht, hv] = L.handlePos(c, i, d, which, this.handleLen()), hx = this.sx(ht), hy = this.sy(hv);
            g.beginPath(); g.moveTo(x, y); g.lineTo(hx, hy); g.stroke();
            g.beginPath(); g.arc(hx, hy, HANDLE_R, 0, 7); g.fill(); g.stroke();
          }
        }
        g.fillStyle = on ? "#fff" : col; g.strokeStyle = "#000"; g.lineWidth = 1;
        g.beginPath(); g.rect(x - KEY_R, y - KEY_R, KEY_R * 2, KEY_R * 2); g.fill(); g.stroke();
      }
    }
    g.restore();
  }
}
