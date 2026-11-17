"""Load the pipeline's Parquet outputs into Postgres.

Reference data (upserted: inserted, or updated if the row already exists):
  hexagons        data/processed/cells_h3r7.parquet (polygons built from the H3 ids)
  landscape       data/processed/landscape_h3r7.parquet + landcover_h3r7.parquet (pop-up facts)
  calls           data/interim/calls_clean.parquet
  monthly_counts  data/processed/panel_h3r7_month.parquet (replaced; non-zero rows only)

Model outputs (each load = one model_runs row; the files' fingerprint is stored, so loading the
same files twice is skipped rather than duplicated):
  forecast     newest data/processed/forecast_h3r7_YYYY-MM.parquet (+ .json)
  activity     data/processed/activity_h3r7.parquet (+ .json)
  bear_layers  habitat_h3r7 + corridors_h3r7 + corridor_paths_h3r7 (+ reports)
  movement     movement_sims_h3r7 + movement_reach_h3r7 (+ movement_report.json)

Bulk rows go in with COPY (Postgres's fast bulk-load path); upserts COPY into a temporary
table first, then INSERT ... ON CONFLICT DO UPDATE.

Usage:
    python -m bearcast.db.load                       # everything that exists
    python -m bearcast.db.load --only forecast activity
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import geopandas as gpd
import h3
import pandas as pd
from sqlalchemy import Connection, Engine, text

from bearcast.db import engine

log = logging.getLogger(__name__)
PROCESSED, INTERIM = Path("data/processed"), Path("data/interim")
STEPS = ["hexagons", "landscape", "calls", "monthly_counts", "forecast", "activity", "bear_layers", "movement"]


# ---------------------------------------------------------------- helpers
def clean_value(v):
    """Python value COPY can write: None for missing / infinite, plain types for numpy/pandas."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date() if v == v.normalize() else v.to_pydatetime()
    if hasattr(v, "item"):                      # numpy scalar
        return clean_value(v.item())
    return v


def rows_of(df: pd.DataFrame) -> Iterable[tuple]:
    for row in df.itertuples(index=False, name=None):
        yield tuple(clean_value(v) for v in row)


def copy_rows(conn: Connection, table: str, columns: Sequence[str], rows: Iterable[tuple]) -> int:
    cur = conn.connection.driver_connection.cursor()
    n = 0
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
            n += 1
    return n


