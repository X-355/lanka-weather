// Weather tab: the full picture for any district capital, town, or the visitor's own location.
import { fill, fitSoon } from "./fit.js?v=dev";
// Everything is fetched live in the browser from free, keyless services, and each card names its source:
//   Open-Meteo Forecast (ECMWF and other national models), Air Quality (Copernicus CAMS),
//   Flood (Copernicus GloFAS), Marine; plus our own model's chance of rain and GDACS cyclones (nightly).
import { bars } from "./charts.js?v=dev";

const OM = "https://api.open-meteo.com/v1/forecast";
const AQ = "https://air-quality-api.open-meteo.com/v1/air-quality";
const FLOOD = "https://flood-api.open-meteo.com/v1/flood";
const MARINE = "https://marine-api.open-meteo.com/v1/marine";
const TZ = "Asia/Colombo";
const $ = id => document.getElementById(id);
const pct = x => (x == null ? "–" : `${Math.round(100 * x)}%`);
const r0 = x => (x == null ? "–" : Math.round(x));
const r1 = x => (x == null ? "–" : (Math.round(x * 10) / 10).toString());

// WMO weather codes -> words + a simple symbol.
const WMO = {
  0: ["Clear sky", "☀️"], 1: ["Mainly clear", "🌤️"], 2: ["Partly cloudy", "⛅"], 3: ["Overcast", "☁️"],
  45: ["Fog", "🌫️"], 48: ["Fog", "🌫️"], 51: ["Light drizzle", "🌦️"], 53: ["Drizzle", "🌦️"], 55: ["Heavy drizzle", "🌧️"],
  61: ["Light rain", "🌦️"], 63: ["Rain", "🌧️"], 65: ["Heavy rain", "🌧️"], 80: ["Light showers", "🌦️"],
  81: ["Showers", "🌧️"], 82: ["Violent showers", "⛈️"], 95: ["Thunderstorm", "⛈️"], 96: ["Thunderstorm, hail", "⛈️"], 99: ["Thunderstorm, hail", "⛈️"],
};
const wmo = c => WMO[c] || ["–", ""];
const compass = d => d == null ? "" : ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(d / 45) % 8];

// Status scales (colour always comes with a label).
const STATUS = { good: "var(--good)", warning: "var(--warning)", serious: "var(--serious)", critical: "var(--critical)" };
const badge = (level, text) => `<span class="status"><span class="dot" style="background:${STATUS[level]}"></span>${text}</span>`;

function aqiLevel(a) {  // US EPA AQI categories
  if (a == null) return ["good", "–", ""];
  if (a <= 50) return ["good", "Good", "Air quality is fine for everyone."];
  if (a <= 100) return ["warning", "Moderate", "Fine for most people. Unusually sensitive people may want to cut back long or heavy outdoor exertion."];
  if (a <= 150) return ["serious", "Unhealthy for sensitive groups", "Children, older adults and people with asthma or heart or lung disease should reduce long or heavy outdoor exertion."];
  if (a <= 200) return ["critical", "Unhealthy", "Everyone should reduce long or heavy outdoor exertion; sensitive groups should avoid it."];
  if (a <= 300) return ["critical", "Very unhealthy", "Avoid long or heavy outdoor exertion. Sensitive groups should stay indoors."];
  return ["critical", "Hazardous", "Everyone should avoid outdoor exertion."];
}
function uvLevel(u) {  // WHO UV index bands
  if (u == null) return ["good", "–", ""];
  if (u < 3) return ["good", "Low", "No protection needed."];
  if (u < 6) return ["warning", "Moderate", "Shade around midday; hat and sunscreen."];
  if (u < 8) return ["serious", "High", "Shade 10am–3pm, cover up, SPF 30+."];
  if (u < 11) return ["critical", "Very high", "Avoid the midday sun; full protection."];
  return ["critical", "Extreme", "Stay out of the sun around midday."];
}
function heatLevel(t) {  // feels-like temperature, after the US NWS heat index bands
  if (t == null) return ["good", "–", ""];
  if (t < 27) return ["good", "Comfortable", ""];
  if (t < 32) return ["warning", "Caution", "Fatigue is possible with long exposure and activity. Drink water."];
  if (t < 41) return ["serious", "Extreme caution", "Heat cramps and exhaustion are possible. Rest in shade, drink often."];
  if (t < 54) return ["critical", "Danger", "Heat exhaustion likely, heatstroke possible. Limit outdoor work."];
  return ["critical", "Extreme danger", "Heatstroke likely. Avoid exertion outdoors."];
}

