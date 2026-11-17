import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import box

from bearcast.features import panel as P

START, END = pd.Timestamp("2015-01-01"), pd.Timestamp("2015-03-01")


def _calls(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["Latitude"], df["Longitude"] = df["lat"], df["lon"]
    df["call_class"] = df.get("call_class", pd.Series(index=df.index, dtype=object)).fillna("attractant_property")
    df["is_conflict"] = df["call_class"].isin(["attractant_property", "pets_livestock", "human_safety"])
    df["is_encounter"] = df["is_conflict"] | (df["call_class"] == "sighting")
    df["is_repeat"] = df.get("is_repeat", pd.Series(index=df.index, dtype=object)).fillna(False).astype(bool)
    df["IncidentID"] = [f"IN-{i}" for i in range(len(df))]
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326)


def test_last_complete_month():
    assert P.last_complete_month(pd.Timestamp("2026-10-31")) == pd.Timestamp("2026-10-01")
    assert P.last_complete_month(pd.Timestamp("2026-11-15")) == pd.Timestamp("2026-10-01")


def test_filters_repeats_low_precision_and_default_geocodes():
    rows = [
        {"date": "2015-01-05", "lat": 29.123456, "lon": -81.654321},                     # kept
        {"date": "2015-01-05", "lat": 29.123456, "lon": -81.654321, "is_repeat": True},  # repeat
        {"date": "2015-01-06", "lat": 29.12, "lon": -81.65},                             # 2 decimals
        {"date": "2014-12-31", "lat": 29.223456, "lon": -81.754321},                     # before window
    ]
    rows += [{"date": "2015-02-01", "lat": 29.1685, "lon": -81.6427}] * 10              # default geocode
    ev, rep = P.filter_events(_calls(rows), START, END)
    assert rep["calls_in_window"] == 13
    assert rep["dropped_repeat"] == 1
    assert rep["dropped_low_precision"] == 1
    assert rep["dropped_default_geocode"] == 10
    assert rep["events_kept"] == 1 and len(ev) == 1


def test_panel_is_zero_filled_and_counts_are_right():
    rows = [
        {"date": "2015-01-05", "lat": 29.123456, "lon": -81.654321},
        {"date": "2015-01-20", "lat": 29.123457, "lon": -81.654322},
        {"date": "2015-03-02", "lat": 29.123456, "lon": -81.654321, "call_class": "sighting"},
        {"date": "2015-02-10", "lat": 30.400001, "lon": -86.600001, "call_class": "bear_welfare"},
    ]
    ev, _ = P.filter_events(_calls(rows), START, END)
    rng = gpd.GeoDataFrame({"Label": ["Frequent"]}, geometry=[box(-82.0, 28.0, -81.95, 28.05)], crs=4326)
    rr = P.range_cells(rng, 7)
    panel, cells = P.build_panel(ev, rr, START, END, 7)

    call_cell = h3.latlng_to_cell(29.123456, -81.654321, 7)
    assert len(rr) > 0 and set(rr.values()) == {4}
    # range cells + two call cells outside the range, each with its 6 neighbours (ring=1)
    assert panel["cell"].nunique() == len(rr) + 2 * 7    
    assert len(panel) == panel["cell"].nunique() * 3        # 3 months each
    jan = panel[(panel.cell == call_cell) & (panel.month == START)].iloc[0]
    assert jan.n_conflict == 2 and jan.n_encounter == 2 and jan.n_sighting == 0
    mar = panel[(panel.cell == call_cell) & (panel.month == END)].iloc[0]
    assert mar.n_sighting == 1 and mar.n_conflict == 0 and mar.n_encounter == 1
    assert panel["n_bear_welfare"].sum() == 1
    assert cells.set_index("cell").loc[call_cell, "range_label"] == "outside"
    assert cells["range_label"].value_counts()["frequent"] == len(rr)


def test_split_labels():
    m = pd.Series(pd.to_datetime(["2023-12-01", "2024-01-01", "2024-12-01", "2025-01-01"]))
    assert list(P.label_split(m)) == ["train", "val", "val", "test"]

    

def test_ring_zero_keeps_only_range_and_call_cells():
    rows = [{"date": "2015-01-05", "lat": 29.123456, "lon": -81.654321}]
    ev, _ = P.filter_events(_calls(rows), START, END)
    panel0, _ = P.build_panel(ev, {}, START, END, 7, ring=0)
    panel1, cells1 = P.build_panel(ev, {}, START, END, 7, ring=1)
    assert panel0["cell"].nunique() == 1
    assert panel1["cell"].nunique() == 7
    assert (cells1["n_conflict_total"] == 0).sum() == 6