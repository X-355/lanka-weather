import { bars, districtMap, hbars, reliability, timeline } from "./charts.js?v=dev";

const C = {
  model: "var(--series-1)", nwp: "var(--series-2)", markov: "var(--series-3)",
  clim: "var(--neutral)", year: "var(--series-2)",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS_IN = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const pct = (x, d = 0) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);
const num = (x, d = 3) => (x == null ? "–" : x.toFixed(d));
const fmtDate = s => new Date(s + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const $ = id => document.getElementById(id);

async function get(name) {
  try {
    const r = await fetch(`data/${name}`, { cache: "no-cache" });  // revalidate data on every visit
    return r.ok ? r.json() : null;
  } catch { return null; }
}

const state = {};
const renderers = {};
let active = "forecast";

// ------------------------------------------------------------------ shell
function setupTabs() {
  const buttons = document.querySelectorAll("nav.tabs button");
  const go = tab => {
    active = tab;
    buttons.forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
    document.querySelectorAll("main section").forEach(s => s.classList.toggle("active", s.id === tab));
    if (location.hash.slice(1) !== tab) history.replaceState(null, "", "#" + tab);
    renderers[tab]?.();
    scrollTo(0, 0);
  };
  buttons.forEach(b => b.addEventListener("click", () => go(b.dataset.tab)));
  const h = location.hash.slice(1);
  go(document.getElementById(h) ? h : "forecast");
  addEventListener("hashchange", () => { const k = location.hash.slice(1); if (k !== active && document.getElementById(k)) go(k); });
  let t;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => renderers[active]?.(), 150); });
  $("theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch {}
    renderers[active]?.();
  });
}
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}

function townOptions(select, selected) {
  select.innerHTML = state.meta.towns.map(t => `<option value="${t.key}">${t.name}</option>`).join("");
  select.value = selected || state.meta.towns[0].key;
}
const townName = k => state.meta.towns.find(t => t.key === k)?.name ?? k;

// ------------------------------------------------------------------ forecast checker
let mapMode = "rain";
let selectedDistrict = null;

