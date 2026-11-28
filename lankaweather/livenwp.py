"""Tomorrow's professional forecasts for the live system.

- Deterministic rain from ECMWF IFS, GFS and ICON (inputs to the hybrid; same daily totals as the archive the
  hybrid was fitted on, summed over the Sri Lankan calendar day).
- ECMWF's 51-member ensemble: the share of members with ≥ 1 mm (and ≥ 50 mm) tomorrow. That is ECMWF's own
  probability, logged in the ledger so the live comparison is probability against probability. Open-Meteo does
  not archive ensembles, so this comparison can only be built going forward.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import load_config
from .http import Client

log = logging.getLogger(__name__)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]


def _chunks(df, n):
    for i in range(0, len(df), n):
        yield df.iloc[i:i + n]


def deterministic(client: Client, pts: pd.DataFrame, target: pd.Timestamp, chunk: int = 50) -> pd.DataFrame:
    """pts: key, lat, lon → key, nwp_ecmwf_ifs025, nwp_gfs_seamless, nwp_icon_seamless (mm on target day)."""
    tz = load_config()["timezone"]
    rows = []
    for part in _chunks(pts, chunk):
        params = {"latitude": ",".join(f"{v:.4f}" for v in part["lat"]), "longitude": ",".join(f"{v:.4f}" for v in part["lon"]),
                  "daily": "precipitation_sum", "models": ",".join(MODELS), "timezone": tz, "forecast_days": 3}
        j = client.get_json(FORECAST_URL, params, weight=len(part) * 1.5)
        arr = j if isinstance(j, list) else [j]
        for key, loc in zip(part["key"], arr):
            d = loc["daily"]
            k = d["time"].index(target.date().isoformat()) if target.date().isoformat() in d["time"] else None
            row = {"key": key}
            for m in MODELS:
                v = d.get(f"precipitation_sum_{m}")
                row[f"nwp_{m}"] = np.nan if (k is None or v is None or v[k] is None) else float(v[k])
            rows.append(row)
    return pd.DataFrame(rows)


def ensemble(client: Client, pts: pd.DataFrame, target: pd.Timestamp, rain_mm: float, heavy_mm: float) -> pd.DataFrame:
    """Share of ECMWF ensemble members with ≥ rain_mm / ≥ heavy_mm on the target local day."""
    tz = load_config()["timezone"]
    params = {"latitude": ",".join(f"{v:.4f}" for v in pts["lat"]), "longitude": ",".join(f"{v:.4f}" for v in pts["lon"]),
              "hourly": "precipitation", "models": "ecmwf_ifs025", "timezone": tz, "forecast_days": 3}
    j = client.get_json(ENSEMBLE_URL, params, weight=len(pts) * 10)
    arr = j if isinstance(j, list) else [j]
    rows = []
    for key, loc in zip(pts["key"], arr):
        h = loc["hourly"]
        day = pd.to_datetime(pd.Series(h["time"])).dt.normalize() == target.normalize()
        members = [k for k in h if k.startswith("precipitation")]
        tots = []
        for m in members:
            v = pd.to_numeric(pd.Series(h[m]), errors="coerce")[day.to_numpy()]
            if v.notna().sum() >= 20:  # nearly a full day of values
                tots.append(float(v.sum()))
        tots = np.array(tots)
        rows.append({"key": key, "n_members": len(tots),
                     "p_ecmwf_ens": round(float((tots >= rain_mm).mean()), 4) if len(tots) >= 20 else np.nan,
                     "p_ecmwf_ens_heavy": round(float((tots >= heavy_mm).mean()), 4) if len(tots) >= 20 else np.nan})
    return pd.DataFrame(rows)


# Open-Meteo's own record of the model run it is serving (init = when the run started, available = when it
# reached the API). Saved with each forecast, so live inputs can be compared with the archive used in testing.
META = {"ecmwf": "ecmwf_ifs025", "gfs": "ncep_gfs025", "icon": "dwd_icon", "ecmwf_ens": "ecmwf_ifs025_ensemble"}
ARCHIVE_LEAD_HOURS = (24, 30)  # the "previous_day1" archive: each hour comes from a run made about a day earlier


def run_info(client: Client, target: pd.Timestamp) -> dict:
    """Which run of each model tonight's forecast used, and its lead to the middle of the target day."""
    tz = load_config()["timezone"]
    mid = (pd.Timestamp(target.date()).tz_localize(tz) + pd.Timedelta(hours=12)).tz_convert("UTC")
    out = {}
    for k, m in META.items():
        try:
            j = client.get_json(f"https://api.open-meteo.com/data/{m}/static/meta.json", {}, weight=0.1)
            init = pd.Timestamp(j["last_run_initialisation_time"], unit="s", tz="UTC")
            avail = pd.Timestamp(j["last_run_availability_time"], unit="s", tz="UTC")
            out[k] = {"run": init.strftime("%Y-%m-%dT%H:%MZ"), "available": avail.strftime("%Y-%m-%dT%H:%MZ"),
                      "lead_to_midday_h": round((mid - init).total_seconds() / 3600, 1)}
        except Exception as e:  # the forecast still goes out; the record says the run is unknown
            log.warning("run info for %s failed: %s", m, e)
            out[k] = None
    return out
