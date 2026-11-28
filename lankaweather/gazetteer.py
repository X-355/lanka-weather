"""Place finder: postal towns (config/gazetteer.csv) + OpenStreetMap villages (config/osm_places.csv).

OpenStreetMap is refreshed monthly through the Overpass API. Every place is assigned to a district by
point-in-polygon against config/districts.geojson; places outside Sri Lanka (the query box clips a bit of
India) are dropped, and an OSM place is skipped when a postal place with the same name lies within 3 km.
OSM data © OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import json
import logging
import math
import re

import pandas as pd
import requests

from .config import ROOT

log = logging.getLogger(__name__)

OVERPASS = [  # busy public servers; try in turn
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
QUERY = """[out:json][timeout:240][bbox:5.85,79.45,9.9,81.95];
node[place~"^(city|town|suburb|village|hamlet|neighbourhood)$"][name];
out body;"""
OSM_CSV = ROOT / "config" / "osm_places.csv"
POSTAL_CSV = ROOT / "config" / "gazetteer.csv"
DISTRICTS = ROOT / "config" / "districts.geojson"


def _shapes():
    from shapely.geometry import shape
    g = json.loads(DISTRICTS.read_text())
    return {f["properties"]["district"]: shape(f["geometry"]) for f in g["features"]}


def district_of(shapes, lat: float, lon: float, coast_km: float = 2.0) -> str | None:
    """District containing the point; for points just off the coast (within coast_km) the nearest one."""
    from shapely.geometry import Point
    p = Point(lon, lat)
    for k, s in shapes.items():
        if s.contains(p):
            return k
    k = min(shapes, key=lambda k: shapes[k].distance(p))
    return k if shapes[k].distance(p) * 111 <= coast_km else None


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def fetch_osm() -> list[dict]:
    last = None
    for url in OVERPASS:
        try:
            r = requests.post(url, data={"data": QUERY}, timeout=300,
                              headers={"User-Agent": "lanka-weather/0.1 (+https://github.com/X-355/lanka-weather)"})
            r.raise_for_status()
            els = r.json()["elements"]
            log.info("Overpass %s: %d places", url, len(els))
            return els
        except Exception as e:
            log.warning("Overpass %s failed: %s", url, e)
            last = e
    raise RuntimeError(f"all Overpass servers failed: {last}")


def build_osm(elements: list[dict]) -> pd.DataFrame:
    shapes = _shapes()
    postal = pd.read_csv(POSTAL_CSV, dtype=str, keep_default_na=False)
    pindex: dict[str, list[tuple[float, float]]] = {}
    for r in postal.itertuples():
        pindex.setdefault(norm(r.name), []).append((float(r.lat), float(r.lon)))
    km = lambda a, b: math.hypot((a[0] - b[0]) * 111, (a[1] - b[1]) * 111 * math.cos(math.radians(a[0])))
    rows, outside, dup = [], 0, 0
    for e in elements:
        t = e.get("tags", {})
        name = (t.get("name:en") or t.get("name") or "").strip()
        if not name:
            continue
        lat, lon = e["lat"], e["lon"]
        d = district_of(shapes, lat, lon)
        if d is None:
            outside += 1
            continue
        if any(km((lat, lon), q) <= 3 for q in pindex.get(norm(name), [])):
            dup += 1
            continue
        rows.append({"name": name, "name_si": t.get("name:si", ""), "name_ta": t.get("name:ta", ""),
                     "district": d, "place": t.get("place", ""), "lat": round(lat, 5), "lon": round(lon, 5),
                     "osm_id": e["id"]})
    log.info("OSM: %d kept, %d outside Sri Lanka, %d already in the postal list", len(rows), outside, dup)
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates(["name", "district", "lat", "lon"]).sort_values(["district", "name"])
    return df


def refresh() -> dict:
    els = fetch_osm()
    df = build_osm(els)
    if len(df) < 1000:  # a truncated response would silently shrink the finder; keep last month's file
        raise RuntimeError(f"only {len(df)} OSM places after filtering — refusing to overwrite {OSM_CSV.name}")
    df.to_csv(OSM_CSV, index=False)
    return {"osm_places": len(df)}


# ----------------------------------------------------------------------------- GN divisions (official)

HDX_PACKAGE = "https://data.humdata.org/api/3/action/package_show?id=cod-ab-lka"
GN_CSV = ROOT / "config" / "gn_divisions.csv"
SINHALA = re.compile(r"[඀-෿]")
TAMIL = re.compile(r"[஀-௿]")


def _district_key(name: str) -> str | None:
    from .config import towns
    alias = {"moneragala": "monaragala", "nuwaraeliya": "nuwara_eliya", "mulativu": "mullaitivu", "mullativu": "mullaitivu"}
    n = re.sub(r"[^a-z]", "", (name or "").lower()).removesuffix("district")
    if n in alias:
        return alias[n]
    for t in towns():
        if re.sub(r"[^a-z]", "", t.name.lower()) == n:
            return t.key
    return None


def gn_from_shapefile(shp_path) -> pd.DataFrame:
    """One row per Grama Niladhari division: names (EN/SI/TA), official district, a point inside the polygon."""
    import shapefile  # pyshp
    from shapely.geometry import shape

    r = shapefile.Reader(str(shp_path))
    fields = [f[0] for f in r.fields[1:]]
    log.info("GN shapefile fields: %s", fields)
    low = {f.lower(): f for f in fields}
    pick = lambda *names: next((low[n] for n in names if n in low), None)
    f_en = pick("adm4_name", "adm4_en", "adm4_name_en", "adm4name")
    f_d = pick("adm2_name", "adm2_en", "adm2_name_en", "adm2name")
    f_code = pick("adm4_pcode", "adm4_pcod", "adm4pcode")
    if not f_en or not f_d:
        raise RuntimeError(f"unexpected GN fields: {fields}")
    rows = []
    for sr in r.iterShapeRecords():
        rec = sr.record.as_dict()
        en = str(rec[f_en]).strip()
        d = _district_key(str(rec[f_d]))
        if not en or not d or not sr.shape.points:
            continue
        si = next((str(v) for k, v in rec.items() if isinstance(v, str) and SINHALA.search(v) and k.lower().startswith("adm4")), "")
        ta = next((str(v) for k, v in rec.items() if isinstance(v, str) and TAMIL.search(v) and k.lower().startswith("adm4")), "")
        pt = shape(sr.shape.__geo_interface__).representative_point()
        rows.append({"name": en, "name_si": si, "name_ta": ta, "district": d, "pcode": rec.get(f_code, "") if f_code else "",
                     "lat": round(pt.y, 5), "lon": round(pt.x, 5)})
    return pd.DataFrame(rows)


def refresh_gn() -> dict:
    import tempfile
    import zipfile

    meta = requests.get(HDX_PACKAGE, timeout=60).json()["result"]
    res = next(x for x in meta["resources"] if x["name"].endswith(".shp.zip"))
    with tempfile.TemporaryDirectory() as tmp:
        zpath = f"{tmp}/adm.zip"
        with requests.get(res["url"], stream=True, timeout=600) as resp:
            resp.raise_for_status()
            with open(zpath, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            adm4 = [n for n in names if re.search(r"adm(in)?_?4[^/]*\.shp$", n, re.I)]
            if not adm4:
                raise RuntimeError(f"no admin-4 shapefile in {res['name']}: {names[:20]}")
            stem = adm4[0][:-4]
            for ext in (".shp", ".shx", ".dbf", ".cpg", ".prj"):
                if stem + ext in names:
                    z.extract(stem + ext, tmp)
            df = gn_from_shapefile(f"{tmp}/{stem}.shp")
    if len(df) < 10000:  # Sri Lanka has ~14,000 GN divisions
        raise RuntimeError(f"only {len(df)} GN divisions parsed — refusing to overwrite {GN_CSV.name}")
    df.sort_values(["district", "name"]).to_csv(GN_CSV, index=False)
    log.info("GN divisions: %d (source updated %s)", len(df), meta.get("metadata_modified"))
    return {"gn_divisions": len(df), "hdx_updated": meta.get("metadata_modified")}
