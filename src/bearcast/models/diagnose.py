"""Diagnostics on saved validation predictions (no retraining).

1. Paired bootstrap over months: is the full model's edge over the history-only model real,
   or within month-to-month noise? Resamples the validation months with replacement and reports
   the 95% range of (full - history) capture.
2. Ranking within subgroups: rank cells only against others in the same group, then ask what share
   of that group's conflicts land in its top k%.
     cold  = no conflict in the previous 12 months
     never = no conflict at all since the panel began (long-run rate is 0)
   This is the "early warning for new places" question, where landscape features should matter.

Reads data/processed/preds_h3r7_<fold>.parquet written by train_gbm.
Writes data/processed/gbm_h3r7_diagnostics.json

Usage:
    python -m bearcast.models.diagnose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from bearcast.models.evaluate import capture_by_month

log = logging.getLogger(__name__)
PROCESSED = Path("data/processed")
SCORES = ["base_longrun", "base_last12", "gbm_history", "gbm_full"]


def within_group_capture(df: pd.DataFrame, score: str, k: float, mask: pd.Series) -> pd.Series:
    """Rank only the rows in `mask` (per month) and capture that group's conflicts in its top k%."""
    return capture_by_month(df.loc[mask], score, k)


def paired_bootstrap(a: pd.Series, b: pd.Series, n: int = 5000, seed: int = 0) -> dict:
    """Mean of (b - a) over months, with a 95% interval from resampling months."""
    d = (b - a).dropna().to_numpy()
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, len(d), size=(n, len(d)))].mean(axis=1)
    return {"mean_diff": float(d.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "share_months_b_better": float((d > 0).mean()), "months": int(len(d))}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)
    tag = f"h3r{args.res}"

    preds = pd.concat([pd.read_parquet(f).assign(fold=f.stem.split("_")[-1])
                       for f in sorted(args.dir.glob(f"preds_{tag}_val*.parquet"))], ignore_index=True)
    scores = [s for s in SCORES if s in preds]
    report: dict = {"bootstrap_full_minus_history": {}, "within_group": {}}

    # 1. Is full better than history? Months from both folds pooled (24 months).
    if {"gbm_history", "gbm_full"} <= set(preds):
        for k in (0.01, 0.05):
            h = capture_by_month(preds, "gbm_history", k)
            f = capture_by_month(preds, "gbm_full", k)
            report["bootstrap_full_minus_history"][f"capture@{k:.0%}"] = paired_bootstrap(h, f)
        cold = preds["conf_sum12"].eq(0)
        for k in (0.05, 0.10):
            h = within_group_capture(preds, "gbm_history", k, cold)
            f = within_group_capture(preds, "gbm_full", k, cold)
            report["bootstrap_full_minus_history"][f"within_cold@{k:.0%}"] = paired_bootstrap(h, f)
        log.info("Full minus history (pooled months, 95%% bootstrap range):\n%s", "\n".join(
            f"  {m:<16} {r['mean_diff']:+.3f}  [{r['ci95'][0]:+.3f}, {r['ci95'][1]:+.3f}]  "
            f"full better in {r['share_months_b_better']:.0%} of {r['months']} months"
            for m, r in report["bootstrap_full_minus_history"].items()))

    # 2. Ranking within groups, per fold.
    groups = {"cold": preds["conf_sum12"].eq(0), "never": preds["base_longrun"].eq(0)}
    for fold, g in preds.groupby("fold"):
        rows = []
        for gname, mask in groups.items():
            m = mask.loc[g.index]
            n_conf = int(g.loc[m, "n_conflict"].sum())
            n_cells_month = int(m.sum() / g["month"].nunique())
            for s in scores:
                r = {"group": gname, "score": s, "conflicts": n_conf, "cells_per_month": n_cells_month}
                for k in (0.05, 0.10):
                    r[f"within@{k:.0%}"] = within_group_capture(g, s, k, m).mean()
                rows.append(r)
        tbl = pd.DataFrame(rows).set_index(["group", "score"])
        report["within_group"][fold] = tbl.round(4).reset_index().to_dict(orient="records")
        log.info("[%s] ranking within groups (random ranking would give 0.05 and 0.10):\n%s",
                 fold, tbl.round(3).to_string())

    path = args.dir / f"gbm_{tag}_diagnostics.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())