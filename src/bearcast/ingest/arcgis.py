"""Generic downloader for ArcGIS REST MapServer/FeatureServer layers.

Strategy (robust to the 2,000-record cap on FWC services):
  1. GET layer metadata (?f=json) for field types and maxRecordCount.
  2. GET all object IDs (returnIdsOnly=true) - this call is not capped.
  3. POST objectIds in chunks <= maxRecordCount, asking for GeoJSON in WGS84.
  4. Convert esriFieldTypeDate columns (epoch milliseconds, UTC) to timestamps.

Fetching by object ID avoids the ordering/skip bugs that resultOffset paging
can have on some servers, and lets us verify we got every record.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd
import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
USER_AGENT = "bearcast/0.0.1 (black bear conflict research project)"


@dataclass
class LayerInfo:
    url: str
    name: str
    max_record_count: int
    oid_field: str
    date_fields: list[str]
    raw: dict[str, Any]


class ArcGISError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _request(session: requests.Session, method: str, url: str, *, retries: int = 4, **kwargs) -> dict:
    """HTTP call with retry/backoff. ArcGIS returns HTTP 200 with an 'error' key on failure."""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    for attempt in range(1, retries + 1):
        try:
            resp = session.request(method, url, **kwargs)
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict) and "error" in payload:
                raise ArcGISError(f"{url}: {payload['error']}")
            return payload
        except (requests.RequestException, ValueError, ArcGISError) as exc:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            log.warning("Request failed (%s); retry %d/%d in %ds", exc, attempt, retries, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def get_layer_info(layer_url: str, session: requests.Session | None = None) -> LayerInfo:
    session = session or _session()
    meta = _request(session, "GET", layer_url, params={"f": "json"})
    fields = meta.get("fields") or []
    oid_field = meta.get("objectIdField") or next(
        (f["name"] for f in fields if f.get("type") == "esriFieldTypeOID"), "OBJECTID"
    )
    date_fields = [f["name"] for f in fields if f.get("type") == "esriFieldTypeDate"]
    return LayerInfo(
        url=layer_url,
        name=meta.get("name", layer_url.rsplit("/", 2)[-2]),
        max_record_count=int(meta.get("maxRecordCount") or 1000),
        oid_field=oid_field,
        date_fields=date_fields,
        raw=meta,
    )


def get_object_ids(layer_url: str, where: str = "1=1", session: requests.Session | None = None) -> list[int]:
    session = session or _session()
    payload = _request(
        session, "GET", f"{layer_url}/query",
        params={"where": where, "returnIdsOnly": "true", "f": "json"},
    )
    ids = payload.get("objectIds") or []
    return sorted(ids)


def _fetch_chunk(session: requests.Session, layer_url: str, ids: list[int]) -> list[dict]:
    payload = _request(
        session, "POST", f"{layer_url}/query",
        data={
            "objectIds": ",".join(map(str, ids)),
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        },
    )
    return payload.get("features") or []


def fetch_layer(
    layer_url: str,
    where: str = "1=1",
    chunk_size: int | None = None,
    pause_s: float = 0.25,
    session: requests.Session | None = None,
) -> gpd.GeoDataFrame:
    """Download every feature of an ArcGIS layer as a WGS84 GeoDataFrame."""
    session = session or _session()
    info = get_layer_info(layer_url, session)
    ids = get_object_ids(layer_url, where, session)
    if not ids:
        log.warning("%s returned zero object IDs", info.name)
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    size = min(chunk_size or info.max_record_count, info.max_record_count)
    log.info("%s: %d features, fetching in chunks of %d", info.name, len(ids), size)

    features: list[dict] = []
    for start in range(0, len(ids), size):
        chunk = ids[start:start + size]
        features.extend(_fetch_chunk(session, layer_url, chunk))
        log.info("  %d / %d", min(start + size, len(ids)), len(ids))
        time.sleep(pause_s)  # be polite to a state agency server

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

    # Completeness check: every requested ID should come back exactly once.
    if info.oid_field in gdf.columns:
        got = set(gdf[info.oid_field].astype("int64"))
        missing = set(ids) - got
        if missing:
            raise ArcGISError(f"{info.name}: {len(missing)} object IDs missing from download")
        if gdf[info.oid_field].duplicated().any():
            raise ArcGISError(f"{info.name}: duplicate object IDs in download")

    # ArcGIS dates are epoch milliseconds in UTC.
    for col in info.date_fields:
        if col in gdf.columns:
            gdf[col] = pd.to_datetime(gdf[col], unit="ms", utc=True, errors="coerce")

    gdf.attrs["source_url"] = layer_url
    gdf.attrs["layer_name"] = info.name
    return gdf
