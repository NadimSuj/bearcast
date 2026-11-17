"""API tests: the FastAPI app on top of the tiny fake dataset from test_load (needs TEST_DATABASE_URL)."""

import h3
import pytest
from fastapi.testclient import TestClient

from bearcast.api.app import create_app
from bearcast.db.load import load_all
from test_load import CELLS, CENTER, data, eng  # noqa: F401  (shared fixtures: fresh test DB + fake outputs)


@pytest.fixture(scope="module")
def client(eng, data):  # noqa: F811
    d, interim = data
    load_all(eng, processed=d, interim=interim)
    return TestClient(create_app(eng))


def test_health_and_runs(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert set(r.json()["runs"]) == {"forecast", "activity", "bear_layers", "movement"}
    runs = client.get("/api/runs").json()
    assert {x["kind"] for x in runs} == {"forecast", "activity", "bear_layers", "movement"}


def test_point_nearby_top(client):
    lat, lng = h3.cell_to_latlng(CENTER)
    r = client.get("/api/point", params={"lat": lat, "lon": lng})
    assert r.status_code == 200 and r.json()["cell"] == CENTER and r.json()["tier"] == "very high"
    assert client.get("/api/point", params={"lat": 25.0, "lon": -80.3}).status_code == 404   # no hexagon there
    assert client.get("/api/point", params={"lat": 45.0, "lon": -81.7}).status_code == 422   # not in Florida
    near = client.get("/api/nearby", params={"lat": lat, "lon": lng, "km": 5, "limit": 3}).json()
    assert near[0]["cell"] == CENTER and len(near) == 3
    assert client.get("/api/top", params={"n": 1}).json()[0]["cell"] == CENTER


def test_layers_geojson_h3_and_bbox(client):
    assert set(client.get("/api/layers").json()) == {"forecast", "emerging", "activity", "habitat", "corridors", "road_risk"}
    gj = client.get("/api/layers/habitat").json()
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == len(CELLS)
    rows = client.get("/api/layers/habitat", params={"format": "h3"}).json()
    assert len(rows) == len(CELLS) and set(rows[0]) == {"cell", "habitat_pct", "core"}
    lat, lng = h3.cell_to_latlng(CENTER)
    tiny = f"{lng - 0.001},{lat - 0.001},{lng + 0.001},{lat + 0.001}"
    assert len(client.get("/api/layers/habitat", params={"bbox": tiny}).json()["features"]) == 1
    assert client.get("/api/layers/habitat", params={"bbox": "1,2,3"}).status_code == 422
    assert client.get("/api/layers/nope").status_code == 404
    # corridors: cells inside a population core (core != '') and unreachable cells are left out
    cor = client.get("/api/layers/corridors", params={"format": "h3"}).json()
    assert len(cor) == len(CELLS) - 2 and CELLS[0] not in {r["cell"] for r in cor}


def test_corridors_sims_scores(client):
    paths = client.get("/api/corridors/paths").json()
    assert paths["features"][0]["properties"]["pair"] == "A - B"
    starts = client.get("/api/sims").json()
    assert starts[0]["start_label"] == "core: Ocala" and starts[0]["lat"] is not None
    sim = client.get(f"/api/sims/{starts[0]['start_id']}").json()
    assert len(sim["tracks"]["features"]) == 2                                 # 2 simulated bears
    assert len(sim["tracks"]["features"][0]["geometry"]["coordinates"]) == 3   # 3 hours each
    assert sim["reach"][0]["cell"] == CENTER
    assert client.get("/api/sims/999").status_code == 404
    assert client.get("/api/scores").json() == []

def test_cors_allows_deployed_origin_from_env(monkeypatch, client):
    monkeypatch.setenv("CORS_ORIGINS", "https://bearcast.onrender.com/, https://example.org")
    c = TestClient(create_app(client.app.state.engine))
    ok = c.get("/api/runs", headers={"Origin": "https://bearcast.onrender.com"})
    assert ok.headers.get("access-control-allow-origin") == "https://bearcast.onrender.com"
    local = c.get("/api/runs", headers={"Origin": "http://localhost:5173"})
    assert local.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = c.get("/api/runs", headers={"Origin": "https://not-allowed.example"})
    assert "access-control-allow-origin" not in other.headers
