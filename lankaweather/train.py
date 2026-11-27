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
from .config import ERA5_DIR, MODELS, NWP_DIR, REPORTS, load_config, towns
from .features import CATEGORICAL, SEASON_NAMES, SEASON_OF_MONTH, build, feature_columns
from .fetch import load_history, load_power
from .metrics import reliability, scores

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