// Moon: mean synodic month from a known new moon (2000-01-06 18:14 UTC). Good to about a day.
const SYNODIC = 29.530588853, NEW_MOON = Date.UTC(2000, 0, 6, 18, 14);
function moon(d = new Date()) {
  const age = (((d - NEW_MOON) / 864e5) % SYNODIC + SYNODIC) % SYNODIC;
  const names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"];
  const icons = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"];
  const i = Math.round(age / SYNODIC * 8) % 8;
  const toFull = ((SYNODIC / 2 - age) % SYNODIC + SYNODIC) % SYNODIC;
  const nextFull = new Date(d.getTime() + toFull * 864e5);
  return { name: names[i], icon: icons[i], illum: (1 - Math.cos(2 * Math.PI * age / SYNODIC)) / 2, nextFull };
}

const fmtTime = s => s ? new Date(s).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "–";
const fmtDay = (s, i) => i === 0 ? "Today" : i === 1 ? "Tomorrow" : new Date(s + "T00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
const q = (base, params) => fetch(`${base}?${new URLSearchParams(params)}`, { signal: AbortSignal.timeout(15000) }).then(r => r.ok ? r.json() : Promise.reject(r.status));

let state, places = [], built = false, cyclones = null, current = null;

const title = k => k.split("_").map(w => w[0].toUpperCase() + w.slice(1)).join(" ");

function placeList(gaz, townList) {
  const caps = state.meta.towns.map(t => ({ id: `d:${t.key}`, name: t.name, lat: t.lat, lon: t.lon, district: t.key,
    label: `${t.name} — ${t.name} District (capital)`, alt: "" }));
  let rows = gaz?.rows || (townList || []).map(p => [p.name, "", "", p.district, "", p.lat, p.lon]);
  const seen = {};
  for (const r of rows) seen[`${r[0]}|${r[3]}`] = (seen[`${r[0]}|${r[3]}`] || 0) + 1;
  const ps = rows.map(([name, si, ta, district, postcode, lat, lon, src]) => {
    const dup = seen[`${name}|${district}`] > 1;
    return { id: `g:${name}|${district}|${postcode}|${lat}`, name, lat, lon, district, osm: src === "o" || src === "g", gn: src === "g", si, ta,
             label: `${name}${dup ? ` (${postcode || lat.toFixed(2) + "°N"})` : ""} — ${title(district)} District`, alt: [si, ta].filter(Boolean).join(" · ") };
  });
  // A postal/OSM entry for a district capital itself (same name, within 3 km) would just duplicate it.
  const near = (a, b) => Math.abs(a.lat - b.lat) < 0.03 && Math.abs(a.lon - b.lon) < 0.03;
  const keep = ps.filter(p => !caps.some(c => c.name.toLowerCase() === p.name.toLowerCase() && near(c, p)));
  for (const c of caps) {  // lend the capital its Sinhala/Tamil names
    const twin = ps.find(p => p.name.toLowerCase() === c.name.toLowerCase() && near(c, p) && (p.si || p.ta));
    if (twin) { c.si = twin.si; c.ta = twin.ta; c.alt = twin.alt; }
  }
  return [...caps, ...keep];
}

const LAST = "lw-weather-place";
const remember = id => { try { localStorage.setItem(LAST, id); } catch {} };
const recall = () => { try { return localStorage.getItem(LAST); } catch { return null; } };

// Lower-case, drop Latin accents, keep letters, digits and combining marks (Sinhala/Tamil vowel signs matter).
const fold = t => (t || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^\p{L}\p{N}\p{M}]/gu, "")
  // English spellings of Sinhala/Tamil names vary: Thudella/Tudella, Kapuwaththa/Kapuwatta, Kandy/Kandi…
  .replace(/([a-z])h/g, (m, c) => "tdkgbpc".includes(c) ? c : m).replace(/([a-z])\1+/g, "$1").replace(/v/g, "w");
function populateSelect() {
  for (const p of places) p._k = [fold(p.name), fold(p.si), fold(p.ta)];
}

