// The panel shown after clicking the map: everything known about that hexagon.
import type { PointInfo } from '../api'

const num = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? '–' : v.toLocaleString(undefined, { maximumFractionDigits: digits })
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`)
const ago = (days: number | null) =>
  days === null ? 'never' : days < 365 ? `${days} days ago` : `${Math.floor(days / 365)}+ years ago`
const month = (iso: string | null) =>
  iso ? new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) : '–'

export function Details({ info, error, onClose }: { info: PointInfo | null; error: string | null; onClose: () => void }) {
  if (!info && !error) return null
  return (
    <div className="card details">
      <button className="close" onClick={onClose} aria-label="Close">×</button>
      {error ? (
        <p className="muted">{error}</p>
      ) : info && (
        <>
          <h3>This hexagon</h3>
          <p className="muted small">H3 {info.cell} · FWC range: {info.range_label ?? '–'}</p>

          <h4>Conflict forecast{info.forecast_month ? `, ${month(info.forecast_month)}` : ''}</h4>
          {info.forecast_month ? (
            <p>
              <span className={`pill tier-${(info.tier ?? 'low').replace(' ', '-')}`}>{info.tier ?? 'low'}</span>{' '}
              {num(info.expected_calls, 2)} expected calls · rank #{num(info.risk_rank)} statewide
            </p>
          ) : <p className="muted">No forecast for this hexagon.</p>}
          {info.emerging_rank !== null && (
            <p className="small">Emerging-risk rank #{num(info.emerging_rank)} among hexagons with no conflict in the last year
              {info.never_conflict ? ' (none ever recorded)' : ''}.</p>
          )}

          <h4>Recent activity (90 days to {info.activity_as_of ?? '–'})</h4>
          <p>{num(info.reports_90d)} reports here · last one {ago(info.days_since_report)} ·{' '}
            <b>{info.activity_level ?? '–'}</b> nearby</p>

          <h4>Bears and landscape</h4>
          <ul className="facts">
            <li>Habitat percentile <b>{pct(info.habitat_pct)}</b>{info.core ? ` · inside the ${info.core} population core` : ''}</li>
            {info.corridor_pair && <li>Corridor {info.corridor_pair} · route {pct(info.corridor_value)} costlier than the best</li>}
            <li>Road risk {num(info.road_risk, 2)} · busiest road {num(info.max_aadt)} vehicles/day</li>
            <li>{num(info.hu_den_2020)} homes per km² · {pct(info.cons_share)} conservation land · {pct(info.lc_natural)} natural cover</li>
          </ul>
        </>
      )}
    </div>
  )
}