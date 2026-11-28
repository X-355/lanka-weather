"""Real rain gauges in Sri Lanka from NOAA (independent of ERA5 and of every forecast model).

- GSOD (Global Summary of the Day): 21 Sri Lankan synoptic stations, one file per station-year, public AWS bucket.
- GHCN-Daily: 6 Sri Lankan stations, one file per station, public AWS bucket; fills some 2026 days GSOD lacks.
NOAA's copies of Sri Lankan data stop or thin out from late 2025, so gauges check the backtest years, not
yesterday's forecast (NASA IMERG does that).

Stored in data/raw/gauges/{gsod,ghcn}.parquet: station, name, lat, lon, date, prcp_mm.
"""
from __future__ import annotations

import io
import logging
import math
from datetime import date

import numpy as np
import pandas as pd
import requests

from .config import ROOT, towns

log = logging.getLogger(__name__)
GAUGE_DIR = ROOT / "data" / "raw" / "gauges"
GSOD = "https://noaa-gsod-pds.s3.amazonaws.com"
GHCN = "https://noaa-ghcn-pds.s3.amazonaws.com"
GHCN_STATIONS = ["CE000043497", "CE000434730", "CEM00043418", "CEM00043424", "CEM00043441", "CEM00043466"]
FIRST_YEAR = 2010   # 2010–2021 give each gauge its own climatology; 2022+ are the test years
MAX_KM = 35         # a gauge is matched to the nearest district point within this distance
# GSOD precipitation flags that mean a complete 24-hour total (NOAA GSOD readme): D = four 6-hour amounts,
# F = two 12-hour amounts, G = one 24-hour amount. Others are partial or "possibly had rain".
FULL_DAY = {"D", "F", "G"}


def _gsod_station_ids(year: int) -> list[str]:
    r = requests.get(f"{GSOD}/?list-type=2&prefix={year}/434", timeout=60)
    r.raise_for_status()
    import re
    return sorted({k.split("/")[1][:11] for k in re.findall(r"<Key>([^<]+)</Key>", r.text)})


def fetch(refresh_years: int = 2) -> dict:
    GAUGE_DIR.mkdir(parents=True, exist_ok=True)
    stats = {}
    # ---- GSOD, per station-year; past years are kept, the last `refresh_years` are re-downloaded
    path = GAUGE_DIR / "gsod.parquet"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    have = set(zip(old["station"], old["date"].dt.year)) if len(old) else set()
    this_year = date.today().year
    frames, n = [old], 0
    for year in range(FIRST_YEAR, this_year + 1):
        recent = year > this_year - refresh_years
        try:
            ids = _gsod_station_ids(year)
        except requests.RequestException as e:
            log.warning("GSOD list %d failed: %s", year, e)
            continue
        for sid in ids:
            if (sid, year) in have and not recent:
                continue
            try:
                r = requests.get(f"{GSOD}/{year}/{sid}.csv", timeout=60)
                if r.status_code != 200:
                    continue
                f = pd.read_csv(io.StringIO(r.text), dtype={"STATION": str})
            except Exception as e:  # one bad file shouldn't stop the rest
                log.warning("GSOD %s %d: %s", sid, year, e)
                continue
            flag = f["PRCP_ATTRIBUTES"].astype(str).str.strip()
            mm = pd.to_numeric(f["PRCP"], errors="coerce").where(lambda s: s < 99, np.nan) * 25.4
            frames = [x[~((x["station"] == sid) & (x["date"].dt.year == year))] if len(x) else x for x in frames]
            frames.append(pd.DataFrame({
                "station": sid, "name": f["NAME"].str.replace(", CE", "", regex=False).str.title(),
                "lat": pd.to_numeric(f["LATITUDE"], errors="coerce"), "lon": pd.to_numeric(f["LONGITUDE"], errors="coerce"),
                "date": pd.to_datetime(f["DATE"]), "prcp_mm": mm.where(flag.isin(FULL_DAY)).round(1), "flag": flag}))
            n += 1
    g = pd.concat([x for x in frames if len(x)], ignore_index=True) if any(len(x) for x in frames) else pd.DataFrame()
    if len(g):
        g = g.drop_duplicates(["station", "date"], keep="last").sort_values(["station", "date"])
        g.to_parquet(path, index=False)
    stats["gsod_files"] = n
    stats["gsod_rows"] = int(len(g))
    # ---- GHCN-Daily, whole record per station (small)
    rows = []
    for sid in GHCN_STATIONS:
        try:
            r = requests.get(f"{GHCN}/csv/by_station/{sid}.csv", timeout=120)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("GHCN %s: %s", sid, e)
            continue
        f = pd.read_csv(io.StringIO(r.text), header=None, usecols=range(7),
                        names=["id", "date", "el", "val", "m", "q", "s"], dtype={"date": str})
        f = f[(f["el"] == "PRCP") & f["q"].isna()]  # drop values that failed NOAA quality checks
        rows.append(pd.DataFrame({"station": sid, "date": pd.to_datetime(f["date"], format="%Y%m%d"),
                                  "prcp_mm": f["val"].astype(float) / 10}))
    if rows:
        h = pd.concat(rows, ignore_index=True)
        h = h[h["date"] >= f"{FIRST_YEAR}-01-01"]
        h.to_parquet(GAUGE_DIR / "ghcn.parquet", index=False)
        stats["ghcn_rows"] = int(len(h))
    log.info("gauges: %s", stats)
    return stats


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def stations() -> pd.DataFrame:
    path = GAUGE_DIR / "gsod.parquet"
    if not path.exists():
        return pd.DataFrame()
    g = pd.read_parquet(path)
    st = g.groupby("station").agg(name=("name", "last"), lat=("lat", "median"), lon=("lon", "median")).reset_index()
    best = []
    for r in st.itertuples():
        d = sorted((_km(r.lat, r.lon, t.lat, t.lon), t.key) for t in towns())
        best.append((d[0][1], round(d[0][0], 1)) if d[0][0] <= MAX_KM else (None, round(d[0][0], 1)))
    st["town"], st["km"] = zip(*best)
    return st


