// Rivers & reservoirs: modelled flow at the main river gauging stations, and upstream rain for the major reservoirs.
// Live in the browser from free, keyless services:
//   Copernicus GloFAS river model (via Open-Meteo Flood API) — flow forecast, long-term average, wettest year on record
//   Open-Meteo Forecast — rain over each reservoir's catchment, past 3 days and next 3 days
// This is NOT an official warning service: official river levels are on rivernet.lk (Irrigation Department),
// and spill-gate openings are announced by the Irrigation Department and the Mahaweli Authority.
import { bars } from "./charts.js?v=dev";
import { snapshot } from "./snapshot.js?v=dev";
import { fill, fitSoon } from "./fit.js?v=dev";

const FLOOD = "https://flood-api.open-meteo.com/v1/flood";
const OM = "https://api.open-meteo.com/v1/forecast";
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const $ = id => document.getElementById(id);

// Main gauging stations. lat/lon = the GloFAS river cell chosen as the main channel: the cell with the largest
// long-term flow within ±5 km of the station (checked: flow grows downstream along every river, and no station
// shares a cell with a different river).
const STATIONS = [
  { river: "Kelani Ganga", name: "Nagalagam Street", lat: 6.975, lon: 79.825, official: { unit: "ft", alert: 4, minor: 5, major: 7 } },
  { river: "Kelani Ganga", name: "Hanwella", lat: 6.925, lon: 80.025 },
  { river: "Kelani Ganga", name: "Kitulgala", lat: 6.975, lon: 80.375 },
  { river: "Kalu Ganga", name: "Putupaula", lat: 6.625, lon: 80.025 },
  { river: "Kalu Ganga", name: "Ellagawa", lat: 6.725, lon: 80.175 },
  { river: "Kalu Ganga", name: "Ratnapura", lat: 6.675, lon: 80.375 },
  { river: "Gin Ganga", name: "Baddegama", lat: 6.125, lon: 80.125 },
  { river: "Gin Ganga", name: "Tawalama", lat: 6.275, lon: 80.275 },
  { river: "Nilwala Ganga", name: "Thalgahagoda", lat: 5.925, lon: 80.525 },
  { river: "Nilwala Ganga", name: "Pitabeddara", lat: 6.175, lon: 80.425 },
  { river: "Attanagalu Oya", name: "Dunamale", lat: 7.125, lon: 80.025 },
  { river: "Maha Oya", name: "Giriulla", lat: 7.325, lon: 80.075 },
  { river: "Deduru Oya", name: "Lower Deduru Oya", lat: 7.625, lon: 79.775, regulated: true },
  { river: "Mahaweli Ganga", name: "Peradeniya", lat: 7.275, lon: 80.625 },
  { river: "Mahaweli Ganga", name: "Manampitiya", lat: 7.975, lon: 81.075, regulated: true },
  { river: "Walawe Ganga", name: "Embilipitiya", lat: 6.275, lon: 80.875, regulated: true },
  { river: "Kala Oya", name: "Lower Kala Oya", lat: 8.225, lon: 80.025, regulated: true },
  { river: "Malwathu Oya", name: "Thanthirimale", lat: 8.625, lon: 80.225 },
  { river: "Gal Oya", name: "Lower Gal Oya", lat: 7.275, lon: 81.775, regulated: true },
  { river: "Kirindi Oya", name: "Lower Kirindi Oya", lat: 6.175, lon: 81.225, regulated: true },
  { river: "Menik Ganga", name: "Near Kataragama", lat: 6.425, lon: 81.375, regulated: true },
];

