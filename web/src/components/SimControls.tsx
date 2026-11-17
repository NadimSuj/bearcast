// Pick a start point and play the simulated 14-day tracks.
import type { SimStart } from '../api'
import { startName } from '../format'

interface Props {
  starts: SimStart[]
  selected: number | null
  onSelect: (id: number | null) => void
  hour: number
  maxHour: number
  playing: boolean
  onPlay: (p: boolean) => void
  onSeek: (h: number) => void
}

export function SimControls({ starts, selected, onSelect, hour, maxHour, playing, onPlay, onSeek }: Props) {
  const day = Math.min(14, Math.floor(hour / 24) + 1)
  const named = starts.map(s => ({ ...s, label: startName(s.start_label) }))
  const cores = named.filter(s => s.label.kind === 'core')
  const hotspots = named.filter(s => s.label.kind === 'activity')
  const others = named.filter(s => s.label.kind === 'other')
  const current = named.find(s => s.start_id === selected)?.label
  return (
    <section className="sb-section">
      <p className="eyebrow">Simulated movement</p>
      <p className="sb-sub">Where bears could wander from a starting point</p>
      <select className="sb-select" value={selected ?? ''} onChange={e => onSelect(e.target.value === '' ? null : Number(e.target.value))}>
        <option value="">Choose a Starting Point…</option>
        {cores.length > 0 && (
          <optgroup label="Population Cores">
            {cores.map(s => <option key={s.start_id} value={s.start_id}>{s.label.name}</option>)}
          </optgroup>
        )}
        {hotspots.length > 0 && (
          <optgroup label="Recent Activity Hotspots">
            {hotspots.map(s => <option key={s.start_id} value={s.start_id}>{s.label.name}</option>)}
          </optgroup>
        )}
        {others.map(s => <option key={s.start_id} value={s.start_id}>{s.label.name}</option>)}
      </select>

      {current ? (
        <p className="sb-note start-what">
          {current.kind === 'core' && <><b>{current.full}.</b> Starts in prime habitat inside one of Florida's main bear populations.</>}
          {current.kind === 'activity' && <><b>{current.full}.</b> Starts in one of the 10 hexagons with the most bear reports in the last 90 days ({current.name} = rank).</>}
          {current.kind === 'other' && <b>{current.full}</b>}
        </p>
      ) : (
        <dl className="start-help">
          <dt>Population Cores</dt>
          <dd>The heart of each of Florida's main black bear populations (FWC's "frequent" bear range). The simulated bears start in prime habitat inside the core.</dd>
          <dt>Recent Activity Hotspots</dt>
          <dd>The 10 hexagons with the most bear reports (sightings, conflict calls, roadkill) in the 90 days before the simulations were made. #1 had the most.</dd>
        </dl>
      )}

      {selected !== null && (
        <>
          <div className="player">
            <button className="play" onClick={() => onPlay(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
              {playing
                ? <svg width="12" height="14" viewBox="0 0 12 14"><rect width="4" height="14" fill="currentColor" /><rect x="8" width="4" height="14" fill="currentColor" /></svg>
                : <svg width="12" height="14" viewBox="0 0 12 14"><path d="M0 0l12 7-12 7z" fill="currentColor" /></svg>}
            </button>
            <input type="range" min={0} max={maxHour} step={1} value={Math.floor(hour)} onChange={e => onSeek(Number(e.target.value))} />
            <span className="day">Day {day}<span> / 14</span></span>
          </div>
          <p className="sb-note">25 simulated bears following movement rules learned from GPS-collared bears: typical wandering
            over two weeks, not real animals or dispersal. Orange hexagons: darker = more of them got there.</p>
          <button className="sb-link" onClick={() => onSelect(null)}>Clear simulation</button>
        </>
      )}
    </section>
  )
}
