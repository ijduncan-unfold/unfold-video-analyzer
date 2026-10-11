import { useCallback, useEffect, useRef, useState } from 'react'
import type { PointerEvent } from 'react'
import { getProjects } from '../../api/library'
import type { AssetDetail, LibraryProject } from '../../api/library'
import type { Shot } from '../../api/types'
import { visualFrame, visualRequest } from '../../api/visual'
import type { VisualIndex, VisualMatch, VisualResults, VisualShape } from '../../api/visual'
import { errorMessage, timestamp } from './format'
import { readMatchCutSession, saveMatchCutSession } from './matchCutSession'
import { ProjectSearchPicker } from './ProjectSearchPicker'
import { simplifyOutline } from './freehandOutline'
import './MatchCutPanel.css'

const AXES = ['shape', 'composition', 'color'] as const
const time = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(3).padStart(6, '0')}`
const midpoint = (shot: Shot) => (timestamp(shot.start_time) + timestamp(shot.end_time)) / 2

export function MatchCutPanel({ asset, shots, initialShot, useInitialShot = false }: { asset: AssetDetail; shots: Shot[]; initialShot?: Shot; useInitialShot?: boolean }) {
  const run = asset.analysis_config?.started_at || null
  const [saved] = useState(() => {
    const session = readMatchCutSession(asset.job_id, run)
    if (!session || !shots.some(s => s.shot_number === session.shotNumber)) return null
    if (!useInitialShot || !initialShot) return session
    return { ...session, shotNumber: initialShot.shot_number, seconds: midpoint(initialShot),
      region: null, sourceOutline: null, sourceShapeId: null, knownShapes: null, result: null, resultKey: '', searchIntent: '', selected: null }
  })
  const [shotNumber, setShotNumber] = useState(saved?.shotNumber ?? initialShot?.shot_number ?? shots[0]?.shot_number)
  const shot = shots.find(s => s.shot_number === shotNumber) || shots[0]
  const [seconds, setSeconds] = useState(saved?.seconds ?? (shot ? midpoint(shot) : 0))
  const [frameSeconds, setFrameSeconds] = useState(seconds)
  const [projects, setProjects] = useState<LibraryProject[]>([])
  const [targetIds, setTargetIds] = useState(saved?.targetIds ?? [asset.job_id])
  const [indexes, setIndexes] = useState<VisualIndex[]>(saved?.indexes ?? [])
  const [validatedScope, setValidatedScope] = useState('')
  const [weights, setWeights] = useState(saved?.weights ?? { shape: 1, composition: 0, color: 0 })
  const [region, setRegion] = useState<number[] | null>(saved?.region ?? null)
  const [sourceOutline, setSourceOutline] = useState<number[][] | null>(saved?.sourceOutline ?? null)
  const [drawingMode, setDrawingMode] = useState<'outline' | 'box'>(saved?.drawingMode ?? 'outline')
  const [sourceShapeId, setSourceShapeId] = useState<number | null>(saved?.sourceShapeId ?? null)
  const [alignShape, setAlignShape] = useState(saved?.alignShape ?? true)
  const [knownShapes, setKnownShapes] = useState<{ key: string; forms: VisualShape[] } | null>(saved?.knownShapes ?? null)
  const [result, setResult] = useState<VisualResults | null>(saved?.result ?? null)
  const [selected, setSelected] = useState<VisualMatch | null>(saved?.selected ?? null)
  const [incoming, setIncoming] = useState(saved?.incoming ?? 0)
  const [incomingFrame, setIncomingFrame] = useState(incoming)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [working, setWorking] = useState(false)
  const [preview, setPreview] = useState(false)
  const [handle, setHandle] = useState(saved?.handle ?? 2)
  const [indexErrors, setIndexErrors] = useState<string[]>([])
  const request = useRef<AbortController | null>(null)
  const sourceIndex = indexes.find(i => i.job_id === asset.job_id)
  const scopeKey = JSON.stringify([...new Set([asset.job_id, ...targetIds])])
  const searchKey = JSON.stringify([shotNumber, frameSeconds, targetIds, weights, region, sourceShapeId, alignShape, indexes.map(i => [i.job_id, i.revision, i.composition?.version, i.shape?.version]), ...(sourceOutline ? [sourceOutline] : [])])
  const [resultKey, setResultKey] = useState(saved?.resultKey ?? '')
  const [searchIntent, setSearchIntent] = useState(saved?.searchIntent ?? '')
  const ready = validatedScope === scopeKey
  const stale = !!result && (!ready || resultKey !== searchKey)
  const coverage = indexes.filter(i => targetIds.includes(i.job_id)).map(i => `${i.job_id}:${i.indexed}:${i.composition?.indexed || 0}:${i.shape?.indexed || 0}`).join('|')
  const lastCoverage = useRef('')
  const sourceRevision = sourceIndex?.revision
  const number = shot?.shot_number
  const shapeFrameKey = JSON.stringify([asset.job_id, number, frameSeconds, sourceRevision, sourceIndex?.shape?.version])
  const forms = knownShapes?.key === shapeFrameKey ? knownShapes.forms : []
  function selectRegion(value: number[] | null) { setRegion(value); setSourceShapeId(null); setSourceOutline(null) }
  function drawOutline(value: number[][]) { setSourceOutline(value); setRegion(null); setSourceShapeId(null); setSelected(null); setError('') }
  async function identify() {
    if (!number || !sourceRevision) return
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    setWorking(true); setError('')
    try {
      const data = await visualRequest<{ forms: VisualShape[] }>(`${asset.job_id}/shapes`, {
        shot_number: number, seconds: frameSeconds, revision: sourceRevision,
      }, controller.signal)
      if (!controller.signal.aborted) {
        setKnownShapes({ key: shapeFrameKey, forms: data.forms })
        if (!data.forms.length) setNotice('No clear silhouettes found. Try another frame.')
      }
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)) }
    finally { if (!controller.signal.aborted) setWorking(false) }
  }

  useEffect(() => {
    const controller = new AbortController()
    getProjects(controller.signal).then(data => setProjects(data.projects.filter(p => p.shot_count > 0))).catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)) })
    return () => { controller.abort(); request.current?.abort() }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const ids = JSON.parse(scopeKey) as string[]
      const rows = await Promise.all(ids.map(async id => {
        try { return { data: await visualRequest<VisualIndex>(`${encodeURIComponent(id)}/index`, undefined, controller.signal) } }
        catch (err) { return { error: errorMessage(err) } }
      }))
      if (controller.signal.aborted) return
      setValidatedScope(rows.every(row => row.data) ? scopeKey : '')
      setIndexes(rows.flatMap(row => row.data ? [row.data] : []))
      setIndexErrors([...new Set(rows.flatMap(row => row.error ? [row.error] : []))])
      timer = setTimeout(poll, 1800)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [scopeKey])
  useEffect(() => {
    const timer = setTimeout(() => setFrameSeconds(seconds), 250)
    return () => clearTimeout(timer)
  }, [seconds])
  useEffect(() => {
    const timer = setTimeout(() => setIncomingFrame(incoming), 250)
    return () => clearTimeout(timer)
  }, [incoming])
  useEffect(() => { request.current?.abort(); setWorking(false) }, [searchKey])

  useEffect(() => {
    if (!shotNumber) return
    // Persist user state, not playback, errors or in-flight request flags. Indexes
    // are revalidated before saved candidates can be previewed or exported.
    saveMatchCutSession(asset.job_id, { run, shotNumber, seconds, targetIds, weights,
      region, sourceOutline, drawingMode, sourceShapeId, alignShape, knownShapes, indexes, result, resultKey,
      searchIntent, selected, incoming, handle })
  }, [asset.job_id, run, shotNumber, seconds, targetIds, weights, region, sourceOutline, drawingMode, sourceShapeId,
    alignShape, knownShapes, indexes, result, resultKey, searchIntent, selected, incoming, handle])

  async function build() {
    setError('')
    try {
      for (const id of targetIds) await visualRequest(`${encodeURIComponent(id)}/index`, {})
      setNotice('Search as frames arrive; search again for newly indexed clips.')
    } catch (err) { setError(errorMessage(err)) }
  }
  const find = useCallback(async (automatic = false) => {
    if (!number || !sourceRevision || !ready) return
    if (!automatic) setSearchIntent(searchKey)
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    lastCoverage.current = coverage
    setWorking(true); setError(''); setNotice('')
    try {
      const data = await visualRequest<VisualResults>(`${asset.job_id}/search`, {
        shot_number: number, seconds: frameSeconds, revision: sourceRevision,
        target_ids: targetIds, ...weights, region: weights.shape ? region : null, prepare_composition: !automatic, prepare_shape: !automatic, source_shape_id: sourceShapeId, align_shape: alignShape, source_outline: weights.shape ? sourceOutline : null,
      }, controller.signal)
      if (controller.signal.aborted) return
      setResult(data); setResultKey(searchKey)
      if (weights.shape) setKnownShapes({ key: shapeFrameKey, forms: data.source_shapes || [] })
      if (!automatic) setSelected(null)
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)) }
    finally { if (!controller.signal.aborted) setWorking(false) }
  }, [number, sourceRevision, ready, coverage, asset.job_id, frameSeconds, targetIds, weights, region, sourceOutline, sourceShapeId, alignShape, shapeFrameKey, searchKey])
  useEffect(() => {
    // Returning to an unfinished search only reads cached analysis. It never
    // starts additional Gemini work; active server jobs keep their own progress.
    if (!ready || searchIntent !== searchKey || working || coverage === lastCoverage.current) return
    const timer = setTimeout(() => { void find(true) }, 300)
    return () => clearTimeout(timer)
  }, [ready, searchIntent, searchKey, working, coverage, find])
  function choose(match: VisualMatch) { setSelected(match); setIncoming(match.seconds); setIncomingFrame(match.seconds) }
  function exportPair() {
    if (!selected || !shot || stale) return
    const data = { version: 1, type: 'visual_match_cut', timing: 'media_file_seconds', timestamp_accuracy: 'approximate; verify in editor',
      outgoing: { job_id: asset.job_id, filename: asset.filename, shot_number: shot.shot_number, revision: sourceIndex?.revision,
        start_seconds: Math.max(timestamp(shot.start_time), frameSeconds - handle), end_seconds: frameSeconds },
      incoming: { job_id: selected.job_id, title: selected.title, filename: selected.filename, shot_number: selected.shot_number, revision: selected.revision,
        start_seconds: incoming, end_seconds: Math.min(selected.end_seconds, incoming + handle) },
      discovery: { sampled_outgoing_seconds: frameSeconds, sampled_incoming_seconds: selected.seconds,
        scores: selected.scores, score_basis: result?.score_basis || 'local_visual_measurements', weights, source_region: region, source_outline: sourceOutline, source_shape: result?.source_shape, target_outline: selected.target_outline, target_shape: selected.shape_label, shape_geometry: selected.shape_geometry, align_shape: alignShape } }
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = `match-cut-${shot.shot_number}-${selected.shot_number}.json`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  if (!shot || !asset.preview_url) return <p className="lw-panel-empty">Import and analyze a local video to discover match cuts.</p>
  const start = timestamp(shot.start_time), end = timestamp(shot.end_time)
  const step = Math.min(1 / (asset.technical.frame_rate || 24), (end - start) / 2)
  const targets = indexes.filter(i => targetIds.includes(i.job_id))
  const available = targets.reduce((sum, index) => sum + index.indexed, 0)
  const readingComposition = targets.some(i => i.composition?.running)
  const readingShape = targets.some(i => i.shape?.running)
  const partialSearch = targets.some(i => i.status !== 'complete' ||
    (weights.shape > 0 && (i.shape?.indexed || 0) < (i.shape?.total || 0)) ||
    (weights.composition > 0 && (i.composition?.indexed || 0) < (i.composition?.total || 0)))
  const selectedForm = sourceOutline ? { label: 'Drawn outline', outline: sourceOutline } : !stale && result?.source_shape ? result.source_shape : region ? null : forms[sourceShapeId ?? 0]
  function pickForm(point: number[]) {
    const hits = forms.map((form, id) => ({ form, id })).filter(({ form }) => contains(form.outline, point))
    hits.sort((a, b) => a.form.box[2]*a.form.box[3]-b.form.box[2]*b.form.box[3])
    if (hits.length) { setSourceShapeId(hits[0].id); setRegion(null); setSourceOutline(null) }
  }
  const sourceBox = !stale && weights.composition && result?.source_composition?.subject_box
  const subjectBox = sourceBox ? [sourceBox[1] / 1000, sourceBox[0] / 1000, (sourceBox[3] - sourceBox[1]) / 1000, (sourceBox[2] - sourceBox[0]) / 1000] : null
  return <section className="mc-panel" aria-label="Visual match-cut discovery">
    <div className="mc-heading"><div><h2>Match cuts</h2><p>Match shape, composition, and color across your footage.</p></div><button className="lw-button lw-button-primary mc-find" onClick={() => find()} disabled={!ready || working || !available || !targetIds.length || !sourceIndex || seconds !== frameSeconds || !Object.values(weights).some(Boolean)}>{working ? weights.composition ? 'Reading composition…' : 'Finding matches…' : 'Find match cut'}</button></div>
    <div className="mc-controls">
      <label className="lw-field">Outgoing clip<select aria-label="Outgoing clip" value={shot.shot_number} onChange={event => {
        const next = shots.find(s => s.shot_number === Number(event.target.value))!
        setShotNumber(next.shot_number); setSeconds(midpoint(next)); setFrameSeconds(midpoint(next)); setRegion(null); setSourceShapeId(null); setSourceOutline(null); setSelected(null)
      }}>{shots.map(s => <option key={s.shot_number} value={s.shot_number}>Clip {String(s.shot_number).padStart(2, '0')} · {s.start_time}</option>)}</select></label>
      <ProjectSearchPicker projects={projects} selectedIds={targetIds} onChange={setTargetIds} />
      <div className="mc-weights">{AXES.map(axis => <label key={axis}>{axis}<input aria-label={`${axis} weight`} type="range" min="0" max="1" step="0.1" value={weights[axis]} onChange={event => setWeights(w => ({ ...w, [axis]: Number(event.target.value) }))} /><span>{weights[axis] === 0 ? 'Off' : `${Math.round(weights[axis] * 100)}%`}</span></label>)}</div>
    </div>
    <div className="mc-index"><div role="status">{targets.some(i => i.status === 'indexing') ? 'Indexing' : 'Visual index'} · {available} / {targets.reduce((n, i) => n + i.total, 0)} frames{targets.some(i => i.failed) ? ' · Some frames failed; resume to retry.' : ''}</div>
      <button className="lw-button" disabled={!targetIds.length || targets.some(i => i.status === 'indexing') || (targets.length === targetIds.length && targets.every(i => i.status === 'complete'))} onClick={build}>{targets.some(i => i.status === 'indexing') ? 'Indexing…' : targets.every(i => i.status === 'complete') && targets.length ? 'Frames ready' : available ? 'Resume indexing' : 'Build visual index'}</button>
      {(targets.some(i => i.status === 'indexing') || readingComposition || readingShape) && <button className="lw-text-button" onClick={async () => { try { await Promise.all(targetIds.map(id => visualRequest(`${id}/index/cancel`, {}))); setNotice('Indexing paused. Saved frame analysis remains searchable.') } catch (err) { setError(errorMessage(err)) } }}>Pause</button>}
      <small>{weights.shape || weights.composition ? 'Shape and composition use Gemini · frame analysis is cached' : 'Color uses the local index · no API calls'}</small>
    </div>
    {(weights.composition > 0 || readingComposition) && <div className="mc-composition-status" role="status">{readingComposition ? 'Understanding composition' : targets.some(i => i.composition?.indexed) ? 'Composition analyzed' : 'Composition not started'} · {targets.reduce((n, i) => n + (i.composition?.indexed || 0), 0)} / {targets.reduce((n, i) => n + (i.composition?.total || 0), 0)} frames{readingComposition && ' · Matches update as frames are understood.'}</div>}
    {(weights.shape > 0 || readingShape) && <div className="mc-composition-status" role="status">{readingShape ? 'Identifying shapes' : targets.some(i => i.shape?.indexed) ? 'Shapes analyzed' : 'Shape analysis not started'} · {targets.reduce((n, i) => n + (i.shape?.indexed || 0), 0)} / {targets.reduce((n, i) => n + (i.shape?.total || 0), 0)} frames{readingShape && ' · Matches update as silhouettes arrive.'}</div>}
    {[...indexErrors, ...indexes.flatMap(i => [i.error, weights.composition ? i.composition?.error : null, weights.shape ? i.shape?.error : null].filter((e): e is string => !!e)), ...(error ? [error] : [])].map((message, i) => <p className="lw-inline-error" role="alert" key={i}>{message}</p>)}
    {notice && <p role="status">{notice}</p>}
    <div className="mc-pair">
      <div><div className="mc-frame-heading"><strong>A · Outgoing</strong><span>{time(frameSeconds)}</span></div>
        <FrameRegion key={`${shot.shot_number}:${frameSeconds}`} drawingMode={drawingMode} onOutline={weights.shape ? drawOutline : undefined} onDrawError={() => setError('Trace a larger outline around the subject without crossing the line.')} label="Outgoing frame" url={sourceIndex ? visualFrame(asset.job_id, shot.shot_number, frameSeconds, sourceIndex.revision) : undefined} region={weights.shape && (stale || !result?.source_shape) ? region : null} box={weights.shape ? null : subjectBox || (!stale ? selected?.source_box : null)} outline={weights.shape ? selectedForm?.outline : null} outlines={weights.shape && !sourceOutline && drawingMode === 'box' ? forms.map(f => f.outline) : undefined} onPick={weights.shape ? pickForm : undefined} onRegion={weights.shape ? selectRegion : undefined} />
        {!stale && weights.composition > 0 && result?.source_composition && <p className="mc-composition-summary">{result.source_composition.summary}<small>{weights.shape ? 'Framing: Gemini interpretation' : 'Outlined: dominant subject · Gemini interpretation'}</small></p>}
        <div className="mc-trim"><input type="range" aria-label="Outgoing cut frame" min={start} max={Math.max(start, end - step)} step={step} value={seconds} onChange={event => { setSeconds(Number(event.target.value)); setRegion(null); setSourceShapeId(null); setSourceOutline(null) }} /><input type="number" aria-label="Outgoing cut seconds" min={start} max={end - step} step={step} value={Number(seconds.toFixed(3))} onChange={event => { if (event.target.value !== '') { setSeconds(Math.min(end - step, Math.max(start, Number(event.target.value)))); setRegion(null); setSourceShapeId(null); setSourceOutline(null) } }} /></div>
        {weights.shape > 0 && <div className="mc-shape-controls">
          <div className="lw-segmented" aria-label="Shape selection tool"><button aria-pressed={drawingMode === 'outline'} className={drawingMode === 'outline' ? 'is-active' : ''} onClick={() => setDrawingMode('outline')}>Draw outline</button><button aria-pressed={drawingMode === 'box'} className={drawingMode === 'box' ? 'is-active' : ''} onClick={() => setDrawingMode('box')}>Select detected shape</button></div>
          <button className="lw-button" onClick={identify} disabled={working || !sourceRevision || seconds !== frameSeconds}>Identify shapes</button>
          {!!forms.length && <label className="lw-field">Shape<select aria-label="Source shape" value={sourceOutline ? 'drawn' : sourceShapeId ?? ''} onChange={event => { if (event.target.value === 'drawn') return; setSourceShapeId(event.target.value === '' ? null : Number(event.target.value)); setRegion(null); setSourceOutline(null) }}>{sourceOutline && <option value="drawn">Drawn outline</option>}<option value="">{region ? 'Drawn selection' : 'Main visible form'}</option>{forms.map((form, id) => <option key={id} value={id}>{form.label}</option>)}</select></label>}
          <label className="mc-align"><input type="checkbox" checked={alignShape} onChange={event => setAlignShape(event.target.checked)} />Match position and size too</label>
          <small>{drawingMode === 'outline' ? 'Drag around the entire visible subject; release to close the outline. Your outline is used directly.' : 'Click an identified shape or drag a box to select one.'}</small>
          {selectedForm && <p>Matching: <strong>{selectedForm.label}</strong></p>}
          {(region || sourceOutline) && <button className="lw-text-button" onClick={() => selectRegion(null)}>Clear selection</button>}
        </div>}

      </div>
      <div><div className="mc-frame-heading"><strong>B · Incoming{selected ? ` · Clip ${selected.shot_number}` : ''}</strong><span>{selected ? time(incomingFrame) : ''}</span></div>
        {selected && !stale ? <><FrameRegion url={visualFrame(selected.job_id, selected.shot_number, incomingFrame, selected.revision)} box={Math.abs(incomingFrame - selected.seconds) < .001 ? selected.target_box : null} outline={Math.abs(incomingFrame - selected.seconds) < .001 ? selected.target_outline : null} />
          <div className="mc-trim"><input type="range" aria-label="Incoming cut frame" min={selected.start_seconds} max={selected.end_seconds - Math.min(.04, (selected.end_seconds - selected.start_seconds) / 2)} step="0.01" value={incoming} onChange={event => setIncoming(Number(event.target.value))} /><input type="number" aria-label="Incoming cut seconds" min={selected.start_seconds} max={selected.end_seconds - Math.min(.04, (selected.end_seconds - selected.start_seconds) / 2)} step="0.01" value={Number(incoming.toFixed(3))} onChange={event => { if (event.target.value !== '') setIncoming(Math.min(selected.end_seconds - Math.min(.04, (selected.end_seconds - selected.start_seconds) / 2), Math.max(selected.start_seconds, Number(event.target.value)))) }} /></div>
          <p>{selected.title}</p><div className="mc-pair-actions"><button className="lw-button lw-button-primary" disabled={frameSeconds <= start || seconds !== frameSeconds || incoming !== incomingFrame} onClick={() => setPreview(true)}>Play A → B</button><button className="lw-button" disabled={frameSeconds <= start || seconds !== frameSeconds || incoming !== incomingFrame} onClick={exportPair}>Export cut JSON</button><label>Preview each side <select value={handle} onChange={event => setHandle(Number(event.target.value))}>{[.5, 1, 2, 3, 5].map(v => <option key={v} value={v}>{v}s</option>)}</select></label></div>
          {Math.abs(incomingFrame - selected.seconds) > .001 && <small>Scores refer to the discovered frame. Review your adjusted cut in the preview.</small>}
        </> : <div className="mc-placeholder">{stale ? 'Search again to match your new selection.' : 'Choose a match below to preview the cut.'}</div>}
      </div>
    </div>
    {result && !stale && <><div className="mc-results-title"><h3>{result.matches.length} candidate{result.matches.length === 1 ? '' : 's'}{partialSearch ? ' so far' : ''}</h3><small>Relative visual similarity · not confidence. Motion is not scored yet.</small></div>
      {!result.matches.length && <p>{readingShape || readingComposition ? 'Still analyzing the film. Matches appear as frames are checked.' : partialSearch ? 'Search incomplete. Click Find match cut to resume analysis.' : weights.shape ? 'No close silhouettes found. Try another shape or search more shots.' : weights.composition ? 'No compatible framing found in the analyzed frames. Try another source frame or search more shots.' : 'No candidates yet. Index more clips, change the frame, or widen the region.'}</p>}
      <div className="mc-results">{result.matches.map(match => <button key={`${match.job_id}-${match.shot_number}`} className={`mc-card ${selected?.job_id === match.job_id && selected?.seconds === match.seconds ? 'is-selected' : ''}`} aria-pressed={selected?.job_id === match.job_id && selected?.seconds === match.seconds} onClick={() => choose(match)}><div style={{ aspectRatio: match.aspect_ratio }}><img src={match.frame_url} loading="lazy" alt={`Candidate clip ${match.shot_number} at ${time(match.seconds)}`} /><Box box={match.target_box} /><Outline points={match.target_outline} /></div><strong>Clip {String(match.shot_number).padStart(2, '0')} <time>{time(match.seconds)}</time></strong><span>{match.title}</span><p>{match.shape_summary || match.composition_summary || match.description}</p>{match.reasons && <div className="mc-reasons">{match.reasons.map(reason => <span key={reason}>{reason}</span>)}</div>}{match.shape_geometry && <div className="mc-reasons"><span>Position {Math.round(match.shape_geometry.position*100)}</span><span>Size {Math.round(match.shape_geometry.scale*100)}</span></div>}<div className="mc-scores">{AXES.filter(axis => weights[axis] > 0).map(axis => <span key={axis}>{axis} <b>{Math.round(match.scores[axis] * 100)}</b></span>)}</div></button>)}</div>
    </>}
    {preview && selected && !stale && <CutPreview sourceUrl={asset.preview_url} targetUrl={selected.media_url} aStart={Math.max(start, frameSeconds - handle)} aEnd={frameSeconds} bStart={incoming} bEnd={Math.min(selected.end_seconds, incoming + handle)} onClose={() => setPreview(false)} />}
  </section>
}

function contains(outline: number[][], point: number[]) {
  const [x, y] = point.map(v => v*1000)
  let inside = false
  for (let i = 0, j = outline.length-1; i < outline.length; j = i++) {
    const [a, b] = outline[i], [c, d] = outline[j]
    if ((b > y) !== (d > y) && x < (c-a)*(y-b)/(d-b)+a) inside = !inside
  }
  return inside
}
function Outline({ points, faint = false }: { points?: number[][] | null; faint?: boolean }) {
  return points ? <svg className={`mc-outline ${faint ? 'is-faint' : ''}`} viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true"><polygon points={points.map(p => p.join(',')).join(' ')} vectorEffect="non-scaling-stroke" /></svg> : null
}

function Box({ box }: { box?: number[] | null }) {
  return box ? <span className="mc-box" style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` }} /> : null
}

