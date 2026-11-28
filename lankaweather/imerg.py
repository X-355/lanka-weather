"""NASA GPM IMERG daily rainfall at the district points: an independent, satellite-based truth.

Needs a free NASA Earthdata token in the EARTHDATA_TOKEN environment variable (a GitHub secret); without it
this module does nothing. Data comes from NASA GES DISC over OPeNDAP, cut to a small box around Sri Lanka.

Products, best first: Final (gauge-calibrated, ~3.5 months late), Late (~14 h), Early (~4 h). Each day keeps the
best product available; Early/Late days are upgraded when Final appears. The value per district is the mean of
the 3×3 IMERG cells (0.1°, ~30 km box) around the district point, similar in scale to the ERA5 cell the model
learned from.

Caveat: IMERG daily files are UTC days (05:30–05:30 Sri Lanka time), not local calendar days. That affects every
forecast equally, so comparisons between forecasts stay fair, but absolute scores are a little lower than they
would be on matching days.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from .config import ROOT, towns

log = logging.getLogger(__name__)
DIR = ROOT / "data" / "raw" / "imerg"
BASE = "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3"
PRODUCTS = {"final": ("GPM_3IMERGDF.07", "3B-DAY.MS"), "late": ("GPM_3IMERGDL.07", "3B-DAY-L.MS"),
            "early": ("GPM_3IMERGDE.07", "3B-DAY-E.MS")}
RANK = {"final": 3, "late": 2, "early": 1}
# 0.1° grid: lon index = (lon + 179.95) / 0.1, lat index = (lat + 89.95) / 0.1
LON0, LON1, LAT0, LAT1 = 2595, 2621, 955, 1002   # ≈ 79.55–82.15 E, 5.55–10.25 N
START = date(2022, 1, 1)


def _session() -> requests.Session | None:
    tok = os.environ.get("EARTHDATA_TOKEN", "").strip()
    if not tok:
        return None
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


_listing: dict = {}


def _file_name(s: requests.Session, product: str, d: date) -> str | None:
    coll, prefix = PRODUCTS[product]
    key = (product, d.year, d.month)
    if key not in _listing:
        r = s.get(f"{BASE}/{coll}/{d.year}/{d.month:02d}/contents.html", timeout=60)
        _listing[key] = re.findall(r'(3B-DAY[^"/<>]*?\.nc4)', r.text) if r.ok else []
    tag = d.strftime("%Y%m%d")
    names = sorted({n for n in _listing[key] if n.startswith(prefix) and f".{tag}-" in n})
    return names[-1] if names else None


def _fetch_day(s: requests.Session, product: str, d: date) -> np.ndarray | None:
    name = _file_name(s, product, d)
    if not name:
        return None
    coll, _ = PRODUCTS[product]
    url = (f"{BASE}/{coll}/{d.year}/{d.month:02d}/{name}.ascii?"
           f"precipitation[0:0][{LON0}:{LON1}][{LAT0}:{LAT1}]")
    r = s.get(url, timeout=120)
    if r.status_code in (401, 403):
        raise PermissionError(f"Earthdata refused the token ({r.status_code}); check EARTHDATA_TOKEN and that "
                              "the 'NASA GESDISC DATA ARCHIVE' application is approved")
    if not r.ok:
        return None
    a = _parse_ascii(r.text, LON1 - LON0 + 1, LAT1 - LAT0 + 1)
    if a is None:
        log.warning("IMERG %s %s: unexpected response: %r", product, d, r.text[:400])
    return a


def _parse_ascii(text: str, nlon: int, nlat: int) -> np.ndarray | None:
    """GES DISC OPeNDAP ASCII for precipitation[time][lon][lat] looks like:
        precipitation.lat, 5.55, 5.65, ...
        precipitation.precipitation[precipitation.time=17120][precipitation.lon=79.55], 0.11, 0, ...
    One row per longitude, one value per latitude. Anything else is rejected (no guessing)."""
    rows = []
    for ln in text.splitlines():
        if ln.startswith("precipitation.precipitation[") or re.match(r"^\[\d+\]\[\d+\],", ln):
            rows.append([float(v) for v in ln.rsplit("],", 1)[1].split(",") if v.strip()])
    if len(rows) != nlon or any(len(r) != nlat for r in rows):
        return None
    a = np.array(rows, dtype=float)
    a[a < 0] = np.nan  # fill values
    return a


def _district_values(a: np.ndarray) -> dict:
    out = {}
    for t in towns():
        i = round((t.lon + 179.95) / 0.1) - LON0
        j = round((t.lat + 89.95) / 0.1) - LAT0
        box = a[max(0, i - 1): i + 2, max(0, j - 1): j + 2]
        out[t.key] = float(np.nanmean(box)) if np.isfinite(box).any() else np.nan
    return out


def fetch(max_requests: int = 350) -> dict:
    s = _session()
    if s is None:
        log.info("IMERG: no EARTHDATA_TOKEN set; skipping")
        return {"skipped": "no token"}
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / "daily.parquet"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["date", "town", "imerg_mm", "product"])
    best = old.groupby("date")["product"].first().to_dict() if len(old) else {}
    today = date.today()
    todo = []
    # newest first, so live scoring gets yesterday quickly; then backfill and upgrades
    for k in range(1, (today - START).days + 1):
        d = today - timedelta(days=k)
        want = "final" if k > 110 else ("late" if k >= 2 else "early")
        have = best.get(pd.Timestamp(d))
        if have is None or RANK[have] < RANK[want]:
            todo.append((d, want))
    new, n = [], 0
    for d, want in todo:
        if n >= max_requests:
            break
        for product in [p for p in ("final", "late", "early") if RANK[p] <= RANK[want]]:
            n += 1
            try:
                a = _fetch_day(s, product, d)
            except PermissionError:
                raise
            except requests.RequestException as e:
                log.warning("IMERG %s %s: %s", product, d, e)
                a = None
            if a is not None:
                for town, v in _district_values(a).items():
                    new.append({"date": pd.Timestamp(d), "town": town, "imerg_mm": round(v, 2), "product": product})
                break
    if new:
        nf = pd.DataFrame(new)
        old = old[~old["date"].isin(nf["date"].unique())]
        out = pd.concat([old, nf], ignore_index=True).sort_values(["date", "town"])
        out.to_parquet(path, index=False)
    stats = {"days_added": len({r["date"] for r in new}), "requests": n, "remaining": max(0, len(todo) - len({r['date'] for r in new}))}
    log.info("IMERG: %s", stats)
    return stats


def truth() -> pd.DataFrame | None:
    path = DIR / "daily.parquet"
    if not path.exists():
        return None
    d = pd.read_parquet(path)
    return d.rename(columns={"date": "target_date", "imerg_mm": "obs_mm"})[["town", "target_date", "obs_mm", "product"]]
