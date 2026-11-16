"""Download GBIF Ursus americanus occurrences in Florida, EXCLUDING iNaturalist.

iNaturalist records reach GBIF through one dataset (key below), so we drop that
dataset plus anything whose institutionCode says iNaturalist, as a safety net.

Uses the public occurrence search API (no account needed). It caps paging at
100,000 records, far more than Florida bears will have. For a citable DOI later,
switch to the GBIF download API (needs a free account).

Usage:
    python -m bearcast.ingest.gbif
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

API = "https://api.gbif.org/v1/occurrence/search"
URSUS_AMERICANUS_TAXON_KEY = 2433407
FLORIDA_GADM_GID = "USA.10_1"
INATURALIST_DATASET_KEY = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"
PAGE = 300  # GBIF max page size for search
SEARCH_OFFSET_CAP = 100_000

KEEP = [
    "key", "datasetKey", "datasetName", "publishingOrgKey", "institutionCode",
    "basisOfRecord", "occurrenceStatus", "eventDate", "year", "month", "day",
    "decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters",
    "stateProvince", "county", "license", "issues",
]

DEFAULT_OUT = Path("data/raw")
log = logging.getLogger(__name__)


def _get(session: requests.Session, params: dict, retries: int = 4) -> dict:
    for attempt in range(1, retries + 1):
        try:
            r = session.get(API, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise
            log.warning("GBIF request failed (%s); retry %d", exc, attempt)
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def fetch_occurrences(session: requests.Session | None = None, area: dict | None = None) -> list[dict]:
    session = session or requests.Session()
    base = {
        "taxonKey": URSUS_AMERICANUS_TAXON_KEY,
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "occurrenceStatus": "PRESENT",
        "limit": PAGE,
    }
    base.update(area or {"gadmGid": FLORIDA_GADM_GID})

    records: list[dict] = []
    offset = 0
    while True:
        page = _get(session, {**base, "offset": offset})
        results = page.get("results", [])
        records.extend(results)
        log.info("GBIF: %d / %s", len(records), page.get("count"))
        if page.get("endOfRecords", True) or not results:
            break
        offset += PAGE
        if offset >= SEARCH_OFFSET_CAP:
            log.warning("Hit GBIF search offset cap; use the download API instead")
            break
        time.sleep(0.2)
    return records


def is_inaturalist(rec: dict) -> bool:
    return (
        rec.get("datasetKey") == INATURALIST_DATASET_KEY
        or "inaturalist" in str(rec.get("institutionCode", "")).lower()
        or "inaturalist" in str(rec.get("datasetName", "")).lower()
    )


def to_geodataframe(records: list[dict]) -> gpd.GeoDataFrame:
    df = pd.DataFrame([{k: r.get(k) for k in KEEP} for r in records])
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs="EPSG:4326")
    df = df[df["basisOfRecord"] != "FOSSIL_SPECIMEN"]
    df["issues"] = df["issues"].apply(lambda x: ",".join(x) if isinstance(x, list) else x)
    df["eventDate"] = pd.to_datetime(df["eventDate"], errors="coerce", utc=True, format="mixed")
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["decimalLongitude"], df["decimalLatitude"]),
        crs="EPSG:4326",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    records = fetch_occurrences()
    if not records:
        log.warning("No records via gadmGid; retrying with stateProvince=Florida")
        records = fetch_occurrences(area={"country": "US", "stateProvince": "Florida"})

    kept = [r for r in records if not is_inaturalist(r)]
    dropped = len(records) - len(kept)
    log.info("Dropped %d iNaturalist records; kept %d", dropped, len(kept))

    gdf = to_geodataframe(kept)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "gbif_ursus_americanus_fl.parquet"
    gdf.to_parquet(path, index=False)

    by_dataset = gdf["datasetName"].value_counts().to_dict() if len(gdf) else {}
    manifest = {
        "source": API,
        "taxon_key": URSUS_AMERICANUS_TAXON_KEY,
        "area": FLORIDA_GADM_GID,
        "excluded_dataset_keys": [INATURALIST_DATASET_KEY],
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_total_before_filter": len(records),
        "n_inaturalist_dropped": dropped,
        "n_kept": int(len(gdf)),
        "records_by_dataset": by_dataset,
        "license_counts": gdf["license"].value_counts().to_dict() if len(gdf) else {},
    }
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Wrote %s", path)
    for name, n in list(by_dataset.items())[:15]:
        log.info("  %6d  %s", n, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
