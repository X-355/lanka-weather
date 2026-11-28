// Small dependency-free SVG charts with hover tooltips. One y-axis per chart, always.
const NS = "http://www.w3.org/2000/svg";
const tip = (() => {
  const d = document.createElement("div");
  d.className = "tip";
  document.body.appendChild(d);
  return d;
})();

function showTip(evt, html) {
  tip.innerHTML = html;
  tip.style.display = "block";
  const pad = 14, r = tip.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}
function hideTip() { tip.style.display = "none"; }

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function niceTicks(lo, hi, n = 5) {
  if (hi === lo) hi = lo + 1;
  const span = hi - lo, step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
  const start = Math.floor(lo / step) * step, out = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// Rounded-top bar path anchored to the baseline (square at the base).
function barPath(x, y0, w, y1, r = 4) {
  const top = Math.min(y0, y1), h = Math.abs(y1 - y0);
  r = Math.min(r, w / 2, h);
  if (y1 <= y0) return `M${x},${y0}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${y0}Z`;
  const bot = y1;  // negative bar: round the bottom
  return `M${x},${y0}V${bot - r}Q${x},${bot} ${x + r},${bot}H${x + w - r}Q${x + w},${bot} ${x + w},${bot - r}V${y0}Z`;
}

function legend(container, items) {
  if (items.length < 2) return;
  const lg = document.createElement("div");
  lg.className = "legend";
  for (const it of items) {
    const s = document.createElement("span");
    const sw = it.kind === "line"
      ? `<svg width="18" height="10" style="margin-right:6px"><line x1="0" y1="5" x2="18" y2="5" stroke="${it.color}" stroke-width="2"/><circle cx="9" cy="5" r="3" fill="${it.color}"/></svg>`
      : it.kind === "range"
      ? `<svg width="12" height="12" style="margin-right:6px"><line x1="6" y1="0" x2="6" y2="12" stroke="${it.color}" stroke-width="2"/></svg>`
      : it.kind === "shade"
      ? `<span class="swatch" style="background:${it.color}"></span>`
      : `<span class="swatch" style="background:${it.color}"></span>`;
    s.innerHTML = sw + it.name;
    lg.appendChild(s);
  }
  container.appendChild(lg);
}

function frame(container, height, m) {
  container.innerHTML = "";
  const W = Math.max(280, container.clientWidth || 600);
  const wrap = document.createElement("div");
  wrap.className = "chart";
  container.appendChild(wrap);
  const svg = el("svg", { viewBox: `0 0 ${W} ${height}`, role: "img" }, wrap);
  return { svg, W, H: height, iw: W - m.l - m.r, ih: height - m.t - m.b, wrap };
}

/**
 * Vertical grouped bars + optional line and range overlays on ONE y-scale.
 * opts: {categories, series:[{name,color,values,kind:'bar'|'line'|'range', lo, hi}], height, fmt, yTitle, min, max}
 */
export function bars(container, opts) {
  const m = { t: 14, r: 12, b: 28, l: 46 };
  const fmt = opts.fmt || (v => v.toFixed(0));
  const cats = opts.categories;
  const ser = opts.series;
  const all = [];
  ser.forEach(s => {
    if (s.kind === "range") { s.lo.forEach(v => v != null && all.push(v)); s.hi.forEach(v => v != null && all.push(v)); }
    else s.values.forEach(v => v != null && all.push(v));
  });
  let lo = opts.min ?? Math.min(0, ...all), hi = opts.max ?? Math.max(...all, 0) * 1.05;
  const ticks = niceTicks(lo, hi);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  legend(container, ser.map(s => ({ name: s.name, color: s.color, kind: s.kind || "bar" })));
  const holder = document.createElement("div");
  container.appendChild(holder);
  const { svg, iw, ih } = frame(holder, opts.height || 260, m);
  const g = el("g", { transform: `translate(${m.l},${m.t})` }, svg);
  const y = v => ih - ((v - lo) / (hi - lo)) * ih;
  const gridG = el("g", { class: "grid" }, g);
  ticks.forEach(t => {
    el("line", { x1: 0, x2: iw, y1: y(t), y2: y(t) }, gridG);
    const tx = el("text", { x: -8, y: y(t) + 4, "text-anchor": "end" }, g);
    tx.textContent = fmt(t);
  });
  if (opts.yTitle) { const t = el("text", { x: -m.l + 2, y: -4 }, g); t.textContent = opts.yTitle; }
  const band = iw / cats.length;
  const barSer = ser.filter(s => (s.kind || "bar") === "bar");
  // overlay: series never share a category (e.g. final vs provisional days), so they share one slot
  const slots = opts.overlay ? 1 : barSer.length;
  const gap = 2, inner = Math.min(band * 0.78, 18 * slots + gap * (slots - 1));
  const bw = slots ? (inner - gap * (slots - 1)) / slots : 0;
  const every = Math.ceil(cats.length / Math.max(1, Math.floor(iw / 44)));
  cats.forEach((c, i) => {
    const x0 = i * band + (band - inner) / 2;
    barSer.forEach((s, j) => {
      const v = s.values[i];
      if (v == null) return;
      const k = opts.overlay ? 0 : j;
      el("path", { d: barPath(x0 + k * (bw + gap), y(0), bw, y(v), Math.min(4, bw / 2)), fill: s.color }, g);
    });
    if (i % every === 0) {
      const t = el("text", { x: i * band + band / 2, y: ih + 18, "text-anchor": "middle" }, g);
      t.textContent = c;
    }
  });
  ser.filter(s => s.kind === "range").forEach(s => {
    cats.forEach((c, i) => {
      if (s.lo[i] == null) return;
      const cx = i * band + band / 2;
      el("line", { x1: cx, x2: cx, y1: y(s.lo[i]), y2: y(s.hi[i]), stroke: s.color, "stroke-width": 2, "stroke-linecap": "round" }, g);
    });
  });
  ser.filter(s => s.kind === "line").forEach(s => {
    const pts = s.values.map((v, i) => v == null ? null : [i * band + band / 2, y(v)]);
    let d = "", pen = false;
    pts.forEach(p => { if (!p) { pen = false; return; } d += (pen ? "L" : "M") + p[0] + "," + p[1]; pen = true; });
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round" }, g);
    pts.forEach(p => p && el("circle", { cx: p[0], cy: p[1], r: 4, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2 }, g));
  });
  el("line", { x1: 0, x2: iw, y1: y(0), y2: y(0), stroke: "var(--axis)" }, g);
  // hover targets: one per category, full height
  cats.forEach((c, i) => {
    const r = el("rect", { x: i * band, y: 0, width: band, height: ih, fill: "transparent" }, g);
    r.addEventListener("mousemove", e => {
      const rows = ser.map(s => {
        const v = s.kind === "range" ? (s.lo[i] == null ? null : `${fmt(s.lo[i])}–${fmt(s.hi[i])}`) : (s.values[i] == null ? "–" : fmt(s.values[i]));
        return `<div class="row"><span><span class="swatch" style="background:${s.color}"></span>${s.name}</span><b>${v ?? "–"}</b></div>`;
      }).join("");
      showTip(e, `<b>${opts.tipTitle ? opts.tipTitle(i) : c}</b>${rows}`);
    });
    r.addEventListener("mouseleave", hideTip);
  });
  return svg;
}

/** Horizontal grouped bars (values may be negative). opts: {categories, series:[{name,color,values}], fmt} */
export function hbars(container, opts) {
  const fmt = opts.fmt || (v => v.toFixed(2));
  const cats = opts.categories, ser = opts.series;
  const rowH = 10 * ser.length + 2 * (ser.length - 1) + 14;
  const m = { t: 8, r: 50, b: 24, l: 110 };
  legend(container, ser.map(s => ({ name: s.name, color: s.color })));
  const holder = document.createElement("div");
  container.appendChild(holder);
  const { svg, iw, ih } = frame(holder, m.t + m.b + rowH * cats.length, m);
  const g = el("g", { transform: `translate(${m.l},${m.t})` }, svg);
  const all = ser.flatMap(s => s.values).filter(v => v != null);
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  const ticks = niceTicks(lo, hi, 4);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  const x = v => ((v - lo) / (hi - lo)) * iw;
  const gridG = el("g", { class: "grid" }, g);
  ticks.forEach(t => {
    el("line", { x1: x(t), x2: x(t), y1: 0, y2: ih }, gridG);
    const tx = el("text", { x: x(t), y: ih + 16, "text-anchor": "middle" }, g);
    tx.textContent = fmt(t);
  });
  cats.forEach((c, i) => {
    const y0 = i * rowH + 7;
    const t = el("text", { x: -10, y: y0 + (rowH - 14) / 2 + 4, "text-anchor": "end" }, g);
    t.textContent = c;
    ser.forEach((s, j) => {
      const v = s.values[i];
      if (v == null) return;
      const yy = y0 + j * 12, a = x(0), b = x(v);
      const left = Math.min(a, b), w = Math.max(1, Math.abs(b - a)), r = Math.min(4, w / 2);
      const d = v >= 0
        ? `M${a},${yy}H${b - r}Q${b},${yy} ${b},${yy + r}V${yy + 10 - r}Q${b},${yy + 10} ${b - r},${yy + 10}H${a}Z`
        : `M${a},${yy}H${b + r}Q${b},${yy} ${b},${yy + r}V${yy + 10 - r}Q${b},${yy + 10} ${b + r},${yy + 10}H${a}Z`;
      el("path", { d, fill: s.color }, g);
      if (j === 0) {
        const lbl = el("text", { x: v >= 0 ? b + 6 : b - 6, y: yy + 9, "text-anchor": v >= 0 ? "start" : "end" }, g);
        lbl.textContent = fmt(v);
      }
    });
    const hit = el("rect", { x: -m.l, y: i * rowH, width: iw + m.l, height: rowH, fill: "transparent" }, g);
    hit.addEventListener("mousemove", e => showTip(e, `<b>${c}</b>` + ser.map(s =>
      `<div class="row"><span><span class="swatch" style="background:${s.color}"></span>${s.name}</span><b>${s.values[i] == null ? "–" : fmt(s.values[i])}</b></div>`).join("")));
    hit.addEventListener("mouseleave", hideTip);
  });
  el("line", { x1: x(0), x2: x(0), y1: 0, y2: ih, stroke: "var(--axis)" }, g);
}

/** Reliability diagram. opts: {series:[{name,color,points:[{mean_p,obs_freq,n}]}]} */
export function reliability(container, opts) {
  const m = { t: 10, r: 14, b: 40, l: 46 };
  legend(container, [{ name: "Perfectly calibrated", color: "var(--neutral)", kind: "line" },
    ...opts.series.map(s => ({ name: s.name, color: s.color, kind: "line" }))]);
  const holder = document.createElement("div");
  container.appendChild(holder);
  const { svg, iw, ih } = frame(holder, opts.height || 300, m);
  const g = el("g", { transform: `translate(${m.l},${m.t})` }, svg);
  const x = v => v * iw, y = v => ih - v * ih;
  const gridG = el("g", { class: "grid" }, g);
  [0, .2, .4, .6, .8, 1].forEach(t => {
    el("line", { x1: 0, x2: iw, y1: y(t), y2: y(t) }, gridG);
    el("line", { x1: x(t), x2: x(t), y1: 0, y2: ih }, gridG);
    el("text", { x: -8, y: y(t) + 4, "text-anchor": "end" }, g).textContent = `${t * 100}%`;
    el("text", { x: x(t), y: ih + 16, "text-anchor": "middle" }, g).textContent = `${t * 100}%`;
  });
  el("text", { x: iw / 2, y: ih + 34, "text-anchor": "middle" }, g).textContent = "Forecast probability";
  el("line", { x1: 0, y1: ih, x2: iw, y2: 0, stroke: "var(--neutral)", "stroke-width": 2, "stroke-dasharray": "4 4" }, g);
  opts.series.forEach(s => {
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${x(p.mean_p)},${y(p.obs_freq)}`).join("");
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2 }, g);
    s.points.forEach(p => {
      const c = el("circle", { cx: x(p.mean_p), cy: y(p.obs_freq), r: 5, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2 }, g);
      const hit = el("circle", { cx: x(p.mean_p), cy: y(p.obs_freq), r: 12, fill: "transparent" }, g);
      hit.addEventListener("mousemove", e => showTip(e, `<b>${s.name}</b>
        <div class="row"><span>Forecast</span><b>${(100 * p.mean_p).toFixed(0)}%</b></div>
        <div class="row"><span>Rained</span><b>${(100 * p.obs_freq).toFixed(0)}%</b></div>
        <div class="row"><span>Days</span><b>${p.n.toLocaleString()}</b></div>`));
      hit.addEventListener("mouseleave", hideTip);
    });
  });
}

/**
 * Probability timeline with rain days shaded. opts: {dates, obs (mm), rainMm, series:[{name,color,values (0..1)}]}
 */
export function timeline(container, opts) {
  const m = { t: 10, r: 12, b: 28, l: 46 };
  legend(container, [...opts.series.map(s => ({ name: s.name, color: s.color, kind: "line" })),
    { name: `Rained ≥ ${opts.rainMm} mm`, color: "var(--neutral-soft)", kind: "shade" }]);
  const holder = document.createElement("div");
  container.appendChild(holder);
  const { svg, iw, ih } = frame(holder, opts.height || 240, m);
  const g = el("g", { transform: `translate(${m.l},${m.t})` }, svg);
  const n = opts.dates.length, band = iw / n;
  const x = i => i * band + band / 2, y = v => ih - v * ih;
  opts.obs.forEach((v, i) => { if (v != null && v >= opts.rainMm) el("rect", { x: i * band + 1, y: 0, width: Math.max(1, band - 2), height: ih, fill: "var(--neutral-soft)", rx: 2 }, g); });
  const gridG = el("g", { class: "grid" }, g);
  [0, .25, .5, .75, 1].forEach(t => {
    el("line", { x1: 0, x2: iw, y1: y(t), y2: y(t) }, gridG);
    el("text", { x: -8, y: y(t) + 4, "text-anchor": "end" }, g).textContent = `${t * 100}%`;
  });
  const every = Math.ceil(n / Math.max(1, Math.floor(iw / 50)));
  opts.dates.forEach((d, i) => { if (i % every === 0) el("text", { x: x(i), y: ih + 18, "text-anchor": "middle" }, g).textContent = d.slice(5); });
  opts.series.forEach(s => {
    let d = "", pen = false;
    s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + x(i) + "," + y(v); pen = true; });
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round" }, g);
  });
  const cross = el("line", { y1: 0, y2: ih, stroke: "var(--text-tertiary)", "stroke-width": 1, visibility: "hidden" }, g);
  const hit = el("rect", { x: 0, y: 0, width: iw, height: ih, fill: "transparent" }, g);
  hit.addEventListener("mousemove", e => {
    const r = svg.getBoundingClientRect(), scale = (iw + m.l + m.r) / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - r.left) * scale - m.l) / band)));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    showTip(e, `<b>${opts.dates[i]}</b>` + opts.series.map(s => `<div class="row"><span><span class="swatch" style="background:${s.color}"></span>${s.name}</span><b>${s.values[i] == null ? "–" : (100 * s.values[i]).toFixed(0) + "%"}</b></div>`).join("")
      + `<div class="row"><span>Observed</span><b>${opts.obs[i] == null ? "–" : opts.obs[i].toFixed(1) + " mm"}</b></div>`);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
}

import { DISTRICTS, VIEWBOX } from "./sri-lanka-districts.js";

/**
 * District choropleth. opts: {areas: {key: {fill, label, tip, muted}}, labelNudge: {key: [dx, dy]}}
 */
export function districtMap(container, opts) {
  container.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "chart";
  wrap.style.maxWidth = "380px";
  wrap.style.margin = "0 auto";
  container.appendChild(wrap);
  const svg = el("svg", { viewBox: VIEWBOX, role: "img", "aria-label": "Map of Sri Lanka's 25 districts" }, wrap);
  // Diagonal hatch for "no forecast yet", so it can't be mistaken for a low chance of rain.
  const defs = el("defs", {}, svg);
  const pat = el("pattern", { id: "nodata-hatch", width: 7, height: 7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  el("rect", { width: 7, height: 7, fill: "var(--surface-1)" }, pat);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 7, stroke: "var(--neutral)", "stroke-width": 2.2 }, pat);
  const shapes = el("g", {}, svg), labels = el("g", { "pointer-events": "none" }, svg);
  for (const [key, d] of Object.entries(DISTRICTS)) {
    const a = opts.areas[key] || {};
    const path = el("path", { d, fill: a.fill || "var(--surface-2)", stroke: "var(--surface-1)", "stroke-width": 1.6,
      "stroke-linejoin": "round" }, shapes);
    if (a.muted) path.setAttribute("stroke-dasharray", "3 2"), path.setAttribute("stroke", "var(--axis)");
    path.style.cursor = "default";
    path.addEventListener("mousemove", e => { path.setAttribute("stroke", "var(--text-primary)"); showTip(e, a.tip || key); });
    path.addEventListener("mouseleave", () => { path.setAttribute("stroke", a.muted ? "var(--axis)" : "var(--surface-1)"); hideTip(); });
    const b = path.getBBox(), n = (opts.labelNudge || {})[key] || [0, 0];
    const t = el("text", { x: b.x + b.width / 2 + n[0], y: b.y + b.height / 2 + 4 + n[1], "text-anchor": "middle",
      style: "font-size:13px;font-weight:500;fill:var(--text-primary);paint-order:stroke;stroke:var(--surface-1);stroke-width:3px;stroke-linejoin:round" }, labels);
    t.textContent = a.label || key;
  }
}
