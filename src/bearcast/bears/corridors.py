"""Movement corridors between bear populations, and road-crossing risk.

1. Resistance: each hexagon's cost to move through, from the habitat map (habitat_pct, 0-1) with a
   negative-exponential transform (R = 1 in the best habitat, 100 in the worst; c = 4):
       R = 100 - 99 * (1 - exp(-c*h)) / (1 - exp(-c))
2. Cores: connected clusters of FWC "frequent" range with >= MIN_CORE_CELLS hexagons, named after
   the nearest known population.
3. Corridors (the Linkage Mapper idea): for core pairs that are near each other in cost terms, every
   hexagon gets   (costdist_A + costdist_B - best_path_cost) / best_path_cost   = how much costlier a
   route through it is than the best route (0 = on the best path). Each hexagon keeps its lowest
   value over pairs. Best paths are also saved as lines for the map.
4. Road-crossing risk: movement (1 inside cores, exp(-value / WIDTH) elsewhere) x traffic
   (log of the busiest road's AADT, scaled 0-1).
5. Check against roadkill (2010-2025, FWC carcasses + vehicle-strike captures): among hexagons with
   state roads, share of roadkill in the top 5/10/20% by crossing risk vs traffic alone vs habitat x traffic.

Outputs:
  data/processed/corridors_h3r7.parquet         per hexagon: resistance, core, corridor_value, crossing_risk
  data/processed/corridor_paths_h3r7.parquet    best-path lines between core pairs (GeoParquet, EPSG:4326)
  data/processed/corridors_h3r7_report.json

Usage:
    python -m bearcast.bears.corridors
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from itertools import combinations
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from scipy import sparse
from scipy.sparse.csgraph import connected_components, dijkstra
from shapely.geometry import LineString

from bearcast.features.history import ring_adjacency

log = logging.getLogger(__name__)
PROCESSED = Path("data/processed")
RAW = Path("data/raw")
C_TRANSFORM = 4.0
MIN_CORE_CELLS = 100
K_NEAREST = 2            # each core links to its 2 cheapest-to-reach neighbours
WIDTH = 0.05             # corridor band: routes up to 5% costlier than the best one
KNOWN_POPULATIONS = {    # rough centres, only used to name cores
    "Eglin": (30.6, -86.6), "Apalachicola": (30.2, -84.8), "Big Bend": (29.9, -83.6),
    "Osceola": (30.4, -82.4), "Ocala/St. Johns": (29.2, -81.7), "Chassahowitzka": (28.6, -82.5),
    "Highlands/Glades": (27.3, -81.3), "Big Cypress": (26.1, -81.3),
}


def resistance(h: np.ndarray, c: float = C_TRANSFORM) -> np.ndarray:
    h = np.clip(np.nan_to_num(h, nan=0.0), 0, 1)
    return 100 - 99 * (1 - np.exp(-c * h)) / (1 - np.exp(-c))


def cost_graph(cells: list[str], r: np.ndarray) -> sparse.csr_matrix:
    a = ring_adjacency(cells, 1).tocoo()
    w = (r[a.row] + r[a.col]) / 2
    return sparse.csr_matrix((w, (a.row, a.col)), shape=a.shape)


def find_cores(cells: list[str], is_core: np.ndarray, min_cells: int = MIN_CORE_CELLS) -> np.ndarray:
    """Label connected clusters of core cells; -1 = not a core (or too small)."""
    idx = np.flatnonzero(is_core)
    sub = [cells[i] for i in idx]
    n, lab = connected_components(ring_adjacency(sub, 1), directed=False)
    sizes = np.bincount(lab, minlength=n)
    keep = {old: new for new, old in enumerate(np.flatnonzero(sizes >= min_cells))}
    out = np.full(len(cells), -1)
    out[idx] = [keep.get(l, -1) for l in lab]
    return out


def name_cores(cells: list[str], core: np.ndarray) -> dict[int, str]:
    names: dict[int, str] = {}
    for k in sorted(set(core) - {-1}):
        pts = np.array([h3.cell_to_latlng(cells[i]) for i in np.flatnonzero(core == k)])
        lat, lon = pts.mean(axis=0)
        dist = {n: np.hypot(lat - a, (lon - b) * np.cos(np.radians(lat))) for n, (a, b) in KNOWN_POPULATIONS.items()}
        best = min(dist, key=dist.get)
        name = best if dist[best] < 0.8 else f"core {k}"          # ~80 km
        names[k] = name if name not in names.values() else f"{name} ({k})"
    return names


def corridors(cells: list[str], r: np.ndarray, core: np.ndarray, k_nearest: int = K_NEAREST):
    """Return (corridor_value, best pair index per cell, pairs, paths)."""
    g = cost_graph(cells, r)
    ids = sorted(set(core) - {-1})
    dist, pred = {}, {}
    for k in ids:
        dist[k], pred[k], _ = dijkstra(g, directed=False, indices=np.flatnonzero(core == k),
                                       min_only=True, return_predecessors=True)
    lcp = {(a, b): float(np.min(dist[a][core == b])) for a, b in combinations(ids, 2)}
    pairs = set()
    for k in ids:
        near = sorted((v, p) for p, v in lcp.items() if k in p and np.isfinite(v))[:k_nearest]
        pairs.update(p for _, p in near)
    pairs = sorted(pairs)

    value = np.full(len(cells), np.inf)
    which = np.full(len(cells), -1)
    paths = []
    for i, (a, b) in enumerate(pairs):
        total = dist[a] + dist[b]
        best = lcp[(a, b)]
        v = (total - best) / best
        better = v < value
        value[better], which[better] = v[better], i
        m = int(np.argmin(total))                            # a cell on the best path
        path = walk(pred[a], m)[::-1] + walk(pred[b], m)[1:]
        paths.append({"pair": i, "a": a, "b": b, "cost": best, "cells": [cells[j] for j in path]})
    return value, which, pairs, paths


def walk(pred: np.ndarray, start: int) -> list[int]:
    out = [start]
    while pred[out[-1]] >= 0:
        out.append(int(pred[out[-1]]))
    return out


def capture(score: np.ndarray, y: np.ndarray, k: float, seed: int = 0) -> float:
    order = np.lexsort((np.random.default_rng(seed).random(len(score)), -score))
    n = max(1, int(np.ceil(k * len(score))))
    return float(y[order[:n]].sum() / y.sum())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    p.add_argument("--habitat", default="habitat_h3r7.parquet")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)
    tag = f"h3r{args.res}"

    cells_tbl = pd.read_parquet(args.dir / f"cells_{tag}.parquet", columns=["cell", "range_rank"])
    hab = pd.read_parquet(args.dir / args.habitat)
    land = pd.read_parquet(args.dir / f"landscape_{tag}.parquet", columns=["cell", "max_aadt"])
    t = cells_tbl.merge(hab, on="cell", how="left").merge(land, on="cell", how="left")
    cells = t["cell"].astype(str).tolist()

    t["resistance"] = resistance(t["habitat_pct"].to_numpy())
    core = find_cores(cells, t["range_rank"].eq(4).to_numpy())
    names = name_cores(cells, core)
    t["core"] = pd.Series(core).map(names).fillna("")
    log.info("Cores (>= %d hexagons of FWC frequent range):\n%s", MIN_CORE_CELLS,
             t.loc[t["core"] != "", "core"].value_counts().to_string())

    value, which, pairs, paths = corridors(cells, t["resistance"].to_numpy(), core)
    pair_names = [f"{names[a]} - {names[b]}" for a, b in pairs]
    t["corridor_value"] = np.where(core >= 0, 0.0, value).astype(np.float32)
    t["corridor_pair"] = [pair_names[w] if w >= 0 else "" for w in which]
    t["in_corridor"] = (core < 0) & (value <= WIDTH)
    movement = np.where(core >= 0, 1.0, np.exp(-np.nan_to_num(value, posinf=1e9) / WIDTH))
    aadt = t["max_aadt"].fillna(0).to_numpy()
    traffic = np.log1p(aadt) / np.log1p(aadt.max())
    t["movement"] = movement.astype(np.float32)
    t["crossing_risk"] = (movement * traffic).astype(np.float32)

    lines = []
    for pth, nm in zip(paths, pair_names):
        pts = [h3.cell_to_latlng(c)[::-1] for c in pth["cells"]]
        km = sum(h3.great_circle_distance(h3.cell_to_latlng(x), h3.cell_to_latlng(y), unit="km")
                 for x, y in zip(pth["cells"][:-1], pth["cells"][1:]))
        crossings = int(t.set_index("cell").loc[pth["cells"], "max_aadt"].fillna(0).ge(10_000).sum())
        lines.append({"pair": nm, "cost": pth["cost"], "length_km": round(km, 1), "busy_road_hexagons": crossings,
                      "geometry": LineString(pts) if len(pts) > 1 else None})
    cols = ["pair", "cost", "length_km", "busy_road_hexagons", "geometry"]
    paths_gdf = gpd.GeoDataFrame(pd.DataFrame(lines, columns=cols), geometry="geometry", crs=4326)
    if len(names) < 2:
        log.warning("Fewer than 2 cores found: no corridors to draw (check range_rank / MIN_CORE_CELLS)")
    log.info("Best paths between cores:\n%s", paths_gdf.drop(columns="geometry").round(1).to_string(index=False))

    # Roadkill check
    rk = pd.read_parquet(RAW / "fwc_roadkill.parquet", columns=["SubType", "RoadDate", "Latitude", "Longitude"])
    rd = pd.to_datetime(rk["RoadDate"])
    rd = rd.dt.tz_localize(None) if rd.dt.tz is not None else rd
    rk = rk[rk["SubType"].isin(["Carcass", "Capture"]) & rd.dt.year.between(2010, 2025) & rk["Latitude"].ne(0)]
    rk_cell = pd.Series([h3.latlng_to_cell(a, b, args.res) for a, b in zip(rk["Latitude"], rk["Longitude"])])
    counts = rk_cell.value_counts()
    t["roadkill_2010_2025"] = t["cell"].map(counts).fillna(0).astype(int)
    on_road = t["max_aadt"].fillna(0) > 0
    in_univ = int(rk_cell.isin(set(cells)).sum())
    log.info("Roadkill 2010-2025: %d records, %d in the hexagon universe, %d in hexagons with state-road traffic data",
             len(rk_cell), in_univ, int(t.loc[on_road, "roadkill_2010_2025"].sum()))
    r = t[on_road]
    y = r["roadkill_2010_2025"].to_numpy(dtype=float)
    scores = {"traffic only": np.log1p(r["max_aadt"].to_numpy()),
              "habitat x traffic": r["habitat_pct"].fillna(0).to_numpy() * np.log1p(r["max_aadt"].to_numpy()),
              "crossing risk (corridors)": r["crossing_risk"].to_numpy()}
    check = pd.DataFrame({name: {f"top {k:.0%}": capture(s, y, k) for k in (0.05, 0.10, 0.20)}
                          for name, s in scores.items()}).T
    log.info("Share of roadkill in the top k%% of road hexagons (%d hexagons):\n%s", len(r), check.round(3).to_string())

    out_cols = ["cell", "habitat_pct", "resistance", "core", "corridor_value", "corridor_pair", "in_corridor",
                "movement", "crossing_risk", "roadkill_2010_2025"]
    t[out_cols].to_parquet(args.dir / f"corridors_{tag}.parquet", index=False)
    paths_gdf.to_parquet(args.dir / f"corridor_paths_{tag}.parquet", index=False)
    report = {"cores": t.loc[t["core"] != "", "core"].value_counts().to_dict(),
              "pairs": pair_names, "paths": paths_gdf.drop(columns="geometry").round(2).to_dict(orient="records"),
              "corridor_hexagons": int(t["in_corridor"].sum()),
              "roadkill_check": check.round(4).to_dict(orient="index"),
              "params": {"c": C_TRANSFORM, "min_core_cells": MIN_CORE_CELLS, "k_nearest": K_NEAREST, "width": WIDTH}}
    (args.dir / f"corridors_{tag}_report.json").write_text(json.dumps(report, indent=2, default=str))
    log.info("Corridor hexagons (within %.0f%% of best path, outside cores): %d", WIDTH * 100, report["corridor_hexagons"])
    log.info("Wrote %s and %s", args.dir / f"corridors_{tag}.parquet", args.dir / f"corridor_paths_{tag}.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())