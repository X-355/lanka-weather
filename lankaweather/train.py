"""Train, evaluate honestly on unseen years, then refit a production model.

1. Chronological split by *target* date: train <= train_end < valid <= valid_end < test.
2. Fit LightGBM with early stopping on the validation years only.
3. Score the frozen model on the test years against climatology, persistence, a Markov-chain
   baseline and archived day-ahead NWP forecasts — all on identical rows.
4. Refit on every labelled day with the same settings for the live model.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import __version__
from .config import CELL_MAP, ERA5_DIR, MODELS, NWP_DIR, REPORTS, load_config, towns
from .features import CATEGORICAL, SEASON_NAMES, SEASON_OF_MONTH, build, build_points, feature_columns
from .fetch import load_history, load_place_history, load_places, load_power
from .metrics import reliability, scores
from . import gauges, hybrid, imerg, verify

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- baselines

def climatology_table(train: pd.DataFrame, target: str, halfwidth: int = 15) -> pd.DataFrame:
    """P(target | town, day-of-year of target date), smoothed over a ±15 day window, from training only."""
    rows = []
    for town, g in train.groupby("town"):
        doy = g["target_date"].dt.dayofyear.to_numpy()
        y = g[target].to_numpy()
        for d in range(1, 367):
            dist = np.abs(doy - d)
            dist = np.minimum(dist, 366 - dist)
            m = dist <= halfwidth
            rows.append({"town": town, "doy": d, "p": float(y[m].mean()) if m.any() else float(y.mean())})
    return pd.DataFrame(rows)


def markov_table(train: pd.DataFrame, rain_mm: float) -> pd.DataFrame:
    """P(rain tomorrow | town, month of target, rained today) from training only."""
    t = train.assign(month=train["target_date"].dt.month, today_wet=(train["precip"] >= rain_mm).astype(int))
    return t.groupby(["town", "month", "today_wet"])["y_rain"].mean().rename("p").reset_index()


def add_baselines(df: pd.DataFrame, clim_rain, clim_heavy, markov, rain_mm: float) -> pd.DataFrame:
    df = df.copy()
    df["doy"] = df["target_date"].dt.dayofyear
    df = df.merge(clim_rain.rename(columns={"p": "p_clim"}), on=["town", "doy"], how="left")
    df = df.merge(clim_heavy.rename(columns={"p": "p_clim_heavy"}), on=["town", "doy"], how="left")
    df["month"] = df["target_date"].dt.month
    df["today_wet"] = (df["precip"] >= rain_mm).astype(int)
    df = df.merge(markov.rename(columns={"p": "p_markov"}), on=["town", "month", "today_wet"], how="left")
    df["p_markov"] = df["p_markov"].fillna(df["p_clim"])
    df["p_persist"] = df["today_wet"].astype(float)
    return df.drop(columns=["doy", "month", "today_wet"])


def load_nwp() -> pd.DataFrame | None:
    frames = []
    for t in towns():
        p = NWP_DIR / f"{t.key}.parquet"
        if p.exists():
            f = pd.read_parquet(p)
            f["town"] = t.key
            frames.append(f)
    if not frames:
        return None
    nwp = pd.concat(frames, ignore_index=True).rename(columns={"date": "target_date"})
    return nwp.rename(columns={m: f"nwp_{m}" for m in load_config()["nwp"]["models"]})


# ----------------------------------------------------------------------------- model

def _params(cfg: dict) -> dict:
    p = dict(cfg["lightgbm"])
    for k in ("num_boost_round", "early_stopping_rounds"):
        p.pop(k)
    return p


def fit(train, valid, cols, target, cfg) -> lgb.Booster:
    params = _params(cfg)
    dtr = lgb.Dataset(train[cols], train[target], categorical_feature=CATEGORICAL, free_raw_data=False)
    dva = lgb.Dataset(valid[cols], valid[target], categorical_feature=CATEGORICAL, reference=dtr)
    return lgb.train(
        params, dtr, num_boost_round=cfg["lightgbm"]["num_boost_round"], valid_sets=[dva],
        callbacks=[lgb.early_stopping(cfg["lightgbm"]["early_stopping_rounds"], verbose=False)],
    )


def refit(full, cols, target, cfg, rounds: int) -> lgb.Booster:
    d = lgb.Dataset(full[cols], full[target], categorical_feature=CATEGORICAL)
    return lgb.train(_params(cfg), d, num_boost_round=rounds)


# ----------------------------------------------------------------------------- evaluation

def _rnd(x):
    if isinstance(x, dict):
        return {k: _rnd(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rnd(v) for v in x]
    if isinstance(x, float):
        return round(x, 4)
    return x


def evaluate(test: pd.DataFrame, nwp_models: dict) -> dict:
    y = test["y_rain"].to_numpy()
    clim = test["p_clim"].to_numpy()
    rep = {"rain": {}, "heavy": {}, "by_town": {}, "by_season": {}, "nwp_comparison": None}
    for name, col in [("model", "p_rain"), ("climatology", "p_clim"), ("markov", "p_markov"), ("persistence", "p_persist")]:
        rep["rain"][name] = scores(y, test[col], clim)
    rep["reliability"] = {"model": reliability(y, test["p_rain"]), "markov": reliability(y, test["p_markov"])}

    yh = test["y_heavy"].to_numpy()
    rep["heavy"]["model"] = scores(yh, test["p_heavy"], test["p_clim_heavy"], threshold=0.2)
    rep["heavy"]["climatology"] = scores(yh, test["p_clim_heavy"], test["p_clim_heavy"], threshold=0.2)
    rep["heavy"]["reliability"] = reliability(yh, test["p_heavy"], bins=5)

    for town, g in test.groupby("town"):
        rep["by_town"][town] = {n: scores(g["y_rain"], g[c], g["p_clim"]) for n, c in
                                [("model", "p_rain"), ("climatology", "p_clim"), ("markov", "p_markov")]}
    season = test["target_date"].dt.month.map(SEASON_OF_MONTH)
    for s, g in test.groupby(season):
        rep["by_season"][s] = {"label": SEASON_NAMES[s], **{n: scores(g["y_rain"], g[c], g["p_clim"]) for n, c in
                               [("model", "p_rain"), ("climatology", "p_clim"), ("markov", "p_markov")]}}

    # Head-to-head with professional NWP on exactly the same rows.
    nwp_cols = [f"nwp_{m}" for m in nwp_models if f"nwp_{m}" in test]
    if nwp_cols:
        rain_mm = load_config()["target"]["rain_mm"]
        common = test.dropna(subset=nwp_cols)
        if len(common):
            yc, cc = common["y_rain"], common["p_clim"]
            comp = {"period": [str(common["target_date"].min().date()), str(common["target_date"].max().date())],
                    "n": int(len(common)), "entries": {}}
            comp["entries"]["model"] = {"label": "Weather Lab (LightGBM)", "kind": "probability", **scores(yc, common["p_rain"], cc)}
            comp["entries"]["model_binary"] = {"label": "Weather Lab, yes/no at 50%", "kind": "yes/no",
                                               **scores(yc, (common["p_rain"] >= 0.5).astype(float), cc)}
            for m in nwp_models:
                c = f"nwp_{m}"
                if c in common:
                    comp["entries"][m] = {"label": nwp_models[m], "kind": "yes/no (≥1 mm forecast)",
                                          **scores(yc, (common[c] >= rain_mm).astype(float), cc)}
            for n, c in [("markov", "p_markov"), ("climatology", "p_clim"), ("persistence", "p_persist")]:
                comp["entries"][n] = {"label": n.capitalize(), "kind": "baseline", **scores(yc, common[c], cc)}
            rep["nwp_comparison"] = comp

    # Second, independent "truth": NASA POWER (MERRA-2/GPCP-corrected) precipitation.
    alt = []
    for t in towns():
        pw = load_power(t.key)
        if pw is not None:
            alt.append(pw[["date", "precip"]].rename(columns={"date": "target_date", "precip": "power_precip"}).assign(town=t.key))
    if alt:
        j = test.merge(pd.concat(alt), on=["town", "target_date"], how="inner").dropna(subset=["power_precip"])
        if len(j):
            ya = (j["power_precip"] >= load_config()["target"]["rain_mm"]).astype(float)
            rep["alt_truth_power"] = {n: scores(ya, j[c], j["p_clim"]) for n, c in
                                      [("model", "p_rain"), ("climatology", "p_clim"), ("markov", "p_markov")]}
            rep["alt_truth_power"]["agreement_era5_vs_power"] = float((ya == j["y_rain"]).mean())
    return _rnd(rep)


# ----------------------------------------------------------------------------- fair comparison, hybrid, truths

def evaluate_extended(test: pd.DataFrame, hist: pd.DataFrame, cfg: dict) -> dict:
    """Everything that needs the professional forecasts or another truth. See hybrid.py and verify.py."""
    rain_mm, heavy_mm = cfg["target"]["rain_mm"], cfg["target"]["heavy_mm"]
    labels = cfg["nwp"]["models"]
    out = {}
    # 1. Error bars on the main test (2022+), resampling whole days.
    out["bootstrap_main"] = verify.bootstrap(test, "y_rain", "p_clim", {"model": "p_rain", "markov": "p_markov"},
                                             pairs=[("model", "markov")])
    # 2. Heavy rain on the main test: base model only (the hybrid needs 2024+).
    out["heavy_main"] = verify.heavy_scorecard(test, "y_heavy", {
        "model_20": {"col": "p_heavy", "threshold": 0.2, "label": "Weather Lab, warn at ≥ 20%", "probability": True},
        "model_30": {"col": "p_heavy", "threshold": 0.3, "label": "Weather Lab, warn at ≥ 30%"},
        "climatology": {"col": "p_clim_heavy", "threshold": 0.2, "label": "Climatology, warn at ≥ 20%"}})
    # 3. Fair head-to-head with ECMWF (probability vs probability) and the hybrid, on the second half of the
    #    NWP era (the first half fits the calibration and the hybrid; nothing is fitted on the scored rows).
    summ, d = hybrid.evaluate(test)
    if summ is not None:
        fwd = d.dropna(subset=["p_hybrid"]).copy()
        for m in labels:
            fwd[f"yn_{m}"] = (fwd[f"nwp_{m}"] >= rain_mm).astype(float)
        y, c = fwd["y_rain"], fwd["p_clim"]
        ent = {"hybrid": ("Hybrid: Weather Lab + ECMWF/GFS/ICON", "probability", "p_hybrid"),
               "model": ("Weather Lab (LightGBM)", "probability", "p_rain"),
               "ecmwf_cal": (f"{labels.get('ecmwf_ifs025', 'ECMWF')}, as a calibrated probability", "probability", "p_ecmwf_cal")}
        for m, lab in labels.items():
            ent[f"yn_{m}"] = (f"{lab}, yes/no (≥ {rain_mm:g} mm forecast)", "yes/no", f"yn_{m}")
        ent.update({"markov": ("Markov baseline", "baseline", "p_markov"), "climatology": ("Climatology", "baseline", "p_clim")})
        out["fair"] = {**summ, "entries": {k: {"label": l, "kind": kd, **scores(y, fwd[col], c)} for k, (l, kd, col) in ent.items()},
                       "bootstrap": verify.bootstrap(fwd, "y_rain", "p_clim",
                                                     {"hybrid": "p_hybrid", "model": "p_rain", "ecmwf_cal": "p_ecmwf_cal",
                                                      "markov": "p_markov"},
                                                     pairs=[("hybrid", "ecmwf_cal"), ("model", "ecmwf_cal"), ("hybrid", "model")]),
                       "reliability": {"hybrid": reliability(y, fwd["p_hybrid"]), "ecmwf_cal": reliability(y, fwd["p_ecmwf_cal"]),
                                       "model": reliability(y, fwd["p_rain"]), "markov": reliability(y, fwd["p_markov"])}}
        out["by_town_fair"] = {t: {n: scores(g["y_rain"], g[col], g["p_clim"]) for n, col in
                                   [("hybrid", "p_hybrid"), ("model", "p_rain"), ("ecmwf_cal", "p_ecmwf_cal"), ("markov", "p_markov")]}
                               for t, g in fwd.groupby("town")}
        out["heavy_fair"] = verify.heavy_scorecard(fwd, "y_heavy", {
            "ecmwf_50": {"col": "nwp_ecmwf_ifs025", "threshold": heavy_mm, "label": f"ECMWF forecast ≥ {heavy_mm:g} mm"},
            "ecmwf_cal_30": {"col": "p_ecmwf_cal_heavy", "threshold": 0.3, "label": "ECMWF calibrated, warn at ≥ 30%"},
            "model_30": {"col": "p_heavy", "threshold": 0.3, "label": "Weather Lab, warn at ≥ 30%"},
            "hybrid_30": {"col": "p_hybrid_heavy", "threshold": 0.3, "label": "Hybrid, warn at ≥ 30%", "probability": True},
            "hybrid_20": {"col": "p_hybrid_heavy", "threshold": 0.2, "label": "Hybrid, warn at ≥ 20%"}})
        out["heavy_fair"]["period"] = summ["test_period"]
        # out-of-fold columns for the independent truths
        test = test.merge(d[["town", "target_date", "p_hybrid_oof", "p_ecmwf_cal_oof", "p_hybrid_heavy_oof"]],
                          on=["town", "target_date"], how="left")
        test["yn_ecmwf"] = np.where(test["nwp_ecmwf_ifs025"].notna(), (test["nwp_ecmwf_ifs025"] >= rain_mm).astype(float), np.nan)
    # 4. Independent truths: rain gauges and NASA IMERG.
    nwp_fc = {"hybrid": "p_hybrid_oof", "model": "p_rain", "ecmwf_cal": "p_ecmwf_cal_oof", "markov": "p_markov"}
    # Strict: only the later days, predicted by fits on the earlier months alone (same rows as the fair comparison).
    strict_rows = fwd if summ is not None else None
    strict_fc = {"hybrid": "p_hybrid", "model": "p_rain", "ecmwf_cal": "p_ecmwf_cal", "markov": "p_markov"}
    pairs = [("hybrid", "ecmwf_cal"), ("hybrid", "model"), ("model", "ecmwf_cal"), ("model", "markov")]
    try:
        g, ginfo = gauges.truth(hist)
    except Exception as e:  # never let a truth source break training
        log.warning("gauge truth failed: %s", e)
        g, ginfo = None, {}
    if g is not None and len(g):
        out["gauges"] = {"info": ginfo,
                         "all_years": verify.score_against(test, g, rain_mm, heavy_mm, {"model": "p_rain", "markov": "p_markov"},
                                                           pairs=[("model", "markov")], label="Rain gauges: Weather Lab vs Markov, all days with gauge data"),
                         "strict": verify.score_against(strict_rows, g, rain_mm, heavy_mm, strict_fc, pairs=pairs,
                                                        label="Rain gauges: strict test, later days only") if summ else None,
                         "nwp_era": verify.score_against(test, g, rain_mm, heavy_mm, nwp_fc, pairs=pairs,
                                                         label="Rain gauges: with the hybrid and ECMWF, out-of-fold") if summ else None}
    im = imerg.truth()
    if im is not None and len(im):
        out["imerg"] = {"products": im["product"].value_counts().to_dict(),
                        "all_years": verify.score_against(test, im, rain_mm, heavy_mm, {"model": "p_rain", "markov": "p_markov"},
                                                          pairs=[("model", "markov")], label="NASA IMERG: Weather Lab vs Markov, all days with satellite data"),
                        "strict": verify.score_against(strict_rows, im, rain_mm, heavy_mm, strict_fc, pairs=pairs,
                                                       label="NASA IMERG: strict test, later days only") if summ else None,
                        "nwp_era": verify.score_against(test, im, rain_mm, heavy_mm, nwp_fc, pairs=pairs,
                                                        label="NASA IMERG: with the hybrid and ECMWF, out-of-fold") if summ else None}
    return _rnd(out)


# ----------------------------------------------------------------------------- towns (spatial generalisation)

def evaluate_places(hist: pd.DataFrame, models: dict, cols: list, cfg: dict) -> dict | None:
    """Score the frozen district-trained model at towns, on the same test years.

    The headline uses towns in ERA5 cells that contain no district point, i.e. places whose weather the
    model never saw in training. Each cell counts once in the aggregate. Baselines (climatology, Markov)
    come from each town's own pre-test years (history_start..valid_end), never from the test years.
    """
    ph = load_place_history()
    places = load_places()
    if ph is None or places is None or not CELL_MAP.exists():
        return None
    rain_mm = cfg["target"]["rain_mm"]
    v_end = pd.Timestamp(cfg["split"]["valid_end"])
    start = pd.Timestamp(cfg["places"]["history_start"]) + pd.Timedelta(days=31)
    places = places[places["name"].isin(ph["town"].unique())]
    feat = build_points(hist, ph.drop(columns=["cell_id"]), places)
    lab = feat.dropna(subset=["y_rain"])
    base = lab[(lab["target_date"] >= start) & (lab["target_date"] <= v_end)]
    test = lab[lab["target_date"] > v_end].copy()
    if test.empty or base.empty:
        return None
    X = test.reindex(columns=cols)
    test["p_rain"] = models["y_rain"].predict(X, num_iteration=models["y_rain"].best_iteration)
    test["p_heavy"] = models["y_heavy"].predict(X, num_iteration=models["y_heavy"].best_iteration)
    test = add_baselines(test, climatology_table(base, "y_rain"), climatology_table(base, "y_heavy"),
                         markov_table(base, rain_mm), rain_mm)

    cells = pd.read_csv(CELL_MAP)
    cell_of = dict(zip(cells.loc[cells["kind"] == "place", "name"], cells.loc[cells["kind"] == "place", "cell_id"]))
    district_cells = dict(zip(cells.loc[cells["kind"] == "district", "cell_id"], cells.loc[cells["kind"] == "district", "name"]))
    test["cell_id"] = test["town"].map(cell_of)

    def agg(df):
        rep = df.drop_duplicates(["cell_id", "target_date"])  # one vote per grid cell per day
        y, clim = rep["y_rain"], rep["p_clim"]
        return {"n_towns": int(df["town"].nunique()), "n_cells": int(rep["cell_id"].nunique()), "n_days": int(len(rep)),
                **{n: scores(y, rep[c], clim) for n, c in [("model", "p_rain"), ("markov", "p_markov"), ("climatology", "p_clim")]},
                "heavy": scores(rep["y_heavy"], rep["p_heavy"], rep["p_clim_heavy"], threshold=0.2)}

    unseen = test[~test["cell_id"].isin(district_cells)]
    shared = test[test["cell_id"].isin(district_cells)]
    by_place = {}
    for name, g in test.groupby("town"):
        s_m = scores(g["y_rain"], g["p_rain"], g["p_clim"])
        s_k = scores(g["y_rain"], g["p_markov"], g["p_clim"])
        cid = cell_of[name]
        by_place[name] = {"district": g["district"].iloc[0], "bss": s_m.get("bss"), "auc": s_m.get("auc"),
                          "markov_bss": s_k.get("bss"), "brier": s_m["brier"], "n": s_m["n"],
                          "base_rate": s_m["base_rate"], "unseen_cell": cid not in district_cells,
                          "shares_cell_with": district_cells.get(cid)}
    out = {"period": [str(test["target_date"].min().date()), str(test["target_date"].max().date())],
           "baseline_years": [str(start.date()), str(v_end.date())],
           "unseen_cells": agg(unseen) if len(unseen) else None,
           "district_cells": agg(shared) if len(shared) else None,
           "by_place": by_place,
           "coverage": {"towns_scored": int(test["town"].nunique()), "towns_total": int(len(load_places()))}}
    return _rnd(out)


# ----------------------------------------------------------------------------- main

def run() -> dict:
    cfg = load_config()
    rain_mm = cfg["target"]["rain_mm"]
    synthetic = (ERA5_DIR / "SYNTHETIC").exists()
    hist = load_history()
    feat = build(hist)
    cols = feature_columns(feat)
    lab = feat.dropna(subset=["y_rain"]).copy()

    t_end, v_end = pd.Timestamp(cfg["split"]["train_end"]), pd.Timestamp(cfg["split"]["valid_end"])
    train = lab[lab["target_date"] <= t_end]
    valid = lab[(lab["target_date"] > t_end) & (lab["target_date"] <= v_end)]
    test = lab[lab["target_date"] > v_end].copy()
    log.info("rows: train %d, valid %d, test %d, features %d", len(train), len(valid), len(test), len(cols))
    if min(len(train), len(valid), len(test)) == 0:
        raise RuntimeError("a split is empty — check config dates against the data range")

    models = {}
    for target in ["y_rain", "y_heavy"]:
        models[target] = fit(train, valid, cols, target, cfg)
        log.info("%s best iteration %d", target, models[target].best_iteration)

    test["p_rain"] = models["y_rain"].predict(test[cols], num_iteration=models["y_rain"].best_iteration)
    test["p_heavy"] = models["y_heavy"].predict(test[cols], num_iteration=models["y_heavy"].best_iteration)
    clim_r = climatology_table(train, "y_rain")
    clim_h = climatology_table(train, "y_heavy")
    test = add_baselines(test, clim_r, clim_h, markov_table(train, rain_mm), rain_mm)
    nwp = load_nwp()
    if nwp is not None:
        test = test.merge(nwp, on=["town", "target_date"], how="left")
    report = evaluate(test, cfg["nwp"]["models"])
    report.update(evaluate_extended(test, hist, cfg))
    prod_hybrid = hybrid.fit_production(test)
    places_rep = evaluate_places(hist, models, cols, cfg)
    if places_rep:
        report["places"] = places_rep

    imp = models["y_rain"].feature_importance("gain")
    importance = sorted(zip(cols, (imp / imp.sum()).round(4).tolist()), key=lambda x: -x[1])[:25]

    # Production refit on all labelled data. Scale rounds with the extra data.
    MODELS.mkdir(exist_ok=True)
    (MODELS / "eval").mkdir(exist_ok=True)
    prod_rounds = {}
    for target, fname in [("y_rain", "rain"), ("y_heavy", "heavy")]:
        m = models[target]
        m.save_model(str(MODELS / "eval" / f"{fname}.txt"), num_iteration=m.best_iteration)
        rounds = max(50, int(m.best_iteration * len(lab) / len(train)))
        prod_rounds[target] = rounds
        refit(lab, cols, target, cfg, rounds).save_model(str(MODELS / f"{fname}.txt"))
    # Baseline tables for the live system, refit on all labelled data.
    climatology_table(lab, "y_rain").to_csv(MODELS / "climatology_rain.csv", index=False)
    climatology_table(lab, "y_heavy").to_csv(MODELS / "climatology_heavy.csv", index=False)
    markov_table(lab, rain_mm).to_csv(MODELS / "markov.csv", index=False)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "version": f"{__version__}+{now[:10].replace('-', '')}",
        "trained_at": now,
        "data_source": "synthetic" if synthetic else f"Open-Meteo {cfg['data']['archive_model'].upper()} reanalysis",
        "features": cols,
        "categorical": CATEGORICAL,
        "targets": {"rain_mm": rain_mm, "heavy_mm": cfg["target"]["heavy_mm"]},
        "data_range": [str(lab["date"].min().date()), str(lab["target_date"].max().date())],
        "split": {"train": [str(train["target_date"].min().date()), str(train["target_date"].max().date())],
                  "valid": [str(valid["target_date"].min().date()), str(valid["target_date"].max().date())],
                  "test": [str(test["target_date"].min().date()), str(test["target_date"].max().date())]},
        "rows": {"train": len(train), "valid": len(valid), "test": len(test)},
        "best_iteration": {k: int(m.best_iteration) for k, m in models.items()},
        "production_rounds": prod_rounds,
        "towns": [t.key for t in towns()],
        "hybrid": prod_hybrid,
    }
    (MODELS / "metadata.json").write_text(json.dumps(meta, indent=2))
    REPORTS.mkdir(exist_ok=True)
    full = {"meta": meta, "metrics": report, "importance": importance}
    (REPORTS / "metrics.json").write_text(json.dumps(full, indent=2))
    keep = ["town", "date", "target_date", "y_amount", "y_rain", "y_heavy", "p_rain", "p_heavy",
            "p_clim", "p_markov", "p_persist"] + [c for c in test if c.startswith("nwp_")]
    test[keep].to_parquet(REPORTS / "test_predictions.parquet", index=False)
    write_report_md(full)
    return full


def _f(x, pct=False):
    if x is None:
        return "–"
    return f"{100 * x:.1f}%" if pct else f"{x:.3f}"


def write_report_md(full: dict) -> None:
    m, meta = full["metrics"], full["meta"]
    lines = [
        "# Evaluation report", "",
        f"Model `{meta['version']}` — data: {meta['data_source']}.",
        f"Test period (never used for training or tuning): **{meta['split']['test'][0]} → {meta['split']['test'][1]}**, "
        f"{meta['rows']['test']:,} town-days.", "",
        "## Next-day rain (≥ {} mm)".format(meta["targets"]["rain_mm"]), "",
        "| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ | Accuracy | POD | FAR |",
        "|---|---|---|---|---|---|---|",
    ]
    for n, s in m["rain"].items():
        lines.append(f"| {n} | {_f(s.get('brier'))} | {_f(s.get('bss'), True)} | {_f(s.get('auc'))} | "
                     f"{_f(s.get('accuracy'), True)} | {_f(s.get('pod'), True)} | {_f(s.get('far'), True)} |")
    c = m.get("nwp_comparison")
    if c:
        lines += ["", f"## Versus professional forecasts ({c['period'][0]} → {c['period'][1]}, {c['n']:,} town-days)", "",
                  "Same rows for every entry. NWP forecasts are day-ahead runs, scored as yes/no (≥ 1 mm forecast).", "",
                  "| Forecast | Type | Brier ↓ | Skill vs clim ↑ | Accuracy | CSI ↑ | POD | FAR |", "|---|---|---|---|---|---|---|---|"]
        for e in c["entries"].values():
            lines.append(f"| {e['label']} | {e['kind']} | {_f(e.get('brier'))} | {_f(e.get('bss'), True)} | "
                         f"{_f(e.get('accuracy'), True)} | {_f(e.get('csi'))} | {_f(e.get('pod'), True)} | {_f(e.get('far'), True)} |")
    ci = lambda b, k: f"{_f(b['skill'][k]['bss'], True)} ({_f(b['skill'][k]['lo'], True)} to {_f(b['skill'][k]['hi'], True)})" if b and k in b.get("skill", {}) else "–"
    bm = m.get("bootstrap_main")
    if bm:
        dd = bm["diff"].get("model - markov", {})
        lines += ["", "### Error bars (95%, resampling whole days)", "",
                  f"Model skill {ci(bm, 'model')}; Markov {ci(bm, 'markov')}. Model minus Markov: "
                  f"{_f(dd.get('est'), True)} ({_f(dd.get('lo'), True)} to {_f(dd.get('hi'), True)}), {bm['n_days']:,} days."]
    fr = m.get("fair")
    if fr:
        lines += ["", f"## Fair comparison with professional forecasts ({fr['test_period'][0]} → {fr['test_period'][1]})", "",
                  f"ECMWF is turned into a probability, and the hybrid is fitted, on {fr['fit_period'][0]} → {fr['fit_period'][1]} only; "
                  f"every entry is scored on the same {fr['n_test']:,} later district-days.", "",
                  "| Forecast | Type | Brier ↓ | Skill vs clim (95% range) ↑ | AUC ↑ |", "|---|---|---|---|---|"]
        b = fr["bootstrap"]
        for k, e in fr["entries"].items():
            sk = ci(b, k) if k in b.get("skill", {}) else _f(e.get("bss"), True)
            lines.append(f"| {e['label']} | {e['kind']} | {_f(e.get('brier'))} | {sk} | {_f(e.get('auc'))} |")
        for k, v in b.get("diff", {}).items():
            lines.append(f"\n- {k}: {_f(v['est'], True)} ({_f(v['lo'], True)} to {_f(v['hi'], True)}); better in {100 * v['share_positive']:.0f}% of resamples")
    for key, title in [("heavy_fair", "Heavy-rain scorecard vs professional forecasts"), ("heavy_main", "Heavy-rain scorecard, all test years")]:
        hs = m.get(key)
        if hs:
            lines += ["", f"## {title} (≥ {meta['targets']['heavy_mm']} mm)", "",
                      f"{hs['events']} heavy-rain district-days out of {hs['n']:,}.", "",
                      "| Warning rule | Caught | Missed | False alarms | POD ↑ | FAR ↓ | CSI ↑ | Avg precision ↑ |", "|---|---|---|---|---|---|---|---|"]
            for e in hs["entries"].values():
                lines.append(f"| {e['label']} | {e['hits']} | {e['misses']} | {e['false_alarms']} | {_f(e['pod'], True)} | {_f(e['far'], True)} | {_f(e['csi'])} | {_f(e.get('avg_precision'))} |")
    for key, title in [("gauges", "Against real rain gauges (NOAA GSOD/GHCN)"), ("imerg", "Against NASA IMERG satellite rainfall")]:
        tr = m.get(key)
        if not tr:
            continue
        lines += ["", f"## {title}", ""]
        if tr.get("info"):
            lines.append(f"{tr['info'].get('stations')} gauges in {tr['info'].get('districts')} districts; day shift {tr['info'].get('shift_days')} "
                         f"(agreement with ERA5 by shift on pre-test years: {tr['info'].get('agreement_by_shift')}).")
        for part in ("strict", "nwp_era", "all_years"):
            r = tr.get(part)
            if not r:
                continue
            ne = tr.get("nwp_era")
            if part == "all_years" and ne and ne["n"] == r["n"] and ne["period"] == r["period"]:
                continue  # same rows as the table above; nothing new to show
            a = r["adjusted"]
            lines += ["", f"**{r['label']}**: {r['period'][0]} → {r['period'][1]}, {r['n']:,} rows on {r['n_days']:,} days, identical for every "
                          f"forecast. Rain (≥ 1 mm) on {_f(r['wet_freq_truth'], True)} of rows here vs {_f(r['wet_freq_era5'], True)} in ERA5.", "",
                      f"| Forecast | Ranking AUC (95% range) ↑ | Adjusted skill, {a['test_period'][0][:7]} → {a['test_period'][1][:7]} (95% range) ↑ |",
                      "|---|---|---|"]
            for k, e in r["ranking"]["auc"].items():
                sk = a["skill"].get(k, {})
                lines.append(f"| {k} | {e['auc']:.3f} ({e['lo']:.3f}–{e['hi']:.3f}) | {_f(sk.get('bss'), True)} ({_f(sk.get('lo'), True)} to {_f(sk.get('hi'), True)}) |")
            for k, v in r["ranking"]["diff"].items():
                av = a.get("diff", {}).get(k, {})
                lines.append(f"\n- {k}: AUC {v['est']:+.3f} ({v['lo']:+.3f} to {v['hi']:+.3f}); adjusted skill "
                             f"{_f(av.get('est'), True)} ({_f(av.get('lo'), True)} to {_f(av.get('hi'), True)})")
    h = m["heavy"]
    lines += ["", f"## Heavy rain (≥ {meta['targets']['heavy_mm']} mm) — research only", "",
              f"Base rate {_f(h['model']['base_rate'], True)}. Model Brier skill {_f(h['model'].get('bss'), True)}, "
              f"AUC {_f(h['model'].get('auc'))}, average precision {_f(h['model'].get('avg_precision'))} "
              f"(climatology AP {_f(h['climatology'].get('avg_precision'))}).", "",
              "## By season", "", "| Season | Model BSS | Markov BSS | Base rate |", "|---|---|---|---|"]
    for s in m["by_season"].values():
        lines.append(f"| {s['label']} | {_f(s['model'].get('bss'), True)} | {_f(s['markov'].get('bss'), True)} | {_f(s['model']['base_rate'], True)} |")
    lines += ["", "## By town", "", "| Town | Model BSS | Markov BSS | AUC | Base rate |", "|---|---|---|---|---|"]
    for t, s in m["by_town"].items():
        lines.append(f"| {t} | {_f(s['model'].get('bss'), True)} | {_f(s['markov'].get('bss'), True)} | {_f(s['model'].get('auc'))} | {_f(s['model']['base_rate'], True)} |")
    pl = m.get("places")
    if pl and pl.get("unseen_cells"):
        u = pl["unseen_cells"]
        lines += ["", f"## Towns the model never trained on ({pl['period'][0]} → {pl['period'][1]})", "",
                  f"{u['n_towns']} towns in {u['n_cells']} ERA5 cells that contain no district point; each cell counted once "
                  f"({u['n_days']:,} cell-days). Baselines from each town's own {pl['baseline_years'][0][:4]}–{pl['baseline_years'][1][:4]} data. "
                  f"Coverage: {pl['coverage']['towns_scored']} of {pl['coverage']['towns_total']} towns scored.", "",
                  "| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ |", "|---|---|---|---|"]
        for n in ["model", "markov", "climatology"]:
            s_ = u[n]
            lines.append(f"| {n} | {_f(s_.get('brier'))} | {_f(s_.get('bss'), True)} | {_f(s_.get('auc'))} |")
        if pl.get("district_cells"):
            d = pl["district_cells"]
            lines += ["", f"Towns sharing a district point's cell ({d['n_towns']} towns, {d['n_cells']} cells): "
                          f"model skill {_f(d['model'].get('bss'), True)}, Markov {_f(d['markov'].get('bss'), True)}."]
    alt = m.get("alt_truth_power")
    if alt:
        lines += ["", "## Cross-check against NASA POWER precipitation", "",
                  f"ERA5 and NASA POWER agree on rain/no-rain on {_f(alt['agreement_era5_vs_power'], True)} of test days. "
                  f"Scored against POWER instead of ERA5, the model's Brier skill is {_f(alt['model'].get('bss'), True)} "
                  f"(Markov {_f(alt['markov'].get('bss'), True)})."]
    lines += ["", "## Caveats", "",
              "- Truth is ERA5 reanalysis grid-cell rainfall (~25 km), not a rain gauge. It smooths showers, "
              "overcounts drizzle days and underestimates extremes.",
              "- The live model reads Open-Meteo's operational analysis for its inputs, not ERA5. The live ledger on the "
              "site measures how much that costs.",
              "- NWP forecasts are single deterministic runs scored yes/no, which penalises them under Brier score; "
              "compare CSI/POD/FAR for a like-for-like view.",
              "- Home advantage: the model learned from ERA5 and is graded against ERA5, so it has absorbed ERA5's quirks "
              "(e.g. its drizzle days); ECMWF, GFS and ICON have not. Treat the head-to-head as indicative until it is "
              "repeated against rain-gauge data.", "",
              "## Top features (share of gain)", ""]
    lines += [f"- `{k}` {100 * v:.1f}%" for k, v in full["importance"][:12]]
    (REPORTS / "REPORT.md").write_text("\n".join(lines) + "\n")
