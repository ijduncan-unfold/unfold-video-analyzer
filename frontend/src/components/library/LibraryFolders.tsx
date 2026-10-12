import { useEffect, useId, useRef, useState } from 'react'
import { deleteFolder, saveFolder } from '../../api/library'
import type { FolderResponse, LibraryFolder } from '../../api/library'
import { Icon } from './Icon'
import { errorMessage } from './format'
import './LibraryFolders.css'

export const SHOT_DRAG_TYPE = 'application/x-video-analyzer-shots'

export function LibraryFolders({ data, activeId, onSelect, onRefresh, onMove, error }: {
  data: FolderResponse | null; activeId: string; onSelect: (id: string) => void; onRefresh: () => void
  onMove: (ids: string[], folderId: string | null) => Promise<void>; error: string
}) {
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<LibraryFolder | 'new' | null>(null)
  const [removing, setRemoving] = useState<LibraryFolder | null>(null)
  const [dropId, setDropId] = useState<string | null>(null)
  function destination(id: string) {
    return {
      onDragOver: (event: React.DragEvent) => {
        if (!event.dataTransfer.types.includes(SHOT_DRAG_TYPE) || id === '') return
        event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'move'; setDropId(id)
      },
      onDragLeave: () => setDropId(null),
      onDrop: (event: React.DragEvent) => {
        if (!event.dataTransfer.types.includes(SHOT_DRAG_TYPE) || id === '') return
        event.preventDefault(); event.stopPropagation(); setDropId(null)
        try {
          const ids: unknown = JSON.parse(event.dataTransfer.getData(SHOT_DRAG_TYPE))
          if (Array.isArray(ids) && ids.length > 0 && ids.length <= 500 && ids.every(value => typeof value === 'string')) {
            void onMove(ids, id === 'unfiled' ? null : id).catch(() => {})
          }
        } catch { /* External or invalid drag payloads are ignored. */ }
      },
    }
  }
  return <aside className="lf-sidebar" aria-label="Library folders">
    <div className="lf-heading"><h2>Folders</h2><button className="lw-icon-button" aria-label="Create folder" title="Create folder" onClick={() => setEditing('new')}><Icon name="plus" size={17} /></button></div>
    <button className={`lf-folder ${activeId === '' ? 'is-active' : ''}`} aria-pressed={activeId === ''} onClick={() => onSelect('')}><Icon name="film" size={16} /><span>All media</span><small>{data?.total_shots ?? '–'}</small></button>
    <button {...destination('unfiled')} className={`lf-folder ${activeId === 'unfiled' ? 'is-active' : ''} ${dropId === 'unfiled' ? 'is-drop-target' : ''}`} aria-pressed={activeId === 'unfiled'} onClick={() => onSelect('unfiled')}><Icon name="folder" size={16} /><span>Unfiled</span><small>{data?.unfiled_shots ?? '–'}</small></button>
    <input className="lf-search" type="search" aria-label="Search folders" placeholder="Find a folder…" value={query} onChange={event => setQuery(event.target.value)} />
    <div className="lf-list">{data?.folders.filter(folder => folder.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).map(folder => <div {...destination(folder.id)} className={`lf-row ${dropId === folder.id ? 'is-drop-target' : ''}`} key={folder.id}>
      <button className={`lf-folder ${activeId === folder.id ? 'is-active' : ''}`} aria-pressed={activeId === folder.id} onClick={() => onSelect(folder.id)} title={folder.name}><Icon name="folder" size={16} /><span>{folder.name}</span><small>{folder.shot_count}</small></button>
      <div className="lf-actions"><button className="lw-icon-button" aria-label={`Rename folder ${folder.name}`} onClick={() => setEditing(folder)}>✎</button><button className="lw-icon-button" aria-label={`Delete folder ${folder.name}`} onClick={() => setRemoving(folder)}><Icon name="trash" size={13} /></button></div>
    </div>)}</div>
    {data && !data.folders.length && <p className="lf-hint">Create folders to organize your media.</p>}
    <button className="lw-text-button" onClick={() => setEditing('new')}><Icon name="plus" size={14} />New folder</button>
    <p className="lf-hint">Drag media into a folder, or select media and choose Move to folder.</p>
    {error && <p className="lw-inline-error" role="alert">{error}<button className="lw-text-button" onClick={onRefresh}>Try again</button></p>}
    {editing && <FolderDialog title={editing === 'new' ? 'Create folder' : 'Rename folder'} onClose={() => setEditing(null)}><FolderNameForm folder={editing === 'new' ? undefined : editing} onSaved={() => { setEditing(null); onRefresh() }} /></FolderDialog>}
    {removing && <FolderDialog title={`Delete ${removing.name}?`} onClose={() => setRemoving(null)}><DeleteFolderForm folder={removing} onDeleted={() => { if (activeId === removing.id) onSelect('unfiled'); setRemoving(null); onRefresh() }} /></FolderDialog>}
  </aside>
}

