// The panel shown after clicking the map: everything known about that hexagon.
import type { PointInfo } from '../api'
import { longDate } from '../format'

const num = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? '–' : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`)
const ago = (days: number | null) =>
  days === null ? 'none on record' : days === 0 ? 'today' : days < 365 ? `${days} day${days === 1 ? '' : 's'} ago` : `${Math.floor(days / 365)}+ years ago`
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function Row({ label, value }: { label: string; value: string }) {
  return <div className="d-row"><span className="d-label">{label}</span><b>{value}</b></div>
}

export function Details({ info, error, onClose }: { info: PointInfo | null; error: string | null; onClose: () => void }) {
  if (!info && !error) return null
  const tier = info?.tier ?? 'low'
  const level = info?.activity_level ?? 'none'
  return (
    <div className="details">
      <header className="d-head">
        <div>
          <p className="eyebrow">Selected location</p>
          <h2>This hexagon</h2>
          {info && <p className="d-cell">H3 {info.cell}</p>}
        </div>
        <button className="d-close" onClick={onClose} aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 14 14"><path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </button>
      </header>

      <div className="d-body">
        {error ? <p className="d-muted">{error}</p> : info && (
          <>
            <section>
              <p className="d-eyebrow">Conflict forecast{info.forecast_month ? ` · ${longDate(info.forecast_month, { month: 'long', year: 'numeric' })}` : ''}</p>
              {info.forecast_month ? (
                <>
                  <span className={`tier tier-${tier.replace(' ', '-')}`}>{cap(tier)}</span>
                  <div className="stats">
                    <div><p className="stat">{num(info.expected_calls, 2)}</p><p className="stat-label">Expected calls</p></div>
                    <div><p className="stat stat-rank">#{num(info.risk_rank)}</p><p className="stat-label">Statewide rank</p></div>
                  </div>
                </>
              ) : <p className="d-muted">No forecast for this hexagon.</p>}
              {info.emerging_rank !== null && (
                <p className="callout">Emerging-risk rank #{num(info.emerging_rank)} among hexagons with no conflict in the last year
                  {info.never_conflict ? ', and none ever recorded' : ''}.</p>
              )}
            </section>

            <section>
              <p className="d-eyebrow">Recent activity</p>
              <p className="d-sub">90 days to {longDate(info.activity_as_of)}</p>
              <div className="d-split">
                <p className="big">{num(info.reports_90d)} report{info.reports_90d === 1 ? '' : 's'}</p>
                <span className={`chip chip-${level}`}>{cap(level)} nearby</span>
              </div>
              <p className="d-text">Last report: {ago(info.days_since_report)}</p>
            </section>

            <section>
              <p className="d-eyebrow">Bears and landscape</p>
              <p className="range">{info.range_label ? `FWC range: ${info.range_label}` : 'Outside FWC bear range'}</p>

              <Row label="Habitat percentile" value={pct(info.habitat_pct)} />
              {info.core && <p className="d-text">Inside the {info.core} population core</p>}

              {info.corridor_pair && !info.core && (
                <>
                  <Row label="Corridor route" value={`+${pct(info.corridor_value)}`} />
                  <p className="d-text">{info.corridor_pair}, {pct(info.corridor_value)} costlier than the best route</p>
                </>
              )}

              <Row label="Road risk" value={num(info.road_risk, 2)} />
              <p className="d-text">{info.max_aadt ? `Busiest road: ${num(info.max_aadt)} vehicles/day` : 'No major roads'}</p>

              <p className="d-text spaced">{num(info.hu_den_2020)} homes/km² · {pct(info.cons_share)} conservation land</p>
              <p className="d-text">{pct(info.lc_natural)} natural cover</p>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
