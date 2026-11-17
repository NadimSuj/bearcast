// Pick a start point and play the simulated 14-day tracks.
import type { SimStart } from '../api'

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
  return (
    <section className="sb-section">
      <p className="eyebrow">Simulated movement</p>
      <p className="sb-sub">Where bears could wander from a starting point</p>
      <select className="sb-select" value={selected ?? ''} onChange={e => onSelect(e.target.value === '' ? null : Number(e.target.value))}>
        <option value="">Choose a starting point…</option>
        {starts.map(s => <option key={s.start_id} value={s.start_id}>{s.start_label}</option>)}
      </select>
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
