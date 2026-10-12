import { useEffect, useId, useRef, useState } from 'react'
import type { LibraryProject } from '../../api/library'
import { Icon } from './Icon'

export function ProjectSearchPicker({ projects, selectedIds, onChange }: {
  projects: LibraryProject[]
  selectedIds: string[]
  onChange: (ids: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  return <>
    <button className="lw-button mc-project-picker-trigger" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <Icon name="search" size={16} />Search {selectedIds.length} media {selectedIds.length === 1 ? 'item' : 'items'}
    </button>
    {open && <ProjectPickerDialog projects={projects} selectedIds={selectedIds} onClose={() => setOpen(false)} onApply={ids => { onChange(ids); setOpen(false) }} />}
  </>
}

function ProjectPickerDialog({ projects, selectedIds, onClose, onApply }: {
  projects: LibraryProject[]
  selectedIds: string[]
  onClose: () => void
  onApply: (ids: string[]) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const [query, setQuery] = useState('')
  const [selectedOnly, setSelectedOnly] = useState(false)
  const [draft, setDraft] = useState(() => new Set(selectedIds))
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  const shown = projects.filter(project => (!selectedOnly || draft.has(project.id)) &&
    terms.every(term => `${project.title} ${project.filename}`.toLocaleLowerCase().includes(term)))

  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    input.current?.focus()
    return () => element.close()
  }, [])

  function toggle(id: string) {
    setDraft(previous => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return <dialog ref={dialog} className="mc-project-picker" aria-labelledby={titleId} onCancel={onClose}>
    <header><h2 id={titleId}>Search media</h2><button className="lw-icon-button" aria-label="Close media picker" onClick={onClose}><Icon name="close" size={18} /></button></header>
    <div className="mc-project-search"><Icon name="search" size={18} /><input ref={input} type="search" aria-label="Search media by title or filename" placeholder="Search by title or filename…" value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="mc-project-picker-filter"><span role="status">{shown.length} media {shown.length === 1 ? 'item' : 'items'}</span><button className="lw-text-button" aria-pressed={selectedOnly} onClick={() => setSelectedOnly(value => !value)}>Selected only</button></div>
    <div className="mc-project-picker-results" role="group" aria-label="Media to search">
      {shown.map(project => <label key={project.id} className={draft.has(project.id) ? 'is-selected' : ''}>
        <input type="checkbox" checked={draft.has(project.id)} onChange={() => toggle(project.id)} />
        <span><strong>{project.title || project.filename}</strong>{project.title && project.title !== project.filename && <small>{project.filename}</small>}</span>
      </label>)}
      {!shown.length && <p className="mc-project-picker-empty">{query.trim() ? 'No media match your search.' : selectedOnly ? 'No media selected.' : 'No analyzed media available.'}</p>}
    </div>
    <footer><span>{draft.size} selected</span><button className="lw-button" onClick={onClose}>Cancel</button><button className="lw-button lw-button-primary" disabled={!draft.size} onClick={() => onApply([...draft])}>Use selection</button></footer>
  </dialog>
}
