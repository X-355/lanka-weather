"""Shared "live" snapshots for the site, fetched by GitHub Actions every few hours.

Without these, every visitor's browser would call Open-Meteo itself: one Live sky visit costs ~200 location-calls,
and Open-Meteo's free tier allows 600 per minute per internet connection, so a few people in one office (all on
one connection) could hit the limit. With these files the cost is the same whether 1 or 1,000 people look.

Writes site/data/live/{skygrid,rivers,reservoirs}.json. Each has "generated" (UTC ISO). Any part that fails is
skipped and the browser falls back to calling the service directly, so a failed fetch never breaks the build.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import ROOT

log = logging.getLogger(__name__)
OUT = ROOT / "site" / "data" / "live"
FORECAST = "https://api.open-meteo.com/v1/forecast"
FLOOD = "https://flood-api.open-meteo.com/v1/flood"
# Must match FC_GRID in site/assets/livesky.js
GRID = dict(lat0=4.9, lat1=10.9, lon0=78.4, lon1=83.2, step=0.4)
GRID_HOURS = 30  # enough to keep 12 future hours available until the next refresh, with margin


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url: str, params: dict, tries: int = 3) -> list:
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 429:
                log.warning("429 from %s; waiting", url)
                time.sleep(20 * (k + 1))
                continue
            r.raise_for_status()
            j = r.json()
            return j if isinstance(j, list) else [j]
        except requests.RequestException as e:
            log.warning("%s failed (%s), try %d", url, e, k + 1)
            time.sleep(5)
    raise RuntimeError(f"giving up on {url}")


def _seq(a: float, b: float, s: float) -> list[float]:
    out, x = [], a
    while x <= b + 1e-9:
        out.append(round(x, 3))
        x += s
    return out


def skygrid() -> dict:
    lats, lons = _seq(GRID["lat0"], GRID["lat1"], GRID["step"]), _seq(GRID["lon0"], GRID["lon1"], GRID["step"])
    pts = [(la, lo) for la in lats for lo in lons]
    arr = _get(FORECAST, {"latitude": ",".join(str(p[0]) for p in pts), "longitude": ",".join(str(p[1]) for p in pts),
                          "hourly": "precipitation,cloud_cover", "forecast_hours": GRID_HOURS, "timezone": "GMT"})
    times = arr[0]["hourly"]["time"]
    # [hour][point], compact numbers
    precip = [[round(a["hourly"]["precipitation"][h] or 0, 1) for a in arr] for h in range(len(times))]
    cloud = [[int(a["hourly"]["cloud_cover"][h] or 0) for a in arr] for h in range(len(times))]
    return {"generated": _now(), "grid": GRID, "lats": lats, "lons": lons, "times": [t + "Z" for t in times],
            "precip": precip, "cloud": cloud}


def rivers(cfg: dict) -> dict:
    st = cfg["stations"]
    arr = _get(FLOOD, {"latitude": ",".join(str(s["lat"]) for s in st), "longitude": ",".join(str(s["lon"]) for s in st),
                       "daily": "river_discharge,river_discharge_mean,river_discharge_p75,river_discharge_max",
                       "past_days": 7, "forecast_days": 10})
    return {"generated": _now(), "stations": [{**s, "daily": a["daily"]} for s, a in zip(st, arr)]}


def reservoirs(cfg: dict) -> dict:
    rs = cfg["reservoirs"]
    pts = [(k, la, lo) for k, r in enumerate(rs) for la, lo in r["catch"]]
    arr = _get(FORECAST, {"latitude": ",".join(str(p[1]) for p in pts), "longitude": ",".join(str(p[2]) for p in pts),
                          "daily": "precipitation_sum", "past_days": 3, "forecast_days": 4, "timezone": "Asia/Colombo"})
    out = [{**r, "points": []} for r in rs]
    for (k, _, _), a in zip(pts, arr):
        out[k]["points"].append({"time": a["daily"]["time"], "precipitation_sum": a["daily"]["precipitation_sum"]})
    return {"generated": _now(), "reservoirs": out}


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT / "config" / "rivers.json").read_text())
    done = {}
    for name, fn in [("skygrid", skygrid), ("rivers", lambda: rivers(cfg)), ("reservoirs", lambda: reservoirs(cfg))]:
        try:
            data = fn()
            (OUT / f"{name}.json").write_text(json.dumps(data, separators=(",", ":")))
            done[name] = "ok"
        except Exception as e:  # never break the site build over a snapshot
            log.error("snapshot %s failed: %s", name, e)
            done[name] = f"failed: {e}"
        time.sleep(2)
    try:  # the cyclone list too, so a GDACS timeout at night is retried within 3 hours
        from .build_site import build_cyclones
        build_cyclones()
        done["cyclones"] = "ok"
    except Exception as e:
        done["cyclones"] = f"failed: {e}"
    log.info("snapshots: %s", done)
    return done
