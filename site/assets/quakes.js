// Earthquakes & tsunami: live earthquakes around Sri Lanka and the Indian Ocean, and pointers to official
// tsunami warnings. This page never issues a tsunami warning: in Sri Lanka those come from the Department of
// Meteorology (National Tsunami Warning Centre) and the Disaster Management Centre.
// Data: USGS real-time GeoJSON feeds (free, no key, updated about every minute).

const USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary";
const BASEMAP = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const TZ = "Asia/Colombo";
const $ = id => document.getElementById(id);

// Region shown: the Indian Ocean around Sri Lanka.
export const REGION = { lon0: 40, lon1: 110, lat0: -15, lat1: 30 };
// Source zones that can send a tsunami to Sri Lanka, as used in national planning (the 2004 tsunami came from
// the Sunda–Andaman trench). Approximate boxes, for prompting people to check official bulletins.
export const ZONES = [
  { name: "Sunda–Andaman trench (Sumatra, Andaman & Nicobar)", lon0: 88, lon1: 106, lat0: -11, lat1: 16 },
  { name: "Makran trench (Pakistan–Iran coast)", lon0: 56, lon1: 68, lat0: 22, lat1: 27 },
];
// "Check official bulletins" rule: a large, shallow earthquake in a source zone (or USGS flags tsunami
// potential in the region). Magnitude 6.5+ shallow earthquakes under the sea can cause tsunamis.
export const CHECK_MAG = 6.5, CHECK_DEPTH = 100, CHECK_HOURS = 24;

// Points around Sri Lanka's coast (approximate) for "distance from Sri Lanka".
const COAST = [[9.82, 80.03], [9.35, 80.95], [8.57, 81.23], [7.72, 81.70], [6.85, 81.85], [6.12, 81.12], [5.92, 80.59],
  [6.05, 80.22], [6.58, 79.96], [6.93, 79.84], [7.45, 79.80], [8.03, 79.83], [8.78, 79.92], [9.30, 79.90]];

