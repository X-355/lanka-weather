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

from .config import ERA5_DIR, LIVE_DIR, NWP_DIR, POWER_DIR, Town, load_config, towns
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
    start = date.fromisoformat(cfg["data"]["start_date"])
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
