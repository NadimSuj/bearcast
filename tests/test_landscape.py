import geopandas as gpd
import h3
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from bearcast.features import landscape as L

A = h3.latlng_to_cell(29.0, -81.6, 7)
B = sorted(h3.grid_ring(A, 1))[0]


def _cells():
    polys = [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in (A, B)]
    g = gpd.GeoDataFrame({"cell": [A, B]}, geometry=polys, crs=4326).to_crs(L.CRS)
    g["area_km2"] = g.geometry.area / 1e6
    return g


def test_housing_sums_units_per_cell_and_wui_shares():
    cells = _cells()
    ca, cb = cells.geometry.centroid.iloc[0], cells.geometry.centroid.iloc[1]
    blocks = gpd.GeoDataFrame({
        "HU2010": [5, 10, 20, 5], "HU2020": [10, 20, 30, 5], "POP2020": [20, 40, 60, 10],
        "WUICLASS_2": ["Low_Dens_Intermix", "Med_Dens_Interface", "Non-WUI", "Low_Dens_Intermix"],
        "VEG2019PC": [80.0, 40.0, 10.0, 90.0], "Shape_Area": [1e6, 1e6, 1e6, 2e6],
    }, geometry=[ca, ca, ca, cb], crs=L.CRS)
    h = L.housing_features(cells, blocks)
    area_a = cells.set_index("cell").loc[A, "area_km2"]
    assert h.loc[A, "hu_2020"] == 60 and h.loc[B, "hu_2020"] == 5
    assert h.loc[A, "hu_den_2020"] == pytest.approx(60 / area_a)
    assert h.loc[A, "wui_intermix_share"] == pytest.approx(1 / area_a)
    assert h.loc[A, "wui_interface_share"] == pytest.approx(1 / area_a)
    assert h.loc[A, "veg_pct_2019"] == pytest.approx((80 + 40 + 10) / 3)
    assert h.loc[A, "hu_growth_2010_2020"] == pytest.approx(np.log1p(60) - np.log1p(35))


def test_conservation_share_and_distances():
    cells = _cells()
    cons = gpd.GeoDataFrame({"MANAME": ["Big forest"], "TOTACRES": [50_000.0]},
                            geometry=[cells.geometry.iloc[0]], crs=L.CRS)  # covers cell A exactly
    c = L.conservation_features(cells, cons)
    assert c.loc[A, "cons_share"] == pytest.approx(1.0, abs=1e-6)
    assert c.loc[B, "cons_share"] == pytest.approx(0.0, abs=1e-6)
    assert c.loc[A, "dist_cons_km"] == 0 and c.loc[A, "dist_bigcons_km"] == 0
    assert 0.5 < c.loc[B, "dist_cons_km"] < 2.0   # centre of B to the shared edge (~1.2 km)


def test_road_length_and_traffic_split_between_cells():
    cells = _cells()
    ca, cb = cells.geometry.centroid.iloc[0], cells.geometry.centroid.iloc[1]
    line = LineString([ca, cb])                     # centre to centre: half in each cell
    routes = gpd.GeoDataFrame({"ROADWAY": ["1"]}, geometry=[line], crs=L.CRS)
    aadt = gpd.GeoDataFrame({"AADT": [20_000]}, geometry=[line], crs=L.CRS)
    r = L.road_features(cells, routes, aadt)
    half_km = line.length / 2 / 1000
    area = cells.set_index("cell")["area_km2"]
    assert r.loc[A, "road_km_per_km2"] == pytest.approx(half_km / area[A], rel=1e-3)
    assert r.loc[B, "traffic_vkm_per_km2"] == pytest.approx(20_000 * half_km / area[B], rel=1e-3)
    assert r.loc[A, "max_aadt"] == 20_000 and r.loc[A, "dist_major_road_km"] == 0


def test_overlapping_conservation_areas_are_not_double_counted():
    cells = _cells()
    hexa = cells.geometry.iloc[0]
    cons = gpd.GeoDataFrame({"MANAME": ["Forest", "Easement inside forest"], "TOTACRES": [50_000.0, 500.0]},
                            geometry=[hexa, hexa.buffer(-300)], crs=L.CRS)
    c = L.conservation_features(cells, cons)
    assert c.loc[A, "cons_share"] == pytest.approx(1.0, abs=1e-6)