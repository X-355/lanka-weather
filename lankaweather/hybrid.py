"""Hybrid forecast: the Weather Lab model's probability corrected with professional forecast rain.

Model output statistics (MOS), the way forecast offices post-process physics models: a small logistic regression
on [logit(Weather Lab probability), log1p(ECMWF rain), log1p(GFS rain), log1p(ICON rain)].

Honesty rules:
- The Weather Lab probabilities used to fit the hybrid come from the *evaluation* model (trained on data up to
  the end of the validation years), so on the NWP era (2024+) they are genuinely out of sample.
- Headline scores use a strict forward split inside the NWP era: fit on the first half, score on the second.
- For independent truths (rain gauges, NASA IMERG) that only overlap part of the era, out-of-fold predictions
  from a 2-fold blocked split are also kept. Each half is predicted by a model fitted on the other half only.
- "ECMWF calibrated" is ECMWF's own rain turned into a probability by the same kind of fit, so ECMWF is compared
  probability against probability, not as a yes/no forecast.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .config import MODELS

log = logging.getLogger(__name__)

NWP = ["nwp_ecmwf_ifs025", "nwp_gfs_seamless", "nwp_icon_seamless"]
EPS = 1e-4


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _lg(x):
    return np.log1p(np.clip(np.asarray(x, dtype=float), 0, None))


def design(df: pd.DataFrame, pcol: str | None) -> np.ndarray:
    """Feature matrix. pcol=None gives the ECMWF-only calibration (no Weather Lab input)."""
    if pcol is None:
        return np.c_[_lg(df["nwp_ecmwf_ifs025"])]
    e = df["nwp_ecmwf_ifs025"]
    # A missing GFS/ICON value falls back to ECMWF's, so one late model never blocks the hybrid.
    g = df["nwp_gfs_seamless"].fillna(e)
    i = df["nwp_icon_seamless"].fillna(e)
    return np.c_[_logit(df[pcol]), _lg(e), _lg(g), _lg(i)]


def fit(df: pd.DataFrame, pcol: str | None, target: str) -> LogisticRegression:
    return LogisticRegression(C=1.0, max_iter=1000).fit(design(df, pcol), df[target].astype(int))


def to_dict(m: LogisticRegression, pcol: str | None) -> dict:
    return {"inputs": ["logit_p", "log1p_ecmwf", "log1p_gfs", "log1p_icon"] if pcol else ["log1p_ecmwf"],
            "coef": [round(float(c), 6) for c in m.coef_[0]], "intercept": round(float(m.intercept_[0]), 6)}


def apply(spec: dict, p_base, ecmwf, gfs=None, icon=None) -> np.ndarray:
    """Use a saved hybrid with plain arrays (live forecasts). NaN ECMWF → NaN result (caller falls back)."""
    e = np.asarray(ecmwf, dtype=float)
    g = e if gfs is None else np.where(np.isnan(np.asarray(gfs, dtype=float)), e, gfs)
    i = e if icon is None else np.where(np.isnan(np.asarray(icon, dtype=float)), e, icon)
    if len(spec["coef"]) == 1:
        z = spec["intercept"] + spec["coef"][0] * _lg(e)
    else:
        X = np.c_[_logit(p_base), _lg(e), _lg(g), _lg(i)]
        z = spec["intercept"] + X @ np.asarray(spec["coef"])
    out = 1 / (1 + np.exp(-z))
    return np.where(np.isnan(e), np.nan, out)


def nwp_era(test: pd.DataFrame) -> pd.DataFrame:
    return test.dropna(subset=["nwp_ecmwf_ifs025"]).copy()


def evaluate(test: pd.DataFrame) -> tuple[dict | None, pd.DataFrame | None]:
    """Forward-split headline + 2-fold out-of-fold columns. Returns (summary, frame with new columns)."""
    d = nwp_era(test)
    if len(d) < 2000 or d["target_date"].nunique() < 120:
        return None, None
    days = np.sort(d["target_date"].unique())
    cut = days[len(days) // 2]
    first = d["target_date"] < cut
    specs = {"p_hybrid": ("p_rain", "y_rain"), "p_hybrid_heavy": ("p_heavy", "y_heavy"),
             "p_ecmwf_cal": (None, "y_rain"), "p_ecmwf_cal_heavy": (None, "y_heavy")}
    for col, (pcol, target) in specs.items():
        # forward: fit on first half, predict second half only
        m = fit(d[first], pcol, target)
        d.loc[~first, col] = m.predict_proba(design(d[~first], pcol))[:, 1]
        # out-of-fold: each half predicted by the model fitted on the other half
        oof = np.empty(len(d))
        for tr in (first, ~first):
            mm = fit(d[tr], pcol, target)
            oof[~tr.to_numpy()] = mm.predict_proba(design(d[~tr], pcol))[:, 1]
        d[col + "_oof"] = oof
    summary = {"fit_period": [str(pd.Timestamp(days[0]).date()), str((pd.Timestamp(cut) - pd.Timedelta(days=1)).date())],
               "test_period": [str(pd.Timestamp(cut).date()), str(pd.Timestamp(days[-1]).date())],
               "n_fit": int(first.sum()), "n_test": int((~first).sum())}
    return summary, d


def fit_production(test: pd.DataFrame) -> dict | None:
    """Fit on the whole NWP era (evaluation-model probabilities) and save for live use."""
    d = nwp_era(test)
    if len(d) < 2000:
        return None
    out = {"rain": to_dict(fit(d, "p_rain", "y_rain"), "p_rain"),
           "heavy": to_dict(fit(d, "p_heavy", "y_heavy"), "p_heavy"),
           "ecmwf_cal_rain": to_dict(fit(d, None, "y_rain"), None),
           "fit_period": [str(d["target_date"].min().date()), str(d["target_date"].max().date())], "n": int(len(d))}
    MODELS.mkdir(exist_ok=True)
    (MODELS / "hybrid.json").write_text(json.dumps(out, indent=1))
    return out


def load_spec() -> dict | None:
    p = MODELS / "hybrid.json"
    return json.loads(p.read_text()) if p.exists() else None
