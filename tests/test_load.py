"""Loader + query tests against the throwaway TEST_DATABASE_URL database (skipped without it)."""

import json
import os

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString
from sqlalchemy import create_engine, text

import bearcast.db  # noqa: F401  (loads .env)
from bearcast.db.load import load_all, load_forecast
from bearcast.db.migrate import migrate
from bearcast.db.queries import LAYERS, at_point, layer_geojson, nearby, top_hexagons

CENTER = h3.latlng_to_cell(29.2, -81.7, 7)
CELLS = sorted(h3.grid_disk(CENTER, 1))                  # 7 hexagons


@pytest.fixture(scope="module")
def eng():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    e = create_engine(url)
    try:
        with e.begin() as c:
            c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable: {exc}")
    migrate(e)
    yield e
    e.dispose()


def _forecast(month: str, top_cell: str) -> pd.DataFrame:
    exp = np.where(np.array(CELLS) == top_cell, 3.0, np.linspace(0.1, 0.6, len(CELLS)))
    rank = pd.Series(exp).rank(ascending=False, method="first").astype(int)
    return pd.DataFrame({"cell": CELLS, "month": pd.Timestamp(month), "expected_calls": exp, "risk_rank": rank,
                         "risk_pct": rank / len(CELLS), "tier": np.where(rank == 1, "very high", "low"),
                         "no_recent_conflict": rank > 1, "never_conflict": False,
                         "emerging_rank": pd.array(np.where(rank > 1, rank - 1, pd.NA), dtype="Int64"),
                         "emerging_pct": np.nan, "conf_sum12": 0.0, "conf_hist_rate": 0.1})


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    root = tmp_path_factory.mktemp("bearcast")
    d, interim = root / "processed", root / "interim"
    d.mkdir(); interim.mkdir()
    n = len(CELLS)
    pd.DataFrame({"cell": CELLS, "range_rank": 4, "range_label": "frequent"}).to_parquet(d / "cells_h3r7.parquet")
    pd.DataFrame({"cell": CELLS, "hu_den_2020": 10.0, "pop_den_2020": 20.0, "wui_intermix_share": 0.1, "cons_share": 0.5,
                  "dist_bigcons_km": 1.0, "road_km_per_km2": 0.5, "max_aadt": 12000.0}).to_parquet(d / "landscape_h3r7.parquet")
    pd.DataFrame({"cell": CELLS, "lc_urban_high": 0.1, "lc_urban_low": 0.2, "lc_natural": 0.6,
                  "lc_forested_wetland": 0.3}).to_parquet(d / "landcover_h3r7.parquet")
    lat, lng = h3.cell_to_latlng(CENTER)
    pd.DataFrame({"IncidentID": ["a", "b", "far"], "date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-03"]),
                  "Latitude": [lat, lat, 25.0], "Longitude": [lng, lng, -80.3], "call_class": "sighting",
                  "is_conflict": [True, False, False], "is_repeat": False,
                  "reason": "garbage"}).to_parquet(interim / "calls_clean.parquet")
    pd.DataFrame({"cell": CELLS * 2, "month": pd.to_datetime(["2026-09-01"] * n + ["2026-10-01"] * n),
                  "n_conflict": [1] + [0] * (2 * n - 1), "n_encounter": 0, "n_sighting": 0, "n_bear_welfare": 0}
                 ).to_parquet(d / "panel_h3r7_month.parquet")
    _forecast("2026-11-01", CENTER).to_parquet(d / "forecast_h3r7_2026-11.parquet")
    (d / "forecast_h3r7_2026-11.json").write_text(json.dumps({"model": "m.txt", "calibration": {"factor": 1.2}}))
    act = pd.DataFrame({"cell": CELLS, "sighting_30d": 1, "conflict_30d": 0, "roadkill_30d": 0, "reports_30d": 1,
                        "sighting_90d": 2, "conflict_90d": 1, "roadkill_90d": 0, "reports_90d": 3,
                        "last_report": pd.Timestamp("2026-10-20"), "days_since_report": 11,
                        "activity_index": 4.0, "activity_level": "high"})
    act.to_parquet(d / "activity_h3r7.parquet")
    (d / "activity_h3r7.json").write_text(json.dumps({"as_of": "2026-10-31"}))
    pd.DataFrame({"cell": CELLS, "habitat_score": 0.5, "habitat_pct": 0.8}).to_parquet(d / "habitat_h3r7.parquet")
    pd.DataFrame({"cell": CELLS, "habitat_pct": 0.8, "resistance": 2.0, "core": ["Ocala"] + [""] * (n - 1),
                  "corridor_value": [0.0] + [0.03] * (n - 2) + [np.inf], "corridor_pair": "A - B",
                  "in_corridor": True, "on_best_path": False, "movement": 1.0, "road_risk": 0.4, "crossing_risk": 0.2,
                  "max_aadt": 12000.0, "roadkill_2010_2025": 2}).to_parquet(d / "corridors_h3r7.parquet")
    gpd.GeoDataFrame({"pair": ["A - B"], "cost": [10.0], "length_km": [5.0], "busy_road_hexagons": [1]},
                     geometry=[LineString([(lng, lat), (lng + 0.05, lat)])], crs=4326).to_parquet(d / "corridor_paths_h3r7.parquet")
    sims = pd.DataFrame({"start_id": 0, "sim": np.repeat([0, 1], 3), "hour": np.tile([0, 1, 2], 2),
                         "lon": lng + np.tile([0, 0.01, 0.02], 2), "lat": lat, "start_label": "core: Ocala", "start_cell": CENTER})
    sims.to_parquet(d / "movement_sims_h3r7.parquet")
    pd.DataFrame({"start_id": [0], "cell": [CENTER], "p_7d": [1.0], "p_14d": [1.0],
                  "start_label": ["core: Ocala"]}).to_parquet(d / "movement_reach_h3r7.parquet")
    (d / "movement_report.json").write_text(json.dumps({"validation": {"ok": 1}}))
    return d, interim


