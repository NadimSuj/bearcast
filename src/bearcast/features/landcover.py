"""Land-cover shares per hexagon from the Cooperative Land Cover (CLC v4) raster.

The raster is the STATE-level classification converted once from the File Geodatabase to
GeoTIFF (see docs/phase1_decisions.md). Pixel values are the hierarchical CLC codes
(1110 = Hardwood Forested Uplands, 18333 = Tree Plantations). Codes are grouped by their
leading digits into 13 bear-relevant groups, so 4-, 5- or zero-padded 7-digit codes all work.
Each pixel goes to the hexagon containing its centre, so neighbouring hexagons never share
a pixel. The raster is read in windows, so memory stays small at any resolution.

Features: lc_<group> share of the hexagon's pixels for each group, lc_nodata (pixels with
no class), lc_natural (all natural cover combined) and lc_diversity (Shannon index of the groups).

Output: data/processed/landcover_h3r7.parquet (+ _report.json)

Usage:
    python -m bearcast.features.landcover
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, bounds as window_bounds, transform as window_transform
from shapely.geometry import box

log = logging.getLogger(__name__)

PROCESSED = Path("data/processed")
CLC_TIF = Path("data/raw/landscape/landcover/clc_v4_state.tif")

# (group, code prefixes). First match wins, so specific prefixes come before general ones.
GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("tree_plantation", ("18333",)),
    ("urban_high", ("1822",)),
    ("urban_low", ("1821", "1810")),           # low-intensity urban + mowed grass (lawns, parks, golf)
    ("rural_developed", ("1830", "1831", "1832")),  # rural (unspecified), rural open, rural structures
    ("agriculture", ("1833",)),
    ("transport_extractive", ("184", "185", "186", "187", "188")),
    ("hardwood_upland", ("11", "14")),         # hardwood forested uplands + mixed hardwood-coniferous
    ("pine_scrub_upland", ("12", "13")),       # high pine, scrub, flatwoods, dry prairie
    ("shrub_coastal_barren", ("15", "16", "17")),
    ("forested_wetland", ("22",)),
    ("open_wetland", ("21", "23", "24")),
    ("water", ("3", "4", "5", "6", "8")),      # lakes, rivers, estuarine, marine, open water
    ("other", ()),                             # exotic plants (7000), other (9100), cultural parent (1800)
]
GROUP_NAMES = [g for g, _ in GROUPS]
NATURAL = ["hardwood_upland", "pine_scrub_upland", "shrub_coastal_barren", "forested_wetland", "open_wetland"]


def code_group(code: int) -> int:
    s = str(int(code))
    for i, (_, prefixes) in enumerate(GROUPS):
        if prefixes and s.startswith(prefixes):
            return i
    return len(GROUPS) - 1


def count_pixels(cells: gpd.GeoDataFrame, tif: Path, window: int = 2048) -> np.ndarray:
    """Return an (n_cells, n_groups + 1) matrix of pixel counts; the last column is no-data."""
    g_nodata = len(GROUPS)
    width = g_nodata + 1
    lut: dict[int, int] = {}
    with rasterio.open(tif) as src:
        hexes = cells[["cell", "geometry"]].to_crs(src.crs).reset_index(drop=True)
        geoms = hexes.geometry.values
        sidx = hexes.sindex
        counts = np.zeros((len(hexes) + 1) * width, dtype=np.int64)
        nodata = src.nodata
        n_win = 0
        for row in range(0, src.height, window):
            for col in range(0, src.width, window):
                win = Window(col, row, min(window, src.width - col), min(window, src.height - row))
                hits = sidx.query(box(*window_bounds(win, src.transform)))
                if len(hits) == 0:
                    continue
                ids = rasterize(((geoms[i], int(i) + 1) for i in hits), out_shape=(win.height, win.width),
                                transform=window_transform(win, src.transform), fill=0, dtype="int32")
                m = ids > 0
                if not m.any():
                    continue
                data = src.read(1, window=win)[m]
                uniq, inv = np.unique(data, return_inverse=True)
                for u in uniq.tolist():
                    if u not in lut:
                        lut[u] = g_nodata if (u == nodata or u <= 0) else code_group(u)
                groups = np.fromiter((lut[u] for u in uniq.tolist()), dtype=np.int64, count=len(uniq))[inv]
                counts += np.bincount(ids[m].astype(np.int64) * width + groups, minlength=counts.size)
                n_win += 1
        log.info("  read %d windows with hexagons; %d distinct pixel values", n_win, len(lut))
        unknown = sorted(u for u, g in lut.items() if g == len(GROUPS) - 1)
        if unknown:
            log.info("  codes grouped as 'other': %s", unknown[:40])
    return counts.reshape(len(hexes) + 1, width)[1:]


def landcover_features(cells: gpd.GeoDataFrame, tif: Path, window: int = 2048) -> pd.DataFrame:
    counts = count_pixels(cells, tif, window)
    total = counts.sum(axis=1, keepdims=True)
    shares = np.divide(counts, total, out=np.full(counts.shape, np.nan), where=total > 0)
    res = pd.DataFrame(shares, columns=[f"lc_{g}" for g in GROUP_NAMES] + ["lc_nodata"],
                       index=pd.Index(cells["cell"].values, name="cell"))
    res["lc_natural"] = res[[f"lc_{g}" for g in NATURAL]].sum(axis=1, min_count=1)
    classed = counts[:, :-1]
    n = classed.sum(axis=1, keepdims=True)
    p = np.divide(classed, n, out=np.zeros(classed.shape), where=n > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ent = -np.where(p > 0, p * np.log(p), 0).sum(axis=1)
    res["lc_diversity"] = np.where(n[:, 0] > 0, ent, np.nan)
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    p.add_argument("--tif", type=Path, default=CLC_TIF)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tag = f"h3r{args.res}"
    cells = gpd.read_parquet(args.dir / f"cells_{tag}.parquet", columns=["cell", "geometry"])
    with rasterio.open(args.tif) as src:
        log.info("%d cells | raster %d x %d, res %s, %s", len(cells), src.width, src.height, src.res, src.crs)
    out = landcover_features(cells, args.tif).astype("float32").reset_index()
    feat_cols = [c for c in out.columns if c != "cell"]
    path = args.dir / f"landcover_{tag}.parquet"
    out.to_parquet(path, index=False)

    desc = out[feat_cols].describe(percentiles=[0.5, 0.9]).T[["mean", "50%", "90%", "max"]].round(3)
    report = {"cells": int(len(out)), "features": feat_cols,
              "cells_without_pixels": int(out["lc_nodata"].isna().sum()),
              "summary": desc.to_dict(orient="index")}
    path.with_name(f"landcover_{tag}_report.json").write_text(json.dumps(report, indent=2))
    log.info("Summary:\n%s", desc.to_string())
    log.info("Wrote %s (%d features)", path, len(feat_cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())