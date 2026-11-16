"""Combine FWC historical + recent bear calls into one clean table.

What this does:
  1. Harmonize schemas (the recent layer renamed three fields).
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

# Every value seen in the historical layer (Phase 0 EDA, 2026-11-16), normalized to lowercase.
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
    # not about bear behavior
    "illegal activity": "admin_other",
    "research": "admin_other",
    "research project": "admin_other",
    "species information": "admin_other",
    "other": "admin_other",
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


def combine(historical: gpd.GeoDataFrame, recent: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame:
    parts = [historical.assign(source_layer="historical")]
    if recent is not None and len(recent):
        parts.append(recent.rename(columns=RECENT_RENAME).assign(source_layer="recent"))
    df = pd.concat([p[[c for c in KEEP if c in p.columns] + ["source_layer"]] for p in parts], ignore_index=True)
    # If an incident appears in both layers, keep the recent copy.
    before = len(df)
    df = df.sort_values("source_layer", key=lambda s: s.eq("recent")).drop_duplicates("IncidentID", keep="last")
    if before - len(df):
        log.info("Dropped %d incidents present in both layers", before - len(df))
    return gpd.GeoDataFrame(df, geometry="geometry", crs=historical.crs)


def clean(historical: gpd.GeoDataFrame, recent: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    df = combine(historical, recent)
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

    df = clean(hist, recent)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "calls_clean.parquet"
    df.to_parquet(out, index=False)

    log.info("Wrote %s: %d calls, %s to %s", out, len(df), df["date"].min().date(), df["date"].max().date())
    log.info("By class:\n%s", df["call_class"].value_counts().to_string())
    log.info("Conflict calls: %d | encounter calls: %d | repeat rows: %d",
             df["is_conflict"].sum(), df["is_encounter"].sum(), df["is_repeat"].sum())
    return 0


if __name__ == "__main__":
    sys.exit(main())