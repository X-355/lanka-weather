"""Verification metrics for probabilistic and yes/no rain forecasts."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

EPS = 1e-4


def _safe(fn, y, p):
    if len(np.unique(y)) < 2:  # undefined with a single class (e.g. a short live window)
        return None
    v = float(fn(y, p))
    return None if np.isnan(v) else v


def contingency(y: np.ndarray, yes: np.ndarray) -> dict:
    y = y.astype(bool)
    yes = yes.astype(bool)
    hits = int((y & yes).sum())
    misses = int((y & ~yes).sum())
    fa = int((~y & yes).sum())
    cn = int((~y & ~yes).sum())
    n = hits + misses + fa + cn
    return {
        "accuracy": (hits + cn) / n if n else None,
        "pod": hits / (hits + misses) if hits + misses else None,          # probability of detection
        "far": fa / (hits + fa) if hits + fa else None,                     # false alarm ratio
        "csi": hits / (hits + misses + fa) if hits + misses + fa else None,  # critical success index
        "freq_bias": (hits + fa) / (hits + misses) if hits + misses else None,
    }


def scores(y, p, clim=None, threshold: float = 0.5) -> dict:
    """y: 0/1 outcomes; p: probabilities (or 0/1 for deterministic forecasts); clim: reference probs."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    out = {"n": int(len(y)), "base_rate": float(y.mean()) if len(y) else None}
    if not len(y):
        return out
    brier = float(np.mean((p - y) ** 2))
    out["brier"] = brier
    if clim is not None:
        ref = float(np.mean((np.asarray(clim, dtype=float) - y) ** 2))
        out["bss"] = 1 - brier / ref if ref > 0 else None  # Brier skill score vs climatology
    pc = np.clip(p, EPS, 1 - EPS)
    out["log_loss"] = float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc)))
    out["auc"] = _safe(roc_auc_score, y, p)
    out["avg_precision"] = _safe(average_precision_score, y, p)
    out.update(contingency(y, p >= threshold))
    return out


def reliability(y, p, bins: int = 10) -> list[dict]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({"bin_lo": float(edges[b]), "bin_hi": float(edges[b + 1]), "n": int(m.sum()),
                     "mean_p": float(p[m].mean()), "obs_freq": float(y[m].mean())})
    return rows
