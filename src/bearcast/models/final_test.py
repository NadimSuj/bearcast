"""One-time final test: train on 2016-2024, predict every month of 2025-01 .. latest.

Everything here was fixed before the test period was ever looked at:
  model        the tuning winner (data/models/best_params_h3r7.json), fixed number of rounds
  features     full set (history + landscape + land cover); history-only trained alongside for comparison
  calibration  predictions x 1 / (winner's mean pred/actual on the two validation years)
  forecasting  each test month uses only data from before that month (one month ahead)
  metrics      capture@1/5%, cold capture, ranking within cold / never-seen cells, deviance, pred/actual,
               per year, plus 95% month-bootstrap ranges for the key comparisons

It refuses to run twice (the report file acts as a lock) unless --force is given, so the test score
can't be quietly re-rolled after looking at it.

Outputs:
  data/models/gbm_h3r7_final_full.txt           the model the app will use
  data/processed/preds_h3r7_test.parquet        test-period predictions + baselines
  data/processed/final_test_h3r7_report.json

Usage:
    python -m bearcast.models.final_test
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from bearcast.models.diagnose import paired_bootstrap, within_group_capture
from bearcast.models.evaluate import capture_by_month, summarize
from bearcast.models.train_gbm import BASELINES, MODELS, PROCESSED, Data, period_slices, top_features

log = logging.getLogger(__name__)
TRAIN_END = "2024-12-01"
TEST_START = "2025-01-01"


def calibration_factor(tune_report: dict, config: str) -> float:
    """1 / mean pred/actual of the chosen config over the validation folds (never uses test data)."""
    ratios = [r["pred/actual"] for r in tune_report["per_fold"] if r["config"] == config]
    if not ratios:
        raise ValueError(f"config {config!r} not found in the tuning report")
    return 1.0 / float(np.mean(ratios))


def within_table(df: pd.DataFrame, scores: list[str]) -> pd.DataFrame:
    groups = {"cold": df["conf_sum12"].eq(0), "never": df["base_longrun"].eq(0)}
    rows = []
    for g, mask in groups.items():
        for s in scores:
            rows.append({"group": g, "score": s, "conflicts": int(df.loc[mask, "n_conflict"].sum()),
                         **{f"within@{k:.0%}": within_group_capture(df, s, k, mask).mean() for k in (0.05, 0.10)}})
    return pd.DataFrame(rows).set_index(["group", "score"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    p.add_argument("--force", action="store_true", help="run again even though a final report exists")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)
    tag = f"h3r{args.res}"

    report_path = args.dir / f"final_test_{tag}_report.json"
    if report_path.exists() and not args.force:
        log.error("%s already exists: the final test has been run. Use --force only if something was broken.", report_path)
        return 1

    best = json.loads((MODELS / f"best_params_{tag}.json").read_text())
    calib = calibration_factor(json.loads((args.dir / f"tune_{tag}_report.json").read_text()), best["config"])
    log.info("Model: %s, %d rounds, calibration x%.4f", best["config"], best["num_boost_round"], calib)

    data = Data(args.dir, tag, last_month=None)
    meta = data.meta
    months = meta["month"].to_numpy()
    y = meta["n_conflict"].to_numpy(dtype="float32")
    last = pd.Timestamp(months.max())
    tr, te = period_slices(months, (TRAIN_END, TEST_START, str(last.date())))
    log.info("Train %d rows (to %s) | test %d rows (%s to %s)", tr.stop, TRAIN_END, te.stop - te.start,
             TEST_START, last.date())

    out = meta.iloc[te][["cell", "month", "n_conflict", "conf_sum12"]].copy()
    for name, col in BASELINES.items():
        out[name] = meta[col].to_numpy()[te]

    report: dict = {"model": best, "calibration": calib, "test_months": [TEST_START, str(last.date())]}
    for set_name in ("history", "full"):
        t = time.time()
        X, names = data.matrix(set_name)
        booster = lgb.train(best["params"], lgb.Dataset(X[tr], y[tr], feature_name=names),
                            num_boost_round=best["num_boost_round"])
        raw = booster.predict(X[te])
        out[f"gbm_{set_name}"] = raw * calib
        if set_name == "full":
            out["gbm_full_raw"] = raw
            booster.save_model(str(MODELS / f"gbm_{tag}_final_full.txt"))
            report["top_features"] = top_features(booster)
        log.info("%s model trained and scored in %.0fs", set_name, time.time() - t)
        del booster, X

    scores = [*BASELINES, "gbm_history", "gbm_full"]
    rate = ["base_longrun", "gbm_history", "gbm_full", "gbm_full_raw"]
    overall = summarize(out, [*scores, "gbm_full_raw"], rate_scores=rate)
    years = {str(yr): summarize(g, [*scores, "gbm_full_raw"], rate_scores=rate)
             for yr, g in out.groupby(out["month"].dt.year)}
    within = within_table(out, scores)
    never = out["base_longrun"].eq(0)
    boot = {
        "full_minus_longrun_capture@1%": paired_bootstrap(capture_by_month(out, "base_longrun", 0.01),
                                                          capture_by_month(out, "gbm_full", 0.01)),
        "full_minus_longrun_capture@5%": paired_bootstrap(capture_by_month(out, "base_longrun", 0.05),
                                                          capture_by_month(out, "gbm_full", 0.05)),
        "full_minus_history_never@10%": paired_bootstrap(within_group_capture(out, "gbm_history", 0.10, never),
                                                         within_group_capture(out, "gbm_full", 0.10, never)),
    }
    report.update({
        "conflicts": int(out["n_conflict"].sum()),
        "overall": overall.round(4).to_dict(orient="index"),
        "by_year": {k: v.round(4).to_dict(orient="index") for k, v in years.items()},
        "within_group": within.round(4).reset_index().to_dict(orient="records"),
        "bootstrap": boot,
    })
    out.to_parquet(args.dir / f"preds_{tag}_test.parquet", index=False)
    report_path.write_text(json.dumps(report, indent=2, default=str))

    log.info("TEST %s to %s: %d conflicts\n%s", TEST_START, last.date(), report["conflicts"], overall.round(3).to_string())
    for yr, tbl in years.items():
        log.info("Year %s:\n%s", yr, tbl.round(3).to_string())
    log.info("Ranking within groups (random = 0.05 / 0.10):\n%s", within.round(3).to_string())
    log.info("95%% month-bootstrap ranges:\n%s", "\n".join(
        f"  {k:<32} {v['mean_diff']:+.3f}  [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]  better in "
        f"{v['share_months_b_better']:.0%} of {v['months']} months" for k, v in boot.items()))
    log.info("Top features:\n%s", "\n".join(f"  {k:<24} {v:.3f}" for k, v in report["top_features"].items()))
    log.info("Wrote %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())