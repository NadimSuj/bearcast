"""Landscape and people features for each hexagon (static: one row per cell).

Sources (downloaded manually into data/raw/landscape/):
  housing/       SILVIS WUI change 1990-2020, Florida census blocks
  conservation/  FNAI Florida Conservation Lands (FLMA), File Geodatabase
  roads/         FDOT Basemap Routes + Annual Average Daily Traffic (AADT)

Features:
  hu_2020, hu_den_2020, hu_den_2010   housing units, and units per km2 (2020, 2010)
  hu_growth_2010_2020                 log(1 + units 2020) - log(1 + units 2010)
  pop_den_2020                        people per km2
  wui_intermix_share                  share of the cell in "intermix" WUI (houses mixed into vegetation), 2020
  wui_interface_share                 share in "interface" WUI (houses next to large wild areas), 2020
  veg_pct_2019                        % vegetation cover (area-weighted, from SILVIS)
  cons_share                          share of the cell inside conservation land
  dist_cons_km                        km from the cell centre to the nearest conservation land (0 if inside)
  dist_bigcons_km                     km to the nearest conservation area of 10,000+ acres
  road_km_per_km2                     km of FDOT-mapped road per km2
  traffic_vkm_per_km2                 daily vehicle-km per km2 (AADT x road length)
  max_aadt                            busiest road segment in the cell (vehicles per day)
  dist_major_road_km                  km to the nearest road carrying 10,000+ vehicles per day

Census blocks are assigned to the hexagon containing their interior point, which is
accurate where blocks are small (towns) and coarser in rural areas, where there are few houses.

Output: data/processed/landscape_h3r7.parquet (+ _report.json)

Usage:
    python -m bearcast.features.landscape
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

log = logging.getLogger(__name__)

CRS = 3087  # NAD83(HARN) Florida GDL Albers: equal-area metres, FNAI's native CRS
PROCESSED = Path("data/processed")
LAND = Path("data/raw/landscape")
HOUSING = LAND / "housing/FL_wui_block_1990_2020_change_v4.shp"
CONSERVATION = LAND / "conservation/FloridaConservationLands.gdb"
ROUTES = LAND / "roads/Basemap_routes.shp"
AADT = LAND / "roads/aadt.shp"

BIG_CONS_ACRES = 10_000
MAJOR_ROAD_AADT = 10_000
# SILVIS field names are truncated to 10 characters: WUICLASS_2 is the 2020 class.
HOUSING_COLS = ["HU2010", "HU2020", "POP2020", "WUICLASS_2", "VEG2019PC", "Shape_Area"]

warnings.filterwarnings("ignore", message="Measured \\(M\\) geometry types are not supported")
warnings.filterwarnings("ignore", message="organizePolygons\\(\\) received a polygon with more than 100 parts")


def load_cells(path: Path) -> gpd.GeoDataFrame:
    cells = gpd.read_parquet(path, columns=["cell", "geometry"]).to_crs(CRS)
    cells["area_km2"] = cells.geometry.area / 1e6
    return cells


def read_housing(path: Path, chunk: int = 100_000) -> gpd.GeoDataFrame:
    """Read census blocks in chunks and keep one interior point per block (keeps memory low)."""
    n = pyogrio.read_info(path)["features"]
    parts = []
    for start in range(0, n, chunk):
        g = pyogrio.read_dataframe(path, columns=HOUSING_COLS, skip_features=start, max_features=chunk)
        g["geometry"] = g.geometry.representative_point()
        parts.append(g.to_crs(CRS))
        log.info("  housing blocks %d / %d", min(start + chunk, n), n)
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS)


def housing_features(cells: gpd.GeoDataFrame, blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    b = gpd.sjoin(blocks, cells[["cell", "geometry"]], predicate="within", how="inner")
    wui = b["WUICLASS_2"].astype(str)
    b["area_intermix"] = np.where(wui.str.contains("Intermix", case=False), b["Shape_Area"], 0.0)
    b["area_interface"] = np.where(wui.str.contains("Interface", case=False), b["Shape_Area"], 0.0)
    b["veg_x_area"] = b["VEG2019PC"].clip(0, 100) * b["Shape_Area"]
    agg = b.groupby("cell").agg(
        hu_2020=("HU2020", "sum"), hu_2010=("HU2010", "sum"), pop_2020=("POP2020", "sum"),
        area_intermix=("area_intermix", "sum"), area_interface=("area_interface", "sum"),
        veg_x_area=("veg_x_area", "sum"), block_area=("Shape_Area", "sum"),
    )
    out = cells[["cell", "area_km2"]].set_index("cell").join(agg).fillna(0)
    km2 = out["area_km2"]
    res = pd.DataFrame(index=out.index)
    res["hu_2020"] = out["hu_2020"]
    res["hu_den_2020"] = out["hu_2020"] / km2
    res["hu_den_2010"] = out["hu_2010"] / km2
    res["hu_growth_2010_2020"] = np.log1p(out["hu_2020"]) - np.log1p(out["hu_2010"])
    res["pop_den_2020"] = out["pop_2020"] / km2
    res["wui_intermix_share"] = (out["area_intermix"] / (km2 * 1e6)).clip(0, 1)
    res["wui_interface_share"] = (out["area_interface"] / (km2 * 1e6)).clip(0, 1)
    res["veg_pct_2019"] = np.where(out["block_area"] > 0, out["veg_x_area"] / out["block_area"].where(out["block_area"] > 0, 1), np.nan)
    return res


def conservation_features(cells: gpd.GeoDataFrame, cons: gpd.GeoDataFrame) -> pd.DataFrame:
    cons = cons.to_crs(CRS)
    cons = cons[cons.geometry.notna() & ~cons.geometry.is_empty].copy()
    cons["geometry"] = cons.geometry.make_valid()
    # Clip conservation land to each hexagon, then merge the pieces within each hexagon so
    # overlapping areas (e.g. an easement inside a forest) are counted once.
    inter = gpd.overlay(cells[["cell", "geometry"]], cons[["geometry"]], how="intersection", keep_geom_type=True)
    covered = inter.dissolve(by="cell").geometry.area
    share = covered / cells.set_index("cell")["area_km2"].mul(1e6)

    centres = gpd.GeoDataFrame(cells[["cell"]], geometry=cells.geometry.centroid, crs=CRS)
    near = gpd.sjoin_nearest(centres, cons[["geometry"]], distance_col="d").groupby("cell")["d"].min()
    big = cons[cons["TOTACRES"] >= BIG_CONS_ACRES][["geometry"]]
    near_big = gpd.sjoin_nearest(centres, big, distance_col="d").groupby("cell")["d"].min()

    res = pd.DataFrame(index=cells["cell"])
    res["cons_share"] = share.reindex(res.index).fillna(0).clip(0, 1)
    res["dist_cons_km"] = near.reindex(res.index) / 1000
    res["dist_bigcons_km"] = near_big.reindex(res.index) / 1000
    return res


def road_features(cells: gpd.GeoDataFrame, routes: gpd.GeoDataFrame, aadt: gpd.GeoDataFrame) -> pd.DataFrame:
    hexes = cells[["cell", "geometry"]]
    r = gpd.overlay(routes.to_crs(CRS)[["geometry"]], hexes, how="intersection", keep_geom_type=True)
    road_km = r.assign(km=r.geometry.length / 1000).groupby("cell")["km"].sum()

    a = gpd.overlay(aadt.to_crs(CRS)[["AADT", "geometry"]], hexes, how="intersection", keep_geom_type=True)
    a["vkm"] = a["AADT"] * a.geometry.length / 1000
    vkm = a.groupby("cell")["vkm"].sum()
    max_aadt = a.groupby("cell")["AADT"].max()

    centres = gpd.GeoDataFrame(cells[["cell"]], geometry=cells.geometry.centroid, crs=CRS)
    major = aadt.to_crs(CRS)
    major = major[major["AADT"] >= MAJOR_ROAD_AADT][["geometry"]]
    near_major = gpd.sjoin_nearest(centres, major, distance_col="d").groupby("cell")["d"].min()

    area = cells.set_index("cell")["area_km2"]
    res = pd.DataFrame(index=cells["cell"])
    res["road_km_per_km2"] = (road_km.reindex(res.index).fillna(0) / area).values
    res["traffic_vkm_per_km2"] = (vkm.reindex(res.index).fillna(0) / area).values
    res["max_aadt"] = max_aadt.reindex(res.index).fillna(0).values
    res["dist_major_road_km"] = (near_major.reindex(res.index) / 1000).values
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    tag = f"h3r{args.res}"
    cells = load_cells(args.dir / f"cells_{tag}.parquet")
    log.info("%d cells", len(cells))

    log.info("Housing (SILVIS blocks)...")
    house = housing_features(cells, read_housing(HOUSING))
    log.info("Conservation lands (FNAI)...")
    cons = conservation_features(cells, pyogrio.read_dataframe(CONSERVATION, layer="flma", columns=["MANAME", "TOTACRES"]))
    log.info("Roads and traffic (FDOT)...")
    roads = road_features(cells, pyogrio.read_dataframe(ROUTES, columns=["ROADWAY"]),
                          pyogrio.read_dataframe(AADT, columns=["AADT"]))

    out = house.join(cons).join(roads).reset_index().rename(columns={"index": "cell"})
    feat_cols = [c for c in out.columns if c != "cell"]
    out[feat_cols] = out[feat_cols].astype("float32")
    path = args.dir / f"landscape_{tag}.parquet"
    out.to_parquet(path, index=False)

    desc = out[feat_cols].describe(percentiles=[0.5, 0.9]).T[["mean", "50%", "90%", "max"]].round(3)
    report = {"cells": int(len(out)), "features": feat_cols,
              "nan_counts": {c: int(out[c].isna().sum()) for c in feat_cols if out[c].isna().any()},
              "summary": desc.to_dict(orient="index")}
    path.with_name(f"landscape_{tag}_report.json").write_text(json.dumps(report, indent=2))
    log.info("Summary:\n%s", desc.to_string())
    log.info("Wrote %s (%d features)", path, len(feat_cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())