function FolderDialog({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const element = dialog.current!
    const returnFocus = document.activeElement
    element.showModal(); element.querySelector<HTMLInputElement>('input')?.focus()
    return () => { element.close(); if (returnFocus instanceof HTMLElement && returnFocus.isConnected) returnFocus.focus() }
  }, [])
  return <dialog className="lf-dialog" ref={dialog} aria-labelledby={titleId} onCancel={onClose}><header><h2 id={titleId}>{title}</h2><button className="lw-icon-button" aria-label="Close folder dialog" onClick={onClose}><Icon name="close" size={18} /></button></header>{children}</dialog>
}

function FolderNameForm({ folder, onSaved }: { folder?: LibraryFolder; onSaved: () => void }) {
  const [name, setName] = useState(folder?.name || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <form onSubmit={async event => {
    event.preventDefault(); if (busy || !name.trim()) return
    setBusy(true); setError('')
    try { await saveFolder(name.trim(), folder?.id); onSaved() } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }}><label className="lw-field">Folder name<input autoFocus required maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label>{error && <p className="lw-inline-error" role="alert">{error}</p>}<button className="lw-button lw-button-primary" disabled={busy || !name.trim()}>{busy ? 'Saving…' : folder ? 'Save name' : 'Create folder'}</button></form>
}

function DeleteFolderForm({ folder, onDeleted }: { folder: LibraryFolder; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <><p>Its media will move to Unfiled. All media and analysis will be kept.</p>{error && <p className="lw-inline-error" role="alert">{error}</p>}<button className="lw-button lw-button-danger" disabled={busy} onClick={async () => {
    if (busy) return; setBusy(true); setError('')
    try { await deleteFolder(folder.id); onDeleted() } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }}>{busy ? 'Deleting…' : 'Delete folder'}</button></>
}

export function FolderMovePicker({ folders, ids, disabled, onMove }: {
  folders: LibraryFolder[]; ids: string[]; disabled: boolean; onMove: (ids: string[], folderId: string | null) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  return <><button className="lw-button" disabled={disabled || !ids.length} onClick={() => setOpen(true)}><Icon name="folder" size={15} />Move to folder</button>{open && <FolderDialog title={`Move ${ids.length} media ${ids.length === 1 ? 'item' : 'items'}`} onClose={() => setOpen(false)}><MoveForm folders={folders} ids={ids} onMove={onMove} onDone={() => setOpen(false)} /></FolderDialog>}</>
}

function MoveForm({ folders, ids, onMove, onDone }: { folders: LibraryFolder[]; ids: string[]; onMove: (ids: string[], folderId: string | null) => Promise<void>; onDone: () => void }) {
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function move(id: string | null) {
    if (busy) return; setBusy(true); setError('')
    try { await onMove(ids, id); onDone() } catch (err) { setError(errorMessage(err)) } finally { setBusy(false) }
  }
  const shown = folders.filter(folder => folder.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  return <><input className="lf-search" type="search" autoFocus aria-label="Find destination folder" placeholder="Find a folder…" value={query} onChange={event => setQuery(event.target.value)} /><div className="lf-destinations"><button className="lf-folder" disabled={busy} onClick={() => void move(null)}><Icon name="folder" size={16} />Unfiled</button>{shown.map(folder => <button className="lf-folder" disabled={busy} key={folder.id} onClick={() => void move(folder.id)}><Icon name="folder" size={16} /><span>{folder.name}</span></button>)}{query && !shown.length && <p>No folders match your search.</p>}</div>{busy && <p role="status">Moving media…</p>}{error && <p className="lw-inline-error" role="alert">{error}</p>}</>
}
