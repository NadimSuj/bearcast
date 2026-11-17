"""Inspect the bear point layers already downloaded: GPS telemetry, roadkill, captures, relocations.

For each file prints size, columns, date range and extent. For telemetry it also guesses which
column identifies individual bears and reports fixes per bear and the typical time between fixes,
which decides what kind of movement modelling the data supports.

Usage:
    python scripts/inspect_bear_points.py > bear_points.txt 2>&1
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

RAW = Path("data/raw")
FILES = ["fwc_telemetry_combined", "fwc_telemetry_camp_blanding", "fwc_roadkill", "fwc_captures", "fwc_relocations"]
ID_HINTS = ("bear", "animal", "collar", "indiv", "name", "tag", "id")


def load(path: Path) -> pd.DataFrame:
    try:
        return gpd.read_parquet(path)
    except Exception:  # noqa: BLE001  (not a GeoParquet file)
        return pd.read_parquet(path)


def date_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]


def id_candidates(df: pd.DataFrame) -> list[tuple[str, int]]:
    out = []
    for c in df.columns:
        lc = c.lower()
        if lc in ("objectid", "fid", "oid") or c == "geometry" or not any(h in lc for h in ID_HINTS):
            continue
        n = df[c].nunique(dropna=True)
        if 2 <= n <= max(5000, len(df) // 20):
            out.append((c, int(n)))
    return sorted(out, key=lambda x: x[1])


def describe(name: str) -> None:
    path = RAW / f"{name}.parquet"
    print("\n" + "=" * 80 + f"\n{name}")
    if not path.exists():
        print("  (file not found - run: python -m bearcast.ingest.fwc " + name.removeprefix("fwc_") + ")")
        return
    df = load(path)
    print(f"  rows {len(df):,} | columns: {list(df.columns)}")
    dcols = date_columns(df)
    for c in dcols:
        s = df[c].dropna()
        if len(s):
            print(f"  {c}: {s.min()} -> {s.max()} ({s.dt.year.nunique()} distinct years); "
                  f"per year: {s.dt.year.value_counts().sort_index().to_dict()}")
    if isinstance(df, gpd.GeoDataFrame) and df.geometry.notna().any():
        b = df.to_crs(4326).total_bounds
        print(f"  extent lon {b[0]:.2f}..{b[2]:.2f}, lat {b[1]:.2f}..{b[3]:.2f}")
    for c in [c for c in df.columns if c.lower() in ("county", "region", "fwcregion", "study_area", "studyarea", "site")]:
        print(f"  {c}: {df[c].value_counts().head(15).to_dict()}")
    print("  first 3 rows:")
    print(df.drop(columns="geometry", errors="ignore").head(3).to_string(max_colwidth=30))

    if "telemetry" not in name:
        return
    cands = id_candidates(df)
    print(f"  likely bear-ID columns (column, distinct values): {cands}")
    if not cands or not dcols:
        print("  -> can't build tracks without both a bear ID and a timestamp")
        return
    idc, tc = cands[-1][0] if len(cands) == 1 else cands[0][0], dcols[0]
    for idc in [c for c, _ in cands][:2]:
        g = df[[idc, tc]].dropna().sort_values([idc, tc])
        per = g.groupby(idc)[tc].agg(["count", "min", "max"])
        gaps_h = g.groupby(idc)[tc].diff().dt.total_seconds().div(3600).dropna()
        gaps_h = gaps_h[gaps_h > 0]
        print(f"\n  using ID column '{idc}' and time column '{tc}':")
        print(f"    bears {len(per):,} | fixes per bear: median {per['count'].median():.0f}, "
              f"max {per['count'].max():,} | days tracked per bear: median "
              f"{(per['max'] - per['min']).dt.days.median():.0f}")
        if len(gaps_h):
            q = np.percentile(gaps_h, [10, 25, 50, 75, 90])
            print("    hours between fixes (10/25/50/75/90th pct): " + " / ".join(f"{v:.1f}" for v in q))
            print(f"    share of gaps <= 1h {np.mean(gaps_h <= 1):.0%}, <= 4h {np.mean(gaps_h <= 4):.0%}, "
                  f"<= 24h {np.mean(gaps_h <= 24):.0%}")
        print(f"    tracking years per bear-start: {per['min'].dt.year.value_counts().sort_index().to_dict()}")


def main() -> int:
    for f in FILES:
        describe(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())