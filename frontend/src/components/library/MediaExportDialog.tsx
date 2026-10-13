import { useEffect, useRef, useState } from 'react'
import { downloadMediaExport, getMediaExportOptions } from '../../api/library'
import type { MediaExportOptions, MediaExportFormat } from '../../api/library'
import { errorMessage } from './format'
import { Icon } from './Icon'
import './MediaExportDialog.css'

export function MediaExportDialog({ media, onClose, onSavePremiere }: {
  media: { id: string; title: string }[]
  onClose: () => void
  onSavePremiere: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const lock = useRef(false)
  const downloadController = useRef<AbortController | null>(null)
  const [checkedOptions, setOptions] = useState<MediaExportOptions | null>(null)
  const [retry, setRetry] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState('Video Analyzer selects')
  const [layout, setLayout] = useState<'timeline' | 'bin'>('bin')
  const [format, setFormat] = useState<MediaExportFormat>('fcpxml')
  const options = checkedOptions?.format === format && checkedOptions?.layout === layout ? checkedOptions : null
  const extension = { premiere_xml: 'xml', fcpxml: 'fcpxml', resolve: 'py', edl: 'edl' }[format]
  const [includeMetadata, setIncludeMetadata] = useState(true)
  const idsKey = JSON.stringify(media.map(item => item.id))
  const details = new Map(options?.items.map(item => [item.id, item]))

  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    return () => { downloadController.current?.abort(); element.close() }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    getMediaExportOptions(JSON.parse(idsKey), format, layout, controller.signal).then(result => {
      if (!controller.signal.aborted) setOptions(result)
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [idsKey, retry, format, layout])

  async function download() {
    if (lock.current || !options?.available) return
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
    <header className="lw-dialog-heading"><h2 id="media-export-title">Export {media.length} selected media</h2><button className="lw-icon-button" aria-label="Close export" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <p>For native Premiere import, <button className="lw-text-button" disabled={busy} onClick={onSavePremiere}>Save selection for Premiere</button>.</p>
    <p>Send the selected original recordings and their saved metadata to your editor.</p>
    <fieldset disabled={busy}>
      <label className="me-field">Project / timeline name<input value={title} maxLength={200} onChange={event => setTitle(event.target.value)} /></label>
      <label className="me-field">Export format<select value={format} onChange={event => { const next = event.target.value as MediaExportFormat; setFormat(next); setError(''); setNotice(''); if (next === 'edl') setLayout('timeline'); if (next === 'resolve') setLayout('bin') }}>
        <option value="fcpxml">Final Cut Pro XML (.fcpxml)</option>
        <option value="resolve">DaVinci Resolve native media import (.py)</option>
        <option value="edl">CMX3600 EDL (.edl) · video only</option>
        <option value="premiere_xml">Premiere / Resolve legacy XML (.xml)</option>
      </select></label>
      {(format === 'fcpxml' || format === 'premiere_xml') && <label className="me-field">Create<select value={layout} onChange={event => { setLayout(event.target.value as 'timeline' | 'bin'); setNotice('') }}><option value="bin">{format === 'fcpxml' ? 'Media event only · Final Cut Pro' : 'Media bin only · Premiere'}</option><option value="timeline">{format === 'fcpxml' ? 'Timeline + media event · Final Cut / Resolve' : 'Timeline + media bin · Premiere / Resolve'}</option></select></label>}
      <p className="me-hint">{format === 'resolve' ? 'Original video/audio and searchable metadata in a Resolve bin, without XML timing or pixel-aspect checks. Run the included script in Resolve’s Python 3 console; the package includes instructions.' : format === 'edl' ? 'Video-only conform list; requires matching frame rates and known non-drop timecode. Import originals separately and map reel IDs. Choose the package for reel mapping and full metadata.' : format === 'fcpxml' && layout === 'bin' ? 'Whole originals with descriptions and keywords. Final Cut discovers native timing and pixel aspect. For a Resolve bin, choose native Resolve import.' : 'Whole recordings in selection order. Timeline exports require reliable source timing. FCPXML carries known non-square pixel aspect ratios directly; the legacy XML route retains its stricter mapping checks.'}</p>
      <label className="me-checkbox"><input type="checkbox" checked={includeMetadata} onChange={event => setIncludeMetadata(event.target.checked)} /><span>Include complete metadata package<small>Handoff, instructions, full JSON, CSV, XMP and available SRT transcripts in one ZIP.</small></span></label>
    </fieldset>
    <p className="me-hint">Originals are linked in place. Keep drives and camera sidecars available. The receiving editor must support their codec.</p>
    {!options && !error && <p role="status">Checking selected media…</p>}
    <ul className="me-media-list">{media.map(item => {
      const detail = details.get(item.id)
      return <li key={item.id}><strong>{item.title}</strong>{detail && <>{!detail.available && <span className="me-error">{detail.reason}</span>}{!detail.analyzed && <small>No saved clip analysis; original media metadata will be included.</small>}{detail.partial && <small>Includes currently saved results; analysis may be incomplete.</small>}</>}</li>
    })}</ul>
    {options && !options.available && <p className="me-error" role="alert">{options.reason || 'Some media cannot use this format and layout. Choose a media event or Resolve import, or resolve the issues above. No media will be silently skipped.'}</p>}
    {error && <p className="me-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <footer className="lw-dialog-actions"><button className="lw-button" disabled={busy} onClick={() => { setError(''); setOptions(null); setRetry(value => value + 1) }}>Check again</button><button className="lw-button" disabled={busy} onClick={onClose}>Close</button><button className="lw-button lw-button-primary" disabled={busy || !options?.available || !title.trim()} onClick={() => void download()}><Icon name="download" size={16} />{busy ? 'Preparing export…' : includeMetadata ? 'Download editor package' : `Download .${extension}`}</button></footer>
  </dialog>
}