def test_load_everything_then_reload_is_idempotent(eng, data):
    d, interim = data
    first = load_all(eng, processed=d, interim=interim)
    assert first == {"hexagons": 7, "landscape": 7, "calls": 3, "monthly_counts": 1, "forecast": 7,
                     "activity": 7, "bear_layers": 7, "movement": 6}
    second = load_all(eng, processed=d, interim=interim)
    assert second["forecast"] == second["activity"] == second["bear_layers"] == second["movement"] == 0
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM model_runs")).scalar() == 4
        assert c.execute(text("SELECT count(*) FROM calls")).scalar() == 3
        assert c.execute(text("SELECT cell FROM calls WHERE incident_id = 'far'")).scalar() is None   # outside universe
        assert c.execute(text("SELECT corridor_value FROM bear_layers WHERE corridor_value IS NULL")).first() is not None
        assert c.execute(text("SELECT lc_urban FROM landscape LIMIT 1")).scalar() == pytest.approx(0.3)


def test_point_lookup_combines_all_layers(eng, data):
    lat, lng = h3.cell_to_latlng(CENTER)
    r = at_point(eng, lat, lng)
    assert r["cell"] == CENTER and r["tier"] == "very high" and r["expected_calls"] == pytest.approx(3.0)
    assert r["activity_level"] == "high" and str(r["activity_as_of"]) == "2026-10-31"
    assert r["core"] == "Ocala" and r["habitat_pct"] == pytest.approx(0.8)
    assert at_point(eng, 0.0, 0.0) is None


def test_nearby_top_and_geojson(eng, data):
    lat, lng = h3.cell_to_latlng(CENTER)
    near = nearby(eng, lat, lng, km=5, limit=3)
    assert near[0]["cell"] == CENTER and len(near) == 3
    assert [r["expected_calls"] for r in near] == sorted([r["expected_calls"] for r in near], reverse=True)
    assert top_hexagons(eng, 1)[0]["cell"] == CENTER
    assert top_hexagons(eng, 1, emerging=True)[0]["emerging_rank"] == 1
    for name in LAYERS:
        gj = layer_geojson(eng, name)
        assert gj["type"] == "FeatureCollection"
    assert len(layer_geojson(eng, "forecast")["features"]) == 1                      # only non-'low' tiers
    assert layer_geojson(eng, "habitat")["features"][0]["geometry"]["type"] == "Polygon"


def test_a_new_forecast_becomes_the_latest(eng, data):
    d, _ = data
    other = CELLS[3]
    _forecast("2026-12-01", other).to_parquet(d / "forecast_h3r7_2026-12.parquet")
    with eng.begin() as c:
        assert load_forecast(c, d, "h3r7") == 7
    lat, lng = h3.cell_to_latlng(other)
    r = at_point(eng, lat, lng)
    assert str(r["forecast_month"]) == "2026-12-01" and r["tier"] == "very high"
    with eng.connect() as c:
        assert c.execute(text("SELECT count(DISTINCT run_id) FROM forecasts")).scalar() == 2   # history kept