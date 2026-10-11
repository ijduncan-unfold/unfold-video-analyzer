import { memo, useEffect, useId, useMemo, useRef, useState } from 'react'
import { exportUrl, getExportOptions } from '../../api/library'
import type { AssetDetail, ExportOptions } from '../../api/library'
import { Icon } from './Icon'
import { errorMessage } from './format'
import './TranscriptPanel.css'

interface Props {
  asset: AssetDetail
  onSeek: (seconds: number) => void
  currentTime: number
  active: boolean
}

type TranscriptEntry = AssetDetail['transcript'][number] & { start?: string | number; end?: string | number; end_seconds?: number }
interface Cue { key: string; start: number; end: number; text: string; speaker: string }

function timingValue(entry: TranscriptEntry, side: 'start' | 'end') {
  // Mirror backend segment_times: an explicitly invalid preferred value must
  // not be replaced with a lower-priority timestamp that looks valid.
  const numeric = `${side}_seconds` as 'start_seconds' | 'end_seconds'
  const formatted = `${side}_time` as 'start_time' | 'end_time'
  if (Object.hasOwn(entry, numeric)) return entry[numeric]
  if (Object.hasOwn(entry, formatted)) return entry[formatted]
  return entry[side]
}

function seconds(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) && value >= 0 ? value : null
  if (typeof value !== 'string' || !/^\d+(?::\d{1,2}){0,2}(?:[.,]\d+)?$/.test(value.trim())) return null
  const parts = value.trim().replace(',', '.').split(':').map(Number)
  if (parts.length > 1 && parts[parts.length - 1] >= 60 || parts.length === 3 && parts[1] >= 60) return null
  const result = parts.reduce((total, part) => total * 60 + part, 0)
  return Number.isFinite(result) && result >= 0 ? result : null
}

function timedCues(entries: TranscriptEntry[], sourceDuration?: number | null) {
  const cues: Cue[] = []
  let skipped = 0
  entries.forEach((entry, index) => {
    const text = typeof entry?.text === 'string' ? entry.text.trim() : ''
    if (!text) { skipped++; return }
    const start = seconds(timingValue(entry, 'start'))
    const end = seconds(timingValue(entry, 'end'))
    if (start === null || end === null || end <= start || sourceDuration != null && Number.isFinite(sourceDuration) && end > sourceDuration) {
      skipped++; return
    }
    cues.push({ key: `${index}-${start}-${end}`, start, end, text, speaker: typeof entry.speaker === 'string' ? entry.speaker.trim() : '' })
  })
  cues.sort((a, b) => a.start - b.start || a.end - b.end)
  return { cues, skipped }
}

function cueTime(value: number) {
  const milliseconds = Math.round(value * 1000)
  const whole = Math.floor(milliseconds / 1000)
  const hours = Math.floor(whole / 3600)
  return `${hours ? `${hours}:` : ''}${String(Math.floor(whole % 3600 / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}.${String(milliseconds % 1000).padStart(3, '0')}`
}

function tokenSource(term: string) {
  const literal = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return term.length <= 2 ? `(?<![\\p{L}\\p{N}_])${literal}(?![\\p{L}\\p{N}_])` : literal
}

function subtitleFilename(disposition: string | null, fallback: string) {
  let supplied = ''
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)
  if (encoded) { try { supplied = decodeURIComponent(encoded[1]) } catch { /* Use the local fallback. */ } }
  if (!supplied) supplied = disposition?.match(/filename="([^"]+)"/i)?.[1] || ''
  const base = (supplied || fallback).split(/[\\/]/).pop() || 'video'
  const clean = Array.from(base, character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127 ? '_' : character).join('')
  let stem = clean.replace(/\.[^.]+$/, '').replace(/[<>:"/\\|?*\u202a-\u202e\u2066-\u2069]/g, '_').replace(/[. ]+$/, '').slice(0, 140)
  if (!stem || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem)) stem = `video_${stem || 'subtitles'}`
  return `${stem}${supplied ? '' : '_subtitles'}.srt`
}

