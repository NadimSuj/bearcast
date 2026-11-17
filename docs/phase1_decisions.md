# Phase 1 decisions

Status: **accepted** (2026-11-16).

## Step 1: the cell x month panel
- Window: 2015-01 to 2026-10 (142 months). 69,465 calls in the window; 68,067 events kept.
  Dropped: 591 repeats, 498 imprecise (< 3 decimals), 309 at suspected default points
  (a point with <= 4 decimals and >= 10 calls; Fort McCoy 29.1685,-81.6427 has 60).
- Resolution: H3 res 7 (~5 km2). Before the ring fix: 30,676 cells, 3,702 with any conflict,
  0.47% of cell-months > 0 (3.9% among active cells), max 20 in one cell-month.
  Res 6 for comparison: 4,468 cells, 2.4% (7.3% among active cells).
- Why res 7: neighbourhood scale is what residents need; the sparsity is workable for count models;
  wider context comes back as neighbour features in step 2.
- Concentration: the top 5% of res 7 cells hold 89.5% of conflicts. Evaluate by how many future
  conflicts fall in the top-ranked cells, not by accuracy.
- FWC "frequent" range: 28% of cells, 91.5% of conflicts. 1,110 conflicts (3.8%) fall outside the mapped range.
- Fix: the universe now adds a 1-cell ring around calls outside the range, so the model also sees quiet cells there.
- Validation: 2024 (val) was a quiet year; 2023 and 2025-26 were high. When modelling, validate on two
  folds (2023 high, 2024 low).
- Default-geocode rule kept (309 events, 0.4%). Some flagged 4-decimal Panhandle points may be real
  problem properties; revisit if needed.


## Step 1 result after the ring fix
- Universe: 31,069 cells (30,463 in range + 606 outside). Outside-range rate is now 1.83 conflicts per cell
  (was 5.21, inflated because only cells with calls were included).

## Step 2a: history features
- Forecast question: conflicts in a cell THIS month, using only information up to LAST month.
- Rule: every feature for month t uses only months before t (enforced by a test).
- 2015 is history only; feature rows start 2016-01. Training is now 2016-2023.
- 22 features: own-cell lags and sums, months since last conflict, encounters and bear-welfare calls,
  neighbour sums (ring 1 and 2), statewide level and year-over-year signal, season, range level, location.
- Scoring: % of each month's conflicts in the top 1/5/10% of cells. Simple baselines set the bar the ML model must beat.

## Step 2b: landscape features (housing, conservation, roads)

- `bearcast.features.landscape` → `data/processed/landscape_h3r7.parquet`, one row per cell, 15 static features.
- All area/length work in EPSG:3087 (Florida Albers, equal-area metres).
- Housing: SILVIS 2020 blocks assigned to the hex containing each block's interior point. Densities = units or people / hex km². WUI 2020 (`WUICLASS_2`) intermix/interface area shares. Vegetation % is area-weighted.
- Conservation: FNAI FLMA clipped per hex and merged within each hex (overlaps counted once). Distances from the hex centre to any conservation land and to areas of 10,000+ acres.
- Roads: FDOT basemap km/km², AADT × km per km², max AADT, distance to a road with 10,000+ AADT. AADT is 2025 only, so it is treated as static.
- Treated as static (no time dimension). Housing is 2020 and traffic 2025, so values may sit slightly "ahead" of 2016–2019 rows. Accepted because these change slowly.
- Land cover: CLC v4 **STATE** raster (77 classes, mapped consistently statewide; SITE detail is only partial). rasterio's bundled GDAL can't read File Geodatabase rasters, so it is converted once to a tiled GeoTIFF with Homebrew GDAL (`gdal_translate`). `bearcast.features.landcover` → `landcover_h3r7.parquet`: 13 group shares by code prefix, plus no-data share, natural-cover share and Shannon diversity. Each pixel counts toward the hexagon containing its centre.

## Step 3: first model (LightGBM, Poisson)

- Pixel values in the CLC STATE raster are plain 4–5 digit codes; grouping by leading digits handles that. 1830 (Rural, unspecified) → rural_developed. 1800, 7000, 9100 → other.
- Hexagons outside Florida (from the ring of neighbours) have no CLC or SILVIS coverage: lc_nodata ≈ 1 and zero housing. A handful of cells; the model can use lc_nodata as a flag.
- `bearcast.models.train_gbm`: LightGBM, Poisson objective, target = conflicts per hexagon-month.
- Folds: train ≤2022 → validate 2023 (high year); train ≤2023 → validate 2024 (low year). Test (2025+) is never loaded.
- Early stopping on the validation year, so validation scores are slightly optimistic; the test period stays clean for the final number.
- Two feature sets: history (22) vs full (history + 15 landscape + 16 land cover), to measure what the landscape adds.
- Metrics (`bearcast.models.evaluate`): capture@1%/5%, cold capture@1%/5% (hexagons with no conflict in the prior 12 months), Poisson deviance, pred/actual.
- Baselines: long-run average (conf_hist_rate) and last-12-months sum.

