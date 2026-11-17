import h3
import numpy as np
import pandas as pd
import pytest

from bearcast.features import history as H

CENTER = h3.latlng_to_cell(29.0, -81.6, 7)


def _panel(counts_by_cell: dict[str, list[int]], start="2015-01"):
    months = pd.date_range(start + "-01", periods=len(next(iter(counts_by_cell.values()))), freq="MS")
    rows = []
    for c, counts in counts_by_cell.items():
        for m, n in zip(months, counts):
            rows.append({"cell": c, "month": m, "n_conflict": n, "n_encounter": n,
                         "n_sighting": 0, "n_bear_welfare": 0, "split": "train"})
    panel = pd.DataFrame(rows)
    cells = pd.DataFrame({"cell": list(counts_by_cell), "range_rank": 4, "lat": 29.0, "lon": -81.6})
    return panel, cells


def test_basic_lags_sums_and_months_since():
    nbrs = sorted(h3.grid_ring(CENTER, 1))
    counts = {CENTER: [0] * 14 + [2, 0, 1]}  # conflicts in month 14 and 16
    counts.update({n: [0] * 17 for n in nbrs})
    counts[nbrs[0]] = [1] * 17                # one neighbour has a conflict every month
    panel, cells = _panel(counts)
    f = H.build_features(panel, cells, "2016-01").set_index(["cell", "month"])

    m15, m16 = pd.Timestamp("2016-04-01"), pd.Timestamp("2016-05-01")  # month index 15, 16
    r = f.loc[(CENTER, m15)]
    assert r.conf_lag1 == 2 and r.conf_lag2 == 0 and r.conf_sum3 == 2
    assert r.months_since_conf == 1
    assert r.n_conflict == 0                                  # the target is this month's count
    r = f.loc[(CENTER, m16)]
    assert r.conf_lag1 == 0 and r.conf_lag2 == 2 and r.months_since_conf == 2 and r.n_conflict == 1
    assert r.nbr1_conf_sum12 == 12 and r.nbr1_conf_lag1 == 1  # 12 months x 1 from one neighbour
    assert r.state_conf_lag1 == 1                             # statewide last month = the neighbour's 1
    early = f.loc[(CENTER, pd.Timestamp("2016-01-01"))]
    assert early.months_since_conf == H.NEVER


def test_no_feature_uses_the_current_or_future_month():
    rng = np.random.default_rng(0)
    cells = sorted(h3.grid_disk(CENTER, 2))
    counts = {c: list(rng.poisson(0.5, 30)) for c in cells}
    panel, tbl = _panel(counts)
    f1 = H.build_features(panel, tbl, "2016-01")

    cut = pd.Timestamp("2016-06-01")
    panel2 = panel.copy()
    future = panel2["month"] >= cut
    panel2.loc[future, "n_conflict"] += 5   # change the present and the future only
    f2 = H.build_features(panel2, tbl, "2016-01")

    past_and_now = f1["month"] <= cut
    for col in H.FEATURE_COLS:
        a = f1.loc[past_and_now, col].to_numpy()
        b = f2.loc[past_and_now, col].to_numpy()
        assert np.allclose(a, b, equal_nan=True), f"{col} leaks information from month t or later"


def test_needs_a_year_of_history():
    panel, cells = _panel({CENTER: [0] * 20})
    with pytest.raises(ValueError):
        H.build_features(panel, cells, "2015-06")