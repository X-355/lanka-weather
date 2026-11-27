"""Synthetic stand-in data with the same schema as the real fetchers.

Used only for CI smoke tests and offline development. Nothing produced from synthetic data is
ever published: `train` records `data_source` in the metadata and `publish-hf` refuses it.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import ERA5_DIR, NWP_DIR, POWER_DIR, load_config, towns

# Rough monthly rain-day propensity by zone (Jan..Dec), shaped like Sri Lanka's monsoons.
SEASON = {
    "wet":          [0.25, 0.25, 0.40, 0.60, 0.70, 0.65, 0.55, 0.55, 0.60, 0.75, 0.75, 0.50],
    "intermediate": [0.40, 0.25, 0.30, 0.55, 0.45, 0.40, 0.35, 0.35, 0.40, 0.65, 0.75, 0.60],
    "dry":          [0.45, 0.25, 0.15, 0.30, 0.15, 0.08, 0.10, 0.12, 0.18, 0.45, 0.70, 0.65],
}


def generate(end: date | None = None, seed: int = 7) -> None:
    cfg = load_config()
    rng = np.random.default_rng(seed)
    start = date.fromisoformat(cfg["data"]["start_date"])
    end = end or date.today() - timedelta(days=cfg["data"]["archive_delay_days"])
    dates = pd.date_range(start, end, freq="D")
    n = len(dates)
    shared = np.zeros(n)  # island-wide synoptic signal, so towns co-vary
    for i in range(1, n):
        shared[i] = 0.8 * shared[i - 1] + rng.normal(0, 0.6)
    for town in towns():
        base = np.array(SEASON[town.zone])[dates.month - 1]
        m = np.zeros(n)
        for i in range(1, n):
            m[i] = 0.65 * m[i - 1] + rng.normal(0, 0.8)
        logit = np.log(base / (1 - base)) + 1.2 * m + 0.6 * shared
        p = 1 / (1 + np.exp(-logit))
        wet = rng.random(n) < p
        amount = np.where(wet, rng.gamma(0.8, 12 * (0.5 + p), n) + 1.0, rng.random(n) * 0.8)
        rh = 70 + 8 * np.tanh(m) + 6 * p + rng.normal(0, 3, n) - town.elevation / 400
        cloud = np.clip(40 + 25 * np.tanh(m + 0.5 * shared) + 20 * p + rng.normal(0, 10, n), 0, 100)
        tmax = 31 - town.elevation * 0.0065 - 2 * p + rng.normal(0, 1, n)
        tmin = tmax - 7 + 2 * p + rng.normal(0, 0.8, n)
        df = pd.DataFrame({
            "date": dates,
            "precip": amount.round(1),
            "rain": amount.round(1),
            "precip_hours": np.where(wet, rng.integers(1, 18, n), 0).astype(float),
            "tmax": tmax, "tmin": tmin, "tmean": (tmax + tmin) / 2,
            "rh": rh, "dew": tmin + rng.normal(0, 0.5, n),
            "pmsl": 1010 - 2 * np.tanh(m + shared) + rng.normal(0, 0.8, n),
            "cloud": cloud,
            "wind_max": np.abs(15 + 5 * shared + rng.normal(0, 4, n)),
            "gust_max": np.abs(30 + 8 * shared + rng.normal(0, 6, n)),
            "wind_dir": np.where(np.isin(dates.month, [5, 6, 7, 8, 9]), 240, 45) + rng.normal(0, 30, n),
            "sw_rad": np.clip(24 - 0.12 * cloud + rng.normal(0, 1.5, n), 3, 30),
            "et0": np.clip(5 - 0.03 * cloud + rng.normal(0, 0.4, n), 0.5, 8),
        })
        tdir = ERA5_DIR / town.key
        tdir.mkdir(parents=True, exist_ok=True)
        for y, g in df.groupby(df["date"].dt.year):
            g.to_parquet(tdir / f"{y}.parquet", index=False)

        # NWP: a noisy but skilful forecast of the same day's precipitation.
        nwp_start = pd.Timestamp(cfg["nwp"]["start_date"])
        sel = df[df["date"] >= nwp_start][["date", "precip"]].copy()
        NWP_DIR.mkdir(parents=True, exist_ok=True)
        for k, model in enumerate(cfg["nwp"]["models"]):
            noise = rng.lognormal(0, 0.9 + 0.2 * k, len(sel))
            fake = sel["precip"].to_numpy() * noise + rng.gamma(0.4, 2.0, len(sel)) * (rng.random(len(sel)) < 0.3)
            sel[model] = fake.round(1)
        sel.drop(columns="precip").to_parquet(NWP_DIR / f"{town.key}.parquet", index=False)

        POWER_DIR.mkdir(parents=True, exist_ok=True)
        clear = 6.8 - 0.6 * np.cos(2 * np.pi * (dates.dayofyear - 80) / 365)
        pd.DataFrame({
            "date": dates,
            "ghi": np.clip(clear * (1 - 0.0055 * cloud) + rng.normal(0, 0.3, n), 1, 8),
            "ghi_clear": clear,
            "precip": (amount * rng.lognormal(0, 0.5, n)).round(1),
            "t2m": (tmax + tmin) / 2,
        }).to_parquet(POWER_DIR / f"{town.key}.parquet", index=False)
    (ERA5_DIR / "SYNTHETIC").write_text("This data is synthetic. Do not publish.\n")
