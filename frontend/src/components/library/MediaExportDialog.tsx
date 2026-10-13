import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { downloadMediaExport, getMediaExportOptions, getPremiereSelections, getProjects, savePremiereSelection } from '../../api/library'
import type { MediaExportOptions, MediaExportFormat, PremiereSelection } from '../../api/library'
import { errorMessage } from './format'
import { Icon } from './Icon'
import './MediaExportDialog.css'

type Media = { id: string; title: string }
type Destination = MediaExportFormat | 'premiere_selection'

export function MediaExportDialog({ selectedMedia, initialName, matchCount, onCollectMatches, onClose }: {
  selectedMedia: Media[]
  initialName: string
  matchCount: number
  onCollectMatches: () => Promise<Media[] | undefined>
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const lock = useRef(false)
  const downloadController = useRef<AbortController | null>(null)
  const [checkedOptions, setOptions] = useState<MediaExportOptions | null>(null)
  const [retry, setRetry] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState(initialName.slice(0, 160))
  const [layout, setLayout] = useState<'timeline' | 'bin'>('bin')
  const [format, setFormat] = useState<Destination>('premiere_xml')
  const [scope, setScope] = useState<'selected' | 'matches' | 'saved'>(selectedMedia.length ? 'selected' : 'matches')
  const [matches, setMatches] = useState<Media[] | null>(null)
  const [matchError, setMatchError] = useState('')
  const [loaded, setLoaded] = useState<{ name: string; media: Media[] } | null>(null)
  const [selections, setSelections] = useState<PremiereSelection[]>([])
  const [savedKey, setSavedKey] = useState('')
  const media = scope === 'selected' ? selectedMedia : scope === 'saved' ? loaded?.media || [] : matches || []
  const collecting = scope === 'matches' && matches === null && !matchError
  const nativePremiere = format === 'premiere_selection'
  const idsKey = JSON.stringify(media.map(item => item.id))
  const selectionKey = idsKey + title.trim()
  const options = checkedOptions?.format === format && checkedOptions?.layout === layout && JSON.stringify(checkedOptions.items.map(item => item.id)) === idsKey ? checkedOptions : null
  const extension = nativePremiere ? '' : { premiere_xml: 'xml', fcpxml: 'fcpxml', resolve: 'py', edl: 'edl' }[format]
  const [includeMetadata, setIncludeMetadata] = useState(true)
  const details = new Map(options?.items.map(item => [item.id, item]))
  const collectMatches = useEffectEvent(onCollectMatches)

  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    return () => { downloadController.current?.abort(); element.close() }
  }, [])
  useEffect(() => {
    if (scope !== 'matches' || matches !== null) return
    let active = true
    collectMatches().then(result => {
      if (!active) return
      if (result) setMatches(result)
      else setMatchError('Could not collect this search. Close Export and try again.')
    }).catch(cause => { if (active) setMatchError(errorMessage(cause)) })
    return () => { active = false }
  }, [scope, matches])
  useEffect(() => {
    if (!nativePremiere) return
    let active = true
    getPremiereSelections().then(result => {
      if (active) setSelections(previous => [...new Map([...result.selections, ...previous].map(item => [item.id, item])).values()])
    }).catch(cause => { if (active) setError(errorMessage(cause)) })
    return () => { active = false }
  }, [nativePremiere])
  useEffect(() => {
    if (format === 'premiere_selection' || !JSON.parse(idsKey).length || JSON.parse(idsKey).length > 1000) return
    const controller = new AbortController()
    getMediaExportOptions(JSON.parse(idsKey), format, layout, controller.signal).then(result => {
      if (!controller.signal.aborted) setOptions(result)
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [idsKey, retry, format, layout])

  async function loadSelection(selection: PremiereSelection) {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const result = await getProjects()
      const names = new Map(result.projects.map(item => [item.id, item.title || item.filename]))
      setLoaded({ name: selection.name, media: selection.job_ids.map(id => ({ id, title: names.get(id) || 'Unavailable recording' })) })
      setTitle(selection.name); setScope('saved'); setSavedKey(JSON.stringify(selection.job_ids) + selection.name)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { lock.current = false; setBusy(false) }
  }
  async function saveSelection() {
    if (lock.current || !media.length || media.length > 1000 || !title.trim() || savedKey === selectionKey) return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const selection = await savePremiereSelection(title.trim(), media.map(item => item.id))
      setSavedKey(selectionKey); setSelections(previous => [selection, ...previous])
      setNotice(`“${selection.name}” saved for Premiere · ${selection.count} ${selection.count === 1 ? 'recording' : 'recordings'}.`)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { lock.current = false; setBusy(false) }
  }
  async function download() {
    if (lock.current || !options?.available || format === 'premiere_selection') return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    const controller = new AbortController()
    downloadController.current = controller
    try {
      const response = await downloadMediaExport(JSON.parse(idsKey), title.trim(), layout, includeMetadata,
        Object.fromEntries(options.items.map(item => [item.id, item.analysis_version])), format, controller.signal)
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        if (response.status === 409) setOptions(null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : `Export failed (${response.status}).`)
      }
      const blob = await response.blob()
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = includeMetadata ? 'Video Analyzer media export.zip' : format === 'resolve' ? 'Import into Resolve.py' : `Video Analyzer.${extension}`
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice(`Export download started for ${media.length} media ${media.length === 1 ? 'item' : 'items'}.`)
    } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)) }
    finally { lock.current = false; setBusy(false); downloadController.current = null }
  }

  return <dialog ref={dialog} className="lw-settings-dialog me-dialog" aria-labelledby="media-export-title" onCancel={event => { if (lock.current) event.preventDefault(); else onClose() }}>
    <header className="lw-dialog-heading"><h2 id="media-export-title">Export</h2><button className="lw-icon-button" aria-label="Close export" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <p>Send original recordings and their saved metadata to your editor.</p>
    <fieldset disabled={busy}>
      <label className="me-field">Include<select value={scope} disabled={collecting} onChange={event => { setScope(event.target.value as typeof scope); setMatchError(''); setError(''); setNotice('') }}>
        <option value="selected" disabled={!selectedMedia.length}>Selected recordings ({selectedMedia.length})</option>
        <option value="matches">All search matches ({matchCount})</option>
        {loaded && <option value="saved">Saved selection: {loaded.name} ({loaded.media.length})</option>}
      </select></label>
      <label className="me-field">Export format<select value={format} onChange={event => { const next = event.target.value as Destination; setFormat(next); setError(''); setNotice(''); if (next === 'edl') setLayout('timeline'); if (next === 'resolve') setLayout('bin') }}>
        <option value="premiere_xml">Final Cut Pro XML — Legacy (.xml) · Premiere / Resolve</option>
        <option value="fcpxml">Final Cut Pro XML — Modern (.fcpxml) · Final Cut Pro</option>
        <option value="premiere_selection">Premiere panel · saved selection (requires panel)</option>
        <option value="resolve">DaVinci Resolve native media import (.py)</option>
        <option value="edl">CMX3600 EDL (.edl) · video only</option>
      </select></label>
      <label className="me-field">{nativePremiere ? 'Selection name' : 'Project / timeline name'}<input value={title} maxLength={160} onChange={event => setTitle(event.target.value)} /></label>
      {(format === 'fcpxml' || format === 'premiere_xml') && <label className="me-field">Create<select value={layout} onChange={event => { setLayout(event.target.value as 'timeline' | 'bin'); setNotice('') }}><option value="bin">{format === 'fcpxml' ? 'Media event only · Final Cut Pro' : 'Media bin · whole original clips'}</option><option value="timeline">{format === 'fcpxml' ? 'Timeline + media event · Final Cut / Resolve' : 'Timeline + media bin · Premiere / Resolve'}</option></select></label>}
      <p className="me-hint">{nativePremiere ? 'Requires the Video Analyzer panel installed in Premiere (0.1.21+) and connected to the companion. Saving here does not import clips. In the panel, choose Saved analyzer selections → Import clips + metadata. For a downloadable file, choose Legacy (.xml).' : format === 'resolve' ? 'Original video/audio and searchable metadata in a Resolve bin, without XML timing or pixel-aspect checks. Run the included script in Resolve’s Python 3 console; the package includes instructions.' : format === 'edl' ? 'Video-only conform list; requires matching frame rates and known non-drop timecode. Import originals separately and map reel IDs. Choose the package for reel mapping and full metadata.' : format === 'premiere_xml' && layout === 'bin' ? 'Download the legacy XML, then in Premiere choose File → Import and select the .xml file. It brings whole original clips into a bin with searchable clip Description and Log Note metadata. Search All metadata in the Project panel; these fields appear under Clip in the Metadata panel. No panel is required.' : format === 'fcpxml' && layout === 'bin' ? 'Modern FCPXML is for Final Cut Pro; Premiere cannot import .fcpxml directly. For Premiere, choose Legacy (.xml). Whole originals include descriptions and keywords.' : 'Whole recordings in selection order. Timeline exports require reliable source timing. FCPXML carries known non-square pixel aspect ratios directly; the legacy XML route retains its stricter mapping checks.'}</p>
      {!nativePremiere && <label className="me-checkbox"><input type="checkbox" checked={includeMetadata} onChange={event => setIncludeMetadata(event.target.checked)} /><span>Include complete metadata package<small>Handoff, Resolve metadata CSV, Dublin Core XMP, full JSON, shot CSV and available SRT transcripts in one ZIP.</small></span></label>}
    </fieldset>
    {collecting ? <p role="status">Collecting all search matches…</p> : <p>{media.length} original {media.length === 1 ? 'recording' : 'recordings'} included. Originals stay in place.</p>}
    {media.length > 1000 && <p className="me-error" role="alert">Refine this selection to 1,000 recordings or fewer.</p>}
    {!nativePremiere && !!media.length && media.length <= 1000 && !options && !error && <p role="status">Checking selected media…</p>}
    <details className="me-recordings"><summary>Included recordings ({media.length})</summary><ul className="me-media-list">{media.map(item => {
      const detail = details.get(item.id)
      return <li key={item.id}><strong>{item.title}</strong>{detail && <>{!detail.available && <span className="me-error">{detail.reason}</span>}{!detail.analyzed && <small>No saved clip analysis; original media metadata will be included.</small>}{detail.partial && <small>Includes currently saved results; analysis may be incomplete.</small>}</>}</li>
    })}</ul></details>
    {!nativePremiere && options && !options.available && <p className="me-error" role="alert">{options.reason || 'Some recordings cannot use this format and layout. Expand Included recordings for details, or choose a media event or native import.'}</p>}
    {nativePremiere && <details className="me-recordings"><summary>Saved Premiere selections ({selections.length})</summary><ul className="me-media-list">{selections.map(selection => <li key={selection.id}><strong>{selection.name}</strong><small>{selection.count} {selection.count === 1 ? 'recording' : 'recordings'}</small><button className="lw-text-button" disabled={busy || collecting} onClick={() => void loadSelection(selection)}>Use this selection</button></li>)}</ul>{!selections.length && <p>No saved selections yet.</p>}</details>}
    {(error || (scope === 'matches' && matchError)) && <p className="me-error" role="alert">{error || matchError}</p>}
    {notice && <p role="status">{notice}</p>}
    <footer className="lw-dialog-actions">{!nativePremiere && <button className="lw-button" disabled={busy} onClick={() => { setError(''); setOptions(null); setRetry(value => value + 1) }}>Check again</button>}<button className="lw-button" disabled={busy} onClick={onClose}>Close</button><button className="lw-button lw-button-primary" disabled={busy || collecting || !media.length || media.length > 1000 || !title.trim() || (nativePremiere ? savedKey === selectionKey : !options?.available)} onClick={() => void (nativePremiere ? saveSelection() : download())}><Icon name="download" size={16} />{busy ? 'Preparing export…' : nativePremiere ? savedKey === selectionKey ? 'Saved for Premiere' : scope === 'matches' ? 'Save matches for Premiere' : 'Save selection for Premiere' : includeMetadata ? 'Download editor package' : `Download .${extension}`}</button></footer>
  </dialog>
}
