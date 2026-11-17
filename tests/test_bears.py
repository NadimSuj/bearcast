import h3
import numpy as np
import pandas as pd
import pytest

from bearcast.bears.habitat import boyce, build_design, clean_gps, clean_radio, era_column, weighted_auc
from bearcast.features.activity import recent_activity


def test_clean_radio_drops_relocations_bad_fix_types_and_thins_to_one_per_day():
    t = pd.DataFrame({
        "Source": ["Wekiva", "Wekiva", "Wekiva", "Wekiva", "Nuisance Relocation", "Wekiva"],
        "Bear_ID": ["A", "A", "A", "A", "B", "A"],
        "Date": pd.to_datetime(["1994-01-01", "1994-01-01", "1994-01-02", "1994-01-03", "1994-01-01", "1994-01-04"]),
        "Loc_Type": ["Telemetry", None, "Capture", "Mortality - killed by vehicle", "Telemetry", "Visual"],
        "Latitude": 28.8, "Longitude": -81.45,
    })
    out = clean_radio(t)
    assert set(out["date"].dt.day) == {1, 4}               # day 1 thinned to one fix; capture + mortality dropped
    assert (out["group"] == "wekiva").all() and len(out) == 2


def test_clean_gps_filters_dop_and_thins():
    c = pd.DataFrame({"Bear": [1, 1, 1, 2], "GMT_DATE": pd.to_datetime(["2012-01-01"] * 3 + ["2012-01-01"]),
                      "LATITUDE": 30.0, "LONGITUDE": -82.0, "DOP": [2.0, 3.0, 11.0, 4.0]})
    out = clean_gps(c)
    assert len(out) == 2 and set(out["bear"]) == {"g_1", "g_2"}


def test_era_column():
    assert list(era_column(pd.Series([1985, 1995, 2006, 2013, 2020]))) == \
        ["hu_den_1990", "hu_den_2000", "hu_den_2010", "hu_den_2010", "hu_den_2020"]


def test_design_weights_each_bear_equally_and_availability_surrounds_fixes():
    c0 = h3.latlng_to_cell(29.0, -81.6, 7)
    universe = set(h3.grid_disk(c0, 10))
    lat, lon = h3.cell_to_latlng(c0)
    fixes = pd.DataFrame({"bear": ["a"] * 4 + ["b"], "group": "g", "date": pd.Timestamp("2000-06-01"),
                          "lat": lat, "lon": lon})
    d = build_design(fixes, universe, rings=2)
    used = d[d["used"] == 1]
    assert used.groupby("bear")["w"].sum().round(6).tolist() == [1.0, 1.0]
    av = d[d["used"] == 0]
    assert set(av["cell"]) == set(h3.grid_disk(c0, 2)) and av["w"].sum() == pytest.approx(2.0)


def test_weighted_auc_and_boyce():
    rng = np.random.default_rng(0)
    s = np.concatenate([rng.normal(1, 1, 2000), rng.normal(0, 1, 2000)])
    u = np.r_[np.ones(2000), np.zeros(2000)].astype(int)
    w = np.ones(4000)
    auc = weighted_auc(s, u, w)
    assert 0.72 < auc < 0.8                              # theory: Phi(1/sqrt(2)) = 0.76
    assert weighted_auc(-s, u, w) == pytest.approx(1 - auc)
    rho, top = boyce(s, u, w)
    assert rho > 0.9 and top > 2


def test_recent_activity_counts_windows_and_smooths_to_neighbours():
    c0 = h3.latlng_to_cell(29.0, -81.6, 7)
    cells = sorted(h3.grid_disk(c0, 2))
    lat, lon = h3.cell_to_latlng(c0)
    end = pd.Timestamp("2026-10-31")
    ev = pd.DataFrame({"date": pd.to_datetime(["2026-10-20", "2026-09-01", "2026-05-01", "2026-11-05"]),
                       "Latitude": lat, "Longitude": lon, "kind": ["sighting", "conflict", "roadkill", "sighting"]})
    a = recent_activity(ev, cells, end).set_index("cell")
    assert a.loc[c0, "sighting_30d"] == 1 and a.loc[c0, "conflict_30d"] == 0     # after-end report ignored
    assert a.loc[c0, "reports_90d"] == 2 and a.loc[c0, "days_since_report"] == 11
    n1 = sorted(h3.grid_ring(c0, 1))[0]
    n2 = sorted(h3.grid_ring(c0, 2))[0]
    assert a.loc[c0, "activity_index"] == 2 and a.loc[n1, "activity_index"] == 1 and a.loc[n2, "activity_index"] == 0.5