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
from .features import SEASON_NAMES, SEASON_OF_MONTH, build, build_points
from .fetch import fetch_live, fetch_live_places, load_history, load_places
from .http import Client
from .metrics import scores
from . import hybrid, livenwp

log = logging.getLogger(__name__)

LEDGER_COLS = ["issued_at", "issue_date", "target_date", "town", "p_rain", "p_heavy", "p_clim", "p_markov",
               "model_version", "input_source", "obs_precip", "obs_rain", "obs_heavy", "scored_at",
               # added Nov 2026: the hybrid is what's published as p_rain when the professional forecasts were
               # available; p_base is the Weather Lab model alone. ECMWF ensemble = ECMWF's own probability.
               "method", "p_base", "p_base_heavy", "nwp_ecmwf_ifs025", "nwp_gfs_seamless", "nwp_icon_seamless",
               "p_ecmwf_ens", "p_ecmwf_ens_heavy", "obs_imerg", "imerg_product",
               # added 29 Nov 2026: every issue is kept and scored separately; the headline uses the evening slot
               "issue_slot", "lead_hours"]
TEXT_COLS = ["issued_at", "issue_date", "target_date", "town", "model_version", "input_source", "scored_at", "method",
             "imerg_product", "issue_slot"]


def issue_slot(issued_at: str, target_date: str) -> tuple[str, float]:
    """("evening" | "off-schedule", hours from issue to the start of the target day, Sri Lanka time).

    Evening = issued 22:00–24:00 on the day before the target day. Anything else (a manual run, a job GitHub
    started hours late) is kept and scored, but reported separately and left out of the headline scores.
    """
    tz = load_config()["timezone"]
    t = pd.Timestamp(issued_at)
    t = (t.tz_localize("UTC") if t.tzinfo is None else t).tz_convert(tz)
    start = pd.Timestamp(target_date).tz_localize(tz)
    lead = round((start - t).total_seconds() / 3600, 1)
    evening = (start - pd.Timedelta(days=1)).date() == t.date() and t.hour >= EARLIEST_HOUR
    return ("evening" if evening else "off-schedule"), lead


def add_hybrid(df: pd.DataFrame, nwp: pd.DataFrame | None) -> pd.DataFrame:
    """Keep the model's own probabilities as p_base*, and publish the hybrid where all inputs are present."""
    df = df.copy()
    df["p_base"], df["p_base_heavy"], df["method"] = df["p_rain"], df["p_heavy"], "model"
    spec = hybrid.load_spec()
    if nwp is None or spec is None or not len(nwp):
        return df
    df = df.merge(nwp, on="key", how="left")
    args = [df.get(c) for c in hybrid.NWP]
    hr = hybrid.apply(spec["rain"], df["p_base"], *args)
    hh = hybrid.apply(spec["heavy"], df["p_base_heavy"], *args)
    ok = ~np.isnan(hr)
    df.loc[ok, "p_rain"], df.loc[ok, "p_heavy"], df.loc[ok, "method"] = hr[ok], hh[ok], "hybrid"
    return df


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


EARLIEST_HOUR = 22  # local time: the nightly forecast is issued between 22:00 and midnight, so every
                    # forecast has the same lead time (0–2 h before the day starts). Runs outside the
                    # window need --force and are recorded as "off-schedule".


