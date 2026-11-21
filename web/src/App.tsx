import { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { FlyToInterpolator, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { H3HexagonLayer, TripsLayer } from '@deck.gl/geo-layers'
import { Map } from 'react-map-gl/maplibre'
import { setWorkerUrl, type StyleSpecification } from 'maplibre-gl'
import maplibreWorker from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'

import { api, type FeatureCollection, type HexRow, type LayerName, type PointInfo, type Run, type SimResult, type SimStart } from './api'
import { LAYERS, SIM_COLORS, hex } from './layers'
import { BRAND } from './theme'
import { Details } from './components/Details'
import { longDate, startName } from './format'
import { SimControls } from './components/SimControls'

// base maps. Color and Artistic are MapTiler styles and need a MapTiler key in web/.env.local (VITE_MAPTILER_KEY=...).
// Without a key, Color falls back to CARTO Voyager and Artistic is disabled. Light is CARTO Positron (free, no key).
const VOYAGER = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json'
const MAPTILER_KEY = import.meta.env.VITE_MAPTILER_KEY as string | undefined
const maptiler = (mapId: string) => `https://api.maptiler.com/maps/${mapId}/style.json?key=${MAPTILER_KEY ?? ''}`
const COLOR_MAP_ID = '01a1eae1-07b8-73f8-bbcc-6c44853bafe3'
const AQUARELLE_MAP_ID = '01a1ead8-0efa-7410-afbf-66cf36d1650a'
const BASEMAPS = {
  color: { label: 'Color', style: (MAPTILER_KEY ? maptiler(COLOR_MAP_ID) : VOYAGER) as string | StyleSpecification },
  artistic: { label: 'Artistic', style: maptiler(AQUARELLE_MAP_ID) as string | StyleSpecification },
  light: { label: 'Light', style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json' as string | StyleSpecification },
}
type BasemapName = keyof typeof BASEMAPS

// keep the map on Florida: the centre can't leave this box and you can't zoom out past the state
const BOUNDS = { west: -87.7, east: -79.9, south: 24.5, north: 31.0 }
const MIN_ZOOM = 5.8
const MAX_ZOOM = 14
const FLORIDA: MapViewState = { longitude: -83.0, latitude: 28.2, zoom: 6.1, pitch: 0, bearing: 0, minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM }

const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x))
function keepInFlorida(v: MapViewState): MapViewState {
  return { ...v, longitude: clamp(v.longitude, BOUNDS.west, BOUNDS.east), latitude: clamp(v.latitude, BOUNDS.south, BOUNDS.north),
           zoom: clamp(v.zoom, MIN_ZOOM, MAX_ZOOM), pitch: 0, bearing: 0 }
}
const SIM_HOURS = 14 * 24
const MS_PER_HOUR = 50          // playback speed: two simulated weeks in about 17 seconds

const TOOLTIP_STYLE = {
  backgroundColor: BRAND.wilderness, color: '#fff', fontSize: '12px', fontFamily: 'inherit',
  padding: '6px 10px', borderRadius: '6px', boxShadow: '0 4px 14px rgba(0,0,0,0.25)',
}

// MapLibre loads a helper worker script; tell it where Vite put the file
setWorkerUrl(maplibreWorker)

interface Track { path: [number, number][]; timestamps: number[]; sim: number }

export default function App() {
  const [layerName, setLayerName] = useState<LayerName>('forecast')
  const [rows, setRows] = useState<Partial<Record<LayerName, HexRow[]>>>({})
  const [showPaths, setShowPaths] = useState(false)
  const [paths, setPaths] = useState<FeatureCollection | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [point, setPoint] = useState<PointInfo | null>(null)
  const [pointError, setPointError] = useState<string | null>(null)
  const [starts, setStarts] = useState<SimStart[]>([])
  const [simId, setSimId] = useState<number | null>(null)
  const [sim, setSim] = useState<SimResult | null>(null)
  const [hour, setHour] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [viewState, setViewState] = useState<MapViewState>(FLORIDA)
  const [basemap, setBasemap] = useState<BasemapName>('color')
  const [waking, setWaking] = useState(false)

  // first load: run dates and simulation starts. On the free host the API sleeps when idle, so if the first
  // answer takes more than a few seconds, say that it's waking up rather than leaving the page silently empty.
  useEffect(() => {
    const slow = setTimeout(() => setWaking(true), 4000)
    api.runs().then(setRuns).catch(e => setError(`Can't reach the BearCast server (${e.message}). Try refreshing in a minute.`))
      .finally(() => { clearTimeout(slow); setWaking(false) })
    api.sims().then(setStarts).catch(() => {})
    return () => clearTimeout(slow)
  }, [])

  // fetch each hexagon layer once, the first time it's chosen
  useEffect(() => {
    if (rows[layerName]) return
    api.layer(layerName).then(r => setRows(prev => ({ ...prev, [layerName]: r }))).catch(e => setError(e.message))
  }, [layerName, rows])

  useEffect(() => {
    if (showPaths && !paths) api.paths().then(setPaths).catch(e => setError(e.message))
  }, [showPaths, paths])

  // choosing a start clears the old tracks; the effect below fetches the new ones
  // and flies the map to it (two weeks of bear movement is only ~10-20 km, invisible at state zoom)
  function selectSim(id: number | null) {
    setSimId(id); setSim(null); setHour(0); setPlaying(false)
    const start = starts.find(s => s.start_id === id)
    if (start?.lat != null && start.lon != null)
      setViewState(v => ({ ...v, longitude: start.lon!, latitude: start.lat!, zoom: 10.5,
                           transitionDuration: 1500, transitionInterpolator: new FlyToInterpolator() }))
  }

  useEffect(() => {
    if (simId === null) return
    api.sim(simId).then(s => { setSim(s); setPlaying(true) }).catch(e => setError(e.message))
  }, [simId])

  // animation clock tied to real elapsed time, so slow computers skip frames instead of slowing down
  useEffect(() => {
    if (!playing) return
    let raf = 0
    let last = performance.now()
    const tick = (now: number) => {
      const dh = (now - last) / MS_PER_HOUR
      last = now
      setHour(h => (h + dh) % SIM_HOURS)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  const tracks: Track[] = useMemo(() => (sim?.tracks.features ?? []).map(f => ({
    path: f.geometry.coordinates, timestamps: f.geometry.coordinates.map((_, i) => i), sim: Number(f.properties.sim),
  })), [sim])

  const spec = LAYERS[layerName]
  // simulated tracks: the whole path so far stays solid (no fade); a dark casing underneath makes them pop
  const trackProps = {
    data: tracks, getPath: (d: Track) => d.path, getTimestamps: (d: Track) => d.timestamps,
    currentTime: hour, trailLength: SIM_HOURS, fadeTrail: false, capRounded: true, jointRounded: true,
  }
  const layers = [
    new H3HexagonLayer<HexRow>({
      id: `hex-${layerName}`, data: rows[layerName] ?? [], getHexagon: d => d.cell, getFillColor: spec.color,
      extruded: false, stroked: false, pickable: true, updateTriggers: { getFillColor: layerName },
      opacity: sim ? 0.35 : 0.9,   // fade the background layer while a simulation plays
    }),
    sim && new H3HexagonLayer<{ cell: string; p_14d: number }>({
      id: 'sim-reach', data: sim.reach, getHexagon: d => d.cell, extruded: false, stroked: true,
      getFillColor: d => hex(BRAND.amber, Math.round(40 + 150 * d.p_14d)), getLineColor: [255, 255, 255, 140], lineWidthMinPixels: 0.5,
    }),
    showPaths && paths && new GeoJsonLayer({
      id: 'paths', data: paths as never, getLineColor: hex(BRAND.swamp, 240), lineWidthMinPixels: 3, pickable: true,
    }),
    sim && new TripsLayer<Track>({ id: 'sim-casing', ...trackProps, getColor: hex(BRAND.bear, 200), widthMinPixels: 7 }),
    sim && new TripsLayer<Track>({
      id: 'sim-tracks', ...trackProps, getColor: d => SIM_COLORS[d.sim % SIM_COLORS.length], widthMinPixels: 4,
    }),
    new ScatterplotLayer<SimStart>({
      id: 'sim-starts', data: starts.filter(s => s.lat !== null), getPosition: d => [d.lon!, d.lat!],
      getRadius: d => (d.start_id === simId ? 8 : 6), radiusUnits: 'pixels',
      getFillColor: d => (d.start_id === simId ? hex(BRAND.amber, 255) : hex(BRAND.bear, 220)),
      getLineColor: hex(BRAND.sand, 255), stroked: true, lineWidthMinPixels: 2, pickable: true,
      updateTriggers: { getFillColor: simId, getRadius: simId },
    }),
  ]

  function onClick(info: PickingInfo) {
    if (info.layer?.id === 'sim-starts' && info.object) { selectSim((info.object as SimStart).start_id); return }
    if (!info.coordinate) return
    const [lon, lat] = info.coordinate
    setPointError(null)
    api.point(lat, lon).then(setPoint).catch(e => { setPoint(null); setPointError(e.message) })
  }

  function tooltip(info: PickingInfo) {
    if (!info.object) return null
    let text: string | null = null
    if (info.layer?.id === 'sim-starts') text = `${startName((info.object as SimStart).start_label).full} · click to simulate`
    else if (info.layer?.id === 'paths') {
      const p = (info.object as { properties: Record<string, unknown> }).properties
      text = `${p.pair} · ${p.length_km} km · ${p.busy_road_hexagons} busy-road hexagons`
    } else if (info.layer?.id === `hex-${layerName}`) text = spec.tooltip(info.object as HexRow)
    return text ? { text, style: TOOLTIP_STYLE } : null
  }

  const forecastRun = runs.find(r => r.kind === 'forecast')
  const activityRun = runs.find(r => r.kind === 'activity')
  const forecastMonth = forecastRun?.data_through
    ? new Date(new Date(forecastRun.data_through + 'T12:00:00').setMonth(new Date(forecastRun.data_through + 'T12:00:00').getMonth() + 1))
        .toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
    : '–'

  return (
    <div className="app">
      <aside className="sidebar">
        <header className="brand">
          <h1>BearCast</h1>
          <span className="brand-tag">Florida black bear outlook</span>
        </header>

        <p className="eyebrow">Florida / Statewide</p>
        <h2 className="sb-title">Black bear outlook</h2>

        <div className="period">
          <p className="period-label">Forecast period</p>
          <p className="period-month">{forecastMonth}</p>
          <p className="period-sub">{activityRun?.data_through ? `Reports through ${longDate(activityRun.data_through)}` : 'Loading…'}</p>
        </div>
        {waking && <p className="sb-wake">Waking up the BearCast server.</p>}
        {error && <p className="sb-error">{error}</p>}

        <section className="sb-section">
          <p className="eyebrow">Map layer</p>
          <p className="sb-sub">Choose what to explore</p>
          <div className="options">
            {(Object.keys(LAYERS) as LayerName[]).map(name => (
              <label key={name} className={`option${layerName === name ? ' selected' : ''}`}>
                <input type="radio" name="layer" checked={layerName === name} onChange={() => setLayerName(name)} />
                {LAYERS[name].title}
              </label>
            ))}
            <label className="option check">
              <input type="checkbox" checked={showPaths} onChange={e => setShowPaths(e.target.checked)} />
              Corridor routes between populations
            </label>
          </div>
        </section>

        <section className="sb-section">
          <p className="eyebrow">Base map</p>
          <div className="seg">
            {(Object.keys(BASEMAPS) as BasemapName[]).map(b => (
              <button key={b} className={b === basemap ? 'active' : ''} onClick={() => setBasemap(b)}
                      disabled={b === 'artistic' && !MAPTILER_KEY}
                      title={b === 'artistic' && !MAPTILER_KEY ? 'Needs VITE_MAPTILER_KEY in web/.env.local' : undefined}>{BASEMAPS[b].label}</button>
            ))}
          </div>
          {basemap === 'artistic' && (
            <p className="basemap-note"><b>Artistic map.</b> A hand-painted watercolour style (MapTiler Aquarelle), made for looks
              rather than navigation: roads, places and coastlines are simplified, so use Color or Light for detail.</p>
          )}
        </section>

        <section className="sb-section">
          <p className="eyebrow">{spec.heading}</p>
          <p className="sb-blurb">{spec.blurb}</p>
          <ul className="legend">
            {spec.legend.map(l => (
              <li key={l.label}><span className="swatch" style={{ background: l.color }} />{l.label}{l.note && <em>{l.note}</em>}</li>
            ))}
          </ul>
          {!rows[layerName] && <p className="sb-sub">Loading map layer…</p>}
        </section>

        <SimControls starts={starts} selected={simId} onSelect={selectSim} hour={hour} maxHour={SIM_HOURS}
                     playing={playing} onPlay={setPlaying} onSeek={h => { setPlaying(false); setHour(h) }} />

        <footer className="sb-foot">
          <p>• Forecasts are statistical estimates. Click anywhere on the map for details.</p>
          <p>Data: Florida Fish and Wildlife Conservation Commission (FWC), SILVIS Lab, FNAI, FDOT, FNAI/FWC Cooperative Land Cover.</p>
        </footer>
      </aside>

      <main className="map">
        <DeckGL viewState={viewState} onViewStateChange={e => setViewState(keepInFlorida(e.viewState as MapViewState))}
                controller={{ dragRotate: false, touchRotate: false }} layers={layers} onClick={onClick} getTooltip={tooltip}>
          <Map mapStyle={BASEMAPS[basemap].style} renderWorldCopies={false} />
        </DeckGL>
        <div className="logo" title="BearCast"><img src="/bearcast-logo.png" alt="BearCast logo" /></div>
        <Details info={point} error={pointError} onClose={() => { setPoint(null); setPointError(null) }} />
      </main>
    </div>
  )
}