const km = (a, b, c, d) => {
  const p = Math.PI / 180, x = Math.sin((c - a) * p / 2) ** 2 + Math.cos(a * p) * Math.cos(c * p) * Math.sin((d - b) * p / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
};
export const distSL = (lat, lon) => Math.min(...COAST.map(([a, b]) => km(lat, lon, a, b)));
const inBox = (z, lat, lon) => lon >= z.lon0 && lon <= z.lon1 && lat >= z.lat0 && lat <= z.lat1;
export const zoneOf = (lat, lon) => ZONES.find(z => inBox(z, lat, lon))?.name || null;

export function parse(features) {
  return features.map(f => {
    const [lon, lat, depth] = f.geometry.coordinates, p = f.properties;
    return { id: f.id, mag: p.mag, place: p.place, time: p.time, lat, lon, depth, url: p.url, tsunami: p.tsunami === 1,
             alert: p.alert, status: p.status, dist: distSL(lat, lon), zone: zoneOf(lat, lon) };
  }).filter(q => q.mag != null && inBox(REGION, q.lat, q.lon));
}

/** Earthquakes in the last CHECK_HOURS that should prompt people to check official tsunami bulletins. */
export function needsCheck(qs, now = Date.now()) {
  return qs.filter(q => now - q.time < CHECK_HOURS * 3600e3 &&
    ((q.mag >= CHECK_MAG && q.depth <= CHECK_DEPTH && q.zone) || q.tsunami));
}

const fmtT = t => new Date(t).toLocaleString("en-GB", { timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const ago = t => { const m = Math.round((Date.now() - t) / 60e3); return m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };

export function checkBanner(hits) {
  if (!hits.length) return "";
  const q = hits.sort((a, b) => b.mag - a.mag)[0];
  return `<div class="notice quake-alert" role="alert"><b>Large earthquake: magnitude ${q.mag.toFixed(1)}, ${q.place}</b>, ${fmtT(q.time)} Sri Lanka time
    (${ago(q.time)}), about ${Math.round(q.dist).toLocaleString()} km from Sri Lanka, ${Math.round(q.depth)} km deep${q.zone ? `, in the ${q.zone}` : ""}.
    <b>This site does not assess tsunami threat.</b> Check the official bulletin now: <a href="https://www.meteo.gov.lk" rel="noopener">Department of Meteorology</a>
    · <a href="https://www.dmc.gov.lk" rel="noopener">Disaster Management Centre</a> · hotline <b>117</b>.
    If you are at the coast and feel strong shaking, or see the sea suddenly fall or rise, go to high ground now. Don't wait for a warning.</div>`;
}

let map, L, timer = null, built = false, qs = [];

async function loadLeaflet() {
  if (window.L) return window.L;
  const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "assets/vendor/leaflet/leaflet.css"; document.head.appendChild(css);
  await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "assets/vendor/leaflet/leaflet.js"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  return window.L;
}

async function fetchQuakes() {
  // M4.5+ for the whole region over a week, plus smaller ones (M2.5+) for anything near Sri Lanka.
  const [big, small] = await Promise.all(["4.5_week", "2.5_week"].map(f =>
    fetch(`${USGS}/${f}.geojson`, { cache: "no-cache", signal: AbortSignal.timeout(20000) }).then(r => r.ok ? r.json() : Promise.reject(r.status))));
  const all = new Map();
  for (const q of parse(big.features)) all.set(q.id, q);
  for (const q of parse(small.features)) if (q.dist < 600) all.set(q.id, q);
  return [...all.values()].sort((a, b) => b.time - a.time);
}

const colourOf = q => (Date.now() - q.time < 24 * 3600e3 ? "var(--critical)" : Date.now() - q.time < 3 * 86400e3 ? "var(--warning)" : "var(--neutral)");
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v.slice(4, -1)).trim() || "#888";

function drawMap() {
  if (!map) {
    map = L.map("eq-map", { minZoom: 3, maxZoom: 9, scrollWheelZoom: false, zoomSnap: 0.25 });
    map.fitBounds([[REGION.lat0, REGION.lon0], [REGION.lat1, REGION.lon1]], { padding: [4, 4] });
    L.tileLayer(`${BASEMAP}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`,
      { maxNativeZoom: 8, attribution: 'Basemap: NASA Blue Marble via <a href="https://earthdata.nasa.gov/gibs">GIBS</a>' }).addTo(map);
    map.attributionControl.addAttribution('Earthquakes: <a href="https://earthquake.usgs.gov">USGS</a>');
    for (const z of ZONES) {
      L.rectangle([[z.lat0, z.lon0], [z.lat1, z.lon1]], { color: "#fff", weight: 1, dashArray: "4 4", fill: false, interactive: false }).addTo(map)
        .bindTooltip(z.name, { permanent: false });
    }
    L.circleMarker([7.87, 80.77], { radius: 4, color: "#fff", weight: 2, fillOpacity: 0 }).addTo(map).bindTooltip("Sri Lanka");
  }
  map.eachLayer(l => { if (l.options?.eq) map.removeLayer(l); });
  for (const q of [...qs].reverse()) {
    L.circleMarker([q.lat, q.lon], { eq: 1, radius: Math.max(4, (q.mag - 2) * 3.2), color: "#fff", weight: 1.5,
      fillColor: css(colourOf(q)), fillOpacity: 0.9 })
      .bindTooltip(`<b>M${q.mag.toFixed(1)}</b> ${q.place}<br>${fmtT(q.time)} (${ago(q.time)})<br>${Math.round(q.depth)} km deep · ${Math.round(q.dist).toLocaleString()} km from Sri Lanka`)
      .addTo(map);
  }
}

function render() {
  const hits = needsCheck(qs);
  $("eq-check").innerHTML = hits.length ? checkBanner(hits)
    : `<div class="notice" style="border-left-color:var(--good)"><b>No large shallow earthquake in a tsunami source zone in the last ${CHECK_HOURS} hours.</b>
       This page only watches earthquakes; it is not a tsunami warning. Official warnings come from the Department of Meteorology and the DMC.</div>`;
  const near = qs.filter(q => q.dist < 600);
  $("eq-status").textContent = `Updated ${new Date().toLocaleTimeString("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit" })} Sri Lanka time from USGS; refreshes every 2 minutes while this page is open. ${qs.length} earthquakes in the region this week${near.length ? `, ${near.length} within 600 km of Sri Lanka` : ", none within 600 km of Sri Lanka"}.`;
  $("eq-list").innerHTML = qs.length ? `<div class="table-scroll"><table class="data"><thead><tr><th>When (Sri Lanka time)</th><th class="num">Magnitude</th><th class="num">Depth</th>
    <th>Where</th><th class="num">From Sri Lanka</th><th>Note</th></tr></thead><tbody>
    ${qs.map(q => `<tr class="${needsCheck([q]).length ? "hl" : ""}"><td>${fmtT(q.time)}<div class="muted small">${ago(q.time)}</div></td>
      <td class="num"><b>${q.mag.toFixed(1)}</b></td><td class="num">${Math.round(q.depth)} km</td>
      <td><a href="${q.url}" rel="noopener">${q.place}</a></td><td class="num">${Math.round(q.dist).toLocaleString()} km</td>
      <td class="small">${[q.zone ? "Tsunami source zone" : "", q.tsunami ? "USGS: tsunami potential flagged" : "", q.status === "automatic" ? "automatic solution (may be revised)" : ""].filter(Boolean).join(" · ") || "–"}</td></tr>`).join("")}
    </tbody></table></div>` : `<p class="muted">No earthquakes of magnitude 4.5 or more in the region this week.</p>`;
  drawMap();
}

async function refresh() {
  try {
    qs = await fetchQuakes();
    render();
  } catch (e) {
    $("eq-status").textContent = "Couldn't reach USGS just now; retrying in 2 minutes. For official information use the Department of Meteorology and the DMC.";
  }
}

export async function renderQuakes() {
  if (built) { map?.invalidateSize(); return; }
  built = true;
  L = await loadLeaflet();
  await refresh();
  clearInterval(timer);
  timer = setInterval(() => { if (location.hash.replace("#", "") === "quakes" && !document.hidden) refresh(); }, 120e3);
}