function search(qs) {
  const q = fold(qs);
  if (!q) return [];
  const hits = [];
  for (const p of places) {
    let score = -1;
    for (const k of p._k) {
      if (!k) continue;
      if (k === q) score = Math.max(score, 3);
      else if (k.startsWith(q)) score = Math.max(score, 2);
      else if (k.includes(q)) score = Math.max(score, 1);
    }
    if (score < 0) continue;
    // capitals first, then postal towns, then OpenStreetMap villages
    hits.push([score * 10 + (p.id.startsWith("d:") ? 3 : p.osm ? 0 : 1), p]);
  }
  return hits.sort((a, b) => b[0] - a[0] || a[1].name.length - b[1].name.length).slice(0, 12).map(h => h[1]);
}

let sugg = [], sel = -1;
function showSuggestions(list) {
  sugg = list; sel = list.length ? 0 : -1;
  const box = $("wx-suggest");
  box.innerHTML = list.map((p, i) => `<button type="button" role="option" data-i="${i}" aria-selected="${i === sel}">
    <span>${p.name}</span> <span class="muted small">${title(p.district)} District${p.alt ? " · " + p.alt : ""}${p.gn ? " · GN division" : p.osm ? " · village" : ""}</span></button>`).join("")
    || ($("wx-place").value.trim() ? `<div class="muted small" style="padding:8px 10px">No match. Try another spelling, or use your location.</div>` : "");
  box.style.display = box.innerHTML ? "" : "none";
}
function highlight(i) {
  sel = i;
  [...$("wx-suggest").children].forEach((b, j) => b.setAttribute?.("aria-selected", j === i));
}

const byLabel = label => places.find(p => p.label === label);

function nearestDistrict(lat, lon) {
  let best = null, bd = 1e9;
  for (const p of places.filter(p => p.id.startsWith("d:") || p.id.startsWith("g:"))) { const d = (p.lat - lat) ** 2 + ((p.lon - lon) * Math.cos(lat * Math.PI / 180)) ** 2; if (d < bd) { bd = d; best = p; } }
  return best;
}

function ourForecast(place) {
  const f = state.forecast;
  if (!f) return null;
  const km = (a, b) => Math.hypot((a.lat - b.lat) * 111, (a.lon - b.lon) * 111 * Math.cos(a.lat * Math.PI / 180));
  let best = null, bd = 8;
  for (const p of f.places || []) { const d = km(place, p); if (d < bd) { bd = d; best = p; } }
  if (best) return { p: best.p_rain, ph: best.p_heavy, target: f.target_date,
    label: best.name === place.name ? best.name : `${best.name}, the nearest forecast town (${bd < 1 ? "<1" : Math.round(bd)} km away)` };
  const d = f.towns.find(t => t.town === place.district);
  return d ? { p: d.p_rain, ph: d.p_heavy, clim: d.p_clim, target: f.target_date, label: `${d.name} district` } : null;
}

async function load(place) {
  current = place;
  const panels = ["wx-now", "wx-hourly", "wx-daily", "wx-air", "wx-health", "wx-sun", "wx-sea", "wx-river"];
  panels.forEach(id => $(id).innerHTML = `<p class="muted small">Loading…</p>`);
  const common = { latitude: place.lat, longitude: place.lon, timezone: TZ };
  const [fc, aq, fl, mr] = await Promise.allSettled([
    q(OM, { ...common, forecast_days: 7,
      current: "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day",
      hourly: "temperature_2m,apparent_temperature,precipitation_probability,precipitation,weather_code,uv_index",
      daily: "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_sum,precipitation_probability_max,uv_index_max,sunrise,sunset,daylight_duration,wind_speed_10m_max" }),
    q(AQ, { ...common, forecast_days: 4, current: "us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,dust", hourly: "us_aqi" }),
    q(FLOOD, { latitude: place.lat, longitude: place.lon, forecast_days: 10, past_days: 7,
      daily: "river_discharge,river_discharge_mean,river_discharge_p75,river_discharge_max" }),
    q(MARINE, { ...common, forecast_days: 3, current: "wave_height,wave_direction,wave_period,sea_surface_temperature", daily: "wave_height_max" }),
  ]);
  if (current !== place) return;  // the visitor picked another place meanwhile
  renderNow(place, fc.value);
  renderHourly(fc.value);
  renderDaily(fc.value);
  renderAir(aq.value);
  renderHealth(fc.value, aq.value);
  renderSun(fc.value);
  renderSea(mr.value);
  renderRiver(fl.value, place);
  renderCyclones();
}

const fail = (id, what) => { $(id).innerHTML = `<p class="muted small">Couldn't load ${what} right now. Try again in a minute.</p>`; };

