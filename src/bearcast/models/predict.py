"""Forecast next month's conflict risk for every hexagon (what the app will show).

Steps:
  1. Next-month features: append an empty month after the panel's last month and rebuild the
     history features. Features for month t only use months before t, so the empty month's
     (unknown) counts never leak in.
  2. Score with the final model (data/models/gbm_h3r7_final_full.txt, trained 2016-2024).
  3. Rolling calibration: scale by actual / predicted over the last 12 months that the model
     forecast out-of-sample (from the final test predictions). This tracks the recent level of
     calls, which trees can't extrapolate. Rankings are unaffected.
  4. Ranks: overall risk rank among all hexagons, and emerging-risk rank among hexagons with no
     conflict in the previous 12 months.

Outputs:
  data/processed/forecast_h3r7_<YYYY-MM>.parquet
  data/processed/forecast_h3r7_<YYYY-MM>.json     (summary)

Usage:
    python -m bearcast.models.predict
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from bearcast.features.history import build_features

log = logging.getLogger(__name__)
PROCESSED = Path("data/processed")
MODELS = Path("data/models")
TIERS = [(0.01, "very high"), (0.05, "high"), (0.20, "elevated"), (1.00, "low")]


def next_month_features(panel: pd.DataFrame, cell_tbl: pd.DataFrame) -> pd.DataFrame:
    """History features for the month after the panel's last month (one row per cell)."""
    last = panel["month"].max()
    nxt = last + pd.offsets.MonthBegin(1)
    cells = panel["cell"].astype(str).unique()
    empty = pd.DataFrame({"cell": cells, "month": nxt, "n_conflict": 0, "n_encounter": 0, "n_bear_welfare": 0})
    full = pd.concat([panel.assign(cell=panel["cell"].astype(str)), empty], ignore_index=True)
    full["cell"] = full["cell"].astype("category")
    feats = build_features(full, cell_tbl, feature_start=nxt.strftime("%Y-%m"))
    return feats[feats["month"] == nxt].drop(columns=["n_conflict", "n_encounter", "split"]).reset_index(drop=True)


def rolling_calibration(test_preds: pd.DataFrame, months: int = 12) -> tuple[float, str, str]:
    """actual / raw predicted over the last `months` months of out-of-sample predictions."""
    recent = sorted(test_preds["month"].unique())[-months:]
    sel = test_preds[test_preds["month"].isin(recent)]
    factor = float(sel["n_conflict"].sum() / sel["gbm_full_raw"].sum())
    return factor, str(pd.Timestamp(recent[0]).date()), str(pd.Timestamp(recent[-1]).date())


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["risk_rank"] = df["expected_calls"].rank(ascending=False, method="first").astype(int)
    df["risk_pct"] = df["risk_rank"] / len(df)                           # 0.01 = top 1%
    df["tier"] = pd.cut(df["risk_pct"], [0] + [t for t, _ in TIERS], labels=[n for _, n in TIERS])
    df["no_recent_conflict"] = df["conf_sum12"].eq(0)
    df["never_conflict"] = df["conf_hist_rate"].eq(0)
    cold = df["no_recent_conflict"]
    df["emerging_rank"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df.loc[cold, "emerging_rank"] = df.loc[cold, "expected_calls"].rank(ascending=False, method="first").astype(int)
    df["emerging_pct"] = (df["emerging_rank"] / cold.sum()).astype("Float64")
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tag = f"h3r{args.res}"

    panel = pd.read_parquet(args.dir / f"panel_{tag}_month.parquet",
                            columns=["cell", "month", "n_conflict", "n_encounter", "n_bear_welfare"])
    cell_tbl = pd.read_parquet(args.dir / f"cells_{tag}.parquet", columns=["cell", "range_rank", "lat", "lon"])
    feats = next_month_features(panel, cell_tbl)
    month = pd.Timestamp(feats["month"].iloc[0])
    log.info("Forecasting %s for %d hexagons (data through %s)", month.strftime("%Y-%m"), len(feats),
             panel["month"].max().strftime("%Y-%m"))

    static = pd.read_parquet(args.dir / f"landscape_{tag}.parquet").merge(
        pd.read_parquet(args.dir / f"landcover_{tag}.parquet"), on="cell", how="outer")
    df = feats.assign(cell=feats["cell"].astype(str)).merge(static, on="cell", how="left")

    booster = lgb.Booster(model_file=str(MODELS / f"gbm_{tag}_final_full.txt"))
    raw = booster.predict(df[booster.feature_name()].to_numpy(dtype=np.float32))
    # out-of-sample predictions: the final-test file, extended each month by the monthly job (oos_*.parquet)
    oos = args.dir / f"oos_{tag}.parquet"
    factor, c_from, c_to = rolling_calibration(pd.read_parquet(oos if oos.exists() else args.dir / f"preds_{tag}_test.parquet"))
    df["expected_calls"] = (raw * factor).astype("float32")
    out = add_ranks(df)[["cell", "month", "expected_calls", "risk_rank", "risk_pct", "tier",
                         "no_recent_conflict", "never_conflict", "emerging_rank", "emerging_pct",
                         "conf_sum12", "conf_hist_rate"]]

    stem = f"forecast_{tag}_{month.strftime('%Y-%m')}"
    out.to_parquet(args.dir / f"{stem}.parquet", index=False)
    summary = {
        "month": month.strftime("%Y-%m"), "hexagons": int(len(out)),
        "expected_calls_statewide": round(float(out["expected_calls"].sum()), 1),
        "calibration": {"factor": round(factor, 4), "from": c_from, "to": c_to},
        "tiers": out["tier"].value_counts().reindex([n for _, n in TIERS]).to_dict(),
        "share_of_expected_calls_in_top_5pct": round(float(out.loc[out["risk_pct"] <= 0.05, "expected_calls"].sum()
                                                             / out["expected_calls"].sum()), 3),
        "hexagons_without_recent_conflict": int(out["no_recent_conflict"].sum()),
        "model": f"gbm_{tag}_final_full.txt",
    }
    (args.dir / f"{stem}.json").write_text(json.dumps(summary, indent=2))
    for k, v in summary.items():
        log.info("%-38s %s", k, v)
    top = out.nsmallest(10, "risk_rank").merge(cell_tbl, on="cell")[["cell", "lat", "lon", "expected_calls", "conf_sum12"]]
    log.info("Top 10 hexagons:\n%s", top.round(3).to_string(index=False))
    emerg = (out.dropna(subset=["emerging_rank"]).nsmallest(10, "emerging_rank").merge(cell_tbl, on="cell")
             [["cell", "lat", "lon", "expected_calls", "never_conflict"]])
    log.info("Top 10 emerging-risk hexagons (no conflict in the last 12 months):\n%s", emerg.round(3).to_string(index=False))
    log.info("Wrote %s", args.dir / f"{stem}.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())