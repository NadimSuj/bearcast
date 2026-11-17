// Colours, legends and labels for each map layer.
import type { HexRow, LayerName } from './api'

export type RGBA = [number, number, number, number]

const hex = (h: string, a = 200): RGBA => [
  parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16), a,
]

/** Linear interpolation through colour stops for a value in [0, 1]. */
function ramp(stops: string[], t: number, alpha = 200): RGBA {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const i = Math.min(Math.floor(x), stops.length - 2)
  const a = hex(stops[i]), b = hex(stops[i + 1]), f = x - i
  return [0, 1, 2].map(k => Math.round(a[k] + (b[k] - a[k]) * f)).concat(alpha) as RGBA
}

const GREENS = ['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b']
const REDS = ['#fff5eb', '#fdbe85', '#fd8d3c', '#d94701', '#7f2704']
const BROWNS = ['#7f3b08', '#b35806', '#e08214', '#fdb863', '#fee0b6']

export interface LegendItem { color: string; label: string }

export interface LayerSpec {
  title: string
  blurb: string
  color: (d: HexRow) => RGBA
  legend: LegendItem[]
  tooltip: (d: HexRow) => string
}

const pct = (v: unknown) => `${Math.round(Number(v) * 100)}%`

export const LAYERS: Record<LayerName, LayerSpec> = {
  forecast: {
    title: 'Conflict risk next month',
    blurb: 'Expected bear conflict calls per hexagon next month, ranked statewide. Very high = top 1%, high = top 5%, elevated = top 20%.',
    color: d => hex({ 'very high': '#b30000', high: '#fc8d59', elevated: '#fdd49e' }[String(d.tier)] ?? '#cccccc', 210),
    legend: [{ color: '#b30000', label: 'Very high (top 1%)' }, { color: '#fc8d59', label: 'High (top 5%)' },
             { color: '#fdd49e', label: 'Elevated (top 20%)' }],
    tooltip: d => `${d.tier} risk · ${Number(d.expected_calls).toFixed(2)} expected calls · rank #${d.risk_rank}`,
  },
  emerging: {
    title: 'Emerging risk',
    blurb: 'Among hexagons with no conflict call in the past 12 months, the 10% most likely to get one next month. In testing, these caught about two-thirds of first-time conflicts.',
    color: d => (d.never_conflict ? hex('#08519c', 220) : hex('#6baed6', 200)),
    legend: [{ color: '#08519c', label: 'Never had a conflict call' }, { color: '#6baed6', label: 'Last conflict over a year ago' }],
    tooltip: d => `Emerging rank #${d.emerging_rank}${d.never_conflict ? ' · no conflict on record' : ''}`,
  },
  activity: {
    title: 'Recent bear activity',
    blurb: 'Sightings, conflict calls and roadkill reported in the last 90 days, spread to neighbouring hexagons. The closest thing to "where bears have been lately".',
    color: d => hex({ high: '#e6550d', moderate: '#fdae6b', low: '#fee6ce' }[String(d.activity_level)] ?? '#cccccc', 200),
    legend: [{ color: '#e6550d', label: 'High' }, { color: '#fdae6b', label: 'Moderate' }, { color: '#fee6ce', label: 'Low' }],
    tooltip: d => `${d.activity_level} activity · ${d.reports_90d} reports in 90 days`,
  },
  habitat: {
    title: 'Likely bear habitat',
    blurb: 'Habitat-use model trained on 383 radio- and GPS-tracked bears (1983–2014), applied to today\'s landscape. Shown as a statewide percentile.',
    color: d => ramp(GREENS, Number(d.habitat_pct), 190),
    legend: [{ color: GREENS[4], label: 'Best (top percentiles)' }, { color: GREENS[2], label: 'Middle' }, { color: GREENS[0], label: 'Poor' }],
    tooltip: d => `Habitat percentile ${pct(d.habitat_pct)}${d.core ? ` · ${d.core} core` : ''}`,
  },
  corridors: {
    title: 'Movement corridors',
    blurb: 'Likely routes for bears travelling between populations. Darkest = on the cheapest route; lighter = up to 15% costlier. Busy-road hexagons on a route are candidate wildlife-crossing sites.',
    color: d => ramp(BROWNS, Number(d.corridor_value) / 0.15, d.on_best_path ? 230 : 170),
    legend: [{ color: BROWNS[0], label: 'On the best route' }, { color: BROWNS[2], label: '~7% costlier' }, { color: BROWNS[4], label: '15% costlier' }],
    tooltip: d => `${d.corridor_pair} · route ${pct(d.corridor_value)} costlier than best`,
  },
  road_risk: {
    title: 'Road risk',
    blurb: 'Where likely bear habitat meets heavy traffic. In testing, the top 10% of road hexagons held 38% of bear roadkill (2010–2025).',
    color: d => ramp(REDS, Number(d.road_risk), Math.round(60 + 170 * Number(d.road_risk))),
    legend: [{ color: REDS[4], label: 'Highest' }, { color: REDS[2], label: 'Medium' }, { color: REDS[0], label: 'Low' }],
    tooltip: d => `Road risk ${Number(d.road_risk).toFixed(2)}`,
  },
}

export const SIM_COLORS: RGBA[] = [
  [31, 119, 180, 230], [255, 127, 14, 230], [44, 160, 44, 230], [214, 39, 40, 230], [148, 103, 189, 230],
  [140, 86, 75, 230], [227, 119, 194, 230], [127, 127, 127, 230], [188, 189, 34, 230], [23, 190, 207, 230],
]