"""SQL queries the app will use, written plainly so they can be read (and tried in psql).

  at_point(lat, lon)            everything we know about the hexagon containing a point
  nearby(lat, lon, km, limit)   hexagons within km of a point, ranked by next month's risk
  top_hexagons(n, emerging)     the n highest-risk hexagons (or emerging-risk ones) statewide
  layer_geojson(layer, bbox)    a map layer as a GeoJSON FeatureCollection, built in the database
  layer_rows(layer, bbox)       the same layer as rows keyed by H3 id (lighter, for H3-aware map libraries)
  latest_runs / forecast_scores / corridor_paths / sim_starts / sim_tracks / sim_reach

Usage (demo):
    python -m bearcast.db.queries --lat 30.41 --lon -86.75
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import Engine, text

from bearcast.db import engine

POINT = "ST_SetSRID(ST_Point(:lon, :lat), 4326)"

AT_POINT = f"""
SELECT h.cell, h.range_label,
       f.month AS forecast_month, f.expected_calls, f.tier, f.risk_rank, f.emerging_rank, f.never_conflict,
       a.as_of AS activity_as_of, a.reports_90d, a.days_since_report, a.activity_level,
       b.habitat_pct, b.core, b.corridor_pair, b.corridor_value, b.road_risk,
       l.hu_den_2020, l.cons_share, l.lc_natural, l.max_aadt,
       round(ST_Y(h.centroid)::numeric, 4) AS lat,
       round(ST_X(h.centroid)::numeric, 4) AS lon,
       (
         SELECT CASE WHEN c.county ~* ' county$' THEN c.county ELSE c.county || ' County' END
         FROM calls c
         WHERE c.county IS NOT NULL AND btrim(c.county) <> ''
         ORDER BY c.geom <-> h.centroid
         LIMIT 1
       ) AS location_name
FROM hexagons h
LEFT JOIN latest_forecast    f USING (cell)
LEFT JOIN latest_activity    a USING (cell)
LEFT JOIN latest_bear_layers b USING (cell)
LEFT JOIN landscape          l USING (cell)
WHERE ST_Contains(h.geom, {POINT})
"""

NEARBY = f"""
SELECT f.cell, f.tier, f.expected_calls, f.risk_rank,
       round((ST_Distance(h.centroid::geography, {POINT}::geography) / 1000)::numeric, 1) AS km
FROM latest_forecast f
JOIN hexagons h USING (cell)
WHERE ST_DWithin(h.centroid::geography, {POINT}::geography, :km * 1000)
ORDER BY f.expected_calls DESC
LIMIT :limit
"""

TOP = """
SELECT f.cell, f.tier, f.expected_calls, f.risk_rank, f.emerging_rank,
       round(ST_Y(h.centroid)::numeric, 3) AS lat, round(ST_X(h.centroid)::numeric, 3) AS lon
FROM latest_forecast f
JOIN hexagons h USING (cell)
WHERE (:emerging = false) OR f.emerging_rank IS NOT NULL
ORDER BY CASE WHEN :emerging THEN f.emerging_rank ELSE f.risk_rank END
LIMIT :n
"""

LAYERS = {   # layer name -> (view, property columns, filter)
    "forecast": ("latest_forecast", ["cell", "tier", "expected_calls", "risk_rank", "emerging_rank"], "tier <> 'low'"),
    "emerging": ("latest_forecast", ["cell", "emerging_rank", "emerging_pct", "expected_calls", "never_conflict"], "emerging_pct <= 0.10"),
    "activity": ("latest_activity", ["cell", "activity_level", "reports_90d", "days_since_report"], "activity_level <> 'none'"),
    "habitat": ("latest_bear_layers", ["cell", "habitat_pct", "core"], "true"),
    "corridors": ("latest_bear_layers", ["cell", "corridor_value", "corridor_pair", "on_best_path"], "corridor_value <= 0.15 AND coalesce(core, '') = ''"),
    "road_risk": ("latest_bear_layers", ["cell", "road_risk"], "road_risk > 0"),
}


def at_point(eng: Engine, lat: float, lon: float) -> dict | None:
    with eng.connect() as c:
        row = c.execute(text(AT_POINT), {"lat": lat, "lon": lon}).mappings().first()
    return dict(row) if row else None


def nearby(eng: Engine, lat: float, lon: float, km: float = 10, limit: int = 10) -> list[dict]:
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(NEARBY), {"lat": lat, "lon": lon, "km": km, "limit": limit}).mappings()]


def top_hexagons(eng: Engine, n: int = 10, emerging: bool = False) -> list[dict]:
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(TOP), {"n": n, "emerging": emerging}).mappings()]


def _bbox_clause(bbox: tuple[float, float, float, float] | None) -> tuple[str, dict]:
    if bbox is None:
        return "", {}
    w, s_, e, n = bbox
    return " AND geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)", {"w": w, "s": s_, "e": e, "n": n}


def layer_geojson(eng: Engine, layer: str, bbox: tuple[float, float, float, float] | None = None) -> dict:
    """The database assembles the GeoJSON itself (json_build_object + ST_AsGeoJSON).
    bbox = (west, south, east, north) limits it to what's on screen."""
    view, cols, where = LAYERS[layer]
    props = ", ".join(f"'{c}', {c}" for c in cols)
    box, params = _bbox_clause(bbox)
    sql = f"""
    SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
             'type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 6)::json,
             'properties', json_build_object({props}))), '[]'::json))
    FROM {view} WHERE {where}{box}
    """
    with eng.connect() as c:
        return c.execute(text(sql), params).scalar()


