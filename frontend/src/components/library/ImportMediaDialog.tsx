import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { importLocalMedia } from '../../api/library'
import type { AssetDetail } from '../../api/library'
import { errorMessage } from './format'
import { Icon } from './Icon'

interface Props { onClose: () => void; onImported: (asset: AssetDetail) => void; onCopy: () => void }

export function ImportMediaDialog({ onClose, onImported, onCopy }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const lock = useRef(false)
  const [original, setOriginal] = useState('')
  const [proxy, setProxy] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { dialog.current?.showModal() }, [])
  async function link(event: FormEvent) {
    event.preventDefault()
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    try { onImported(await importLocalMedia(original, proxy, confirmed)) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { lock.current = false; setBusy(false) }
  }
  return <dialog ref={dialog} className="lw-settings-dialog lw-import-dialog" aria-labelledby="import-media-title" onCancel={event => { if (lock.current) event.preventDefault(); else onClose() }}>
    <div className="lw-dialog-heading"><h2 id="import-media-title">Link original</h2><button className="lw-icon-button" aria-label="Close import" disabled={busy} onClick={onClose}><Icon name="close" /></button></div>
    <p className="lw-model-note">Keep camera media where it is. Local and network paths are supported; only derived proxies and thumbnails are cached.</p>
    <form onSubmit={link}>
      <label className="lw-field"><span>Original file path</span><input required autoFocus value={original} disabled={busy} onChange={event => setOriginal(event.target.value)} placeholder="C:\Footage\clip.mxf or \\server\share\clip.mxf" /></label>
      <details className="lw-details"><summary>Use an existing viewing proxy<Icon name="down" size={14} /></summary>
        <p className="lw-model-note">For Sony RAW/X-OCN, BRAW, R3D, ARRIRAW, or Canon RAW that the installed decoder cannot read. Export a complete clip from Resolve or the camera vendor tool.</p>
        <label className="lw-field"><span>Viewing proxy file path</span><input value={proxy} disabled={busy} onChange={event => { setProxy(event.target.value); setConfirmed(false) }} placeholder="Full-length MP4 or MOV" /></label>
        {proxy.trim() && <label className="lw-import-confirm"><input type="checkbox" required checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />Same complete clip, original frame rate and framing, with no trimming or retiming.</label>}
      </details>
      {error && <p className="lw-inline-error" role="alert">{error}</p>}
      {busy && <p className="lw-model-note" role="status">Checking camera media and decoder… The original is not being copied.</p>}
      <div className="lw-dialog-actions"><button className="lw-button" type="button" disabled={busy} onClick={onClose}>Cancel</button><button className="lw-button lw-button-primary" disabled={busy}>{busy ? 'Linking…' : 'Link original'}</button></div>
    </form>
    <details className="lw-details"><summary>Copy files instead<Icon name="down" size={14} /></summary><p className="lw-model-note">Browser file selection copies the entire file into the local library. Use linked paths for camera originals.</p><button className="lw-button" disabled={busy} onClick={onCopy}>Choose files to copy</button></details>
  </dialog>
}