def truth(era5: pd.DataFrame | None = None, test_start: str = "2022-01-01") -> tuple[pd.DataFrame | None, dict]:
    """Gauge rain per (district, target_date), one row per gauge-day, with the gauge's own climatology.

    GSOD dates are UTC days and Sri Lankan gauges report 24-hour totals in the morning, so a GSOD "day" may
    describe the previous local day. The shift (0 or 1 day) is chosen by agreement with ERA5 rain days on the
    pre-test years and reported, so the choice can't be tuned on the test years.
    """
    path = GAUGE_DIR / "gsod.parquet"
    if not path.exists():
        return None, {}
    g = pd.read_parquet(path).dropna(subset=["prcp_mm"])
    st = stations()
    g = g.merge(st[["station", "town", "km"]], on="station").dropna(subset=["town"])
    # GHCN fills days GSOD lacks (same WMO station numbers: CE...43466 = 43466099999)
    hp = GAUGE_DIR / "ghcn.parquet"
    if hp.exists():
        h = pd.read_parquet(hp)
        h["station"] = h["station"].str[-5:] + "099999"
        h = h.merge(st[["station", "town", "km"]], on="station").dropna(subset=["town"])
        key = set(zip(g["station"], g["date"]))
        h = h[[(s, d) not in key for s, d in zip(h["station"], h["date"])]]
        g = pd.concat([g, h], ignore_index=True)
    info = {"stations": int(g["station"].nunique()), "districts": int(g["town"].nunique())}
    shift = 0
    if era5 is not None:
        e = era5.rename(columns={"date": "target_date"})[["town", "target_date", "precip"]]
        pre = g[g["date"] < test_start]
        agree = {}
        for s in (0, 1):
            j = pre.assign(target_date=pre["date"] - pd.Timedelta(days=s)).merge(e, on=["town", "target_date"])
            if len(j):
                agree[s] = float(((j["prcp_mm"] >= 1) == (j["precip"] >= 1)).mean())
        if agree:
            shift = max(agree, key=agree.get)
            info["shift_days"] = shift
            info["agreement_by_shift"] = {str(k): round(v, 3) for k, v in agree.items()}
    g = g.assign(target_date=g["date"] - pd.Timedelta(days=shift))
    # each gauge's own wet-day climatology from its pre-test years (±15 days of year)
    pre = g[g["target_date"] < test_start]
    clim = {}
    for sid, s in pre.groupby("station"):
        doy, wet = s["target_date"].dt.dayofyear.to_numpy(), (s["prcp_mm"] >= 1).to_numpy(float)
        if len(s) < 700:
            continue
        for d in range(1, 367):
            dist = np.minimum(np.abs(doy - d), 366 - np.abs(doy - d))
            m = dist <= 15
            clim[(sid, d)] = wet[m].mean() if m.sum() >= 20 else np.nan
    g["clim_truth"] = [clim.get((s, d), np.nan) for s, d in zip(g["station"], g["target_date"].dt.dayofyear)]
    out = g[g["target_date"] >= test_start][["town", "target_date", "station", "prcp_mm", "clim_truth"]]
    return out.rename(columns={"prcp_mm": "obs_mm"}), info