def layer_rows(eng: Engine, layer: str, bbox: tuple[float, float, float, float] | None = None) -> list[dict]:
    """The same layer as plain rows keyed by H3 id: much smaller than GeoJSON, for map libraries that draw
    H3 hexagons themselves (deck.gl H3HexagonLayer, h3-js)."""
    view, cols, where = LAYERS[layer]
    box, params = _bbox_clause(bbox)
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(f"SELECT {', '.join(cols)} FROM {view} WHERE {where}{box}"), params).mappings()]


def latest_runs(eng: Engine) -> list[dict]:
    """What each layer is based on, for 'as of' labels in the app."""
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT kind, run_id, data_through, created_at, metrics FROM latest_runs ORDER BY kind")).mappings()]


def forecast_scores(eng: Engine) -> list[dict]:
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT month, conflicts, expected_total, capture_1pct, capture_5pct, emerging_capture_10 "
            "FROM forecast_scores ORDER BY month")).mappings()]


def corridor_paths(eng: Engine) -> dict:
    sql = """
    SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
             'type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 6)::json,
             'properties', json_build_object('pair', pair, 'length_km', length_km,
                                             'busy_road_hexagons', busy_road_hexagons))), '[]'::json))
    FROM corridor_paths WHERE geom IS NOT NULL
      AND run_id = (SELECT run_id FROM latest_runs WHERE kind = 'bear_layers')
    """
    with eng.connect() as c:
        return c.execute(text(sql)).scalar()


MOVEMENT_RUN = "(SELECT run_id FROM latest_runs WHERE kind = 'movement')"


def sim_starts(eng: Engine) -> list[dict]:
    sql = f"""
    SELECT s.start_id, s.start_label, s.start_cell,
           round(ST_Y(h.centroid)::numeric, 5) AS lat, round(ST_X(h.centroid)::numeric, 5) AS lon
    FROM sim_starts s LEFT JOIN hexagons h ON h.cell = s.start_cell
    WHERE s.run_id = {MOVEMENT_RUN} ORDER BY s.start_id
    """
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(sql)).mappings()]


def sim_tracks(eng: Engine, start_id: int) -> dict:
    """Simulated tracks from one start as GeoJSON lines; vertex i of each line = hour i (for animation)."""
    sql = f"""
    SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
             'type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 5)::json,
             'properties', json_build_object('sim', sim, 'start_label', start_label)) ORDER BY sim), '[]'::json))
    FROM sim_tracks WHERE run_id = {MOVEMENT_RUN} AND start_id = :sid
    """
    with eng.connect() as c:
        return c.execute(text(sql), {"sid": start_id}).scalar()


def sim_reach(eng: Engine, start_id: int) -> list[dict]:
    """Share of simulated bears reaching each hexagon (H3 ids; some may lie outside the main hexagon set)."""
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(
            f"SELECT cell, p_7d, p_14d FROM sim_reach WHERE run_id = {MOVEMENT_RUN} AND start_id = :sid "
            "ORDER BY p_14d DESC"), {"sid": start_id}).mappings()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lat", type=float, default=30.41)
    p.add_argument("--lon", type=float, default=-86.75)
    p.add_argument("--km", type=float, default=10)
    args = p.parse_args(argv)
    eng = engine()
    print(f"\nHexagon containing ({args.lat}, {args.lon}):")
    print(json.dumps(at_point(eng, args.lat, args.lon), indent=2, default=str))
    print(f"\nHighest-risk hexagons within {args.km:g} km:")
    for r in nearby(eng, args.lat, args.lon, args.km, 5):
        print(f"  {r['cell']}  {r['tier']:<9}  {r['expected_calls']:.2f} expected calls  {r['km']} km away")
    print("\nTop 5 statewide:")
    for r in top_hexagons(eng, 5):
        print(f"  #{r['risk_rank']:<3} {r['cell']}  ({r['lat']}, {r['lon']})  {r['expected_calls']:.2f}")
    print("\nTop 5 emerging (no conflict in the last 12 months):")
    for r in top_hexagons(eng, 5, emerging=True):
        print(f"  #{r['emerging_rank']:<3} {r['cell']}  ({r['lat']}, {r['lon']})  {r['expected_calls']:.2f}")
    print("\nMap layers as GeoJSON (feature counts):")
    for name in LAYERS:
        print(f"  {name:<10} {len(layer_geojson(eng, name)['features']):>6} features")
    return 0


if __name__ == "__main__":
    sys.exit(main())