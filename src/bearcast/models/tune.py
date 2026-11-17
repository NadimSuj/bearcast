"""Small tuning round for the full LightGBM model, on the two validation folds.

Tries a handful of settings (tree size, minimum leaf size, learning rate, feature sampling,
Tweedie vs Poisson loss) and picks a winner by a rule fixed in advance: the lowest average
Poisson deviance over the two validation years. Capture numbers are reported alongside.

The winner and its average number of boosting rounds are saved for the final test run.

Outputs:
  data/models/best_params_h3r7.json
  data/processed/tune_h3r7_report.json

Usage:
    python -m bearcast.models.tune
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from bearcast.models.diagnose import within_group_capture
from bearcast.models.evaluate import capture_by_month, poisson_deviance
from bearcast.models.train_gbm import FOLDS, MODELS, PARAMS, PROCESSED, Data, fit_fold, period_slices

log = logging.getLogger(__name__)

CONFIGS: dict[str, dict] = {
    "base": {},
    "leaves31": {"num_leaves": 31},
    "leaves127": {"num_leaves": 127},
    "minleaf1000": {"min_data_in_leaf": 1000},
    "minleaf100": {"min_data_in_leaf": 100},
    "slow_lr": {"learning_rate": 0.02},
    "ff0.5": {"feature_fraction": 0.5},
    "tweedie": {"objective": "tweedie", "tweedie_variance_power": 1.2},
}


def pick_best(results: pd.DataFrame) -> str:
    """The pre-registered rule: lowest mean Poisson deviance across folds."""
    return results.groupby("config")["poisson_dev"].mean().idxmin()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    p.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)
    tag = f"h3r{args.res}"

    data = Data(args.dir, tag)
    meta = data.meta
    months = meta["month"].to_numpy()
    y = meta["n_conflict"].to_numpy(dtype="float32")
    X, names = data.matrix("full")

    rows = []
    for cfg in args.configs:
        params = {**PARAMS, **CONFIGS[cfg], "metric": "poisson"}   # same early-stopping yardstick for all
        for fold_name, fold in FOLDS.items():
            t = time.time()
            booster, pred = fit_fold(X, y, months, names, fold, params)
            _, ev = period_slices(months, fold)
            df = meta.iloc[ev][["month", "n_conflict", "conf_sum12", "conf_hist_rate"]].assign(pred=pred)
            rows.append({
                "config": cfg, "fold": fold_name, "rounds": booster.best_iteration,
                "poisson_dev": poisson_deviance(df["n_conflict"].to_numpy(), pred),
                "pred/actual": float(pred.sum() / df["n_conflict"].sum()),
                "capture@1%": capture_by_month(df, "pred", 0.01).mean(),
                "capture@5%": capture_by_month(df, "pred", 0.05).mean(),
                "cold@10%": within_group_capture(df, "pred", 0.10, df["conf_sum12"].eq(0)).mean(),
                "never@10%": within_group_capture(df, "pred", 0.10, df["conf_hist_rate"].eq(0)).mean(),
            })
            log.info("%-12s %s: %4d rounds, dev %.5f, %.0fs", cfg, fold_name, booster.best_iteration,
                     rows[-1]["poisson_dev"], time.time() - t)
            del booster

    res = pd.DataFrame(rows)
    summary = res.drop(columns="fold").groupby("config").mean().sort_values("poisson_dev")
    best = pick_best(res)
    best_params = {**PARAMS, **CONFIGS[best], "metric": "poisson"}
    rounds = int(np.ceil(res.loc[res["config"] == best, "rounds"].mean()))
    shown = summary.round(4).assign(poisson_dev=summary["poisson_dev"].round(5))
    log.info("Mean over the two validation years (sorted by Poisson deviance):\n%s", shown.to_string())
    log.info("Winner: %s (%d rounds on average)", best, rounds)

    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / f"best_params_{tag}.json").write_text(json.dumps(
        {"config": best, "params": best_params, "num_boost_round": rounds}, indent=2))
    (args.dir / f"tune_{tag}_report.json").write_text(json.dumps(
        {"winner": best, "per_fold": res.round(5).to_dict(orient="records"),
         "summary": summary.round(5).to_dict(orient="index")}, indent=2))
    log.info("Wrote %s", MODELS / f"best_params_{tag}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())