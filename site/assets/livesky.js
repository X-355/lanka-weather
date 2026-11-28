// Live sky: animated satellite clouds and rain over Sri Lanka, with the district forecast on top.
// Sources (all free, keyless, called straight from the browser):
//   EUMETSAT EUMETView WMS — Meteosat IODC (Indian Ocean) imagery and H SAF rain rate, every 15 min
//   NASA GIBS WMTS — GPM IMERG Early 30-min precipitation (≈5 h behind, but better calibrated)
//   Open-Meteo Forecast — next 12 hours of model cloud cover and rain on a 0.3° grid ("future" frames)
import { DISTRICTS, VIEWBOX } from "./sri-lanka-districts.js?v=dev";

const EUM = "https://view.eumetsat.int/geoserver";
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const BASES = {
  // Same infrared picture, recoloured in the browser by brightness (= cloud-top coldness = height):
  // tall, cold storm tops stand out in yellow → red → magenta, like the "enhanced" satellite maps on weather sites.
  enhanced: { layer: "msg_iodc:ir108", label: "Storm clouds (enhanced infrared)", opacity: 0.9, pane: "cloudsColor", colour: true },
  // Infrared is an opaque greyscale image; "screen" blending drops the dark clear sky and keeps bright cloud tops.
  ir: { layer: "msg_iodc:ir108", label: "Clouds (infrared, day & night)", opacity: 1, pane: "clouds" },
  natural: { layer: "msg_iodc:rgb_naturalenhncd", label: "True colour (daytime only)", opacity: 1, pane: "imagery" },
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

const FC_HOURS = 12;
const FC_GRID = { lat0: 4.9, lat1: 10.9, lon0: 78.4, lon1: 83.2, step: 0.3 };

// frames: [{ t: Date, fc: bool, layers: [Leaflet layers] }] — past satellite frames, then forecast hours.
let map, L, state, ui = {}, frames = [], idx = 0, timer = null, base = "enhanced", rain = "none", tint = true, future = true, built = false;
let fcCache = null, liveTimer = null, lastObs = 0;

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

function eumLayer(layer, time, opacity, pane = "rain") {
  const l = L.tileLayer.wms(`${EUM}/wms`, {
    layers: layer, styles: "", format: "image/png", transparent: true, version: "1.3.0", time: iso(time),
    opacity: 0, pane, attribution: "Meteosat imagery © EUMETSAT",
  });
  l.maxOpacity = opacity;
  return l;
}

function imergLayer(time) {
  const l = L.tileLayer(`${GIBS}/${IMERG}/default/${iso(time)}/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`, {
    maxNativeZoom: 6, opacity: 0, pane: "rain", attribution: 'Rain: NASA GPM IMERG via <a href="https://earthdata.nasa.gov/gibs">GIBS</a>',
  });
  l.maxOpacity = 0.85;
  return l;
}

// Colour table for the enhanced infrared picture. EUMETView's IR10.8 greyscale is brighter for colder cloud tops,
// so brightness stands in for cloud-top height. Breakpoints were set by eye on Sri Lankan scenes; they are bands of
// "how tall", not calibrated temperatures.
const STOPS = [ // [grey value, r, g, b, alpha]
  [70, 255, 255, 255, 0], [150, 225, 230, 238, 0.7], [151, 150, 200, 240, 0.8], [178, 60, 140, 230, 0.85],
  [179, 50, 200, 90, 0.9], [196, 255, 220, 20, 0.92], [197, 255, 150, 20, 0.95], [220, 240, 50, 40, 0.97], [221, 215, 60, 240, 1], [255, 255, 200, 255, 1],
];
const LUT = (() => {
  const lut = new Uint8ClampedArray(256 * 4);
  for (let v = 0; v < 256; v++) {
    let k = STOPS.findIndex(s => s[0] >= v);
    if (k <= 0) { if (k === 0) { lut.set([0, 0, 0, 0], v * 4); continue; } k = STOPS.length - 1; }
    const a = STOPS[k - 1], b = STOPS[k], f = (v - a[0]) / Math.max(1, b[0] - a[0]);
    const c = i => a[i] + (b[i] - a[i]) * f;
    lut.set([c(1), c(2), c(3), c(4) * 255], v * 4);
  }
  return lut;
})();
export const CLOUD_LEGEND = [["Low / thin", "rgb(225,230,238)"], ["Raised", "rgb(60,140,230)"], ["Tall", "rgb(255,220,20)"], ["Storm tops", "rgb(240,50,40)"], ["Severe", "rgb(215,60,240)"]];

function enhancedLayer(time, opacity) {
  const Grid = L.GridLayer.extend({
    createTile(coords, done) {
      const tile = document.createElement("canvas");
      tile.width = tile.height = 256;
      const b = this._tileCoordsToBounds(coords);
      const sw = L.CRS.EPSG3857.project(b.getSouthWest()), ne = L.CRS.EPSG3857.project(b.getNorthEast());
      // Ask for about one pixel per satellite pixel (~4 km over Sri Lanka) and let the browser smooth the upscale;
      // asking for more makes the server repeat pixels, which shows up as hard square blocks.
      const px = Math.max(24, Math.min(256, Math.round((ne.x - sw.x) / 1000 / 4)));
      const url = `${EUM}/wms?service=WMS&version=1.3.0&request=GetMap&layers=msg_iodc:ir108&styles=&format=image/png&transparent=true` +
        `&crs=EPSG:3857&bbox=${sw.x},${sw.y},${ne.x},${ne.y}&width=${px}&height=${px}&time=${iso(time)}`;
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        const ctx = tile.getContext("2d", { willReadFrequently: true });
        ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
        ctx.drawImage(img, 0, 0, 256, 256);
        const im = ctx.getImageData(0, 0, 256, 256), d = im.data;
        for (let i = 0; i < d.length; i += 4) {
          if (!d[i + 3]) continue;
          const o = d[i] * 4;
          d[i] = LUT[o]; d[i + 1] = LUT[o + 1]; d[i + 2] = LUT[o + 2]; d[i + 3] = LUT[o + 3];
        }
        ctx.putImageData(im, 0, 0);
        done(null, tile);
      };
      img.onerror = () => done(null, tile);
      img.src = url;
      return tile;
    },
  });
  const l = new Grid({ opacity: 0, pane: "cloudsColor", attribution: "Meteosat imagery © EUMETSAT" });
  l.maxOpacity = opacity;
  return l;
}

