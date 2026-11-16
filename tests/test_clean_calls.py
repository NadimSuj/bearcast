import geopandas as gpd
import pandas as pd

from bearcast.clean import calls as C

# The 41 raw values seen in the real historical layer during Phase 0 EDA.
OBSERVED = [
    "in garbage", "in area", "in yard", "property damage", "dead bear", "in tree", "sick/injured",
    "killed animal", "in feeder", "in feed", "sick/injured bear", "threatened human", "illegal activity",
    "threatened animal", "in screened porch/patio", "in unscreened porch/patio", "attacked animal",
    "apiary", "in building", "in open garage", "in building/tent/vehicle", "other",
    "human threatened bear", "unintentionally approached human", "in crops", "injured animal",
    "animal threatened bear", "in hog trap", "animal attacked bear", "attacked human", "harvest (hunt)",
    "animal injured bear", "injured human", "research", "", "under structure", "confirmed sighting",
    "research project", "animal killed bear", "species information",
]


def _gdf(rows, recent=False):
    df = pd.DataFrame(rows)
    if recent:
        df = df.rename(columns={"Region": "FWCRegion", "ZipCode": "ZIPCode", "CallNature": "ReasonForCall"})
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.Longitude, df.Latitude), crs=4326)


def _row(i, date, reason="In Garbage", lat=29.0, lon=-81.5):
    return {"IncidentID": f"IN-{i}", "ReportDate": pd.Timestamp(date, tz="UTC"), "Region": "NC",
            "BMU": "Central", "County": "Lake", "City": "X", "ZipCode": "32000",
            "Latitude": lat, "Longitude": lon, "CallNature": reason}


def test_every_observed_reason_is_mapped():
    assert set(OBSERVED) <= set(C.CALL_CLASS)
    assert len(set(OBSERVED)) == 40  # 41 raw values collapse to 40 after case/whitespace normalizing


def test_midnight_utc_dates_are_not_shifted_back_a_day():
    hist = _gdf([_row(1, "2015-01-30"), _row(2, "2022-01-01")])
    out = C.clean(hist)
    assert list(out["date"].dt.strftime("%Y-%m-%d")) == ["2015-01-30", "2022-01-01"]
    assert list(out["year"]) == [2015, 2022]


def test_recent_layer_is_renamed_combined_and_wins_on_overlap():
    hist = _gdf([_row(1, "2022-12-31"), _row(2, "2022-06-01")])
    rec = _gdf([_row(2, "2022-06-01", reason="Property Damage"), _row(3, "2023-03-05", reason="In Yard")], recent=True)
    out = C.clean(hist, rec)
    assert len(out) == 3
    assert out.set_index("IncidentID").loc["IN-2", "source_layer"] == "recent"
    assert out.set_index("IncidentID").loc["IN-3", "call_class"] == "sighting"
    assert out["date"].max() == pd.Timestamp("2023-03-05")


def test_classes_flags_and_repeats():
    hist = _gdf([
        _row(1, "2020-10-01", "In Garbage"),
        _row(2, "2020-10-01", "in garbage "),         # repeat of 1 (same day/place/reason)
        _row(3, "2020-10-01", "In Yard"),             # same place, different reason -> not a repeat
        _row(4, "2020-10-02", "Threatened Human"),
        _row(5, "2020-10-03", "Dead Bear"),
        _row(6, "2020-10-04", "Something New"),
    ])
    out = C.clean(hist).set_index("IncidentID")
    assert out.loc["IN-1", "call_class"] == "attractant_property" and out.loc["IN-1", "is_conflict"]
    assert out.loc["IN-3", "is_encounter"] and not out.loc["IN-3", "is_conflict"]
    assert out.loc["IN-4", "call_class"] == "human_safety"
    assert not out.loc["IN-5", "is_encounter"]
    assert out.loc["IN-6", "call_class"] == "unmapped"
    assert out["is_repeat"].sum() == 1 and out.loc["IN-1", "repeat_group_size"] == 2



def test_new_recent_reasons_are_mapped():
    for r in ["unknown", "orphaned cub", "in trap", "killed human"]:
        assert r in C.CALL_CLASS


def test_open_layer_only_fills_the_gap_between_historical_and_recent():
    hist = _gdf([_row(1, "2022-12-31")])
    rec = _gdf([_row(10, "2025-01-02")], recent=True)
    # Open layer overlaps both ends, with different IDs for the same calls.
    opn = _gdf([_row(901, "2022-12-31"), _row(902, "2023-06-01"), _row(903, "2024-11-30"), _row(904, "2025-01-02")])
    out = C.clean(hist, rec, opn)
    assert sorted(out["IncidentID"]) == ["IN-1", "IN-10", "IN-902", "IN-903"]
    assert set(out.loc[out["source_layer"] == "open_gapfill", "date"].dt.year) == {2023, 2024}



def test_duplicate_ids_within_recent_keep_last_downloaded_copy():
    hist = _gdf([_row(1, "2022-12-31")])
    rec = _gdf([_row(7, "2026-03-01", reason="In Area"), _row(7, "2026-03-01", reason="In Garbage")], recent=True)
    out = C.clean(hist, rec).set_index("IncidentID")
    assert len(out) == 2
    assert out.loc["IN-7", "reason"] == "in garbage"