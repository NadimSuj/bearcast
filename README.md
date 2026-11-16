# bearcast: Florida black bear conflict forecasting

Predict where and when human–bear conflicts are likely in Florida, using FWC data,
non-iNaturalist occurrence records, and environmental covariates.

---

## Phase 0: get the data and understand it

**Exit criteria:** all raw data downloaded and reproducible with one command each,
tests passing, the EDA notebook run on real data, and the decisions table at the
bottom of the notebook filled in.

### Step 0. Tools and accounts (one time)

| What | Why | Link |
|---|---|---|
| Python 3.11+ | runtime | https://www.python.org/downloads/ |
| Git + a GitHub repo | version control | https://github.com/new |
| GBIF account (free) | optional now; needed later for citable DOI downloads | https://www.gbif.org/user/profile |
| Docker Desktop | not needed until Phase 2 (Postgres/PostGIS) | https://www.docker.com/products/docker-desktop/ |

### Step 1. Set up the environment

```bash
cd bearcast
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                              # 6 offline tests, should all pass
git init && git add . && git commit -m "Phase 0 scaffold"
```

If `geopandas` fails to install on your machine, use conda instead:
`conda create -n bearcast -c conda-forge python=3.12 geopandas pyarrow duckdb requests matplotlib jupyterlab pytest`, then `pip install -e .`.

### Step 2. Download the FWC bear layers

```bash
python -m bearcast.ingest.fwc --list   # see what's available
python -m bearcast.ingest.fwc --all    # takes a few minutes; ~40+ requests for the calls layer
```

The script writes `data/raw/fwc_<layer>.parquet` and a `.manifest.json` per layer (source URL, fetch time, row count).
It fetches by object ID in chunks of 2,000 (the server's cap) and **fails loudly if any record is missing**.

| Layer | Service (open in a browser to inspect fields) |
|---|---|
| bear_calls (1980–2025) | https://gis.myfwc.com/mapping/rest/services/Projects_FWC/InteractivePublicBearMap/MapServer/3 |
| roadkill | https://gis.myfwc.com/hosting/rest/services/Open_Data/Black_Bear_Roadkill_Locations/MapServer/12 |
| captures (1981–2024) | https://gis.myfwc.com/hosting/rest/services/Open_Data/Black_Bear_Capture_Locations/MapServer/9 |
| relocations | https://gis.myfwc.com/hosting/rest/services/Open_Data/Black_Bear_Relocation_Locations/MapServer/8 |
| telemetry_camp_blanding (GPS) | https://gis.myfwc.com/hosting/rest/services/Open_Data/Black_Bear_Telemetry_Camp_Blanding_WMA/MapServer/0 |
| telemetry_combined | https://gis.myfwc.com/hosting/rest/services/Open_Data/Black_Bear_Telemetry/MapServer/11 |
| range (polygons) | https://gis.myfwc.com/hosting/rest/services/Open_Data/Black_Bear_Range_Florida/MapServer/8 |

Fallback if the REST service misbehaves: download the shapefile from the
FWC Open Data hub: https://geodata.myfwc.com/datasets/black-bear-related-calls-in-florida

### Step 3. Download GBIF occurrences (no iNaturalist)

```bash
python -m bearcast.ingest.gbif
```

Pulls *Ursus americanus* (GBIF taxon key `2433407`) inside Florida (GADM `USA.10_1`) and drops the
iNaturalist Research-grade dataset (`50c9509d-22c7-4a22-a47d-8c48425ef4a7`), plus anything whose institution
or dataset name mentions iNaturalist. The manifest records how many were dropped and which datasets remain.

- Species page: https://www.gbif.org/species/2433407
- Occurrence API docs: https://www.gbif.org/developer/occurrence
- pygbif (for the download API later): https://pygbif.readthedocs.io/
- iNaturalist dataset being excluded: https://www.gbif.org/dataset/50c9509d-22c7-4a22-a47d-8c48425ef4a7

### Step 4. Manual downloads

| What | Where | Save to |
|---|---|---|
| SNAPSHOT USA camera traps 2019–2023 | https://datadryad.org/stash/dataset/doi:10.5061/dryad.k0p2ngfhn | `data/raw/snapshot_usa/` |
| SNAPSHOT USA 2024 | https://datadryad.org/dataset/doi:10.5061/dryad.bnzs7h4qf | `data/raw/snapshot_usa/` |

Read and note the key figures from these (no download needed):
- Bear Management Units + population per BMU: https://myfwc.com/wildlifehabitats/wildlife/bear/bmu/
- 2014–15 statewide abundance study: https://myfwc.com/research/wildlife/bear/statewide-abundance/
- 2023–2028 abundance study (in progress): https://myfwc.com/research/wildlife/bear/abundance-23-28/
- 2025 hunt results: https://myfwc.com/BearHunting

### Step 5. Run the EDA

```bash
jupyter lab notebooks/01_eda_bear_calls.ipynb
```

Run all cells, then fill in the **Phase 0 decisions** table at the end. Commit the notebook.

---

## Data notes (read before modeling)

- **Attribution:** FWC asks that products acknowledge the *Florida Fish and Wildlife Conservation Commission (FWC)* as the data source.
- **Coordinates:** FWC layers are stored in Florida GDL Albers (WKID 6439). The ingest script requests WGS84 (EPSG:4326). Reproject to a metric CRS (e.g. EPSG:3086 or 6439) before measuring distances or building a grid.
- **Dates:** ArcGIS dates are UTC epoch milliseconds. Convert to `America/New_York` before taking the calendar date.
- **`CallNature` is free text** with no coded values. You'll need to build your own mapping to classes.
- **Sightings reflect people as well as bears.** Plan for effort/bias covariates (housing density, roads) in Phase 1.
- **No iNaturalist data** anywhere in this project.

## Phase 1 preview: covariates (not needed yet)

| Feature | Source |
|---|---|
| Florida land cover (CLC v4.0) | https://myfwc.com/research/gis/regional-projects/cooperative-land-cover/ |
| Annual NLCD 1985–2024 | https://www.mrlc.gov/data |
| Conservation lands (FNAI) | https://www.fnai.org/publications/gis-data |
| Wildlife corridors (FEGN) | https://conservation.dcp.ufl.edu/fegn |
| Housing density / WUI | https://silvis.forest.wisc.edu/data/wui-change/ |
| Census API (key required) | https://www.census.gov/data/developers/data-sets/planning-database.2025.html |
| Roads + traffic (FDOT) | https://www.fdot.gov/statistics/gis/default.shtm |
| Daily climate (PRISM) | https://prism.oregonstate.edu/ |
| Drought (USDM) | https://droughtmonitor.unl.edu/DmData/DataDownload/ComprehensiveStatistics.aspx |
| NDVI (MODIS MOD13Q1) | https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD13Q1 |
| Fire perimeters (MTBS) | https://www.mtbs.gov/direct-download |

## Layout

```
bearcast/
├── src/bearcast/ingest/
│   ├── arcgis.py      # generic ArcGIS REST downloader (object-ID chunking, completeness check)
│   ├── fwc.py         # FWC layer registry + CLI
│   └── gbif.py        # GBIF occurrences, iNaturalist excluded
├── notebooks/01_eda_bear_calls.ipynb
├── tests/test_ingest.py   # offline tests with mocked APIs
└── data/raw/              # git-ignored; re-create with the commands above
```

The ingest functions return GeoDataFrames, so in Phase 2 you can point the same code at
`gdf.to_postgis(...)` instead of `to_parquet(...)`.
