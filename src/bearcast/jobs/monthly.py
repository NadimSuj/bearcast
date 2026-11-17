"""The monthly job: new FWC calls in, last month's forecast scored, next month's forecast out.

Steps (in order; --from and --only let you resume or run part of it):
  download        fetch the FWC layers that change: recent calls, the 2023-24 gap-fill layer, roadkill
  clean           bearcast.clean.calls        -> data/interim/calls_clean.parquet
  panel           bearcast.features.panel     -> hexagon x month counts up to the last complete month
  features        bearcast.features.history   -> history features
  load_reference  hexagons, calls, monthly_counts -> Postgres
  score           every stored forecast whose month now has complete data and no score yet:
                  capture of the top 1% / 5%, emerging capture (top 10% of quiet hexagons), totals.
                  Each scored month's predictions are appended to data/processed/oos_h3r7.parquet,
                  which the forecast uses for its rolling 12-month calibration.
  predict         bearcast.models.predict     -> next month's forecast (skipped if no new complete month)
  activity        bearcast.features.activity  -> recent activity
  load_outputs    forecast + activity -> Postgres (as new model_runs)

Usage:
    python -m bearcast.jobs.monthly                    # the whole thing
    python -m bearcast.jobs.monthly --skip-download    # use the raw files already on disk
    python -m bearcast.jobs.monthly --from score       # resume after a failure
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, text

from bearcast.db import engine
from bearcast.db.load import load_all

log = logging.getLogger(__name__)
PROCESSED = Path("data/processed")
STEPS = ["download", "clean", "panel", "features", "load_reference", "score", "predict", "activity", "load_outputs"]
DOWNLOAD_LAYERS = ["calls_recent", "calls_open", "roadkill"]

SCORE_SQL = """
WITH f AS (
    SELECT f.*, count(*) OVER () AS n_all,
           count(*) FILTER (WHERE no_recent_conflict) OVER () AS n_cold
    FROM forecasts f WHERE run_id = :run
), j AS (
    SELECT f.*, coalesce(m.n_conflict, 0) AS y
    FROM f LEFT JOIN monthly_counts m ON m.cell = f.cell AND m.month = f.month
)
SELECT sum(y)                                                                       AS conflicts,
       sum(expected_calls)                                                          AS expected_total,
       sum(y) FILTER (WHERE risk_rank <= ceil(0.01 * n_all))::real / nullif(sum(y), 0) AS capture_1pct,
       sum(y) FILTER (WHERE risk_rank <= ceil(0.05 * n_all))::real / nullif(sum(y), 0) AS capture_5pct,
       sum(y) FILTER (WHERE no_recent_conflict AND emerging_rank <= ceil(0.10 * n_cold))::real
           / nullif(sum(y) FILTER (WHERE no_recent_conflict), 0)                     AS emerging_capture_10
FROM j
"""


def unscored_runs(conn: Connection, through: pd.Timestamp) -> list[tuple[int, pd.Timestamp, float]]:
    """Forecast runs for months <= `through` (complete data) that have no score yet: (run_id, month, calibration)."""
    rows = conn.execute(text("""
        SELECT r.run_id, min(f.month) AS month, (r.params -> 'calibration' ->> 'factor')::float AS factor
        FROM model_runs r JOIN forecasts f USING (run_id)
        WHERE r.kind = 'forecast' AND r.run_id NOT IN (SELECT run_id FROM forecast_scores)
        GROUP BY r.run_id HAVING min(f.month) <= :through ORDER BY r.run_id"""), {"through": through.date()}).all()
    return [(r.run_id, pd.Timestamp(r.month), r.factor or 1.0) for r in rows]


def score_run(conn: Connection, run_id: int, month: pd.Timestamp) -> dict:
    s = dict(conn.execute(text(SCORE_SQL), {"run": run_id}).mappings().one())
    conn.execute(text("""INSERT INTO forecast_scores (run_id, month, conflicts, expected_total, capture_1pct,
                                                      capture_5pct, emerging_capture_10)
                         VALUES (:run, :month, :conflicts, :expected_total, :capture_1pct, :capture_5pct,
                                 :emerging_capture_10)"""),
                 {"run": run_id, "month": month.date(), **s})
    return s


def append_oos(conn: Connection, run_id: int, factor: float, oos: Path, seed: Path) -> int:
    """Add a scored month's (cell, month, actual, raw prediction) rows to the out-of-sample file."""
    df = pd.read_sql(text("""SELECT f.cell, f.month, coalesce(m.n_conflict, 0) AS n_conflict,
                                    f.expected_calls / :factor AS gbm_full_raw
                             FROM forecasts f LEFT JOIN monthly_counts m ON m.cell = f.cell AND m.month = f.month
                             WHERE f.run_id = :run"""), conn, params={"run": run_id, "factor": factor})
    df["month"] = pd.to_datetime(df["month"])
    base = pd.read_parquet(oos if oos.exists() else seed, columns=["cell", "month", "n_conflict", "gbm_full_raw"])
    base["cell"] = base["cell"].astype(str)
    out = pd.concat([base, df]).drop_duplicates(["cell", "month"], keep="last").sort_values(["month", "cell"])
    out.to_parquet(oos, index=False)
    return len(df)