function FrameRegion({ url, region, box, outline, outlines, onPick, onRegion, onOutline, onDrawError, drawingMode = 'box', label = 'Incoming frame' }: { drawingMode?: 'outline' | 'box'; onOutline?: (points: number[][]) => void; onDrawError?: () => void; label?: string; url?: string; region?: number[] | null; box?: number[] | null; outline?: number[][] | null; outlines?: number[][][]; onPick?: (point: number[]) => void; onRegion?: (region: number[] | null) => void }) {
  const anchor = useRef<number[] | null>(null)
  const path = useRef<number[][]>([])
  const [trace, setTrace] = useState<number[][] | null>(null)
  const [drag, setDrag] = useState<number[] | null>(null)
  const [loaded, setLoaded] = useState('')
  const [failed, setFailed] = useState('')
  const [aspect, setAspect] = useState(16 / 9)
  function point(event: PointerEvent<HTMLDivElement>) { const rect = event.currentTarget.getBoundingClientRect(); return [Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))] }
  function rectangle(p: number[]) { const a = anchor.current!; return [Math.min(a[0], p[0]), Math.min(a[1], p[1]), Math.abs(a[0] - p[0]), Math.abs(a[1] - p[1])] }
  return <div className={`mc-frame ${onRegion ? 'can-select' : ''}`} style={{ aspectRatio: aspect }} aria-label={onRegion ? `${label}. ${drawingMode === 'outline' ? 'Drag to draw an outline.' : 'Drag to select a region.'}` : label} onPointerDown={event => { if (!onRegion || loaded !== url || event.button !== 0) return; anchor.current = point(event); path.current = [anchor.current.map(v => v * 1000)]; event.currentTarget.setPointerCapture(event.pointerId) }} onPointerMove={event => {
      if (!anchor.current) return
      if (drawingMode === 'outline' && onOutline) {
        const p = point(event).map(v => v * 1000), last = path.current[path.current.length - 1]
        if (path.current.length < 2048 && Math.hypot(p[0] - last[0], p[1] - last[1]) >= 2) { path.current.push(p); setTrace([...path.current]) }
      } else setDrag(rectangle(point(event)))
    }} onPointerUp={event => {
      if (!anchor.current) return
      const r = rectangle(point(event)); anchor.current = null; setDrag(null); setTrace(null)
      if (drawingMode === 'outline' && onOutline && path.current.length > 2) {
        const outline = simplifyOutline([...path.current, point(event).map(v => v * 1000)])
        if (outline) onOutline(outline); else onDrawError?.()
      } else if (drawingMode === 'box' && r[2] >= .02 && r[3] >= .02) onRegion?.(r)
      else if (r[2] < .02 && r[3] < .02) onPick?.(point(event))
      else onDrawError?.()
      path.current = []
    }} onPointerCancel={() => { anchor.current = null; path.current = []; setDrag(null); setTrace(null) }}>
    {url && <img key={url} src={url} alt="Video frame" draggable={false} onLoad={event => { setLoaded(url); setFailed(''); setAspect(event.currentTarget.naturalWidth / event.currentTarget.naturalHeight) }} onError={() => setFailed(url)} />}
    {(!url || loaded !== url) && <span className="mc-frame-status">{url && failed === url ? 'Frame unavailable. Try another time.' : 'Loading frame…'}</span>}
    {loaded === url && <><Box box={drag || region || box} />{outlines?.map((points, i) => <Outline key={i} points={points} faint />)}<Outline points={trace || outline} /></>}
  </div>
}