// Major reservoirs (approximate positions) and a few points in the catchment upstream of each.
const RESERVOIRS = [
  { name: "Kotmale", river: "Kotmale Oya → Mahaweli", by: "Mahaweli Authority", lat: 7.065, lon: 80.596, catch: [[6.97, 80.77], [6.92, 80.65], [7.0, 80.7]] },
  { name: "Victoria", river: "Mahaweli Ganga", by: "Mahaweli Authority", lat: 7.234, lon: 80.785, catch: [[7.27, 80.6], [7.1, 80.62], [7.3, 80.72]] },
  { name: "Randenigala", river: "Mahaweli Ganga", by: "Mahaweli Authority", lat: 7.2, lon: 80.925, catch: [[7.23, 80.79], [6.95, 80.92], [7.05, 80.95]] },
  { name: "Rantambe", river: "Mahaweli Ganga", by: "Mahaweli Authority", lat: 7.2, lon: 81.04, catch: [[7.2, 80.93], [7.1, 81.05], [6.95, 80.92]] },
  { name: "Moragahakanda", river: "Amban Ganga", by: "Mahaweli Authority", lat: 7.7, lon: 80.77, catch: [[7.55, 80.75], [7.47, 80.7], [7.62, 80.65]] },
  { name: "Kalawewa", river: "Kala Oya", by: "Mahaweli Authority", lat: 8.02, lon: 80.53, catch: [[7.86, 80.65], [7.8, 80.55], [7.95, 80.6]] },
  { name: "Rajanganaya", river: "Kala Oya", by: "Irrigation Department", lat: 8.17, lon: 80.27, catch: [[8.02, 80.53], [8.05, 80.4], [7.95, 80.45]] },
  { name: "Deduru Oya", river: "Deduru Oya", by: "Irrigation Department", lat: 7.66, lon: 80.12, catch: [[7.48, 80.36], [7.4, 80.45], [7.55, 80.3]] },
  { name: "Inginimitiya", river: "Mee Oya", by: "Irrigation Department", lat: 8.03, lon: 80.14, catch: [[7.95, 80.25], [8.05, 80.25]] },
  { name: "Tabbowa", river: "Mee Oya", by: "Irrigation Department", lat: 8.07, lon: 79.94, catch: [[8.05, 80.05], [8.0, 80.1]] },
  { name: "Udawalawe", river: "Walawe Ganga", by: "Mahaweli Authority", lat: 6.43, lon: 80.85, catch: [[6.65, 80.7], [6.72, 80.77], [6.6, 80.8]] },
  { name: "Lunugamvehera", river: "Kirindi Oya", by: "Irrigation Department", lat: 6.35, lon: 81.2, catch: [[6.74, 81.1], [6.6, 81.15], [6.5, 81.2]] },
  { name: "Weheragala", river: "Menik Ganga", by: "Irrigation Department", lat: 6.45, lon: 81.37, catch: [[6.76, 81.24], [6.65, 81.3], [6.55, 81.35]] },
  { name: "Senanayake Samudra", river: "Gal Oya", by: "Irrigation Department", lat: 7.21, lon: 81.53, catch: [[7.17, 81.22], [7.25, 81.35], [7.1, 81.4]] },
  { name: "Maduru Oya", river: "Maduru Oya", by: "Mahaweli Authority", lat: 7.65, lon: 81.2, catch: [[7.55, 81.05], [7.45, 81.15]] },
];

const STATUS = { neutral: "var(--neutral)", good: "var(--good)", warning: "var(--warning)", serious: "var(--serious)", critical: "var(--critical)" };
const badge = (l, t) => `<span class="status"><span class="dot" style="background:${STATUS[l]}"></span>${t}</span>`;
const r0 = x => (x == null ? "–" : Math.round(x).toLocaleString());
const fmtD = s => new Date(s + "T00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Colombo" });

// The label follows the "× average" number shown beside it (peak flow ÷ long-term average for that date).
// GloFAS's 75th percentile can sit below the average for these flashy rivers, so it isn't used for the label.
export const RIVER_RULE = "Labels follow the × average column: under 0.5× lower than usual, 0.5–1.5× usual, 1.5–3× higher than usual, over 3× much higher than usual; red if the peak tops the wettest year on record for the date.";
export function riverStatus(peak, mean, max) {
  if (peak == null) return ["good", "–"];
  if (max != null && peak > max) return ["critical", "Above the wettest year on record"];
  const r = mean ? peak / mean : null;
  if (r == null) return ["good", "–"];
  if (r >= 3) return ["serious", "Much higher than usual"];
  if (r >= 1.5) return ["warning", "Higher than usual"];
  if (r < 0.5) return ["neutral", "Lower than usual"];
  return ["good", "Usual"];
}
function reservoirStatus(past, next) {
  // Rule of thumb on catchment rain, not an official threshold and not a release warning.
  if (next >= 150 || past + next >= 250) return ["critical", "Very heavy rain upstream"];
  if (next >= 75 || past + next >= 150) return ["serious", "Heavy rain upstream"];
  if (next >= 30 || past + next >= 75) return ["warning", "Some rain upstream"];
  return ["good", "Little rain upstream"];
}

