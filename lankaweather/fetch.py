"""Data acquisition.

* ERA5 daily history (Open-Meteo Historical Weather API)  -> data/raw/era5/<town>/<year>.parquet
* Day-ahead NWP forecasts (Open-Meteo Previous Runs API)  -> data/raw/nwp/<town>.parquet
* NASA POWER daily irradiance + precipitation             -> data/raw/power/<town>.parquet
* Live recent days for today's prediction (Forecast API)  -> data/raw/live/<town>.parquet

Everything is cached and incremental so a run that hits the free-tier limits can resume.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .config import CELL_MAP, CELLS_DIR, ERA5_DIR, LIVE_DIR, NWP_DIR, PLACES, POWER_DIR, Town, load_config, towns
from .http import BudgetExhausted, Client, RateLimited, openmeteo_weight

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# Open-Meteo daily variable -> our column name. Missing variables are dropped gracefully.
DAILY_VARS = {
    "precipitation_sum": "precip",
    "rain_sum": "rain",
    "precipitation_hours": "precip_hours",
    "temperature_2m_max": "tmax",
    "temperature_2m_min": "tmin",
    "temperature_2m_mean": "tmean",
    "relative_humidity_2m_mean": "rh",
    "dew_point_2m_mean": "dew",
    "pressure_msl_mean": "pmsl",
    "cloud_cover_mean": "cloud",
    "wind_speed_10m_max": "wind_max",
    "wind_gusts_10m_max": "gust_max",
    "wind_direction_10m_dominant": "wind_dir",
    "shortwave_radiation_sum": "sw_rad",
    "et0_fao_evapotranspiration": "et0",
}
INVALID_VAR = re.compile(r"invalid String value (\w+)")


def _daily_frame(payload: dict, used: list[str]) -> pd.DataFrame:
    d = payload["daily"]
    df = pd.DataFrame({"date": pd.to_datetime(d["time"])})
    for v in used:
        df[DAILY_VARS[v]] = pd.to_numeric(pd.Series(d.get(v)), errors="coerce")
    return df


def _request_daily(client: Client, url: str, params: dict, n_days: int) -> pd.DataFrame:
    """Ask for all DAILY_VARS; if the API rejects one, drop it and retry."""
    used = list(DAILY_VARS)
    while used:
        p = dict(params, daily=",".join(used))
        try:
            payload = client.get_json(url, p, weight=openmeteo_weight(len(used), n_days))
        except ValueError as e:
            m = INVALID_VAR.search(str(e))
            if not m or m.group(1) not in used:
                raise
            log.warning("API does not offer %s; dropping it", m.group(1))
            used.remove(m.group(1))
            continue
        return _daily_frame(payload, used)
    raise RuntimeError("no daily variables accepted")


# ----------------------------------------------------------------------------- ERA5 history

def latest_archive_day() -> date:
    cfg = load_config()
    return date.today() - timedelta(days=cfg["data"]["archive_delay_days"])


def fetch_history(client: Client, only: list[str] | None = None) -> dict:
    """Fill data/raw/era5/<town>/<year>.parquet. Past years are fetched once; the current year is refreshed."""
    cfg = load_config()
    start = date.fromisoformat(cfg["data"]["start_date"])
    end = latest_archive_day()
    stats = {"fetched": 0, "skipped": 0, "stopped": None}
    try:
        for town in towns():
            if only and town.key not in only:
                continue
            tdir = ERA5_DIR / town.key
            tdir.mkdir(parents=True, exist_ok=True)
            for year in range(start.year, end.year + 1):
                y0 = max(start, date(year, 1, 1))
                y1 = min(end, date(year, 12, 31))
                path = tdir / f"{year}.parquet"
                if path.exists():
                    have = pd.read_parquet(path, columns=["date"])["date"].max().date()
                    if have >= y1:
                        stats["skipped"] += 1
                        continue
                params = {
                    "latitude": town.lat, "longitude": town.lon,
                    "start_date": y0.isoformat(), "end_date": y1.isoformat(),
                    "timezone": cfg["timezone"], "models": cfg["data"]["archive_model"],
                }
                df = _request_daily(client, ARCHIVE_URL, params, (y1 - y0).days + 1)
                df = df.dropna(subset=["precip"])
                df.to_parquet(path, index=False)
                stats["fetched"] += 1
                log.info("ERA5 %s %d: %d days", town.key, year, len(df))
    except (BudgetExhausted, RateLimited) as e:
        log.warning("stopping history fetch early: %s (will resume next run)", e)
        stats["stopped"] = str(e)
    return stats


def load_history(town_key: str | None = None) -> pd.DataFrame:
    """All cached ERA5 days as one long frame with a `town` column."""
    frames = []
    for town in towns():
        if town_key and town.key != town_key:
            continue
        for p in sorted((ERA5_DIR / town.key).glob("*.parquet")):
            f = pd.read_parquet(p)
            f["town"] = town.key
            frames.append(f)
    if not frames:
        raise FileNotFoundError(f"no ERA5 data under {ERA5_DIR}; run `python -m lankaweather fetch` (or `synthetic`)")
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(["town", "date"]).sort_values(["town", "date"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- NWP day-ahead

def fetch_nwp(client: Client) -> dict:
    """Archived forecasts issued ~24 h ahead (`*_previous_day1`), summed to local-day totals."""
    cfg = load_config()
    models = list(cfg["nwp"]["models"])
    start = date.fromisoformat(cfg["nwp"]["start_date"])
    end = date.today() - timedelta(days=1)
    NWP_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"towns": 0, "stopped": None}
    try:
        for town in towns():
            path = NWP_DIR / f"{town.key}.parquet"
            old = pd.read_parquet(path) if path.exists() else None
            s = start
            if old is not None and len(old):
                s = max(start, old["date"].max().date() - timedelta(days=3))
            if s > end:
                continue
            params = {
                "latitude": town.lat, "longitude": town.lon,
                "hourly": "precipitation_previous_day1",
                "models": ",".join(models),
                "start_date": s.isoformat(), "end_date": end.isoformat(),
                "timezone": cfg["timezone"],
            }
            n_days = (end - s).days + 1
            payload = client.get_json(PREVIOUS_RUNS_URL, params, weight=openmeteo_weight(len(models), n_days) * 2)
            h = payload["hourly"]
            hf = pd.DataFrame({"time": pd.to_datetime(h["time"])})
            for m in models:
                key = f"precipitation_previous_day1_{m}" if len(models) > 1 else "precipitation_previous_day1"
                hf[m] = pd.to_numeric(pd.Series(h.get(key)), errors="coerce")
            hf["date"] = hf["time"].dt.normalize()
            # A day counts only if all 24 hours are present for that model.
            daily = hf.groupby("date")[models].agg(lambda x: x.sum() if x.notna().sum() == 24 else np.nan).reset_index()
            if old is not None:
                daily = pd.concat([old[old["date"] < pd.Timestamp(s)], daily], ignore_index=True)
            daily.to_parquet(path, index=False)
            stats["towns"] += 1
            log.info("NWP %s: %d days", town.key, len(daily))
    except (BudgetExhausted, RateLimited) as e:
        log.warning("stopping NWP fetch early: %s", e)
        stats["stopped"] = str(e)
    return stats


# ----------------------------------------------------------------------------- NASA POWER

def fetch_power(client: Client) -> dict:
    """Daily all-sky irradiance (kWh/m²/day) and bias-corrected precipitation (mm) from NASA POWER."""
    cfg = load_config()
    # POWER's all-sky irradiance record starts in 1984.
    start = max(date.fromisoformat(cfg["data"]["start_date"]), date(1984, 1, 1))
    end = date.today() - timedelta(days=7)
    POWER_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"towns": 0, "stopped": None}
    for town in towns():
        path = POWER_DIR / f"{town.key}.parquet"
        if path.exists() and pd.read_parquet(path)["date"].max().date() >= end - timedelta(days=40):
            continue
        params = {
            "parameters": "ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN,PRECTOTCORR,T2M",
            "community": "RE", "latitude": town.lat, "longitude": town.lon,
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON",
        }
        try:
            payload = client.get_json(POWER_URL, params, weight=0)
        except Exception as e:  # POWER is optional; never fail the pipeline on it
            log.warning("NASA POWER fetch failed for %s: %s", town.key, e)
            stats["stopped"] = str(e)
            continue
        p = payload["properties"]["parameter"]
        df = pd.DataFrame({
            "date": pd.to_datetime(list(p["ALLSKY_SFC_SW_DWN"].keys()), format="%Y%m%d"),
            "ghi": list(p["ALLSKY_SFC_SW_DWN"].values()),
            "ghi_clear": list(p["CLRSKY_SFC_SW_DWN"].values()),
            "precip": list(p["PRECTOTCORR"].values()),
            "t2m": list(p["T2M"].values()),
        })
        df = df.replace(-999.0, np.nan)
        df.to_parquet(path, index=False)
        stats["towns"] += 1
    return stats


def load_power(town_key: str) -> pd.DataFrame | None:
    path = POWER_DIR / f"{town_key}.parquet"
    return pd.read_parquet(path) if path.exists() else None


# ----------------------------------------------------------------------------- live

def fetch_live(client: Client, past_days: int = 45) -> pd.DataFrame:
    """Recent days (incl. today) from the Forecast API, for the operational prediction.

    These come from operational analyses/short-range forecasts, not ERA5, so their statistics
    differ slightly from the training data. The live ledger measures what that costs us.
    """
    cfg = load_config()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for town in towns():
        params = {
            "latitude": town.lat, "longitude": town.lon, "timezone": cfg["timezone"],
            "past_days": past_days, "forecast_days": 1,
        }
        df = _request_daily(client, FORECAST_URL, params, past_days + 1)
        df["town"] = town.key
        frames.append(df)
    live = pd.concat(frames, ignore_index=True)
    live["fetched_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    live.to_parquet(LIVE_DIR / "latest.parquet", index=False)
    return live


def load_places() -> pd.DataFrame | None:
    """Towns inside each district (config/places.csv), or None if the file is absent."""
    return pd.read_csv(PLACES) if PLACES.exists() else None


def fetch_live_places(client: Client, places: pd.DataFrame, past_days: int = 45, chunk: int = 50) -> pd.DataFrame:
    """Recent days for every town, several locations per request (Open-Meteo accepts coordinate lists)."""
    cfg = load_config()
    frames = []
    used = list(DAILY_VARS)
    for i in range(0, len(places), chunk):
        part = places.iloc[i:i + chunk]
        while True:
            params = {
                "latitude": ",".join(f"{x:.4f}" for x in part["lat"]),
                "longitude": ",".join(f"{x:.4f}" for x in part["lon"]),
                "timezone": cfg["timezone"], "past_days": past_days, "forecast_days": 1, "daily": ",".join(used),
            }
            try:
                payload = client.get_json(FORECAST_URL, params,
                                          weight=openmeteo_weight(len(used), past_days + 1) * len(part))
                break
            except ValueError as e:
                m = INVALID_VAR.search(str(e))
                if not m or m.group(1) not in used:
                    raise
                used.remove(m.group(1))
        payload = payload if isinstance(payload, list) else [payload]
        for (_, row), loc in zip(part.iterrows(), payload):
            df = _daily_frame(loc, used)
            df["town"] = row["name"]
            df["elevation"] = loc.get("elevation")
            frames.append(df)
    live = pd.concat(frames, ignore_index=True)
    live.to_parquet(LIVE_DIR / "places.parquet", index=False)
    return live


# ----------------------------------------------------------------------------- town history (backtest)

def _cell_id(lat: float, lon: float) -> str:
    return f"c{lat:.3f}_{lon:.3f}"


def probe_cells(client: Client) -> pd.DataFrame:
    """Find the ERA5 grid cell (and point elevation) for every district point and town. Cached."""
    cfg = load_config()
    places = load_places()
    pts = [{"kind": "district", "name": t.key, "lat": t.lat, "lon": t.lon} for t in towns()]
    pts += [{"kind": "place", "name": r.name, "lat": r.lat, "lon": r.lon} for r in places.itertuples()]
    have = pd.read_csv(CELL_MAP) if CELL_MAP.exists() else pd.DataFrame(columns=["kind", "name", "lat", "lon"])
    known = {(k, n, round(a, 4), round(b, 4)) for k, n, a, b in have[["kind", "name", "lat", "lon"]].itertuples(index=False)}
    todo = [p for p in pts if (p["kind"], p["name"], round(p["lat"], 4), round(p["lon"], 4)) not in known]
    rows = []
    for i in range(0, len(todo), 50):
        part = todo[i:i + 50]
        params = {"latitude": ",".join(f"{p['lat']:.4f}" for p in part), "longitude": ",".join(f"{p['lon']:.4f}" for p in part),
                  "start_date": "2020-01-01", "end_date": "2020-01-01", "daily": "precipitation_sum",
                  "timezone": cfg["timezone"], "models": cfg["data"]["archive_model"]}
        payload = client.get_json(ARCHIVE_URL, params, weight=len(part))
        payload = payload if isinstance(payload, list) else [payload]
        for p, loc in zip(part, payload):
            rows.append({**p, "cell_lat": loc["latitude"], "cell_lon": loc["longitude"],
                         "cell_id": _cell_id(loc["latitude"], loc["longitude"]), "elevation": loc.get("elevation")})
    keep = {(p["kind"], p["name"]) for p in pts}
    cells = pd.concat([have, pd.DataFrame(rows)], ignore_index=True)
    cells = cells[[(k, n) in keep for k, n in zip(cells["kind"], cells["name"])]]
    cells = cells.drop_duplicates(["kind", "name"], keep="last")
    CELL_MAP.parent.mkdir(parents=True, exist_ok=True)
    cells.to_csv(CELL_MAP, index=False)
    return cells


def fetch_places_history(client: Client) -> dict:
    """ERA5 history for every grid cell that has a town but no district point. Resumable, like fetch_history."""
    cfg = load_config()
    stats = {"cells": 0, "fetched": 0, "skipped": 0, "stopped": None}
    if load_places() is None:
        return stats
    try:
        cells = probe_cells(client)
        district_cells = set(cells.loc[cells["kind"] == "district", "cell_id"])
        need = cells[(cells["kind"] == "place") & ~cells["cell_id"].isin(district_cells)].drop_duplicates("cell_id")
        stats["cells"] = len(need)
        start = date.fromisoformat(cfg["places"]["history_start"])
        end = latest_archive_day()
        for c in need.itertuples():
            cdir = CELLS_DIR / c.cell_id
            cdir.mkdir(parents=True, exist_ok=True)
            for year in range(start.year, end.year + 1):
                y0, y1 = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
                path = cdir / f"{year}.parquet"
                if path.exists() and pd.read_parquet(path, columns=["date"])["date"].max().date() >= y1:
                    stats["skipped"] += 1
                    continue
                params = {"latitude": c.cell_lat, "longitude": c.cell_lon, "start_date": y0.isoformat(),
                          "end_date": y1.isoformat(), "timezone": cfg["timezone"], "models": cfg["data"]["archive_model"]}
                df = _request_daily(client, ARCHIVE_URL, params, (y1 - y0).days + 1).dropna(subset=["precip"])
                df.to_parquet(path, index=False)
                stats["fetched"] += 1
    except (BudgetExhausted, RateLimited) as e:
        log.warning("stopping town-history fetch early: %s (will resume next run)", e)
        stats["stopped"] = str(e)
    return stats


def load_place_history() -> pd.DataFrame | None:
    """Long frame (town=<place name>, date, vars, elevation) for every town whose cell history is complete."""
    if not CELL_MAP.exists() or load_places() is None:
        return None
    cfg = load_config()
    start = date.fromisoformat(cfg["places"]["history_start"])
    cells = pd.read_csv(CELL_MAP)
    dmap = dict(zip(cells.loc[cells["kind"] == "district", "cell_id"], cells.loc[cells["kind"] == "district", "name"]))
    frames, cache = [], {}
    for r in cells[cells["kind"] == "place"].itertuples():
        if r.cell_id not in cache:
            if r.cell_id in dmap:
                h = load_history(dmap[r.cell_id]).drop(columns="town")
                h = h[h["date"] >= pd.Timestamp(start)]
            else:
                files = sorted((CELLS_DIR / r.cell_id).glob("*.parquet"))
                years = {int(f.stem) for f in files}
                if not files or not set(range(start.year, latest_archive_day().year)) <= years:
                    cache[r.cell_id] = None
                    continue
                h = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
            cache[r.cell_id] = h
        h = cache[r.cell_id]
        if h is None:
            continue
        frames.append(h.assign(town=r.name, cell_id=r.cell_id, elevation=r.elevation))
    return pd.concat(frames, ignore_index=True) if frames else None
