// Typed helpers for the BearCast API (FastAPI, see /docs on the API server).

export type LayerName = 'forecast' | 'emerging' | 'activity' | 'habitat' | 'corridors' | 'road_risk'

export interface HexRow {
  cell: string
  [key: string]: string | number | boolean | null
}

export interface Run {
  kind: 'forecast' | 'activity' | 'bear_layers' | 'movement'
  run_id: number
  data_through: string | null
  created_at: string
  metrics: Record<string, unknown>
}

export interface PointInfo {
  cell: string
  range_label: string | null
  forecast_month: string | null
  expected_calls: number | null
  tier: string | null
  risk_rank: number | null
  emerging_rank: number | null
  never_conflict: boolean | null
  activity_as_of: string | null
  reports_90d: number | null
  days_since_report: number | null
  activity_level: string | null
  habitat_pct: number | null
  core: string | null
  corridor_pair: string | null
  corridor_value: number | null
  road_risk: number | null
  hu_den_2020: number | null
  cons_share: number | null
  lc_natural: number | null
  max_aadt: number | null
}

export interface SimStart {
  start_id: number
  start_label: string
  start_cell: string
  lat: number | null
  lon: number | null
}

export interface LineFeature {
  type: 'Feature'
  geometry: { type: 'LineString'; coordinates: [number, number][] }
  properties: Record<string, string | number | null>
}

export interface FeatureCollection {
  type: 'FeatureCollection'
  features: LineFeature[]
}

export interface SimResult {
  tracks: FeatureCollection
  reach: { cell: string; p_7d: number; p_14d: number }[]
  note: string
}

// Where the API lives. Empty in development (Vite proxies /api to uvicorn); set VITE_API_BASE when deployed,
// e.g. https://bearcast-api.onrender.com
// Forgiving about how it's typed: "bearcast-api.onrender.com", ".../" and ".../api" all work.
const RAW_BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '').trim().replace(/\/+$/, '').replace(/\/api$/, '')
const API_BASE = RAW_BASE && !/^https?:\/\//.test(RAW_BASE) ? `https://${RAW_BASE}` : RAW_BASE

async function get<T>(path: string): Promise<T> {
  const url = API_BASE + path
  const res = await fetch(url)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`)
  }
  // a web page instead of data means the request went to the map's own server, not the API
  if (!(res.headers.get('content-type') ?? '').includes('json'))
    throw new Error(`${new URL(url, window.location.href).href} returned a web page instead of data; check the API address (VITE_API_BASE)`)
  return res.json() as Promise<T>
}

export const api = {
  runs: () => get<Run[]>('/api/runs'),
  layer: (name: LayerName) => get<HexRow[]>(`/api/layers/${name}?format=h3`),
  point: (lat: number, lon: number) => get<PointInfo>(`/api/point?lat=${lat}&lon=${lon}`),
  paths: () => get<FeatureCollection>('/api/corridors/paths'),
  sims: () => get<SimStart[]>('/api/sims'),
  sim: (id: number) => get<SimResult>(`/api/sims/${id}`),
}