function renderNow(place, f) {
  if (!f) return fail("wx-now", "the forecast");
  const c = f.current, [word, icon] = wmo(c.weather_code), ours = ourForecast(place);
  const tomorrowP = f.daily.precipitation_probability_max?.[1];
  $("wx-now").innerHTML = `
    <div style="display:flex;flex-wrap:wrap;gap:18px 32px;align-items:center">
      <div><div class="muted small">${place.name} · now (${fmtTime(c.time)})</div>
        <div style="display:flex;align-items:center;gap:10px"><span style="font-size:46px" aria-hidden="true">${icon}</span>
          <span style="font-size:48px;font-weight:650;letter-spacing:-0.03em">${r0(c.temperature_2m)}°C</span></div>
        <div>${word} · feels like <b>${r0(c.apparent_temperature)}°C</b></div></div>
      <div class="grid kpis" style="flex:1;min-width:280px">
        <div class="kpi"><div class="label">Humidity</div><div class="value" style="font-size:20px">${r0(c.relative_humidity_2m)}%</div></div>
        <div class="kpi"><div class="label">Wind</div><div class="value" style="font-size:20px">${r0(c.wind_speed_10m)} km/h ${compass(c.wind_direction_10m)}</div><div class="note">gusts ${r0(c.wind_gusts_10m)} km/h</div></div>
        <div class="kpi"><div class="label">Cloud cover</div><div class="value" style="font-size:20px">${r0(c.cloud_cover)}%</div></div>
        <div class="kpi"><div class="label">UV index</div><div class="value" style="font-size:20px">${r1(c.uv_index)}</div><div class="note">${uvLevel(c.uv_index)[1]}</div></div>
      </div>
    </div>
    ${ours ? `<div class="card" style="margin-top:14px;background:var(--surface-2);border:0">
      <b>Rain tomorrow (${new Date(ours.target + "T00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" })})</b>:
      our model says <b>${pct(ours.p)}</b>${ours.clim != null ? ` (normal for the date ${pct(ours.clim)})` : ""}; the professional forecast's chance is <b>${tomorrowP == null ? "–" : tomorrowP + "%"}</b>.
      <span class="muted small">Ours is for ${ours.label}; see the Forecast checker tab for how each is tested.</span></div>` : ""}
    <p class="muted small" style="margin:8px 0 0">Conditions and forecast: <a href="https://open-meteo.com">Open-Meteo</a> (ECMWF and other national weather models). The model uses its own grid, so "now" means the model's latest estimate, not a station reading.</p>`;
}

function renderHourly(f) {
  if (!f) return fail("wx-hourly", "the hourly forecast");
  const now = Date.now(), h = f.hourly;
  let start = h.time.findIndex(t => new Date(t).getTime() >= now - 3600e3);
  if (start < 0) start = 0;
  const idx = [...Array(48).keys()].map(i => start + i).filter(i => i < h.time.length);
  const cats = idx.map(i => { const d = new Date(h.time[i]); return d.getHours() === 0 ? d.toLocaleDateString("en-GB", { weekday: "short" }) : `${d.getHours()}:00`; });
  const tip = i => new Date(h.time[idx[i]]).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  $("wx-hourly").innerHTML = `<h3 style="margin:0 0 4px;font-size:15px">Temperature (°C)</h3><div id="wx-h-t"></div>
    <h3 style="margin:14px 0 4px;font-size:15px">Chance of rain (%)</h3><div id="wx-h-p"></div>`;
  // Start compact; fit.js grows both charts to fill the box when the Air quality box beside it is taller.
  fill($("wx-h-t"), ht => bars($("wx-h-t"), { categories: cats, height: ht, fmt: v => v.toFixed(0), tipTitle: tip,
    min: Math.floor(Math.min(...idx.map(i => h.temperature_2m[i])) - 2),
    series: [{ name: "Temperature", color: "var(--series-2)", kind: "line", values: idx.map(i => h.temperature_2m[i]) },
             { name: "Feels like", color: "var(--series-3)", kind: "line", values: idx.map(i => h.apparent_temperature[i]) }] }),
    { base: 160, max: 320, weight: 1.3 });
  fill($("wx-h-p"), hp => bars($("wx-h-p"), { categories: cats, height: hp, fmt: v => v.toFixed(0), min: 0, max: 100, tipTitle: tip,
    series: [{ name: "Chance of rain", color: "var(--series-1)", values: idx.map(i => h.precipitation_probability[i]) }] }),
    { base: 120, max: 260, weight: 1 });
  fitSoon();
}

