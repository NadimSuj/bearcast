// Physical map: Florida coloured by elevation (green lowlands → yellow → tan ridges), with hillshading,
// built on top of CARTO Voyager so water, rivers, borders and place names stay. Roads and buildings are hidden.
// Elevation comes from the free AWS Terrain Tiles (Terrarium encoding, ~30 m detail in the US).
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'

export const VOYAGER = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json'
const DEM_TILES = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'

// Florida tops out at 105 m (Britton Hill), so the colours are stretched over 0–105 m rather than a continental scale
export const ELEVATION_STOPS: [number, string][] = [
  [0, '#4E9F55'], [5, '#69B35C'], [15, '#97C964'], [30, '#CBDD67'], [45, '#EEE15B'],
  [60, '#E8C254'], [80, '#CF9B49'], [105, '#A8723A'],
]

const HIDE = new Set(['transportation', 'transportation_name', 'building', 'aeroway', 'poi', 'housenumber',
                      'landuse', 'landcover', 'park', 'mountain_peak'])
const WATER = '#93C6E8'
const RIVER = '#6CAAD6'

type Loose = LayerSpecification & { 'source-layer'?: string; layout?: Record<string, unknown>; paint?: Record<string, unknown> }

/** Turn the Voyager style into the physical map. Pure function, so it can be tested without a browser. */
export function toTerrainStyle(base: StyleSpecification): StyleSpecification {
  const style: StyleSpecification = structuredClone(base)
  style.sources = {
    ...style.sources,
    dem: { type: 'raster-dem', tiles: [DEM_TILES], encoding: 'terrarium', tileSize: 256, maxzoom: 15,
           attribution: 'Elevation: AWS Terrain Tiles (USGS 3DEP, SRTM and others)' },
  }
  const relief: LayerSpecification[] = [
    { id: 'bc-relief', type: 'color-relief', source: 'dem',
      paint: { 'color-relief-color': ['interpolate', ['linear'], ['elevation'], ...ELEVATION_STOPS.flat()] } },
    { id: 'bc-hillshade', type: 'hillshade', source: 'dem',
      paint: { 'hillshade-exaggeration': 0.45, 'hillshade-shadow-color': '#3A3A2A', 'hillshade-highlight-color': '#FFFFFF',
               'hillshade-accent-color': '#5C5A3C' } },
  ]
  const layers = (style.layers as Loose[]).map(l => {
    const src = l['source-layer']
    if (src && HIDE.has(src)) return { ...l, layout: { ...l.layout, visibility: 'none' } } as Loose
    if (src === 'water' && l.type === 'fill') return { ...l, paint: { ...l.paint, 'fill-color': WATER } } as Loose
    if (src === 'waterway' && l.type === 'line') return { ...l, paint: { ...l.paint, 'line-color': RIVER } } as Loose
    return l
  })
  // relief goes under the first water layer: water, borders and labels draw on top of it
  let at = layers.findIndex(l => l['source-layer'] === 'water' || l['source-layer'] === 'waterway')
  if (at < 0) at = layers.findIndex(l => l.type === 'symbol')
  if (at < 0) at = layers.length
  style.layers = [...layers.slice(0, at), ...relief, ...layers.slice(at)] as LayerSpecification[]
  return style
}

let cached: Promise<StyleSpecification> | null = null
/** Fetch Voyager once and build the physical style from it. */
export function loadTerrainStyle(): Promise<StyleSpecification> {
  cached ??= fetch(VOYAGER).then(r => {
    if (!r.ok) throw new Error(`base map style: ${r.status}`)
    return r.json() as Promise<StyleSpecification>
  }).then(toTerrainStyle).catch(e => { cached = null; throw e })
  return cached
}
