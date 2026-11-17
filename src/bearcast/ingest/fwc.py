"""Download FWC Florida black bear layers to data/raw as GeoParquet.

Usage:
    python -m bearcast.ingest.fwc --all
    python -m bearcast.ingest.fwc bear_calls roadkill
    python -m bearcast.ingest.fwc --list
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from bearcast.ingest.arcgis import fetch_layer

HOST = "https://gis.myfwc.com"
OPEN = f"{HOST}/hosting/rest/services/Open_Data"
PUBLIC_MAP = f"{HOST}/mapping/rest/services/Projects_FWC/InteractivePublicBearMap/MapServer"

# Layer IDs verified against the live services on 2026-11-16.
FWC_LAYERS: dict[str, dict[str, str]] = {
    "bear_calls": {
        "url": f"{PUBLIC_MAP}/3",
        "note": "Calls HISTORICAL (1980-2022). Fields: Region, ZipCode, CallNature.",
    },
    "calls_recent": {
        "url": f"{PUBLIC_MAP}/2",
        "note": "Calls RECENT (2023+). Renamed fields: FWCRegion, ZIPCode, ReasonForCall.",
    },
    "handled_historical": {
        "url": f"{PUBLIC_MAP}/1",
        "note": "Bears handled by FWC (captures, relocations, mortalities) with reason/sex/age.",
    },
    "handled_recent": {
        "url": f"{PUBLIC_MAP}/0",
        "note": "Bears handled by FWC, recent records.",
    },
        "calls_open": {
        "url": f"{HOST}/mapping/rest/services/Open_Data/Black_Bear_Related_Calls_in_Florida/MapServer/13",
        "note": "Open Data calls layer (WIMS). Used only to fill dates the other calls layers miss.",
    },
    "roadkill": {
        "url": f"{OPEN}/Black_Bear_Roadkill_Locations/MapServer/12",
        "note": "Vehicle-killed bears. Secondary target / movement signal.",
    },
    "captures": {
        "url": f"{OPEN}/Black_Bear_Capture_Locations/MapServer/9",
        "note": "Capture locations 1981-2024.",
    },
    "relocations": {
        "url": f"{OPEN}/Black_Bear_Relocation_Locations/MapServer/8",
        "note": "Where bears were moved to. Exclude from 'natural' presence data.",
    },
    "telemetry_camp_blanding": {
        "url": f"{OPEN}/Black_Bear_Telemetry_Camp_Blanding_WMA/MapServer/0",
        "note": "GPS collar fixes, Ocala-to-Osceola corridor study. Movement model.",
    },
    "telemetry_combined": {
        "url": f"{OPEN}/Black_Bear_Telemetry/MapServer/11",
        "note": "Telemetry combined from multiple shared projects.",
    },
    "range": {
        "url": f"{OPEN}/Black_Bear_Range_Florida/MapServer/8",
        "note": "Range polygons: frequent / common / occasional / rare.",
    },
}

DEFAULT_OUT = Path("data/raw")


def download(name: str, out_dir: Path = DEFAULT_OUT) -> Path:
    spec = FWC_LAYERS[name]
    gdf = fetch_layer(spec["url"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"fwc_{name}.parquet"
    gdf.to_parquet(path, index=False)

    manifest = {
        "layer": name,
        "source_url": spec["url"],
        "note": spec["note"],
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_features": int(len(gdf)),
        "columns": [c for c in gdf.columns if c != "geometry"],
        "attribution": "Florida Fish and Wildlife Conservation Commission (FWC)",
    }
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))
    logging.info("Wrote %s (%d rows)", path, len(gdf))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("layers", nargs="*", help="layer names (see --list)")
    parser.add_argument("--all", action="store_true", help="download every layer")
    parser.add_argument("--list", action="store_true", help="list available layers")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.list:
        for k, v in FWC_LAYERS.items():
            print(f"{k:26s} {v['note']}")
        return 0

    names = list(FWC_LAYERS) if args.all else args.layers
    unknown = [n for n in names if n not in FWC_LAYERS]
    if not names or unknown:
        parser.error(f"choose layers from: {', '.join(FWC_LAYERS)}" + (f" (unknown: {unknown})" if unknown else ""))

    failures = []
    for n in names:
        try:
            download(n, args.out)
        except Exception as exc:  # keep going; report at the end
            logging.error("FAILED %s: %s", n, exc)
            failures.append(n)
    if failures:
        logging.error("Failed layers: %s", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
