import { useEffect, useId, useRef, useState } from 'react'
import { exportUrl, getExportOptions } from '../../api/library'
import type { AssetDetail, ExportFormat, ExportOptions } from '../../api/library'
import type { Shot } from '../../api/types'
import { Icon } from './Icon'
import { duration, errorMessage } from './format'
import './ExportPanel.css'

interface Props {
  asset: AssetDetail
  shots: Shot[]
  selectedShots: Shot[]
  staleSelection: boolean
  onChooseShots: () => void
  onClearSelection: () => void
  initialScope: 'asset' | 'selected_shots'
}

const FORMATS: { format: ExportFormat; title: string; description: string }[] = [
  { format: 'json', title: 'Structured metadata', description: 'Shot metadata, annotations and analysis in a portable document.' },
  { format: 'csv', title: 'Shot list', description: 'Rows for spreadsheets and production handoffs.' },
  { format: 'xmp', title: 'Metadata sidecar', description: 'Asset metadata and shot markers in an XMP file.' },
  { format: 'srt', title: 'Timed transcript', description: 'Subtitles from the available spoken-word transcript.' },
  { format: 'edl', title: 'Edit decision list', description: 'A video-only shot sequence using source timecode.' },
  { format: 'premiere_xml', title: 'Premiere XML', description: 'A shot timeline with original media, source audio and logging metadata.' },
  { format: 'fcpxml', title: 'Final Cut Pro XML', description: 'A timeline for Final Cut Pro and Resolve.' },
]
const PAGE_SIZE = 50
const formatLabel = (format: ExportFormat) => format === 'premiere_xml' ? 'Premiere XML' : format.toUpperCase()

