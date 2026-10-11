import { useState } from 'react'
import { setDisplayColor } from '../../api/library'
import type { AssetDetail } from '../../api/library'
import { errorMessage } from './format'

export function DisplayColorControl({ asset, disabled, onRefresh, onPause, onPreparing }: {
  asset: AssetDetail; disabled: boolean; onRefresh: () => void; onPause: () => void; onPreparing: (value: boolean) => void
}) {
  const applied = asset.technical.display_color || 'source'
  const [mode, setMode] = useState(applied)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const red = asset.filename.toLowerCase().endsWith('.r3d')
  async function apply() {
    if (disabled || saving) return
    onPause(); onPreparing(true); setSaving(true); setError('')
    try { await setDisplayColor(asset.job_id, mode); onRefresh() }
    catch (error) { setError(errorMessage(error)) }
    finally { setSaving(false); onPreparing(false) }
  }
  return <details className="vw-display-color">
    <summary>Display color</summary>
    <p>{asset.technical.viewing_proxy?.viewing_transform || 'As decoded · no display LUT applied'}</p>
    <div className="vw-display-controls">
      <label>Transform<select aria-label="Display color transform" disabled={disabled || saving} value={mode} onChange={event => setMode(event.target.value)}>
        <option value="source">As decoded</option>
        {red && <option value="red_rec709">RED SDK → Rec.709</option>}
        {!red && <option value="dji_zenmuse_dlog">DJI X5S/X7 D-Log → Rec.709</option>}
      </select></label>
      <button className="lw-button" disabled={disabled || saving || mode === applied} onClick={apply}>{saving ? 'Preparing display preview…' : 'Apply'}</button>
    </div>
    {!red && <small>Select DJI only for Zenmuse X5S/X7 D-Log footage. Other DJI Log profiles need their own transform.</small>}
    <small>Applies to playback and future analysis. Your original camera media stays unchanged.</small>
    {error && <p className="lw-inline-error" role="alert">{error}</p>}
  </details>
}
