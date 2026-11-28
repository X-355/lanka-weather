"""Verification helpers: day-block bootstrap error bars, heavy-rain scorecard, scoring against other truths.

Bootstrap by day: on any one day, a monsoon system rains on many districts together, so district-days are not
independent. Resampling whole days (every district on a drawn day comes along) gives honest error bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from .metrics import reliability, scores

B_DEFAULT = 1000


def _bss(p, y, clim):
    ref = np.mean((clim - y) ** 2)
    return 1 - np.mean((p - y) ** 2) / ref if ref > 0 else np.nan


def bootstrap(df: pd.DataFrame, ycol: str, climcol: str, forecasts: dict[str, str], pairs: list[tuple[str, str]] = (),
              B: int = B_DEFAULT, seed: int = 0) -> dict:
    """95% intervals for Brier skill (vs climatology) of each forecast, and for differences between pairs."""
    d = df.dropna(subset=[ycol, climcol, *forecasts.values()])
    if d.empty:
        return {}
    days = d["target_date"].to_numpy()
    uniq, inv = np.unique(days, return_inverse=True)
    rows_of = [np.flatnonzero(inv == k) for k in range(len(uniq))]
    y = d[ycol].to_numpy(float)
    c = d[climcol].to_numpy(float)
    P = {n: d[col].to_numpy(float) for n, col in forecasts.items()}
    rng = np.random.default_rng(seed)
    draws = {n: np.empty(B) for n in forecasts}
    for b in range(B):
        s = np.concatenate([rows_of[k] for k in rng.integers(0, len(uniq), len(uniq))])
        for n in forecasts:
            draws[n][b] = _bss(P[n][s], y[s], c[s])
    out = {"n_days": int(len(uniq)), "n": int(len(d)), "B": B, "skill": {}, "diff": {}}
    for n in forecasts:
        out["skill"][n] = {"bss": round(float(_bss(P[n], y, c)), 4),
                           "lo": round(float(np.nanpercentile(draws[n], 2.5)), 4),
                           "hi": round(float(np.nanpercentile(draws[n], 97.5)), 4)}
    for a, b_ in pairs:
        if a in draws and b_ in draws:
            dd = draws[a] - draws[b_]
            out["diff"][f"{a} - {b_}"] = {"est": round(float(_bss(P[a], y, c) - _bss(P[b_], y, c)), 4),
                                          "lo": round(float(np.nanpercentile(dd, 2.5)), 4),
                                          "hi": round(float(np.nanpercentile(dd, 97.5)), 4),
                                          "share_positive": round(float(np.mean(dd > 0)), 3)}
    return out


def heavy_scorecard(df: pd.DataFrame, ycol: str, entries: dict[str, dict]) -> dict:
    """entries: name -> {"col": column, "threshold": warn when col >= threshold, "label": ..., "kind": ...}.

    Counts (hits / misses / false alarms) are on rare days only; accuracy and Brier would look fine
    even for a forecast that misses most floods, so they are deliberately not the headline here.
    """
    d = df.dropna(subset=[ycol])
    y = d[ycol].to_numpy(bool)
    out = {"n": int(len(d)), "events": int(y.sum()), "n_days": int(d["target_date"].nunique()), "entries": {}}
    for name, e in entries.items():
        if e["col"] not in d:
            continue
        v = d[e["col"]].to_numpy(float)
        ok = ~np.isnan(v)
        w = (v >= e["threshold"]) & ok
        hits, misses = int((w & y).sum()), int((~w & y & ok).sum())
        fa = int((w & ~y).sum())
        row = {"label": e.get("label", name), "kind": e.get("kind", ""), "threshold": e["threshold"],
               "hits": hits, "misses": misses, "false_alarms": fa, "warnings": int(w.sum()),
               "pod": round(hits / (hits + misses), 3) if hits + misses else None,
               "far": round(fa / (hits + fa), 3) if hits + fa else None,
               "csi": round(hits / (hits + misses + fa), 3) if hits + misses + fa else None}
        if ok.sum() and len(np.unique(y[ok])) == 2:
            row["avg_precision"] = round(float(average_precision_score(y[ok], v[ok])), 3)
        if e.get("probability"):
            row["reliability"] = reliability(y[ok].astype(float), v[ok], bins=5)
        out["entries"][name] = row
    return out


def score_against(df: pd.DataFrame, truth: pd.DataFrame, rain_mm: float, heavy_mm: float,
                  forecasts: dict[str, str], climcol: str = "p_clim", pairs=(), label: str = "") -> dict | None:
    """Score forecasts (rows: town, target_date) against another truth (rows: town, target_date, obs_mm).

    Climatology for the skill score is the truth's own (smoothed day-of-year wet frequency per town, computed
    from the truth before the scored period when available), so a truth that is wetter or drier than ERA5
    doesn't distort everyone's skill.
    """
    j = df.merge(truth, on=["town", "target_date"], how="inner").dropna(subset=["obs_mm"])
    if len(j) < 200:
        return None
    j = j.assign(t_rain=(j["obs_mm"] >= rain_mm).astype(float), t_heavy=(j["obs_mm"] >= heavy_mm).astype(float))
    if "clim_truth" in j:
        j[climcol] = j["clim_truth"].fillna(j[climcol])
    fc = {n: c for n, c in forecasts.items() if c in j and j[c].notna().any()}
    out = {"label": label, "period": [str(j["target_date"].min().date()), str(j["target_date"].max().date())],
           "n": int(len(j)), "towns": int(j["town"].nunique()),
           "agreement_with_era5": round(float((j["t_rain"] == j["y_rain"]).mean()), 3) if "y_rain" in j else None,
           "entries": {}}
    for n, c in fc.items():
        sub = j.dropna(subset=[c])
        out["entries"][n] = {k: v for k, v in scores(sub["t_rain"], sub[c], sub[climcol]).items()
                             if k in ("n", "base_rate", "brier", "bss", "auc", "pod", "far", "csi")}
    out["bootstrap"] = bootstrap(j, "t_rain", climcol, fc, pairs=pairs)
    return out