### Step 3 results (validation; test still untouched)

| fold | score | capture@1% | capture@5% | pred/actual |
|---|---|---|---|---|
| 2023 | long-run avg | 0.435 | 0.810 | 0.82 |
| 2023 | GBM full | 0.512 | 0.858 | 0.99 |
| 2024 | long-run avg | 0.436 | 0.814 | 1.13 |
| 2024 | GBM full | 0.476 | 0.844 | 0.97 |

- The GBM beats the baselines in both years. Its biggest gain is calibration across boom and bust years (statewide trend features); Poisson deviance is about 40% lower.
- The top features are cell history, then neighbours, then season; lat/lon add little, so the model isn't memorising the map.
- History-only vs full differ by about ±1 point, within noise. Likely cause: the long-run rate already encodes the landscape. Next: rank within cold/never-seen cells to test landscape where history is missing.

### Step 3b: diagnostics, landscape is kept

- Overall capture: full vs history-only is a tie (95% ranges straddle 0). Known hotspots dominate the top slots.
- Ranking within cold hexagons (no conflict in 12 months): full beats history by +3.1 pts at 5% [+1.1, +5.2] and +4.5 pts at 10% [+2.2, +7.0].
- Ranking within never-seen hexagons (no conflict since 2015), top 10%: long-run avg ≈ 13–14% (random), history-only 52–57%, **full 69–72%**, in both years.
- Decision: keep the full model. The app shows two views from the same predictions: overall next-month risk, and **emerging risk** (ranking among hexagons with no recent history).

### Step 3c: tuning round

- 8 settings × 2 folds, full features. Winner chosen by a rule fixed before the run: lowest mean Poisson deviance across folds. Saved to data/models/best_params_h3r7.json with its mean best round count.

### Step 3c result: tuning

- All 8 settings within ~3% on mean deviance; capture@5% 85.1–85.4%. The model isn't sensitive to these settings.
- Winner by the pre-registered rule: **Tweedie (variance power 1.2), 85 rounds**, mean deviance 0.0345 (base 0.0352).
- Tweedie under-predicts by ~8% (pred/actual 0.92). Fixed before the test: multiply predictions by 1 / (mean validation pred/actual) ≈ 1.08. Rankings are unchanged; only the counts move.

### Step 4: final test plan (written before running)

- Train on 2016-01 to 2024-12 with the winner's settings and fixed rounds; predict each month 2025-01 to 2026-10, one month ahead.
- Report overall / per-year / within cold and never-seen hexagons, and 95% month-bootstrap ranges (model vs long-run average; full vs history among never-seen hexagons).
- Run once. The report file locks it; no changes after seeing the result.

### Step 4 result: final test (run once, 2025-01 to 2026-10, 5,487 conflicts)

| score | capture@1% | capture@5% | never-seen, within@10% | pred/actual |
|---|---|---|---|---|
| long-run average | 0.450 | 0.803 | 0.109 | 0.81 |
| GBM history-only | 0.520 | 0.836 | 0.558 | 0.89 |
| **GBM full (final)** | **0.523** | **0.838** | **0.677** | **0.88** |

- Full vs long-run average: capture@1% +7.3 pts [+5.8, +8.9], better in 22/22 months; capture@5% +3.5 [+2.5, +4.4].
- Emerging risk: full vs history-only among never-seen hexagons (top 10%) +11.9 pts [+7.4, +16.9]. Matches validation (69–72%), so the landscape features are confirmed.
- Validation and test scores agree: no sign of overfitting from tuning.
- **Known limitation:** counts run low in record years (calibrated pred/actual 0.92 in 2025, 0.84 in 2026). Tree models can't predict above the range in their training data; 2025–26 statewide totals beat every training year. Rankings are unaffected.
- Planned fix (production only, judged on months after 2026-10): rolling calibration, i.e. scale each month's predictions by actual/predicted over the previous 12 months.
- Final model: data/models/gbm_h3r7_final_full.txt (Tweedie, 85 rounds, trained 2016–2024).

## Phase 1 summary
Pipeline: FWC calls → clean → H3 res-7 cell×month panel → history features (no peeking) + static landscape (SILVIS housing, FNAI conservation, FDOT roads/AADT, CLC v4 land cover) → LightGBM Tweedie → one-month-ahead risk per hexagon. Two products from one model: next-month risk (overall ranking) and emerging risk (ranking among hexagons with no recent conflicts).

### Step 5: production forecast