function renderDaily(f) {
  if (!f) return fail("wx-daily", "the 7-day forecast");
  const d = f.daily;
  $("wx-daily").innerHTML = `<div class="table-scroll"><table class="data"><thead><tr><th>Day</th><th>Sky</th><th class="num">High / low</th><th class="num">Feels like</th>
    <th class="num">Chance of rain</th><th class="num">Rain</th><th class="num">Max wind</th><th class="num">UV</th></tr></thead><tbody>
    ${d.time.map((t, i) => `<tr><td>${fmtDay(t, i)}</td><td>${wmo(d.weather_code[i])[1]} ${wmo(d.weather_code[i])[0]}</td>
      <td class="num"><b>${r0(d.temperature_2m_max[i])}°</b> / ${r0(d.temperature_2m_min[i])}°</td><td class="num">${r0(d.apparent_temperature_max[i])}°</td>
      <td class="num">${d.precipitation_probability_max[i] ?? "–"}%</td><td class="num">${r1(d.precipitation_sum[i])} mm</td>
      <td class="num">${r0(d.wind_speed_10m_max[i])} km/h</td><td class="num">${r1(d.uv_index_max[i])}</td></tr>`).join("")}
    </tbody></table></div><p class="muted small" style="margin:8px 0 0">Source: Open-Meteo (ECMWF and other national weather models).</p>`;
}

function renderAir(a) {
  if (!a) return fail("wx-air", "air quality");
  const c = a.current, [lvl, label, advice] = aqiLevel(c.us_aqi);
  // daily max AQI for the next 4 days
  const days = {};
  a.hourly.time.forEach((t, i) => { const k = t.slice(0, 10); days[k] = Math.max(days[k] ?? 0, a.hourly.us_aqi[i] ?? 0); });
  const keys = Object.keys(days).slice(0, 4);
  $("wx-air").innerHTML = `
    <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
      <div><div class="muted small">US AQI now</div><div style="font-size:40px;font-weight:650">${r0(c.us_aqi)}</div>${badge(lvl, label)}</div>
      <p style="flex:1;min-width:220px;margin:0">${advice}</p>
    </div>
    <div class="pairs">
      <div><span>PM2.5 (fine particles)</span><b>${r1(c.pm2_5)} µg/m³</b></div><div><span>PM10</span><b>${r1(c.pm10)} µg/m³</b></div>
      <div><span>Ozone (O₃)</span><b>${r0(c.ozone)} µg/m³</b></div><div><span>Nitrogen dioxide (NO₂)</span><b>${r1(c.nitrogen_dioxide)} µg/m³</b></div>
      <div><span>Sulphur dioxide (SO₂)</span><b>${r1(c.sulphur_dioxide)} µg/m³</b></div><div><span>Carbon monoxide (CO)</span><b>${r0(c.carbon_monoxide)} µg/m³</b></div>
    </div>
    <div class="chips">${keys.map((k, i) => { const [l, n] = aqiLevel(days[k]); return `<div class="card"><div class="muted small">${fmtDay(k, i)}</div><b>${days[k]}</b> ${badge(l, n)}</div>`; }).join("")}</div>
    <p class="muted small" style="margin:8px 0 0">Model estimate from <a href="https://atmosphere.copernicus.eu">Copernicus CAMS</a> (~10–40 km grid) via Open-Meteo, not a ground monitor. City-street pollution can be higher than shown.</p>`;
}

