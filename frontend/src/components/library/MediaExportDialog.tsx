import { useEffect, useRef, useState } from 'react'
import { downloadMediaExport, getMediaExportOptions } from '../../api/library'
import type { MediaExportOptions } from '../../api/library'
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
  const [options, setOptions] = useState<MediaExportOptions | null>(null)
  const [retry, setRetry] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState('Video Analyzer selects')
  const [layout, setLayout] = useState<'timeline' | 'bin'>('timeline')
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
    getMediaExportOptions(JSON.parse(idsKey), controller.signal).then(result => {
      if (!controller.signal.aborted) setOptions(result)
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
    return () => controller.abort()
  }, [idsKey, retry])

  async function download() {
    if (lock.current || !options?.available) return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    const controller = new AbortController()
    downloadController.current = controller
    try {
      const response = await downloadMediaExport(JSON.parse(idsKey), title.trim(), layout, includeMetadata,
        Object.fromEntries(options.items.map(item => [item.id, item.analysis_version])), controller.signal)
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        if (response.status === 409) setOptions(null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : `Export failed (${response.status}).`)
      }
      const blob = await response.blob()
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `Video Analyzer media export.${includeMetadata ? 'zip' : 'xml'}`
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice(`Export download started for ${media.length} media ${media.length === 1 ? 'item' : 'items'}.`)
    } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)) }
    finally { lock.current = false; setBusy(false); downloadController.current = null }
  }

  return <dialog ref={dialog} className="lw-settings-dialog me-dialog" aria-labelledby="media-export-title" onCancel={event => { if (lock.current) event.preventDefault(); else onClose() }}>
    <header className="lw-dialog-heading"><h2 id="media-export-title">Export {media.length} selected media</h2><button className="lw-icon-button" aria-label="Close export" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <p>For a Premiere bin with searchable metadata, <button className="lw-text-button" disabled={busy} onClick={onSavePremiere}>Save selection for Premiere</button>. Native import works with whole recordings even when timeline timing is unverified.</p>
    <p>Import all selected original recordings into Premiere or Resolve. Your saved analysis becomes descriptions, logging notes and markers.</p>
    <fieldset disabled={busy}>
      <label className="me-field">Project / timeline name<input value={title} maxLength={200} onChange={event => setTitle(event.target.value)} /></label>
      <label className="me-field">Create<select value={layout} onChange={event => setLayout(event.target.value as 'timeline' | 'bin')}><option value="timeline">Timeline and media bin · Premiere / Resolve</option><option value="bin">Media bin only · Premiere</option></select></label>
      <p className="me-hint">Whole media, in selection order. Analysis clips are markers; they don’t split your recordings. The timeline uses the first selected source’s frame rate and dimensions.</p>
      <label className="me-checkbox"><input type="checkbox" checked={includeMetadata} onChange={event => setIncludeMetadata(event.target.checked)} /><span>Include complete metadata package<small>One ZIP with editor XML, full analysis JSON, combined CSV, XMP sidecars and available SRT transcripts.</small></span></label>
    </fieldset>
    <p className="me-hint">Originals stay in place; relink in the editor if they’ve moved or are offline. XML imports expose supported logging fields. Use the editor integration to sync other native metadata fields; the package retains the complete data.</p>
    {!options && !error && <p role="status">Checking selected media…</p>}
    <ul className="me-media-list">{media.map(item => {
      const detail = details.get(item.id)
      return <li key={item.id}><strong>{item.title}</strong>{detail && <>{!detail.available && <span className="me-error">{detail.reason}</span>}{!detail.analyzed && <small>No saved clip analysis; original media metadata will be included.</small>}{detail.partial && <small>Includes currently saved results; analysis may be incomplete.</small>}</>}</li>
    })}</ul>
    {options && !options.available && <p className="me-error" role="alert">Some media cannot be included in this XML. Resolve the issues above or change your selection. No media will be silently skipped.</p>}
    {error && <p className="me-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <footer className="lw-dialog-actions"><button className="lw-button" disabled={busy} onClick={() => { setError(''); setOptions(null); setRetry(value => value + 1) }}>Check again</button><button className="lw-button" disabled={busy} onClick={onClose}>Close</button><button className="lw-button lw-button-primary" disabled={busy || !options?.available || !title.trim()} onClick={() => void download()}><Icon name="download" size={16} />{busy ? 'Preparing export…' : includeMetadata ? 'Download editor package' : 'Download editor XML'}</button></footer>
  </dialog>
}