def run(offline: bool = False, force: bool = False) -> dict:
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    synthetic = (ERA5_DIR / "SYNTHETIC").exists()
    if offline or synthetic:
        hist = load_history()
        issue_date = hist["date"].max()
        live = hist[hist["date"] > issue_date - pd.Timedelta(days=45)].copy()
        source = "synthetic" if synthetic else "era5-replay"
    else:
        now = datetime.now(tz)
        # A forecast for tomorrow should use an (almost) complete "today". GitHub sometimes starts scheduled
        # jobs hours late; a run that slips past midnight would then be forecasting a day already under way.
        if not (EARLIEST_HOUR <= now.hour) and not force:
            log.warning("local time %s is outside the issuing window (from %02d:00); not issuing a forecast",
                        now.strftime("%H:%M"), EARLIEST_HOUR)
            return {}
        live = fetch_live(Client(budget=1000))
        issue_date = pd.Timestamp(now.date())
        source = "open-meteo-forecast-api"
    meta, today = predict_frame(live, issue_date)
    cfg_t = cfg["target"]
    target_ts = today["target_date"].iloc[0]
    pts = pd.DataFrame([{"key": t.key, "lat": t.lat, "lon": t.lon} for t in towns() if t.key in set(today["town"])])
    nwp = ens = None
    if not (offline or synthetic):
        try:
            nwp = livenwp.deterministic(Client(budget=500), pts, target_ts)
        except Exception as e:  # the base model still issues a forecast
            log.warning("live professional forecasts failed: %s", e)
        try:
            ens = livenwp.ensemble(Client(budget=1000), pts, target_ts, cfg_t["rain_mm"], cfg_t["heavy_mm"])
        except Exception as e:
            log.warning("ECMWF ensemble failed: %s", e)
    today = add_hybrid(today.assign(key=today["town"]), nwp)
    if ens is not None:
        today = today.merge(ens.drop(columns=["n_members"]), on="key", how="left")
    for c in ["p_ecmwf_ens", "p_ecmwf_ens_heavy", *hybrid.NWP]:
        if c not in today:
            today[c] = np.nan

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
        "method": today["method"],
        "p_base": today["p_base"].round(4),
        "p_base_heavy": today["p_base_heavy"].round(4),
        **{c: today[c].round(1) for c in hybrid.NWP},
        "p_ecmwf_ens": today["p_ecmwf_ens"],
        "p_ecmwf_ens_heavy": today["p_ecmwf_ens_heavy"],
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
             "today_precip": None if pd.isna(r.precip) else round(float(r.precip), 1),
             "method": r.method, "p_base": round(float(r.p_base), 3),
             "ecmwf_mm": None if pd.isna(r.nwp_ecmwf_ifs025) else round(float(r.nwp_ecmwf_ifs025), 1),
             "p_ecmwf_ens": None if pd.isna(r.p_ecmwf_ens) else round(float(r.p_ecmwf_ens), 3)}
            for r in today.itertuples()
        ],
    }
    try:
        out["places"] = predict_places(live, issue_date, meta, offline=offline or synthetic)
    except Exception as e:  # towns are an extra; never let them block the district forecast
        log.warning("town-level forecasts failed: %s", e)
        out["places"] = []
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "forecast.json").write_text(json.dumps(out, indent=1))
    log.info("forecast for %s issued (%d towns)", out["target_date"], len(out["towns"]))
    return out


def read_ledger() -> pd.DataFrame:
    text = TEXT_COLS
    if LEDGER.exists():
        led = pd.read_csv(LEDGER, dtype={c: "object" for c in text})
    else:
        led = pd.DataFrame(columns=LEDGER_COLS)
    led = led.reindex(columns=LEDGER_COLS)
    for c in LEDGER_COLS:
        if c not in text:
            led[c] = pd.to_numeric(led[c], errors="coerce").astype("float64")
    return led


def append_ledger(new: pd.DataFrame) -> None:
    """Add forecasts; never change or replace a saved one.

    Each issue is kept and scored on its own. Within one slot only the first is kept, so backup runs in the
    same evening (GitHub schedule delays) don't add duplicates; a forecast from a different slot is a new row.
    """
    led = read_ledger()
    new = new.copy()
    sl = [issue_slot(a, d) for a, d in zip(new["issued_at"], new["target_date"])]
    new["issue_slot"], new["lead_hours"] = [x[0] for x in sl], [x[1] for x in sl]
    key = set(zip(led["target_date"], led["town"], led["issue_slot"].fillna("evening")))
    fresh = new[[(d, t, s) not in key for d, t, s in zip(new["target_date"], new["town"], new["issue_slot"])]]
    if len(fresh) < len(new):
        log.info("%d forecasts already saved for this slot; not adding duplicates", len(new) - len(fresh))
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
    # Provisional: NASA IMERG satellite rain, available the day after (final scores still come from ERA5).
    m = 0
    try:
        from . import imerg
        im = imerg.truth()
        if im is not None:
            look = {(t, d.date().isoformat()): (v, pr) for t, d, v, pr in im[["town", "target_date", "obs_mm", "product"]].itertuples(index=False)}
            for i in led.index:
                v = look.get((led.at[i, "town"], led.at[i, "target_date"]))
                if v is not None and not np.isnan(v[0]):
                    if pd.isna(led.at[i, "obs_imerg"]) or led.at[i, "imerg_product"] != v[1]:
                        m += 1
                    led.at[i, "obs_imerg"], led.at[i, "imerg_product"] = round(v[0], 2), v[1]
    except Exception as e:
        log.warning("IMERG provisional scoring failed: %s", e)
    led.to_csv(LEDGER, index=False)
    return {"scored": n, "imerg_updated": m}


