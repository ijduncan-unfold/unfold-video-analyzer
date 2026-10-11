export function duration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  if (seconds > 0 && seconds < 1) return `${Number(seconds.toFixed(3))}s`
  const value = Math.max(0, Math.floor(seconds))
  return value >= 3600 ? `${Math.floor(value / 3600)}:${String(Math.floor(value % 3600 / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}` : `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
}
export function bytes(value: number) {
  if (!value) return '—'
  return value >= 1073741824 ? `${(value / 1073741824).toFixed(1)} GB` : `${(value / 1048576).toFixed(1)} MB`
}
export function timestamp(value: string) {
  const parts = value.split(':').map(Number)
  return parts.reduce((total, part) => total * 60 + part, 0) || 0
}
export function splitTags(value: string) { return [...new Set(value.split(',').map(item => item.trim()).filter(Boolean))] }
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.' }
export function reviewLabel(value: string) { return ({ unreviewed: 'Unreviewed', reviewed: 'Reviewed', needs_changes: 'Needs changes' } as Record<string, string>)[value] || 'Unreviewed' }
