"""Monthly-job tests: forecast scoring in SQL, the out-of-sample file, and the skip-if-nothing-new rule."""

import os

import h3
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

import bearcast.db  # noqa: F401
from bearcast.db.load import copy_rows, hex_ewkt, point
from bearcast.db.migrate import migrate
from bearcast.jobs import monthly as J
from bearcast.models.predict import rolling_calibration

CENTER = h3.latlng_to_cell(29.2, -81.7, 7)
CELLS = sorted(h3.grid_disk(CENTER, 2))                       # 19 hexagons


@pytest.fixture(scope="module")
def eng():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    e = create_engine(url)
    try:
        with e.begin() as c:
            c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable: {exc}")
    migrate(e)
    with e.begin() as c:
        copy_rows(c, "hexagons", ["cell", "geom", "centroid"],
                  ((x, hex_ewkt(x), point(*h3.cell_to_latlng(x)[::-1])) for x in CELLS))
    yield e
    e.dispose()


def _add_forecast(c, month: str, factor: float) -> int:
    run = c.execute(text("INSERT INTO model_runs (kind, data_through, params) VALUES ('forecast', :d, "
                         "CAST(:p AS jsonb)) RETURNING run_id"),
                    {"d": (pd.Timestamp(month) - pd.offsets.MonthBegin(1)).date(),
                     "p": '{"calibration": {"factor": %s}}' % factor}).scalar()
    n = len(CELLS)
    rows = []
    for i, cell in enumerate(CELLS):                          # rank 1 = CELLS[0]; odd ranks are "quiet" hexagons
        rank = i + 1
        cold = rank % 2 == 1 and rank > 1
        rows.append((run, month, cell, float(n - i) / 10, rank, rank / n, "very high" if rank == 1 else "low",
                     cold, False, None, None))
    copy_rows(c, "forecasts", ["run_id", "month", "cell", "expected_calls", "risk_rank", "risk_pct", "tier",
                               "no_recent_conflict", "never_conflict", "emerging_rank", "emerging_pct"], rows)
    # emerging ranks among the quiet hexagons, in risk order
    c.execute(text("""UPDATE forecasts f SET emerging_rank = s.r FROM (
                        SELECT cell, row_number() OVER (ORDER BY risk_rank) AS r FROM forecasts
                        WHERE run_id = :run AND no_recent_conflict) s
                      WHERE f.run_id = :run AND f.cell = s.cell"""), {"run": run})
    return run


def test_scoring_matches_hand_calculation_and_only_scores_complete_months(eng):
    with eng.begin() as c:
        nov = _add_forecast(c, "2026-11-01", 1.25)
        dec = _add_forecast(c, "2026-12-01", 1.25)
        # actual November conflicts: 3 in the top hexagon, 1 in the 3rd (quiet, emerging #1), 1 in the last
        copy_rows(c, "monthly_counts", ["cell", "month", "n_conflict"],
                  [(CELLS[0], "2026-11-01", 3), (CELLS[2], "2026-11-01", 1), (CELLS[-1], "2026-11-01", 1)])
        todo = J.unscored_runs(c, pd.Timestamp("2026-11-01"))
        assert [r[0] for r in todo] == [nov]                       # December isn't complete yet
        s = J.score_run(c, nov, pd.Timestamp("2026-11-01"))
    n = len(CELLS)
    assert s["conflicts"] == 5
    assert s["capture_1pct"] == pytest.approx(3 / 5)               # top ceil(0.19) = 1 hexagon
    assert s["capture_5pct"] == pytest.approx(3 / 5)               # top ceil(0.95) = 1 hexagon
    quiet = [i + 1 for i in range(n) if (i + 1) % 2 == 1 and i > 0]  # ranks 3, 5, ..., 19 -> 9 quiet hexagons
    assert len(quiet) == 9
    # top ceil(0.9) = 1 quiet hexagon = rank 3, which had 1 of the 2 quiet-hexagon conflicts (rank 3 and rank 19)
    assert s["emerging_capture_10"] == pytest.approx(1 / 2)
    with eng.begin() as c:
        assert J.unscored_runs(c, pd.Timestamp("2026-11-01")) == []   # scored runs aren't scored twice


def test_append_oos_feeds_rolling_calibration(eng, tmp_path):
    seed = tmp_path / "preds_h3r7_test.parquet"
    months = pd.date_range("2025-12-01", "2026-10-01", freq="MS")   # 11 months, raw always 1.0, actual 1.0
    pd.DataFrame({"cell": np.repeat(CELLS[:2], len(months)), "month": np.tile(months, 2),
                  "n_conflict": 1.0, "gbm_full_raw": 1.0}).to_parquet(seed)
    oos = tmp_path / "oos_h3r7.parquet"
    with eng.begin() as c:
        nov = c.execute(text("SELECT run_id FROM forecast_scores ORDER BY run_id LIMIT 1")).scalar()
        assert J.append_oos(c, nov, 1.25, oos, seed) == len(CELLS)
    o = pd.read_parquet(oos)
    novrows = o[o["month"] == "2026-11-01"]
    assert len(novrows) == len(CELLS) and novrows["n_conflict"].sum() == 5
    top = novrows.set_index("cell").loc[CELLS[0], "gbm_full_raw"]
    assert top == pytest.approx((len(CELLS) / 10) / 1.25)          # raw = calibrated / factor
    factor, start, end = rolling_calibration(o)
    assert end == "2026-11-01" and start == "2025-12-01"
    expected = (22 + 5) / (22 + novrows["gbm_full_raw"].sum())
    assert factor == pytest.approx(expected)


def test_predict_is_skipped_when_no_new_month(eng, tmp_path, monkeypatch):
    pd.DataFrame({"cell": [CENTER], "month": [pd.Timestamp("2026-11-01")]}).to_parquet(tmp_path / "panel_h3r7_month.parquet")
    called = []
    monkeypatch.setattr("bearcast.models.predict.main", lambda argv=None: called.append(argv))
    out = J.run(["predict"], d=tmp_path, eng=eng)                  # latest forecast data_through = 2026-11 (Dec run)
    assert out["forecast"] == "skipped" and called == []