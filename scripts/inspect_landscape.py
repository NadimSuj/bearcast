"""Describe every GIS file under data/raw/landscape without loading it all into memory.

For each vector layer (shapefile, GeoPackage, File Geodatabase): layer name, geometry type,
coordinate system, feature count and every field with its type.
For each raster (GeoTIFF, .img, Esri grid, FileGDB raster): size, cell size, coordinate system,
data type, nodata value, and the most common cell values from a sampled read.

Usage:
    python scripts/inspect_landscape.py                # everything under data/raw/landscape
    python scripts/inspect_landscape.py path/to/folder # somewhere else
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyogrio
import rasterio
from rasterio.enums import Resampling

VECTOR_EXT = {".shp", ".gpkg", ".geojson", ".json"}
RASTER_EXT = {".tif", ".tiff", ".img", ".vrt"}


def crs_name(crs) -> str:
    if crs is None:
        return "none"
    epsg = crs.to_epsg() if hasattr(crs, "to_epsg") else None
    return f"EPSG:{epsg}" if epsg else str(crs)[:80]


def describe_vector(path: Path) -> None:
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:  # not a vector source
        print(f"  (not readable as vector: {exc})")
        return
    for name, geom in layers:
        info = pyogrio.read_info(path, layer=name)
        print(f"  layer '{name}': {geom}, {info['features']:,} features, crs {info['crs']}")
        for field, dtype in zip(info["fields"], info["dtypes"]):
            print(f"      {field:<28} {dtype}")
        if info["features"]:
            sample = pyogrio.read_dataframe(path, layer=name, max_features=3, read_geometry=False)
            print("    first rows:")
            print("      " + sample.head(3).to_string(max_colwidth=30).replace("\n", "\n      "))


def describe_raster(path: Path) -> None:
    try:
        src = rasterio.open(path)
    except Exception as exc:
        print(f"  (not readable as raster: {exc})")
        return
    with src:
        print(f"  {src.driver}: {src.width:,} x {src.height:,} cells, {src.count} band(s), "
              f"cell size {src.res[0]:.2f} x {src.res[1]:.2f}, crs {crs_name(src.crs)}")
        print(f"  dtype {src.dtypes[0]}, nodata {src.nodata}, bounds {tuple(round(b, 1) for b in src.bounds)}")
        # Sampled read (about 2,000 cells on the short side) so a statewide 10 m raster doesn't fill memory.
        step = max(1, min(src.width, src.height) // 2000)
        data = src.read(1, out_shape=(src.height // step, src.width // step), resampling=Resampling.nearest)
        vals = data[data != src.nodata] if src.nodata is not None else data.ravel()
        counts = Counter(np.asarray(vals).ravel().tolist()).most_common(40)
        print(f"  {len(counts)} most common values in a 1/{step} sample (value: share of sampled cells):")
        total = max(1, len(vals))
        print("    " + ", ".join(f"{v}: {100 * n / total:.1f}%" for v, n in counts))
        try:
            print(f"  band description: {src.descriptions[0]}; tags: {dict(list(src.tags(1).items())[:10])}")
        except Exception:
            pass


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw/landscape")
    if not root.exists():
        print(f"{root} does not exist. Create it and unzip the downloads into it.")
        return 1

    print(f"=== Files under {root} ===")
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".gdb" not in p.parts[-2]:
            print(f"  {p.relative_to(root)}  ({p.stat().st_size / 1e6:.1f} MB)")

    gdbs = sorted({p for p in root.rglob("*.gdb") if p.is_dir()})
    for gdb in gdbs:
        print(f"\n=== File Geodatabase: {gdb.relative_to(root)} ===")
        describe_vector(gdb)
        for sub in _raster_subdatasets(gdb):  # rasters stored inside a FileGDB, if any
            print(f"\n  raster subdataset {sub}")
            describe_raster(Path(sub))

    for p in sorted(root.rglob("*")):
        if any(part.endswith(".gdb") for part in p.parts):
            continue
        ext = p.suffix.lower()
        if p.is_file() and ext in VECTOR_EXT:
            print(f"\n=== Vector: {p.relative_to(root)} ===")
            describe_vector(p)
        elif p.is_file() and ext in RASTER_EXT:
            print(f"\n=== Raster: {p.relative_to(root)} ===")
            describe_raster(p)
        elif p.is_dir() and (p / "hdr.adf").exists():  # Esri grid folder
            print(f"\n=== Raster (Esri grid): {p.relative_to(root)} ===")
            describe_raster(p)
    return 0


def _raster_subdatasets(path: Path) -> list[str]:
    try:
        with rasterio.open(path) as src:
            return list(src.subdatasets) or [str(path)]
    except Exception:  # vector-only geodatabase
        return []


if __name__ == "__main__":
    sys.exit(main())