# Phase 0 decisions

Status: **accepted** (2026-11-16). Based on the historical calls layer (80,331 calls).
Re-check after calls_recent (2023+) is cleaned.

## Findings
- Historical layer ends 2022-12-31. 2023+ is in a separate Calls Recent layer (MapServer/2) with renamed fields.
- IncidentID is unique per call, so it can't group calls into events. 996 rows are same-day/same-spot/same-reason repeats.
- Dates are date-only at 00:00 UTC. Use the UTC date (v0.1 shifted every date back a day).
- Reporting ramps until ~2012, plateaus ~5,000-5,900/yr from 2015. 2014 dips to ~2,300 (likely a data gap).
- Seasonality: low Feb-Mar, peak October.
- Call reasons are a fixed list of 41 values, mapped to 6 classes.
- Calls concentrate in Central BMU. Calls measure bears meeting people, not bear density.
- Telemetry: 16 bears, 123,772 validated GPS fixes, 2011-2014 (Camp Blanding / O2O corridor).
- GBIF without iNaturalist = 99 museum/fossil records from 1942-1997. Dropped.

## Call classes (historical counts)
| Class | Calls | Flags |
|---|---:|---|
| attractant_property | 36,213 | conflict, encounter |
| pets_livestock | 2,994 | conflict, encounter |
| human_safety | 1,007 | conflict, encounter |
| sighting | 31,417 | encounter |
| bear_welfare | 7,721 | separate signal |
| admin_other | 979 | excluded |

## Decisions
1. Training window: 2015 to latest. Skip 2014; use 2012-2013 for a robustness check.
2. Time step: month.
3. Spatial unit: H3 resolution 7 (~5 km2); fall back to resolution 6 (~36 km2) if too sparse.
4. Primary target: count of is_conflict events per cell-month (repeats removed). Secondary: is_encounter.
5. Bear-welfare calls: separate mortality/roads signal; cross-check with roadkill.
6. Default geocodes (many years + many reasons at one point): exclude from the res 7 model.
7. Relocations: excluded from any presence/habitat data.
8. GBIF: dropped. Presence data = FWC captures/telemetry + SNAPSHOT USA (check FL sites in Phase 1).
9. Phase 1 must include bias features (housing density, population, roads).


## Update (v0.3, after adding the recent layer)
- Combined calls: 93,793 (historical 80,331 + recent 13,462), 1980-10-02 to 2026-10-31.
- GAP: historical ends 2022-12-31, recent starts 2025-01-02. Filling 2023-2024 from the Open Data layer (MapServer/13), gap dates only.
- Recent layer runs ~37% above the 2015-2022 average (2025 = 7,450; 2026 heading for ~7,500 vs ~5,440).
  Real increase or recording change? Unknown until 2023-2024 are filled. Model must include a year/regime term.
- New reasons mapped: unknown -> admin_other, orphaned cub -> bear_welfare, in trap -> attractant_property,
  killed human -> human_safety (1 record, verify).
- Repeats: 643 rows -> drop.
- Precision: 224 calls with <= 2 decimals (>= ~1 km error) -> exclude from the res 7 model.
- Default-geocode suspects: Fort McCoy 29.1685,-81.6427 (58 dead bear, 2015-2026) and 29.1783,-81.709 (18 in area) -> flag.
- Handled layers (historical 11,704) = roadkill + captures + relocations (11,752). Use the handled layers only.
  handled_historical runs to 2025-12-05 and handled_recent starts 2025-01-02 -> dedupe 2025 in Phase 1.
- Telemetry: 30-min fixes for 15 bears, 2 h for bear 155. Resample to 2 h for a common interval, or drop 155 at 30 min.
- Fallback if 2023-2024 can't be filled: train on 2015-2022 and test on 2025-2026 as an out-of-time holdout.


## Phase 0 final (2026-11-16)
- Calls: 106,285 = historical (1980-2022) + Open Data gap fill (2023-2024, 12,492 rows) + recent (2025 to 2026-10-31). No missing years.
- Calls per year: 2015-2022 between 4,982 and 5,932; 2023 7,054; 2024 5,438; 2025 7,450; 2026 6,012 (Jan-Oct).
- The Open Data layer (same system as historical) shows 2025 = 7,404, matching the recent layer's 7,450.
  So the high years are real swings, not a recording change. Year-to-year drivers (acorn/mast, drought) are Phase 1 features.
  No separate "regime" term needed.
- Recent layer had 903 duplicate IncidentIDs (2026). Dedupe keeps the last-downloaded copy (deterministic since v0.3). See notebook 02, cell 18.
- No placeholder dates: Jan 1 has 6-10 calls/yr; calls by day of month are flat.
- "killed human" (1 record) = FWC-confirmed first fatal black bear attack in state history, Collier County, 2025-05-05. Kept in human_safety.
- Split: train 2015-2023, validate 2024, test 2025 to 2026-10 (out-of-time).
- All earlier decisions stand: monthly time step, H3 res 7, is_conflict target, handled layers only, relocations excluded, GBIF dropped.