const qs = p => new URLSearchParams(p).toString();
const getJSON = (base, p) => fetch(`${base}?${qs(p)}`, { signal: AbortSignal.timeout(20000) }).then(r => r.ok ? r.json() : Promise.reject(r.status));

let source = "";
async function loadRivers() {
  // Prefer the shared file (one fetch for everyone); fall back to asking the flood service directly.
  let list;
  const snap = await snapshot("rivers");
  if (snap) { list = snap.stations; source = snap.generated; }
  else {
    const j = await getJSON(FLOOD, {
      latitude: STATIONS.map(s => s.lat).join(","), longitude: STATIONS.map(s => s.lon).join(","),
      daily: "river_discharge,river_discharge_mean,river_discharge_p75,river_discharge_max", past_days: 7, forecast_days: 10,
    });
    const arr = Array.isArray(j) ? j : [j];
    list = STATIONS.map((s, k) => ({ ...s, daily: arr[k].daily }));
  }
  const t = today();
  return list.map(s => {
    const d = s.daily, i0 = Math.max(0, d.time.indexOf(t));
    let pi = i0;
    for (let i = i0; i < d.time.length; i++) if ((d.river_discharge[i] ?? -1) > (d.river_discharge[pi] ?? -1)) pi = i;
    const peak = d.river_discharge[pi];
    return { ...s, d, i0, pi, now: d.river_discharge[i0], peak, peakDate: d.time[pi],
      ratio: d.river_discharge_mean[pi] ? peak / d.river_discharge_mean[pi] : null,
      status: riverStatus(peak, d.river_discharge_mean[pi], d.river_discharge_max[pi]) };
  });
}

async function loadReservoirs() {
  let list = RESERVOIRS, daily; // daily: [{k, time, precipitation_sum}]
  const snap = await snapshot("reservoirs");
  if (snap) {
    list = snap.reservoirs;
    daily = list.flatMap((r, k) => r.points.map(p => ({ k, ...p })));
  } else {
    const pts = RESERVOIRS.flatMap((r, k) => r.catch.map(c => [k, ...c]));
    const j = await getJSON(OM, { latitude: pts.map(p => p[1]).join(","), longitude: pts.map(p => p[2]).join(","),
      daily: "precipitation_sum", past_days: 3, forecast_days: 4, timezone: "Asia/Colombo" });
    const arr = Array.isArray(j) ? j : [j];
    daily = arr.map((loc, i) => ({ k: pts[i][0], ...loc.daily }));
  }
  const sums = list.map(() => ({ past: [], next: [] }));
  daily.forEach(dd => {
    const k = dd.k, p = dd.precipitation_sum, t = dd.time.indexOf(today());
    const at = t < 0 ? 3 : t;
    sums[k].past.push(p.slice(at - 3, at).reduce((a, v) => a + (v || 0), 0));
    sums[k].next.push(p.slice(at, at + 3).reduce((a, v) => a + (v || 0), 0));
  });
  const avg = a => a.reduce((s, v) => s + v, 0) / a.length;
  return list.map((r, k) => { const past = avg(sums[k].past), next = avg(sums[k].next); return { ...r, past, next, status: reservoirStatus(past, next) }; });
}

const cssVar = v => getComputedStyle(document.documentElement).getPropertyValue(v.slice(4, -1)).trim() || "#888";

let map, L, rivers = [], reservoirs = [], built = false, selected = null;

