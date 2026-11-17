"""First model: gradient-boosted trees predicting next month's conflict calls per hexagon.

LightGBM with a Poisson objective: it predicts an expected count (a rate) for every
cell-month, which suits rare counts that are mostly zero.

Two validation folds, each trained only on months before its validation year:
  val2023: train 2016-2022, validate on 2023 (a high year)
  val2024: train 2016-2023, validate on 2024 (a low year)
The test period (2025 onward) is never loaded here; it stays untouched for the final check.
Early stopping watches the validation year, so validation scores are slightly optimistic.
That's the price of tuning, and why the test period is held back.

Each fold trains two models so we can see what the landscape adds:
  history: the 22 history/calendar/location features
  full:    history + landscape (housing, conservation, roads) + land cover
and compares them with two simple baselines: the long-run average and the last 12 months.

Outputs:
  data/models/gbm_h3r7_<fold>_<set>.txt         LightGBM model files
  data/processed/preds_h3r7_<fold>.parquet      validation predictions + baselines (for notebooks)
  data/processed/gbm_h3r7_report.json           metrics, rounds, top features

Usage:
    python -m bearcast.models.train_gbm
    python -m bearcast.models.train_gbm --folds val2024 --sets full    # quicker
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
import pyarrow.parquet as pq

from bearcast.features.history import FEATURE_COLS as HISTORY_COLS
from bearcast.models.evaluate import summarize

log = logging.getLogger(__name__)

PROCESSED = Path("data/processed")
MODELS = Path("data/models")
FOLDS = {
    "val2023": ("2022-12-01", "2023-01-01", "2023-12-01"),   # train_end, eval_start, eval_end
    "val2024": ("2023-12-01", "2024-01-01", "2024-12-01"),
}
LAST_LOADED_MONTH = "2024-12-01"     # never read test months
PARAMS = {
    "objective": "poisson", "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 300,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
    "max_bin": 255, "metric": "poisson", "verbose": -1, "seed": 42,
}
BASELINES = {"base_longrun": "conf_hist_rate", "base_last12": "conf_sum12"}


class Data:
    """Model inputs held as compact arrays, rows sorted by month.

    meta:   cell, month, n_conflict and the two baseline columns (small DataFrame)
    hist:   (rows x 22) float32 history features
    static: (cells x 31) float32 landscape + land-cover features, one row per cell
    codes:  row -> index into static
    """

    def __init__(self, d: Path, tag: str, last_month: str | None = LAST_LOADED_MONTH):
        # last_month=None loads every month, including the test period (final test only).
        t = pq.read_table(d / f"features_{tag}_month.parquet",
                          columns=["cell", "month", "n_conflict", *HISTORY_COLS],
                          filters=[("month", "<=", pd.Timestamp(last_month))] if last_month else None)
        month = t.column("month").to_numpy()
        order = np.argsort(month, kind="stable")
        self.hist = np.empty((t.num_rows, len(HISTORY_COLS)), dtype=np.float32)
        for j, c in enumerate(HISTORY_COLS):                 # one column at a time keeps peak memory low
            self.hist[:, j] = t.column(c).to_numpy()[order]
        cell = t.column("cell").to_pandas()
        if not isinstance(cell.dtype, pd.CategoricalDtype):
            cell = cell.astype("category")
        cats = cell.cat.categories.astype(str)
        self.codes = cell.cat.codes.to_numpy()[order]
        self.meta = pd.DataFrame({
            "cell": pd.Categorical.from_codes(self.codes, categories=cats),
            "month": month[order],
            "n_conflict": t.column("n_conflict").to_numpy()[order].astype(np.float32),
            "conf_hist_rate": self.hist[:, HISTORY_COLS.index("conf_hist_rate")],
            "conf_sum12": self.hist[:, HISTORY_COLS.index("conf_sum12")],
        })
        del t, month, order, cell

        static = pd.read_parquet(d / f"landscape_{tag}.parquet").merge(
            pd.read_parquet(d / f"landcover_{tag}.parquet"), on="cell", how="outer").set_index("cell")
        self.static_cols = list(static.columns)
        self.static = static.reindex(cats).to_numpy(dtype=np.float32)
        n_missing = int(np.isnan(self.static[np.unique(self.codes)]).all(axis=1).sum())
        if n_missing:
            log.warning("%d cells have no landscape row (their landscape features will be NaN)", n_missing)
        self.hist_cols = list(HISTORY_COLS)

    def matrix(self, set_name: str) -> tuple[np.ndarray, list[str]]:
        if set_name == "history":
            return self.hist, self.hist_cols
        nh, ns = len(self.hist_cols), len(self.static_cols)
        X = np.empty((len(self.codes), nh + ns), dtype=np.float32)
        X[:, :nh] = self.hist
        step = 1_000_000
        for i in range(0, len(self.codes), step):              # fill the static part in chunks
            X[i:i + step, nh:] = self.static[self.codes[i:i + step]]
        return X, self.hist_cols + self.static_cols


def period_slices(months: np.ndarray, fold: tuple[str, str, str]) -> tuple[slice, slice]:
    """Rows are sorted by month, so the training and evaluation periods are contiguous slices."""
    m = np.asarray(months, dtype="datetime64[ns]")
    train_end, eval_start, eval_end = (np.datetime64(pd.Timestamp(x), "ns") for x in fold)
    a = np.searchsorted(m, train_end, side="right")
    b0, b1 = np.searchsorted(m, eval_start, side="left"), np.searchsorted(m, eval_end, side="right")
    return slice(0, a), slice(b0, b1)


def fit_fold(X: np.ndarray, y: np.ndarray, months: np.ndarray, feature_names: list[str],
             fold: tuple[str, str, str], params: dict = PARAMS, max_rounds: int = 3000,
             patience: int = 100) -> tuple[lgb.Booster, np.ndarray]:
    """Train on months up to the fold's train end, early-stop on its evaluation year.

    X, y, months must be sorted by month; slicing then gives views, not copies, which keeps memory low.
    """
    tr, ev = period_slices(months, fold)
    dtrain = lgb.Dataset(X[tr], y[tr], feature_name=feature_names, free_raw_data=True)
    dvalid = lgb.Dataset(X[ev], y[ev], reference=dtrain)
    booster = lgb.train(params, dtrain, num_boost_round=max_rounds, valid_sets=[dvalid], valid_names=["eval"],
                        callbacks=[lgb.early_stopping(patience, verbose=False), lgb.log_evaluation(0)])
    pred = booster.predict(X[ev], num_iteration=booster.best_iteration)
    return booster, pred


def top_features(booster: lgb.Booster, n: int = 15) -> dict[str, float]:
    gain = pd.Series(booster.feature_importance("gain"), index=booster.feature_name())
    return (gain / gain.sum()).sort_values(ascending=False).head(n).round(4).to_dict()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    p.add_argument("--folds", nargs="+", default=list(FOLDS), choices=list(FOLDS))
    p.add_argument("--sets", nargs="+", default=["history", "full"], choices=["history", "full"])
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)

    tag = f"h3r{args.res}"
    data = Data(args.dir, tag)
    table = data.meta
    log.info("Loaded %d rows x %d features (%d history, %d landscape/land cover), %s to %s",
             len(table), len(data.hist_cols) + len(data.static_cols), len(data.hist_cols), len(data.static_cols),
             pd.Timestamp(table["month"].min()).date(), pd.Timestamp(table["month"].max()).date())
    MODELS.mkdir(parents=True, exist_ok=True)

    report: dict = {"params": PARAMS, "folds": {}}
    months = table["month"].to_numpy()
    y = table["n_conflict"].to_numpy(dtype="float32")
    outs: dict[str, pd.DataFrame] = {}
    fold_reps: dict[str, dict] = {}
    for fold_name in args.folds:
        _, ev = period_slices(months, FOLDS[fold_name])
        out = table.iloc[ev][["cell", "month", "n_conflict", "conf_sum12"]].copy()
        for name, col in BASELINES.items():
            out[name] = table[col].to_numpy()[ev]
        outs[fold_name] = out
        fold_reps[fold_name] = {"rounds": {}, "top_features": {}}

    for set_name in args.sets:
        X, names = data.matrix(set_name)                        # one feature matrix per set, shared by folds
        for fold_name in args.folds:
            t = time.time()
            booster, pred = fit_fold(X, y, months, names, FOLDS[fold_name])
            outs[fold_name][f"gbm_{set_name}"] = pred
            booster.save_model(str(MODELS / f"gbm_{tag}_{fold_name}_{set_name}.txt"), num_iteration=booster.best_iteration)
            fold_reps[fold_name]["rounds"][set_name] = booster.best_iteration
            fold_reps[fold_name]["top_features"][set_name] = top_features(booster)
            log.info("[%s] %s model: %d rounds in %.0fs", fold_name, set_name, booster.best_iteration, time.time() - t)
            del booster
        del X

    model_scores = [f"gbm_{s}" for s in args.sets]
    for fold_name in args.folds:
        out, fold_rep = outs[fold_name], fold_reps[fold_name]
        metrics = summarize(out, [*BASELINES, *model_scores], rate_scores=["base_longrun", *model_scores])
        fold_rep["metrics"] = metrics.round(4).to_dict(orient="index")
        fold_rep["eval_conflicts"] = int(out["n_conflict"].sum())
        fold_rep["eval_cold_conflicts"] = int(out.loc[out["conf_sum12"].eq(0), "n_conflict"].sum())
        report["folds"][fold_name] = fold_rep
        out.to_parquet(args.dir / f"preds_{tag}_{fold_name}.parquet", index=False)
        log.info("[%s] %d conflicts (%d in cold cells)\n%s", fold_name, fold_rep["eval_conflicts"],
                 fold_rep["eval_cold_conflicts"], metrics.round(3).to_string())
        if "full" in args.sets:
            log.info("[%s] top features (full):\n%s", fold_name,
                     "\n".join(f"  {k:<24} {v:.3f}" for k, v in fold_rep["top_features"]["full"].items()))

    path = args.dir / f"gbm_{tag}_report.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())