// ---- "future" frames: next 12 h of model cloud and rain on a coarse grid, smoothed by the browser when scaled up.
const RAIN_STOPS = [[0.2, [120, 190, 255]], [1, [40, 120, 240]], [4, [255, 215, 0]], [10, [255, 130, 0]], [25, [230, 30, 30]], [50, [200, 40, 220]]];
export const RAIN_LEGEND = [["0.2", "rgb(120,190,255)"], ["1", "rgb(40,120,240)"], ["4", "rgb(255,215,0)"], ["10", "rgb(255,130,0)"], ["25", "rgb(230,30,30)"], ["50+ mm/h", "rgb(200,40,220)"]];
function rainColour(mm) {
  let c = null;
  for (const [v, rgb] of RAIN_STOPS) if (mm >= v) c = rgb;
  return c;
}

async function loadForecast() {
  if (fcCache && Date.now() - fcCache.at < 30 * 60e3) return fcCache;
  const G = FC_GRID, lats = [], lons = [];
  for (let la = G.lat0; la <= G.lat1 + 1e-9; la += G.step) lats.push(+la.toFixed(3));
  for (let lo = G.lon0; lo <= G.lon1 + 1e-9; lo += G.step) lons.push(+lo.toFixed(3));
  const pts = lats.flatMap(la => lons.map(lo => [la, lo]));
  const q = new URLSearchParams({ latitude: pts.map(p => p[0]).join(","), longitude: pts.map(p => p[1]).join(","),
    hourly: "precipitation,cloud_cover", forecast_hours: FC_HOURS + 1, timezone: "GMT" });
  const j = await fetch(`https://api.open-meteo.com/v1/forecast?${q}`, { signal: AbortSignal.timeout(25000) }).then(r => r.ok ? r.json() : Promise.reject(r.status));
  const arr = Array.isArray(j) ? j : [j];
  const times = arr[0].hourly.time.map(t => new Date(t + "Z"));
  fcCache = { at: Date.now(), lats, lons, times, arr };
  return fcCache;
}