async function loadLeaflet() {
  if (window.L) return window.L;
  const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "assets/vendor/leaflet/leaflet.css"; document.head.appendChild(css);
  await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "assets/vendor/leaflet/leaflet.js"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  return window.L;
}

function drawMap() {
  if (!map) {
    const LK = [[5.88, 79.62], [9.86, 81.92]];
    map = L.map("rv-map", { minZoom: 7, maxZoom: 11, zoomSnap: 0.25, scrollWheelZoom: false, maxBounds: [[4.8, 78.6], [10.9, 82.9]] });
    map.fitBounds(LK, { padding: [6, 6] });
    new ResizeObserver(() => { map.invalidateSize(); if (!map._lwMoved) map.fitBounds(LK, { padding: [6, 6] }); }).observe($("rv-map"));
    map.on("dragstart", () => { map._lwMoved = true; });
    map.getContainer().querySelector(".leaflet-control-zoom").addEventListener("click", () => { map._lwMoved = true; });
    L.tileLayer(`${GIBS}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`,
      { maxNativeZoom: 8, attribution: 'Basemap: NASA Blue Marble via <a href="https://earthdata.nasa.gov/gibs">GIBS</a>' }).addTo(map);
    L.tileLayer(`${GIBS}/Coastlines_15m/default/GoogleMapsCompatible_Level13/{z}/{y}/{x}.png`, { maxNativeZoom: 9 }).addTo(map);
    map.attributionControl.addAttribution('Rivers: Copernicus GloFAS via Open-Meteo');
  }
  map.eachLayer(l => { if (l.options?.lw) map.removeLayer(l); });
  for (const r of reservoirs) {
    const col = cssVar(STATUS[r.status[0]]);
    L.marker([r.lat, r.lon], { lw: 1, icon: L.divIcon({ className: "", iconSize: [12, 12], iconAnchor: [6, 6], html: `<div style="width:8px;height:8px;margin:2px;background:${col};outline:1.5px solid rgba(255,255,255,.9);transform:rotate(45deg)"></div>` }) })
      .bindTooltip(`<b>${r.name} reservoir</b><br>${r.status[1]}<br>Rain upstream: ${Math.round(r.past)} mm past 3 days, ${Math.round(r.next)} mm next 3 days`).addTo(map);
  }
  for (const s of rivers) {
    const on = s === selected;
    L.circleMarker([s.lat, s.lon], { lw: 1, radius: on ? 7.5 : 5, weight: on ? 2.5 : 1.5, color: on ? "#111" : "rgba(255,255,255,.9)", fillColor: cssVar(STATUS[s.status[0]]), fillOpacity: 1 })
      .bindTooltip(`<b>${s.river}</b> at ${s.name}<br>${s.status[1]}<br>Peak ${r0(s.peak)} m³/s ${fmtD(s.peakDate)}`).on("click", () => select(s)).addTo(map);
  }
}

