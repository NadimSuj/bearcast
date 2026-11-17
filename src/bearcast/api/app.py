"""BearCast API: a thin FastAPI layer over the database queries.

Run locally:
    uvicorn bearcast.api.app:app --reload
then open http://127.0.0.1:8000/docs for interactive documentation (try every endpoint in the browser).

Endpoints
  GET /api/health                       database reachable? which runs are loaded?
  GET /api/runs                         what each layer is based on ("as of" dates, key metrics)
  GET /api/point?lat=&lon=              everything about the hexagon containing a point (a map click)
  GET /api/nearby?lat=&lon=&km=&limit=  highest-risk hexagons near a point
  GET /api/top?n=&emerging=             statewide top hexagons (or emerging-risk ones)
  GET /api/layers                       available map layers
  GET /api/layers/{layer}?format=&bbox= a layer as GeoJSON (default) or H3 rows; bbox=west,south,east,north
  GET /api/corridors/paths              best corridor routes (GeoJSON lines)
  GET /api/sims                         simulation start points
  GET /api/sims/{start_id}              simulated tracks (GeoJSON) + reach shares for one start
  GET /api/scores                       how past forecasts did
"""

from __future__ import annotations

import os
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import Engine, text

from bearcast.db import engine as default_engine
from bearcast.db import queries as Q

FL_LAT = {"ge": 24.0, "le": 31.5}      # Florida-ish bounds, so typos get a clear 422 error
FL_LON = {"ge": -88.0, "le": -79.5}


def allowed_origins() -> list[str]:
    """Local dev servers, plus any comma-separated origins in CORS_ORIGINS (e.g. the deployed web map's address)."""
    extra = [o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    return ["http://localhost:5173", "http://localhost:3000", *extra]


def create_app(eng: Engine | None = None, cors_origins: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="BearCast API", version="0.1.0",
                  description="Florida black bear conflict forecasts, recent activity, habitat, corridors and "
                              "simulated movement. Data: Florida Fish and Wildlife Conservation Commission (FWC).")
    app.state.engine = eng
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins or allowed_origins(),
                       allow_methods=["GET"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=1000)    # map layers are big JSON; gzip shrinks them ~5-10x

    def db(request: Request) -> Engine:
        if request.app.state.engine is None:              # created on first use, so importing needs no database
            request.app.state.engine = default_engine()
        return request.app.state.engine

    def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
        if bbox is None:
            return None
        try:
            w, s, e, n = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(422, "bbox must be west,south,east,north")
        if not (w < e and s < n):
            raise HTTPException(422, "bbox must satisfy west < east and south < north")
        return w, s, e, n

    @app.get("/api/health")
    def health(eng: Engine = Depends(db)):
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ok", "runs": {r["kind"]: r["run_id"] for r in Q.latest_runs(eng)}}

    @app.get("/api/runs")
    def runs(eng: Engine = Depends(db)):
        return Q.latest_runs(eng)

    @app.get("/api/point")
    def point(lat: float = Query(..., **FL_LAT), lon: float = Query(..., **FL_LON), eng: Engine = Depends(db)):
        row = Q.at_point(eng, lat, lon)
        if row is None:
            raise HTTPException(404, "no hexagon here (outside the area BearCast covers)")
        return row

    @app.get("/api/nearby")
    def nearby(lat: float = Query(..., **FL_LAT), lon: float = Query(..., **FL_LON),
               km: float = Query(10, gt=0, le=50), limit: int = Query(10, ge=1, le=100), eng: Engine = Depends(db)):
        return Q.nearby(eng, lat, lon, km, limit)

    @app.get("/api/top")
    def top(n: int = Query(10, ge=1, le=500), emerging: bool = False, eng: Engine = Depends(db)):
        return Q.top_hexagons(eng, n, emerging)

    @app.get("/api/layers")
    def layers():
        return {name: {"properties": cols, "shown": where} for name, (_, cols, where) in Q.LAYERS.items()}

    @app.get("/api/layers/{layer}")
    def layer(layer: str, format: Literal["geojson", "h3"] = "geojson", bbox: str | None = None,
              eng: Engine = Depends(db)):
        if layer not in Q.LAYERS:
            raise HTTPException(404, f"unknown layer; choose from {sorted(Q.LAYERS)}")
        box = parse_bbox(bbox)
        return Q.layer_geojson(eng, layer, box) if format == "geojson" else Q.layer_rows(eng, layer, box)

    @app.get("/api/corridors/paths")
    def paths(eng: Engine = Depends(db)):
        return Q.corridor_paths(eng)

    @app.get("/api/sims")
    def sims(eng: Engine = Depends(db)):
        return Q.sim_starts(eng)

    @app.get("/api/sims/{start_id}")
    def sim(start_id: int, eng: Engine = Depends(db)):
        tracks = Q.sim_tracks(eng, start_id)
        if not tracks["features"]:
            raise HTTPException(404, "no simulation with that start_id")
        return {"tracks": tracks, "reach": Q.sim_reach(eng, start_id),
                "note": "Simulated typical movement over 14 days from Camp Blanding GPS rules; not real bears."}

    @app.get("/api/scores")
    def scores(eng: Engine = Depends(db)):
        return Q.forecast_scores(eng)

    return app


app = create_app()
