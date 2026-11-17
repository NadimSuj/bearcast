import geopandas as gpd
import h3
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from bearcast.features import landcover as LC

A = h3.latlng_to_cell(29.0, -81.6, 7)
B = sorted(h3.grid_ring(A, 1))[0]


def _cells():
    polys = [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in (A, B)]
    return gpd.GeoDataFrame({"cell": [A, B]}, geometry=polys, crs=4326)


def _tif(tmp_path, cells, fill_a, fill_b):
    """30 m raster in Florida Albers: pixels inside A get fill_a, everything else fill_b."""
    c = cells.to_crs(3087)
    minx, miny, maxx, maxy = c.total_bounds
    tr = from_origin(minx - 300, maxy + 300, 30, 30)
    w, h = int((maxx - minx + 600) / 30), int((maxy - miny + 600) / 30)
    from rasterio.features import rasterize
    inside_a = rasterize([(c.geometry.iloc[0], 1)], out_shape=(h, w), transform=tr, fill=0).astype(bool)
    arr = np.where(inside_a, fill_a, fill_b).astype("int32")
    path = tmp_path / "clc.tif"
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="int32",
                       crs="EPSG:3087", transform=tr, nodata=0) as dst:
        dst.write(arr, 1)
    return path


@pytest.mark.parametrize("code,group", [
    (1110000, "hardwood_upland"), (1311000, "pine_scrub_upland"), (1833300, "tree_plantation"),
    (1833000, "agriculture"), (1821000, "urban_low"), (1822000, "urban_high"),
    (2210000, "forested_wetland"), (2110000, "open_wetland"), (8000000, "water"),
    (1800000, "other"), (7000000, "other"),
    (1110, "hardwood_upland"), (18333, "tree_plantation"), (18331, "agriculture"),
    (1830, "rural_developed"), (1800, "other"), (9100, "other"),
])
def test_code_groups(code, group):
    assert LC.GROUP_NAMES[LC.code_group(code)] == group


def test_shares_per_hexagon_and_nodata(tmp_path):
    cells = _cells()
    tif = _tif(tmp_path, cells, fill_a=2210000, fill_b=0)       # A: forested wetland, B: no data
    f = LC.landcover_features(cells, tif)
    assert f.loc[A, "lc_forested_wetland"] == pytest.approx(1.0)
    assert f.loc[A, "lc_natural"] == pytest.approx(1.0)
    assert f.loc[A, "lc_diversity"] == pytest.approx(0.0)
    assert f.loc[B, "lc_nodata"] == pytest.approx(1.0)
    assert np.isnan(f.loc[B, "lc_diversity"])
    row = f.loc[A, [f"lc_{g}" for g in LC.GROUP_NAMES] + ["lc_nodata"]]
    assert row.sum() == pytest.approx(1.0)


def test_small_windows_give_same_counts(tmp_path):
    cells = _cells()
    tif = _tif(tmp_path, cells, fill_a=1821000, fill_b=1311000)
    big = LC.count_pixels(cells, tif, window=4096)
    small = LC.count_pixels(cells, tif, window=37)                # many windows cutting through hexagons
    assert (big == small).all() and big.sum() > 0
    expected_px = cells.to_crs(3087).geometry.area.values / 900
    assert big.sum(axis=1) == pytest.approx(expected_px, rel=0.02)