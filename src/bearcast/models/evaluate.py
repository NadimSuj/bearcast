"""Scoring rules shared by every model and baseline.

capture@k: each month, rank all cells by a score, take the top k% and ask what share of that
month's conflicts happened in them. It answers the practical question "if FWC could only watch
k% of the map next month, how much of the trouble would they see?". Averaged over months.

cold capture@k: the same ranking, but counting only conflicts in cells with no conflict in the
previous 12 months. History-only rankings are weak here, and it's where the landscape should help.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def capture_by_month(df: pd.DataFrame, score: str, k: float, target: str = "n_conflict",
                     only: pd.Series | None = None, seed: int = 0) -> pd.Series:
    """Share of each month's conflicts (optionally only rows where `only` is True) in the top k% of cells."""
    rng = np.random.default_rng(seed)
    y_all = df[target].to_numpy(dtype=float)
    y_num = y_all * only.to_numpy() if only is not None else y_all
    s_all = df[score].to_numpy(dtype=float)
    out = {}
    for m, idx in df.groupby("month", sort=True, observed=True).indices.items():
        total = y_num[idx].sum()
        if total == 0:
            continue
        n_top = max(1, int(np.ceil(k * len(idx))))
        order = np.lexsort((rng.random(len(idx)), -s_all[idx]))   # ties broken at random, not by row order
        out[m] = y_num[idx][order[:n_top]].sum() / total
    return pd.Series(out, name=score, dtype=float)


def poisson_deviance(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.clip(np.asarray(mu, dtype=float), 1e-9, None)
    y = np.asarray(y, dtype=float)
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(2 * np.mean(term - (y - mu)))


def summarize(df: pd.DataFrame, scores: list[str], ks=(0.01, 0.05), rate_scores=(),
              target: str = "n_conflict", cold_col: str = "conf_sum12") -> pd.DataFrame:
    """One row per score: capture@k, cold capture@k, and for rate-like scores Poisson deviance + calibration."""
    cold = df[cold_col].eq(0)
    rows = []
    for s in scores:
        r: dict = {"score": s}
        for k in ks:
            r[f"capture@{k:.0%}"] = capture_by_month(df, s, k, target).mean()
        for k in ks:
            r[f"cold_capture@{k:.0%}"] = capture_by_month(df, s, k, target, only=cold).mean()
        if s in rate_scores:
            r["poisson_dev"] = poisson_deviance(df[target].to_numpy(), df[s].to_numpy())
            r["pred/actual"] = float(df[s].sum() / df[target].sum())
        rows.append(r)
    return pd.DataFrame(rows).set_index("score")