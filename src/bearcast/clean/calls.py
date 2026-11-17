"""Combine FWC historical + recent bear calls into one clean table.

What this does:
  1. Harmonize schemas (the recent layer renamed three fields). Rows from the
     Open Data layer are used only to fill dates the other two layers miss.
  2. Fix dates: FWC stores date-only values as 00:00 UTC. Converting those to
     Florida time would shift every call back one day, so we take the UTC date.
  3. Map the 41 raw call reasons to six classes + conflict/encounter flags.
  4. Flag same-day repeat reports at the same spot for the same reason.

Output: data/interim/calls_clean.parquet

Usage:
    python -m bearcast.clean.calls
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)

RAW = Path("data/raw")
INTERIM = Path("data/interim")

# Recent layer field names -> historical names
RECENT_RENAME = {"FWCRegion": "Region", "ZIPCode": "ZipCode", "ReasonForCall": "CallNature"}

# Every value seen in the historical + recent layers (Phase 0 EDA, 2026-11-16), lowercase.
CALL_CLASS: dict[str, str] = {
    # bear getting into human food / property
    "in garbage": "attractant_property",
    "in feeder": "attractant_property",
    "in feed": "attractant_property",
    "property damage": "attractant_property",
    "apiary": "attractant_property",
    "in crops": "attractant_property",
    "in screened porch/patio": "attractant_property",
    "in unscreened porch/patio": "attractant_property",
    "in building": "attractant_property",
    "in open garage": "attractant_property",
    "in building/tent/vehicle": "attractant_property",
    "under structure": "attractant_property",
    "in hog trap": "attractant_property",
    "in trap": "attractant_property",
    # pets / livestock
    "killed animal": "pets_livestock",
    "attacked animal": "pets_livestock",
    "threatened animal": "pets_livestock",
    "injured animal": "pets_livestock",
    # people
    "threatened human": "human_safety",
    "attacked human": "human_safety",
    "injured human": "human_safety",
    "unintentionally approached human": "human_safety",
    "killed human": "human_safety",  # 1 record: FWC-confirmed fatal attack, Collier County, 2025-05-05
    # bear seen near people, no damage
    "in area": "sighting",
    "in yard": "sighting",
    "in tree": "sighting",
    "confirmed sighting": "sighting",
    # bear is the one harmed
    "dead bear": "bear_welfare",
    "sick/injured": "bear_welfare",
    "sick/injured bear": "bear_welfare",
    "human threatened bear": "bear_welfare",
    "animal threatened bear": "bear_welfare",
    "animal attacked bear": "bear_welfare",
    "animal injured bear": "bear_welfare",
    "animal killed bear": "bear_welfare",
    "harvest (hunt)": "bear_welfare",
    "orphaned cub": "bear_welfare",
    # not about bear behavior
    "illegal activity": "admin_other",
    "research": "admin_other",
    "research project": "admin_other",
    "species information": "admin_other",
    "other": "admin_other",
    "unknown": "admin_other",
    "": "admin_other",
}
CONFLICT_CLASSES = {"attractant_property", "pets_livestock", "human_safety"}
ENCOUNTER_CLASSES = CONFLICT_CLASSES | {"sighting"}

KEEP = ["IncidentID", "ReportDate", "Region", "BMU", "County", "City", "ZipCode",
        "Latitude", "Longitude", "CallNature", "geometry"]


def report_date(ts: pd.Series) -> pd.Series:
    """Calendar date of the report as naive datetime64 (midnight)."""
    utc = ts.dt.tz_convert("UTC")
    non_midnight = float((utc != utc.dt.normalize()).mean())
    if non_midnight > 0.01:
        # Real timestamps: take the date in Florida time instead.
        log.warning("%.1f%% of ReportDate values have a time of day; using local dates", 100 * non_midnight)
        return ts.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    return utc.dt.tz_localize(None).dt.normalize()


def normalize_reason(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)


def classify(reason_norm: pd.Series) -> pd.Series:
    cls = reason_norm.map(CALL_CLASS)
    unmapped = reason_norm[cls.isna()].value_counts()
    if len(unmapped):
        log.warning("Unmapped call reasons (add them to CALL_CLASS):\n%s", unmapped.to_string())
    return cls.fillna("unmapped")


def combine(
    historical: gpd.GeoDataFrame,
    recent: gpd.GeoDataFrame | None,
    open_layer: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    parts = [historical.assign(source_layer="historical")]
    has_recent = recent is not None and len(recent) > 0
    if has_recent:
        parts.append(recent.rename(columns=RECENT_RENAME).assign(source_layer="recent"))

    if open_layer is not None and len(open_layer):
        # Use the Open Data layer ONLY for dates the other two layers don't cover
        # (the 2023-2024 gap). Its IncidentIDs may not match, so this avoids double counting.
        hist_end = historical["ReportDate"].max()
        in_gap = open_layer["ReportDate"] > hist_end
        rec_start = recent["ReportDate"].min() if has_recent else None
        if rec_start is not None:
            in_gap &= open_layer["ReportDate"] < rec_start
        gap = open_layer[in_gap]
        log.info("Open Data layer: %d of %d rows fall between %s and %s",
                 len(gap), len(open_layer), hist_end.date(), rec_start.date() if rec_start is not None else "now")
        if len(gap):
            parts.append(gap.assign(source_layer="open_gapfill"))

    df = pd.concat([p[[c for c in KEEP if c in p.columns] + ["source_layer"]] for p in parts], ignore_index=True)
    # If an IncidentID appears more than once, keep one copy: recent beats other layers, and within a
    # layer the last-downloaded row (highest OBJECTID) wins. kind="stable" keeps that order deterministic.
    before = len(df)
    df = df.sort_values("source_layer", key=lambda s: s.eq("recent"), kind="stable").drop_duplicates("IncidentID", keep="last")
    if before - len(df):
        log.info("Dropped %d incidents present in more than one layer", before - len(df))
    return gpd.GeoDataFrame(df, geometry="geometry", crs=historical.crs)


def clean(
    historical: gpd.GeoDataFrame,
    recent: gpd.GeoDataFrame | None = None,
    open_layer: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    df = combine(historical, recent, open_layer)
    df["date"] = report_date(df["ReportDate"])
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["reason"] = normalize_reason(df["CallNature"])
    df["call_class"] = classify(df["reason"])
    df["is_conflict"] = df["call_class"].isin(CONFLICT_CLASSES)
    df["is_encounter"] = df["call_class"].isin(ENCOUNTER_CLASSES)

    # Same day + same coordinates + same reason = repeat report of one event.
    key = ["date", "Latitude", "Longitude", "reason"]
    df["repeat_group_size"] = df.groupby(key)["IncidentID"].transform("size")
    df["is_repeat"] = df.duplicated(key, keep="first")  # keep first row of each group as the "event"

    for c in ["BMU", "Region", "County", "City", "ZipCode"]:
        if c in df:
            df[c] = df[c].replace("", pd.NA)

    return df.sort_values(["date", "IncidentID"]).reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", type=Path, default=RAW)
    p.add_argument("--out", type=Path, default=INTERIM)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    hist = gpd.read_parquet(args.raw / "fwc_bear_calls.parquet")
    recent_path = args.raw / "fwc_calls_recent.parquet"
    recent = gpd.read_parquet(recent_path) if recent_path.exists() else None
    if recent is None:
        log.warning("No recent calls file; run `python -m bearcast.ingest.fwc calls_recent`")
    open_path = args.raw / "fwc_calls_open.parquet"
    open_layer = gpd.read_parquet(open_path) if open_path.exists() else None
    if open_layer is None:
        log.warning("No Open Data calls file; run `python -m bearcast.ingest.fwc calls_open` to fill 2023-2024")

    df = clean(hist, recent, open_layer)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "calls_clean.parquet"
    df.to_parquet(out, index=False)

    log.info("Wrote %s: %d calls, %s to %s", out, len(df), df["date"].min().date(), df["date"].max().date())
    log.info("By class:\n%s", df["call_class"].value_counts().to_string())
    log.info("Conflict calls: %d | encounter calls: %d | repeat rows: %d",
             df["is_conflict"].sum(), df["is_encounter"].sum(), df["is_repeat"].sum())
    per_year = df[df["year"] >= 2015].groupby(["year", "source_layer"]).size().unstack(fill_value=0)
    log.info("Calls per year since 2015, by source layer:\n%s", per_year.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())