function safeFilename(value: string, format: ExportFormat, selected: boolean) {
  const basename = value.split(/[\\/]/).pop() || 'video'
  const clean = Array.from(basename, character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127 ? '_' : character).join('')
  let stem = clean.replace(/\.[^.]+$/, '').replace(/[<>:"/\\|?*\u202a-\u202e\u2066-\u2069]/g, '_').replace(/[. ]+$/, '').slice(0, 140)
  if (!stem || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem)) stem = `video_${stem || 'metadata'}`
  return `${stem}_${selected ? 'selected_shots' : 'metadata'}.${format === 'premiere_xml' ? 'xml' : format}`
}

function CopyGlyph() {
  return <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5" /><path d="M13 7V3H3v10h4" /></svg>
}

export function ExportPanel({ asset, shots, selectedShots, staleSelection, onChooseShots, onClearSelection, initialScope }: Props) {
  const [scope, setScope] = useState(initialScope)
  const [format, setFormat] = useState<ExportFormat>('json')
  const [page, setPage] = useState(0)
  const [pageRun, setPageRun] = useState(asset.analysis_config?.started_at ?? '')
  const [retry, setRetry] = useState(0)
  const [availability, setAvailability] = useState<{ key: string; data: ExportOptions | null; error: string }>({ key: '', data: null, error: '' })
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const downloadController = useRef<AbortController | null>(null)
  const downloadObjectUrl = useRef<string | null>(null)
  const choiceId = useId()
  const isSelection = scope === 'selected_shots'
  const blocked = isSelection && (staleSelection || selectedShots.length === 0)
  const scopedShots = isSelection ? selectedShots : shots
  const selectedNumbers = isSelection ? selectedShots.map(shot => shot.shot_number).join(',') : ''
  const run = asset.analysis_config?.started_at ?? ''
  const jobId = asset.job_id
  // Primitive signatures keep polling from resetting choices or repeating unchanged requests.
  const requestKey = JSON.stringify([jobId, scope, selectedNumbers, run, blocked, retry,
    asset.technical, asset.transcript, asset.status, asset.deep?.length, !!asset.video_summary,
    shots.map(shot => [shot.shot_number, shot.start_time, shot.end_time])])
  const current = availability.key === requestKey ? availability : null
  const loading = !blocked && current === null
  const options = current?.data
  const chosen = options?.formats.find(option => option.format === format)
  const canDownload = !blocked && !loading && !downloading && !!chosen?.available
  const totalPages = Math.max(1, Math.ceil(scopedShots.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages - 1)
  const visibleShots = scopedShots.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE)
  const active = ['queued', 'analyzing', 'processing'].includes(asset.status)
  if (pageRun !== run) {
    setPageRun(run); setPage(0)
  } else if (page !== currentPage) {
    setPage(currentPage)
  }

  useEffect(() => {
    if (blocked) return
    const controller = new AbortController()
    const numbers = isSelection ? selectedNumbers.split(',').map(Number) : undefined
    getExportOptions(jobId, numbers, run, controller.signal).then(data => {
      if (!controller.signal.aborted) setAvailability({ key: requestKey, data, error: '' })
    }).catch(cause => {
      if (!controller.signal.aborted) setAvailability({ key: requestKey, data: null, error: errorMessage(cause) })
    })
    return () => controller.abort()
  }, [blocked, isSelection, jobId, selectedNumbers, run, requestKey])

  useEffect(() => () => { downloadController.current?.abort() }, [requestKey])
  useEffect(() => () => {
    if (downloadObjectUrl.current) URL.revokeObjectURL(downloadObjectUrl.current)
  }, [])

  function changeScope(next: Props['initialScope']) {
    setScope(next); setPage(0); setError(''); setNotice('')
  }

  async function copy(text: string, label: string) {
    setError(''); setNotice('')
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard access is unavailable. Select and copy the displayed times instead.')
      await navigator.clipboard.writeText(text)
      setNotice(`${label} copied.`)
    } catch (cause) { setError(errorMessage(cause)) }
  }

  function copyAll() {
    const text = ['Shot\tIn (source relative)\tOut (source relative)\tRange', ...scopedShots.map(shot =>
      `${shot.shot_number}\t${shot.start_time}\t${shot.end_time}\t${shot.start_time} - ${shot.end_time}`)].join('\n')
    void copy(text, `${scopedShots.length} shot ${scopedShots.length === 1 ? 'range' : 'ranges'}`)
  }

  async function download() {
    if (!canDownload) return
    const controller = new AbortController()
    downloadController.current = controller
    setDownloading(true); setError(''); setNotice('')
    try {
      const numbers = isSelection ? selectedNumbers.split(',').map(Number) : undefined
      const response = await fetch(exportUrl(jobId, format, numbers, run), { signal: controller.signal })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : `Export failed (${response.status}). Please try again.`)
      }
      const blob = await response.blob()
      if (controller.signal.aborted) return
      const objectUrl = URL.createObjectURL(blob)
      downloadObjectUrl.current = objectUrl
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = safeFilename(asset.metadata.title || asset.filename, format, isSelection)
      document.body.appendChild(anchor)
      anchor.click(); anchor.remove()
      window.setTimeout(() => {
        URL.revokeObjectURL(objectUrl)
        if (downloadObjectUrl.current === objectUrl) downloadObjectUrl.current = null
      }, 1000)
      setNotice(`${formatLabel(format)} download started.`)
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause))
    } finally {
      if (downloadController.current === controller) downloadController.current = null
      setDownloading(false)
    }
  }

  return <section className="ep-panel" aria-label="Export metadata">
    <div className="ep-shell">
      <header className="ep-intro">
        <h2>Export</h2>
      </header>

      <fieldset className="ep-scope" disabled={downloading}>
        <legend>Export scope</legend>
        <div className="ep-scope-grid">
          <label className={scope === 'asset' ? 'is-selected' : ''}>
            <input type="radio" name={`${choiceId}-scope`} value="asset" checked={!isSelection} onChange={() => changeScope('asset')} />
            <span><strong>Whole video</strong><small>{shots.length} saved {shots.length === 1 ? 'shot' : 'shots'} · {duration(asset.technical.duration_seconds)}</small></span>
          </label>
          <label className={isSelection ? 'is-selected' : ''}>
            <input type="radio" name={`${choiceId}-scope`} value="selected_shots" checked={isSelection} onChange={() => changeScope('selected_shots')} />
            <span><strong>Selected shots</strong><small>{selectedShots.length} {selectedShots.length === 1 ? 'shot' : 'shots'} selected</small></span>
          </label>
        </div>
      </fieldset>
      {isSelection && <div className="ep-selection-tools">
        <button className="ep-button" onClick={onChooseShots} disabled={downloading}>{staleSelection ? 'Reselect shots' : selectedShots.length ? 'Change selection' : 'Choose shots'}</button>
        {(selectedShots.length > 0 || staleSelection) && <button className="ep-text-button" disabled={downloading} onClick={() => { onClearSelection(); changeScope('asset') }}>Clear selection</button>}
        <p>{staleSelection ? 'Analysis changed the selected shots. Reselect them before exporting, or clear the selection to export the whole video.' : selectedShots.length ? 'Only these shots and their relevant metadata will be included.' : 'Choose one or more shots in the shot browser to create a scoped export.'}</p>
      </div>}
      {staleSelection && isSelection && <p className="ep-alert" role="alert">This selection is out of date. Selected-shot downloads and time copying are paused.</p>}
      {(active || asset.status === 'error') && <p className="ep-hint"><Icon name="info" size={15} />Exports include the results currently saved. {active ? 'More shots may arrive as analysis continues.' : 'Some sections may be incomplete.'}</p>}

      <fieldset className="ep-formats" disabled={downloading} aria-busy={loading}>
        <legend>File format</legend>
        <div className="ep-format-grid">
          {FORMATS.map(item => {
            const option = options?.formats.find(value => value.format === item.format)
            const unavailable = !!option && !option.available
            return <label key={item.format} className={`ep-format${format === item.format ? ' is-selected' : ''}${unavailable ? ' is-unavailable' : ''}`}>
              <div className="ep-format-heading"><span>{item.format === 'premiere_xml' ? 'XML' : item.format.toUpperCase()}</span><input type="radio" name={`${choiceId}-format`} value={item.format} checked={format === item.format} disabled={blocked || loading || unavailable || !option} onChange={() => { setFormat(item.format); setError(''); setNotice('') }} /></div>
              <strong>{item.title}</strong><p>{item.description}</p>
              {unavailable && <small className="ep-unavailable">Unavailable: {option.reason || 'This format is not available for the current scope.'}</small>}
            </label>
          })}
        </div>
      </fieldset>
      {loading && <p className="ep-hint" role="status">Checking available formats…</p>}
      {current?.error && !blocked && <div className="ep-alert" role="alert"><p>{current.error}</p><button className="ep-button" onClick={() => setRetry(value => value + 1)}>Check again</button></div>}
      <div className="ep-download-bar">
        <div><strong>{formatLabel(format)} · {isSelection ? `${selectedShots.length} selected ${selectedShots.length === 1 ? 'shot' : 'shots'}` : 'Whole video'}</strong><p>{blocked ? 'Choose a current selection to continue.' : chosen && !chosen.available ? chosen.reason : format === 'premiere_xml' ? 'Originals stay in place. Relink in the editor if their location changes.' : format === 'fcpxml' ? 'Keep the original video beside the XML, or relink it in your editor.' : format === 'srt' && isSelection ? 'Complete overlapping subtitle cues keep their original timestamps.' : 'Metadata and editorial interchange files reference your source video.'}</p></div>
        <button className="ep-button ep-primary" disabled={!canDownload} onClick={() => void download()}><Icon name="download" size={17} />{downloading ? 'Preparing download…' : `Download ${formatLabel(format)}`}</button>
      </div>
      {error && <p className="ep-alert" role="alert">{error}</p>}
      {notice && <p className="ep-notice" role="status">{notice}</p>}

      <section className="ep-times" aria-labelledby={`${choiceId}-times`}>
        <div className="ep-times-heading"><div><h3 id={`${choiceId}-times`}>Time ranges</h3><p>Source-relative timestamps. AI boundaries are approximate.</p></div><button className="ep-button" disabled={blocked || scopedShots.length === 0} onClick={copyAll}><CopyGlyph />Copy all ranges</button></div>
        {asset.technical.source_timecode && <p className="ep-source-time">Embedded source timecode: <code>{asset.technical.source_timecode}</code>. The times below are relative to the start of the video.</p>}
        {blocked ? <p className="ep-empty">{staleSelection ? 'Reselect shots to see their current time ranges.' : 'Your selected shot ranges will appear here.'}</p> : scopedShots.length === 0 ? <p className="ep-empty">No shot ranges are available yet. Asset metadata can still be exported in supported formats.</p> : <>
          <div className="ep-table-scroll"><table><thead><tr><th scope="col">Shot</th><th scope="col">In</th><th scope="col">Out</th><th scope="col"><span className="lw-sr-only">Copy range</span></th></tr></thead><tbody>{visibleShots.map(shot => <tr key={`${shot.shot_number}-${shot.start_time}-${shot.end_time}`}>
            <th scope="row"><strong>{String(shot.shot_number).padStart(2, '0')} <span>{shot.shot_type}</span></strong><p title={shot.visual_description}>{shot.visual_description}</p></th>
            <td><button className="ep-time-copy" title="Copy in time" aria-label={`Copy in time for shot ${shot.shot_number}: ${shot.start_time}`} onClick={() => void copy(shot.start_time, `Shot ${shot.shot_number} in time`)}><code>{shot.start_time}</code><CopyGlyph /></button></td>
            <td><button className="ep-time-copy" title="Copy out time" aria-label={`Copy out time for shot ${shot.shot_number}: ${shot.end_time}`} onClick={() => void copy(shot.end_time, `Shot ${shot.shot_number} out time`)}><code>{shot.end_time}</code><CopyGlyph /></button></td>
            <td><button className="ep-time-copy" aria-label={`Copy range for shot ${shot.shot_number}`} onClick={() => void copy(`${shot.start_time} - ${shot.end_time}`, `Shot ${shot.shot_number} range`)}><CopyGlyph /><span>Range</span></button></td>
          </tr>)}</tbody></table></div>
          {totalPages > 1 && <nav className="ep-pagination" aria-label="Shot range pages"><span>{currentPage * PAGE_SIZE + 1}–{Math.min((currentPage + 1) * PAGE_SIZE, scopedShots.length)} of {scopedShots.length} shots</span><div><button className="ep-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button><button className="ep-button" disabled={currentPage === totalPages - 1} onClick={() => setPage(currentPage + 1)}>Next</button></div></nav>}
        </>}
      </section>
    </div>
  </section>
}
