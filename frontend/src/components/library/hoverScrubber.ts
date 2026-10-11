/** Coalesce pointer moves while a media seek is in flight; always land on the latest position. */
export function createHoverScrubber(video: HTMLVideoElement, start: number, end: number | undefined,
  onFrame: (seconds: number, fraction: number) => void) {
  let fraction = 0
  let scheduled = 0
  let pending = false
  let disposed = false
  let revision = 0
  let completed = -1
  let inFlight = -1

  function bounds() {
    if (!Number.isFinite(video.duration) || video.duration <= 0) return null
    const first = Math.max(0, Math.min(Number.isFinite(start) ? start : 0, video.duration))
    const last = Math.min(Number.isFinite(end) ? end! : video.duration, video.duration)
    return last > first ? { first, last, span: last - first } : null
  }
  function showFrame() {
    const range = bounds()
    if (range && video.readyState >= 2 && !disposed) {
      onFrame(video.currentTime, Math.max(0, Math.min(1, (video.currentTime - range.first) / range.span)))
    }
  }
  function flush() {
    scheduled = 0
    if (disposed || pending || video.seeking || video.readyState < 1 || completed === revision) return
    const range = bounds()
    if (!range) return
    // Seeking exactly to EOF can show a blank/ended frame, especially with very short recordings.
    const target = Math.min(range.last - Math.min(0.001, range.span / 2), range.first + fraction * range.span)
    if (Math.abs(video.currentTime - target) < 0.0005) {
      if (video.readyState >= 2) { completed = revision; showFrame() }
      return
    }
    pending = true
    inFlight = revision
    try { video.currentTime = target } catch { pending = false }
  }
  function schedule() {
    if (!disposed && !scheduled) scheduled = requestAnimationFrame(flush)
  }
  function landed() { if (pending) completed = inFlight; pending = false; showFrame(); schedule() }
  video.addEventListener('loadedmetadata', schedule)
  video.addEventListener('loadeddata', schedule)
  video.addEventListener('seeked', landed)

  return {
    seek(value: number) { fraction = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0; revision++; schedule() },
    dispose() {
      disposed = true
      cancelAnimationFrame(scheduled)
      video.removeEventListener('loadedmetadata', schedule)
      video.removeEventListener('loadeddata', schedule)
      video.removeEventListener('seeked', landed)
    },
  }
}