def ledger_summary() -> dict:
    """Headline scores use the evening issue only (same lead time every day). Off-schedule issues are kept
    and scored separately, so nothing disappears from the record."""
    every = read_ledger()
    slot = every["issue_slot"].fillna("evening")
    led = every[slot == "evening"]
    off = every[slot != "evening"]
    done = led.dropna(subset=["obs_rain"])
    summary = {"issued": int(len(led)), "scored": int(len(done)), "pending": int(len(led) - len(done)),
               "window": f"{EARLIEST_HOUR:02d}:00–24:00 Sri Lanka time, the evening before"}
    if len(off):
        od = off.dropna(subset=["obs_rain"])
        summary["off_schedule"] = {
            "issued": int(len(off)), "scored": int(len(od)),
            "issues": [{"issued_at": a, "target_date": d, "n": int(len(g)), "lead_hours": float(g["lead_hours"].iloc[0]),
                        "method": str(g["method"].fillna("model (before hybrid)").iloc[0])}
                       for (a, d), g in off.groupby(["issued_at", "target_date"])],
            "scores": {n: scores(od["obs_rain"].astype(float), od[c].astype(float), od["p_clim"].astype(float))
                       for n, c in [("published", "p_rain"), ("markov", "p_markov")]} if len(od) else None}
    if len(done):
        y = done["obs_rain"].astype(float)
        summary["all"] = {n: scores(y, done[c].astype(float), done["p_clim"].astype(float))
                          for n, c in [("model", "p_rain"), ("markov", "p_markov"), ("climatology", "p_clim")]}
        last = done[pd.to_datetime(done["target_date"]) > pd.to_datetime(done["target_date"]).max() - timedelta(days=30)]
        summary["last30"] = {n: scores(last["obs_rain"].astype(float), last[c].astype(float), last["p_clim"].astype(float))
                             for n, c in [("model", "p_rain"), ("markov", "p_markov"), ("climatology", "p_clim")]}
        summary["first_target"] = str(done["target_date"].min())
        summary["last_target"] = str(done["target_date"].max())
        both = done.dropna(subset=["p_ecmwf_ens", "p_base"])
        if len(both):
            summary["vs_ecmwf_ens"] = {n: scores(both["obs_rain"].astype(float), both[c].astype(float), both["p_clim"].astype(float))
                                       for n, c in [("published", "p_rain"), ("model", "p_base"), ("ecmwf_ens", "p_ecmwf_ens")]}
    summary["by_method"] = {str(k): int(v) for k, v in led["method"].fillna("model (before hybrid)").value_counts().items()}
    cfg = load_config()
    prov = led.dropna(subset=["obs_imerg"])
    if len(prov):
        y = (prov["obs_imerg"].astype(float) >= cfg["target"]["rain_mm"]).astype(float)
        ents = [("published", "p_rain"), ("markov", "p_markov"), ("climatology", "p_clim")]
        if prov["p_ecmwf_ens"].notna().any():
            ents.append(("ecmwf_ens", "p_ecmwf_ens"))
        out = {}
        for n, c in ents:
            sub = prov[c].notna()
            out[n] = scores(y[sub], prov.loc[sub, c].astype(float), prov.loc[sub, "p_clim"].astype(float))
        summary["provisional_imerg"] = {"n": int(len(prov)), "first_target": str(prov["target_date"].min()),
                                        "last_target": str(prov["target_date"].max()), "scores": out}
    return summary


def predict_places(live: pd.DataFrame, issue_date: pd.Timestamp, meta: dict, offline: bool = False) -> list[dict]:
    """Forecasts for the towns inside each district (config/places.csv).

    Same model as the districts, fed with each town's own recent weather. Not separately backtested:
    the published scores are for the 25 district points.
    """
    places = load_places()
    if places is None:
        return []
    rain = lgb.Booster(model_file=str(MODELS / "rain.txt"))
    heavy = lgb.Booster(model_file=str(MODELS / "heavy.txt"))
    districts = live[live["town"].isin(meta["towns"])]
    places = places[places["district"].isin(meta["towns"])]
    if offline:  # no network: stand in each town's parent district series (keeps the pipeline testable)
        parts = []
        for r in places.itertuples():
            d = districts[districts["town"] == r.district].copy()
            d["town"], d["elevation"] = r.name, np.nan
            parts.append(d)
        place_live = pd.concat(parts, ignore_index=True)
    else:
        place_live = fetch_live_places(Client(budget=4000), places)
    feat = build_points(districts, place_live, places)
    today = feat[feat["date"] == issue_date].copy()
    X = today.reindex(columns=meta["features"])
    today["p_rain"] = rain.predict(X)
    today["p_heavy"] = heavy.predict(X)
    nwp = None
    if not offline:
        try:
            pts = places.rename(columns={"name": "key"})[["key", "lat", "lon"]]
            nwp = livenwp.deterministic(Client(budget=1500), pts, today["target_date"].iloc[0])
        except Exception as e:
            log.warning("town professional forecasts failed: %s", e)
    today = add_hybrid(today.assign(key=today["town"]), nwp)
    return [{"name": r.town, "district": r.district, "lat": round(float(r.lat), 4), "lon": round(float(r.lon), 4),
             "elevation": None if pd.isna(r.elevation) else round(float(r.elevation)),
             "p_rain": round(float(r.p_rain), 3), "p_heavy": round(float(r.p_heavy), 4),
             "today_precip": None if pd.isna(r.precip) else round(float(r.precip), 1), "method": r.method}
            for r in today.sort_values(["district", "town"]).itertuples()]
