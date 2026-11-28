"""Guards for the things that would make the evaluation dishonest."""
import numpy as np
import pandas as pd
import pytest

from lankaweather import features, metrics
from lankaweather.config import town_keys


def _toy(n=120, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    rows = []
    for t in town_keys():
        rows.append(pd.DataFrame({
            "date": dates, "town": t,
            "precip": rng.gamma(0.6, 6, n), "rh": rng.normal(80, 5, n), "pmsl": rng.normal(1010, 2, n),
            "cloud": rng.uniform(0, 100, n), "tmax": rng.normal(31, 1, n), "tmin": rng.normal(24, 1, n),
            "tmean": rng.normal(27, 1, n), "dew": rng.normal(23, 1, n), "wind_dir": rng.uniform(0, 360, n),
        }))
    return pd.concat(rows, ignore_index=True)


def test_features_do_not_see_the_future():
    """Changing everything after day k must not change any feature of days <= k."""
    df = _toy()
    k = pd.Timestamp("2020-03-01")
    a = features.build(df)
    df2 = df.copy()
    future = df2["date"] > k
    for c in ["precip", "rh", "pmsl", "cloud", "tmax", "tmin", "tmean", "dew", "wind_dir"]:
        df2.loc[future, c] = df2.loc[future, c] * 3 + 7
    b = features.build(df2)
    cols = features.feature_columns(a)
    pa = a[a["date"] <= k].sort_values(["town", "date"])[cols].reset_index(drop=True)
    pb = b[b["date"] <= k].sort_values(["town", "date"])[cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(pa, pb)


def test_target_is_tomorrow():
    df = _toy()
    f = features.build(df).sort_values(["town", "date"])
    g = f[f["town"] == "colombo"].reset_index(drop=True)
    assert np.allclose(g["y_amount"].iloc[:-1].to_numpy(), g["precip"].iloc[1:].to_numpy())
    assert np.isnan(g["y_rain"].iloc[-1])
    assert not any(c.startswith("y_") for c in features.feature_columns(f))


def test_gaps_are_not_bridged():
    df = _toy()
    df = df[~((df["town"] == "kandy") & (df["date"] == "2020-02-10"))]
    f = features.build(df)
    row = f[(f["town"] == "kandy") & (f["date"] == "2020-02-11")]
    assert row["precip_lag1"].isna().all()


def test_brier_and_skill():
    y = np.array([1, 0, 1, 0])
    s = metrics.scores(y, np.array([1, 0, 1, 0.0]), clim=np.full(4, 0.5))
    assert s["brier"] == 0 and s["bss"] == 1 and s["csi"] == 1
    s = metrics.scores(y, np.full(4, 0.5), clim=np.full(4, 0.5))
    assert s["bss"] == pytest.approx(0)


def test_ledger_never_replaces_and_keeps_each_slot(tmp_path, monkeypatch):
    """Saved forecasts are never changed. A second run in the same evening adds nothing; an issue at another
    time (off-schedule) is kept as its own row and scored separately."""
    from lankaweather import predict
    monkeypatch.setattr(predict, "LEDGER", tmp_path / "ledger.csv")
    row = dict(issued_at="2026-01-01T17:35:00Z", issue_date="2026-01-01", target_date="2026-01-02", town="colombo",
               p_rain=0.3, p_heavy=0.01, p_clim=0.4, p_markov=0.4, model_version="v", input_source="x")
    predict.append_ledger(pd.DataFrame([row]))                                                  # 23:05 SLT: evening
    predict.append_ledger(pd.DataFrame([dict(row, issued_at="2026-01-01T18:10:00Z", p_rain=0.9)]))  # 23:40: same slot
    predict.append_ledger(pd.DataFrame([dict(row, issued_at="2026-01-01T01:12:00Z", p_rain=0.7)]))  # 06:42: off-schedule
    led = predict.read_ledger().sort_values("issued_at")
    assert len(led) == 2
    ev = led[led["issue_slot"] == "evening"].iloc[0]
    off = led[led["issue_slot"] == "off-schedule"].iloc[0]
    assert ev["p_rain"] == 0.3 and abs(ev["lead_hours"] - 0.9) < 0.05
    assert off["p_rain"] == 0.7 and abs(off["lead_hours"] - 17.3) < 0.05
    assert predict.ledger_summary()["issued"] == 1  # headline counts the evening issue only


def test_town_features_do_not_see_the_future():
    """Same guard for towns: their features use their own past and the districts' same-day context only."""
    df = _toy()
    pts = pd.DataFrame([{"name": "Testtown", "district": "kandy", "lat": 7.3, "lon": 80.6}])
    town = df[df["town"] == "kandy"].assign(town="Testtown", elevation=450.0)
    k = pd.Timestamp("2020-03-01")
    a = features.build_points(df, town, pts)
    df2, town2 = df.copy(), town.copy()
    for frame in (df2, town2):
        future = frame["date"] > k
        frame.loc[future, "precip"] = frame.loc[future, "precip"] * 3 + 7
    b = features.build_points(df2, town2, pts)
    cols = [c for c in features.feature_columns(a) if c in b]
    pd.testing.assert_frame_equal(a[a["date"] <= k][cols].reset_index(drop=True), b[b["date"] <= k][cols].reset_index(drop=True))
