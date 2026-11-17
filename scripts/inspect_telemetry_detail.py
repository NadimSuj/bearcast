"""Second look at the bear point layers: study areas, real GPS fix intervals, quality flags, roadkill subtypes.

Usage:
    python scripts/inspect_telemetry_detail.py > telemetry_detail.txt 2>&1
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", "GeoSeries.notna", UserWarning)
RAW = Path("data/raw")


def counts(s: pd.Series, n: int = 25) -> str:
    return s.astype(str).str.strip().replace("", "(blank)").value_counts(dropna=False).head(n).to_string()


def hhmm_to_hours(t: pd.Series) -> pd.Series:
    """'930' / 930 / '09:30' / '6:00:50' -> hours since midnight."""
    s = t.astype(str).str.strip()
    colon = s.str.contains(":")
    out = pd.Series(np.nan, index=t.index)
    parts = s[colon].str.split(":", expand=True).apply(pd.to_numeric, errors="coerce")
    if len(parts):
        out[colon] = parts[0] + parts[1] / 60 + (parts[2].fillna(0) / 3600 if 2 in parts else 0)
    num = pd.to_numeric(s[~colon], errors="coerce")
    out[~colon] = (num // 100) + (num % 100) / 60
    return out


def gaps(df: pd.DataFrame, bear: str, date: str, time: str) -> None:
    ts = pd.to_datetime(df[date]).dt.tz_localize(None) + pd.to_timedelta(hhmm_to_hours(df[time]), unit="h")
    g = pd.DataFrame({"bear": df[bear], "ts": ts}).dropna().sort_values(["bear", "ts"])
    h = g.groupby("bear")["ts"].diff().dt.total_seconds().div(3600).dropna()
    h = h[h > 0]
    q = np.percentile(h, [10, 25, 50, 75, 90])
    print(f"  real hours between fixes (10/25/50/75/90th): " + " / ".join(f"{v:.2f}" for v in q))
    print(f"  share of gaps <= 1.1h {np.mean(h <= 1.1):.0%}, <= 2.1h {np.mean(h <= 2.1):.0%}, "
          f"<= 4.1h {np.mean(h <= 4.1):.0%}, <= 24h {np.mean(h <= 24):.0%}; unparsed times: {ts.isna().sum()}")
    fixes_day = g.groupby([g["bear"], g["ts"].dt.date]).size()
    print(f"  fixes per bear-day: median {fixes_day.median():.0f}, 90th pct {fixes_day.quantile(0.9):.0f}")


def main() -> int:
    t = pd.read_parquet(RAW / "fwc_telemetry_combined.parquet")
    print("=" * 80 + "\ntelemetry_combined")
    by = t.groupby("Source").agg(bears=("Bear_ID", "nunique"), fixes=("Bear_ID", "size"),
                                 first=("Year", "min"), last=("Year", "max"),
                                 lat=("Latitude", "median"), lon=("Longitude", "median"))
    print("\nby study area (Source):\n" + by.sort_values("fixes", ascending=False).to_string())
    for c in ["Researcher", "Loc_Type", "Telem_Type", "Activity", "Habitat"]:
        print(f"\n{c}:\n{counts(t[c], 15)}")
    sexes = t.drop_duplicates("Bear_ID")
    print(f"\nbears by Sex:\n{counts(sexes['Sex'])}\n\nbears by AgeClass:\n{counts(sexes['AgeClass'])}")
    print("\nTime column samples:", t["Time"].astype(str).drop_duplicates().head(12).tolist())
    gaps(t, "Bear_ID", "Date", "Time")
    per = t.groupby("Bear_ID").size()
    print(f"  bears with >= 30 fixes: {(per >= 30).sum()}, >= 100: {(per >= 100).sum()}")
    zero = (t["Latitude"].fillna(0) == 0) | (t["Longitude"].fillna(0) == 0)
    print(f"  rows with missing/zero coordinates: {zero.sum()}")

    c = pd.read_parquet(RAW / "fwc_telemetry_camp_blanding.parquet")
    print("\n" + "=" * 80 + "\ntelemetry_camp_blanding")
    print("GMT_TIME samples:", c["GMT_TIME"].astype(str).head(8).tolist())
    for col in ["NAV", "VALIDATED"]:
        print(f"\n{col}:\n{counts(c[col])}")
    print(f"\nDOP: {c['DOP'].describe(percentiles=[0.5, 0.9, 0.99]).round(2).to_dict()}")
    print(f"\nfixes per bear:\n{c.groupby('Bear').size().describe().round(0).to_dict()}")
    print(f"\nbear <-> collar pairs:\n{c.groupby(['Bear', 'Collar']).size().to_string()}")
    gaps(c, "Bear", "GMT_DATE", "GMT_TIME")

    r = pd.read_parquet(RAW / "fwc_roadkill.parquet")
    print("\n" + "=" * 80 + "\nroadkill")
    print(f"SubType:\n{counts(r['SubType'])}")
    print(f"\nOrigCap:\n{counts(r['OrigCap'])}")
    print(f"\nrows with ReportDate 1900-01-01: {(pd.to_datetime(r['ReportDate']).dt.year == 1900).sum()}")
    rd = pd.to_datetime(r["RoadDate"])
    print(f"\nroadkill by calendar month:\n{rd.dt.month.value_counts().sort_index().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())