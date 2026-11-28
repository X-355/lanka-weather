"""Turn models, reports, the ledger and the data cache into small JSON files for the static site."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import ERA5_DIR, LIVE_DIR, MODELS, REPORTS, SITE_DATA, load_config, towns
from .features import SEASON_NAMES, SEASON_OF_MONTH
from .fetch import load_history, load_power
from .predict import ledger_summary, read_ledger

log = logging.getLogger(__name__)


def _r(a, d=1):
    return [None if (x is None or (isinstance(x, float) and np.isnan(x))) else round(float(x), d) for x in a]


def _write(name: str, obj) -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    def conv(o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        raise TypeError(type(o))

    (SITE_DATA / name).write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False, default=conv))


def build_meta() -> None:
    cfg = load_config()
    meta = json.loads((MODELS / "metadata.json").read_text()) if (MODELS / "metadata.json").exists() else {}
    _write("meta.json", {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic": (ERA5_DIR / "SYNTHETIC").exists(),
        "towns": [t.__dict__ for t in towns()],
        "targets": cfg["target"],
        "model": {k: meta.get(k) for k in ["version", "trained_at", "data_source", "data_range", "split", "rows"]},
        "nwp_models": cfg["nwp"]["models"],
        "solar": cfg["solar"],
        "seasons": SEASON_NAMES,
    })


def build_metrics() -> None:
    p = REPORTS / "metrics.json"
    if p.exists():
        full = json.loads(p.read_text())
        m = dict(full["metrics"])
        places = m.pop("places", None)
        _write("metrics.json", {"metrics": m, "importance": full["importance"],
                                "places_summary": {k: v for k, v in (places or {}).items() if k != "by_place"} or None})
        _write("places_backtest.json", (places or {}).get("by_place", {}))


def build_backtest() -> None:
    p = REPORTS / "test_predictions.parquet"
    if not p.exists():
        return
    t = pd.read_parquet(p)
    nwp_cols = [c for c in t if c.startswith("nwp_")]
    out = {}
    for town, g in t.groupby("town"):
        g = g.sort_values("target_date")
        out[town] = {
            "start": g["target_date"].min().date().isoformat(),
            "dates": g["target_date"].dt.strftime("%Y-%m-%d").tolist(),
            "obs": _r(g["y_amount"]),
            "p": _r(g["p_rain"], 3),
            "ph": _r(g["p_heavy"], 3),
            "clim": _r(g["p_clim"], 3),
            "markov": _r(g["p_markov"], 3),
            **{c: _r(g[c]) for c in nwp_cols},
        }
    _write("backtest.json", out)


def build_ledger() -> None:
    led = read_ledger()
    rows = led.tail(31 * len(towns())).replace({np.nan: None}).to_dict(orient="records")
    _write("ledger.json", {"summary": ledger_summary(), "recent": rows})


def build_climate(hist: pd.DataFrame) -> None:
    """Monsoon/rainfall dashboard: normals, this year vs normal, season totals by year, last 90 days."""
    cfg = load_config()
    rain_mm = cfg["target"]["rain_mm"]
    h = hist.copy()
    h["year"], h["month"] = h["date"].dt.year, h["date"].dt.month
    last_day = h["date"].max()
    full_years = [y for y in sorted(h["year"].unique()) if (h[h["year"] == y]["date"].dt.dayofyear.max() >= 365)]
    # WMO standard climate normal 1991-2020 (falls back to whatever full years exist)
    base_years = [y for y in full_years if 1991 <= y <= 2020] or full_years
    live_path = LIVE_DIR / "latest.parquet"
    live = pd.read_parquet(live_path) if live_path.exists() else None
    out = {"last_day": last_day.date().isoformat(), "baseline": [min(base_years), max(base_years)], "towns": {},
           "provisional_through": None if live is None else live["date"].max().date().isoformat()}
    for town, g in h.groupby("town"):
        monthly = g.groupby(["year", "month"]).agg(total=("precip", "sum"), wet=("precip", lambda x: int((x >= rain_mm).sum())),
                                                   days=("precip", "size")).reset_index()
        base = monthly[monthly["year"].isin(base_years)]
        normal = base.groupby("month")[["total", "wet"]].mean()
        p10 = base.groupby("month")["total"].quantile(0.1)
        p90 = base.groupby("month")["total"].quantile(0.9)
        years = sorted(monthly["year"].unique())[-6:]
        by_year = {}
        for y in years:
            m = monthly[monthly["year"] == y].set_index("month")
            by_year[int(y)] = [None if mo not in m.index else round(float(m.at[mo, "total"]), 1) for mo in range(1, 13)]
        # Seasonal totals by "monsoon year" (NEM Dec counts toward the following year's NEM).
        s = g.assign(season=g["month"].map(SEASON_OF_MONTH), syear=np.where(g["month"] == 12, g["year"] + 1, g["year"]))
        st = s.groupby(["syear", "season"])["precip"].agg(["sum", "size"]).reset_index()
        expected = {"NEM": 90, "FIM": 61, "SWM": 153, "SIM": 61}
        seasons = {}
        for code in ["NEM", "FIM", "SWM", "SIM"]:
            q = st[st["season"] == code]
            complete = q[q["size"] >= expected[code] - 1]
            seasons[code] = {"years": complete["syear"].astype(int).tolist(), "totals": _r(complete["sum"]),
                             "partial": None}
            inprog = q[(q["size"] < expected[code] - 1) & (q["syear"] == q["syear"].max())]
            if len(inprog) and inprog["syear"].iloc[0] >= last_day.year:
                seasons[code]["partial"] = {"year": int(inprog["syear"].iloc[0]), "total": round(float(inprog["sum"].iloc[0]), 1),
                                            "days": int(inprog["size"].iloc[0]), "of": expected[code]}
        recent = g[g["date"] > last_day - pd.Timedelta(days=90)]
        prov = []
        if live is not None:
            lv = live[(live["town"] == town) & (live["date"] > g["date"].max())].sort_values("date")
            prov = [{"date": d.date().isoformat(), "precip": None if pd.isna(p) else round(float(p), 1)}
                    for d, p in zip(lv["date"], lv["precip"])]
        out["towns"][town] = {
            "normal_total": _r(normal["total"]), "normal_wet": _r(normal["wet"]),
            "p10": _r(p10), "p90": _r(p90), "by_year": by_year,
            "annual_normal": round(float(normal["total"].sum()), 0),
            "seasons": seasons,
            "recent": {"dates": recent["date"].dt.strftime("%Y-%m-%d").tolist(), "precip": _r(recent["precip"])},
            # Days newer than ERA5, from the operational analysis used for tonight's forecast. Replaced once ERA5 has them.
            "provisional": prov,
        }
    _write("climate.json", out)


def build_solar(hist: pd.DataFrame) -> None:
    """Monthly mean daily irradiation (kWh/m²/day) per town; NASA POWER first, ERA5 as fallback."""
    out = {}
    for t in towns():
        pw = load_power(t.key)
        if pw is not None and pw["ghi"].notna().sum() > 365:
            src, d = "NASA POWER (CERES/MERRA-2)", pw[["date", "ghi", "ghi_clear"]].dropna(subset=["ghi"])
        else:
            g = hist[hist["town"] == t.key]
            src, d = "Open-Meteo ERA5", pd.DataFrame({"date": g["date"], "ghi": g["sw_rad"] / 3.6, "ghi_clear": np.nan})
        d = d.assign(year=d["date"].dt.year, month=d["date"].dt.month)
        monthly = d.groupby("month")["ghi"].mean()
        annual = d.groupby("year")["ghi"].agg(["mean", "size"])
        annual = annual[annual["size"] >= 360]["mean"]
        out[t.key] = {
            "source": src, "ghi_month": _r(monthly, 2),
            "clear_month": _r(d.groupby("month")["ghi_clear"].mean(), 2),
            "annual_mean": round(float(monthly.mean()), 2),
            "annual_p10": round(float(annual.quantile(0.1)), 2) if len(annual) else None,
            "annual_p90": round(float(annual.quantile(0.9)), 2) if len(annual) else None,
            "years": [int(d["year"].min()), int(d["year"].max())],
        }
    _write("solar.json", out)


def build_heavy(hist: pd.DataFrame) -> None:
    cfg = load_config()
    thr = cfg["target"]["heavy_mm"]
    h = hist.assign(month=hist["date"].dt.month, year=hist["date"].dt.year)
    out = {"threshold_mm": thr, "towns": {}}
    for town, g in h.groupby("town"):
        n_years = max(1.0, len(g) / 365.25)  # towns can have different record lengths
        heavy = g[g["precip"] >= thr]
        per_month = heavy.groupby("month").size().reindex(range(1, 13), fill_value=0) / n_years
        events = heavy.sort_values("date", ascending=False).head(15)
        out["towns"][town] = {
            "per_month": _r(per_month, 2),
            "per_year": round(len(heavy) / n_years, 2),
            "max": {"mm": round(float(g["precip"].max()), 1), "date": g.loc[g["precip"].idxmax(), "date"].date().isoformat()},
            "recent_events": [{"date": d.date().isoformat(), "mm": round(float(p), 1)} for d, p in zip(events["date"], events["precip"])],
        }
    _write("heavy.json", out)


def run() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    build_meta()
    build_metrics()
    build_backtest()
    build_ledger()
    hist = load_history()
    build_climate(hist)
    build_solar(hist)
    build_heavy(hist)
    if (REPORTS / "REPORT.md").exists():
        shutil.copy(REPORTS / "REPORT.md", SITE_DATA / "REPORT.md")
    log.info("site data written to %s", SITE_DATA)
