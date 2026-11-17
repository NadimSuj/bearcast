// Small text formatters shared by the sidebar and the details panel.
export const longDate = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { month: 'long', day: 'numeric', year: 'numeric' }) =>
  iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', opts) : '–'