function renderHealth(f, a) {
  if (!f) return fail("wx-health", "health indices");
  const d = f.daily, c = f.current;
  const heat = heatLevel(d.apparent_temperature_max[0]), uv = uvLevel(d.uv_index_max[0]);
  const aqi = a ? aqiLevel(Math.max(...a.hourly.us_aqi.slice(0, 24).filter(v => v != null))) : null;
  const rainP = d.precipitation_probability_max[0] ?? 0;
  const outdoor = rainP >= 70 || (aqi && aqi[0] === "critical") || heat[0] === "critical" ? ["serious", "Poor"]
    : rainP >= 40 || (aqi && aqi[0] === "serious") || heat[0] === "serious" ? ["warning", "Fair"] : ["good", "Good"];
  const laundry = rainP < 30 && c.relative_humidity_2m < 80 ? ["good", "Good"] : rainP < 60 ? ["warning", "Fair"] : ["serious", "Poor"];
  // Mosquito activity rises with warmth, humidity and recent rain (rough guide; relevant to dengue prevention).
  const wetWeek = (f.hourly.precipitation.slice(0, 24).reduce((s, v) => s + (v || 0), 0)) > 1 || rainP >= 50;
  const mosq = d.temperature_2m_max[0] >= 25 && c.relative_humidity_2m >= 70 && wetWeek ? ["serious", "High"]
    : d.temperature_2m_max[0] >= 22 && c.relative_humidity_2m >= 60 ? ["warning", "Moderate"] : ["good", "Low"];
  const rows = [
    ["Heat stress (feels like " + r0(d.apparent_temperature_max[0]) + "°C)", heat],
    ["UV (max " + r1(d.uv_index_max[0]) + ")", uv],
    ["Air quality (worst hour today)", aqi || ["good", "–", ""]],
    ["Outdoor activities", [...outdoor, `Based on rain chance (${rainP}%), heat and air quality.`]],
    ["Drying clothes outside", [...laundry, "Based on rain chance and humidity."]],
    ["Mosquito activity", [...mosq, "Rough guide from warmth, humidity and rain. Clear standing water to help prevent dengue."]],
  ];
  $("wx-health").innerHTML = `<table class="data"><tbody>${rows.map(([n, [l, label, adv]]) =>
    `<tr><td>${n}</td><td style="white-space:nowrap">${badge(l, label)}</td><td class="small muted">${adv || ""}</td></tr>`).join("")}</tbody></table>
    <p class="muted small" style="margin:8px 0 0">Today. These are rules of thumb from the forecast (US NWS heat bands, WHO UV bands, US EPA AQI), not medical advice.</p>`;
}

function renderSun(f) {
  const m = moon();
  const d = f?.daily;
  const dl = d ? d.daylight_duration[0] : null;
  $("wx-sun").innerHTML = `<div class="grid kpis">
    <div class="kpi"><div class="label">Sunrise</div><div class="value" style="font-size:22px">${d ? fmtTime(d.sunrise[0]) : "–"}</div></div>
    <div class="kpi"><div class="label">Sunset</div><div class="value" style="font-size:22px">${d ? fmtTime(d.sunset[0]) : "–"}</div><div class="note">${dl ? `${Math.floor(dl / 3600)} h ${Math.round(dl % 3600 / 60)} min of daylight` : ""}</div></div>
    <div class="kpi"><div class="label">Moon</div><div class="value" style="font-size:22px">${m.icon} ${m.name}</div><div class="note">${Math.round(m.illum * 100)}% lit</div></div>
    <div class="kpi"><div class="label">Next full moon (Poya)</div><div class="value" style="font-size:22px">${m.nextFull.toLocaleDateString("en-GB", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" })}</div><div class="note">Astronomical estimate; the official Poya holiday can differ by a day.</div></div>
  </div><div id="wx-sun-uv" class="fill-bottom"></div>`;
  // Spare height in this box: the week's peak UV index (WHO: 8–10 very high, 11+ extreme). Hidden if there's no room.
  if (d?.uv_index_max) {
    const days = d.time.map(t => new Date(t + "T00:00").toLocaleDateString("en-GB", { weekday: "short" }));
    fill($("wx-sun-uv"), hh => {
      if (hh < 1) return;
      $("wx-sun-uv").innerHTML = `<h3 style="margin:12px 0 2px;font-size:14px">Peak UV index, next 7 days</h3>`;
      const box = document.createElement("div"); $("wx-sun-uv").appendChild(box);
      bars(box, { categories: days, height: Math.max(80, hh - 64), min: 0, fmt: v => v.toFixed(0),
        series: [{ name: "Peak UV index (8–10 very high, 11+ extreme)", color: "var(--series-2)", values: d.uv_index_max }] });
    }, { base: 0, min: 0, max: 320, hideBelow: 150 });
    fitSoon();
  }
}

