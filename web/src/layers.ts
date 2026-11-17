// Colours, legends and labels for each map layer.
import type { HexRow, LayerName } from './api'
import { BRAND } from './theme'

export type RGBA = [number, number, number, number]

export const hex = (h: string, a = 200): RGBA => [
  parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16), a,
]

/** Linear interpolation through colour stops for a value in [0, 1]. */
function ramp(stops: string[], t: number, alpha = 200): RGBA {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const i = Math.min(Math.floor(x), stops.length - 2)
  const a = hex(stops[i]), b = hex(stops[i + 1]), f = x - i
  return [0, 1, 2].map(k => Math.round(a[k] + (b[k] - a[k]) * f)).concat(alpha) as RGBA
}

// palettes built around the brand colours
const RISK = { veryHigh: '#A3361F', high: BRAND.amber, elevated: '#EBC594' }
const GREENS = ['#F4EFE3', '#B9CDA8', '#4A7856', '#2C5A24', '#1A4314']
const REDS = ['#FBEFE3', '#F0B98C', BRAND.amber, '#A3361F', '#5E1A0E']
const BROWNS = ['#5C3311', '#8A5222', '#B97F45', '#DDB98A', '#F1E2C8']
const BLUES = { never: BRAND.swamp, lapsed: '#8FB0B8' }

export interface LegendItem { color: string; label: string; note?: string }

export interface LayerSpec {
  title: string
  heading: string          // "Reading the forecast" etc.
  blurb: string
  color: (d: HexRow) => RGBA
  legend: LegendItem[]
  tooltip: (d: HexRow) => string
}

const pct = (v: unknown) => `${Math.round(Number(v) * 100)}%`
const cap = (s: unknown) => String(s).charAt(0).toUpperCase() + String(s).slice(1)

export const LAYERS: Record<LayerName, LayerSpec> = {
  forecast: {
    title: 'Conflict risk next month',
    heading: 'Reading the forecast',
    blurb: 'Expected bear conflict calls per hexagon next month, ranked statewide.',
    color: d => hex({ 'very high': RISK.veryHigh, high: RISK.high, elevated: RISK.elevated }[String(d.tier)] ?? '#cccccc', 215),
    legend: [{ color: RISK.veryHigh, label: 'Very high', note: 'Top 1%' }, { color: RISK.high, label: 'High', note: 'Top 5%' },
             { color: RISK.elevated, label: 'Elevated', note: 'Top 20%' }],
    tooltip: d => `${cap(d.tier)} risk · ${Number(d.expected_calls).toFixed(2)} expected calls · rank #${Number(d.risk_rank).toLocaleString()}`,
  },
  emerging: {
    title: 'Emerging risk',
    heading: 'Reading emerging risk',
    blurb: 'Among hexagons with no conflict call in the past 12 months, the 10% most likely to get one next month. In testing, these caught about two-thirds of first-time conflicts.',
    color: d => (d.never_conflict ? hex(BLUES.never, 225) : hex(BLUES.lapsed, 205)),
    legend: [{ color: BLUES.never, label: 'Never had a conflict call' }, { color: BLUES.lapsed, label: 'Last conflict over a year ago' }],
    tooltip: d => `Emerging rank #${Number(d.emerging_rank).toLocaleString()}${d.never_conflict ? ' · no conflict on record' : ''}`,
  },
  activity: {
    title: 'Recent bear activity',
    heading: 'Reading recent activity',
    blurb: 'Sightings, conflict calls and roadkill reported in the last 90 days, spread to neighbouring hexagons. The closest thing to "where bears have been lately".',
    color: d => hex({ high: '#A3361F', moderate: BRAND.amber, low: '#EBC594' }[String(d.activity_level)] ?? '#cccccc', 205),
    legend: [{ color: '#A3361F', label: 'High' }, { color: BRAND.amber, label: 'Moderate' }, { color: '#EBC594', label: 'Low' }],
    tooltip: d => `${cap(d.activity_level)} activity · ${d.reports_90d} reports in 90 days`,
  },
  habitat: {
    title: 'Likely bear habitat',
    heading: 'Reading the habitat map',
    blurb: 'Habitat-use model trained on 383 radio- and GPS-tracked bears (1983–2014), applied to today\'s landscape. Shown as a statewide percentile.',
    color: d => ramp(GREENS, Number(d.habitat_pct), 195),
    legend: [{ color: GREENS[4], label: 'Best', note: 'Top percentiles' }, { color: GREENS[2], label: 'Middle' },
             { color: GREENS[0], label: 'Poor', note: 'Bottom percentiles' }],
    tooltip: d => `Habitat percentile ${pct(d.habitat_pct)}${d.core ? ` · ${d.core} core` : ''}`,
  },
  corridors: {
    title: 'Movement corridors',
    heading: 'Reading the corridors',
    blurb: 'Likely routes for bears travelling between populations. Busy-road hexagons on a route are candidate wildlife-crossing sites.',
    color: d => ramp(BROWNS, Number(d.corridor_value) / 0.15, d.on_best_path ? 235 : 175),
    legend: [{ color: BROWNS[0], label: 'On the best route' }, { color: BROWNS[2], label: 'Slightly longer', note: '~7% costlier' },
             { color: BROWNS[4], label: 'Alternative', note: '15% costlier' }],
    tooltip: d => `${d.corridor_pair} · route ${pct(d.corridor_value)} costlier than best`,
  },
  road_risk: {
    title: 'Road risk',
    heading: 'Reading road risk',
    blurb: 'Where likely bear habitat meets heavy traffic. In testing, the top 10% of road hexagons held 38% of bear roadkill (2010–2025).',
    color: d => ramp(REDS, Number(d.road_risk), Math.round(70 + 170 * Number(d.road_risk))),
    legend: [{ color: REDS[4], label: 'Highest' }, { color: REDS[2], label: 'Medium' }, { color: REDS[0], label: 'Low' }],
    tooltip: d => `Road risk ${Number(d.road_risk).toFixed(2)}`,
  },
}

// simulated-bear track colours: saturated so they stand out on every base map
export const SIM_COLORS: RGBA[] = [
  '#E4572E', '#F3A712', '#29B6F6', '#7CB518', '#D81B60', '#9C4DCC', '#00A896', '#FF7F50', '#3F6FD8', '#F4D35E',
].map(c => hex(c, 255))
