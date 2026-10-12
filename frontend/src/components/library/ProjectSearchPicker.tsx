import { useEffect, useId, useRef, useState } from 'react'
import type { LibraryFolder, LibraryProject } from '../../api/library'
import type { MatchCutScope } from './matchCutScope'
import { resolveMatchCutScope, searchableMedia } from './matchCutScope'
import { Icon } from './Icon'

export function ProjectSearchPicker({ projects, folders, sourceId, scope, onChange, loading }: {
  projects: LibraryProject[]; folders: LibraryFolder[]; sourceId: string; scope: MatchCutScope
  onChange: (scope: MatchCutScope) => void; loading: boolean
}) {
  const [open, setOpen] = useState(false)
  const count = resolveMatchCutScope(scope, projects, sourceId).length
  return <div className="mc-search-scope">
    <label className="lw-field">Search across<select aria-label="Match-cut search scope" disabled={loading} value={scope.mode} onChange={event => {
      const mode = event.target.value as MatchCutScope['mode']
      if (mode === 'selected') setOpen(true)
      else onChange({ ...scope, mode })
    }}><option value="all">All media</option><option value="current">This media</option><option value="selected">Selected media / folders</option></select></label>
    {scope.mode === 'selected' && <button className="lw-button mc-project-picker-trigger" aria-haspopup="dialog" disabled={loading} onClick={() => setOpen(true)}><Icon name="folder" size={16} />Choose media / folders</button>}
    <small role="status">{loading ? 'Loading media…' : `${count} searchable media ${count === 1 ? 'item' : 'items'}`}</small>
    {open && <ProjectPickerDialog projects={projects} folders={folders} sourceId={sourceId} scope={scope} onClose={() => setOpen(false)} onApply={value => { onChange(value); setOpen(false) }} />}
  </div>
}

function ProjectPickerDialog({ projects, folders, sourceId, scope, onClose, onApply }: {
  projects: LibraryProject[]; folders: LibraryFolder[]; sourceId: string; scope: MatchCutScope
  onClose: () => void; onApply: (scope: MatchCutScope) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const [query, setQuery] = useState('')
  const [tab, setTab] = useState<'media' | 'folders'>('media')
  const [selectedOnly, setSelectedOnly] = useState(false)
  const [draft, setDraft] = useState<MatchCutScope>(() => ({ ...scope, mode: 'selected' }))
  const media = searchableMedia(projects)
  const folderRows = [{ id: 'unfiled', name: 'Unfiled', shot_count: 0 }, ...folders]
    .map(folder => ({ ...folder, count: media.filter(item => (item.folder_id || 'unfiled') === folder.id).length }))
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  const includedByFolder = (item: LibraryProject) => draft.folderIds.includes(item.folder_id || 'unfiled')
  const shownMedia = media.filter(item => (!selectedOnly || draft.mediaIds.includes(item.id) || includedByFolder(item)) &&
    terms.every(term => `${item.title} ${item.filename}`.toLocaleLowerCase().includes(term)))
  const shownFolders = folderRows.filter(folder => (!selectedOnly || draft.folderIds.includes(folder.id)) &&
    terms.every(term => folder.name.toLocaleLowerCase().includes(term)))
  const count = resolveMatchCutScope(draft, projects, sourceId).length

  useEffect(() => {
    const element = dialog.current!
    element.showModal(); input.current?.focus()
    return () => element.close()
  }, [])

  function toggle(field: 'mediaIds' | 'folderIds', id: string) {
    setDraft(previous => ({ ...previous, [field]: previous[field].includes(id) ? previous[field].filter(value => value !== id) : [...previous[field], id] }))
  }
  function selectShown(selected: boolean) {
    const field = tab === 'media' ? 'mediaIds' : 'folderIds'
    const ids = tab === 'media' ? shownMedia.map(item => item.id) : shownFolders.filter(folder => folder.count > 0).map(folder => folder.id)
    setDraft(previous => ({ ...previous, [field]: selected ? [...new Set([...previous[field], ...ids])] : previous[field].filter(id => !ids.includes(id)) }))
  }

  return <dialog ref={dialog} className="mc-project-picker" aria-labelledby={titleId} onCancel={onClose}>
    <header><h2 id={titleId}>Choose match-cut sources</h2><button className="lw-icon-button" aria-label="Close media picker" onClick={onClose}><Icon name="close" size={18} /></button></header>
    <div className="mc-picker-tabs lw-segmented" aria-label="Browse search sources">{(['media', 'folders'] as const).map(value => <button key={value} aria-pressed={tab === value} className={tab === value ? 'is-active' : ''} onClick={() => { setTab(value); setQuery('') }}>{value === 'media' ? 'Media' : 'Folders'}</button>)}</div>
    <div className="mc-project-search"><Icon name="search" size={18} /><input ref={input} type="search" aria-label={tab === 'media' ? 'Search media by title or filename' : 'Search folders'} placeholder={tab === 'media' ? 'Search by title or filename…' : 'Search folders…'} value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="mc-project-picker-filter"><button className="lw-text-button" onClick={() => selectShown(true)}>Select all shown</button><button className="lw-text-button" onClick={() => selectShown(false)}>Clear shown</button><button className="lw-text-button" aria-pressed={selectedOnly} onClick={() => setSelectedOnly(value => !value)}>Selected only</button></div>
    <div className="mc-project-picker-results" role="group" aria-label={tab === 'media' ? 'Media to search' : 'Folders to search'}>
      {tab === 'media' ? shownMedia.map(item => <label key={item.id} className={draft.mediaIds.includes(item.id) || includedByFolder(item) ? 'is-selected' : ''}>
        <input type="checkbox" checked={draft.mediaIds.includes(item.id) || includedByFolder(item)} disabled={includedByFolder(item)} onChange={() => toggle('mediaIds', item.id)} />
        <span><strong>{item.title || item.filename}</strong><small>{includedByFolder(item) ? 'Included through selected folder' : `${item.shot_count} ${item.shot_count === 1 ? 'clip' : 'clips'}`}</small></span>
      </label>) : shownFolders.map(folder => <label key={folder.id} className={draft.folderIds.includes(folder.id) ? 'is-selected' : ''}>
        <input type="checkbox" checked={draft.folderIds.includes(folder.id)} disabled={!folder.count && !draft.folderIds.includes(folder.id)} onChange={() => toggle('folderIds', folder.id)} /><span><strong>{folder.name}</strong><small>{folder.count} searchable media {folder.count === 1 ? 'item' : 'items'}</small></span>
      </label>)}
      {(tab === 'media' ? !shownMedia.length : !shownFolders.length) && <p className="mc-project-picker-empty">No {tab === 'media' ? 'searchable media' : 'folders'} match this view.</p>}
    </div>
    <p className="mc-picker-hint">Analyze media first. Single-take recordings work too. Folder selections include all searchable media in that folder.</p>
    <footer><span>{count} media {count === 1 ? 'item' : 'items'} selected</span><button className="lw-text-button" onClick={() => setDraft({ mode: 'selected', mediaIds: [], folderIds: [] })}>Clear all</button><button className="lw-button" onClick={onClose}>Cancel</button><button className="lw-button lw-button-primary" disabled={!count} onClick={() => onApply(draft)}>Use selection</button></footer>
  </dialog>
}