function renderSea(m) {
  const c = m?.current;
  if (!c || c.wave_height == null) { $("wx-sea-card").style.display = "none"; return; }
  $("wx-sea-card").style.display = "";
  const maxW = Math.max(...(m.daily.wave_height_max || []).filter(v => v != null));
  const lvl = maxW >= 3.5 ? ["critical", "Very rough"] : maxW >= 2.5 ? ["serious", "Rough"] : maxW >= 1.5 ? ["warning", "Moderate"] : ["good", "Calm to slight"];
  $("wx-sea").innerHTML = `<div class="grid kpis">
    <div class="kpi"><div class="label">Waves now</div><div class="value" style="font-size:22px">${r1(c.wave_height)} m</div><div class="note">every ${r0(c.wave_period)} s from the ${compass(c.wave_direction)}</div></div>
    <div class="kpi"><div class="label">Highest waves, next 3 days</div><div class="value" style="font-size:22px">${r1(maxW)} m</div><div class="note">${badge(...lvl)}</div></div>
    <div class="kpi"><div class="label">Sea temperature</div><div class="value" style="font-size:22px">${r1(c.sea_surface_temperature)}°C</div></div>
  </div><p class="muted small" style="margin:8px 0 0">Open-sea wave model (Open-Meteo Marine), not surf at the beach. Fishermen: follow the Department of Meteorology's sea-area warnings.</p>`;
}

function renderRiver(fl, place) {
  const d = fl?.daily;
  if (!d || !d.river_discharge?.some(v => v != null)) {
    $("wx-river").innerHTML = `<p class="muted small">No river data for this spot.</p>`; return;
  }
  const todayIdx = d.time.indexOf(new Date().toLocaleDateString("en-CA", { timeZone: TZ }));
  const i0 = todayIdx >= 0 ? todayIdx : 7;
  const fut = d.river_discharge.slice(i0), peak = Math.max(...fut.filter(v => v != null)), pi = i0 + fut.indexOf(peak);
  const ratio = d.river_discharge_mean[pi] ? peak / d.river_discharge_mean[pi] : null;
  const lvl = d.river_discharge_max[pi] && peak > d.river_discharge_max[pi] ? ["critical", "Higher than any year on record for the date"]
    : d.river_discharge_p75[pi] && peak > d.river_discharge_p75[pi] ? ["serious", "Higher than usual"] : ["good", "Within the usual range"];
  $("wx-river").innerHTML = `<p style="margin:0 0 8px">${badge(...lvl)} Peak flow in the next 10 days: <b>${r0(peak)} m³/s</b> on ${new Date(d.time[pi] + "T00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}${ratio ? ` (${r1(ratio)}× the long-term average for the date)` : ""}.</p><div id="wx-river-chart"></div>
    <p class="muted small" style="margin:8px 0 0">Copernicus <a href="https://global-flood.emergency.copernicus.eu">GloFAS</a> river model, nearest ~5 km river cell to ${place.name}. It may be a small stream rather than the main river, and it is a model, not a gauge. For flood warnings, follow the Irrigation Department and the Disaster Management Centre.</p>`;
  fill($("wx-river-chart"), hr => bars($("wx-river-chart"), { categories: d.time.map(t => t.slice(5)), height: hr, fmt: v => v.toFixed(0), yTitle: "m³/s", min: 0,
    series: [{ name: "River flow (past week + forecast)", color: "var(--series-1)", values: d.river_discharge },
             { name: "Long-term average", color: "var(--neutral)", kind: "line", values: d.river_discharge_mean },
             { name: "Wettest year on record", color: "var(--critical)", kind: "line", values: d.river_discharge_max }] }), { base: 200, max: 480 });
  fitSoon();
}

function renderCyclones() {
  const c = cyclones;
  if (!c) { $("wx-cyclone").innerHTML = `<p class="muted small">Cyclone check runs nightly; no data yet.</p>`; return; }
  if (!c.ok) { $("wx-cyclone").innerHTML = `<p class="muted small">The nightly cyclone check couldn't reach GDACS last time.</p>`; return; }
  const active = c.events.filter(e => e.ongoing);
  $("wx-cyclone").innerHTML = active.length
    ? active.map(e => `<p style="margin:0 0 6px">${badge(e.alert === "Red" ? "critical" : e.alert === "Orange" ? "serious" : "warning", `${e.alert} alert`)} <b>${e.name}</b>, ${e.severity || ""}. ${e.url ? `<a href="${e.url}" rel="noopener">GDACS report</a>` : ""}</p>`).join("")
    : `<p style="margin:0">${badge("good", "None active")} No tropical cyclones in the region around Sri Lanka in the last two weeks.</p>`;
  $("wx-cyclone").innerHTML += `<p class="muted small" style="margin:8px 0 0">Checked ${new Date(c.fetched_at).toLocaleString("en-GB", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" })} from <a href="https://www.gdacs.org">GDACS</a>, which compiles agency advisories. Official cyclone warnings come from the Department of Meteorology.</p>`;
}

