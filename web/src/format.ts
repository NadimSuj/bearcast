// Small text formatters shared by the sidebar and the details panel.
export const longDate = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { month: 'long', day: 'numeric', year: 'numeric' }) =>
  iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', opts) : '–'

/** Capitalise each word, including after a hyphen or slash ("eglin-apalachicola" → "Eglin-Apalachicola"). */
export const titleCase = (s: string) => s.replace(/(^|[\s\-/])([a-z])/g, (_, p: string, c: string) => p + c.toUpperCase())

export interface StartName { kind: 'core' | 'activity' | 'other'; name: string; full: string }

/** Turn the stored simulation start labels ("core: Big Cypress", "recent activity #3") into display names. */
export function startName(raw: string): StartName {
  const core = raw.match(/^core:\s*(.+)$/i)
  if (core) { const n = titleCase(core[1]); return { kind: 'core', name: n, full: `${n} Population Core` } }
  const act = raw.match(/^recent activity #(\d+)$/i)
  if (act) return { kind: 'activity', name: `Hotspot #${act[1]}`, full: `Recent Activity Hotspot #${act[1]}` }
  return { kind: 'other', name: titleCase(raw), full: titleCase(raw) }
}
