"""History features: what happened recently in each hexagon and around it.

Rule that every feature follows: the value for month t uses ONLY months before t.
At prediction time you know last month's calls, never this month's, so the model must
learn under the same conditions (otherwise it "cheats" and looks better than it is).

Features (per cell, per month t):
  conf_lag1/2/3        conflicts in this cell 1, 2, 3 months ago
  conf_lag12           conflicts in the same month last year
  conf_sum3/12         conflicts in the last 3 / 12 months
  conf_hist_rate       average conflicts per month over all earlier months in the window
  months_since_conf    months since this cell's last conflict (999 = none yet)
  enc_sum12            encounters (conflicts + sightings) in the last 12 months
  welfare_sum12        dead / injured bear calls in the last 12 months
  nbr1_conf_sum12      conflicts in the 6 touching cells, last 12 months
  nbr2_conf_sum12      conflicts in the next ring out (12 cells), last 12 months
  nbr1_conf_lag1       conflicts in the 6 touching cells last month
  state_conf_lag1      statewide conflicts last month
  state_conf_sum12     statewide conflicts in the last 12 months
  state_conf_yoy3      statewide last 3 months / same 3 months a year earlier (boom-year signal)
  month, month_sin, month_cos   calendar (season)
  range_rank, lat, lon          static cell facts

The first year of the panel (2015) is used only as history; feature rows start 2016-01.

Output: data/processed/features_h3r7_month.parquet (+ _report.json)

Usage:
    python -m bearcast.features.history
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from scipy import sparse

from bearcast.features.panel import label_split

log = logging.getLogger(__name__)

PROCESSED = Path("data/processed")
FEATURE_START = "2016-01"
NEVER = 999.0


def grid_matrices(panel: pd.DataFrame, cols: list[str]) -> tuple[list[str], pd.DatetimeIndex, dict[str, np.ndarray]]:
    """Sort the long panel ONCE and reshape each count column into a cells x months matrix."""
    cells = sorted(panel["cell"].unique())
    months = pd.DatetimeIndex(sorted(panel["month"].unique()))
    p = panel[["cell", "month", *cols]].sort_values(["cell", "month"])
    if len(p) != len(cells) * len(months):
        raise ValueError("panel is not a complete cell x month grid")
    if list(p["cell"].iloc[:: len(months)].astype(str)) != [str(c) for c in cells]:
        raise ValueError("panel cells are not in the expected order")
    shape = (len(cells), len(months))
    return [str(c) for c in cells], months, {c: p[c].to_numpy(dtype=np.float32).reshape(shape) for c in cols}


def lag(m: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(m, np.nan)
    out[:, k:] = m[:, :-k]
    return out


def past_sum(m: np.ndarray, w: int) -> np.ndarray:
    """Sum over months t-w .. t-1 (NaN until w months of history exist)."""
    c = np.concatenate([np.zeros((m.shape[0], 1), m.dtype), np.cumsum(m, axis=1)], axis=1)
    out = np.full_like(m, np.nan)
    out[:, w:] = c[:, w:-1] - c[:, : -w - 1]
    return out


def hist_rate(m: np.ndarray) -> np.ndarray:
    """Mean per month over all months before t."""
    c = np.cumsum(m, axis=1)
    out = np.full_like(m, np.nan)
    out[:, 1:] = c[:, :-1] / np.arange(1, m.shape[1], dtype=m.dtype)
    return out


def months_since(m: np.ndarray) -> np.ndarray:
    """Months since the last month with a positive count, looking only at months before t."""
    t_idx = np.arange(m.shape[1], dtype=np.float32)
    last = np.where(m > 0, t_idx, -1.0)
    last = np.maximum.accumulate(last, axis=1)
    out = np.full_like(m, NEVER)
    prev = last[:, :-1]
    out[:, 1:] = np.where(prev >= 0, t_idx[1:] - prev, NEVER)
    return out


def ring_adjacency(cells: list[str], k: int) -> sparse.csr_matrix:
    """Row i has a 1 for every universe cell exactly k steps from cell i."""
    index = {c: i for i, c in enumerate(cells)}
    rows, cols = [], []
    for i, c in enumerate(cells):
        for n in h3.grid_ring(c, k):
            j = index.get(n)
            if j is not None:
                rows.append(i)
                cols.append(j)
    data = np.ones(len(rows), dtype=np.float32)
    return sparse.csr_matrix((data, (rows, cols)), shape=(len(cells), len(cells)))


def build_features(panel: pd.DataFrame, cell_tbl: pd.DataFrame, feature_start: str = FEATURE_START) -> pd.DataFrame:
    cells, months, mats = grid_matrices(panel, ["n_conflict", "n_encounter", "n_bear_welfare"])
    t0 = int(months.searchsorted(pd.Timestamp(feature_start + "-01")))
    if t0 < 12:
        raise ValueError("need at least 12 months of history before feature_start")
    conf, enc, welf = mats["n_conflict"], mats["n_encounter"], mats["n_bear_welfare"]

    a1, a2 = ring_adjacency(cells, 1), ring_adjacency(cells, 2)
    conf_sum12 = past_sum(conf, 12)
    nbr1_month = a1 @ conf  # neighbours' counts in each month (not yet shifted)

    state = conf.sum(axis=0, keepdims=True)                 # 1 x months
    state_sum3 = past_sum(state, 3)
    state_prev3 = np.full_like(state, np.nan)
    state_prev3[:, 12:] = state_sum3[:, :-12]                # same 3 months, a year earlier
    with np.errstate(divide="ignore", invalid="ignore"):
        state_yoy3 = np.where(state_prev3 > 0, state_sum3 / state_prev3, np.nan)

    feats = {
        "conf_lag1": lag(conf, 1),
        "conf_lag2": lag(conf, 2),
        "conf_lag3": lag(conf, 3),
        "conf_lag12": lag(conf, 12),
        "conf_sum3": past_sum(conf, 3),
        "conf_sum12": conf_sum12,
        "conf_hist_rate": hist_rate(conf),
        "months_since_conf": months_since(conf),
        "enc_sum12": past_sum(enc, 12),
        "welfare_sum12": past_sum(welf, 12),
        "nbr1_conf_sum12": a1 @ np.nan_to_num(conf_sum12),
        "nbr2_conf_sum12": a2 @ np.nan_to_num(conf_sum12),
        "nbr1_conf_lag1": lag(np.asarray(nbr1_month, dtype=np.float32), 1),
    }
    for name, row in {"state_conf_lag1": lag(state, 1), "state_conf_sum12": past_sum(state, 12),
                      "state_conf_yoy3": state_yoy3}.items():
        feats[name] = np.repeat(row, len(cells), axis=0)
    # neighbour sums of a NaN-free copy: restore NaN where history is too short
    for name in ("nbr1_conf_sum12", "nbr2_conf_sum12"):
        feats[name] = np.asarray(feats[name], dtype=np.float32)
        feats[name][:, :12] = np.nan

    keep_months = months[t0:]
    n_c, n_m = len(cells), len(keep_months)
    codes = np.repeat(np.arange(n_c), n_m)  # cell index for every output row (cell-major order)
    out = pd.DataFrame({
        "cell": pd.Categorical.from_codes(codes, categories=cells),
        "month": np.tile(keep_months.values, n_c),
        "n_conflict": conf[:, t0:].ravel().astype(np.int32),
        "n_encounter": enc[:, t0:].ravel().astype(np.int32),
    })
    for name in list(feats):
        out[name] = np.asarray(feats.pop(name), dtype=np.float32)[:, t0:].ravel()  # free each matrix once copied

    out["month_num"] = out["month"].dt.month.astype(np.int8)
    angle = 2 * np.pi * (out["month_num"] - 1) / 12
    out["month_sin"] = np.sin(angle).astype(np.float32)
    out["month_cos"] = np.cos(angle).astype(np.float32)

    static = cell_tbl.set_index("cell").reindex(cells)
    out["range_rank"] = static["range_rank"].to_numpy(dtype=np.int8)[codes]
    out["lat"] = static["lat"].to_numpy(dtype=np.float32)[codes]
    out["lon"] = static["lon"].to_numpy(dtype=np.float32)[codes]
    out["split"] = pd.Categorical(label_split(out["month"]))
    return out


FEATURE_COLS = [
    "conf_lag1", "conf_lag2", "conf_lag3", "conf_lag12", "conf_sum3", "conf_sum12",
    "conf_hist_rate", "months_since_conf", "enc_sum12", "welfare_sum12",
    "nbr1_conf_sum12", "nbr2_conf_sum12", "nbr1_conf_lag1",
    "state_conf_lag1", "state_conf_sum12", "state_conf_yoy3",
    "month_num", "month_sin", "month_cos", "range_rank", "lat", "lon",
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--start", default=FEATURE_START, help="first feature month, YYYY-MM")
    p.add_argument("--dir", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tag = f"h3r{args.res}"
    panel = pd.read_parquet(args.dir / f"panel_{tag}_month.parquet",
                            columns=["cell", "month", "n_conflict", "n_encounter", "n_bear_welfare"])
    panel["cell"] = panel["cell"].astype("category")  # far less memory than millions of repeated strings
    cell_tbl = pd.read_parquet(args.dir / f"cells_{tag}.parquet", columns=["cell", "range_rank", "lat", "lon"])
    feats = build_features(panel, cell_tbl, args.start)

    out = args.dir / f"features_{tag}_month.parquet"
    feats.to_parquet(out, index=False)
    report = {
        "rows": int(len(feats)),
        "cells": int(feats["cell"].nunique()),
        "months": f"{feats['month'].min().date()} to {feats['month'].max().date()}",
        "rows_by_split": feats["split"].value_counts().to_dict(),
        "features": FEATURE_COLS,
        "nan_counts": {c: int(feats[c].isna().sum()) for c in FEATURE_COLS if feats[c].isna().any()},
    }
    out.with_name(f"features_{tag}_report.json").write_text(json.dumps(report, indent=2, default=str))
    for k, v in report.items():
        if k != "features":
            log.info("%-14s %s", k, v)
    log.info("Wrote %s (%d features)", out, len(FEATURE_COLS))
    return 0


if __name__ == "__main__":
    sys.exit(main())