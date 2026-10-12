import { useState } from 'react'
import type { CosplayIdentification, Shot } from '../../api/types'
import { saveShot } from '../../api/library'
import type { ShotAnnotation } from '../../api/library'
import { errorMessage } from './format'
import './CosplayEditor.css'

const blankCostume = (): CosplayIdentification => ({ character: '', franchise: '', costume_variant: '', visible_details: '', activity: '', possible_characters: [], identification: 'unknown' })

export function CosplayBadges({ records = [] }: { records?: CosplayIdentification[] }) {
  const labels = [...new Set(records.map(item => item.character || 'Unknown costume'))]
  if (!labels.length) return null
  return <div className="cosplay-badges" aria-label="Cosplay identifications">{labels.map(label => <span key={label}>{label}</span>)}</div>
}

export function CosplayEditor({ jobId, shot, annotation, run, onRefresh }: {
  jobId: string; shot: Shot; annotation?: ShotAnnotation; run?: string; onRefresh: () => void
}) {
  const records = annotation?.cosplay ?? shot.cosplay ?? []
  const [draft, setDraft] = useState<CosplayIdentification[] | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  function update(index: number, patch: Partial<CosplayIdentification>) {
    setDraft(items => items?.map((item, i) => i === index ? { ...item, ...patch } : item) ?? null)
  }
  async function save(value: CosplayIdentification[] | null) {
    setSaving(true); setError(''); setNotice('')
    try {
      await saveShot(jobId, shot.shot_number, { cosplay: value }, shot, run)
      setDraft(null); setNotice(value === null ? 'Model suggestions restored.' : 'Cosplay identifications saved.'); onRefresh()
    } catch (err) { setError(errorMessage(err)) }
    finally { setSaving(false) }
  }
  return <section className="cosplay-panel" aria-label={`Cosplay in clip ${shot.shot_number}`}>
    <div className="cosplay-heading"><h3>Cosplay</h3><span>{records.length} {records.length === 1 ? 'costume' : 'costumes'}</span></div>
    {draft === null ? <>
      {!records.length && <p className="lw-field-hint">No costumes identified. Add an identification or run Cosplay analysis.</p>}
      {records.map((item, index) => <div className="cosplay-observation" key={index}>
        <div className="cosplay-heading"><strong>{item.character || 'Unknown costume'}</strong><span className={`cosplay-status is-${item.identification}`}>{item.identification}</span></div>
        {(item.franchise || item.costume_variant) && <p className="cosplay-subtitle">{[item.franchise, item.costume_variant].filter(Boolean).join(' · ')}</p>}
        {item.visible_details && <p>{item.visible_details}</p>}
        {item.activity && <p className="lw-field-hint">{item.activity}</p>}
        {!!item.possible_characters.length && <p className="lw-field-hint">Possible: {item.possible_characters.join(', ')}</p>}
      </div>)}
      <button className="lw-text-button" disabled={saving} onClick={() => { setDraft(records.map(item => ({ ...item, possible_characters: [...item.possible_characters] }))); setNotice(''); setError('') }}>Edit identifications</button>
    </> : <form onSubmit={event => { event.preventDefault(); void save(draft) }}>
      <fieldset disabled={saving}>
        {draft.map((item, index) => <div className="cosplay-observation" key={index}>
          <div className="cosplay-heading"><strong>Costume {index + 1}</strong><button type="button" className="lw-text-button" onClick={() => setDraft(items => items?.filter((_, i) => i !== index) ?? [])}>Remove costume {index + 1}</button></div>
          <div className="cosplay-fields">
            <label className="lw-field">Character<input maxLength={200} value={item.character} placeholder="e.g. Spider-Man" onChange={event => update(index, { character: event.target.value, identification: event.target.value.trim() ? 'suggested' : 'unknown' })} /></label>
            <label className="lw-field">Franchise<input maxLength={200} value={item.franchise} placeholder="e.g. Marvel" onChange={event => update(index, { franchise: event.target.value, identification: item.character ? 'suggested' : 'unknown' })} /></label>
          </div>
          <label className="lw-field">Costume variant<input maxLength={300} value={item.costume_variant} placeholder="e.g. Miles Morales / Spider-Verse" onChange={event => update(index, { costume_variant: event.target.value, identification: item.character ? 'suggested' : 'unknown' })} /></label>
          <label className="lw-field">Visible details<textarea rows={2} maxLength={2000} value={item.visible_details} onChange={event => update(index, { visible_details: event.target.value })} /></label>
          <label className="lw-field">Activity<input maxLength={1000} value={item.activity} onChange={event => update(index, { activity: event.target.value })} /></label>
          <label className="lw-field">Possible characters<input value={item.possible_characters.join(',')} placeholder="Comma-separated alternatives" onChange={event => update(index, { possible_characters: event.target.value.split(',') })} /></label>
          <label className="lw-field">Identification<select value={item.identification} onChange={event => update(index, { identification: event.target.value as CosplayIdentification['identification'] })}><option value="unknown">Unknown</option><option value="suggested" disabled={!item.character.trim()}>Suggested</option><option value="confirmed" disabled={!item.character.trim()}>Confirmed by you</option></select></label>
        </div>)}
        <div className="cosplay-actions"><button type="button" className="lw-button" disabled={draft.length >= 100} onClick={() => setDraft(items => [...(items ?? []), blankCostume()])}>Add costume</button><button type="submit" className="lw-button lw-button-primary">{saving ? 'Saving…' : 'Save identifications'}</button><button type="button" className="lw-text-button" onClick={() => { setDraft(null); setError('') }}>Cancel</button></div>
        {annotation?.cosplay != null && <button type="button" className="lw-text-button" onClick={() => void save(null)}>Restore model suggestions</button>}
      </fieldset>
    </form>}
    {notice && <p role="status" className="lw-inline-success">{notice}</p>}
    {error && <p role="alert" className="lw-inline-error">{error}</p>}
  </section>
}