def upsert(conn: Connection, table: str, columns: Sequence[str], rows: Iterable[tuple], key: str) -> int:
    conn.execute(text(f"CREATE TEMP TABLE _stage (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP"))
    n = copy_rows(conn, "_stage", columns, rows)
    cols = ", ".join(columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
    conn.execute(text(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _stage "
                      f"ON CONFLICT ({key}) DO UPDATE SET {updates}"))
    conn.execute(text("DROP TABLE _stage"))
    return n


def point(lon: float, lat: float) -> str:
    return f"SRID=4326;POINT({lon} {lat})"


def hex_ewkt(cell: str) -> str:
    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return "SRID=4326;POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"


def fingerprint(paths: Sequence[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def new_run(conn: Connection, kind: str, sources: Sequence[Path], data_through: date | None = None,
            params: dict | None = None, metrics: dict | None = None) -> int | None:
    """Create a model_runs row, or return None if these exact files were already loaded."""
    sha = fingerprint(sources)
    if conn.execute(text("SELECT 1 FROM model_runs WHERE kind = :k AND params->>'source_sha' = :s"),
                    {"k": kind, "s": sha}).first():
        log.info("%s: these files are already loaded (fingerprint %s), skipping", kind, sha)
        return None
    p = {**(params or {}), "source_sha": sha, "sources": [s.name for s in sources]}
    return conn.execute(text("INSERT INTO model_runs (kind, git_commit, data_through, params, metrics) "
                             "VALUES (:k, :g, :d, CAST(:p AS jsonb), CAST(:m AS jsonb)) RETURNING run_id"),
                        {"k": kind, "g": git_commit(), "d": data_through, "p": json.dumps(p, default=str),
                         "m": json.dumps(metrics or {}, default=str)}).scalar()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


# ---------------------------------------------------------------- reference data
def load_hexagons(conn: Connection, d: Path, tag: str) -> int:
    c = pd.read_parquet(d / f"cells_{tag}.parquet", columns=["cell", "range_rank", "range_label"])
    c["cell"] = c["cell"].astype(str)
    lat, lng = zip(*(h3.cell_to_latlng(x) for x in c["cell"]))
    df = pd.DataFrame({"cell": c["cell"], "res": [h3.get_resolution(x) for x in c["cell"]],
                       "geom": [hex_ewkt(x) for x in c["cell"]], "centroid": [point(b, a) for a, b in zip(lat, lng)],
                       "range_rank": c["range_rank"].astype("Int64"), "range_label": c["range_label"].astype(str),
                       "area_km2": [h3.cell_area(x, unit="km^2") for x in c["cell"]]})
    return upsert(conn, "hexagons", list(df.columns), rows_of(df), "cell")


def load_landscape(conn: Connection, d: Path, tag: str) -> int:
    s = pd.read_parquet(d / f"landscape_{tag}.parquet").merge(pd.read_parquet(d / f"landcover_{tag}.parquet"),
                                                              on="cell", how="left")
    s["lc_urban"] = s["lc_urban_high"].fillna(0) + s["lc_urban_low"].fillna(0)
    cols = ["cell", "hu_den_2020", "pop_den_2020", "wui_intermix_share", "cons_share", "dist_bigcons_km",
            "lc_natural", "lc_forested_wetland", "lc_urban", "road_km_per_km2", "max_aadt"]
    return upsert(conn, "landscape", cols, rows_of(s[cols].assign(cell=s["cell"].astype(str))), "cell")


def load_calls(conn: Connection, interim: Path, res: int) -> int:
    c = pd.read_parquet(interim / "calls_clean.parquet")
    c = c.dropna(subset=["Latitude", "Longitude", "date"])
    cells = set(r[0] for r in conn.execute(text("SELECT cell FROM hexagons")))
    cell = [h3.latlng_to_cell(a, b, res) for a, b in zip(c["Latitude"], c["Longitude"])]
    df = pd.DataFrame({
        "incident_id": c["IncidentID"].astype(str),
        "report_date": pd.to_datetime(c["date"]).dt.date,
        "call_class": c["call_class"].astype(str),
        "reason": c["reason"].astype(str) if "reason" in c else None,
        "is_conflict": c["is_conflict"].astype(bool),
        "is_repeat": c["is_repeat"].astype(bool),
        "county": c["County"].astype(str).str.strip().str.title() if "County" in c else None,
        "source_layer": c["source_layer"].astype(str) if "source_layer" in c else None,
        "cell": [x if x in cells else None for x in cell],
        "geom": [point(b, a) for a, b in zip(c["Latitude"], c["Longitude"])],
    }).drop_duplicates("incident_id", keep="last")
    return upsert(conn, "calls", list(df.columns), rows_of(df), "incident_id")


def load_monthly_counts(conn: Connection, d: Path, tag: str) -> int:
    cols = ["cell", "month", "n_conflict", "n_encounter", "n_sighting", "n_bear_welfare"]
    p = pd.read_parquet(d / f"panel_{tag}_month.parquet", columns=cols)
    p = p[p[cols[2:]].sum(axis=1) > 0].assign(cell=lambda x: x["cell"].astype(str),
                                              month=lambda x: pd.to_datetime(x["month"]).dt.date)
    conn.execute(text("DELETE FROM monthly_counts"))
    return copy_rows(conn, "monthly_counts", cols, rows_of(p[cols]))


# ---------------------------------------------------------------- model outputs
def load_forecast(conn: Connection, d: Path, tag: str) -> int:
    files = sorted(d.glob(f"forecast_{tag}_*.parquet"))
    if not files:
        log.warning("no forecast file found")
        return 0
    f = files[-1]
    summary = read_json(f.with_suffix(".json"))
    fc = pd.read_parquet(f)
    month = pd.Timestamp(fc["month"].iloc[0])
    run = new_run(conn, "forecast", [f], data_through=(month - pd.offsets.MonthBegin(1)).date(),
                  params={"model": summary.get("model"), "calibration": summary.get("calibration")},
                  metrics={k: summary.get(k) for k in ("expected_calls_statewide", "tiers",
                                                       "share_of_expected_calls_in_top_5pct")})
    if run is None:
        return 0
    cols = ["month", "cell", "expected_calls", "risk_rank", "risk_pct", "tier", "no_recent_conflict",
            "never_conflict", "emerging_rank", "emerging_pct"]
    df = fc.assign(cell=fc["cell"].astype(str), tier=fc["tier"].astype(str),
                   month=pd.to_datetime(fc["month"]).dt.date)[cols]
    return copy_rows(conn, "forecasts", ["run_id", *cols], ((run, *r) for r in rows_of(df)))


def load_activity(conn: Connection, d: Path, tag: str) -> int:
    f = d / f"activity_{tag}.parquet"
    summary = read_json(f.with_suffix(".json"))
    a = pd.read_parquet(f)
    as_of = pd.Timestamp(summary.get("as_of") or a["last_report"].max()).date()
    run = new_run(conn, "activity", [f], data_through=as_of, metrics=summary)
    if run is None:
        return 0
    cols = ["cell", "sighting_30d", "conflict_30d", "roadkill_30d", "sighting_90d", "conflict_90d", "roadkill_90d",
            "reports_90d", "last_report", "days_since_report", "activity_index", "activity_level"]
    df = a.assign(cell=a["cell"].astype(str), activity_level=a["activity_level"].astype(str),
                  last_report=pd.to_datetime(a["last_report"]).dt.date)[cols]
    return copy_rows(conn, "activity", ["run_id", "as_of", *cols], ((run, as_of, *r) for r in rows_of(df)))


def load_bear_layers(conn: Connection, d: Path, tag: str) -> int:
    files = [d / f"habitat_{tag}.parquet", d / f"corridors_{tag}.parquet", d / f"corridor_paths_{tag}.parquet"]
    hab_rep, cor_rep = read_json(d / f"habitat_{tag}_report.json"), read_json(d / f"corridors_{tag}_report.json")
    run = new_run(conn, "bear_layers", files, params={"corridors": cor_rep.get("params")},
                  metrics={"habitat_leave_one_area_out_mean": hab_rep.get("mean"),
                           "roadkill_check": cor_rep.get("roadkill_check"), "cores": cor_rep.get("cores")})
    if run is None:
        return 0
    hab = pd.read_parquet(files[0], columns=["cell", "habitat_score"])
    cor = pd.read_parquet(files[1])
    t = cor.merge(hab, on="cell", how="left").assign(cell=lambda x: x["cell"].astype(str))
    cols = ["cell", "habitat_score", "habitat_pct", "resistance", "core", "corridor_value", "corridor_pair",
            "in_corridor", "on_best_path", "road_risk", "crossing_risk", "roadkill_2010_2025"]
    n = copy_rows(conn, "bear_layers", ["run_id", *cols], ((run, *r) for r in rows_of(t[cols])))
    paths = gpd.read_parquet(files[2])
    prow = ((run, r.pair, clean_value(r.cost), clean_value(r.length_km), clean_value(r.busy_road_hexagons),
             f"SRID=4326;{r.geometry.wkt}" if r.geometry is not None else None) for r in paths.itertuples())
    copy_rows(conn, "corridor_paths", ["run_id", "pair", "cost", "length_km", "busy_road_hexagons", "geom"], prow)
    return n


def load_movement(conn: Connection, d: Path, tag: str) -> int:
    files = [d / f"movement_sims_{tag}.parquet", d / f"movement_reach_{tag}.parquet"]
    rep = read_json(d / "movement_report.json")
    run = new_run(conn, "movement", files, metrics={"validation": rep.get("validation"),
                                                    "held_out_bears": rep.get("held_out_bears")})
    if run is None:
        return 0
    sims = pd.read_parquet(files[0])
    starts = sims.drop_duplicates("start_id")[["start_id", "start_label", "start_cell"]].sort_values("start_id")
    copy_rows(conn, "sim_starts", ["run_id", "start_id", "start_label", "start_cell"],
              ((run, *r) for r in rows_of(starts)))
    pts = ((run, int(s), int(m), int(h), point(lo, la)) for s, m, h, lo, la in
           zip(sims["start_id"], sims["sim"], sims["hour"], sims["lon"].astype(float), sims["lat"].astype(float)))
    n = copy_rows(conn, "sim_points", ["run_id", "start_id", "sim", "hour", "geom"], pts)
    reach = pd.read_parquet(files[1])[["start_id", "cell", "p_7d", "p_14d"]]
    copy_rows(conn, "sim_reach", ["run_id", "start_id", "cell", "p_7d", "p_14d"], ((run, *r) for r in rows_of(reach)))
    return n


def load_all(eng: Engine, steps: Sequence[str] = STEPS, processed: Path = PROCESSED, interim: Path = INTERIM,
             res: int = 7) -> dict[str, int]:
    tag = f"h3r{res}"
    funcs = {"hexagons": lambda c: load_hexagons(c, processed, tag),
             "landscape": lambda c: load_landscape(c, processed, tag),
             "calls": lambda c: load_calls(c, interim, res),
             "monthly_counts": lambda c: load_monthly_counts(c, processed, tag),
             "forecast": lambda c: load_forecast(c, processed, tag),
             "activity": lambda c: load_activity(c, processed, tag),
             "bear_layers": lambda c: load_bear_layers(c, processed, tag),
             "movement": lambda c: load_movement(c, processed, tag)}
    out = {}
    for step in [s for s in STEPS if s in steps]:          # always in dependency order
        with eng.begin() as conn:                           # one transaction per step
            out[step] = funcs[step](conn)
        log.info("%-15s %8d rows", step, out[step])
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", choices=STEPS, default=STEPS)
    p.add_argument("--res", type=int, default=7)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    eng = engine()
    load_all(eng, args.only, res=args.res)
    with eng.connect() as conn:
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in
                  ["hexagons", "landscape", "calls", "monthly_counts", "model_runs", "forecasts", "activity",
                   "bear_layers", "corridor_paths", "sim_points", "sim_reach"]}
        runs = conn.execute(text("SELECT kind, run_id, data_through, created_at::date FROM latest_runs ORDER BY kind")).all()
    log.info("Table sizes: %s", counts)
    log.info("Latest runs:\n%s", "\n".join(f"  {k:<12} run {r:<4} data through {dt}  loaded {c}" for k, r, dt, c in runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())