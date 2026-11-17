-- 001_init.sql: core tables for BearCast.
-- Hexagons are the spatial backbone: every per-cell table references hexagons(cell).
-- Model outputs are versioned by model_runs.run_id, so old forecasts and layers are kept and comparable.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------- spatial backbone
CREATE TABLE hexagons (
    cell        text PRIMARY KEY,                 -- H3 index (resolution 7, ~5 km2)
    res         smallint NOT NULL DEFAULT 7,
    geom        geometry(Polygon, 4326) NOT NULL,
    centroid    geometry(Point, 4326)   NOT NULL,
    range_rank  smallint,                         -- FWC range: 0 outside, 1 rare .. 4 frequent
    range_label text,
    area_km2    real
);
CREATE INDEX hexagons_geom_gix     ON hexagons USING gist (geom);
CREATE INDEX hexagons_centroid_gix ON hexagons USING gist (centroid);

-- descriptive landscape facts for map pop-ups (the full model features stay in Parquet)
CREATE TABLE landscape (
    cell               text PRIMARY KEY REFERENCES hexagons (cell) ON DELETE CASCADE,
    hu_den_2020        real,     -- housing units per km2
    pop_den_2020       real,     -- people per km2
    wui_intermix_share real,
    cons_share         real,     -- share inside conservation land
    dist_bigcons_km    real,
    lc_natural         real,     -- share natural land cover
    lc_forested_wetland real,
    lc_urban           real,     -- urban high + low
    road_km_per_km2    real,
    max_aadt           real      -- busiest road, vehicles per day
);

-- ---------------------------------------------------------------- observations
CREATE TABLE calls (
    incident_id  text PRIMARY KEY,
    report_date  date    NOT NULL,
    call_class   text    NOT NULL,     -- attractant_property, pets_livestock, human_safety, sighting, ...
    reason       text,
    is_conflict  boolean NOT NULL,
    is_repeat    boolean NOT NULL,     -- same day + place + reason as an earlier call
    county       text,
    source_layer text,
    cell         text REFERENCES hexagons (cell),   -- NULL if outside the hexagon universe
    geom         geometry(Point, 4326) NOT NULL
);
CREATE INDEX calls_date_idx ON calls (report_date);
CREATE INDEX calls_cell_idx ON calls (cell);
CREATE INDEX calls_geom_gix ON calls USING gist (geom);

-- sparse: only cell-months with at least one call are stored (missing = 0)
CREATE TABLE monthly_counts (
    cell           text REFERENCES hexagons (cell) ON DELETE CASCADE,
    month          date NOT NULL,
    n_conflict     integer NOT NULL DEFAULT 0,
    n_encounter    integer NOT NULL DEFAULT 0,
    n_sighting     integer NOT NULL DEFAULT 0,
    n_bear_welfare integer NOT NULL DEFAULT 0,
    PRIMARY KEY (cell, month)
);
CREATE INDEX monthly_counts_month_idx ON monthly_counts (month);

-- ---------------------------------------------------------------- model bookkeeping
CREATE TABLE model_runs (
    run_id      serial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('forecast', 'activity', 'bear_layers', 'movement')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    git_commit  text,
    data_through date,            -- newest data month/date the run used
    params      jsonb NOT NULL DEFAULT '{}',
    metrics     jsonb NOT NULL DEFAULT '{}',
    notes       text
);

-- ---------------------------------------------------------------- conflict forecast
CREATE TABLE forecasts (
    run_id             integer REFERENCES model_runs (run_id) ON DELETE CASCADE,
    month              date NOT NULL,     -- the month being forecast
    cell               text REFERENCES hexagons (cell) ON DELETE CASCADE,
    expected_calls     real NOT NULL,
    risk_rank          integer NOT NULL,  -- 1 = highest
    risk_pct           real NOT NULL,     -- 0.01 = top 1%
    tier               text NOT NULL CHECK (tier IN ('very high', 'high', 'elevated', 'low')),
    no_recent_conflict boolean NOT NULL,
    never_conflict     boolean NOT NULL,
    emerging_rank      integer,           -- among hexagons with no conflict in 12 months
    emerging_pct       real,
    PRIMARY KEY (run_id, cell)
);
CREATE INDEX forecasts_month_idx ON forecasts (month);
CREATE INDEX forecasts_tier_idx  ON forecasts (run_id, tier);