const CueLine = memo(function CueLine({ cue, pattern, isCurrent, canSeek, onSeek }: {
  cue: Cue; pattern: string; isCurrent: boolean; canSeek: boolean; onSeek: Props['onSeek']
}) {
  const highlighted = useMemo(() => {
    if (!pattern) return cue.text
    const parts = []
    let end = 0
    for (const match of cue.text.matchAll(new RegExp(pattern, 'giu'))) {
      parts.push(cue.text.slice(end, match.index), <mark key={match.index}>{match[0]}</mark>)
      end = match.index + match[0].length
    }
    parts.push(cue.text.slice(end))
    return parts
  }, [cue.text, pattern])
  return <li className={`tp-cue${isCurrent ? ' is-current' : ''}`}>
    <button type="button" disabled={!canSeek} aria-current={isCurrent ? 'true' : undefined} onClick={() => onSeek(cue.start)} title={canSeek ? `Jump to ${cueTime(cue.start)}` : 'Video preview is unavailable'}>
      <span className="tp-cue-time"><time dateTime={`PT${cue.start}S`}>{cueTime(cue.start)}</time><small>{cueTime(cue.end)}</small>{isCurrent && <span className="tp-now">Current line</span>}</span>
      <span className="tp-cue-text">{cue.speaker && <strong>{cue.speaker}</strong>}<span>{highlighted}</span></span>
      <Icon name="play" size={14} className="tp-cue-play" />
    </button>
  </li>
})