function forecastLayer(fc, h) {
  const W = fc.lons.length, H = fc.lats.length, c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d"), im = ctx.createImageData(W, H);
  fc.arr.forEach((loc, k) => {
    const row = Math.floor(k / W), col = k % W, y = H - 1 - row, o = (y * W + col) * 4;
    const mm = loc.hourly.precipitation[h] ?? 0, cc = loc.hourly.cloud_cover[h] ?? 0, rc = rainColour(mm);
    if (rc) im.data.set([...rc, 215], o);
    else im.data.set([235, 240, 248, cc < 30 ? 0 : Math.round(((cc - 30) / 70) * 115)], o);
  });
  ctx.putImageData(im, 0, 0);
  // Scale up smoothly, then fade the edges so the forecast blends into the map instead of ending in a hard box.
  const K = 12, big = document.createElement("canvas");
  big.width = W * K; big.height = H * K;
  const g = big.getContext("2d");
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
  g.drawImage(c, 0, 0, big.width, big.height);
  g.globalCompositeOperation = "destination-in";
  for (const horiz of [true, false]) {
    const len = horiz ? big.width : big.height, gr = horiz ? g.createLinearGradient(0, 0, len, 0) : g.createLinearGradient(0, 0, 0, len);
    gr.addColorStop(0, "rgba(0,0,0,0)"); gr.addColorStop(0.14, "rgba(0,0,0,1)"); gr.addColorStop(0.86, "rgba(0,0,0,1)"); gr.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = gr; g.fillRect(0, 0, big.width, big.height);
  }
  const s = FC_GRID.step / 2;
  const l = L.imageOverlay(big.toDataURL(), [[fc.lats[0] - s, fc.lons[0] - s], [fc.lats.at(-1) + s, fc.lons.at(-1) + s]],
    { opacity: 0, pane: "forecast", className: "fc-smooth", attribution: "Forecast: Open-Meteo" });
  l.maxOpacity = 0.85;
  return l;
}

const fmtT = t => t.toLocaleString("en-GB", { timeZone: "Asia/Colombo", weekday: "short", hour: "2-digit", minute: "2-digit" });

function show(i) {
  idx = i;
  frames.forEach((f, j) => f.layers.forEach(l => l.setOpacity(j === i ? l.maxOpacity : 0)));
  const f = frames[i];
  if (!f) return;
  const mins = Math.round((Date.now() - f.t) / 60e3);
  if (f.fc) {
    const h = Math.max(1, Math.round(-mins / 60));
    ui.time.innerHTML = `<span class="tag fc">FORECAST</span> <b>${fmtT(f.t)}</b> · in ${h} h`;
  } else {
    const latest = !frames[i + 1] || frames[i + 1].fc;
    const ago = mins < 90 ? `${mins} min ago` : `${(mins / 60).toFixed(1)} h ago`;
    ui.time.innerHTML = `${latest && mins < 90 ? '<span class="tag live">● LIVE</span> ' : ""}<b>${fmtT(f.t)}</b> Sri Lanka time · ${ago}`;
  }
  ui.slider.value = i;
  const nObs = frames.filter(x => !x.fc).length;
  ui.slider.style.setProperty("--now", nObs < frames.length ? `${(100 * (nObs - 0.5)) / Math.max(1, frames.length - 1)}%` : "200%");
}

function play(on) {
  clearTimeout(timer);
  timer = null;
  if (on) {
    const step = () => {
      const next = (idx + 1) % frames.length;
      show(next);
      // pause a moment on "now" and at the end of the loop, so the eye can catch up
      const nowFrame = frames[next] && !frames[next].fc && (!frames[next + 1] || frames[next + 1].fc);
      timer = setTimeout(step, nowFrame || next === frames.length - 1 ? 1600 : frames[next]?.fc ? 850 : 600);
    };
    timer = setTimeout(step, 600);
  }
  ui.play.textContent = on ? "❚❚ Pause" : "▶ Play";
  ui.play.setAttribute("aria-pressed", on);
}

