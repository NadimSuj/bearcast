"""Build the modeling table: one row per H3 cell per month, with bear-call counts.

Steps:
  1. Keep calls in the modeling window (default 2015-01 to the last complete month).
  2. Drop repeat reports, imprecise coordinates (< 3 decimals ~ >100 m error), and
     suspected default geocodes (a low-precision point reused by many calls).
  3. Assign each call to an H3 hexagon (default resolution 7, ~5 km2).
  4. Cell universe = hexagons inside FWC's bear range polygons + any hexagon with a call
  + a ring of neighbours around calls outside the range (so the model sees zeros there too).
  5. Zero-fill every cell x month, count calls by type, and label the train/val/test split.

Outputs (data/processed/):
  panel_h3r7_month.parquet   cell, month, n_conflict, n_encounter, n_sighting, n_bear_welfare, split
  cells_h3r7.parquet         one row per cell: centroid, range level, hexagon polygon (GeoParquet)
  panel_h3r7_report.json     what was filtered and how sparse the table is

Usage:
    python -m bearcast.features.panel            # resolution 7
    python -m bearcast.features.panel --res 6    # coarser comparison
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

log = logging.getLogger(__name__)

INTERIM = Path("data/interim")
RAW = Path("data/raw")
PROCESSED = Path("data/processed")

RANGE_RANK = {"frequent": 4, "common": 3, "occasional": 2, "rare": 1}
COUNT_COLS = ["n_conflict", "n_encounter", "n_sighting", "n_bear_welfare"]

# Split by time so the test set is truly "the future" for the model.
TRAIN_END = pd.Timestamp("2023-12-01")  # last training month
VAL_END = pd.Timestamp("2024-12-01")    # last validation month; everything after is test


def coord_decimals(x: float) -> int:
    s = repr(float(x))
    return len(s.split(".")[1]) if "." in s and "e" not in s else 0


def last_complete_month(max_date: pd.Timestamp) -> pd.Timestamp:
    """Month start of the last month fully covered by the data."""
    max_date = pd.Timestamp(max_date).normalize()
    month = max_date.to_period("M").to_timestamp()
    if (max_date + pd.Timedelta(days=1)).day == 1:
        return month
    return month - pd.offsets.MonthBegin(1)


def filter_events(
    calls: pd.DataFrame,
    start: pd.Timestamp,
    end_month: pd.Timestamp,
    min_decimals: int = 3,
    default_min_calls: int = 10,
    default_max_decimals: int = 4,
) -> tuple[pd.DataFrame, dict]:
    """Return modeling-window events and a report of what was removed."""
    df = calls.copy()
    dec = np.minimum(df["Latitude"].map(coord_decimals), df["Longitude"].map(coord_decimals))
    df["coord_decimals"] = dec

    # Default geocodes are judged on the FULL history: a low-precision point reused many times.
    point_n = df.groupby(["Latitude", "Longitude"])["IncidentID"].transform("size")
    df["is_default_geocode"] = (point_n >= default_min_calls) & (dec <= default_max_decimals)

    window_end = end_month + pd.offsets.MonthBegin(1)
    in_window = (df["date"] >= start) & (df["date"] < window_end)
    df = df[in_window]

    report = {"calls_in_window": int(len(df))}
    report["dropped_repeat"] = int(df["is_repeat"].sum())
    df = df[~df["is_repeat"]]
    low = df["coord_decimals"] < min_decimals
    report["dropped_low_precision"] = int(low.sum())
    df = df[~low]
    report["dropped_default_geocode"] = int(df["is_default_geocode"].sum())
    report["default_geocode_points"] = (
        df.loc[df["is_default_geocode"]].groupby(["Latitude", "Longitude"]).size()
        .sort_values(ascending=False).head(20).reset_index(name="n").to_dict("records")
    )
    df = df[~df["is_default_geocode"]]
    report["events_kept"] = int(len(df))
    return df, report


def assign_cells(df: gpd.GeoDataFrame, res: int) -> pd.Series:
    return pd.Series(
        [h3.latlng_to_cell(y, x, res) for x, y in zip(df.geometry.x, df.geometry.y)],
        index=df.index, name="cell",
    )


def range_cells(range_gdf: gpd.GeoDataFrame | None, res: int) -> dict[str, int]:
    """Map each hexagon whose centre falls in a range polygon to the highest range rank covering it."""
    out: dict[str, int] = {}
    if range_gdf is None or range_gdf.empty:
        return out
    rng = range_gdf.to_crs(4326).copy()
    labels = rng["Label"].astype(str).str.strip().str.lower()
    rng["rank"] = labels.map(lambda s: next((v for k, v in RANGE_RANK.items() if s.startswith(k)), 0))
    unknown = sorted(set(labels[rng["rank"] == 0]))
    if unknown:
        log.warning("Unrecognized range labels (rank 0): %s", unknown)
    for _, row in rng.sort_values("rank").iterrows():  # higher ranks overwrite lower
        if row.geometry is None or row.geometry.is_empty:
            continue
        for cell in h3.geo_to_cells(row.geometry, res):
            out[cell] = max(out.get(cell, 0), int(row["rank"]))
    return out


def label_split(month: pd.Series) -> pd.Series:
    return pd.Series(
        np.select([month <= TRAIN_END, month <= VAL_END], ["train", "val"], "test"),
        index=month.index, name="split",
    )


def build_panel(
    events: gpd.GeoDataFrame,
    range_rank: dict[str, int],
    start: pd.Timestamp,
    end_month: pd.Timestamp,
    res: int,
    ring: int = 1,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:    
    ev = events.copy()
    ev["cell"] = assign_cells(ev, res)
    ev["month"] = ev["date"].dt.to_period("M").dt.to_timestamp()
    ev["n_conflict"] = ev["is_conflict"].astype(int)
    ev["n_encounter"] = ev["is_encounter"].astype(int)
    ev["n_sighting"] = (ev["call_class"] == "sighting").astype(int)
    ev["n_bear_welfare"] = (ev["call_class"] == "bear_welfare").astype(int)

    # Outside the mapped range, the only cells we'd know about are ones WITH calls, so the model
    # would never see a quiet cell there. Adding their neighbours gives it those zeros.
    call_cells = set(ev["cell"])
    ring_cells: set[str] = set()
    if ring > 0:
        for c in call_cells - set(range_rank):
            ring_cells.update(h3.grid_disk(c, ring))
    cells = sorted(set(range_rank) | call_cells | ring_cells)    
    months = pd.date_range(start, end_month, freq="MS")
    idx = pd.MultiIndex.from_product([cells, months], names=["cell", "month"])

    counts = ev.groupby(["cell", "month"])[COUNT_COLS].sum()
    panel = counts.reindex(idx, fill_value=0).reset_index()
    panel[COUNT_COLS] = panel[COUNT_COLS].astype("int32")
    panel["split"] = label_split(panel["month"])

    # One row per cell with its centre, range level and hexagon polygon (for maps and joins).
    centers = [h3.cell_to_latlng(c) for c in cells]
    polys = [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells]
    rank = [range_rank.get(c, 0) for c in cells]
    inv = {v: k for k, v in RANGE_RANK.items()}
    cell_tbl = gpd.GeoDataFrame(
        {
            "cell": cells,
            "lat": [p[0] for p in centers],
            "lon": [p[1] for p in centers],
            "range_rank": rank,
            "range_label": [inv.get(r, "outside") for r in rank],
            "in_range": [r > 0 for r in rank],
        },
        geometry=polys, crs=4326,
    )
    totals = panel.groupby("cell")[COUNT_COLS].sum().add_suffix("_total")
    cell_tbl = cell_tbl.merge(totals, left_on="cell", right_index=True, how="left")
    return panel, cell_tbl


def sparsity_report(panel: pd.DataFrame, cell_tbl: pd.DataFrame) -> dict:
    nz = panel["n_conflict"] > 0
    per_cell = panel.groupby("cell")["n_conflict"].sum().sort_values(ascending=False)
    top5 = max(1, int(round(len(per_cell) * 0.05)))
    return {
        "n_cells": int(panel["cell"].nunique()),
        "n_cells_in_range": int(cell_tbl["in_range"].sum()),
        "n_cells_with_any_conflict": int((per_cell > 0).sum()),
        "n_months": int(panel["month"].nunique()),
        "n_rows": int(len(panel)),
        "pct_cell_months_with_conflict": round(100 * float(nz.mean()), 2),
        "mean_conflict_per_cell_month": round(float(panel["n_conflict"].mean()), 4),
        "max_conflict_in_one_cell_month": int(panel["n_conflict"].max()),
        "pct_conflicts_in_top5pct_cells": round(100 * float(per_cell.head(top5).sum() / max(per_cell.sum(), 1)), 1),
        "rows_by_split": panel["split"].value_counts().to_dict(),
        "conflicts_by_split": panel.groupby("split")["n_conflict"].sum().to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7, help="H3 resolution (7 ~5 km2, 6 ~36 km2)")
    p.add_argument("--start", default="2015-01", help="first month, YYYY-MM")
    p.add_argument("--end", default=None, help="last month, YYYY-MM (default: last complete month)")
    p.add_argument("--ring", type=int, default=1, help="neighbour rings added around calls outside the range")
    p.add_argument("--interim", type=Path, default=INTERIM)
    p.add_argument("--raw", type=Path, default=RAW)
    p.add_argument("--out", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    calls = gpd.read_parquet(args.interim / "calls_clean.parquet")
    start = pd.Timestamp(args.start + "-01")
    end_month = pd.Timestamp(args.end + "-01") if args.end else last_complete_month(calls["date"].max())
    log.info("Window: %s to %s, H3 resolution %d", start.date(), end_month.date(), args.res)

    events, filt = filter_events(calls, start, end_month)
    range_path = args.raw / "fwc_range.parquet"
    range_gdf = gpd.read_parquet(range_path) if range_path.exists() else None
    if range_gdf is None:
        log.warning("No range file; the cell universe will only contain cells with calls")
    rr = range_cells(range_gdf, args.res)

    panel, cell_tbl = build_panel(events, rr, start, end_month, args.res, args.ring)
    report = {"resolution": args.res, "ring": args.ring, "start": str(start.date()), "end_month": str(end_month.date()),
              "filtering": filt, "sparsity": sparsity_report(panel, cell_tbl)}

    args.out.mkdir(parents=True, exist_ok=True)
    tag = f"h3r{args.res}"
    panel.to_parquet(args.out / f"panel_{tag}_month.parquet", index=False)
    cell_tbl.to_parquet(args.out / f"cells_{tag}.parquet", index=False)
    (args.out / f"panel_{tag}_report.json").write_text(json.dumps(report, indent=2, default=str))

    log.info("Filtering: %s", {k: v for k, v in filt.items() if k != "default_geocode_points"})
    for k, v in report["sparsity"].items():
        log.info("  %-34s %s", k, v)
    log.info("Wrote %s", args.out / f"panel_{tag}_month.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())