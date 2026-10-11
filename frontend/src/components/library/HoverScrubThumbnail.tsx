import { useCallback, useEffect, useRef, useState } from 'react'
import type { PointerEvent, ReactNode } from 'react'
import { createHoverScrubber } from './hoverScrubber'
import { duration } from './format'

export function HoverScrubThumbnail({ src, poster, placeholder, children, start = 0, end, enabled = true }: {
  src: string | null; poster: string | null; placeholder: ReactNode; children: ReactNode
  start?: number; end?: number; enabled?: boolean
}) {
  const [active, setActive] = useState(false)
  const [ready, setReady] = useState(false)
  const hovered = useRef(false)
  const position = useRef(0)
  const scrubber = useRef<ReturnType<typeof createHoverScrubber> | null>(null)
  const playhead = useRef<HTMLSpanElement>(null)
  const time = useRef<HTMLSpanElement>(null)
  const stop = useCallback(() => {
    hovered.current = false
    scrubber.current?.dispose()
    setActive(false); setReady(false)
  }, [])
  const mountVideo = useCallback((video: HTMLVideoElement | null) => {
    if (!video) return
    const controller = createHoverScrubber(video, start, end, (seconds, fraction) => {
      if (!hovered.current) return
      setReady(true)
      if (playhead.current) playhead.current.style.left = `${fraction * 100}%`
      if (time.current) time.current.textContent = `${duration(seconds)} / ${duration(end ?? video.duration)}`
    })
    scrubber.current = controller
    // React can reconnect callback refs to the same element in development or after a range change.
    if (src && video.getAttribute('src') !== src) { video.src = src; video.load() }
    controller.seek(position.current)
    return () => {
      controller.dispose()
      if (scrubber.current === controller) scrubber.current = null
      video.pause(); video.removeAttribute('src'); video.load()
    }
  }, [src, start, end])

  useEffect(() => {
    if (!active) return
    const hide = () => { if (document.hidden) stop() }
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('blur', stop)
    return () => { document.removeEventListener('visibilitychange', hide); window.removeEventListener('blur', stop) }
  }, [active, stop])

  function move(event: PointerEvent<HTMLDivElement>, entering = false) {
    if (!enabled || !src || event.pointerType === 'touch' || event.buttons || (!entering && !hovered.current)) return
    const rect = event.currentTarget.getBoundingClientRect()
    position.current = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)))
    if (entering) { hovered.current = true; setActive(true) }
    scrubber.current?.seek(position.current)
  }
  return <div className={`lw-card-image ${active && enabled && ready ? 'is-scrubbing' : ''}`} title={enabled && src ? 'Move across the image to scrub; click to open' : undefined}
    onPointerEnter={event => move(event, true)} onPointerMove={event => move(event)} onPointerLeave={stop} onPointerDown={stop} onPointerCancel={stop}>
    {poster ? <img src={poster} alt="" loading="lazy" /> : placeholder}
    {active && enabled && src && <video className="lw-scrub-video" ref={mountVideo} src={src} muted playsInline preload="metadata" aria-hidden="true" onError={stop} />}
    {children}
    <span className="lw-scrub-playhead" ref={playhead} aria-hidden="true" />
    <span className="lw-scrub-time" ref={time} aria-hidden="true" />
  </div>
}
