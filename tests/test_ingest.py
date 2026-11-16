"""Offline tests: fake the FWC ArcGIS and GBIF HTTP responses."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest

from bearcast.ingest import arcgis, fwc, gbif

LAYER = "https://example.test/arcgis/rest/services/X/MapServer/3"
N = 4_500       # > 2 chunks at maxRecordCount 2000
MAX_RC = 2000


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _feature(oid):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-81.5 + oid * 1e-5, 29.0]},
        "properties": {
            "OBJECTID": oid,
            "ReportDate": 1_700_000_000_000 + oid * 86_400_000,
            "CallNature": "Garbage",
            "BMU": "Central",
        },
    }


class FakeArcGISSession:
    """Mimics layer metadata, returnIdsOnly, and objectIds POST queries."""

    def __init__(self, drop_ids=()):
        self.headers = {}
        self.calls = []
        self.drop_ids = set(drop_ids)

    def request(self, method, url, params=None, data=None, timeout=None):
        self.calls.append((method, url, params, data))
        if url == LAYER:
            return FakeResp({
                "name": "Black Bear Calls Historical",
                "maxRecordCount": MAX_RC,
                "fields": [
                    {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                    {"name": "ReportDate", "type": "esriFieldTypeDate"},
                    {"name": "CallNature", "type": "esriFieldTypeString"},
                    {"name": "BMU", "type": "esriFieldTypeString"},
                ],
            })
        if url == f"{LAYER}/query" and params and params.get("returnIdsOnly") == "true":
            return FakeResp({"objectIdFieldName": "OBJECTID", "objectIds": list(range(N, 0, -1))})
        if url == f"{LAYER}/query" and method == "POST":
            ids = [int(i) for i in data["objectIds"].split(",")]
            assert len(ids) <= MAX_RC, "chunk exceeded maxRecordCount"
            assert data["outSR"] == "4326"
            return FakeResp({"type": "FeatureCollection",
                             "features": [_feature(i) for i in ids if i not in self.drop_ids]})
        raise AssertionError(f"unexpected call {method} {url} {params}")


def test_fetch_layer_gets_every_record_in_chunks(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    s = FakeArcGISSession()
    gdf = arcgis.fetch_layer(LAYER, session=s)

    assert len(gdf) == N
    assert gdf["OBJECTID"].is_unique
    assert gdf.crs.to_epsg() == 4326
    posts = [c for c in s.calls if c[0] == "POST"]
    assert len(posts) == 3  # 2000 + 2000 + 500
    # date column converted from epoch ms
    assert str(gdf["ReportDate"].dtype).startswith("datetime64")
    assert gdf["ReportDate"].dt.year.min() == 2023


def test_fetch_layer_raises_when_records_missing(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    s = FakeArcGISSession(drop_ids={17, 3001})
    with pytest.raises(arcgis.ArcGISError, match="2 object IDs missing"):
        arcgis.fetch_layer(LAYER, session=s)


def test_arcgis_error_payload_is_retried_then_raised(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)

    class ErrSession:
        headers = {}
        n = 0

        def request(self, *a, **k):
            self.n += 1
            return FakeResp({"error": {"code": 500, "message": "boom"}})

    s = ErrSession()
    with pytest.raises(arcgis.ArcGISError):
        arcgis.get_layer_info(LAYER, session=s)
    assert s.n == 4


def test_fwc_registry_urls_look_right():
    for name, spec in fwc.FWC_LAYERS.items():
        assert spec["url"].startswith("https://gis.myfwc.com/")
        assert spec["url"].rsplit("/", 1)[-1].isdigit(), name


def test_fwc_download_writes_parquet_and_manifest(tmp_path, monkeypatch):
    import geopandas as gpd
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    s = FakeArcGISSession()
    monkeypatch.setitem(fwc.FWC_LAYERS, "bear_calls", {"url": LAYER, "note": "test"})
    monkeypatch.setattr(fwc, "fetch_layer", lambda url: arcgis.fetch_layer(url, session=s))

    path = fwc.download("bear_calls", tmp_path)
    back = gpd.read_parquet(path)
    assert len(back) == N
    manifest = json.loads(path.with_suffix(".manifest.json").read_text())
    assert manifest["n_features"] == N
    assert "FWC" in manifest["attribution"]


# ---------------- GBIF ----------------

def _occ(key, dataset_key="d-museum", inst="UF", name="UF Mammals"):
    return {"key": key, "datasetKey": dataset_key, "datasetName": name, "institutionCode": inst,
            "basisOfRecord": "PRESERVED_SPECIMEN", "occurrenceStatus": "PRESENT",
            "eventDate": "2015-06-01", "year": 2015, "month": 6, "day": 1,
            "decimalLatitude": 29.1, "decimalLongitude": -81.6, "license": "CC_BY_4_0",
            "issues": ["COORDINATE_ROUNDED"]}


class FakeGBIFSession:
    def __init__(self, records):
        self.records = records
        self.params_seen = []

    def get(self, url, params=None, timeout=None):
        self.params_seen.append(params)
        off, lim = params["offset"], params["limit"]
        chunk = self.records[off:off + lim]
        return FakeResp({"count": len(self.records), "results": chunk,
                         "endOfRecords": off + lim >= len(self.records)})


def test_gbif_pages_and_filters_inaturalist(monkeypatch):
    monkeypatch.setattr(gbif.time, "sleep", lambda *_: None)
    recs = [_occ(i) for i in range(500)]
    recs += [_occ(1000 + i, dataset_key=gbif.INATURALIST_DATASET_KEY,
                  inst="iNaturalist", name="iNaturalist Research-grade Observations") for i in range(250)]
    recs += [_occ(5000, dataset_key="other", inst="iNaturalist", name="mirror")]  # safety net case
    s = FakeGBIFSession(recs)

    got = gbif.fetch_occurrences(session=s)
    assert len(got) == len(recs)
    assert s.params_seen[0]["gadmGid"] == "USA.10_1"
    assert s.params_seen[0]["taxonKey"] == 2433407

    kept = [r for r in got if not gbif.is_inaturalist(r)]
    assert len(kept) == 500
    gdf = gbif.to_geodataframe(kept)
    assert len(gdf) == 500 and gdf.crs.to_epsg() == 4326
    assert not gdf["datasetName"].str.contains("iNaturalist").any()
