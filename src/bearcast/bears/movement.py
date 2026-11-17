"""Simulated bear movement from GPS tracks (an integrated step-selection model).

Data: Camp Blanding GPS (16 bears, fixes every ~30 min), regularised to 1-hour steps.

1. Movement pools: observed (step length, turning angle) pairs by time of day (night / dawn / day / dusk,
   Eastern time) and season (fall feeding season Sep-Dec vs rest of year).
2. Step selection: for every observed step, draw 10 alternative steps from the same pool and compare the
   real endpoint with the alternatives (conditional logistic regression). Covariates at the endpoint, on a
   100 m grid: land-cover class, distance to any road, distance to a busy road (AADT >= 10,000), whether
   the step crosses a busy road, plus log step length and cos(turn) (standard iSSF movement terms).
3. Simulation: a bear repeatedly draws 20 candidate steps from the pool for its hour/season and picks
   one with probability proportional to exp(covariates x coefficients).
4. Fair test: fit on 12 bears; for the 4 held-out bears, simulate 14-day tracks from the same start
   points and times and compare with what the real bears did (24 h displacement, 14-day spread,
   busy-road crossings per day, time in natural cover and developed land). A "null" walker that uses
   the same step pool but no habitat preference shows how much the selection model adds.
5. App output: 25 simulated bears x 14 days from each population core and from the 10 hexagons with
   the most recent bear reports; plus, per start, the share of simulated bears reaching each hexagon.

Outputs:
  data/interim/movement_grids_100m.npz           cached covariate grids
  data/models/movement_ssf.json, movement_pools.npz
  data/processed/movement_sims_h3r7.parquet      simulated hourly positions (start, sim, hour, lon, lat)
  data/processed/movement_reach_h3r7.parquet     start, cell, share of sims reaching it within 7 / 14 days
  data/processed/movement_report.json

Usage:
    python -m bearcast.bears.movement
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.optimize import minimize
from scipy.special import logsumexp

log = logging.getLogger(__name__)
PROCESSED, INTERIM, RAW, MODELS = Path("data/processed"), Path("data/interim"), Path("data/raw"), Path("data/models")
CRS = 3087
CELL = 100.0                                   # covariate grid size (m)
CLASSES = ["forest_upland", "plantation", "forested_wetland", "open_wetland", "shrub", "agriculture", "developed", "water"]
GROUP_TO_CLASS = {"hardwood_upland": 0, "pine_scrub_upland": 0, "tree_plantation": 1, "forested_wetland": 2,
                  "open_wetland": 3, "shrub_coastal_barren": 4, "agriculture": 5, "urban_high": 6, "urban_low": 6,
                  "rural_developed": 6, "transport_extractive": 6, "water": 7}
OTHER, OUTSIDE = 8, 255
MODEL_CLASSES = CLASSES[1:]                    # upland forest (and the rare 'other') is the reference
FEATURES = [*MODEL_CLASSES, "log_dist_road", "log_dist_busy_road", "crosses_busy_road", "log_step", "cos_turn"]
CONTINUOUS = ["log_dist_road", "log_dist_busy_road", "log_step"]
PERIODS = ["night", "dawn", "day", "dusk"]
N_CONTROL, N_CANDIDATES = 10, 20
TO_LL = Transformer.from_crs(CRS, 4326, always_xy=True)
TO_XY = Transformer.from_crs(4326, CRS, always_xy=True)


# ---------- data ----------
def load_gps(df: pd.DataFrame, max_dop: float = 10) -> pd.DataFrame:
    d = df[(df["DOP"] <= max_dop) & df["LATITUDE"].ne(0)].copy()
    day = pd.to_datetime(d["GMT_DATE"])
    day = day.dt.tz_convert("UTC") if day.dt.tz is not None else day.dt.tz_localize("UTC")
    t = day.dt.normalize() + pd.to_timedelta(d["GMT_TIME"].astype(str))
    x, y = TO_XY.transform(d["LONGITUDE"].to_numpy(), d["LATITUDE"].to_numpy())
    out = pd.DataFrame({"bear": d["Bear"].astype(str).to_numpy(), "t": t.to_numpy(), "x": x, "y": y})
    out["t"] = pd.to_datetime(out["t"], utc=True)
    return out.sort_values(["bear", "t"]).drop_duplicates(["bear", "t"]).reset_index(drop=True)


def period_season(t_utc: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    local = pd.to_datetime(t_utc).dt.tz_convert("America/New_York")
    h = local.dt.hour.to_numpy()
    period = np.select([(h >= 21) | (h < 5), h < 9, h < 17], [0, 1, 2], 3)
    fall = local.dt.month.isin([9, 10, 11, 12]).to_numpy().astype(int)
    return period, fall


def hourly_steps(gps: pd.DataFrame, tol_min: int = 10) -> pd.DataFrame:
    """Snap fixes to whole hours (within tol), keep consecutive 1-hour pairs as steps, with turning angles."""
    g = gps.copy()
    g["th"] = g["t"].dt.round("h")
    g = g[(g["t"] - g["th"]).abs() <= pd.Timedelta(minutes=tol_min)]
    g = g.drop_duplicates(["bear", "th"]).sort_values(["bear", "th"]).reset_index(drop=True)
    nxt = g.groupby("bear").shift(-1)
    ok = (nxt["th"] - g["th"]) == pd.Timedelta(hours=1)
    s = pd.DataFrame({"bear": g["bear"], "t": g["th"], "x0": g["x"], "y0": g["y"], "x1": nxt["x"], "y1": nxt["y"]})[ok]
    s["sl"] = np.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"])
    s["heading"] = np.arctan2(s["y1"] - s["y0"], s["x1"] - s["x0"])
    prev = s.groupby("bear").shift(1)
    contiguous = (s["t"] - prev["t"]) == pd.Timedelta(hours=1)
    turn = s["heading"] - prev["heading"]
    s["turn"] = np.where(contiguous & (prev["sl"] > 0) & (s["sl"] > 0), np.angle(np.exp(1j * turn)), np.nan)
    s["period"], s["fall"] = period_season(s["t"])
    return s.reset_index(drop=True)


def build_pools(steps: pd.DataFrame) -> dict[int, np.ndarray]:
    """(step length, turn) pairs per period x season key (key = period + 4 * fall)."""
    st = steps.dropna(subset=["turn"])
    return {int(k): g[["sl", "turn"]].to_numpy() for k, g in st.groupby(st["period"] + 4 * st["fall"])}


# ---------- covariate grids ----------
@dataclass
class Grid:
    cls: np.ndarray          # uint8 class id (0-7 CLASSES, 8 other, 255 outside)
    d_road: np.ndarray       # uint16 metres to any road
    d_busy: np.ndarray       # uint16 metres to busy road
    busy: np.ndarray         # uint8 1 = within ~100 m of a busy road (crossing band)
    x0: float
    y0: float                # top-left corner (y0 = max y)
    cell: float = CELL

    def rc(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        c = np.floor((x - self.x0) / self.cell).astype(np.int64)
        r = np.floor((self.y0 - y) / self.cell).astype(np.int64)
        inside = (r >= 0) & (r < self.cls.shape[0]) & (c >= 0) & (c < self.cls.shape[1])
        return np.clip(r, 0, self.cls.shape[0] - 1), np.clip(c, 0, self.cls.shape[1] - 1), inside

    def features(self, x0, y0, x1, y1, sl, turn) -> tuple[np.ndarray, np.ndarray]:
        """Feature array (..., len(FEATURES)) for steps (x0,y0)->(x1,y1); second value = endpoint usable."""
        r, c, inside = self.rc(x1, y1)
        k = self.cls[r, c]
        valid = inside & (k != OUTSIDE)
        f = np.zeros((*np.shape(x1), len(FEATURES)), dtype=np.float32)
        for j, name in enumerate(MODEL_CLASSES):
            f[..., j] = k == CLASSES.index(name)
        col = FEATURES.index
        f[..., col("log_dist_road")] = np.log1p(self.d_road[r, c] / 100.0)
        f[..., col("log_dist_busy_road")] = np.log1p(self.d_busy[r, c] / 100.0)
        # crossing = starts off the busy-road band and touches it somewhere along the step (24 samples)
        frac = np.linspace(1 / 24, 1.0, 24)
        xs = x0[..., None] + (x1 - x0)[..., None] * frac
        ys = y0[..., None] + (y1 - y0)[..., None] * frac
        rr, cc, _ = self.rc(xs, ys)
        r0, c0, _ = self.rc(x0, y0)
        f[..., col("crosses_busy_road")] = (self.busy[r0, c0] == 0) & (self.busy[rr, cc].max(axis=-1) == 1)
        f[..., col("log_step")] = np.log1p(sl)
        f[..., col("cos_turn")] = np.cos(np.nan_to_num(turn))
        return f, valid

    def save(self, path: Path) -> None:
        np.savez_compressed(path, cls=self.cls, d_road=self.d_road, d_busy=self.d_busy, busy=self.busy,
                            origin=np.array([self.x0, self.y0, self.cell]))

    @classmethod
    def load(cls, path: Path) -> "Grid":
        z = np.load(path)
        x0, y0, cell = z["origin"]
        return cls(z["cls"], z["d_road"], z["d_busy"], z["busy"], float(x0), float(y0), float(cell))


def build_grid(tif: Path, routes: Path, aadt: Path, factor: int = 10) -> Grid:
    """100 m grids from the 10 m CLC raster (majority class) and FDOT road lines."""
    import pyogrio
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from scipy.ndimage import distance_transform_edt

    from bearcast.features.landcover import GROUP_NAMES, code_group

    with rasterio.open(tif) as src:
        h, w = src.height // factor, src.width // factor
        codes = src.read(1, out_shape=(h, w), resampling=Resampling.mode)
        tr = src.transform * src.transform.scale(src.width / w, src.height / h)
        nodata = src.nodata
    uniq, inv = np.unique(codes, return_inverse=True)
    lut = np.array([OUTSIDE if (u == nodata or u <= 0) else GROUP_TO_CLASS.get(GROUP_NAMES[code_group(u)], OTHER)
                    for u in uniq.tolist()], dtype=np.uint8)
    cls = lut[inv].reshape(codes.shape)
    del codes, inv
    roads = pyogrio.read_dataframe(routes, columns=[]).to_crs(CRS)
    a = pyogrio.read_dataframe(aadt, columns=["AADT"]).to_crs(CRS)
    busy_lines = a[a["AADT"] >= 10_000]
    road_mask = rasterize(((g, 1) for g in list(roads.geometry) + list(a.geometry) if g is not None),
                          out_shape=(h, w), transform=tr, fill=0, all_touched=True, dtype="uint8")
    busy = rasterize(((g, 1) for g in busy_lines.geometry if g is not None),
                     out_shape=(h, w), transform=tr, fill=0, all_touched=True, dtype="uint8")
    from scipy.ndimage import binary_dilation
    band = binary_dilation(busy, iterations=1).astype(np.uint8)       # ~300 m band so fast steps can't skip it
    d_road = np.minimum(distance_transform_edt(road_mask == 0) * tr.a, 65_000).astype(np.uint16)
    d_busy = np.minimum(distance_transform_edt(busy == 0) * tr.a, 65_000).astype(np.uint16)
    return Grid(cls, d_road, d_busy, band, tr.c, tr.f, tr.a)


# ---------- step-selection fit ----------
def sample_pool(pools: dict[int, np.ndarray], keys: np.ndarray, k: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    sl = np.empty((len(keys), k)); turn = np.empty((len(keys), k))
    for key in np.unique(keys):
        m = keys == key
        pool = pools[int(key)]
        idx = rng.integers(0, len(pool), size=(m.sum(), k))
        sl[m], turn[m] = pool[idx, 0], pool[idx, 1]
    return sl, turn


def design(steps: pd.DataFrame, pools: dict, grid: Grid, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """(n, 1 + N_CONTROL, p) features with the observed step in column 0, plus validity mask."""
    st = steps.dropna(subset=["turn"])
    keys = (st["period"] + 4 * st["fall"]).to_numpy()
    sl_c, turn_c = sample_pool(pools, keys, N_CONTROL, rng)
    prev_heading = (st["heading"] - st["turn"]).to_numpy()[:, None]
    sl = np.c_[st["sl"].to_numpy(), sl_c]
    turn = np.c_[st["turn"].to_numpy(), turn_c]
    x0 = np.repeat(st["x0"].to_numpy()[:, None], 1 + N_CONTROL, axis=1)
    y0 = np.repeat(st["y0"].to_numpy()[:, None], 1 + N_CONTROL, axis=1)
    hd = prev_heading + turn
    x1, y1 = x0 + sl * np.cos(hd), y0 + sl * np.sin(hd)
    x1[:, 0], y1[:, 0] = st["x1"].to_numpy(), st["y1"].to_numpy()
    return grid.features(x0, y0, x1, y1, sl, turn)


@dataclass
class Scaler:
    mean: np.ndarray
    std: np.ndarray

    def __call__(self, X: np.ndarray) -> np.ndarray:
        X = X.copy()
        idx = [FEATURES.index(c) for c in CONTINUOUS]
        X[..., idx] = (X[..., idx] - self.mean) / self.std
        return X


def fit_clogit(X: np.ndarray, valid: np.ndarray, l2: float = 1e-3) -> tuple[np.ndarray, np.ndarray]:
    """Conditional logit: observed option is column 0. Returns coefficients and standard errors."""
    keep = valid[:, 0]
    X, valid = X[keep].astype(np.float64), valid[keep]
    mask = np.where(valid, 0.0, -np.inf)

    def nll(b):
        u = X @ b + mask
        lse = logsumexp(u, axis=1)
        p = np.exp(u - lse[:, None])
        grad = -(X[:, 0, :] - np.einsum("nk,nkp->np", p, X)).sum(axis=0) + 2 * l2 * b
        return -(u[:, 0] - lse).sum() + l2 * b @ b, grad

    res = minimize(nll, np.zeros(X.shape[2]), jac=True, method="L-BFGS-B")
    b = res.x
    u = X @ b + mask
    p = np.exp(u - logsumexp(u, axis=1)[:, None])
    xbar = np.einsum("nk,nkp->np", p, X)
    d = X - xbar[:, None, :]
    info = np.einsum("nk,nkp,nkq->pq", p, d, d) + 2 * l2 * np.eye(len(b))
    se = np.sqrt(np.clip(np.diag(np.linalg.pinv(info)), 0, None))
    return b, se


# ---------- simulation ----------
def simulate(x: np.ndarray, y: np.ndarray, t0: pd.Timestamp | pd.Series, hours: int, beta: np.ndarray, scaler: Scaler,
             pools: dict, grid: Grid, rng: np.random.Generator, k: int = N_CANDIDATES) -> tuple[np.ndarray, np.ndarray]:
    """Simulate len(x) bears for `hours` hourly steps. Returns positions (n, hours + 1) for x and y."""
    n = len(x)
    xs, ys = np.empty((n, hours + 1)), np.empty((n, hours + 1))
    xs[:, 0], ys[:, 0] = x, y
    heading = rng.uniform(-np.pi, np.pi, n)
    start = pd.to_datetime(pd.Series(t0 if isinstance(t0, pd.Series) else [t0] * n), utc=True).reset_index(drop=True)
    for h in range(hours):
        period, fall = period_season(start + pd.Timedelta(hours=h))
        sl, turn = sample_pool(pools, period + 4 * fall, k, rng)
        hd = heading[:, None] + turn
        cx, cy = xs[:, h:h + 1], ys[:, h:h + 1]
        x1, y1 = cx + sl * np.cos(hd), cy + sl * np.sin(hd)
        f, valid = grid.features(np.broadcast_to(cx, x1.shape), np.broadcast_to(cy, y1.shape), x1, y1, sl, turn)
        u = scaler(f) @ beta
        u = np.where(valid, u, -np.inf)
        stuck = ~np.isfinite(u).any(axis=1)
        u[stuck, 0] = 0.0
        p = np.exp(u - logsumexp(u, axis=1, keepdims=True))
        choice = (p.cumsum(axis=1) > rng.random((n, 1))).argmax(axis=1)
        rows = np.arange(n)
        xs[:, h + 1] = np.where(stuck, cx[:, 0], x1[rows, choice])
        ys[:, h + 1] = np.where(stuck, cy[:, 0], y1[rows, choice])
        heading = np.where(stuck, heading, hd[rows, choice])
    return xs, ys


# ---------- validation metrics ----------
def track_metrics(xs: np.ndarray, ys: np.ndarray, grid: Grid, present: np.ndarray | None = None) -> dict[str, float]:
    """Metrics over hourly tracks (n, T). `present` marks real fixes (gaps allowed)."""
    present = np.ones(xs.shape, bool) if present is None else present
    d24 = np.hypot(xs[:, 24:] - xs[:, :-24], ys[:, 24:] - ys[:, :-24])[present[:, 24:] & present[:, :-24]]
    spread = np.nanmax(np.where(present, np.hypot(xs - xs[:, :1], ys - ys[:, :1]), np.nan), axis=1)
    pair = present[:, 1:] & present[:, :-1]
    f, _ = grid.features(xs[:, :-1], ys[:, :-1], xs[:, 1:], ys[:, 1:], np.zeros(pair.shape), np.zeros(pair.shape))
    crossings_per_day = 24 * f[..., FEATURES.index("crosses_busy_road")][pair].mean()
    r, c, _ = grid.rc(xs[present], ys[present])
    k = grid.cls[r, c]
    natural = np.isin(k, [0, 2, 3, 4]).mean()
    return {"disp24_median_km": float(np.median(d24) / 1000), "disp24_p90_km": float(np.percentile(d24, 90) / 1000),
            "spread14d_median_km": float(np.median(spread) / 1000), "busy_crossings_per_day": float(crossings_per_day),
            "share_natural_cover": float(natural), "share_developed": float((k == 6).mean())}


def real_segments(gps: pd.DataFrame, days: int = 14) -> list[dict]:
    """Held-out bears' hourly fixes cut into 14-day windows (positions on an hourly grid, NaN for gaps)."""
    g = gps.copy()
    g["th"] = g["t"].dt.round("h")
    g = g[(g["t"] - g["th"]).abs() <= pd.Timedelta(minutes=10)].drop_duplicates(["bear", "th"])
    segs = []
    T = days * 24
    for bear, b in g.groupby("bear"):
        start = b["th"].min()
        while start + pd.Timedelta(hours=T) <= b["th"].max():
            w = b[(b["th"] >= start) & (b["th"] <= start + pd.Timedelta(hours=T))]
            if len(w) > 0.6 * T and w["th"].iloc[0] == start:
                idx = ((w["th"] - start) / pd.Timedelta(hours=1)).astype(int).to_numpy()
                xs, ys = np.full(T + 1, np.nan), np.full(T + 1, np.nan)
                xs[idx], ys[idx] = w["x"].to_numpy(), w["y"].to_numpy()
                segs.append({"bear": bear, "t0": start, "x": xs, "y": ys})
            start += pd.Timedelta(hours=T)
    return segs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    p.add_argument("--sims", type=int, default=25, help="simulated bears per start point")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--start-time", default="2026-11-15 18:00", help="local (Eastern) start time for app simulations")
    p.add_argument("--rebuild-grid", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pd.set_option("display.width", 160)
    tag = f"h3r{args.res}"
    rng = np.random.default_rng(42)

    grid_path = INTERIM / "movement_grids_100m.npz"
    if grid_path.exists() and not args.rebuild_grid:
        grid = Grid.load(grid_path)
    else:
        t = time.time()
        log.info("Building 100 m covariate grids (one-time, can take a few minutes)...")
        grid = build_grid(RAW / "landscape/landcover/clc_v4_state.tif", RAW / "landscape/roads/Basemap_routes.shp",
                          RAW / "landscape/roads/aadt.shp")
        grid.save(grid_path)
        log.info("  grids %s built in %.0fs", grid.cls.shape, time.time() - t)

    gps = load_gps(pd.read_parquet(RAW / "fwc_telemetry_camp_blanding.parquet",
                                   columns=["Bear", "GMT_DATE", "GMT_TIME", "LATITUDE", "LONGITUDE", "DOP"]))
    steps = hourly_steps(gps)
    bears = sorted(steps["bear"].unique())
    test_bears = [str(b) for b in rng.choice(bears, size=4, replace=False)]
    train = steps[~steps["bear"].isin(test_bears)]
    log.info("%d GPS fixes -> %d hourly steps (%d with a turning angle) from %d bears; held out: %s",
             len(gps), len(steps), int(steps["turn"].notna().sum()), len(bears), test_bears)
    summary = (steps.assign(period=steps["period"].map(dict(enumerate(PERIODS))),
                            season=np.where(steps["fall"] == 1, "fall", "rest"))
                    .groupby(["season", "period"])["sl"].describe(percentiles=[0.5, 0.9])[["count", "50%", "90%"]].round(0))
    log.info("Hourly step length (m) by season and time of day:\n%s", summary.to_string())

    pools_train = build_pools(train)
    X, valid = design(train, pools_train, grid, rng)
    obs = X[:, 0][valid[:, 0]]
    idx = [FEATURES.index(c) for c in CONTINUOUS]
    scaler = Scaler(obs[:, idx].mean(axis=0), obs[:, idx].std(axis=0) + 1e-9)
    beta, se = fit_clogit(scaler(X), valid)
    coef = pd.DataFrame({"coef": beta, "se": se, "z": beta / np.where(se > 0, se, np.nan)}, index=FEATURES)
    log.info("Step-selection coefficients (land classes vs upland forest; continuous ones standardised):\n%s",
             coef.round(3).to_string())

    # fair test on held-out bears
    segs = real_segments(gps[gps["bear"].isin(test_bears)], args.days)
    T = args.days * 24
    real_x, real_y = np.vstack([s["x"] for s in segs]), np.vstack([s["y"] for s in segs])
    present = ~np.isnan(real_x)
    rx = np.where(present, real_x, 0.0); ry = np.where(present, real_y, 0.0)
    R = 10
    sx0 = np.repeat([s["x"][0] for s in segs], R); sy0 = np.repeat([s["y"][0] for s in segs], R)
    st0 = pd.Series(np.repeat([s["t0"] for s in segs], R))
    sim = simulate(sx0, sy0, st0, T, beta, scaler, pools_train, grid, rng)
    null = simulate(sx0, sy0, st0, T, np.zeros_like(beta), scaler, pools_train, grid, rng)
    val = pd.DataFrame({"real held-out bears": track_metrics(rx, ry, grid, present),
                        "step-selection sims": track_metrics(*sim, grid),
                        "null walker (no habitat preference)": track_metrics(*null, grid)}).T
    log.info("Held-out check: %d real 14-day windows from %d bears, %d sims each:\n%s",
             len(segs), len(test_bears), R, val.round(3).to_string())

    # app simulations: refit on all bears
    pools_all = build_pools(steps)
    X, valid = design(steps, pools_all, grid, rng)
    obs = X[:, 0][valid[:, 0]]
    scaler = Scaler(obs[:, idx].mean(axis=0), obs[:, idx].std(axis=0) + 1e-9)
    beta_all, se_all = fit_clogit(scaler(X), valid)
    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / "movement_ssf.json").write_text(json.dumps({"features": FEATURES, "beta": beta_all.tolist(),
                                                          "se": se_all.tolist(), "scaler_mean": scaler.mean.tolist(),
                                                          "scaler_std": scaler.std.tolist()}, indent=2))
    np.savez_compressed(MODELS / "movement_pools.npz", **{str(k): v for k, v in pools_all.items()})

    cor = pd.read_parquet(args.dir / f"corridors_{tag}.parquet", columns=["cell", "core", "habitat_pct"])
    act = pd.read_parquet(args.dir / f"activity_{tag}.parquet", columns=["cell", "activity_index", "reports_90d"])
    starts = []
    for core, g in cor[cor["core"] != ""].groupby("core"):
        c = g.sort_values("habitat_pct", ascending=False)["cell"].iloc[len(g) // 20]   # good habitat, not an edge cell
        starts.append((f"core: {core}", c))
    for i, c in enumerate(act.nlargest(10, "activity_index")["cell"], 1):
        starts.append((f"recent activity #{i}", c))
    lat, lon = np.array([h3.cell_to_latlng(c) for _, c in starts]).T
    x0, y0 = TO_XY.transform(lon, lat)
    n = args.sims
    t0 = pd.Timestamp(args.start_time, tz="America/New_York").tz_convert("UTC")
    xs, ys = simulate(np.repeat(x0, n), np.repeat(y0, n), t0, args.days * 24, beta_all, scaler, pools_all, grid, rng)
    lon_s, lat_s = TO_LL.transform(xs.ravel(), ys.ravel())
    pts = pd.DataFrame({"start_id": np.repeat(np.arange(len(starts)), n * xs.shape[1]),
                        "sim": np.tile(np.repeat(np.arange(n), xs.shape[1]), len(starts)),
                        "hour": np.tile(np.arange(xs.shape[1]), len(starts) * n),
                        "lon": lon_s.astype(np.float32), "lat": lat_s.astype(np.float32)})
    labels = pd.DataFrame({"start_id": range(len(starts)), "start_label": [s for s, _ in starts],
                           "start_cell": [c for _, c in starts]})
    pts = pts.merge(labels, on="start_id")
    pts.to_parquet(args.dir / f"movement_sims_{tag}.parquet", index=False)

    pts["cell"] = [h3.latlng_to_cell(a, b, args.res) for a, b in zip(pts["lat"], pts["lon"])]
    reach = []
    for hrs, col in ((7 * 24, "p_7d"), (args.days * 24, f"p_{args.days}d")):
        v = pts[pts["hour"] <= hrs].drop_duplicates(["start_id", "sim", "cell"])
        reach.append(v.groupby(["start_id", "cell"]).size().div(n).rename(col))
    reach = pd.concat(reach, axis=1).fillna(0).reset_index().merge(labels[["start_id", "start_label"]], on="start_id")
    reach.to_parquet(args.dir / f"movement_reach_{tag}.parquet", index=False)

    last = pts[pts["hour"] == pts["hour"].max()]
    end = last.merge(labels, on=["start_id", "start_label", "start_cell"])
    sx, sy = TO_XY.transform(end["lon"].to_numpy(), end["lat"].to_numpy())
    st_x = np.repeat(x0, n); st_y = np.repeat(y0, n)
    dist_km = np.hypot(sx - st_x, sy - st_y) / 1000
    per_start = pd.DataFrame({"start": end["start_label"], "km_from_start": dist_km}).groupby("start", sort=False)["km_from_start"] \
        .describe(percentiles=[0.5, 0.9])[["50%", "90%", "max"]].round(1)
    log.info("App simulations: %d starts x %d bears x %d days. Distance from start after %d days (km):\n%s",
             len(starts), n, args.days, args.days, per_start.to_string())

    report = {"held_out_bears": test_bears, "steps": int(len(steps)),
              "step_length_summary": summary.reset_index().to_dict(orient="records"),
              "coefficients_train": coef.round(4).reset_index().rename(columns={"index": "feature"}).to_dict(orient="records"),
              "validation": val.round(4).to_dict(orient="index"),
              "starts": labels.to_dict(orient="records"), "distance_after_days": per_start.reset_index().to_dict(orient="records")}
    (args.dir / "movement_report.json").write_text(json.dumps(report, indent=2, default=str))
    log.info("Wrote %s and %s", args.dir / f"movement_sims_{tag}.parquet", args.dir / f"movement_reach_{tag}.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())