-- how a forecast did once the month's calls are in
CREATE TABLE forecast_scores (
    run_id              integer PRIMARY KEY REFERENCES model_runs (run_id) ON DELETE CASCADE,
    month               date NOT NULL,
    conflicts           integer NOT NULL,
    expected_total      real NOT NULL,
    capture_1pct        real,
    capture_5pct        real,
    emerging_capture_10 real,
    scored_at           timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- recent activity
CREATE TABLE activity (
    run_id            integer REFERENCES model_runs (run_id) ON DELETE CASCADE,
    as_of             date NOT NULL,
    cell              text REFERENCES hexagons (cell) ON DELETE CASCADE,
    sighting_30d      integer NOT NULL, conflict_30d integer NOT NULL, roadkill_30d integer NOT NULL,
    sighting_90d      integer NOT NULL, conflict_90d integer NOT NULL, roadkill_90d integer NOT NULL,
    reports_90d       integer NOT NULL,
    last_report       date,
    days_since_report integer,
    activity_index    real NOT NULL,
    activity_level    text NOT NULL CHECK (activity_level IN ('none', 'low', 'moderate', 'high')),
    PRIMARY KEY (run_id, cell)
);

-- ---------------------------------------------------------------- bear layers (habitat, corridors, roads)
CREATE TABLE bear_layers (
    run_id             integer REFERENCES model_runs (run_id) ON DELETE CASCADE,
    cell               text REFERENCES hexagons (cell) ON DELETE CASCADE,
    habitat_score      real,
    habitat_pct        real,
    resistance         real,
    core               text,         -- population core name, '' if not in a core
    corridor_value     real,         -- 0 = on a best route; 0.05 = route 5% costlier
    corridor_pair      text,
    in_corridor        boolean,
    on_best_path       boolean,
    road_risk          real,         -- habitat x traffic (roadkill-risk layer)
    crossing_risk      real,
    roadkill_2010_2025 integer,
    PRIMARY KEY (run_id, cell)
);

CREATE TABLE corridor_paths (
    run_id             integer REFERENCES model_runs (run_id) ON DELETE CASCADE,
    pair               text NOT NULL,
    cost               real,
    length_km          real,
    busy_road_hexagons integer,
    geom               geometry(LineString, 4326),
    PRIMARY KEY (run_id, pair)
);
CREATE INDEX corridor_paths_geom_gix ON corridor_paths USING gist (geom);

-- ---------------------------------------------------------------- simulated movement
CREATE TABLE sim_starts (
    run_id      integer REFERENCES model_runs (run_id) ON DELETE CASCADE,
    start_id    integer NOT NULL,
    start_label text NOT NULL,
    start_cell  text REFERENCES hexagons (cell),
    PRIMARY KEY (run_id, start_id)
);

CREATE TABLE sim_points (
    run_id   integer NOT NULL,
    start_id integer NOT NULL,
    sim      smallint NOT NULL,
    hour     smallint NOT NULL,
    geom     geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (run_id, start_id, sim, hour),
    FOREIGN KEY (run_id, start_id) REFERENCES sim_starts (run_id, start_id) ON DELETE CASCADE
);

CREATE TABLE sim_reach (
    run_id   integer NOT NULL,
    start_id integer NOT NULL,
    cell     text NOT NULL,          -- may lie outside the hexagon universe, so no FK
    p_7d     real NOT NULL,
    p_14d    real NOT NULL,
    PRIMARY KEY (run_id, start_id, cell),
    FOREIGN KEY (run_id, start_id) REFERENCES sim_starts (run_id, start_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------- convenience views for the app
-- newest run of each kind
CREATE VIEW latest_runs AS
SELECT DISTINCT ON (kind) * FROM model_runs ORDER BY kind, run_id DESC;

CREATE VIEW latest_forecast AS
SELECT f.*, h.geom
FROM forecasts f
JOIN hexagons h USING (cell)
WHERE f.run_id = (SELECT run_id FROM latest_runs WHERE kind = 'forecast');

CREATE VIEW latest_activity AS
SELECT a.*, h.geom
FROM activity a
JOIN hexagons h USING (cell)
WHERE a.run_id = (SELECT run_id FROM latest_runs WHERE kind = 'activity');

CREATE VIEW latest_bear_layers AS
SELECT b.*, h.geom
FROM bear_layers b
JOIN hexagons h USING (cell)
WHERE b.run_id = (SELECT run_id FROM latest_runs WHERE kind = 'bear_layers');

-- simulated tracks as lines, built from the hourly points
CREATE VIEW sim_tracks AS
SELECT p.run_id, p.start_id, s.start_label, p.sim,
       ST_MakeLine(p.geom ORDER BY p.hour) AS geom
FROM sim_points p
JOIN sim_starts s USING (run_id, start_id)
GROUP BY p.run_id, p.start_id, s.start_label, p.sim;