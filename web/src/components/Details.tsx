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
  const locationName = info?.location_name
    ? `${cap(info.location_name)}, Florida`
    : 'Florida'
  return (
    <div className="details">
      <header className="d-head">
        <div>
          <p className="eyebrow">Selected location</p>
          <h2>{locationName}</h2>
          {info && (
            <div className="d-location-meta">
              {info.lat !== null && info.lon !== null
                ? <span>{Number(info.lat).toFixed(4)}, {Number(info.lon).toFixed(4)}</span>
                : null}
              <span className="d-h3">H3 <code>{info.cell}</code></span>
            </div>
          )}
        </div>
        <button className="d-close" onClick={onClose} aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 14 14"><path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </button>
      </header>

      <div className="d-body">
        {error ? <p className="d-muted">{error}</p> : info && (
          <>
            <section className="d-section d-forecast">
              <div className="d-section-heading">
                <p className="d-eyebrow">Conflict forecast</p>
                {info.forecast_month && <p className="d-period">{longDate(info.forecast_month, { month: 'long', year: 'numeric' })}</p>}
              </div>
              {info.forecast_month ? (
                <>
                  <div className="d-status">
                    <span className={`tier tier-${tier.replace(' ', '-')}`}>{cap(tier)}</span>
                    <span className="d-status-label">Risk level</span>
                  </div>
                  <div className="stats">
                    <div><p className="stat">{num(info.expected_calls, 2)}</p><p className="stat-label">Expected calls</p></div>
                    <div><p className="stat stat-rank">#{num(info.risk_rank)}</p><p className="stat-label">Statewide rank</p></div>
                  </div>
                </>
              ) : <p className="d-muted">No forecast for this hexagon.</p>}
              {info.emerging_rank !== null && (
                <p className="callout"><span>Emerging risk</span>Rank #{num(info.emerging_rank)} among hexagons with no conflict in the last year
                  {info.never_conflict ? ', and none ever recorded' : ''}</p>
              )}
            </section>

            <section className="d-section">
              <div className="d-section-heading">
                <p className="d-eyebrow">Recent activity</p>
                <p className="d-period">Through {longDate(info.activity_as_of)}</p>
              </div>
              <div className="activity-card">
                <div className="activity-main">
                  <p className="activity-number">{num(info.reports_90d)}</p>
                  <p className="activity-label">Reports in 90 days</p>
                </div>
                <div className="activity-meta">
                  <Row label="Activity" value={`${cap(level)} nearby`} />
                  <Row label="Last report" value={ago(info.days_since_report)} />
                </div>
              </div>
            </section>

            <section className="d-section d-landscape">
              <p className="d-eyebrow">Bears and landscape</p>
              <p className="range">{info.range_label ? `FWC range · ${info.range_label}` : 'Outside FWC bear range'}</p>

              <div className="d-data-card">
                <Row label="Habitat percentile" value={pct(info.habitat_pct)} />
                {info.core && <Row label="Population core" value={info.core} />}
                {info.corridor_pair && !info.core && (
                  <Row label={`Corridor · ${info.corridor_pair}`} value={`+${pct(info.corridor_value)}`} />
                )}
                <Row label="Road risk" value={num(info.road_risk, 2)} />
                <Row label="Busiest road" value={info.max_aadt ? `${num(info.max_aadt)} vehicles/day` : 'No major roads'} />
                <Row label="Housing density" value={`${num(info.hu_den_2020)} / km²`} />
                <Row label="Conservation land" value={pct(info.cons_share)} />
                <Row label="Natural cover" value={pct(info.lc_natural)} />
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
