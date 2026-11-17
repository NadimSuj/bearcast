"""Database tests. The integration tests need TEST_DATABASE_URL (in .env) pointing at a throwaway
database; they wipe and rebuild its public schema. Without it they are skipped."""

import os

import h3
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import bearcast.db  # noqa: F401  (loads .env)
from bearcast.db.migrate import MIGRATIONS, migrate, migration_files


def test_migration_files_are_numbered_and_unique(tmp_path):
    (tmp_path / "001_init.sql").write_text("SELECT 1;")
    (tmp_path / "002_more.sql").write_text("SELECT 1;")
    assert [p.name for p in migration_files(tmp_path)] == ["001_init.sql", "002_more.sql"]
    (tmp_path / "2_bad.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError):
        migration_files(tmp_path)


def test_repo_migrations_are_valid():
    assert migration_files(MIGRATIONS)[0].name == "001_init.sql"


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
    yield e
    e.dispose()


def _hex_wkt(cell):
    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"


def test_migrate_creates_schema_and_is_idempotent(eng):
    assert migrate(eng) == ["001_init.sql"]
    assert migrate(eng) == []
    with eng.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
    assert {"hexagons", "calls", "forecasts", "activity", "bear_layers", "sim_points", "latest_forecast"} <= tables


def test_spatial_lookup_and_latest_forecast_view(eng):
    cell = h3.latlng_to_cell(29.2, -81.7, 7)
    lat, lng = h3.cell_to_latlng(cell)
    with eng.begin() as c:
        c.execute(text("INSERT INTO hexagons (cell, geom, centroid, range_rank) VALUES "
                       "(:cell, ST_GeomFromText(:wkt, 4326), ST_SetSRID(ST_Point(:lng, :lat), 4326), 4)"),
                  {"cell": cell, "wkt": _hex_wkt(cell), "lng": lng, "lat": lat})
        for month, calls in (("2026-10-01", 1.0), ("2026-11-01", 2.5)):
            run = c.execute(text("INSERT INTO model_runs (kind) VALUES ('forecast') RETURNING run_id")).scalar()
            c.execute(text("INSERT INTO forecasts VALUES (:r, :m, :cell, :e, 1, 0.01, 'very high', false, false, NULL, NULL)"),
                      {"r": run, "m": month, "cell": cell, "e": calls})
    with eng.connect() as c:
        # which hexagon contains this point, and what does the newest forecast say?
        row = c.execute(text("SELECT cell, month, expected_calls FROM latest_forecast "
                             "WHERE ST_Contains(geom, ST_SetSRID(ST_Point(:lng, :lat), 4326))"),
                        {"lng": lng + 0.001, "lat": lat}).one()
    assert row.cell == cell and str(row.month) == "2026-11-01" and row.expected_calls == pytest.approx(2.5)


def test_constraints_reject_bad_rows(eng):
    with pytest.raises(IntegrityError), eng.begin() as c:
        run = c.execute(text("INSERT INTO model_runs (kind) VALUES ('forecast') RETURNING run_id")).scalar()
        c.execute(text("INSERT INTO forecasts VALUES (:r, '2026-11-01', 'not-a-cell', 1, 1, 0.01, 'high', false, false, NULL, NULL)"),
                  {"r": run})
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(text("INSERT INTO model_runs (kind) VALUES ('mystery')"))


def test_sim_tracks_view_builds_ordered_lines(eng):
    cell = h3.latlng_to_cell(29.2, -81.7, 7)
    with eng.begin() as c:
        run = c.execute(text("INSERT INTO model_runs (kind) VALUES ('movement') RETURNING run_id")).scalar()
        c.execute(text("INSERT INTO sim_starts VALUES (:r, 0, 'test start', :cell)"), {"r": run, "cell": cell})
        for hour, x in ((2, -81.68), (0, -81.70), (1, -81.69)):
            c.execute(text("INSERT INTO sim_points VALUES (:r, 0, 0, :h, ST_SetSRID(ST_Point(:x, 29.2), 4326))"),
                      {"r": run, "h": hour, "x": x})
    with eng.connect() as c:
        wkt = c.execute(text("SELECT ST_AsText(geom) FROM sim_tracks WHERE run_id = :r"), {"r": run}).scalar()
    assert wkt == "LINESTRING(-81.7 29.2,-81.69 29.2,-81.68 29.2)"