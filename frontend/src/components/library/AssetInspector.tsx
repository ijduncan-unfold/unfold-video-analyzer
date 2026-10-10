import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { analyzeAsset, cancelAnalysis, deleteAsset, getShots, saveMetadata, saveShot } from '../../api/library'
import type { AssetDetail, AssetMetadata, Capabilities, LibraryShot, ReviewStatus, ShotAnnotation } from '../../api/library'
import type { Shot } from '../../api/types'
import { useSettingsStore } from '../../stores/settingsStore'
import { Icon } from './Icon'
import { bytes, duration, errorMessage, reviewLabel, splitTags, timestamp } from './format'
import { analysisFacts, analysisStageLabel, isActiveAnalysis } from './analysisProgress'
import { ExportPanel } from './ExportPanel'
import { SearchHighlight } from './SearchHighlight'
import './VideoWorkspace.css'
import { MatchCutPanel } from './MatchCutPanel'
import { forgetMatchCutSession } from './matchCutSession'
import { TranscriptPanel } from './TranscriptPanel'
import { RoughCutPanel } from './RoughCutPanel'

const ASSET_TABS = ['shots', 'match cuts', 'transcript', 'rough cut', 'analysis', 'metadata', 'export'] as const
type AssetTab = typeof ASSET_TABS[number]

interface Props {
  asset: AssetDetail
  initialSeconds?: number
  initialShot?: number
  capabilities: Capabilities | null
  onClose: () => void
  onRefresh: () => void
  onConfigure: () => void
  onRemoved?: (note: string | null) => void
}

