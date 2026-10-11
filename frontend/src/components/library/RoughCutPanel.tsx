import { useEffect, useMemo, useRef, useState } from 'react'
import { cancelRoughCut, generateRoughCut, getRoughCut, roughCutExportUrl, saveRoughCut } from '../../api/library'
import type { AssetDetail, CutClipEdit, CutMoment, RoughCutDraft } from '../../api/library'
import { duration, errorMessage } from './format'
import { SearchHighlight } from './SearchHighlight'
import { useSettingsStore } from '../../stores/settingsStore'
import './RoughCutPanel.css'

interface Props { asset: AssetDetail; active: boolean; onRefresh: () => void; onConfigure: () => void; configured: boolean }
const isRunning = (draft?: RoughCutDraft | null) => !!draft && ['queued', 'analyzing', 'assembling'].includes(draft.status)
const editsFor = (draft?: RoughCutDraft | null): CutClipEdit[] => (draft?.clips || []).map(({ moment_id, start_seconds, end_seconds }) => ({ moment_id, start_seconds, end_seconds }))

export function RoughCutPanel({ asset, active, onRefresh, onConfigure, configured }: Props) {
  const proxyMode = useSettingsStore(state => state.proxyMode)
  const [draft, setDraft] = useState<RoughCutDraft | null>(asset.rough_cut || null)
  const [edits, setEdits] = useState<CutClipEdit[]>(() => editsFor(asset.rough_cut))
  const [mode, setMode] = useState<RoughCutDraft['mode']>(asset.rough_cut?.mode || 'cleanup')
  const [minutes, setMinutes] = useState(Math.min(10, Math.max(0.5, (asset.technical.duration_seconds || 1200) / 120)))
  const [brief, setBrief] = useState(asset.rough_cut?.brief || '')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(false)
  const player = useRef<HTMLVideoElement>(null)
  const sequence = useRef<{ clips: CutClipEdit[]; index: number } | null>(null)
  const dirty = JSON.stringify(edits) !== JSON.stringify(editsFor(draft))
  const dirtyRef = useRef(dirty)
  const draftRef = useRef(draft)
  const mutationRef = useRef(0)
  useEffect(() => { dirtyRef.current = dirty }, [dirty])
  useEffect(() => { draftRef.current = draft }, [draft])
  const running = isRunning(draft)
  const sourceBusy = ['queued', 'analyzing', 'processing', 'deleting'].includes(asset.status)
  const moments = useMemo(() => new Map((draft?.moments || []).map(moment => [moment.moment_id, moment])), [draft?.moments])
  const selected = useMemo(() => new Set(edits.map(clip => clip.moment_id)), [edits])
  const total = Math.round(edits.reduce((sum, clip) => sum + clip.end_seconds - clip.start_seconds, 0) * 1000) / 1000
  const valid = edits.length > 0 && edits.every(c => Number.isFinite(c.start_seconds) && Number.isFinite(c.end_seconds) && c.end_seconds > c.start_seconds)
  const shown = edits.map((clip, index) => ({ clip, index, moment: moments.get(clip.moment_id) })).filter(({ moment }) => !query || `${moment?.text} ${moment?.visual} ${moment?.reason}`.toLowerCase().includes(query.toLowerCase()))
  const displayPage = Math.min(page, Math.max(0, Math.ceil(shown.length / 30) - 1))
  const rows = shown.slice(displayPage * 30, (displayPage + 1) * 30)

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      const mutation = mutationRef.current
      try {
        const value = await getRoughCut(asset.job_id, controller.signal)
        if (controller.signal.aborted) return
        if (mutation !== mutationRef.current) {
          if (isRunning(draftRef.current)) timer = setTimeout(poll, 3000)
          return
        }
        if (dirtyRef.current && value?.revision !== draftRef.current?.revision) {
          setError('The cut changed elsewhere. Discard local changes to load the latest cut.')
          return
        }
        setDraft(value)
        if (!dirtyRef.current) setEdits(editsFor(value))
        if (isRunning(value)) timer = setTimeout(poll, 3000)
      } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)) }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [active, asset.job_id, running])

  useEffect(() => {
    if (!active) { player.current?.pause(); sequence.current = null }
    return () => { sequence.current = null }
  }, [active])

  function stop() { sequence.current = null; player.current?.pause(); setPlaying(false) }
  function preview(clips: CutClipEdit[]) {
    const video = player.current
    if (!video || !clips.length || clips.some(c => !Number.isFinite(c.start_seconds) || !Number.isFinite(c.end_seconds) || c.end_seconds <= c.start_seconds)) return
    sequence.current = { clips: [...clips], index: 0 }
    video.currentTime = clips[0].start_seconds
    video.play().then(() => setPlaying(true)).catch(() => { sequence.current = null; setError('Press play in the video to start preview.') })
  }
  function advance() {
    const video = player.current, current = sequence.current
    if (!video || !current) return
    if (video.currentTime >= current.clips[current.index].end_seconds) {
      current.index++
      if (current.index >= current.clips.length) { stop(); return }
      video.currentTime = current.clips[current.index].start_seconds
      video.play().catch(() => stop())
    }
  }
  function move(index: number, direction: number) {
    stop()
    setEdits(previous => { const next = [...previous]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; return next })
  }
  function trim(index: number, field: 'start_seconds' | 'end_seconds', value: number) {
    stop(); setEdits(previous => previous.map((clip, i) => i === index ? { ...clip, [field]: value } : clip))
  }
  async function generate() {
    if (!configured) { onConfigure(); return }
    mutationRef.current++; stop(); setWorking(true); setError('')
    try {
      const value = await generateRoughCut(asset.job_id, mode, Math.round(minutes * 60), brief, proxyMode)
      setDraft(value); setEdits([]); setPage(0); onRefresh()
    } catch (cause) { setError(errorMessage(cause)) } finally { setWorking(false) }
  }
  async function save() {
    if (!draft) return
    mutationRef.current++; stop(); setWorking(true); setError('')
    try { const value = await saveRoughCut(asset.job_id, draft.revision, edits); setDraft(value); setEdits(editsFor(value)); onRefresh() }
    catch (cause) { setError(errorMessage(cause)) } finally { setWorking(false) }
  }
  async function cancel() {
    mutationRef.current++
    setWorking(true); setError('')
    try { await cancelRoughCut(asset.job_id); onRefresh() }
    catch (cause) { setError(errorMessage(cause)) } finally { setWorking(false) }
  }
  async function discard() {
    mutationRef.current++; stop(); setWorking(true); setError('')
    try { const value = await getRoughCut(asset.job_id); setDraft(value); setEdits(editsFor(value)); setPage(0) }
    catch (cause) { setError(errorMessage(cause)) } finally { setWorking(false) }
  }
  async function download(format: 'fcpxml' | 'json') {
    if (!draft || dirty) return
    setWorking(true); setError('')
    let url: string | undefined
    try {
      const response = await fetch(roughCutExportUrl(asset.job_id, draft.revision, format))
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || 'Export failed') }
      url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = `rough_cut.${format}`; link.click()
    } catch (cause) { setError(errorMessage(cause)) }
    finally { if (url) setTimeout(() => URL.revokeObjectURL(url!), 1000); setWorking(false) }
  }
  function add(moment: CutMoment) { stop(); setEdits(previous => [...previous, { moment_id: moment.moment_id, start_seconds: moment.start_seconds, end_seconds: moment.end_seconds }]) }

  return <section className="rc-panel" aria-label="Podcast and talking-head rough cut">
    <div className="rc-heading"><div><h2>Rough cut</h2><p>Find the clearest take. Keep the dialogue and on-screen moments that matter.</p></div>
      {running ? <button className="lw-button" disabled={working} onClick={cancel}>Cancel rough cut</button> :
        <button className="lw-button lw-button-primary" disabled={working || sourceBusy || !asset.preview_url || asset.technical.has_audio === false || dirty || (mode === 'condense' && (!Number.isFinite(minutes) || minutes < 0.5 || minutes > 120))} onClick={generate}>{working ? 'Working…' : draft ? 'Generate again' : 'Generate rough cut'}</button>}</div>
    <div className="rc-controls">
      <label className="lw-field">Edit style<select value={mode} disabled={running || working} onChange={event => setMode(event.target.value as RoughCutDraft['mode'])}><option value="cleanup">Clean up the take</option><option value="condense">Make it shorter</option></select></label>
      {mode === 'condense' && <label className="lw-field">Target minutes<input type="number" min="0.5" max="120" step="0.5" value={minutes} disabled={running || working} onChange={event => setMinutes(event.target.valueAsNumber)} /></label>}
      <label className="lw-field rc-brief">Editing brief<textarea rows={2} maxLength={3000} value={brief} disabled={running || working} onChange={event => setBrief(event.target.value)} placeholder="Keep the product demonstration. Remove repeated explanations. Preserve the speaker’s tone." /></label>
    </div>
    {!asset.preview_url && <p>Import a local recording to build a rough cut.</p>}
    {asset.technical.has_audio === false && <p>This video has no audio track.</p>}
    <p className="lw-model-note">Gemini listens to the recording and checks the picture. Additional API charges apply.</p>
    {error && <p className="lw-alert lw-alert-error" role="alert">{error}</p>}
    {draft?.error && <p className="lw-alert lw-alert-error" role="alert">{draft.error}</p>}
    {running && <div className="rc-progress" role="status"><strong>{draft?.progress}</strong><span>{draft?.completed_windows}/{draft?.total_windows} sections · {draft?.moments.length} speech excerpts found</span></div>}
    <div className="rc-workspace"><div className="rc-preview">
      {asset.preview_url && <video ref={player} src={asset.preview_url} controls preload="metadata" onTimeUpdate={advance} onEnded={advance} onPause={() => setPlaying(false)} onPlay={() => setPlaying(true)} onSeeking={event => { const current = sequence.current; if (current) { const clip = current.clips[current.index]; if (event.currentTarget.currentTime < clip.start_seconds - 0.05 || event.currentTarget.currentTime > clip.end_seconds + 0.05) { sequence.current = null } } }} />}
      <div className="rc-actions"><button className="lw-button" disabled={!valid || running || !asset.preview_url} onClick={() => playing ? stop() : preview(edits)}>{playing ? 'Stop preview' : 'Play rough cut'}</button><strong>{duration(total)} / {duration(draft?.source_seconds || asset.technical.duration_seconds)} source</strong></div>
      <p className="lw-model-note">Preview skips between source excerpts. AI timing is approximate; review the joins.</p>
      {draft?.rationale && <details className="lw-details"><summary>Why these excerpts?</summary><p>{draft.rationale}</p></details>}
      {draft?.warnings.map(warning => <p className="lw-model-note" key={warning}>{warning}</p>)}
      {draft?.cost_estimate?.estimated_cost_usd != null && <p className="lw-model-note">Estimated API cost for this draft: ${draft.cost_estimate.estimated_cost_usd.toFixed(4)} USD</p>}
      {draft?.status === 'ready' && <div className="rc-actions"><button className="lw-button lw-button-primary" disabled={!dirty || !valid || working} onClick={save}>{dirty ? 'Save changes' : 'Saved'}</button><button className="lw-button" disabled={dirty || working || !valid} onClick={() => download('fcpxml')}>Export timeline XML</button><button className="lw-text-button" disabled={dirty || working || !valid} onClick={() => download('json')}>JSON</button></div>}
    {dirty && <div className="rc-actions"><p role="status">Save your changes before exporting or generating again.</p><button className="lw-text-button" disabled={working} onClick={discard}>Discard changes</button></div>}
      {draft?.status === 'ready' && <p className="lw-model-note">Import timeline XML into Resolve or Final Cut Pro, then relink to the original recording. Media is not copied.</p>}
    </div><div className="rc-excerpts">
      <label className="lw-field">Search excerpts<input type="search" value={query} onChange={event => { setQuery(event.target.value); setPage(0) }} placeholder="Search dialogue, gestures or edit notes" /></label>
      {rows.length > 0 && <ol className="rc-list">{rows.map(({ clip, index, moment }) => <li key={clip.moment_id}>
        <div className="rc-row-heading"><button className="lw-text-button" onClick={() => preview([clip])} disabled={!asset.preview_url}>Play excerpt {index + 1}</button><div><button className="lw-button" aria-label={`Move excerpt ${index + 1} earlier`} disabled={index === 0 || working} onClick={() => move(index, -1)}>↑</button><button className="lw-button" aria-label={`Move excerpt ${index + 1} later`} disabled={index === edits.length - 1 || working} onClick={() => move(index, 1)}>↓</button><button className="lw-text-button" disabled={working} onClick={() => { stop(); setEdits(previous => previous.filter((_, i) => i !== index)) }}>Remove</button></div></div>
        <p><SearchHighlight text={moment?.text || ''} query={query} /></p>
        {moment?.visual && <p className="lw-model-note"><SearchHighlight text={moment.visual} query={query} /></p>}
        {moment?.reason && <small><SearchHighlight text={moment.reason} query={query} /></small>}
        <div className="rc-trims"><label>In (seconds)<input type="number" step="0.01" min={moment?.window_start} max={clip.end_seconds} value={Number.isFinite(clip.start_seconds) ? clip.start_seconds : ''} disabled={working} onChange={event => trim(index, 'start_seconds', event.target.valueAsNumber)} /></label><label>Out (seconds)<input type="number" step="0.01" min={clip.start_seconds} max={moment?.window_end} value={Number.isFinite(clip.end_seconds) ? clip.end_seconds : ''} disabled={working} onChange={event => trim(index, 'end_seconds', event.target.valueAsNumber)} /></label></div>
      </li>)}</ol>}
      {shown.length > 30 && <div className="rc-actions"><button className="lw-button" disabled={displayPage === 0} onClick={() => setPage(displayPage - 1)}>Previous</button><span>{displayPage + 1}/{Math.ceil(shown.length / 30)}</span><button className="lw-button" disabled={(displayPage + 1) * 30 >= shown.length} onClick={() => setPage(displayPage + 1)}>Next</button></div>}
      {!edits.length && !running && <p>Generate a cut to review the selected excerpts here.</p>}
      {!!draft?.moments.length && <details className="lw-details"><summary>{running ? 'Speech found so far' : 'Other takes and omitted excerpts'}</summary>
        <div className="rc-omitted">{draft.moments.filter(moment => !selected.has(moment.moment_id) && (!query || `${moment.text} ${moment.visual}`.toLowerCase().includes(query.toLowerCase()))).map(moment => <article key={moment.moment_id}><button className="lw-text-button" onClick={() => preview([moment])}>{duration(moment.start_seconds)} · {moment.kind.replaceAll('_', ' ')}</button><p><SearchHighlight text={moment.text} query={query} /></p>{moment.reason && <small>{moment.reason}</small>}{draft.status === 'ready' && <button className="lw-button" disabled={working} onClick={() => add(moment)}>Add to cut</button>}</article>)}</div>
      </details>}
    </div></div>
  </section>
}
