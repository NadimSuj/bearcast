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
  return (
    <div className="section">
      <h4>Simulated movement</h4>
      <select value={selected ?? ''} onChange={e => onSelect(e.target.value === '' ? null : Number(e.target.value))}>
        <option value="">None</option>
        {starts.map(s => <option key={s.start_id} value={s.start_id}>{s.start_label}</option>)}
      </select>
      {selected !== null && (
        <>
          <div className="row">
            <button onClick={() => onPlay(!playing)}>{playing ? 'Pause' : 'Play'}</button>
            <input type="range" min={0} max={maxHour} step={1} value={Math.floor(hour)} onChange={e => onSeek(Number(e.target.value))} />
            <span className="small">day {Math.floor(hour / 24) + 1}</span>
          </div>
          <p className="small muted">25 simulated bears following movement rules learned from GPS-collared bears.
            A picture of typical wandering over two weeks, not real animals or dispersal.
            Orange hexagons: darker = more of the simulated bears got there within 14 days.</p>
        </>
      )}
    </div>
  )
}