"""Daily operational forecast and the live ledger.

The ledger is append-only: the first forecast issued for a (town, date) is locked in and is
later scored against ERA5 once that day is published (~6 days later). That gives a genuine,
out-of-sample track record that cannot be tuned after the fact.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import ERA5_DIR, LEDGER, MODELS, SITE_DATA, load_config, towns
from .features import SEASON_NAMES, SEASON_OF_MONTH, build
from .fetch import fetch_live, load_history
from .http import Client
from .metrics import scores

log = logging.getLogger(__name__)

LEDGER_COLS = ["issued_at", "issue_date", "target_date", "town", "p_rain", "p_heavy", "p_clim", "p_markov",
               "model_version", "input_source", "obs_precip", "obs_rain", "obs_heavy", "scored_at"]


def load_models():
    meta = json.loads((MODELS / "metadata.json").read_text())
    rain = lgb.Booster(model_file=str(MODELS / "rain.txt"))
    heavy = lgb.Booster(model_file=str(MODELS / "heavy.txt"))
    clim = pd.read_csv(MODELS / "climatology_rain.csv")
    clim_h = pd.read_csv(MODELS / "climatology_heavy.csv")
    markov = pd.read_csv(MODELS / "markov.csv")
    return meta, rain, heavy, clim, clim_h, markov


def predict_frame(live: pd.DataFrame, issue_date: pd.Timestamp):
    cfg = load_config()
    meta, rain, heavy, clim, clim_h, markov = load_models()
    # Only the towns this model was trained on, so island-wide features mean the same thing as in training.
    # Newly added towns join at the next retrain.
    live = live[live["town"].isin(meta["towns"])]
    feat = build(live)
    today = feat[feat["date"] == issue_date].copy()
    if len(today) == 0:
        raise RuntimeError(f"no live rows for {issue_date.date()}")
    X = today.reindex(columns=meta["features"])  # columns the API no longer offers become NaN
    today["p_rain"] = rain.predict(X)
    today["p_heavy"] = heavy.predict(X)
    today["doy"] = today["target_date"].dt.dayofyear
    today = today.merge(clim.rename(columns={"p": "p_clim"}), on=["town", "doy"], how="left")
    today = today.merge(clim_h.rename(columns={"p": "p_clim_heavy"}), on=["town", "doy"], how="left")
    today["month"] = today["target_date"].dt.month
    today["today_wet"] = (today["precip"] >= cfg["target"]["rain_mm"]).astype(int)
    today = today.merge(markov.rename(columns={"p": "p_markov"}), on=["town", "month", "today_wet"], how="left")
    return meta, today


def run(offline: bool = False) -> dict:
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    synthetic = (ERA5_DIR / "SYNTHETIC").exists()
    if offline or synthetic:
        hist = load_history()
        issue_date = hist["date"].max()
        live = hist[hist["date"] > issue_date - pd.Timedelta(days=45)].copy()
        source = "synthetic" if synthetic else "era5-replay"
    else:
        live = fetch_live(Client(budget=1000))
        issue_date = pd.Timestamp(datetime.now(tz).date())
        source = "open-meteo-forecast-api"
    meta, today = predict_frame(live, issue_date)

    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = pd.DataFrame({
        "issued_at": issued_at,
        "issue_date": issue_date.date().isoformat(),
        "target_date": today["target_date"].dt.date.astype(str),
        "town": today["town"],
        "p_rain": today["p_rain"].round(4),
        "p_heavy": today["p_heavy"].round(4),
        "p_clim": today["p_clim"].round(4),
        "p_markov": today["p_markov"].round(4),
        "model_version": meta["version"],
        "input_source": source,
    })
    append_ledger(new)

    names = {t.key: t.name for t in towns()}
    target = today["target_date"].iloc[0]
    season = SEASON_OF_MONTH[target.month]
    out = {
        "issued_at": issued_at,
        "issue_date": issue_date.date().isoformat(),
        "target_date": target.date().isoformat(),
        "season": SEASON_NAMES[season],
        "model_version": meta["version"],
        "input_source": source,
        "towns": [
            {"town": r.town, "name": names[r.town], "p_rain": round(float(r.p_rain), 3),
             "p_heavy": round(float(r.p_heavy), 4), "p_clim": round(float(r.p_clim), 3),
             "p_clim_heavy": round(float(r.p_clim_heavy), 4), "p_markov": round(float(r.p_markov), 3),
             "today_precip": None if pd.isna(r.precip) else round(float(r.precip), 1)}
            for r in today.itertuples()
        ],
    }
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "forecast.json").write_text(json.dumps(out, indent=1))
    log.info("forecast for %s issued (%d towns)", out["target_date"], len(out["towns"]))
    return out


def read_ledger() -> pd.DataFrame:
    text = ["issued_at", "issue_date", "target_date", "town", "model_version", "input_source", "scored_at"]
    if LEDGER.exists():
        led = pd.read_csv(LEDGER, dtype={c: "object" for c in text})
    else:
        led = pd.DataFrame(columns=LEDGER_COLS)
    for c in LEDGER_COLS:
        if c not in text:
            led[c] = pd.to_numeric(led[c], errors="coerce").astype("float64")
    return led


def append_ledger(new: pd.DataFrame) -> None:
    led = read_ledger()
    key = set(zip(led["target_date"], led["town"]))
    fresh = new[[(d, t) not in key for d, t in zip(new["target_date"], new["town"])]]
    if len(fresh) < len(new):
        log.info("%d forecasts already locked in ledger; not overwriting", len(new) - len(fresh))
    led = pd.concat([led, fresh.reindex(columns=LEDGER_COLS)], ignore_index=True)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    led.to_csv(LEDGER, index=False)


def score_ledger() -> dict:
    """Fill observed rainfall for matured ledger rows from the ERA5 cache."""
    cfg = load_config()
    led = read_ledger()
    if not len(led):
        return {"scored": 0}
    try:
        hist = load_history()[["town", "date", "precip"]]
    except FileNotFoundError:
        return {"scored": 0}
    obs = {(t, d.date().isoformat()): p for t, d, p in hist.itertuples(index=False)}
    todo = led["obs_precip"].isna()
    n = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i in led.index[todo]:
        v = obs.get((led.at[i, "town"], led.at[i, "target_date"]))
        if v is None or np.isnan(v):
            continue
        led.at[i, "obs_precip"] = v
        led.at[i, "obs_rain"] = float(v >= cfg["target"]["rain_mm"])
        led.at[i, "obs_heavy"] = float(v >= cfg["target"]["heavy_mm"])
        led.at[i, "scored_at"] = now
        n += 1
    led.to_csv(LEDGER, index=False)
    return {"scored": n}


def ledger_summary() -> dict:
    led = read_ledger()
    done = led.dropna(subset=["obs_rain"])
    summary = {"issued": int(len(led)), "scored": int(len(done)), "pending": int(len(led) - len(done))}
    if len(done):
        y = done["obs_rain"].astype(float)
        summary["all"] = {n: scores(y, done[c].astype(float), done["p_clim"].astype(float))
                          for n, c in [("model", "p_rain"), ("markov", "p_markov"), ("climatology", "p_clim")]}
        last = done[pd.to_datetime(done["target_date"]) > pd.to_datetime(done["target_date"]).max() - timedelta(days=30)]
        summary["last30"] = {n: scores(last["obs_rain"].astype(float), last[c].astype(float), last["p_clim"].astype(float))
                             for n, c in [("model", "p_rain"), ("markov", "p_markov"), ("climatology", "p_clim")]}
        summary["first_target"] = str(done["target_date"].min())
        summary["last_target"] = str(done["target_date"].max())
    return summary
