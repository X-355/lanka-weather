// Live sky: animated satellite clouds and rain over Sri Lanka, with the district forecast on top.
// Sources (all free, keyless, called straight from the browser):
//   EUMETSAT EUMETView WMS — Meteosat IODC (Indian Ocean) imagery and H SAF rain rate, every 15 min
//   NASA GIBS WMTS — GPM IMERG Early 30-min precipitation (≈5 h behind, but better calibrated)
import { DISTRICTS, VIEWBOX } from "./sri-lanka-districts.js?v=dev";

const EUM = "https://view.eumetsat.int/geoserver";
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const BASES = {
  ir: { layer: "msg_iodc:ir108", label: "Clouds (infrared, day & night)", opacity: 0.75 },
  natural: { layer: "msg_iodc:rgb_naturalenhncd", label: "True colour (daytime only)", opacity: 1 },
  none: null,
};
const RAIN_EUM = "msg_iodc:h63";
const IMERG = "IMERG_Precipitation_Rate_30min";
const FRAMES = 12;

// Geo-reference of the district SVG (fitted so all 25 district capitals fall in their own district).
const FIT = { lon0: 79.65, lon1: 81.88, lat0: 5.9, lat1: 9.83, xmin: 0.25713, xmax: 449.41727, ymin: 0.26075, ymax: 792.26105 };
const merc = l => Math.log(Math.tan(Math.PI / 4 + (l * Math.PI) / 360));
const unmerc = m => ((2 * Math.atan(Math.exp(m)) - Math.PI / 2) * 180) / Math.PI;
function svgToLatLng(x, y) {
  const lon = FIT.lon0 + ((x - FIT.xmin) / (FIT.xmax - FIT.xmin)) * (FIT.lon1 - FIT.lon0);
  const m = merc(FIT.lat1) - ((y - FIT.ymin) / (FIT.ymax - FIT.ymin)) * (merc(FIT.lat1) - merc(FIT.lat0));
  return [unmerc(m), lon];
}

let map, L, state, ui = {}, frames = [], idx = 0, timer = null, base = "ir", rain = "eum", tint = true, built = false;
const layers = { base: [], rain: [] };

async function loadLeaflet() {
  if (window.L) return window.L;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "assets/vendor/leaflet/leaflet.css";
  document.head.appendChild(css);
  await new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "assets/vendor/leaflet/leaflet.js";
    s.onload = res; s.onerror = rej;
    document.head.appendChild(s);
  });
  return window.L;
}

async function eumTimes(layer) {
  const [ws, name] = layer.split(":");
  const t = await fetch(`${EUM}/${ws}/${name}/ows?service=WMS&version=1.3.0&request=GetCapabilities`).then(r => r.text());
  const m = t.match(/<Dimension[^>]*name="time"[^>]*>([^<]*)</);
  const end = new Date(m[1].trim().split("/")[1]);
  return [...Array(FRAMES)].map((_, i) => new Date(end - (FRAMES - 1 - i) * 15 * 60e3));
}

async function imergTimes() {
  const now = new Date(), start = new Date(now - 24 * 3600e3), iso = d => d.toISOString().slice(0, 19) + "Z";
  const t = await fetch(`${GIBS}/1.0.0/${IMERG}/default/GoogleMapsCompatible_Level6/all/${iso(start)}--${iso(now)}.xml`).then(r => r.text());
  const m = t.match(/<Domain>[^<]*\/([^/<]+)\/PT30M<\/Domain>/);
  const end = new Date(m[1]);
  return [...Array(8)].map((_, i) => new Date(end - (7 - i) * 30 * 60e3));
}

const iso = d => d.toISOString().slice(0, 19) + "Z";

function eumLayer(layer, time, opacity) {
  const l = L.tileLayer.wms(`${EUM}/wms`, {
    layers: layer, styles: "", format: "image/png", transparent: true, version: "1.3.0", time: iso(time),
    opacity: 0, attribution: "Meteosat imagery © EUMETSAT",
  });
  l.maxOpacity = opacity;
  return l;
}

function imergLayer(time) {
  const l = L.tileLayer(`${GIBS}/${IMERG}/default/${iso(time)}/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`, {
    maxNativeZoom: 6, opacity: 0, attribution: 'Rain: NASA GPM IMERG via <a href="https://earthdata.nasa.gov/gibs">GIBS</a>',
  });
  l.maxOpacity = 0.85;
  return l;
}

function show(i) {
  idx = i;
  for (const group of [layers.base, layers.rain])
    group.forEach((l, j) => l.setOpacity(j === i ? l.maxOpacity : 0));
  const t = frames[i];
  const mins = Math.round((Date.now() - t) / 60e3);
  const ago = mins < 90 ? `${mins} min ago` : `${(mins / 60).toFixed(1)} h ago`;
  ui.time.innerHTML = `<b>${t.toLocaleString("en-GB", { timeZone: "Asia/Colombo", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</b> Sri Lanka time · ${ago}`;
  ui.slider.value = i;
}

