import numpy as np
import pandas as pd
import pytest

from bearcast.models.evaluate import capture_by_month, poisson_deviance, summarize
from bearcast.models.train_gbm import fit_fold, period_slices


def _month(scores, y, month="2024-01-01"):
    return pd.DataFrame({"month": pd.Timestamp(month), "s": scores, "n_conflict": y})


def test_capture_counts_share_of_conflicts_in_top_cells():
    # 100 cells, top 1% = 1 cell, top 5% = 5 cells
    s = np.arange(100, dtype=float)[::-1]          # cell 0 has the highest score
    y = np.zeros(100); y[0], y[3], y[50] = 2, 1, 1   # 4 conflicts
    df = _month(s, y)
    assert capture_by_month(df, "s", 0.01).iloc[0] == pytest.approx(0.5)
    assert capture_by_month(df, "s", 0.05).iloc[0] == pytest.approx(0.75)


def test_capture_averages_months_and_skips_empty_ones():
    a = _month(np.arange(100.0)[::-1], [1] + [0] * 99, "2024-01-01")
    b = _month(np.arange(100.0)[::-1], [0] * 100, "2024-02-01")     # no conflicts: skipped
    s = capture_by_month(pd.concat([a, b]), "s", 0.01)
    assert len(s) == 1 and s.iloc[0] == 1.0


def test_cold_capture_counts_only_cold_cells():
    s = np.arange(100, dtype=float)[::-1]
    y = np.zeros(100); y[0], y[90] = 1, 1
    df = _month(s, y)
    df["conf_sum12"] = 5.0; df.loc[90, "conf_sum12"] = 0.0         # only cell 90 is cold
    out = summarize(df, ["s"], ks=(0.05,))
    assert out.loc["s", "capture@5%"] == pytest.approx(0.5)
    assert out.loc["s", "cold_capture@5%"] == pytest.approx(0.0)


def test_poisson_deviance_zero_when_perfect():
    y = np.array([0, 1, 3])
    assert poisson_deviance(y, y + 1e-12) == pytest.approx(0, abs=1e-6)
    assert poisson_deviance(y, np.full(3, 1.0)) > 0


def test_fit_fold_learns_signal_and_beats_random():
    rng = np.random.default_rng(0)
    months = pd.date_range("2020-01-01", "2023-12-01", freq="MS")
    n_cells = 400
    risk = rng.normal(size=n_cells)                                  # hidden per-cell risk
    t = pd.DataFrame({"month": np.repeat(months, n_cells), "x": np.tile(risk, len(months)),
                      "noise": rng.normal(size=len(months) * n_cells)})
    t["n_conflict"] = rng.poisson(np.exp(t["x"] - 2.5))
    t["conf_sum12"] = 1.0
    params = {"objective": "poisson", "learning_rate": 0.1, "num_leaves": 7, "min_data_in_leaf": 50, "verbose": -1}
    X = t[["x", "noise"]].to_numpy("float32")
    booster, pred = fit_fold(X, t["n_conflict"].to_numpy("float32"), t["month"].to_numpy(), ["x", "noise"],
                             ("2022-12-01", "2023-01-01", "2023-12-01"), params, 200, 20)
    ev = t[t["month"] >= "2023-01-01"].assign(pred=pred, rand=rng.random((t["month"] >= "2023-01-01").sum()))
    m = summarize(ev, ["pred", "rand"], ks=(0.05,))
    assert m.loc["pred", "capture@5%"] > 2 * m.loc["rand", "capture@5%"]
    gain = dict(zip(booster.feature_name(), booster.feature_importance("gain")))
    assert gain["x"] > gain["noise"]


def test_period_slices_never_include_later_months():
    months = np.repeat(pd.date_range("2021-01-01", "2025-06-01", freq="MS").to_numpy(), 3)
    tr, ev = period_slices(months, ("2023-12-01", "2024-01-01", "2024-12-01"))
    assert months[tr].max() == np.datetime64("2023-12-01") and months[tr].min() == np.datetime64("2021-01-01")
    assert months[ev].min() == np.datetime64("2024-01-01") and months[ev].max() == np.datetime64("2024-12-01")
    assert ev.stop - ev.start == 36



def test_within_group_capture_ranks_only_inside_the_group():
    from bearcast.models.diagnose import within_group_capture
    s = np.arange(100, dtype=float)[::-1]
    y = np.zeros(100); y[95] = 1                       # the only conflict is in a low-scored cell
    df = _month(s, y)
    group = pd.Series(False, index=df.index); group[90:] = True    # 10 cells; cell 95 is 6th of 10
    assert capture_by_month(df, "s", 0.10).iloc[0] == 0.0         # vs all cells: missed
    assert within_group_capture(df, "s", 0.5, group).iloc[0] == 0.0
    assert within_group_capture(df, "s", 0.6, group).iloc[0] == 1.0


def test_paired_bootstrap_detects_consistent_gain():
    from bearcast.models.diagnose import paired_bootstrap
    a = pd.Series(np.linspace(0.4, 0.5, 24))
    r = paired_bootstrap(a, a + 0.02)
    assert r["mean_diff"] == pytest.approx(0.02) and r["ci95"][0] > 0 and r["share_months_b_better"] == 1.0



def test_pick_best_uses_mean_deviance_across_folds():
    from bearcast.models.tune import pick_best
    r = pd.DataFrame({"config": ["a", "a", "b", "b"], "fold": ["x", "y", "x", "y"],
                      "poisson_dev": [0.030, 0.050, 0.035, 0.040], "capture@1%": [0.9, 0.9, 0.1, 0.1]})
    assert pick_best(r) == "b"          # 0.0375 < 0.040, even though "a" captures more



def test_calibration_factor_uses_only_the_chosen_configs_validation_folds():
    from bearcast.models.final_test import calibration_factor
    rep = {"per_fold": [{"config": "tweedie", "pred/actual": 0.90}, {"config": "tweedie", "pred/actual": 0.95},
                        {"config": "base", "pred/actual": 1.50}]}
    assert calibration_factor(rep, "tweedie") == pytest.approx(1 / 0.925)
    with pytest.raises(ValueError):
        calibration_factor(rep, "missing")