function renderDistrictPanel() {
  const panel = $("district-panel"), f = state.forecast;
  if (!selectedDistrict) {
    panel.innerHTML = `<p class="sub" style="margin:0"><b>Click a district on the map</b> to see the forecast for the towns inside it.</p>`;
    return;
  }
  const t = state.meta.towns.find(x => x.key === selectedDistrict);
  const d = f?.towns.find(x => x.town === selectedDistrict);
  const places = (f?.places || []).filter(p => p.district === selectedDistrict);
  const thr = state.meta.targets;
  if (!d) {
    panel.innerHTML = `<h2 style="margin:0 0 4px">${t.name} District</h2><p class="sub" style="margin:0">No forecast yet for this district. It starts with the first forecast after the 25-district retrain.</p>`;
    return;
  }
  const dsk = state.metrics?.metrics?.by_town?.[selectedDistrict];
  const rows = [{ name: `${t.name} (district point)`, p_rain: d.p_rain, p_heavy: d.p_heavy, today_precip: d.today_precip, elevation: t.elevation, hl: true,
                  skill: dsk ? { bss: dsk.model.bss, markov_bss: dsk.markov.bss, auc: dsk.model.auc, unseen_cell: false } : null },
                ...places.map(p => ({ ...p, skill: state.placesBacktest[p.name] }))];
  const skillCell = r => {
    const k = r.skill;
    if (!k || k.bss == null) return `<span class="muted">not yet</span>`;
    const note = k.shares_cell_with ? `shares weather data with the ${townName(k.shares_cell_with)} district point` : k.unseen_cell ? "location never seen in training" : "district point";
    return `<span title="Brier skill vs this town's own climatology, test years. Markov baseline ${pct(k.markov_bss, 0)}. AUC ${num(k.auc, 2)}. ${note}.">${pct(k.bss, 0)}</span>`;
  };
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap">
      <h2 style="margin:0">${t.name} District</h2><span class="muted small">${t.region} Province · ${t.zone} zone · normal for the date ${pct(d.p_clim)}</span></div>
    <div class="table-scroll" style="margin-top:8px">${scoreTable(rows, [
      { label: "Town", get: r => r.name },
      { label: `Rain ≥ ${thr.rain_mm} mm`, num: true, get: r => `<span class="swatch" style="background:color-mix(in oklab, var(--series-1) ${Math.round(10 + 80 * r.p_rain)}%, var(--surface-1))"></span>${pct(r.p_rain)}` },
      { label: `Heavy ≥ ${thr.heavy_mm} mm`, num: true, get: r => pct(r.p_heavy, 1) },
      { label: "Rain today", num: true, get: r => r.today_precip == null ? "–" : `${r.today_precip} mm` },
      { label: "Elevation", num: true, get: r => r.elevation == null ? "–" : `${r.elevation} m` },
      { label: "Tested skill", num: true, get: skillCell, title: "Backtest skill vs climatology on 2022→ test years" },
    ])}</div>
    <p class="sub small" style="margin:8px 0 0">${places.length ? (Object.keys(state.placesBacktest).length
        ? "Town forecasts use the same model, fed with each town's own weather. <b>Tested skill</b> is the town's backtest on the test years: how much better than the town's own calendar odds (hover for detail). Most towns are places the model never trained on. Towns close together often share weather data, so similar numbers are expected."
        : "Town forecasts use the same model, fed with each town's own weather. Their backtest is still downloading (“not yet”); the published scores are for the district points. Towns close together often share weather data, so similar numbers are expected.") : "No town list for this district yet."}</p>`;
}
// Label nudges (SVG units) where a district's box centre isn't a good spot.
const NUDGE = { colombo: [-14, 0], gampaha: [-6, 0], kalutara: [-4, 2], puttalam: [4, 10], jaffna: [8, 6],
  trincomalee: [-6, 18], hambantota: [6, 4], galle: [-4, 0], kurunegala: [0, -10], mannar: [0, 10], batticaloa: [-6, 0] };

function renderMap() {
  const f = state.forecast, byTown = Object.fromEntries((f?.towns || []).map(t => [t.town, t]));
  const missing = state.meta.towns.filter(t => !byTown[t.key]).length;
  const areas = {};
  for (const t of state.meta.towns) {
    const r = byTown[t.key];
    if (!r) {
      areas[t.key] = { label: t.name, muted: true, fill: "url(#nodata-hatch)",
        tip: `<b>${t.name}</b><div>${t.region} Province</div><div class="muted">Joins at the next retrain, once its history has downloaded.</div>` };
    } else if (mapMode === "heavy") {
      const st = heavyStatus(r.p_heavy, r.p_clim_heavy);
      areas[t.key] = { label: t.name, fill: `color-mix(in oklab, ${st.color} 70%, var(--surface-1))`,
        tip: `<b>${t.name}</b><div class="row"><span>Chance ≥ ${state.meta.targets.heavy_mm} mm</span><b>${pct(r.p_heavy, 1)}</b></div><div class="row"><span>Normal</span><b>${pct(r.p_clim_heavy, 1)}</b></div><div>${st.icon ? st.icon + " " : ""}${st.label} (research only)</div>` };
    } else {
      areas[t.key] = { label: t.name, fill: `color-mix(in oklab, var(--series-1) ${Math.round(10 + 80 * r.p_rain)}%, var(--surface-1))`,
        tip: `<b>${t.name}</b><div class="row"><span>Chance of rain</span><b>${pct(r.p_rain)}</b></div><div class="row"><span>Normal for date</span><b>${pct(r.p_clim)}</b></div>` };
    }
  }
  districtMap($("fc-map"), { areas, labelNudge: NUDGE, selected: selectedDistrict,
    onClick: key => { selectedDistrict = key; renderMap(); renderDistrictPanel(); $("district-panel").scrollIntoView({ behavior: "smooth", block: "nearest" }); } });
  $("map-note").innerHTML = (mapMode === "rain" ? "Darker blue = higher chance of rain tomorrow. " : "Heavy-rain status, research only. ")
    + (missing ? `Hatched: no forecast yet for ${missing} districts. ` : "")
    + `<br><span class="muted">District map: <a href="https://mapsvg.com/maps/sri-lanka">MapSVG</a> via <a href="https://github.com/VictorCazanave/svg-maps">svg-maps</a>, <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.</span>`;
}

function renderForecastCards() {
  const f = state.forecast;
  renderMap();
  renderDistrictPanel();
  if (!f) {
    $("fc-lede").textContent = "No forecast has been issued yet. The daily job publishes one each evening.";
    return;
  }
  $("fc-lede").innerHTML = `Chance of at least ${state.meta.targets.rain_mm} mm of rain on <b>${fmtDate(f.target_date)}</b>, ${f.season}. Issued ${new Date(f.issued_at).toLocaleString("en-GB", { timeZone: "Asia/Colombo", dateStyle: "medium", timeStyle: "short" })} Sri Lanka time.`;
  const zones = Object.fromEntries(state.meta.towns.map(t => [t.key, t.zone]));
  const info = Object.fromEntries(state.meta.towns.map(t => [t.key, t]));
  const rows = [...f.towns].sort((a, b) => (info[a.town].region + a.name).localeCompare(info[b.town].region + b.name));
  $("fc-cards").innerHTML = rows.map(t => {
    const diff = t.p_rain - t.p_clim;
    const vs = Math.abs(diff) < 0.08 ? "about normal for the date" : diff > 0 ? `wetter than normal (${pct(t.p_clim)})` : `drier than normal (${pct(t.p_clim)})`;
    return `<div class="card town-card">
      <div class="name">${t.name}</div><div class="zone">${info[t.town].region} · ${zones[t.town]} zone</div>
      <div class="big">${pct(t.p_rain)}</div>
      <div class="meter" role="img" aria-label="${pct(t.p_rain)} chance; normal ${pct(t.p_clim)}">
        <div class="fill" style="width:${100 * t.p_rain}%"></div><div class="tick" style="left:calc(${100 * t.p_clim}% - 1px)"></div></div>
      <div class="meta">${vs}</div>
      <div class="meta muted">Today: ${t.today_precip == null ? "–" : t.today_precip + " mm"}</div>
    </div>`;
  }).join("");
}

function scoreTable(rows, cols) {
  return `<table class="data"><thead><tr>${cols.map(c => `<th class="${c.num ? "num" : ""}" title="${c.title || ""}">${c.label}</th>`).join("")}</tr></thead>
  <tbody>${rows.map(r => `<tr class="${r.hl ? "hl" : ""}">${cols.map(c => `<td class="${c.num ? "num" : ""}">${c.get(r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
const SCORE_COLS = [
  { label: "Brier ↓", num: true, get: r => num(r.brier), title: "Mean squared error of the probability; lower is better" },
  { label: "Skill vs climatology ↑", num: true, get: r => pct(r.bss, 1), title: "1 − Brier/Brier(climatology)" },
  { label: "AUC ↑", num: true, get: r => num(r.auc), title: "How well rainy days are ranked above dry ones" },
  { label: "Accuracy", num: true, get: r => pct(r.accuracy, 1) },
  { label: "Rain caught (POD)", num: true, get: r => pct(r.pod, 0) },
  { label: "False alarms (FAR)", num: true, get: r => pct(r.far, 0) },
];

function renderLedger() {
  const L = state.ledger;
  const s = L?.summary;
  if (!s || !s.scored) {
    $("fc-ledger").innerHTML = `<div class="card"><b>${s?.issued ?? 0}</b> town-forecasts issued so far, <b>${s?.pending ?? 0}</b> waiting for observations. The first scores appear about six days after launch. Until then, the backtest below is the evidence.</div>`;
    return;
  }
  const a = s.all;
  $("fc-ledger").innerHTML = `<div class="grid kpis">
    <div class="card kpi"><div class="label">Forecasts scored</div><div class="value">${s.scored.toLocaleString()}</div><div class="note">${s.first_target} → ${s.last_target}; ${s.pending} pending</div></div>
    <div class="card kpi"><div class="label">Live skill vs climatology</div><div class="value">${pct(a.model.bss, 1)}</div><div class="note">Markov baseline ${pct(a.markov.bss, 1)}</div></div>
    <div class="card kpi"><div class="label">Live Brier score</div><div class="value">${num(a.model.brier)}</div><div class="note">Climatology ${num(a.climatology.brier)}</div></div>
    <div class="card kpi"><div class="label">Last 30 days skill</div><div class="value">${pct(s.last30.model.bss, 1)}</div><div class="note">${s.last30.model.n} town-days</div></div>
  </div>`;
}

function renderScoreboards() {
  const M = state.metrics?.metrics;
  if (!M) { $("fc-scoreboard").innerHTML = "<p class='muted'>No evaluation yet.</p>"; return; }
  const sp = state.meta.model.split;
  $("fc-test-lede").innerHTML = `The model was trained on ${sp.train[0].slice(0, 4)}–${sp.train[1].slice(0, 4)} and tuned on ${sp.valid[0].slice(0, 4)}–${sp.valid[1].slice(0, 4)}. It never saw <b>${sp.test[0]} → ${sp.test[1]}</b> (${M.rain.model.n.toLocaleString()} town-days). Every forecast below is scored on exactly those days.`;
  const names = { model: "Weather Lab model", markov: "Markov chain (yesterday + season)", climatology: "Climatology (calendar odds)", persistence: "Persistence (tomorrow = today)" };
  const rows = Object.entries(M.rain).map(([k, v]) => ({ ...v, name: names[k] || k, hl: k === "model" }));
  $("fc-scoreboard").innerHTML = scoreTable(rows, [{ label: "Forecast", get: r => r.name }, ...SCORE_COLS]);
  const ps = state.metrics?.places_summary?.unseen_cells;
  if (ps) $("fc-scoreboard").insertAdjacentHTML("beforeend", `<p class="sub small" style="margin:10px 0 0"><b>At places it never trained on</b>: ${ps.n_towns} towns in ${ps.n_cells} separate grid cells, same test years. The model's skill is <b>${pct(ps.model.bss, 1)}</b>, against ${pct(ps.markov.bss, 1)} for the Markov baseline (AUC ${num(ps.model.auc)}).</p>`);

  const c = M.nwp_comparison;
  if (!c) {
    $("fc-nwp-lede").textContent = "Archived day-ahead forecasts from operational weather models are not available yet.";
    $("fc-nwp").innerHTML = "";
  } else {
    $("fc-nwp-lede").innerHTML = `Day-ahead runs of operational weather models (Open-Meteo archive of forecasts issued ~24 h ahead), ${c.period[0]} → ${c.period[1]}, on the same ${c.n.toLocaleString()} town-days. NWP gives rain amounts, not probabilities, so it is scored as yes/no (≥ 1 mm forecast). Brier punishes yes/no forecasts hard, so compare CSI, POD and FAR as well. <b>Caveat:</b> the "truth" here is ERA5, the same dataset our model learned from, so it has a home advantage the professional models don't. Treat this comparison as indicative until it's repeated against rain gauges.`;
    const rows2 = Object.entries(c.entries).map(([k, v]) => ({ ...v, hl: k === "model" }));
    $("fc-nwp").innerHTML = scoreTable(rows2, [
      { label: "Forecast", get: r => r.label }, { label: "Type", get: r => `<span class="muted">${r.kind}</span>` },
      SCORE_COLS[0], SCORE_COLS[1], SCORE_COLS[3],
      { label: "CSI ↑", num: true, get: r => num(r.csi), title: "Hits / (hits + misses + false alarms)" },
      SCORE_COLS[4], SCORE_COLS[5],
    ]);
  }
  $("fc-reliability").innerHTML = "";
  reliability($("fc-reliability"), { series: [
    { name: "Weather Lab model", color: C.model, points: M.reliability.model },
    { name: "Markov baseline", color: C.markov, points: M.reliability.markov },
  ] });
  const keys = state.meta.towns.map(t => t.key).filter(k => M.by_town[k]);
  $("fc-towns").innerHTML = "";
  hbars($("fc-towns"), {
    categories: keys.map(townName),
    series: [
      { name: "Weather Lab model", color: C.model, values: keys.map(k => M.by_town[k].model.bss) },
      { name: "Markov baseline", color: C.markov, values: keys.map(k => M.by_town[k].markov.bss) },
    ],
    fmt: v => `${(100 * v).toFixed(0)}%`,
  });
}

function setupChecker() {
  const B = state.backtest;
  if (!B) { $("ck-result").innerHTML = "<p class='muted'>No backtest data.</p>"; return; }
  townOptions($("ck-town"));
  const first = Object.values(B)[0];
  const d = $("ck-date");
  d.min = first.dates[0];
  d.max = first.dates[first.dates.length - 1];
  d.value = d.max;
  $("ck-town").addEventListener("change", renderChecker);
  d.addEventListener("change", renderChecker);
}

function renderChecker() {
  const B = state.backtest;
  if (!B) return;
  const town = $("ck-town").value, b = B[town];
  let i = b.dates.indexOf($("ck-date").value);
  if (i < 0) { i = b.dates.length - 1; $("ck-date").value = b.dates[i]; }
  const thr = state.meta.targets.rain_mm, obs = b.obs[i], rained = obs != null && obs >= thr;
  const entries = [["Weather Lab model", b.p[i], "prob"], ["Markov baseline", b.markov[i], "prob"], ["Climatology", b.clim[i], "prob"]];
  for (const [k, label] of Object.entries(state.meta.nwp_models)) if (b[`nwp_${k}`]) entries.push([label, b[`nwp_${k}`][i], "mm"]);
  const verdict = (v, kind) => {
    if (v == null) return "<span class='muted'>no data</span>";
    const yes = kind === "prob" ? v >= 0.5 : v >= thr;
    const right = yes === rained;
    return `<span class="verdict">${right ? "✓ right" : "✗ wrong"}</span>`;
  };
  $("ck-result").innerHTML = `<p style="margin:0 0 10px"><b>${townName(town)}, ${fmtDate(b.dates[i])}</b>. Observed: <b>${obs == null ? "–" : obs.toFixed(1) + " mm"}</b> (${rained ? "a rain day" : "dry"}).</p>
    <table class="data"><thead><tr><th>Forecast</th><th class="num">Said</th><th>Call at 50% / 1 mm</th></tr></thead><tbody>
    ${entries.map(([n, v, kind]) => `<tr><td>${n}</td><td class="num">${v == null ? "–" : kind === "prob" ? pct(v) + " chance" : v.toFixed(1) + " mm"}</td><td>${verdict(v, kind)}</td></tr>`).join("")}
    </tbody></table>
    <p class="sub small" style="margin:10px 0 0">A single day can't show skill. A 30% forecast is supposed to see rain on about 3 days in 10. The tables above are the real test.</p>`;
  const lo = Math.max(0, i - 45), hi = Math.min(b.dates.length, i + 15);
  $("ck-timeline").innerHTML = "";
  timeline($("ck-timeline"), {
    dates: b.dates.slice(lo, hi), obs: b.obs.slice(lo, hi), rainMm: thr,
    series: [{ name: "Weather Lab model", color: C.model, values: b.p.slice(lo, hi) },
             { name: "Climatology", color: C.clim, values: b.clim.slice(lo, hi) }],
  });
}

renderers.forecast = () => { renderForecastCards(); renderLedger(); renderScoreboards(); renderChecker(); };

// ------------------------------------------------------------------ monsoon
const SEASON_ORDER = ["NEM", "FIM", "SWM", "SIM"];
let moSeason = null;

function setupMonsoon() {
  if (!state.climate) return;
  townOptions($("mo-town"));
  const years = Object.keys(Object.values(state.climate.towns)[0].by_year).reverse();
  $("mo-year").innerHTML = years.map(y => `<option>${y}</option>`).join("");
  const m = new Date(state.climate.last_day).getMonth() + 1;
  moSeason = { 12: "NEM", 1: "NEM", 2: "NEM", 3: "FIM", 4: "FIM", 10: "SIM", 11: "SIM" }[m] || "SWM";
  $("mo-season-seg").innerHTML = SEASON_ORDER.map(s => `<button data-s="${s}" aria-pressed="${s === moSeason}">${s}</button>`).join("");
  $("mo-season-seg").addEventListener("click", e => {
    const s = e.target.dataset.s; if (!s) return;
    moSeason = s;
    [...$("mo-season-seg").children].forEach(b => b.setAttribute("aria-pressed", b.dataset.s === s));
    renderers.monsoon();
  });
  $("mo-town").addEventListener("change", () => renderers.monsoon());
  $("mo-year").addEventListener("change", () => renderers.monsoon());
}

renderers.monsoon = () => {
  const CL = state.climate;
  if (!CL) return;
  const t = CL.towns[$("mo-town").value], year = $("mo-year").value, yv = t.by_year[year];
  const [b0, b1] = CL.baseline;
  const ytd = yv.reduce((a, v) => a + (v ?? 0), 0);
  const nMonths = yv.filter(v => v != null).length;
  const normYtd = t.normal_total.slice(0, nMonths).reduce((a, v) => a + v, 0);
  const lastMonth = nMonths - 1;
  const lastIsPartial = year === CL.last_day.slice(0, 4);
  const sp = t.seasons[moSeason]?.partial;
  const seasonNormal = (() => { const T = t.seasons[moSeason]; const xs = T.totals.filter((_, i) => T.years[i] <= b1); return xs.length ? xs.reduce((a, v) => a + v, 0) / xs.length : null; })();
  $("mo-kpis").innerHTML = `
    <div class="card kpi"><div class="label">${year} so far</div><div class="value">${Math.round(ytd).toLocaleString()} mm</div><div class="note">${normYtd ? Math.round(100 * ytd / normYtd) + "% of normal for " + MONTHS[0] + "–" + MONTHS[lastMonth] : ""}</div></div>
    <div class="card kpi"><div class="label">Normal year</div><div class="value">${t.annual_normal.toLocaleString()} mm</div><div class="note">${b0}–${b1} average</div></div>
    <div class="card kpi"><div class="label">${moSeason} ${sp ? sp.year + " (in progress)" : "normal"}</div><div class="value">${sp ? Math.round(sp.total) : Math.round(seasonNormal ?? 0)} mm</div><div class="note">${sp ? `${sp.days} of ${sp.of} days; full-season normal ${Math.round(seasonNormal)} mm` : state.meta.seasons[moSeason]}</div></div>
    <div class="card kpi"><div class="label">Final data through</div><div class="value" style="font-size:20px">${fmtDate(CL.last_day)}</div><div class="note">${CL.provisional_through ? `final; provisional through ${fmtDate(CL.provisional_through)}` : "ERA5 is published ~5 days late"}</div></div>`;
  $("mo-monthly-sub").textContent = `Grey bars are the ${b0}–${b1} average. Whiskers span the 10th–90th percentile of past years.${lastIsPartial ? ` ${MONTHS[lastMonth]} ${year} is incomplete.` : ""}`;
  $("mo-monthly").innerHTML = "";
  bars($("mo-monthly"), {
    categories: MONTHS,
    series: [
      { name: `Normal (${b0}–${b1})`, color: C.clim, values: t.normal_total },
      { name: year, color: C.year, values: yv },
      { name: "Typical range (P10–P90)", color: "var(--text-secondary)", kind: "range", lo: t.p10, hi: t.p90 },
    ],
    fmt: v => `${Math.round(v)}`, yTitle: "mm",
  });
  const S = t.seasons[moSeason];
  const yrs = S.years.slice(-16), tots = S.totals.slice(-16);
  $("mo-seasons").innerHTML = "";
  bars($("mo-seasons"), {
    categories: yrs.map(String),
    series: [{ name: state.meta.seasons[moSeason], color: C.model, values: tots },
             { name: "Normal", color: C.clim, kind: "line", values: yrs.map(() => seasonNormal) }],
    fmt: v => `${Math.round(v)}`, yTitle: "mm", height: 230,
  });
  $("mo-recent").innerHTML = "";
  const prov = t.provisional || [];
  const rDates = [...t.recent.dates, ...prov.map(p => p.date)];
  const pad = a => a.concat(new Array(prov.length).fill(null));
  bars($("mo-recent"), {
    categories: rDates.map(d => d.slice(5)),
    series: [{ name: "ERA5 (final)", color: C.model, values: pad(t.recent.precip) },
             ...(prov.length ? [{ name: "Provisional (live analysis)", color: C.nwp,
               values: new Array(t.recent.dates.length).fill(null).concat(prov.map(p => p.precip)) }] : [])],
    fmt: v => v.toFixed(0), yTitle: "mm", height: 230, overlay: true,
    tipTitle: i => fmtDate(rDates[i]),
  });
};

// ------------------------------------------------------------------ solar
function setupSolar() {
  if (!state.solar) return;
  townOptions($("so-town"), "colombo");
  const s = state.meta.solar;
  $("so-pr").value = s.performance_ratio;
  $("so-tariff").value = s.default_tariff_lkr;
  $("so-cost").value = s.default_cost_lkr_per_kwp;
  ["so-town", "so-kwp", "so-orient", "so-shade", "so-pr", "so-tariff", "so-cost"].forEach(id => $(id).addEventListener("input", () => renderers.solar()));
}

renderers.solar = () => {
  const S = state.solar?.[$("so-town").value];
  if (!S) return;
  const kwp = +$("so-kwp").value || 0, pr = +$("so-pr").value || 0.78;
  const f = (+$("so-orient").value) * (+$("so-shade").value);
  const tariff = +$("so-tariff").value || 0, cost = (+$("so-cost").value || 0) * kwp;
  const monthly = S.ghi_month.map((g, i) => kwp * g * pr * f * DAYS_IN[i]);
  const annual = monthly.reduce((a, v) => a + v, 0);
  const lo = S.annual_p10 ? annual * S.annual_p10 / S.annual_mean : null, hi = S.annual_p90 ? annual * S.annual_p90 / S.annual_mean : null;
  const savings = annual * tariff, payback = savings > 0 ? cost / savings : null;
  const best = monthly.indexOf(Math.max(...monthly)), worst = monthly.indexOf(Math.min(...monthly));
  $("so-kpis").innerHTML = `
    <div class="card kpi"><div class="label">Yearly output</div><div class="value">${Math.round(annual).toLocaleString()} kWh</div><div class="note">${lo ? `typical range ${Math.round(lo).toLocaleString()}–${Math.round(hi).toLocaleString()}` : ""}</div></div>
    <div class="card kpi"><div class="label">Per kWp</div><div class="value">${kwp ? Math.round(annual / kwp).toLocaleString() : "–"} kWh</div><div class="note">${(annual / 365).toFixed(1)} kWh a day on average</div></div>
    <div class="card kpi"><div class="label">Value per year</div><div class="value">LKR ${Math.round(savings / 1000).toLocaleString()}k</div><div class="note">at LKR ${tariff}/kWh</div></div>
    <div class="card kpi"><div class="label">Simple payback</div><div class="value">${payback ? payback.toFixed(1) + " yrs" : "–"}</div><div class="note">on LKR ${(cost / 1e6).toFixed(2)}M upfront</div></div>`;
  $("so-sub").textContent = `Best month ${MONTHS[best]}, weakest ${MONTHS[worst]}: monsoon cloud cuts output by ${Math.round(100 * (1 - monthly[worst] / monthly[best]))}%. Sunlight data: ${S.source}, ${S.years[0]}–${S.years[1]}.`;
  $("so-chart").innerHTML = "";
  bars($("so-chart"), {
    categories: MONTHS,
    series: [{ name: "Expected output", color: C.model, values: monthly }],
    fmt: v => `${Math.round(v)}`, yTitle: "kWh",
  });
};

// ------------------------------------------------------------------ heavy rain
function heavyStatus(p, clim) {
  const ratio = clim > 0 ? p / clim : 0;
  if (p >= 0.25) return { label: "High", color: "var(--critical)", icon: "▲▲" };
  if (p >= 0.10 || ratio >= 4) return { label: "Elevated", color: "var(--serious)", icon: "▲" };
  if (ratio >= 2) return { label: "Above normal", color: "var(--warning)", icon: "△" };
  return { label: "Typical", color: "var(--good)", icon: "" };
}

function setupHeavy() {
  if (!state.heavy) return;
  townOptions($("hv-town"), "ratnapura");
  $("hv-town").addEventListener("change", () => renderers.heavy());
}

renderers.heavy = () => {
  const H = state.heavy, f = state.forecast, thr = state.meta.targets.heavy_mm;
  if (f) {
    $("hv-lede").innerHTML = `Model chance of ≥ ${thr} mm on <b>${fmtDate(f.target_date)}</b>, compared with the calendar odds for that date. In ERA5, a ${thr} mm day is rare. Even the wettest towns see only a few a year.`;
    const rows = [...f.towns].sort((a, b) => b.p_heavy - a.p_heavy);
    $("hv-table").innerHTML = scoreTable(rows, [
      { label: "Town", get: r => r.name },
      { label: "Status", get: r => { const s = heavyStatus(r.p_heavy, r.p_clim_heavy); return `<span class="status"><span class="dot" style="background:${s.color}"></span>${s.icon ? s.icon + " " : ""}${s.label}</span>`; } },
      { label: "Chance ≥ " + thr + " mm", num: true, get: r => pct(r.p_heavy, 1) },
      { label: "Normal for date", num: true, get: r => pct(r.p_clim_heavy, 1) },
      { label: "× normal", num: true, get: r => r.p_clim_heavy > 0 ? (r.p_heavy / r.p_clim_heavy).toFixed(1) + "×" : "–" },
      { label: "Heavy days / yr", num: true, get: r => H?.towns[r.town]?.per_year ?? "–" },
    ]);
  }
  const M = state.metrics?.metrics?.heavy;
  if (M) {
    $("hv-skill").innerHTML = `On the unseen test years, heavy-rain days were <b>${pct(M.model.base_rate, 1)}</b> of town-days. The model's average precision is <b>${num(M.model.avg_precision)}</b>, against <b>${num(M.climatology.avg_precision)}</b> for calendar odds. AUC is ${num(M.model.auc)}, and Brier skill vs climatology is ${pct(M.model.bss, 1)}. Read these as "somewhat better than the calendar", not as a warning system. The status labels are fixed probability thresholds, not calibrated alerts.`;
  }
  if (H) {
    const t = H.towns[$("hv-town").value];
    $("hv-months").innerHTML = "";
    bars($("hv-months"), {
      categories: MONTHS,
      series: [{ name: `Average ≥ ${thr} mm days`, color: C.model, values: t.per_month }],
      fmt: v => v.toFixed(1), yTitle: "days / month", height: 230,
    });
    $("hv-events").innerHTML = `<p class="sub">Record day in the data: <b>${t.max.mm} mm</b> on ${fmtDate(t.max.date)}.</p>` + scoreTable(t.recent_events.slice(0, 10), [
      { label: "Date", get: r => fmtDate(r.date) }, { label: "Rainfall", num: true, get: r => r.mm + " mm" }]);
  }
};

// ------------------------------------------------------------------ weather (loaded on first visit)
renderers.weather = async () => {
  const { renderWeather } = await import("./weather.js?v=dev");
  renderWeather(state);
};

// ------------------------------------------------------------------ live sky (loaded on first visit)
renderers.live = async () => {
  const { renderLiveSky } = await import("./livesky.js?v=dev");
  renderLiveSky(state);
};

// ------------------------------------------------------------------ about
renderers.about = () => {
  const m = state.meta, sp = m.model.split || {};
  const imp = state.metrics?.importance?.slice(0, 8) ?? [];
  $("about-body").innerHTML = `
  <p><b>What it predicts.</b> For each town, the probability that tomorrow (local calendar day) gets at least ${m.targets.rain_mm} mm of rain. A second model estimates the chance of ≥ ${m.targets.heavy_mm} mm, for research only.</p>
  <p><b>Model.</b> One LightGBM gradient-boosted tree model shared by all ${m.towns.length} district capitals. It uses today's and recent weather at the town: rain over the last 1–30 days, humidity, pressure and its change, cloud, temperature range, wind direction. It also uses today's rain across the whole island, the season and the town's geography. Every input is known by the evening of the forecast day. A unit test checks that changing the future never changes a feature.</p>
  <p><b>Honest testing.</b> The data is split by date, never shuffled. Train: ${sp.train?.join(" → ") ?? "–"}. Validation, used only to decide when to stop training: ${sp.valid?.join(" → ") ?? "–"}. Test, never touched during development: ${sp.test?.join(" → ") ?? "–"}. Every forecast, including the baselines and professional models, is scored on the same rows. After testing, the model is refit on all data for live use, and the live ledger tracks that model from then on.</p>
  <p><b>Baselines.</b> <i>Climatology</i>: how often it rained at that town within ±15 days of the date in the training years. <i>Markov</i>: the same, split by whether it rained today. <i>Persistence</i>: tomorrow = today. A model that can't beat the Markov baseline isn't learning much weather.</p>
  <p><b>Professional forecasts.</b> Day-ahead runs of ECMWF IFS, NCEP GFS and DWD ICON from the Open-Meteo Previous Runs archive (forecasts made ~24 h earlier, from 2024), summed to local days.</p>
  <p><b>"Truth".</b> ERA5 reanalysis rainfall for the ~25 km grid cell containing the town. This is not a rain gauge: it smooths out showers, records too many drizzle days and underestimates extremes. The evaluation also cross-checks against NASA POWER rainfall as a second, independent estimate.</p>
  <p><b>Live inputs differ from training.</b> For the nightly forecast, the last 45 days come from Open-Meteo's forecast API (operational analyses), because ERA5 is published ~5 days late. Small statistical differences between the two are a known risk, and the live ledger measures what they cost.</p>
  ${imp.length ? `<p><b>Most influential inputs:</b> ${imp.map(([k, v]) => `<code>${k}</code> ${(100 * v).toFixed(0)}%`).join(", ")}.</p>` : ""}
  <p><b>Not an official forecast.</b> For decisions that matter, use the <a href="https://www.meteo.gov.lk">Department of Meteorology</a>.</p>
  <p class="muted small">Full evaluation report: <a href="data/REPORT.md">REPORT.md</a> · model on Hugging Face: <a href="https://huggingface.co/xi1514r/lanka-weather">xi1514r/lanka-weather</a></p>`;
};

// ------------------------------------------------------------------ boot
(async () => {
  const [meta, forecast, metrics, backtest, ledger, climate, solar, heavy, placesBacktest] = await Promise.all(
    ["meta.json", "forecast.json", "metrics.json", "backtest.json", "ledger.json", "climate.json", "solar.json", "heavy.json",
     "places_backtest.json"].map(get));
  if (!meta) { $("fc-lede").textContent = "Site data has not been built yet."; return; }
  Object.assign(state, { meta, forecast, metrics, backtest, ledger, climate, solar, heavy, placesBacktest: placesBacktest || {} });
  if (meta.synthetic) $("banner").innerHTML = `<div class="banner"><b>Demo data.</b> This build uses synthetic weather to test the pipeline. Real numbers appear after the first training run on GitHub Actions.</div>`;
  $("foot-model").textContent = meta.model.version ? `Model ${meta.model.version} · data updated ${new Date(meta.generated_at).toLocaleDateString("en-GB")}` : "";
  setupChecker(); setupMonsoon(); setupSolar(); setupHeavy();
  $("map-mode").addEventListener("click", e => {
    const m = e.target.dataset.m; if (!m) return;
    mapMode = m;
    [...$("map-mode").children].forEach(b => b.setAttribute("aria-pressed", b.dataset.m === m));
    renderMap();
  });
  setupTabs();
})();