const LOCATE_HELP = {
  1: "Location permission is blocked for this site. Allow it via the padlock or site-settings icon in the address bar, then try again. On Windows, also check Settings → Privacy & security → Location. (The browser inside the Claude app doesn't share location at all; open the site in Chrome, Edge or Safari.)",
  2: "Your device couldn't work out where it is. On a computer, turn on location services (Windows: Settings → Privacy & security → Location); on a phone, turn on Location.",
  3: "Finding your location took too long. Try again, ideally with Wi-Fi or location services on.",
};

function locateMsg(text) {
  $("wx-locate-msg").textContent = text;
  $("wx-locate-msg").style.display = text ? "" : "none";
}

function locate() {
  const btn = $("wx-locate");
  locateMsg("");
  if (!("geolocation" in navigator) || !window.isSecureContext) {
    locateMsg("This browser can't share your location. Pick the nearest town from the list instead.");
    return;
  }
  btn.textContent = "Locating…";
  btn.disabled = true;
  const done = () => { btn.textContent = "📍 Use my location"; btn.disabled = false; };
  const ok = pos => {
    done();
    const { latitude: lat, longitude: lon } = pos.coords;
    const near = nearestDistrict(lat, lon);
    const inLanka = lat > 5.7 && lat < 10.1 && lon > 79.4 && lon < 82.1;
    if (!inLanka) locateMsg("You seem to be outside Sri Lanka. Showing your location anyway; the rain-model comparison uses the nearest district.");
    $("wx-place").value = "📍 Your location";
    load({ id: near.id, name: inLanka ? `Your location (near ${near.name})` : "Your location", lat, lon, district: near.district });
  };
  // A quick, low-accuracy fix is plenty for weather; retry once more patiently if it times out.
  navigator.geolocation.getCurrentPosition(ok, err => {
    if (err.code === 3) {
      navigator.geolocation.getCurrentPosition(ok, e2 => { done(); locateMsg(LOCATE_HELP[e2.code] || "Couldn't get your location."); },
        { enableHighAccuracy: true, timeout: 20000, maximumAge: 600000 });
    } else { done(); locateMsg(LOCATE_HELP[err.code] || "Couldn't get your location."); }
  }, { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 });
}

export async function renderWeather(appState) {
  state = appState;
  if (built) return;
  built = true;
  let gaz = null, townList = null;
  try { gaz = await fetch("data/gazetteer.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : null); } catch {}
  if (!gaz) { try { townList = await fetch("data/places.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : null); } catch {} }
  places = placeList(gaz, townList);
  populateSelect();
  try { cyclones = await fetch("data/cyclones.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : null); } catch { cyclones = null; }
  const choose = p => {
    if (!p) return;
    $("wx-place").value = p.label;
    $("wx-suggest").style.display = "none";
    remember(p.label);
    $("wx-place").blur();
    load(p);
  };
  const inp = $("wx-place");
  inp.addEventListener("input", () => showSuggestions(search(inp.value)));
  inp.addEventListener("focus", () => inp.select());
  inp.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" && sugg.length) { e.preventDefault(); highlight((sel + 1) % sugg.length); }
    else if (e.key === "ArrowUp" && sugg.length) { e.preventDefault(); highlight((sel - 1 + sugg.length) % sugg.length); }
    else if (e.key === "Enter") { e.preventDefault(); choose(sugg[sel] || search(inp.value)[0]); }
    else if (e.key === "Escape") $("wx-suggest").style.display = "none";
  });
  $("wx-suggest").addEventListener("mousedown", e => { const b = e.target.closest("button"); if (b) { e.preventDefault(); choose(sugg[+b.dataset.i]); } });
  inp.addEventListener("blur", () => setTimeout(() => { $("wx-suggest").style.display = "none"; }, 150));
  $("wx-locate").addEventListener("click", () => { remember("here"); locate(); });
  // Open where the visitor was last time: their own location if they chose it (and already allowed it), else their last place.
  const last = recall();
  const geo = await navigator.permissions?.query({ name: "geolocation" }).then(p => p.state).catch(() => null);
  if (last === "here" && geo === "granted") return locate();
  const start = byLabel(last) || places.find(p => p.id === "d:colombo");
  $("wx-place").value = start.label;
  load(start);
}
