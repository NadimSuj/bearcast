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