# Phase 3 decisions: the web map

## Stack
- **React + TypeScript + Vite** in `web/`, separate from the Python package. Vite's dev server proxies `/api` to
  uvicorn on port 8000, so the browser sees one origin and no CORS setup is needed in development.
- **MapLibre** draws the base map (free CARTO Positron style, no API key). **deck.gl** draws the data on top:
  `H3HexagonLayer` for every hexagon layer, `GeoJsonLayer` for corridor routes, `TripsLayer` for simulated tracks,
  `ScatterplotLayer` for simulation start points.
- Hexagon layers are fetched as `?format=h3` (cell id + a few numbers). deck.gl builds the hexagon shapes in the
  browser, so the payload is a fraction of the GeoJSON size.

## Layers
| Layer | Source | What's drawn |
|---|---|---|
| Conflict risk next month | latest_forecast | tiers very high / high / elevated (low hidden) |
| Emerging risk | latest_forecast | top 10% of hexagons with no conflict in the last year; darker if none ever |
| Recent bear activity | latest_activity | level low / moderate / high from 30- and 90-day reports |
| Likely bear habitat | latest_bear_layers | habitat percentile, green ramp |
| Movement corridors | latest_bear_layers | corridor value ≤ 0.15, best path emphasised |
| Road risk | latest_bear_layers | habitat × traffic, red ramp |

Emerging risk got its own API layer because most emerging hexagons sit in the "low" tier, which the forecast layer hides.

## Simulated movement
- Picking a start point flies the map to zoom ~10.5: two weeks of movement covers 10–20 km, which is invisible at state zoom.
- Tracks animate with a time-based clock (50 ms per simulated hour), so slow computers drop frames instead of slowing down.
- The background layer fades to 35% while a simulation is shown; orange reach hexagons show the share of the 25 simulated
  bears that got there within 14 days.
- Labelled everywhere as a simulation from movement rules learned on Camp Blanding GPS bears, not real animals or dispersal.

## Practical notes
- MapLibre 6 loads a worker script relative to its own file; Vite's dependency pre-bundling breaks that, so the app
  imports `maplibre-gl-worker.mjs?url` and passes it to `setWorkerUrl`.
- The production bundle is ~1.2 MB gzip-compressed to ~350 KB, mostly MapLibre and deck.gl. The size warning is raised to 2 MB.
- Clicking anywhere calls `/api/point` and opens the details panel (forecast, activity, habitat, corridor, road, landscape).
- The view is kept on Florida: the map centre is clamped to the state's bounding box (−87.7 to −79.9, 24.5 to 31.0),
  zoom is limited to 5.8–14, and rotation/tilt are off. Clamping the centre rather than the edges means Pensacola and
  Key West can still be centred when zoomed in.
- Hexagons inside a population core are left out of the corridors layer and the corridor line in the details panel:
  their corridor value is always 0 (a bear there is already home), which read as "the best corridor".