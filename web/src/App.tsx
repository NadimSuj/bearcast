import { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { FlyToInterpolator, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { H3HexagonLayer, TripsLayer } from '@deck.gl/geo-layers'
import { Map } from 'react-map-gl/maplibre'
import { setWorkerUrl } from 'maplibre-gl'
import maplibreWorker from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'

import { api, type FeatureCollection, type HexRow, type LayerName, type PointInfo, type Run, type SimResult, type SimStart } from './api'
import { LAYERS, SIM_COLORS } from './layers'
import { Details } from './components/Details'
import { SimControls } from './components/SimControls'

const BASEMAP = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'
const FLORIDA: MapViewState = { longitude: -83.2, latitude: 28.4, zoom: 6, pitch: 0, bearing: 0 }
const SIM_HOURS = 14 * 24
const MS_PER_HOUR = 50          // playback speed: two simulated weeks in about 17 seconds

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

  // first load: run dates and simulation starts
  useEffect(() => {
    api.runs().then(setRuns).catch(e => setError(`API not reachable: ${e.message}. Is uvicorn running?`))
    api.sims().then(setStarts).catch(() => {})
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
  const layers = [
    new H3HexagonLayer<HexRow>({
      id: `hex-${layerName}`, data: rows[layerName] ?? [], getHexagon: d => d.cell, getFillColor: spec.color,
      extruded: false, stroked: false, pickable: true, updateTriggers: { getFillColor: layerName },
      opacity: sim ? 0.35 : 0.9,   // fade the background layer while a simulation plays
    }),
    sim && new H3HexagonLayer<{ cell: string; p_14d: number }>({
      id: 'sim-reach', data: sim.reach, getHexagon: d => d.cell, extruded: false, stroked: true,
      getFillColor: d => [255, 140, 0, Math.round(40 + 140 * d.p_14d)], getLineColor: [255, 255, 255, 120], lineWidthMinPixels: 0.5,
    }),
    showPaths && paths && new GeoJsonLayer({
      id: 'paths', data: paths as never, getLineColor: [60, 30, 10, 230], lineWidthMinPixels: 2.5, pickable: true,
    }),
    sim && new TripsLayer<Track>({
      id: 'sim-tracks', data: tracks, getPath: d => d.path, getTimestamps: d => d.timestamps,
      getColor: d => SIM_COLORS[d.sim % SIM_COLORS.length], currentTime: hour, trailLength: 24,
      widthMinPixels: 2.5, capRounded: true, jointRounded: true, fadeTrail: true,
    }),
    new ScatterplotLayer<SimStart>({
      id: 'sim-starts', data: starts.filter(s => s.lat !== null), getPosition: d => [d.lon!, d.lat!],
      getRadius: 6, radiusUnits: 'pixels', getFillColor: d => (d.start_id === simId ? [0, 0, 0, 255] : [40, 40, 40, 160]),
      getLineColor: [255, 255, 255], stroked: true, lineWidthMinPixels: 1.5, pickable: true,
      updateTriggers: { getFillColor: simId },
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
    if (info.layer?.id === 'sim-starts') return `${(info.object as SimStart).start_label} · click to simulate`
    if (info.layer?.id === 'paths') {
      const p = (info.object as { properties: Record<string, unknown> }).properties
      return `${p.pair} · ${p.length_km} km · ${p.busy_road_hexagons} busy-road hexagons`
    }
    if (info.layer?.id === `hex-${layerName}`) return spec.tooltip(info.object as HexRow)
    return null
  }

  const forecastRun = runs.find(r => r.kind === 'forecast')
  const activityRun = runs.find(r => r.kind === 'activity')
  const forecastMonth = forecastRun?.data_through
    ? new Date(new Date(forecastRun.data_through + 'T12:00:00').setMonth(new Date(forecastRun.data_through + 'T12:00:00').getMonth() + 1))
        .toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    : '–'

  return (
    <div className="app">
      <DeckGL viewState={viewState} onViewStateChange={e => setViewState(e.viewState as MapViewState)}
              controller layers={layers} onClick={onClick} getTooltip={tooltip}>
        <Map mapStyle={BASEMAP} />
      </DeckGL>

      <aside className="card sidebar">
        <h1>BearCast</h1>
        <p className="muted small">Florida black bear conflict forecast and bear activity · forecast for <b>{forecastMonth}</b>
          {activityRun?.data_through ? ` · reports to ${activityRun.data_through}` : ''}</p>
        {error && <p className="error small">{error}</p>}

        <div className="section">
          <h4>Map layer</h4>
          {(Object.keys(LAYERS) as LayerName[]).map(name => (
            <label key={name} className="radio">
              <input type="radio" name="layer" checked={layerName === name} onChange={() => setLayerName(name)} />
              {LAYERS[name].title}
            </label>
          ))}
          <label className="radio">
            <input type="checkbox" checked={showPaths} onChange={e => setShowPaths(e.target.checked)} />
            Corridor routes between populations
          </label>
        </div>

        <div className="section">
          <p className="small">{spec.blurb}</p>
          <ul className="legend">
            {spec.legend.map(l => <li key={l.label}><span style={{ background: l.color }} />{l.label}</li>)}
          </ul>
          {!rows[layerName] && <p className="muted small">Loading…</p>}
        </div>

        <SimControls starts={starts} selected={simId} onSelect={selectSim} hour={hour} maxHour={SIM_HOURS}
                     playing={playing} onPlay={setPlaying} onSeek={h => { setPlaying(false); setHour(h) }} />

        <p className="muted tiny">Click anywhere on the map for details. Data: Florida Fish and Wildlife Conservation Commission (FWC),
          SILVIS Lab, FNAI, FDOT, FNAI/FWC Cooperative Land Cover. Forecasts are statistical estimates.</p>
      </aside>

      <Details info={point} error={pointError} onClose={() => { setPoint(null); setPointError(null) }} />
    </div>
  )
}