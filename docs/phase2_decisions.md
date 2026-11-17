# Phase 2 decisions: Postgres + PostGIS backend

## Step 1: database and schema
- Local Postgres + PostGIS via Homebrew; connection settings in `.env` (git-ignored), template in `.env.example`.
- Migrations: numbered SQL files in `db/migrations/`, applied once each in a transaction by `bearcast.db.migrate`, tracked in `schema_migrations`.
- `hexagons` (H3 res 7, PostGIS polygon + centroid, GiST indexes) is the spatial backbone; per-hexagon tables reference it.
- Every model output is tied to a `model_runs` row (kind: forecast / activity / bear_layers / movement), so history is kept and forecasts can be scored later. Views (`latest_forecast`, `latest_activity`, `latest_bear_layers`) always show the newest run.
- `monthly_counts` is sparse (non-zero hexagon-months only). Full model features stay in Parquet; the DB keeps only what the app shows or the monthly job needs.
- Simulated movement stored as hourly points; the `sim_tracks` view builds ordered lines with `ST_MakeLine`.
- Tests use a separate `bearcast_test` database (wiped on each run), set by `TEST_DATABASE_URL`.

## Step 2: loader and app queries
- `bearcast.db.load`: reference tables upserted (COPY into a temp table, then INSERT ... ON CONFLICT DO UPDATE); `monthly_counts` replaced (non-zero rows only); model outputs appended as new `model_runs`.
- Idempotent: each model load stores a SHA-256 fingerprint of its source files in `model_runs.params.source_sha`; the same files are never loaded twice. A new forecast file = a new run; `latest_*` views switch automatically; history is kept for scoring.
- One transaction per load step; infinite values (unreachable corridor cells) stored as NULL.
- Calls outside the hexagon universe are kept with `cell = NULL`.
- `bearcast.db.queries`: point lookup (ST_Contains), nearby ranking (ST_DWithin on geography), statewide top / emerging, and map layers as GeoJSON assembled in SQL (json_build_object + ST_AsGeoJSON).

## Step 3: monthly job
- `bearcast.jobs.monthly`: download (recent calls, gap-fill layer, roadkill) → clean → panel → features → load reference tables → score → predict → activity → load outputs. `--from` / `--only` / `--skip-download` to resume or run parts.
- Scoring in SQL (`forecast_scores`): conflicts vs expected, capture of the top 1% / 5% (rank ≤ ceil(k·N), same rule as the offline metric), emerging capture (top 10% of quiet hexagons). Only months with complete data; each forecast scored once.
- Rolling calibration: each scored month's raw predictions (calibrated ÷ factor) are appended to `oos_h3r7.parquet`, seeded from the final-test predictions; the forecast calibrates on its latest 12 months.
- The forecast is skipped when no new month is complete, so re-running is harmless. Late-reported calls for an already-forecast month don't trigger a new forecast; they show up in that month's score.
- The model itself is not retrained by the job; a retraining schedule (e.g. yearly, after a new final test) is a separate, deliberate step.

## Step 4: API
- `bearcast.api.app` (FastAPI, run with `uvicorn bearcast.api.app:app --reload`; docs at `/docs`).
- Endpoints: health, runs ("as of" dates), point lookup, nearby, top / emerging, map layers, corridor routes, simulations (tracks + reach), forecast scores.
- Map layers come in two formats: GeoJSON (built in SQL) or H3 rows (`?format=h3`, ~5× smaller; the front end draws hexagons from their IDs). `?bbox=west,south,east,north` limits to the visible area.
- Gzip on (JSON layers shrink 5–10×); CORS allows local front-end dev servers (ports 5173, 3000); lat/lon validated to Florida bounds (422 otherwise).
- DB engine created on first request (importing the app needs no database; tests inject the test database).