function CutPreview({ sourceUrl, targetUrl, aStart, aEnd, bStart, bEnd, onClose }: { sourceUrl: string; targetUrl: string; aStart: number; aEnd: number; bStart: number; bEnd: number; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const a = useRef<HTMLVideoElement>(null), b = useRef<HTMLVideoElement>(null)
  const phase = useRef<'a' | 'b' | 'stopped'>('stopped')
  const [visible, setVisible] = useState('a')
  const [error, setError] = useState('')
  const [ready, setReady] = useState(false)
  const [seeking, setSeeking] = useState(false)
  useEffect(() => {
    dialog.current?.showModal()
    let frame = 0
    const tick = () => {
      if (phase.current === 'a' && a.current && a.current.currentTime >= aEnd) {
        a.current.pause(); phase.current = 'b'; setVisible('b')
        b.current?.play().catch(() => { phase.current = 'stopped'; setError('Could not play the incoming clip.') })
      }
      if (phase.current === 'b' && b.current && b.current.currentTime >= bEnd) { b.current.pause(); phase.current = 'stopped' }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(frame); phase.current = 'stopped' }
  }, [aEnd, bEnd])
  function prepare() {
    if (a.current && b.current && a.current.readyState >= 2 && b.current.readyState >= 2) setReady(true)
  }
  async function play() {
    if (!a.current || !b.current) return
    if (seeking) return
    setSeeking(true)
    phase.current = 'stopped'; a.current.pause(); b.current.pause(); setError('')
    try {
      await Promise.all([[a.current, aStart], [b.current, bStart]].map(async ([element, seconds]) => {
        const video = element as HTMLVideoElement, at = seconds as number
        if (Math.abs(video.currentTime - at) < .005) return
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => { cleanup(); reject(new Error('Seeking timed out. Try again.')) }, 10000)
          const done = () => { cleanup(); resolve() }
          const cleanup = () => { clearTimeout(timer); video.removeEventListener('seeked', done) }
          video.addEventListener('seeked', done); video.currentTime = at
        })
      }))
      if (!a.current?.isConnected) return
      setVisible('a'); phase.current = 'a'; await a.current.play()
    } catch (err) { phase.current = 'stopped'; setError(errorMessage(err)) }
    finally { setSeeking(false) }
  }
  return <dialog ref={dialog} className="mc-preview" onCancel={onClose} onClose={onClose} aria-label="Match-cut preview"><div className="mc-frame-heading"><strong>A → B</strong><button className="lw-button" onClick={onClose}>Close</button></div><div className="mc-preview-stage"><video ref={a} src={sourceUrl} muted playsInline preload="auto" onLoadedData={prepare} onError={() => setError('Outgoing media could not be loaded.')} style={{ visibility: visible === 'a' ? 'visible' : 'hidden' }} /><video ref={b} src={targetUrl} muted playsInline preload="auto" onLoadedData={prepare} onError={() => setError('Incoming media could not be loaded.')} style={{ visibility: visible === 'b' ? 'visible' : 'hidden' }} /></div><div className="mc-pair-actions"><button className="lw-button lw-button-primary" disabled={!ready || seeking} onClick={play}>{ready ? 'Play / replay cut' : 'Loading clips…'}</button><small>Silent visual preview · verify exact cut frames in your editor.</small></div>{error && <p role="alert">{error}</p>}</dialog>
}