export function AssetInspector({ asset, initialSeconds, initialShot, capabilities, onClose, onRefresh, onConfigure, onRemoved }: Props) {
  const [tab, setTab] = useState<AssetTab>('shots')
  const [playbackSeconds, setPlaybackSeconds] = useState(initialSeconds ?? 0)
  const [matchCutFromShot, setMatchCutFromShot] = useState(false)
  const [draft, setDraft] = useState<AssetMetadata>(() => ({ ...asset.metadata }))
  const [baseline, setBaseline] = useState(() => JSON.stringify(asset.metadata))
  const [tags, setTags] = useState(() => asset.metadata.tags.join(', '))
  const [collections, setCollections] = useState(() => asset.metadata.collections.join(', '))
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [exportScope, setExportScope] = useState<'asset' | 'selected_shots'>('asset')
  const [exportVisit, setExportVisit] = useState(0)
  const [selection, setSelection] = useState<{ run?: string; shots: Shot[] }>({ run: asset.analysis_config?.started_at, shots: [] })
  const [sectionFilter, setSectionFilter] = useState<number | null>(null)
  const [mode, setMode] = useState<'flash_only' | 'flash_pro'>(() => asset.analysis_config?.mode === 'flash_pro' ? 'flash_pro' : 'flash_only')
  const [fps, setFps] = useState(() => {
    const saved = asset.analysis_config?.fps
    return typeof saved === 'number' && Number.isFinite(saved) && saved >= 0.5 && saved <= 5 ? saved : 1
  })
  const [savedRun, setSavedRun] = useState(asset.analysis_config?.started_at)
  if (savedRun !== asset.analysis_config?.started_at) {
    setSavedRun(asset.analysis_config?.started_at)
    setSectionFilter(null)
    setMode(asset.analysis_config?.mode === 'flash_pro' ? 'flash_pro' : 'flash_only')
    const savedFps = asset.analysis_config?.fps
    setFps(typeof savedFps === 'number' && Number.isFinite(savedFps) && savedFps >= 0.5 && savedFps <= 5 ? savedFps : 1)
  }
  const [prompt, setPrompt] = useState('')
  const [starting, setStarting] = useState(false)
  const [confirmRemoval, setConfirmRemoval] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [previewedShot, setPreviewedShot] = useState(initialShot)
  const apiKey = useSettingsStore(state => state.apiKey)
  const video = useRef<HTMLVideoElement>(null)
  const body = useRef<HTMLDivElement>(null)
  const busy = isActiveAnalysis(asset.status) || starting
  const cutBusy = !!asset.rough_cut && ['queued', 'analyzing', 'assembling'].includes(asset.rough_cut.status)
  const removalDisabled = busy || cutBusy || removing || saving || asset.status === 'deleting'
  const warnings = [...new Set([...(asset.warnings || []), ...(asset.flash?.analysis_warnings || [])])]
  const metadata = { ...draft, tags: splitTags(tags), collections: splitTags(collections) }
  const dirty = JSON.stringify(metadata) !== baseline
  const shots = useMemo(() => asset.flash?.scenes.flatMap(scene => scene.shots.map(shot => ({ ...shot, scene_number: scene.scene_number, scene_title: scene.scene_title }))) || [], [asset.flash])
  const validSelection = shots.filter(shot => selection.shots.some(item => sameShot(item, shot)))
  const staleSelection = selection.shots.length > 0 && (selection.run !== asset.analysis_config?.started_at || validSelection.length !== selection.shots.length)
  function toggleShot(shot: Shot) {
    setSelection(previous => ({ run: asset.analysis_config?.started_at, shots: staleSelection ? [shot] : previous.shots.some(item => sameShot(item, shot)) ? previous.shots.filter(item => !sameShot(item, shot)) : [...previous.shots, shot] }))
  }
  function openExport(shot?: Shot) {
    video.current?.pause()
    if (shot) setSelection({ run: asset.analysis_config?.started_at, shots: [shot] })
    setExportScope(shot || selection.shots.length ? 'selected_shots' : 'asset')
    setExportVisit(value => value + 1)
    setTab('export')
    body.current?.scrollTo({ top: 0 })
  }
  const selectedIndex = Math.max(0, shots.findIndex(shot => shot.shot_number === previewedShot))
  const selectedShot = shots[selectedIndex]
  const progress = (starting && !isActiveAnalysis(asset.status)) || (isActiveAnalysis(asset.status) && asset.analysis_progress?.stage === 'complete') ? null : asset.analysis_progress
  const retainedShots = shots.length > 0 && (progress?.completed_shots === 0 || (busy && !progress))
  const stage = starting && !isActiveAnalysis(asset.status) ? 'starting' : progress?.stage || (asset.status === 'queued' ? 'queued' : 'indexing')
  const stageLabel = analysisStageLabel(stage, asset.analysis_config?.grouping)
  const usesSections = asset.analysis_config?.grouping === 'processing_sections'
  const progressFacts = analysisFacts(progress, retainedShots ? 0 : shots.length)
  function field<K extends keyof AssetMetadata>(name: K, value: AssetMetadata[K]) { setDraft(previous => ({ ...previous, [name]: value })) }
  async function save() {
    setSaving(true); setError(''); setNotice('')
    try {
      const result = await saveMetadata(asset.job_id, metadata)
      setDraft(result.metadata); setBaseline(JSON.stringify(result.metadata))
      setTags(result.metadata.tags.join(', ')); setCollections(result.metadata.collections.join(', '))
      setNotice('Saved.'); onRefresh()
    } catch (err) { setError(errorMessage(err)) } finally { setSaving(false) }
  }
  async function analyze() {
    if (busy || cutBusy || removing || asset.status === 'deleting') return
    if (!capabilities?.google_configured && !apiKey) { onConfigure(); return }
    setTab('shots'); body.current?.scrollTo({ top: 0 })
    setStarting(true); setError(''); setNotice('')
    try { await analyzeAsset(asset.job_id, mode, fps, prompt); onRefresh() }
    catch (err) { setError(errorMessage(err)) }
    finally { setStarting(false) }
  }
  async function cancel() {
    setError('')
    try { await cancelAnalysis(asset.job_id); onRefresh() } catch (err) { setError(errorMessage(err)) }
  }
  async function remove() {
    if (removalDisabled) return
    setRemoving(true); setError(''); setNotice('')
    const player = video.current
    if (player) { player.pause(); player.removeAttribute('src'); player.load() }
    try {
      const result = await deleteAsset(asset.job_id)
      forgetMatchCutSession(asset.job_id)
      onClose(); onRefresh(); onRemoved?.(result.note)
    } catch (err) {
      setError(errorMessage(err)); body.current?.scrollTo({ top: 0 })
      if (player?.isConnected && asset.preview_url) { player.src = asset.preview_url; player.load() }
    } finally { setRemoving(false) }
  }
  const seek = useCallback((seconds: number) => {
    if (video.current) { video.current.currentTime = seconds; video.current.play().catch(() => {}) }
  }, [])
  function playShot(shot: Shot) { setPreviewedShot(shot.shot_number); seek(timestamp(shot.start_time)) }
  const playDialogue = useCallback((seconds: number) => {
    const shot = shots.find(item => timestamp(item.start_time) <= seconds && timestamp(item.end_time) > seconds)
    if (shot) setPreviewedShot(shot.shot_number)
    setPlaybackSeconds(seconds)
    seek(seconds)
  }, [shots, seek])
  function chooseTab(next: AssetTab) {
    setMatchCutFromShot(false)
    if (next === 'export') { openExport(); return }
    if (next === 'match cuts' || next === 'rough cut') video.current?.pause()
    if (next === 'transcript') setPlaybackSeconds(video.current?.currentTime ?? 0)
    setTab(next)
    body.current?.scrollTo({ top: 0 })
  }
  return <main className="lw-inspector lw-video-workspace" aria-label="Video workspace">
    <header className="vw-header">
      <button className="lw-text-button vw-back" data-video-back aria-label="Back to library" onClick={onClose}><Icon name="back" />Library</button>
      <h1 title={asset.filename}>{asset.metadata.title || asset.filename}</h1>
      <div className="vw-header-actions">
        <button className="lw-button" onClick={() => openExport()}><Icon name="download" size={16} />Export{selection.shots.length > 0 ? ` (${selection.shots.length})` : ''}</button>
      <div className="vw-analyze-control" role="group" aria-label="Start video analysis">
        <label className="vw-analysis-type"><span className="lw-sr-only">Analysis type</span><select value={mode} disabled={busy || removing || asset.status === 'deleting'} onChange={event => setMode(event.target.value as 'flash_only' | 'flash_pro')}><option value="flash_only">Shots and tags</option><option value="flash_pro">Full analysis</option></select><Icon name="down" size={14} /></label>
      <button className="lw-button lw-button-primary lw-analyze-primary" disabled={busy || cutBusy || removing || asset.status === 'deleting'} onClick={analyze}>
        <Icon name="spark" size={20} />
        {busy ? starting && !isActiveAnalysis(asset.status) ? 'Starting…' : asset.status === 'queued' ? 'Queued…' : 'Analyzing…' : !capabilities?.google_configured && !apiKey ? 'Connect Gemini' : asset.status === 'error' ? 'Retry analysis' : asset.flash ? 'Analyze again' : 'Analyze video'}
      </button>
      </div>
      </div>
    </header>
    <div className={`vw-layout ${(tab === 'export' || tab === 'match cuts' || tab === 'rough cut') ? 'is-export' : ''}`}>
      <div className="vw-viewer" hidden={tab === 'export' || tab === 'match cuts' || tab === 'rough cut'}>
        <div className="lw-preview">
          {asset.preview_url ? <video ref={video} src={asset.preview_url} poster={asset.thumbnail_url || undefined} controls preload="metadata" onLoadedMetadata={() => { if (video.current && initialSeconds != null) video.current.currentTime = initialSeconds }} onTimeUpdate={event => { if (tab === 'transcript') setPlaybackSeconds(event.currentTarget.currentTime) }} onSeeked={event => { if (tab === 'transcript') setPlaybackSeconds(event.currentTarget.currentTime) }} /> : <div className="lw-no-preview"><Icon name="film" size={32} /><span>{asset.youtube_url ? 'Source video on YouTube' : 'Preview unavailable for this format'}</span>{asset.youtube_url && <a href={asset.youtube_url} target="_blank" rel="noreferrer">Open source ↗</a>}</div>}
        </div>
        {selectedShot && tab !== 'transcript' && <div className="vw-shot-context">
          <div className="vw-shot-transport"><strong>Shot {String(selectedShot.shot_number).padStart(2, '0')}</strong><time>{selectedShot.start_time} – {selectedShot.end_time}</time><div><button className="lw-icon-button" aria-label="Previous shot" disabled={selectedIndex === 0 || !asset.preview_url} onClick={() => playShot(shots[selectedIndex - 1])}><Icon name="chevron" style={{ transform: 'rotate(180deg)' }} /></button><button className="lw-icon-button" aria-label="Next shot" disabled={selectedIndex >= shots.length - 1 || !asset.preview_url} onClick={() => playShot(shots[selectedIndex + 1])}><Icon name="chevron" /></button></div></div>
          <p>{selectedShot.visual_description}</p><button className="lw-text-button" onClick={() => { video.current?.pause(); setMatchCutFromShot(true); setTab('match cuts') }}>Explore match cuts</button>
        </div>}
        {busy && <div className="vw-progress"><div className="lw-analysis-progress" role="status" aria-live="polite" aria-atomic="true"><div className="lw-progress-orbit" /><div><strong>{stageLabel}</strong><p>{progressFacts.summary}</p>{progressFacts.coverage && <small>{progressFacts.coverage}</small>}</div></div><button className="lw-text-button" onClick={cancel} disabled={starting}>Cancel analysis</button></div>}
        {asset.error && <div className="lw-inline-error" role="alert">{asset.error}</div>}
      </div>
      <div className="vw-data">
    <div className="lw-inspector-tabs" role="tablist" aria-label="Asset information">{ASSET_TABS.map(value => <button id={`asset-tab-${value.replaceAll(' ', '-')}`} aria-controls={`asset-panel-${value.replaceAll(' ', '-')}`} role="tab" tabIndex={tab === value ? 0 : -1} onKeyDown={event => { const current = ASSET_TABS.indexOf(tab); const next = event.key === 'ArrowRight' ? (current + 1) % ASSET_TABS.length : event.key === 'ArrowLeft' ? (current + ASSET_TABS.length - 1) % ASSET_TABS.length : event.key === 'Home' ? 0 : event.key === 'End' ? ASSET_TABS.length - 1 : -1; if (next >= 0) { event.preventDefault(); chooseTab(ASSET_TABS[next]); document.getElementById(`asset-tab-${ASSET_TABS[next].replaceAll(' ', '-')}`)?.focus() } }} aria-selected={tab === value} onClick={() => chooseTab(value)} key={value}>{value[0].toUpperCase() + value.slice(1)}</button>)}</div>
    <div ref={body} className="lw-inspector-body" role="tabpanel" id={`asset-panel-${tab.replaceAll(' ', '-')}`}  aria-labelledby={`asset-tab-${tab.replaceAll(' ', '-')}`} >
      {error && <div className="lw-inline-error" role="alert">{error}</div>}{notice && <div className="lw-inline-success" role="status">{notice}</div>}
      {tab === 'analysis' && warnings.length > 0 && <details className="lw-details"><summary>Analysis notes<Icon name="down" size={14} /></summary><div className="lw-analysis-warning">{warnings.map(warning => <p key={warning}>{warning}</p>)}</div></details>}
      {tab === 'metadata' && <>
        {asset.summary && <details className="lw-details"><summary>AI summary<Icon name="down" size={14} /></summary><p className="lw-panel-description">{asset.summary}</p></details>}
        <label className="lw-field">Title<input value={draft.title} onChange={event => field('title', event.target.value)} placeholder={asset.filename} /></label>
        <label className="lw-field">Tags<textarea rows={2} value={tags} onChange={event => setTags(event.target.value)} placeholder="Separate tags with commas" /></label>
        <label className="lw-field">Project label<input value={draft.project} onChange={event => field('project', event.target.value)} /></label>
        <div className="lw-field-pair"><label className="lw-field">Review<select value={draft.review_status} onChange={event => field('review_status', event.target.value as ReviewStatus)}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="needs_changes">Needs changes</option></select></label><label className="lw-field">Usage rights<select title="Set by your team; AI does not verify usage rights." value={draft.rights_status} onChange={event => field('rights_status', event.target.value as AssetMetadata['rights_status'])}><option value="unknown">Unverified</option><option value="cleared">Cleared</option><option value="restricted">Restricted</option></select></label></div>
        <details className="lw-details"><summary>More metadata<Icon name="down" size={14} /></summary><div style={{ paddingTop: 14 }}>
          <label className="lw-field">Client<input value={draft.client} onChange={event => field('client', event.target.value)} /></label>
          <label className="lw-field">Campaign<input value={draft.campaign} onChange={event => field('campaign', event.target.value)} /></label>
          <label className="lw-field">Collections<input value={collections} onChange={event => setCollections(event.target.value)} placeholder="Separate collections with commas" /></label>
          <label className="lw-field">Notes<textarea rows={3} value={draft.notes} onChange={event => field('notes', event.target.value)} /></label>
        </div></details>
        <details className="lw-details"><summary>File details<Icon name="down" size={14} /></summary><dl><div><dt>Filename</dt><dd>{asset.filename}</dd></div><div><dt>Duration</dt><dd>{duration(asset.technical.duration_seconds)}</dd></div><div><dt>Size</dt><dd>{bytes(asset.size_bytes)}</dd></div>{asset.technical.width != null && <div><dt>Resolution</dt><dd>{asset.technical.width} × {asset.technical.height}</dd></div>}{asset.technical.frame_rate != null && <div><dt>Frame rate</dt><dd>{Number(asset.technical.frame_rate).toFixed(2).replace(/\.00$/, '')} fps</dd></div>}<div><dt>Format</dt><dd>{asset.mime_type || 'Unknown'}</dd></div><div><dt>Codec</dt><dd>{asset.technical.codec || 'Unknown'}</dd></div><div><dt>Source timecode</dt><dd>{asset.technical.source_timecode || 'Not embedded'}</dd></div><div><dt>Audio</dt><dd>{asset.technical.has_audio == null ? 'Unknown' : asset.technical.has_audio ? 'Present' : 'No audio track'}</dd></div><div><dt>Imported</dt><dd>{asset.created_at ? new Date(asset.created_at).toLocaleDateString() : 'Unknown'}</dd></div></dl><div className="lw-remove-section">{confirmRemoval ? <div className="lw-remove-confirm" role="group" aria-label="Confirm asset removal"><strong>Remove this asset?</strong><p>Removes this project and its analysis from the app. Deletes only copies and cache files created by the app. Your original source files are never deleted.</p><div><button className="lw-button" disabled={removing} onClick={() => setConfirmRemoval(false)}>Cancel</button><button className="lw-button lw-button-danger" disabled={removalDisabled} onClick={remove}>{removing ? 'Removing…' : 'Remove asset'}</button></div></div> : <button className="lw-text-button lw-remove-link" disabled={removalDisabled} onClick={() => setConfirmRemoval(true)}>Remove asset</button>}{busy && <small>Finish or cancel analysis before removing.</small>}</div></details>
      </>}
      <div hidden={tab !== 'shots'}>
        {retainedShots && <p className="lw-live-result-note">{busy ? 'Previous analysis is shown until this run returns shots.' : 'Previous analysis retained.'}</p>}
        {!retainedShots && asset.status === 'error' && shots.length > 0 && <p className="lw-live-result-note">Saved partial shots</p>}
        <ShotBrowser asset={asset} shots={shots} active={tab === 'shots'} busy={busy} selectedShot={selectedShot} selection={staleSelection ? [] : validSelection} staleSelection={staleSelection} sectionFilter={sectionFilter} onSectionFilter={setSectionFilter} onToggle={toggleShot} onClearSelection={() => setSelection({ run: asset.analysis_config?.started_at, shots: [] })} onExport={openExport} onSeek={playShot} onRefresh={onRefresh} />
      </div>
      {tab === 'match cuts' && <MatchCutPanel key={asset.analysis_config?.started_at || 'unindexed'} asset={asset} shots={shots} initialShot={selectedShot} useInitialShot={matchCutFromShot} />}
      {tab === 'export' && <ExportPanel key={exportVisit} asset={asset} shots={shots} selectedShots={validSelection} staleSelection={staleSelection} onChooseShots={() => setTab('shots')} onClearSelection={() => setSelection({ run: asset.analysis_config?.started_at, shots: [] })} initialScope={exportScope} />}
      <div hidden={tab !== 'transcript'}><TranscriptPanel asset={asset} active={tab === 'transcript'} currentTime={playbackSeconds} onSeek={playDialogue} /></div>
      <div hidden={tab !== 'rough cut'}><RoughCutPanel key={asset.job_id} asset={asset} active={tab === 'rough cut'} configured={!!capabilities?.google_configured || !!apiKey} onConfigure={onConfigure} onRefresh={onRefresh} /></div>
      {tab === 'analysis' && <>
        {asset.video_summary && <details className="lw-details" open><summary>Summary<Icon name="down" size={14} /></summary><AnalysisFields value={asset.video_summary} /></details>}
        {!!asset.flash?.scenes.length && <details className="lw-details" open><summary>{usesSections ? 'Sections' : 'Scenes'}<Icon name="down" size={14} /></summary>{asset.flash.scenes.map(scene => <details className="lw-details" key={scene.scene_number}><summary>{usesSections ? 'Section' : 'Scene'} {scene.scene_number} · {scene.scene_title}<Icon name="down" size={14} /></summary><p className="lw-panel-description">{scene.scene_description}</p><time>{scene.start_time} – {scene.end_time}</time><AnalysisFields value={asset.deep?.find(item => item.scene_number === scene.scene_number)} /><button className="lw-button" onClick={() => { setSectionFilter(scene.scene_number); setTab('shots') }}>View {scene.shots.length} shots</button></details>)}</details>}
        {asset.custom_result && <details className="lw-details"><summary>Custom analysis<Icon name="down" size={14} /></summary><AnalysisFields value={asset.custom_result} /></details>}
        <details className="lw-details vw-analysis-settings" open={!asset.flash}><summary>Analysis settings<Icon name="down" size={14} /></summary><div className="vw-settings-content">
        <details className="lw-details"><summary>Options<Icon name="down" size={14} /></summary><div style={{ paddingTop: 14 }}>
          <label className="lw-field">Sampling<select value={fps} onChange={event => setFps(Number(event.target.value))}>{![0.5, 1, 2, 3, 4, 5].includes(fps) && <option value={fps}>{fps} fps</option>}{[0.5, 1, 2, 3, 4, 5].map(value => <option value={value} key={value}>{value} fps{value === 4 ? ' · fast action' : ''}</option>)}</select><span className="lw-field-hint">Higher sampling uses more tokens. Short cuts may be missed.</span></label>
          <label className="lw-field">Instructions<textarea rows={3} value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="Optional analysis instructions" /></label>
          <p className="lw-model-note">{capabilities?.models ? `${capabilities.models.analysis}${mode === 'flash_pro' ? ` + ${capabilities.models.deep}` : ''}` : 'Gemini'}</p>
        </div></details>
        <p className="lw-model-note">Gemini API charges apply.</p>
        {asset.analysis_config?.analysis_model && <details className="lw-details"><summary>Run details<Icon name="down" size={14} /></summary><dl><div><dt>Index model</dt><dd>{asset.analysis_config.analysis_model}</dd></div>{asset.analysis_config.mode === 'flash_pro' && <div><dt>Creative model</dt><dd>{asset.analysis_config.deep_model}</dd></div>}<div><dt>Sampling</dt><dd>{asset.analysis_config.fps} frame(s) per second</dd></div><div><dt>Schema</dt><dd>{asset.flash?.schema_version || 'Legacy'}</dd></div><div><dt>Started</dt><dd>{asset.analysis_config.started_at ? new Date(asset.analysis_config.started_at).toLocaleString() : 'Not recorded'}</dd></div><div><dt>Timestamp precision</dt><dd>Approximate · verify against source before editing</dd></div>{asset.cost_estimate && <div><dt>Estimated API cost</dt><dd>{asset.cost_estimate.estimated_cost_usd == null ? 'Unavailable for this model or usage record' : `$${asset.cost_estimate.estimated_cost_usd.toFixed(4)} USD`}</dd></div>}</dl>{asset.cost_estimate?.warnings?.map(warning => <p className="lw-model-note" key={warning}>{warning}</p>)}</details>}
        </div></details>
      </>}
    </div>
    {tab === 'metadata' && <div className="lw-inspector-footer"><button className="lw-button lw-button-primary" disabled={!dirty || saving || removing} onClick={save}>{saving ? 'Saving…' : dirty ? 'Save metadata' : 'Saved'}<Icon name="check" size={15} /></button></div>}
      </div>
    </div>
  </main>
}