async function rebuild(keepPlaying = true) {
  play(false);
  frames.forEach(f => f.layers.forEach(l => map.removeLayer(l)));
  frames = [];
  ui.time.textContent = "Loading imagery…";
  let times = [];
  try {
    times = rain === "imerg" ? await imergTimes() : await eumTimes(BASES[base]?.layer || RAIN_EUM);
  } catch (e) {
    ui.time.textContent = "Couldn't reach the satellite service. Try again in a few minutes.";
  }
  lastObs = times.length ? +times.at(-1) : 0;
  frames = times.map(t => {
    const ls = [];
    if (BASES[base]) ls.push(BASES[base].colour ? enhancedLayer(t, BASES[base].opacity) : eumLayer(BASES[base].layer, t, BASES[base].opacity, BASES[base].pane));
    if (rain === "eum") ls.push(eumLayer(RAIN_EUM, t, 0.9));
    if (rain === "imerg") ls.push(imergLayer(t));
    ls.forEach(l => l.addTo(map));
    return { t, fc: false, layers: ls };
  });
  if (future) {
    try {
      const fc = await loadForecast();
      fc.times.forEach((t, h) => {
        if (t <= Date.now() + 20 * 60e3) return; // the satellite covers "now"; forecast starts at the next full hour
        frames.push({ t, fc: true, layers: [forecastLayer(fc, h).addTo(map)] });
      });
    } catch (e) { /* forecast frames are optional */ }
  }
  if (!frames.length) return;
  // Wait until every frame's pictures have downloaded, so the first loop doesn't flash empty frames.
  const tiled = frames.flatMap(f => f.layers).filter(l => l.isLoading);
  let done = 0;
  const progress = () => { ui.time.textContent = `Loading pictures… ${done} of ${tiled.length}`; };
  progress();
  await Promise.all(tiled.map(l => new Promise(res => {
    const fin = () => { done++; progress(); res(); };
    if (!l.isLoading()) return fin();
    const t = setTimeout(fin, 15000);
    l.once("load", () => { clearTimeout(t); fin(); });
  })));
  ui.slider.max = frames.length - 1;
  ui.legend.innerHTML = legendHTML();
  ui.note.innerHTML = rain === "imerg"
    ? "Rain: NASA IMERG Early, a multi-satellite estimate calibrated with microwave sensors. More reliable than the 15-minute estimate, but published about 5 hours late, so these frames show the sky roughly 5–9 hours ago, not now."
    : rain === "eum"
    ? "Rain: EUMETSAT H SAF satellite estimate (infrared, adjusted with microwave passes), every 15 minutes. It's an estimate from space, not radar. Light drizzle and small showers are often missed."
    : "";
  if (future) ui.note.innerHTML += (ui.note.innerHTML ? " " : "") + "After “now”, the animation continues with the next 12 hours of forecast cloud and rain from a weather model (Open-Meteo, ~30 km grid). Forecast frames are smoother and less certain than the satellite frames before them.";
  const nowIdx = frames.filter(f => !f.fc).length - 1;
  show(Math.max(0, nowIdx));
  if (keepPlaying) play(true);
}

function legendHTML() {
  const sw = (c, t) => `<span><i style="background:${c}"></i>${t}</span>`;
  let h = "";
  if (BASES[base]?.colour) h += `<div><b>Cloud tops</b> ${CLOUD_LEGEND.map(([t, c]) => sw(c, t)).join("")}</div>`;
  if (future) h += `<div><b>Forecast rain</b> ${RAIN_LEGEND.map(([t, c]) => sw(c, t)).join("")}</div>`;
  return h;
}

// Every 5 minutes, look for a newer satellite picture and add it without disturbing the viewer.
function startLive() {
  clearInterval(liveTimer);
  liveTimer = setInterval(async () => {
    if (document.hidden || location.hash.replace("#", "") !== "live" || rain === "imerg") return;
    try {
      const t = await eumTimes(BASES[base]?.layer || RAIN_EUM);
      if (+t.at(-1) > lastObs) {
        const playing = !!timer, wasLatest = frames[idx] && !frames[idx].fc && (!frames[idx + 1] || frames[idx + 1].fc);
        await rebuild(playing);
        if (!playing && !wasLatest) show(Math.min(idx, frames.length - 1));
      } else if (frames[idx] && !frames[idx].fc) show(idx); // refresh the "x min ago" text
    } catch (e) { /* try again next time */ }
  }, 5 * 60e3);
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
         note: document.getElementById("ls-note"), legend: document.getElementById("ls-legend") };
  // Scroll wheel scrolls the page, not the map; zoom with the +/− buttons or a pinch.
  map = L.map("ls-map", { zoomControl: true, minZoom: 5, maxZoom: 10, scrollWheelZoom: false }).setView([7.85, 80.7], 7);
  // Keyless, public-domain basemap from NASA GIBS: Blue Marble relief + coastlines on top of everything.
  L.tileLayer(`${GIBS}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`, {
    maxNativeZoom: 8, maxZoom: 10, attribution: 'Basemap: NASA Blue Marble via <a href="https://earthdata.nasa.gov/gibs">GIBS</a>',
  }).addTo(map);
  map.createPane("imagery").style.zIndex = 250;
  const clouds = map.createPane("clouds");
  clouds.style.zIndex = 300;
  clouds.style.mixBlendMode = "screen";
  map.createPane("cloudsColor").style.zIndex = 300;
  map.createPane("forecast").style.zIndex = 340;
  map.createPane("rain").style.zIndex = 350;
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
  document.getElementById("ls-future").addEventListener("change", e => { future = e.target.checked; rebuild(); });
  ui.slider.addEventListener("input", () => { play(false); show(+ui.slider.value); });
  ui.play.addEventListener("click", () => play(!timer));
  await rebuild();
  startLive();
}
