import { useEffect, useRef, useState } from 'react'
import { deleteAsset } from '../../api/library'
import { errorMessage } from './format'
import { forgetMatchCutSession } from './matchCutSession'

export interface DeletionProject { id: string; title: string }

export function DeleteProjectsDialog({ projects, onClose, onDeleted }: {
  projects: DeletionProject[]
  onClose: () => void
  onDeleted: (ids: string[], notes: string[]) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const lock = useRef(false)
  const [remaining, setRemaining] = useState(projects)
  const [busy, setBusy] = useState(false)
  const [completed, setCompleted] = useState(0)
  const [errors, setErrors] = useState<Record<string, string>>({})
  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    return () => element.close()
  }, [])

  async function remove() {
    if (lock.current) return
    lock.current = true; setBusy(true); setErrors({}); setCompleted(0)
    const deleted: string[] = [], notes: string[] = []
    const failures: Record<string, string> = {}
    for (const project of remaining) {
      try {
        const result = await deleteAsset(project.id)
        forgetMatchCutSession(project.id)
        deleted.push(project.id)
        if (result.note) notes.push(`${project.title}: ${result.note}`)
      } catch (err) { failures[project.id] = errorMessage(err) }
      setCompleted(count => count + 1)
    }
    onDeleted(deleted, notes)
    setErrors(failures)
    setRemaining(previous => previous.filter(project => !deleted.includes(project.id)))
    lock.current = false; setBusy(false)
    if (!Object.keys(failures).length) onClose()
  }

  return <dialog ref={dialog} className="lw-settings-dialog lw-delete-projects" aria-labelledby="delete-projects-title" onCancel={event => { if (lock.current) event.preventDefault(); else onClose() }}>
    <h2 id="delete-projects-title">Delete {remaining.length === 1 ? 'media item' : `${remaining.length} media items`}?</h2>
    <p>Removes these media items and their analysis from the app. Deletes only copies and cache files created by the app. Your original source files are never deleted.</p>
    <ul>{remaining.map(project => <li key={project.id}><strong>{project.title}</strong>{errors[project.id] && <span role="alert">{errors[project.id]}</span>}</li>)}</ul>
    {busy && <p role="status">Deleting… {completed} / {remaining.length}</p>}
    {!busy && Object.keys(errors).length > 0 && <p role="status">Some media items couldn’t be deleted. You can retry the remaining media items.</p>}
    <div className="lw-dialog-actions"><button className="lw-button" disabled={busy} onClick={onClose}>Cancel</button><button className="lw-button lw-button-danger" disabled={busy || !remaining.length} onClick={remove}>{busy ? 'Deleting…' : Object.keys(errors).length ? 'Retry deletion' : remaining.length === 1 ? 'Delete media' : `Delete ${remaining.length} media items`}</button></div>
  </dialog>
}
