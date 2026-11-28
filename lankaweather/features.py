"""Feature engineering.

Rule: a row for (town, day t) may only use information available by the evening of day t.
Targets are about day t+1. Every rolling window is backward-looking and closed at t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import load_config, towns

LAGS = [1, 2, 3, 4, 5, 6]
WINDOWS = [3, 7, 14, 30]

# Sri Lanka's four monsoon seasons (Department of Meteorology convention).
SEASON_OF_MONTH = {12: "NEM", 1: "NEM", 2: "NEM", 3: "FIM", 4: "FIM",
                   5: "SWM", 6: "SWM", 7: "SWM", 8: "SWM", 9: "SWM", 10: "SIM", 11: "SIM"}
SEASON_NAMES = {
    "NEM": "Northeast monsoon (Dec–Feb)",
    "FIM": "First inter-monsoon (Mar–Apr)",
    "SWM": "Southwest monsoon (May–Sep)",
    "SIM": "Second inter-monsoon (Oct–Nov)",
}
SEASON_CODE = {"NEM": 0, "FIM": 1, "SWM": 2, "SIM": 3}


def _per_town(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("date").set_index("date").asfreq("D")  # explicit gaps -> NaN, never silent shifts
    out = pd.DataFrame(index=g.index)
    p = g["precip"]
    out["precip"] = p
    for lag in LAGS:
        out[f"precip_lag{lag}"] = p.shift(lag)
    for w in WINDOWS:
        out[f"precip_sum{w}"] = p.rolling(w, min_periods=max(2, w // 2)).sum()
        out[f"wetdays{w}"] = (p >= 1.0).astype(float).where(p.notna()).rolling(w, min_periods=max(2, w // 2)).sum()
    # Consecutive dry/wet run length ending today.
    wet = (p >= 1.0).astype(int)
    run_id = (wet != wet.shift()).cumsum()
    out["run_len"] = wet.groupby(run_id).cumcount() + 1
    out["run_wet"] = wet

    for col in ["rain", "precip_hours", "tmax", "tmin", "tmean", "rh", "dew", "pmsl", "cloud",
                "wind_max", "gust_max", "sw_rad", "et0"]:
        if col in g:
            out[col] = g[col]
    if {"tmax", "tmin"} <= set(g):
        out["trange"] = g["tmax"] - g["tmin"]
    for col in ["pmsl", "rh", "cloud", "tmax", "dew"]:
        if col in g:
            out[f"{col}_d1"] = g[col].diff(1)
            out[f"{col}_d3"] = g[col].diff(3)
            out[f"{col}_anom30"] = g[col] - g[col].rolling(30, min_periods=15).mean()
    if "wind_dir" in g:
        rad = np.deg2rad(g["wind_dir"])
        out["wind_u"] = np.sin(rad)
        out["wind_v"] = np.cos(rad)
    if {"tmean", "dew"} <= set(g):
        out["dep_point"] = g["tmean"] - g["dew"]  # dew-point depression: small = near saturation

    # Targets (tomorrow). NaN where tomorrow is unknown.
    cfg = load_config()
    nxt = p.shift(-1)
    out["y_amount"] = nxt
    out["y_rain"] = (nxt >= cfg["target"]["rain_mm"]).astype(float).where(nxt.notna())
    out["y_heavy"] = (nxt >= cfg["target"]["heavy_mm"]).astype(float).where(nxt.notna())
    return out.reset_index()


def _island(feat: pd.DataFrame) -> pd.DataFrame:
    """Same-day island context from the district points: rain at every district today is known tonight."""
    wide = feat.pivot(index="date", columns="town", values="precip")
    wide.columns = [f"isl_{c}" for c in wide.columns]
    wide["isl_mean"] = wide.mean(axis=1)
    wide["isl_wetfrac"] = (wide.filter(like="isl_").drop(columns="isl_mean") >= 1.0).mean(axis=1)
    if "pmsl" in feat:
        wide["isl_pmsl"] = feat.pivot(index="date", columns="town", values="pmsl").mean(axis=1)
    return wide.reset_index()


def _calendar(feat: pd.DataFrame) -> pd.DataFrame:
    doy = feat["date"].dt.dayofyear
    feat["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    feat["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    target_month = (feat["date"] + pd.Timedelta(days=1)).dt.month
    feat["season_code"] = target_month.map(SEASON_OF_MONTH).map(SEASON_CODE)
    feat["target_date"] = feat["date"] + pd.Timedelta(days=1)
    return feat


ZONE_CODE = {"wet": 0, "intermediate": 1, "dry": 2}


def build(df: pd.DataFrame) -> pd.DataFrame:
    """Long frame (town, date, raw vars) -> feature frame with targets for day t+1."""
    info = {t.key: t for t in towns()}
    parts = []
    for key, g in df.groupby("town", sort=False):
        f = _per_town(g)
        t = info[key]
        f["town"] = key
        f["lat"], f["lon"], f["elevation"] = t.lat, t.lon, t.elevation
        f["zone_code"] = ZONE_CODE[t.zone]
        parts.append(f)
    feat = pd.concat(parts, ignore_index=True)
    feat = feat.merge(_island(feat), on="date", how="left")
    feat = _calendar(feat)
    feat["town_code"] = feat["town"].map({k: i for i, k in enumerate(info)}).astype(int)
    return feat


def build_points(district_df: pd.DataFrame, point_df: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Features for extra points (towns inside districts), using the district model's conventions.

    Each point uses its own weather history; island-wide context comes from the district points, and the
    categorical district identity (town_code, zone) comes from the district the point lies in.
    """
    info = {t.key: t for t in towns()}
    codes = {k: i for i, k in enumerate(info)}
    district_feat = build(district_df)
    island = _island(district_feat)
    meta = points.set_index("name")
    parts = []
    for name, g in point_df.groupby("town", sort=False):
        m = meta.loc[name]
        f = _per_town(g)
        f["town"] = name
        f["district"] = m["district"]
        f["lat"], f["lon"] = float(m["lat"]), float(m["lon"])
        f["elevation"] = float(g["elevation"].iloc[0]) if "elevation" in g and pd.notna(g["elevation"].iloc[0]) else info[m["district"]].elevation
        f["zone_code"] = ZONE_CODE[info[m["district"]].zone]
        f["town_code"] = codes[m["district"]]
        parts.append(f)
    feat = pd.concat(parts, ignore_index=True).merge(island, on="date", how="left")
    return _calendar(feat)


TARGETS = ["y_rain", "y_heavy", "y_amount"]
NON_FEATURES = {"date", "target_date", "town", "district", *TARGETS}
CATEGORICAL = ["town_code", "season_code", "zone_code"]


def feature_columns(feat: pd.DataFrame) -> list[str]:
    return [c for c in feat.columns if c not in NON_FEATURES]
