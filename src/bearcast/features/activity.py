"""Recent bear activity per hexagon: the closest honest answer to "where are bears right now?".

Counts public reports from the last 30 and 90 days before the newest call in the data:
  sightings          FWC calls classed as sightings
  conflicts          FWC conflict calls (property / pets & livestock / human safety)
  roadkill           bears hit by vehicles (FWC roadkill layer: carcasses + injured captures)
Repeat reports of the same event are dropped. Then a smoothed index spreads each hexagon's reports
to its neighbours, since a bear reported in one hexagon was probably also nearby:
  activity_index = own reports + 0.5 x reports in the 6 touching hexagons + 0.25 x the next ring (12)

Output: data/processed/activity_h3r7.parquet (+ .json summary)

Usage:
    python -m bearcast.features.activity
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from bearcast.features.history import ring_adjacency

log = logging.getLogger(__name__)
PROCESSED = Path("data/processed")
INTERIM = Path("data/interim")
RAW = Path("data/raw")
WINDOWS = (30, 90)


def to_cells(lat: pd.Series, lon: pd.Series, res: int) -> pd.Series:
    return pd.Series([h3.latlng_to_cell(a, b, res) for a, b in zip(lat, lon)], index=lat.index)


def roadkill_events(rk: pd.DataFrame) -> pd.DataFrame:
    date = pd.to_datetime(rk["RoadDate"]).fillna(pd.to_datetime(rk["ReportDate"]))
    date = date.dt.tz_localize(None) if getattr(date.dt, "tz", None) is not None else date
    out = pd.DataFrame({"date": date.dt.normalize(), "Latitude": rk["Latitude"], "Longitude": rk["Longitude"],
                        "kind": "roadkill"})
    return out[(out["date"].dt.year > 1900) & out["Latitude"].ne(0)].dropna()


def call_events(calls: pd.DataFrame) -> pd.DataFrame:
    c = calls[~calls["is_repeat"]]
    kind = np.select([c["is_conflict"], c["call_class"].eq("sighting")], ["conflict", "sighting"], "other")
    out = pd.DataFrame({"date": pd.to_datetime(c["date"]).dt.normalize(), "Latitude": c["Latitude"],
                        "Longitude": c["Longitude"], "kind": kind})
    return out[out["kind"] != "other"].dropna()


def recent_activity(events: pd.DataFrame, cells: list[str], end: pd.Timestamp, res: int = 7) -> pd.DataFrame:
    """events: date, Latitude, Longitude, kind. Returns one row per universe cell."""
    ev = events[events["date"] <= end].copy()
    ev["cell"] = to_cells(ev["Latitude"], ev["Longitude"], res)
    ev = ev[ev["cell"].isin(set(cells))]
    out = pd.DataFrame(index=pd.Index(cells, name="cell"))
    for w in WINDOWS:
        recent = ev[ev["date"] > end - pd.Timedelta(days=w)]
        counts = recent.groupby(["cell", "kind"]).size().unstack(fill_value=0)
        for k in ("sighting", "conflict", "roadkill"):
            out[f"{k}_{w}d"] = counts.get(k, pd.Series(dtype=int)).reindex(out.index).fillna(0).astype(np.int32)
        out[f"reports_{w}d"] = out[[f"{k}_{w}d" for k in ("sighting", "conflict", "roadkill")]].sum(axis=1)
    last = ev.groupby("cell")["date"].max().reindex(out.index)
    out["last_report"] = last
    out["days_since_report"] = (end - last).dt.days.astype("Int64")

    a1, a2 = ring_adjacency(cells, 1), ring_adjacency(cells, 2)
    own = out["reports_90d"].to_numpy(dtype=np.float32)
    out["activity_index"] = (own + 0.5 * (a1 @ own) + 0.25 * (a2 @ own)).astype(np.float32)
    out["activity_level"] = pd.cut(out["activity_index"], [-np.inf, 0, 1, 3, np.inf],
                                   labels=["none", "low", "moderate", "high"])
    return out.reset_index()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, default=7)
    p.add_argument("--dir", type=Path, default=PROCESSED)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tag = f"h3r{args.res}"

    cells = pd.read_parquet(args.dir / f"cells_{tag}.parquet", columns=["cell"])["cell"].astype(str).tolist()
    calls = pd.read_parquet(INTERIM / "calls_clean.parquet",
                            columns=["date", "Latitude", "Longitude", "call_class", "is_conflict", "is_repeat"])
    rk = pd.read_parquet(RAW / "fwc_roadkill.parquet", columns=["RoadDate", "ReportDate", "Latitude", "Longitude"])
    events = pd.concat([call_events(calls), roadkill_events(rk)], ignore_index=True)
    end = events.loc[events["kind"] != "roadkill", "date"].max()
    out = recent_activity(events, cells, end, args.res)

    path = args.dir / f"activity_{tag}.parquet"
    out.to_parquet(path, index=False)
    summary = {"as_of": str(end.date()),
               "reports_last_30d": {k: int(out[f"{k}_30d"].sum()) for k in ("sighting", "conflict", "roadkill")},
               "reports_last_90d": {k: int(out[f"{k}_90d"].sum()) for k in ("sighting", "conflict", "roadkill")},
               "newest_roadkill": str(events.loc[events["kind"] == "roadkill", "date"].max().date()),
               "hexagons_by_level": out["activity_level"].value_counts().to_dict()}
    path.with_suffix(".json").write_text(json.dumps(summary, indent=2))
    for k, v in summary.items():
        log.info("%-20s %s", k, v)
    log.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())