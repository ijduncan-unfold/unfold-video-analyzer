import { useEffect, useRef, useState } from 'react'
import { getPremiereSelections, savePremiereSelection } from '../../api/library'
import type { PremiereSelection } from '../../api/library'
import { errorMessage } from './format'
import './MediaExportDialog.css'

export function PremiereSelectionDialog({ media, initialName, onClose, onLoad }: {
  media: { id: string; title: string }[]; initialName: string; onClose: () => void; onLoad: (selection: PremiereSelection) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const lock = useRef(false)
  const [name, setName] = useState(initialName)
  const [saved, setSaved] = useState<PremiereSelection | null>(null)
  const [selections, setSelections] = useState<PremiereSelection[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const element = dialog.current!
    let active = true
    element.showModal()
    getPremiereSelections().then(data => { if (active) setSelections(data.selections) }).catch(cause => { if (active) setError(errorMessage(cause)) })
    return () => { active = false; element.close() }
  }, [])
  async function save() {
    if (lock.current || !name.trim() || !media.length) return
    lock.current = true; setBusy(true); setError('')
    try {
      const selection = await savePremiereSelection(name.trim(), media.map(item => item.id))
      setSaved(selection); setSelections(previous => [selection, ...previous])
    } catch (cause) { setError(errorMessage(cause)) }
    finally { lock.current = false; setBusy(false) }
  }
  return <dialog ref={dialog} className="lw-settings-dialog me-dialog" aria-labelledby="premiere-selection-title" onCancel={event => { if (lock.current) event.preventDefault(); else onClose() }}>
    <header className="lw-dialog-heading"><h2 id="premiere-selection-title">{media.length ? 'Save selection for Premiere' : 'Premiere selections'}</h2><button className="lw-button" onClick={onClose} disabled={busy}>Close</button></header>
    <p>Import the matching original recordings and their searchable metadata into a named Premiere bin. Whole clips stay editable, including media with variable or unverified frame timing.</p>
    {!!media.length && !saved && <>
      <label className="me-field">Selection name<input value={name} maxLength={160} disabled={busy} onChange={event => setName(event.target.value)} placeholder="Wonder Woman selects" /></label>
      <p>{media.length} original {media.length === 1 ? 'recording' : 'recordings'} · saved as a fixed selection</p>
      <ul className="me-media-list">{media.map(item => <li key={item.id}>{item.title}</li>)}</ul>
      <button className="lw-button lw-button-primary" disabled={busy || !name.trim() || media.length > 1000} onClick={() => void save()}>{busy ? 'Saving…' : 'Save for Premiere'}</button>
      {media.length > 1000 && <p role="alert">Refine your search to 1,000 recordings or fewer.</p>}
    </>}
    {saved && <p role="status"><strong>“{saved.name}” saved · {saved.count} {saved.count === 1 ? 'recording' : 'recordings'}.</strong></p>}
    <p className="me-hint">In Premiere, open the Unfold Video Analyzer integration (0.1.21 or newer), connect, click <strong>Saved analyzer selections</strong>, then <strong>Import clips + metadata</strong>. Keep the companion app running and the originals accessible on this computer. This imports real media into your project; it does not copy video files or require another analysis.</p>
    {error && <p className="me-error" role="alert">{error}</p>}
    <h3>Saved selections</h3>
    <ul className="me-media-list">{selections.map(selection => <li key={selection.id}><strong>{selection.name}</strong><small>{selection.count} {selection.count === 1 ? 'recording' : 'recordings'} · {new Date(selection.created_at).toLocaleDateString()}</small><button className="lw-text-button" disabled={busy} onClick={() => onLoad(selection)}>Load selection in library</button></li>)}</ul>
    {!selections.length && <p>No saved selections yet. Search or filter your footage, then save the matching recordings.</p>}
  </dialog>
}
