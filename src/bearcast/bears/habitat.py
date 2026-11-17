"""Where bears are likely to be: a habitat-use model from tracked bears (a resource selection function).

Compares hexagons tracked bears USED with hexagons AVAILABLE to them (everything within ~10 km of
their study area's locations), and learns which landscapes bears favour. Then scores every hexagon.

Data
  telemetry_combined      radio tracking 1983-2007, 11 studies (Nuisance Relocation excluded: people moved those bears)
  telemetry_camp_blanding GPS 2011-2014, 16 bears, thinned to match
  Cleaning: drop fix types that aren't free habitat choice (captures, dens, mortality, dropped collars, ...),
  keep one random fix per bear per day, weight every bear equally.

Features: landscape + land cover (no history, no lat/lon, so the model can transfer to new areas).
Housing density is matched to the decade of each fix (SILVIS 1990/2000/2010/2020); today's map uses 2020.

Honest test: leave-one-area-out. Each of 8 area groups is predicted by a model trained on the other 7.
  AUC        chance a used hexagon scores above an available one (0.5 = coin flip)
  Boyce      do higher scores really get more use? Spearman correlation of score bins with used/available
             ratio (1 = perfectly ordered, 0 = no relationship)
  top-bin    how many times more use than expected the top 10% of available hexagons got
The FWC range map rank is scored the same way for comparison.

Outputs:
  data/processed/habitat_h3r7.parquet       cell, habitat_score, habitat_pct (0-1, 1 = best)
  data/processed/habitat_h3r7_report.json
  data/models/habitat_h3r7.txt

Usage:
    python -m bearcast.bears.habitat
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import h3
import lightgbm as lgb
import numpy as np
import pandas as pd

from bearcast.features.landscape import HOUSING, load_cells, read_housing

log = logging.getLogger(__name__)
PROCESSED = Path("data/processed")
RAW = Path("data/raw")
MODELS = Path("data/models")

AREA_GROUP = {
    "Ocala": "ocala", "Ocala SR40": "ocala", "Urban-wildland Interface": "ocala",
    "Osceola": "osceola", "UT-UF OKNWR-Osceola": "osceola",
    "Wekiva": "wekiva", "S. FL": "south_fl", "UT-UF Eglin": "eglin", "Apalachicola": "apalachicola",
    "UK Chassahowitzka": "gulf_coast", "Cedar Key": "gulf_coast",
}  # "Nuisance Relocation" deliberately absent
DROP_LOC = {"capture", "den", "den creep", "dropped collar", "nuisance", "removed", "injured - hit by vehicle"}
AVAIL_RINGS = 8                      # ~10 km around used hexagons at res 7
ERA_HOUSING = ["hu_den_1990", "hu_den_2000", "hu_den_2010", "hu_den_2020"]
DROP_FEATURES = ["hu_2020", "hu_den_2020", "hu_den_2010", "hu_growth_2010_2020", "pop_den_2020"]  # replaced by era housing
PARAMS = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 50,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
          "verbose": -1, "seed": 42}
ROUNDS = 300


def era_column(year: pd.Series) -> pd.Series:
    """Housing decade closest to the fix year."""
    return pd.cut(year, [-np.inf, 1994, 2004, 2014, np.inf], labels=ERA_HOUSING).astype(str)


def clean_radio(t: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    loc = t["Loc_Type"].fillna("").astype(str).str.strip().str.lower()
    keep = t["Source"].isin(AREA_GROUP) & ~loc.isin(DROP_LOC) & ~loc.str.startswith("mortality")
    keep &= t["Latitude"].fillna(0).ne(0) & t["Longitude"].fillna(0).ne(0)
    d = t.loc[keep].copy()
    d["date"] = pd.to_datetime(d["Date"]).dt.tz_localize(None) if pd.to_datetime(d["Date"]).dt.tz is not None \
        else pd.to_datetime(d["Date"])
    d = d.dropna(subset=["date"])
    out = pd.DataFrame({"bear": "r_" + d["Bear_ID"].astype(str), "group": d["Source"].map(AREA_GROUP),
                        "date": d["date"].dt.normalize(), "lat": d["Latitude"], "lon": d["Longitude"]})
    return thin_daily(out, seed)


def clean_gps(c: pd.DataFrame, max_dop: float = 10, seed: int = 0) -> pd.DataFrame:
    c = c[(c["DOP"] <= max_dop) & c["LATITUDE"].ne(0)]
    date = pd.to_datetime(c["GMT_DATE"])
    date = date.dt.tz_localize(None) if date.dt.tz is not None else date
    out = pd.DataFrame({"bear": "g_" + c["Bear"].astype(str), "group": "camp_blanding",
                        "date": date.dt.normalize(), "lat": c["LATITUDE"], "lon": c["LONGITUDE"]})
    return thin_daily(out, seed)


def thin_daily(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """One random fix per bear per day (random, so no time-of-day bias)."""
    return (df.sample(frac=1, random_state=seed).drop_duplicates(["bear", "date"])
              .sort_values(["bear", "date"]).reset_index(drop=True))


def build_design(fixes: pd.DataFrame, universe: set[str], res: int = 7, rings: int = AVAIL_RINGS) -> pd.DataFrame:
    """Used rows (one per fix, weight 1/fixes-per-bear) + available rows (all cells near each group's fixes)."""
    f = fixes.copy()
    f["cell"] = [h3.latlng_to_cell(a, b, res) for a, b in zip(f["lat"], f["lon"])]
    n0 = f.groupby("group").size()
    f = f[f["cell"].isin(universe)]
    lost = (1 - f.groupby("group").size().reindex(n0.index).fillna(0) / n0).round(3)
    if (lost > 0).any():
        log.info("share of fixes outside the hexagon universe (dropped), by group: %s", lost[lost > 0].to_dict())
    f["w"] = 1.0 / f.groupby("bear")["cell"].transform("size")
    f["era"] = era_column(f["date"].dt.year)
    used = f[["group", "bear", "cell", "w", "era"]].assign(used=1)

    avail = []
    for g, gf in f.groupby("group"):
        area: set[str] = set()
        for c in gf["cell"].unique():
            area.update(h3.grid_disk(c, rings))
        area &= universe
        n_bears = gf["bear"].nunique()
        era = era_column(pd.Series([int(gf["date"].dt.year.median())])).iloc[0]
        avail.append(pd.DataFrame({"group": g, "bear": "", "cell": sorted(area),
                                   "w": n_bears / len(area), "era": era, "used": 0}))
    return pd.concat([used, *avail], ignore_index=True)


def add_features(design: pd.DataFrame, static: pd.DataFrame, era_housing: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    feats = [c for c in static.columns if c != "cell" and c not in DROP_FEATURES]
    d = design.merge(static[["cell", *feats]], on="cell", how="left").merge(era_housing, on="cell", how="left")
    d["hu_den_era"] = d[ERA_HOUSING].to_numpy()[np.arange(len(d)), d["era"].map(ERA_HOUSING.index).to_numpy()]
    return d.drop(columns=ERA_HOUSING), [*feats, "hu_den_era"]


def weighted_auc(score: np.ndarray, used: np.ndarray, w: np.ndarray) -> float:
    """P(score of a used row > score of an available row), weighted, ties count half."""
    s_av, w_av = score[used == 0], w[used == 0]
    order = np.argsort(s_av)
    s_av, cw = s_av[order], np.concatenate([[0], np.cumsum(w_av[order])])
    s_u, w_u = score[used == 1], w[used == 1]
    lo, hi = np.searchsorted(s_av, s_u, "left"), np.searchsorted(s_av, s_u, "right")
    below = cw[lo] + 0.5 * (cw[hi] - cw[lo])
    return float(np.sum(w_u * below) / (np.sum(w_u) * cw[-1]))


def boyce(score: np.ndarray, used: np.ndarray, w: np.ndarray, bins: int = 10) -> tuple[float, float]:
    """Spearman(bin, used/available ratio) over score bins cut at available-score quantiles; and top-bin ratio."""
    edges = np.unique(np.quantile(score[used == 0], np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return float("nan"), float("nan")
    b = np.clip(np.searchsorted(edges, score, "right") - 1, 0, len(edges) - 2)
    u = np.bincount(b[used == 1], weights=w[used == 1], minlength=len(edges) - 1)
    a = np.bincount(b[used == 0], weights=w[used == 0], minlength=len(edges) - 1)
    ok = a > 0
    pe = (u[ok] / u.sum()) / (a[ok] / a.sum())
    rho = pd.Series(pe).corr(pd.Series(np.arange(len(pe))), method="spearman")
    return float(rho), float(pe[-1])


def era_housing_table(cells: gpd.GeoDataFrame) -> pd.DataFrame:
    """Housing units per km2 in 1990/2000/2010/2020 for every cell (SILVIS blocks, interior points)."""
    cols = ["HU1990", "HU2000", "HU2010", "HU2020"]
    blocks = read_housing(HOUSING, columns=cols)
    missing = [c for c in cols if c not in blocks.columns]
    if missing:
        raise KeyError(f"SILVIS file is missing housing columns {missing}")
    j = gpd.sjoin(blocks, cells[["cell", "geometry"]], predicate="within", how="inner")
    s = j.groupby("cell")[["HU1990", "HU2000", "HU2010", "HU2020"]].sum()
    area = cells.set_index("cell")["area_km2"]
    out = s.reindex(area.index).fillna(0).div(area, axis=0)
    out.columns = ERA_HOUSING
    return out.reset_index()


def fit(d: pd.DataFrame, feats: list[str]) -> lgb.Booster:
    return lgb.train(PARAMS, lgb.Dataset(d[feats].to_numpy(np.float32), d["used"], weight=d["w"], feature_name=feats),
                     num_boost_round=ROUNDS)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)
    tag = f"h3r{args.res}"

    radio = clean_radio(pd.read_parquet(RAW / "fwc_telemetry_combined.parquet",
                                        columns=["Source", "Bear_ID", "Date", "Loc_Type", "Latitude", "Longitude"]))
    gps = clean_gps(pd.read_parquet(RAW / "fwc_telemetry_camp_blanding.parquet",
                                    columns=["Bear", "GMT_DATE", "LATITUDE", "LONGITUDE", "DOP"]))
    fixes = pd.concat([radio, gps], ignore_index=True)
    log.info("Fixes after cleaning and daily thinning: %d from %d bears\n%s", len(fixes), fixes["bear"].nunique(),
             fixes.groupby("group").agg(bears=("bear", "nunique"), fixes=("bear", "size"),
                                        first=("date", "min"), last=("date", "max")).to_string())

    cells = load_cells(args.dir / f"cells_{tag}.parquet")
    static = pd.read_parquet(args.dir / f"landscape_{tag}.parquet").merge(
        pd.read_parquet(args.dir / f"landcover_{tag}.parquet"), on="cell", how="outer")
    rank = pd.read_parquet(args.dir / f"cells_{tag}.parquet", columns=["cell", "range_rank"])
    log.info("Housing by decade (SILVIS)...")
    era = era_housing_table(cells)

    design = build_design(fixes, set(cells["cell"]), args.res)
    d, feats = add_features(design, static, era)
    d = d.merge(rank, on="cell", how="left")
    log.info("Design: %d used rows, %d available rows, %d features", int(d["used"].sum()), int((d["used"] == 0).sum()), len(feats))

    rows = []
    for g in sorted(d["group"].unique()):
        train, test = d[d["group"] != g], d[d["group"] == g]
        booster = fit(train, feats)
        sc = booster.predict(test[feats].to_numpy(np.float32))
        u, w = test["used"].to_numpy(), test["w"].to_numpy()
        rr = test["range_rank"].fillna(0).to_numpy(dtype=float)
        b_m, top_m = boyce(sc, u, w)
        jitter = np.random.default_rng(0).random(len(rr)) * 1e-6
        b_r, top_r = boyce(rr + jitter, u, w)              # random jitter only splits the range map's ties
        rows.append({"held_out": g, "bears": int(test.loc[test["used"] == 1, "bear"].nunique()),
                     "auc_model": weighted_auc(sc, u, w), "auc_range_map": weighted_auc(rr, u, w),
                     "boyce_model": b_m, "boyce_range_map": b_r, "topbin_model": top_m, "topbin_range_map": top_r})
    cv = pd.DataFrame(rows).set_index("held_out")
    log.info("Leave-one-area-out (each row: model trained without that area):\n%s\nmean:\n%s",
             cv.round(3).to_string(), cv.drop(columns="bears").mean().round(3).to_string())

    final = fit(d, feats)
    MODELS.mkdir(parents=True, exist_ok=True)
    final.save_model(str(MODELS / f"habitat_{tag}.txt"))
    today = static[["cell", *[f for f in feats if f != "hu_den_era"]]].merge(era[["cell", "hu_den_2020"]], on="cell", how="left")
    today = today.rename(columns={"hu_den_2020": "hu_den_era"})
    score = final.predict(today[feats].to_numpy(np.float32))
    out = pd.DataFrame({"cell": today["cell"], "habitat_score": score.astype(np.float32)})
    out["habitat_pct"] = out["habitat_score"].rank(pct=True).astype(np.float32)
    out.to_parquet(args.dir / f"habitat_{tag}.parquet", index=False)

    by_range = out.merge(rank, on="cell").groupby("range_rank")["habitat_pct"].agg(["mean", "size"]).round(3)
    gain = pd.Series(final.feature_importance("gain"), index=final.feature_name())
    top = (gain / gain.sum()).sort_values(ascending=False).head(15).round(3)
    log.info("Statewide habitat percentile by FWC range class (0 outside, 1 rare ... 4 frequent):\n%s", by_range.to_string())
    log.info("Top features:\n%s", top.to_string())
    report = {"fixes": int(len(fixes)), "bears": int(fixes["bear"].nunique()), "features": feats,
              "leave_one_area_out": cv.round(4).reset_index().to_dict(orient="records"),
              "mean": cv.drop(columns="bears").mean().round(4).to_dict(),
              "habitat_pct_by_range_rank": by_range.reset_index().to_dict(orient="records"),
              "top_features": top.to_dict()}
    (args.dir / f"habitat_{tag}_report.json").write_text(json.dumps(report, indent=2, default=str))
    log.info("Wrote %s", args.dir / f"habitat_{tag}.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())