function sameShot(a: Shot, b: Shot) {
  return a.shot_number === b.shot_number && a.start_time === b.start_time && a.end_time === b.end_time
}

function AnalysisFields({ value, query = '' }: { value: unknown; query?: string }) {
  if (value == null || value === '') return null
  if (Array.isArray(value)) return <>{value.map((item, index) => <div key={index}><AnalysisFields value={item} query={query} /></div>)}</>
  if (typeof value !== 'object') return <span><SearchHighlight text={String(value)} query={query} /></span>
  return <dl className="vw-analysis-fields">{Object.entries(value).filter(([key, item]) => !['scene_number', 'total_runtime', 'total_scenes', 'total_shots'].includes(key) && item != null && item !== '').map(([key, item]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><AnalysisFields value={item} query={query} /></dd></div>)}</dl>
}

const SHOTS_PER_PAGE = 48
type WorkspaceShot = Shot & { scene_title: string; scene_number: number }
function ShotBrowser({ asset, shots, active, busy, selectedShot, selection, staleSelection, sectionFilter, onSectionFilter, onToggle, onClearSelection, onExport, onSeek, onRefresh }: {
  asset: AssetDetail
  shots: WorkspaceShot[]
  active: boolean
  busy: boolean
  selectedShot?: Shot
  selection: Shot[]
  staleSelection: boolean
  sectionFilter: number | null
  onSectionFilter: (number: number | null) => void
  onToggle: (shot: Shot) => void
  onClearSelection: () => void
  onExport: (shot?: Shot) => void
  onSeek: (shot: Shot) => void
  onRefresh: () => void
}) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [searchRun, setSearchRun] = useState(asset.analysis_config?.started_at)
  const [previousSection, setPreviousSection] = useState(sectionFilter)
  const [result, setResult] = useState<{ key: string; shots: LibraryShot[]; total: number; error?: string } | null>(null)
  if (previousSection !== sectionFilter) {
    setPreviousSection(sectionFilter); setQuery(''); setPage(0)
  }
  if (searchRun !== asset.analysis_config?.started_at) {
    setSearchRun(asset.analysis_config?.started_at); setPage(0)
  }
  const search = query.trim()
  const requestKey = JSON.stringify([asset.job_id, asset.analysis_config?.started_at, search, page])
  useEffect(() => {
    if (!search || !active) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      getShots({ job_id: asset.job_id, q: search, limit: SHOTS_PER_PAGE, offset: page * SHOTS_PER_PAGE }, controller.signal).then(data => {
        if (controller.signal.aborted) return
        setResult({ ...data, key: requestKey })
        const lastPage = Math.max(0, Math.ceil(data.total / SHOTS_PER_PAGE) - 1)
        if (page > lastPage) setPage(lastPage)
      }).catch(err => {
        if (!controller.signal.aborted) setResult({ key: requestKey, shots: [], total: 0, error: errorMessage(err) })
      })
    }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [asset, active, search, page, requestKey])
  const currentResult = result?.key === requestKey ? result : null
  const localShots = sectionFilter == null ? shots : shots.filter(shot => shot.scene_number === sectionFilter)
  const total = search ? currentResult?.total || 0 : localShots.length
  const pageCount = Math.max(1, Math.ceil(total / SHOTS_PER_PAGE))
  const visiblePage = Math.min(page, pageCount - 1)
  if (!search && page !== visiblePage) setPage(visiblePage)
  const shown: (WorkspaceShot | LibraryShot)[] = search ? currentResult?.shots || [] : localShots.slice(visiblePage * SHOTS_PER_PAGE, (visiblePage + 1) * SHOTS_PER_PAGE)
  const loading = !!search && !currentResult
  const currentSection = asset.flash?.scenes.find(scene => scene.scene_number === sectionFilter)
  return <>
    <div className="vw-shot-tools">
      <label className="vw-shot-search"><Icon name="search" size={18} /><span className="lw-sr-only">Search this video</span><input type="search" aria-label="Search this video" value={query} placeholder="Search shots, colors, lighting, section notes…" onChange={event => { setQuery(event.target.value); setPage(0); setPreviousSection(null); onSectionFilter(null) }} />{query && <button className="lw-icon-button" aria-label="Clear video search" onClick={() => { setQuery(''); setPage(0) }}><Icon name="close" size={14} /></button>}</label>
      {sectionFilter != null && <div className="vw-scope-note"><span>{currentSection?.scene_title || `Section ${sectionFilter}`}</span><button className="lw-text-button" onClick={() => onSectionFilter(null)}>Show all shots</button></div>}
      <div className="vw-shot-tools-row"><span role="status">{loading ? 'Searching…' : `${total} ${search ? 'matching ' : ''}shot${total === 1 ? '' : 's'}`}</span>{selection.length > 0 && <><button className="lw-text-button" onClick={() => onExport()}>Export {selection.length} selected</button><button className="lw-text-button" onClick={onClearSelection}>Clear selection</button></>}</div>
      {staleSelection && <div className="lw-inline-error">The analysis changed. Select shots again before exporting. <button className="lw-text-button" onClick={onClearSelection}>Clear selection</button></div>}
    </div>
    {currentResult?.error && <div className="lw-inline-error" role="alert">{currentResult.error}</div>}
    {shown.length > 0 && <div className="vw-shot-grid">{shown.map(shot => <ShotEditor key={`${shot.shot_number}-${shot.start_time}-${shot.end_time}`} jobId={asset.job_id} shot={shots.find(item => sameShot(item, shot)) || shot} annotation={asset.shot_annotations?.[String(shot.shot_number)]} selected={!!selectedShot && sameShot(shot, selectedShot)} exportSelected={selection.some(item => sameShot(item, shot))} query={search} match={'match_context' in shot ? shot as LibraryShot : undefined} canPreview={!!asset.preview_url} onToggle={() => onToggle(shot)} onExport={() => onExport(shot)} onSeek={() => onSeek(shot)} onRefresh={onRefresh} />)}</div>}
    {!loading && !shown.length && !currentResult?.error && <div className="lw-panel-empty"><Icon name="film" size={30} /><p>{search ? 'No shots match this search.' : busy ? 'Shots appear as each section finishes.' : 'Analyze this video to find shots.'}</p></div>}
    {pageCount > 1 && <nav className="vw-shot-pagination" aria-label="Shot pages"><button className="lw-button" disabled={visiblePage === 0 || loading} onClick={() => setPage(visiblePage - 1)}>Previous</button><span>{visiblePage + 1} / {pageCount}</span><button className="lw-button" disabled={visiblePage >= pageCount - 1 || loading} onClick={() => setPage(visiblePage + 1)}>Next</button></nav>}
  </>
}