def panel_end(d: Path, tag: str) -> pd.Timestamp:
    return pd.Timestamp(pd.read_parquet(d / f"panel_{tag}_month.parquet", columns=["month"])["month"].max())


def latest_forecast_data_through(conn: Connection) -> pd.Timestamp | None:
    v = conn.execute(text("SELECT max(data_through) FROM model_runs WHERE kind = 'forecast'")).scalar()
    return pd.Timestamp(v) if v else None


def run(steps: list[str], d: Path = PROCESSED, res: int = 7, eng=None) -> dict:
    tag = f"h3r{res}"
    eng = eng or engine()
    summary: dict = {"steps": steps}
    for step in steps:
        t = time.time()
        log.info("=== %s", step)
        if step == "download":
            from bearcast.ingest.fwc import download
            for name in DOWNLOAD_LAYERS:
                download(name)
        elif step == "clean":
            from bearcast.clean.calls import main as clean_main
            clean_main([])
        elif step == "panel":
            from bearcast.features.panel import main as panel_main
            panel_main(["--res", str(res)])
        elif step == "features":
            from bearcast.features.history import main as history_main
            history_main(["--res", str(res)])
        elif step == "load_reference":
            summary["load_reference"] = load_all(eng, ["hexagons", "calls", "monthly_counts"], processed=d, res=res)
        elif step == "score":
            through = panel_end(d, tag)
            summary["data_through"] = str(through.date())
            scores = []
            with eng.begin() as conn:
                for run_id, month, factor in unscored_runs(conn, through):
                    s = score_run(conn, run_id, month)
                    append_oos(conn, run_id, factor, d / f"oos_{tag}.parquet", d / f"preds_{tag}_test.parquet")
                    scores.append({"run_id": run_id, "month": str(month.date()), **s})
                    log.info("scored %s forecast (run %d): %d conflicts vs %.0f expected | top 1%% caught %s, "
                             "top 5%% %s, emerging top 10%% %s", month.strftime("%Y-%m"), run_id, s["conflicts"],
                             s["expected_total"], _pct(s["capture_1pct"]), _pct(s["capture_5pct"]),
                             _pct(s["emerging_capture_10"]))
            if not scores:
                log.info("no forecast is waiting for a score (data complete through %s)", through.strftime("%Y-%m"))
            summary["scores"] = scores
        elif step == "predict":
            through = panel_end(d, tag)
            with eng.connect() as conn:
                last = latest_forecast_data_through(conn)
            if last is not None and through <= last:
                log.info("no new complete month (data through %s, latest forecast already uses it): forecast skipped",
                         through.strftime("%Y-%m"))
                summary["forecast"] = "skipped"
                continue
            from bearcast.models.predict import main as predict_main
            predict_main(["--res", str(res)])
            nxt = (through + pd.offsets.MonthBegin(1)).strftime("%Y-%m")
            summary["forecast"] = json.loads((d / f"forecast_{tag}_{nxt}.json").read_text())
        elif step == "activity":
            from bearcast.features.activity import main as activity_main
            activity_main(["--res", str(res)])
        elif step == "load_outputs":
            summary["load_outputs"] = load_all(eng, ["forecast", "activity"], processed=d, res=res)
        log.info("    %s done in %.0fs", step, time.time() - t)
    return summary


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.0%}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="start", choices=STEPS, default=STEPS[0], help="first step to run")
    p.add_argument("--only", nargs="+", choices=STEPS, help="run just these steps (in pipeline order)")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--url-var", default="DATABASE_URL", help="environment variable holding the database URL")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    steps = [s for s in STEPS if s in args.only] if args.only else STEPS[STEPS.index(args.start):]
    if args.skip_download and "download" in steps:
        steps.remove("download")
    summary = run(steps, res=args.res, eng=engine(args.url_var))
    log.info("Summary:\n%s", json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