function select(s) {
  selected = s;
  if (map) drawMap();
  const d = s.d;
  $("rv-detail").innerHTML = `<h2 style="margin:0 0 4px">${s.river} at ${s.name}</h2>
    <p class="sub" style="margin:0 0 8px">${badge(...s.status)} Peak modelled flow in the next 10 days: <b>${r0(s.peak)} m³/s</b> on ${fmtD(s.peakDate)}${s.ratio ? `, ${s.ratio.toFixed(1)}× the long-term average for that date` : ""}.</p>
    ${s.official ? `<p class="small" style="margin:0 0 8px">Official water-level thresholds at this gauge: alert ${s.official.alert} ${s.official.unit}, minor flood ${s.official.minor} ${s.official.unit}, major flood ${s.official.major} ${s.official.unit}. The current reading is on <a href="https://rivernet.lk" rel="noopener">rivernet.lk</a>.</p>` : ""}
    <div id="rv-chart"></div>
    <p class="muted small" style="margin:8px 0 0">Modelled river flow (Copernicus GloFAS, ~5 km grid), not the gauge reading.${s.regulated ? " This river is controlled by reservoirs, which the model doesn't simulate, so treat it as a rough guide." : ""}</p>`;
  fill($("rv-chart"), h => bars($("rv-chart"), { categories: d.time.map(t => t.slice(5)), height: h, min: 0, fmt: v => r0(v), yTitle: "m³/s",
    tipTitle: i => fmtD(d.time[i]) + (i < s.i0 ? " (past)" : i === s.i0 ? " (today)" : " (forecast)"),
    series: [{ name: "Flow (past week + 10-day forecast)", color: "var(--series-1)", values: d.river_discharge },
             { name: "Long-term average", color: "var(--neutral)", kind: "line", values: d.river_discharge_mean },
             { name: "Wettest year on record", color: "var(--critical)", kind: "line", values: d.river_discharge_max }] }), { base: 280, max: 620 });
  fitSoon();
  $("rv-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function tables() {
  const order = { critical: 0, serious: 1, warning: 2, good: 3, neutral: 4 };
  const rs = [...rivers].sort((a, b) => order[a.status[0]] - order[b.status[0]] || (b.ratio ?? 0) - (a.ratio ?? 0));
  $("rv-rivers").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>River</th><th>Station</th><th>Outlook (10 days)</th>
    <th class="num">Flow now</th><th class="num">Peak</th><th>Peak day</th><th class="num">× average</th></tr></thead><tbody>
    ${rs.map(s => `<tr data-k="${STATIONS.indexOf(STATIONS.find(x => x.name === s.name))}" style="cursor:pointer"><td>${s.river}</td><td>${s.name}</td><td>${badge(...s.status)}</td>
      <td class="num">${r0(s.now)} m³/s</td><td class="num">${r0(s.peak)} m³/s</td><td>${fmtD(s.peakDate)}</td><td class="num">${s.ratio ? s.ratio.toFixed(1) + "×" : "–"}</td></tr>`).join("")}
    </tbody></table></div><p class="sub small" style="margin:8px 0 0">${RIVER_RULE} × average = the highest modelled flow in the next 10 days ÷ the long-term average flow for that date.</p>`;
  $("rv-rivers").querySelectorAll("tr[data-k]").forEach(tr => tr.addEventListener("click", () => select(rivers[+tr.dataset.k])));
  const ro = [...reservoirs].sort((a, b) => order[a.status[0]] - order[b.status[0]] || b.next - a.next);
  $("rv-res").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>Reservoir</th><th>River</th><th>Upstream rain</th>
    <th class="num">Past 3 days</th><th class="num">Next 3 days</th><th>Operated by</th></tr></thead><tbody>
    ${ro.map(r => `<tr><td>${r.name}</td><td>${r.river}</td><td>${badge(...r.status)}</td><td class="num">${Math.round(r.past)} mm</td><td class="num">${Math.round(r.next)} mm</td><td class="small">${r.by}</td></tr>`).join("")}
    </tbody></table></div>`;
}

export async function renderRivers() {
  if (built) { map?.invalidateSize(); return; }
  built = true;
  L = await loadLeaflet();
  $("rv-status").textContent = "Loading river and rain data…";
  const [rv, rs] = await Promise.allSettled([loadRivers(), loadReservoirs()]);
  rivers = rv.status === "fulfilled" ? rv.value : [];
  reservoirs = rs.status === "fulfilled" ? rs.value : [];
  $("rv-status").textContent = !(rivers.length || reservoirs.length) ? "Couldn't reach the river and rain services. Try again in a minute."
    : source ? `Data updated ${new Date(source).toLocaleString("en-GB", { timeZone: "Asia/Colombo", weekday: "short", hour: "2-digit", minute: "2-digit" })} Sri Lanka time (refreshed every 3 hours; the river model itself updates once a day).` : "";
  if (!rivers.length) $("rv-rivers").innerHTML = `<p class="muted small">River data unavailable right now.</p>`;
  if (!reservoirs.length) $("rv-res").innerHTML = `<p class="muted small">Reservoir rain data unavailable right now.</p>`;
  drawMap();
  if (rivers.length || reservoirs.length) tables();
  const worst = rivers.find(s => s.status[0] !== "good");
  if (rivers.length) select(worst || rivers[0]);
}