function ShotEditor({ jobId, shot, annotation, selected, exportSelected, match, query, canPreview, onToggle, onExport, onSeek, onRefresh }: {
  jobId: string
  shot: Shot & { scene_title: string }
  annotation?: ShotAnnotation
  selected: boolean
  exportSelected: boolean
  match?: LibraryShot
  query: string
  canPreview: boolean
  onToggle: () => void
  onExport: () => void
  onSeek: () => void
  onRefresh: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [tags, setTags] = useState(annotation?.tags.join(', ') || '')
  const [notes, setNotes] = useState(annotation?.notes || '')
  const [review, setReview] = useState<ReviewStatus>(annotation?.review_status || 'unreviewed')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [copyMessage, setCopyMessage] = useState('')
  async function copyTimes() {
    try { await navigator.clipboard.writeText(`${shot.start_time} – ${shot.end_time}`); setCopyMessage('Copied times.') }
    catch { setCopyMessage('Could not copy. Select the displayed times to copy them.') }
  }
  async function save() {
    setSaving(true); setMessage(''); setError('')
    try { await saveShot(jobId, shot.shot_number, { tags: splitTags(tags), notes, review_status: review }, { start_time: shot.start_time, end_time: shot.end_time }); setMessage('Saved.'); onRefresh() }
    catch (err) { setError(errorMessage(err)) } finally { setSaving(false) }
  }
  return <div className={`lw-shot-detail ${selected ? 'is-selected' : ''}`}>
    <button className="vw-shot-thumbnail" disabled={!canPreview} onClick={onSeek} aria-label={`Play shot ${shot.shot_number} at ${shot.start_time}`} aria-pressed={selected}>{shot.thumbnail_url ? <img src={shot.thumbnail_url} alt="" loading="lazy" /> : <Icon name="film" size={30} />}<span className="vw-thumbnail-play"><Icon name="play" size={22} /></span></button>
    <div className="vw-shot-caption"><label className="vw-shot-select"><input type="checkbox" checked={exportSelected} onChange={onToggle} aria-label={`Select shot ${shot.shot_number} for export`} /><strong>Shot {String(shot.shot_number).padStart(2, '0')}</strong></label><span className={`lw-review-dot ${review}`} title={reviewLabel(review)} /><time>{shot.start_time} — {shot.end_time}</time></div>
    <p><SearchHighlight text={shot.visual_description} query={query} /></p><div className="lw-tags">{[shot.shot_type, shot.camera_movement, shot.mood].filter(Boolean).map((value, i) => <span key={`${value}-${i}`}><SearchHighlight text={value} query={query} /></span>)}</div>
    {match?.match_context && <details className="lw-details vw-match-context" open><summary>{match.match_sources?.join(' · ') || 'Search match'}<Icon name="down" size={13} /></summary><p><SearchHighlight text={match.match_context} query={query} /></p>{match.match_sources?.includes('Section analysis') && <details className="lw-details"><summary>Section context<Icon name="down" size={13} /></summary><p><SearchHighlight text={match.section_context?.scene_description || ''} query={query} /></p><AnalysisFields value={match.section_analysis} query={query} /></details>}</details>}
    {match?.match_sources?.includes('Custom analysis') && <details className="lw-details"><summary>Custom analysis · whole video<Icon name="down" size={13} /></summary><AnalysisFields value={match.custom_analysis} query={query} /></details>}
    {match?.match_sources?.includes('Transcript') && <details className="lw-details"><summary>Transcript in this shot<Icon name="down" size={13} /></summary><AnalysisFields value={match.transcript_segments} query={query} /></details>}
    <div className="vw-shot-actions"><button className="lw-text-button" onClick={onExport}>Export shot</button><button className="lw-text-button" onClick={copyTimes}>Copy times</button></div>{copyMessage && <small className="vw-copy-message" role="status">{copyMessage}</small>}
    {!!shot.dominant_colors?.length && <details className="lw-details"><summary>Colors<Icon name="down" size={13} /></summary><p><SearchHighlight text={shot.dominant_colors.join(' · ')} query={query} /></p></details>}
    {!!shot.tags?.length && <details className="lw-details"><summary>AI tags<Icon name="down" size={13} /></summary><div className="lw-tags">{shot.tags.map(tag => <span key={tag}><SearchHighlight text={tag} query={query} /></span>)}</div></details>}
    {(shot.evidence?.length || shot.visible_text?.length || shot.logos?.length || shot.subjects?.length || shot.actions?.length || shot.location || shot.transcript || shot.audio_notes) ? <details className="lw-details lw-shot-evidence"><summary>Details<Icon name="down" size={13} /></summary><dl>{shot.subjects?.length > 0 && <div><dt>Subjects</dt><dd><SearchHighlight text={shot.subjects.join(', ')} query={query} /></dd></div>}{!!shot.actions?.length && <div><dt>Actions</dt><dd><SearchHighlight text={shot.actions.join(', ')} query={query} /></dd></div>}{!!shot.visible_text?.length && <div><dt>Visible text</dt><dd><SearchHighlight text={shot.visible_text.join(' · ')} query={query} /></dd></div>}{!!shot.logos?.length && <div><dt>Potential logos</dt><dd><SearchHighlight text={shot.logos.join(', ')} query={query} /></dd></div>}{shot.location && <div><dt>Location description</dt><dd><SearchHighlight text={shot.location} query={query} /></dd></div>}{shot.transcript && <div><dt>Model transcript</dt><dd><SearchHighlight text={shot.transcript} query={query} /></dd></div>}{shot.audio_notes && <div><dt>Audio observations</dt><dd><SearchHighlight text={shot.audio_notes} query={query} /></dd></div>}{shot.evidence?.map((evidence, index) => <div key={index}><dt>{evidence.modality} · {evidence.start_time}–{evidence.end_time}</dt><dd><SearchHighlight text={evidence.description} query={query} /></dd></div>)}</dl><p className="lw-model-note">Model observations are unverified. Shot timestamps are approximate.{shot.confidence != null ? ' Model confidence is uncalibrated and is not a reliability score.' : ''}</p></details> : null}
    {!!shot.analysis_warnings?.length && <details className="lw-details"><summary>Notes<Icon name="down" size={13} /></summary><div className="lw-analysis-warning">{shot.analysis_warnings.map(warning => <p key={warning}><SearchHighlight text={warning} query={query} /></p>)}</div></details>}
    <button className="lw-text-button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? 'Close edit' : 'Edit'}<Icon name="down" size={13} /></button>
    {expanded && <div className="lw-shot-fields"><label className="lw-field">Your tags<input value={tags} onChange={event => setTags(event.target.value)} placeholder="Comma-separated tags" /></label><label className="lw-field">Notes<textarea rows={2} value={notes} onChange={event => setNotes(event.target.value)} /></label><label className="lw-field">Review<select value={review} onChange={event => setReview(event.target.value as ReviewStatus)}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="needs_changes">Needs changes</option></select></label><button className="lw-button" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save shot'}</button>{message && <small className="lw-inline-success" role="status">{message}</small>}{error && <small className="lw-inline-error" role="alert">{error}</small>}</div>}
  </div>
}
