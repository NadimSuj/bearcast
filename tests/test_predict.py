import h3
import numpy as np
import pandas as pd
import pytest

from bearcast.models.predict import add_ranks, next_month_features, rolling_calibration

CENTER = h3.latlng_to_cell(29.2, -81.7, 7)
CELLS = sorted(h3.grid_disk(CENTER, 1))


def _panel(months=30):
    m = pd.date_range("2022-01-01", periods=months, freq="MS")
    p = pd.DataFrame([(c, mm) for c in CELLS for mm in m], columns=["cell", "month"])
    rng = np.random.default_rng(0)
    p["n_conflict"] = rng.poisson(0.5, len(p))
    p["n_encounter"] = p["n_conflict"] + rng.poisson(0.5, len(p))
    p["n_bear_welfare"] = 0
    return p


def _cell_tbl():
    return pd.DataFrame({"cell": CELLS, "range_rank": 4, "lat": 29.2, "lon": -81.7})


def test_next_month_features_are_for_the_following_month_and_use_last_months_counts():
    p = _panel()
    f = next_month_features(p, _cell_tbl())
    last = p["month"].max()
    assert len(f) == len(CELLS)
    assert (f["month"] == last + pd.offsets.MonthBegin(1)).all()
    lag1 = p[p["month"] == last].set_index("cell")["n_conflict"]
    got = f.set_index(f["cell"].astype(str))["conf_lag1"]
    assert (got.reindex(lag1.index).to_numpy() == lag1.to_numpy()).all()
    s12 = p[p["month"] > last - pd.DateOffset(months=12)].groupby("cell")["n_conflict"].sum()
    assert (got.index.size == s12.size)
    assert np.allclose(f.set_index(f["cell"].astype(str))["conf_sum12"].reindex(s12.index), s12)


def test_rolling_calibration_uses_last_12_months_only():
    months = pd.date_range("2025-01-01", periods=18, freq="MS")
    t = pd.DataFrame({"month": np.repeat(months, 2), "n_conflict": 0.0, "gbm_full_raw": 1.0})
    t.loc[t["month"] >= months[6], "n_conflict"] = 1.2       # last 12 months: actual 1.2 vs pred 1.0
    f, start, end = rolling_calibration(t)
    assert f == pytest.approx(1.2) and start == "2025-07-01" and end == "2026-06-01"


def test_ranks_tiers_and_emerging():
    df = pd.DataFrame({"cell": list("abcdefghij") * 10, "expected_calls": np.linspace(1, 0, 100),
                       "conf_sum12": [0.0, 1.0] * 50, "conf_hist_rate": 0.1})
    r = add_ranks(df)
    assert r.loc[0, "risk_rank"] == 1 and r.loc[0, "tier"] == "very high"
    assert (r["tier"] == "high").sum() == 4 and (r["tier"] == "elevated").sum() == 15
    assert r["emerging_rank"].notna().sum() == 50 and r.loc[0, "emerging_rank"] == 1
    assert pd.isna(r.loc[1, "emerging_rank"])