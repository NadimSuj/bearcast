# Phase 2 decisions: Postgres + PostGIS backend

## Step 1: database and schema
- Local Postgres + PostGIS via Homebrew; connection settings in `.env` (git-ignored), template in `.env.example`.
- Migrations: numbered SQL files in `db/migrations/`, applied once each in a transaction by `bearcast.db.migrate`, tracked in `schema_migrations`.
- `hexagons` (H3 res 7, PostGIS polygon + centroid, GiST indexes) is the spatial backbone; per-hexagon tables reference it.
- Every model output is tied to a `model_runs` row (kind: forecast / activity / bear_layers / movement), so history is kept and forecasts can be scored later. Views (`latest_forecast`, `latest_activity`, `latest_bear_layers`) always show the newest run.
- `monthly_counts` is sparse (non-zero hexagon-months only). Full model features stay in Parquet; the DB keeps only what the app shows or the monthly job needs.
- Simulated movement stored as hourly points; the `sim_tracks` view builds ordered lines with `ST_MakeLine`.
- Tests use a separate `bearcast_test` database (wiped on each run), set by `TEST_DATABASE_URL`.