function play(on) {
  clearInterval(timer);
  timer = on ? setInterval(() => show((idx + 1) % frames.length), 700) : null;
  ui.play.textContent = on ? "❚❚ Pause" : "▶ Play";
  ui.play.setAttribute("aria-pressed", on);
}

async function rebuild() {
  play(false);
  [...layers.base, ...layers.rain].forEach(l => map.removeLayer(l));
  layers.base = []; layers.rain = [];
  ui.time.textContent = "Loading imagery…";
  try {
    frames = rain === "imerg" ? await imergTimes() : await eumTimes(BASES[base]?.layer || RAIN_EUM);
  } catch (e) {
    ui.time.textContent = "Couldn't reach the imagery service. Try again in a few minutes.";
    return;
  }
  if (BASES[base]) layers.base = frames.map(t => eumLayer(BASES[base].layer, t, BASES[base].opacity).addTo(map));
  if (rain === "eum") layers.rain = frames.map(t => eumLayer(RAIN_EUM, t, 0.9).addTo(map));
  if (rain === "imerg") layers.rain = frames.map(t => imergLayer(t).addTo(map));
  ui.slider.max = frames.length - 1;
  ui.note.innerHTML = rain === "imerg"
    ? "Rain: NASA IMERG Early, a multi-satellite estimate calibrated with microwave sensors. More reliable than the 15-minute estimate, but published about 5 hours late, so these frames show this morning, not now."
    : rain === "eum"
    ? "Rain: EUMETSAT H SAF satellite estimate (infrared, adjusted with microwave passes), every 15 minutes. It's an estimate from space, not radar. Light drizzle and small showers are often missed."
    : "";
  show(frames.length - 1);
  play(true);
}

function districtsOverlay() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", VIEWBOX);
  const f = state.forecast, byTown = Object.fromEntries((f?.towns || []).map(t => [t.key ?? t.town, t]));
  for (const [key, d] of Object.entries(DISTRICTS)) {
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const r = byTown[key];
    p.setAttribute("d", d);
    p.setAttribute("fill", tint && r ? `color-mix(in oklab, var(--series-1) ${Math.round(10 + 80 * r.p_rain)}%, transparent)` : "transparent");
    p.setAttribute("fill-opacity", tint && r ? 0.45 : 0);
    p.setAttribute("stroke", "#ffffff");
    p.setAttribute("stroke-opacity", 0.85);
    p.setAttribute("stroke-width", 1.2);
    const name = state.meta.towns.find(t => t.key === key)?.name ?? key;
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = r ? `${name}: ${Math.round(100 * r.p_rain)}% chance of rain tomorrow` : name;
    p.appendChild(title);
    svg.appendChild(p);
  }
  const [vx, vy, vw, vh] = VIEWBOX.split(" ").map(Number);
  const sw = svgToLatLng(vx, vy + vh), ne = svgToLatLng(vx + vw, vy);
  return L.svgOverlay(svg, [sw, ne], { interactive: true, attribution: "Districts: MapSVG / svg-maps (CC BY 4.0)" });
}

let districtLayer = null;
function refreshDistricts() {
  if (districtLayer) map.removeLayer(districtLayer);
  districtLayer = districtsOverlay().addTo(map);
}

export async function renderLiveSky(appState) {
  state = appState;
  if (built) { map.invalidateSize(); return; }
  built = true;
  L = await loadLeaflet();
  ui = { time: document.getElementById("ls-time"), slider: document.getElementById("ls-slider"), play: document.getElementById("ls-play"),
         note: document.getElementById("ls-note") };
  map = L.map("ls-map", { zoomControl: true, minZoom: 5, maxZoom: 10 }).setView([7.85, 80.7], 7);
  // Keyless, public-domain basemap from NASA GIBS: Blue Marble relief + coastlines on top of everything.
  L.tileLayer(`${GIBS}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`, {
    maxNativeZoom: 8, maxZoom: 10, attribution: 'Basemap: NASA Blue Marble via <a href="https://earthdata.nasa.gov/gibs">GIBS</a>',
  }).addTo(map);
  map.createPane("coast").style.zIndex = 450;
  L.tileLayer(`${GIBS}/Coastlines_15m/default/GoogleMapsCompatible_Level13/{z}/{y}/{x}.png`, { pane: "coast", maxZoom: 10 }).addTo(map);
  refreshDistricts();

  const seg = (id, val, set) => document.getElementById(id).addEventListener("click", e => {
    const v = e.target.dataset.v; if (!v) return;
    set(v);
    [...document.getElementById(id).children].forEach(b => b.setAttribute("aria-pressed", b.dataset.v === v));
  });
  seg("ls-base", base, v => { base = v; rebuild(); });
  seg("ls-rain", rain, v => { rain = v; rebuild(); });
  document.getElementById("ls-tint").addEventListener("change", e => { tint = e.target.checked; refreshDistricts(); });
  ui.slider.addEventListener("input", () => { play(false); show(+ui.slider.value); });
  ui.play.addEventListener("click", () => play(!timer));
  await rebuild();
}
