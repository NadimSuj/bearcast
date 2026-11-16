# Phase 0 decisions

Status: **proposed** (2026-11-16). Based on the historical calls layer (80,331 calls).
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