"""Ask the FWC server how many calls each calls layer has per year (no download).

Usage:  python scripts/probe_calls_years.py
"""

import requests

MAP = "https://gis.myfwc.com/mapping/rest/services"
LAYERS = {
    "historical (InteractivePublicBearMap/3)": f"{MAP}/Projects_FWC/InteractivePublicBearMap/MapServer/3",
    "recent (InteractivePublicBearMap/2)": f"{MAP}/Projects_FWC/InteractivePublicBearMap/MapServer/2",
    "open data (Black_Bear_Related_Calls_in_Florida/13)": f"{MAP}/Open_Data/Black_Bear_Related_Calls_in_Florida/MapServer/13",
}

# ArcGIS servers accept different date syntaxes; try each until one works.
DATE_SYNTAXES = [
    "ReportDate >= DATE '{a}' AND ReportDate < DATE '{b}'",
    "ReportDate >= timestamp '{a} 00:00:00' AND ReportDate < timestamp '{b} 00:00:00'",
]


def count(url: str, where: str):
    r = requests.get(f"{url}/query", params={"where": where, "returnCountOnly": "true", "f": "json"}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        return None
    return j.get("count")


def count_year(url: str, year: int):
    for syntax in DATE_SYNTAXES:
        n = count(url, syntax.format(a=f"{year}-01-01", b=f"{year + 1}-01-01"))
        if n is not None:
            return n
    return "query error"


for name, url in LAYERS.items():
    print(name)
    print(f"  total: {count(url, '1=1')}")
    for y in range(2020, 2027):
        print(f"  {y}: {count_year(url, y)}")