import numpy as np
import pandas as pd
import pytest

from bearcast.bears import movement as M


def _grid(n=200, cls_left=0, cls_right=6):
    """n x n 100 m grid: left half class cls_left (forest), right half cls_right (developed); a busy road column."""
    cls = np.full((n, n), cls_left, np.uint8)
    cls[:, n // 2:] = cls_right
    busy = np.zeros((n, n), np.uint8)
    busy[:, 150] = 1
    d = np.full((n, n), 5000, np.uint16)
    return M.Grid(cls, d.copy(), d.copy(), busy, x0=0.0, y0=n * 100.0, cell=100.0)


def test_period_and_season():
    t = pd.Series(pd.to_datetime(["2012-10-01 07:00", "2012-03-01 15:00", "2012-03-01 23:30", "2012-03-01 18:00"])
                  .tz_localize("America/New_York").tz_convert("UTC"))
    period, fall = M.period_season(t)
    assert list(period) == [1, 2, 0, 3] and list(fall) == [1, 0, 0, 0]


def test_hourly_steps_snaps_to_hours_and_computes_turns():
    t = pd.to_datetime(["2012-01-01 00:01", "2012-01-01 00:31", "2012-01-01 01:00", "2012-01-01 02:02",
                        "2012-01-01 05:00", "2012-01-01 06:00"], utc=True)
    gps = pd.DataFrame({"bear": "a", "t": t, "x": [0, 50, 100, 100, 500, 500], "y": [0, 0, 0, 100, 0, 300]})
    s = M.hourly_steps(gps)
    assert len(s) == 3                                        # 0->1h, 1h->2h, 5h->6h (gap 2h->5h skipped)
    assert s["sl"].tolist() == pytest.approx([100, 100, 300])
    assert s["turn"].iloc[1] == pytest.approx(np.pi / 2)      # east then north = left turn of 90 degrees
    assert np.isnan(s["turn"].iloc[0]) and np.isnan(s["turn"].iloc[2])


def test_grid_features_classes_distances_and_crossing():
    g = _grid()
    x0 = np.array([14_000.0, 5_000.0]); y0 = np.array([10_000.0, 10_000.0])
    x1 = np.array([16_000.0, 5_200.0]); y1 = np.array([10_000.0, 10_000.0])
    f, valid = g.features(x0, y0, x1, y1, np.array([2000.0, 200.0]), np.zeros(2))
    i = M.FEATURES.index
    assert valid.all()
    assert f[0, i("developed")] == 1 and f[1, :len(M.MODEL_CLASSES)].sum() == 0   # upland forest = reference
    assert f[0, i("crosses_busy_road")] == 1 and f[1, i("crosses_busy_road")] == 0
    _, valid_out = g.features(x0, y0, np.array([-50.0, 1e9]), y1, np.ones(2), np.zeros(2))
    assert not valid_out.any()


def test_clogit_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    n, k, p = 4000, 11, 3
    X = rng.normal(size=(n, k, p))
    beta = np.array([1.0, -0.5, 0.0])
    u = X @ beta
    prob = np.exp(u - u.max(axis=1, keepdims=True)); prob /= prob.sum(axis=1, keepdims=True)
    choice = (prob.cumsum(axis=1) > rng.random((n, 1))).argmax(axis=1)
    X[np.arange(n), [0] * n], X[np.arange(n), choice] = X[np.arange(n), choice], X[np.arange(n), [0] * n].copy()
    b, se = M.fit_clogit(X, np.ones((n, k), bool))
    assert b == pytest.approx(beta, abs=0.1) and (se > 0).all() and (se < 0.1).all()


def test_simulated_bears_prefer_forest_when_told_to():
    g = _grid()
    pools = {k: np.c_[np.full(50, 300.0), np.random.default_rng(k).uniform(-np.pi, np.pi, 50)] for k in range(8)}
    beta = np.zeros(len(M.FEATURES)); beta[M.FEATURES.index("developed")] = -3.0
    scaler = M.Scaler(np.zeros(3), np.ones(3))
    rng = np.random.default_rng(1)
    start_x, start_y = np.full(200, 10_000.0), np.full(200, 10_000.0)   # on the forest/developed border
    t0 = pd.Timestamp("2012-05-01 12:00", tz="UTC")
    xs, _ = M.simulate(start_x, start_y, t0, 48, beta, scaler, pools, g, rng)
    xn, _ = M.simulate(start_x, start_y, t0, 48, np.zeros_like(beta), scaler, pools, g, rng)
    assert (xs[:, -1] < 10_000).mean() > 0.8                 # most end on the forest side
    assert 0.3 < (xn[:, -1] < 10_000).mean() < 0.7           # the null walker doesn't care


def test_track_metrics_counts_crossings_and_cover():
    g = _grid()
    xs = np.tile(np.linspace(14_000, 16_000, 49), (2, 1)); ys = np.full_like(xs, 10_000.0)
    m = M.track_metrics(xs, ys, g)
    assert m["busy_crossings_per_day"] == pytest.approx(24 / 48 * 1, rel=0.6)
    assert m["share_developed"] == 1.0 and m["share_natural_cover"] == 0.0