"""Find out how (or whether) this machine can open the CLC land-cover raster.

Prints: library versions, what the .lyrx layer files point at, every way of opening
the .gdb we try (with the real error message), and, for whatever opens, the size,
resolution, CRS and the most common class codes.

Usage:
    python scripts/probe_gdb_raster.py data/raw/landscape/landcover > probe_gdb.txt 2>&1
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling


def versions() -> None:
    print("== Versions")
    print("rasterio", rasterio.__version__, "| GDAL", rasterio.__gdal_version__)
    try:
        import pyogrio
        print("pyogrio", pyogrio.__version__, "| pyogrio GDAL", pyogrio.__gdal_version_string__)
    except Exception as e:  # noqa: BLE001
        print("pyogrio: not available:", e)
    with rasterio.Env() as env:
        drivers = env.drivers()
    has = "OpenFileGDB" in drivers
    print("rasterio has the OpenFileGDB raster driver:", has)
    if not has:
        print("  -> rasterio's built-in GDAL can't read File Geodatabase rasters; a full GDAL install is needed")
    gi = shutil.which("gdalinfo")
    print("system gdalinfo:", gi or "not installed")
    if gi:
        print(" ", subprocess.run([gi, "--version"], capture_output=True, text=True).stdout.strip())


def system_gdalinfo(target: str) -> None:
    gi = shutil.which("gdalinfo")
    if not gi:
        return
    print(f"\n-- system gdalinfo {target}")
    r = subprocess.run([gi, target], capture_output=True, text=True)
    lines = (r.stdout + r.stderr).splitlines()
    keep = [l for l in lines if not l.startswith(("  ", "    ")) or "SUBDATASET" in l or "Pixel Size" in l]
    print("\n".join(keep[:60]))


def walk_json(obj, found: dict) -> None:
    """Collect dataset names and (value, label) pairs from an ArcGIS Pro .lyrx file."""
    if isinstance(obj, dict):
        for k in ("dataset", "datasetType", "workspaceConnectionString", "workspaceFactory"):
            if k in obj and isinstance(obj[k], str):
                found.setdefault(k, set()).add(obj[k])
        if "label" in obj and ("values" in obj or "value" in obj):
            vals = obj.get("values") or [obj.get("value")]
            found.setdefault("classes", []).append((vals, obj["label"]))
        for v in obj.values():
            walk_json(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_json(v, found)


def read_lyrx(folder: Path) -> set[str]:
    datasets: set[str] = set()
    for f in sorted(folder.glob("*.lyrx")):
        print(f"\n== {f.name}")
        found: dict = {}
        try:
            walk_json(json.loads(f.read_text(encoding="utf-8-sig")), found)
        except Exception as e:  # noqa: BLE001
            print("  could not parse:", e)
            continue
        for k in ("dataset", "datasetType", "workspaceFactory", "workspaceConnectionString"):
            if k in found:
                print(f"  {k}: {sorted(found[k])}")
        datasets |= found.get("dataset", set())
        classes = found.get("classes", [])
        print(f"  {len(classes)} labelled classes; first 15:")
        for vals, label in classes[:15]:
            print(f"    {vals} -> {label}")
    return datasets


def summarize(src) -> None:
    print(f"  size {src.width} x {src.height} | res {src.res} | dtype {src.dtypes[0]} | nodata {src.nodata}")
    print(f"  crs {src.crs.to_string() if src.crs else None}")
    scale = max(1, min(src.width, src.height) // 2000)
    arr = src.read(1, out_shape=(src.height // scale, src.width // scale), resampling=Resampling.nearest)
    vals = arr[arr != src.nodata] if src.nodata is not None else arr.ravel()
    top = Counter(np.asarray(vals).ravel().tolist()).most_common(20)
    print(f"  most common values (sampled 1/{scale}): {top}")
    try:
        cmap_note = "has colormap" if src.colormap(1) else ""
    except Exception:  # noqa: BLE001
        cmap_note = ""
    print(f"  tags: {dict(list(src.tags().items())[:10])} {cmap_note}")


def try_open(label: str, path: str) -> bool:
    print(f"\n-- try {label}: {path}")
    try:
        with rasterio.open(path) as src:
            subs = src.subdatasets
            if subs:
                print(f"  opened as container with {len(subs)} raster subdatasets:")
                for s in subs:
                    print("   ", s)
                ok = False
                for s in subs:
                    ok |= try_open("subdataset", s)
                return ok
            print("  OPENED")
            summarize(src)
            return True
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {e}")
        return False


def main(folder: str) -> int:
    folder = Path(folder)
    versions()
    gdbs = sorted(folder.glob("*.gdb"))
    print("\n== Folder", folder, "\n  .gdb folders:", [g.name for g in gdbs])
    datasets = read_lyrx(folder)
    ok = False
    for gdb in gdbs:
        print(f"\n== {gdb.name}: {sum(1 for _ in gdb.iterdir())} files inside, "
              f"{sum(f.stat().st_size for f in gdb.iterdir()) / 1e9:.2f} GB")
        system_gdalinfo(str(gdb))
        ok |= try_open("plain path", str(gdb))
        ok |= try_open("OpenFileGDB prefix", f'OpenFileGDB:"{gdb}"')
        for name in sorted(datasets | {gdb.stem}):
            ok |= try_open(f"dataset {name}", f'OpenFileGDB:"{gdb}":{name}')
    print("\n== RESULT:", "at least one way worked" if ok else "nothing opened the raster")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/landscape/landcover"))