- `bearcast.models.predict` scores the month after the panel's last month with the final model.
- Rolling calibration: actual / raw prediction over the last 12 out-of-sample months, refreshed every month. Rankings are unaffected.
- Output per hexagon: expected calls, risk rank/percentile/tier (very high ≤1%, high ≤5%, elevated ≤20%), emerging-risk rank among hexagons with no conflict in 12 months.
- The model is not retrained monthly for now. A retraining schedule is decided in Phase 2.

## Phase 1.5 plan: bear layers (decided from the telemetry inspection)

- Habitat use (resource selection): radio tracking 1983–2007 (12 studies, 415 bears) + Camp Blanding GPS 2011–14 (16 bears).
  - Drop "Nuisance Relocation" bears and fixes of type Capture, Den, Den creep, Dropped collar, Nuisance, Mortality*, Removed, Injured.
  - At most one fix per bear per day; equal weight per bear.
  - Held-out groups: Ocala (3 studies), Osceola (2), Wekiva, South FL, Eglin, Apalachicola, Gulf coast (Chassahowitzka + Cedar Key), Camp Blanding.
  - Housing matched to the decade of each fix where SILVIS allows (1990/2000/2010).
- Movement: Camp Blanding GPS, 30-min fixes, all 3D/validated; ID = Bear (collars reused). Held-out bears for checking simulations.
- Roadkill: Carcass + Capture (vehicle strike) = crossing events, an independent check for corridors. Seasonal weighting from roadkill months (Oct–Dec peak, May–Jul secondary).
- Recent activity layer: sightings + conflicts + roadkill over the last 30/90 days, smoothed to neighbours.

### Phase 1 close-out: first real forecast (2026-11)

- 345 expected conflict calls statewide (Oct 2026 actual: 391); rolling calibration ×1.28 (last 12 months the raw model was 22% low).
- Top hexagons: Okaloosa/Walton coast (Fort Walton Beach, Destin, Navarre, Santa Rosa Beach), plus Lake/Orange and Wakulla. Matches the Oct 2026 actual calls.
- Busiest test month (Oct 2026): 88% of calls in the model's top 5%; 76% of quiet-hexagon conflicts inside the emerging-risk top 10%.
- Emerging-risk leaders all had conflicts more than a year ago; the app should offer a "never had a conflict" filter (`never_conflict`).

## Phase 1.5, step 1: recent activity + habitat use

- `bearcast.features.activity`: sightings / conflicts / roadkill in the last 30 and 90 days per hexagon; days since last report; activity index = own + 0.5 × ring-1 + 0.25 × ring-2.
- The FWC roadkill layer currently ends 2025-12-31, so the roadkill part of recent activity is empty until FWC updates it.
- `bearcast.bears.habitat`: resource selection function (LightGBM binary, used vs available within ~10 km, weighted equally per bear), landscape + land-cover features only, housing matched to the decade of each fix.
- Validation: leave-one-area-out over 8 groups (AUC, Boyce index, top-10% ratio), benchmarked against the FWC range map.

### Habitat model v1 result

- 39,460 daily fixes from 383 bears in 8 area groups (Ocala 164 bears; Camp Blanding GPS 16).
- Leave-one-area-out: AUC 0.75 (range 0.68–0.85) vs FWC range map 0.68; model better in 7/8 areas (worse in South FL: 0.68 vs 0.77). Boyce 0.88 vs 0.55.
- Statewide habitat percentile rises through FWC range classes: 0.37 / 0.41 / 0.45 / 0.54 / 0.59.
- Concern: dist_bigcons_km carries 40% of gain. Conservation layer is current, tracking is 1983–2014; land bought after/because of bear studies could make this circular. Wekiva top-10% ratio 0.009.
- Check (rule fixed before running): drop dist_cons_km, dist_bigcons_km, cons_share. Use the no-conservation model if mean held-out AUC is within 0.01 of v1 and no area drops more than 0.02; otherwise keep v1 and caveat.

### Habitat model: conservation check result

- Without the conservation features: mean held-out AUC 0.731 vs 0.751 (−0.020); Ocala −0.087, Camp Blanding −0.031. The rule says **keep v1**.
- Biggest loss is Ocala, protected since 1908, long before any tracking, so the conservation signal there is real, not circular.
- Wekiva top-10% ratio unchanged (0.009), so conservation features aren't the cause; diagnosed in notebook 06.
- Caveat on the map: conservation boundaries are current, so areas protected after the studies may be slightly overrated.

## Phase 1.5, step 2: corridors

- `bearcast.bears.corridors`: resistance from habitat_pct (negative exponential, c = 4, range 1–100); cores = FWC frequent range clusters of ≥100 hexagons; Linkage-Mapper-style corridor value per hexagon for each core's 2 cheapest neighbours; best-path lines saved as GeoParquet.
- Crossing risk = movement (1 in cores, exp(−value/0.05) outside) × scaled log AADT.
- Check: share of 2010–2025 roadkill in the top 5/10/20% of road hexagons, crossing risk vs traffic only vs habitat × traffic.