export function TranscriptPanel({ asset, onSeek, currentTime, active }: Props) {
  const [query, setQuery] = useState('')
  const [retry, setRetry] = useState(0)
  const [availability, setAvailability] = useState<{ key: string; data: ExportOptions | null; error: string }>({ key: '', data: null, error: '' })
  const [downloading, setDownloading] = useState(false)
  const [feedback, setFeedback] = useState({ key: '', error: '', notice: '' })
  const controllerRef = useRef<AbortController | null>(null)
  const objectUrlRef = useRef<string | null>(null)
  const searchId = useId()
  const transcriptSignature = JSON.stringify(asset.transcript || [])
  const sourceDuration = asset.technical.duration_seconds
  const { cues, skipped } = useMemo(() => timedCues(JSON.parse(transcriptSignature) as TranscriptEntry[], sourceDuration), [transcriptSignature, sourceDuration])
  const search = useMemo(() => {
    const terms = [...new Set(query.toLowerCase().match(/[\p{L}\p{N}_]+/gu) || [])]
    return { tests: terms.map(term => new RegExp(tokenSource(term), 'iu')), pattern: terms.sort((a, b) => b.length - a.length).map(tokenSource).join('|') }
  }, [query])
  const visibleCues = useMemo(() => cues.filter(cue => search.tests.every(test => test.test(cue.text))), [cues, search])
  const searching = search.tests.length > 0
  const jobId = asset.job_id
  const run = asset.analysis_config?.started_at ?? ''
  const feedbackKey = JSON.stringify([jobId, run])
  const downloadError = feedback.key === feedbackKey ? feedback.error : ''
  const notice = feedback.key === feedbackKey ? feedback.notice : ''
  const running = ['queued', 'analyzing', 'processing'].includes(asset.status)
  const retained = running && cues.length > 0 && (!asset.analysis_progress || asset.analysis_progress.completed_shots === 0)
  const requestKey = JSON.stringify([jobId, run, transcriptSignature, sourceDuration, retry])
  const current = availability.key === requestKey ? availability : null
  const srtOption = current?.data?.formats.find(option => option.format === 'srt')
  const loading = cues.length > 0 && current === null
  const canDownload = active && !downloading && !loading && !!srtOption?.available
  const canSeek = !!asset.preview_url
  const emptyMessage = cues.length > 0 ? 'No dialogue matches your search.'
    : asset.technical.has_audio === false ? 'This video has no audio track.'
      : running ? 'Waiting for the first dialogue lines…'
        : skipped ? 'No dialogue with valid timing is available.'
          : asset.status === 'error' ? 'No timed dialogue was saved before analysis stopped.'
            : asset.flash || ['complete', 'completed', 'done'].includes(asset.status) ? 'No spoken dialogue was found in the saved analysis.'
              : 'Analyze the video to transcribe its speech.'

  useEffect(() => {
    if (!active || cues.length === 0) return
    const controller = new AbortController()
    getExportOptions(jobId, undefined, run, controller.signal).then(data => {
      if (!controller.signal.aborted) setAvailability({ key: requestKey, data, error: '' })
    }).catch(cause => {
      if (!controller.signal.aborted) setAvailability({ key: requestKey, data: null, error: errorMessage(cause) })
    })
    return () => controller.abort()
  }, [active, cues.length, jobId, run, requestKey])

  useEffect(() => () => {
    controllerRef.current?.abort()
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
  }, [jobId, run])

  async function downloadSubtitles() {
    if (!canDownload) return
    const controller = new AbortController()
    controllerRef.current = controller
    setDownloading(true); setFeedback({ key: feedbackKey, error: '', notice: '' })
    try {
      const response = await fetch(exportUrl(jobId, 'srt', undefined, run), { signal: controller.signal })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : `Subtitle export failed (${response.status}). Please try again.`)
      }
      const blob = await response.blob()
      if (controller.signal.aborted) return
      const objectUrl = URL.createObjectURL(blob)
      objectUrlRef.current = objectUrl
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = subtitleFilename(response.headers.get('Content-Disposition'), asset.metadata.title || asset.filename)
      document.body.appendChild(anchor)
      anchor.click(); anchor.remove()
      window.setTimeout(() => {
        URL.revokeObjectURL(objectUrl)
        if (objectUrlRef.current === objectUrl) objectUrlRef.current = null
      }, 1000)
      setFeedback({ key: feedbackKey, error: '', notice: 'SRT download started with all saved dialogue.' })
    } catch (cause) {
      if (!controller.signal.aborted) setFeedback({ key: feedbackKey, error: errorMessage(cause), notice: '' })
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null
        setDownloading(false)
      }
    }
  }

  return <section className="tp-panel" aria-label="Video dialogue transcript">
    <header className="tp-header"><h2>Transcript</h2><button type="button" className="tp-download" disabled={!canDownload} onClick={() => void downloadSubtitles()} title="Export all saved dialogue, including lines outside your search"><Icon name="download" size={16} />{downloading ? 'Preparing SRT…' : 'Download SRT'}</button></header>
    <div className="tp-search"><Icon name="search" size={17} /><label className="lw-sr-only" htmlFor={searchId}>Search dialogue</label><input id={searchId} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search dialogue…" autoComplete="off" />{query && <button type="button" aria-label="Clear dialogue search" onClick={() => setQuery('')}><Icon name="close" size={15} /></button>}</div>
    <div className="tp-summary"><span role="status">{searching ? `${visibleCues.length} of ${cues.length} lines match` : `${cues.length} ${cues.length === 1 ? 'line' : 'lines'}`}</span>{cues.length > 0 && <span>Click a line to jump to its time.</span>}</div>
    {running && <p className="tp-hint" role="status">{retained ? 'Previous dialogue is shown until this analysis returns new clips.' : 'Dialogue appears as analysis completes. Downloads include the lines saved so far.'}</p>}
    {asset.status === 'error' && cues.length > 0 && <p className="tp-hint">Analysis stopped. Saved dialogue may be incomplete.</p>}
    {loading && <p className="tp-hint" role="status">Checking subtitle export…</p>}
    {!!skipped && <p className="tp-hint">{skipped} {skipped === 1 ? 'entry has' : 'entries have'} missing text or invalid timing and {skipped === 1 ? 'is' : 'are'} hidden.</p>}
    {srtOption && !srtOption.available && <p className="tp-alert">{srtOption.reason || 'Subtitle export is unavailable for this transcript.'}</p>}
    {current?.error && <div className="tp-alert" role="alert"><p>{current.error}</p><button type="button" onClick={() => setRetry(value => value + 1)}>Check again</button></div>}
    {downloadError && <p className="tp-alert" role="alert">{downloadError}</p>}
    {notice && <p className="tp-notice" role="status">{notice}</p>}
    {!canSeek && cues.length > 0 && <p className="tp-hint">Video preview is unavailable. You can still read, search, and export the dialogue.</p>}
    {visibleCues.length > 0 ? <ol className="tp-cues">{visibleCues.map(cue => <CueLine key={cue.key} cue={cue} pattern={search.pattern} isCurrent={active && Number.isFinite(currentTime) && currentTime >= cue.start && currentTime < cue.end} canSeek={canSeek} onSeek={onSeek} />)}</ol> : <div className="tp-empty"><Icon name="volume" size={26} /><p>{emptyMessage}</p>{searching && cues.length > 0 && <button type="button" onClick={() => setQuery('')}>Clear search</button>}</div>}
    {cues.length > 0 && <p className="tp-footnote">AI transcript and timing are approximate. Review before publishing subtitles.</p>}
  </section>
}
