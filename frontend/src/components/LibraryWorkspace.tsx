import { useCallback, useEffect, useRef, useState } from 'react'
import { getAsset, getCapabilities, getLibrary, getProjects, getShots } from '../api/library'
import type { AssetDetail, Capabilities, LibraryAsset, LibraryResponse, LibraryShot } from '../api/library'
import { uploadVideo } from '../api/upload'
import { copyEditorPairingCode, getDesktopHealth, getDesktopToken, saveDesktopGoogleApiKey } from '../api/desktop'
import { useSettingsStore } from '../stores/settingsStore'
import { AssetInspector } from './library/AssetInspector'
import { Icon } from './library/Icon'
import { duration, errorMessage, reviewLabel } from './library/format'
import { isActiveAnalysis } from './library/analysisProgress'
import { DeleteProjectsDialog } from './library/DeleteProjectsDialog'
import type { DeletionProject } from './library/DeleteProjectsDialog'
import './LibraryWorkspace.css'

export function LibraryWorkspace({ onOpenAnalyzer }: { onOpenAnalyzer: () => void }) {
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [project, setProject] = useState('')
  const [review, setReview] = useState('')
  const [tag, setTag] = useState('')
  const [rights, setRights] = useState('')
  const [collection, setCollection] = useState('')
  const [searchMode, setSearchMode] = useState<'assets' | 'shots'>('assets')
  const [layout, setLayout] = useState<'grid' | 'list'>('grid')
  const [sort, setSort] = useState('default')
  const [library, setLibrary] = useState<LibraryResponse | null>(null)
  const [projects, setProjects] = useState<Awaited<ReturnType<typeof getProjects>>['projects'] | null>(null)
  const [projectsError, setProjectsError] = useState('')
  const [shotProjectId, setShotProjectId] = useState('')
  const [shotResults, setShotResults] = useState<LibraryShot[]>([])
  const [shotTotal, setShotTotal] = useState(0)
  const [shotResultProject, setShotResultProject] = useState('')
  const [pagination, setPagination] = useState({ context: '', page: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [selection, setSelection] = useState<{ id: string; seconds?: number; shot?: number } | null>(null)
  const [checkedProjects, setCheckedProjects] = useState<DeletionProject[]>([])
  const [deletingProjects, setDeletingProjects] = useState<DeletionProject[] | null>(null)
  const [detail, setDetail] = useState<AssetDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [settings, setSettings] = useState(false)
  const [upload, setUpload] = useState<{ filename: string; current: number; total: number; percent: number } | null>(null)
  const [uploadNotice, setUploadNotice] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [dragging, setDragging] = useState(false)
  const uploadLock = useRef(false)
  const dragDepth = useRef(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const workspace = useRef<HTMLDivElement>(null)
  const libraryMain = useRef<HTMLElement>(null)
  const libraryReturnFocus = useRef<HTMLElement | null>(null)
  const libraryScrollTop = useRef(0)
  const libraryWindowScroll = useRef({ x: 0, y: 0 })
  const wasSelected = useRef(false)
  const refreshLibrary = useCallback(() => setRefresh(value => value + 1), [])
  const reviewFilter = review
  const pageContext = JSON.stringify([query, project, reviewFilter, tag, rights, collection, searchMode, sort, searchMode === 'shots' ? shotProjectId : ''])
  const page = pagination.context === pageContext ? pagination.page : 0
  const pageSize = 60
  const selectedId = selection?.id
  const detailReady = !!selectedId && detail?.job_id === selectedId

  function openVideo(next: NonNullable<typeof selection>) {
    if (!selection) {
      const activeElement = document.activeElement
      libraryReturnFocus.current = activeElement instanceof HTMLElement && activeElement !== fileInput.current && libraryMain.current?.contains(activeElement) ? activeElement : searchInput.current
      libraryScrollTop.current = libraryMain.current?.scrollTop || 0
      libraryWindowScroll.current = { x: window.scrollX, y: window.scrollY }
    }
    setShotProjectId(next.id); setSelection(next); setDetailError(''); setDragging(false); dragDepth.current = 0
  }

  useEffect(() => {
    if (selectedId) {
      wasSelected.current = true
      window.scrollTo(0, 0)
      workspace.current?.querySelector<HTMLElement>('[data-video-back]')?.focus({ preventScroll: true })
    } else if (wasSelected.current) {
      wasSelected.current = false
      if (libraryMain.current) libraryMain.current.scrollTop = libraryScrollTop.current
      window.scrollTo(libraryWindowScroll.current.x, libraryWindowScroll.current.y)
      const target = libraryReturnFocus.current
      if (target?.isConnected && !target.closest('[hidden]')) target.focus({ preventScroll: true })
      else searchInput.current?.focus({ preventScroll: true })
    }
  }, [selectedId, detailReady])

  useEffect(() => {
    let active = true
    getCapabilities().then(result => { if (active) setCapabilities(result) }).catch(() => {})
    return () => { active = false }
  }, [settings])

  useEffect(() => {
    const controller = new AbortController()
    getProjects(controller.signal).then(result => {
      if (controller.signal.aborted) return
      setProjects(result.projects); setProjectsError('')
      setShotProjectId(current => result.projects.some(item => item.id === current) ? current : result.projects[0]?.id || '')
    }).catch(err => { if (!controller.signal.aborted) setProjectsError(errorMessage(err)) })
    return () => controller.abort()
  }, [refresh])

  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true); setError('')
      try {
        const [result, shots] = await Promise.all([
          getLibrary({ q: searchMode === 'assets' ? query : '', project, review_status: searchMode === 'assets' ? reviewFilter : '', tag: searchMode === 'assets' ? tag : '', rights_status: rights, collection, limit: pageSize, offset: searchMode === 'assets' ? page * pageSize : 0, sort: sort === 'default' ? (query ? 'relevance' : 'newest') : sort }, controller.signal),
          searchMode === 'shots' && shotProjectId ? getShots({ q: query, job_id: shotProjectId, project, review_status: reviewFilter, tag, rights_status: rights, collection, limit: pageSize, offset: page * pageSize }, controller.signal) : Promise.resolve(null),
        ])
        if (controller.signal.aborted) return
        setLibrary(result); setShotResults(shots?.shots.filter(shot => shot.job_id === shotProjectId) || []); setShotTotal(shots?.total || 0); setShotResultProject(shotProjectId)
        const resultTotal = searchMode === 'shots' ? shots?.total || 0 : result.total
        if (page > 0 && resultTotal <= page * pageSize) setPagination({ context: pageContext, page: Math.max(0, Math.ceil(resultTotal / pageSize) - 1) })
      } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)) }
      finally { if (!controller.signal.aborted) setLoading(false) }
    }, query ? 280 : 0)
    return () => { clearTimeout(timer); controller.abort() }
  }, [query, project, reviewFilter, tag, rights, collection, searchMode, shotProjectId, sort, page, pageContext, refresh])

  useEffect(() => {
    if (!selectedId) return
    const controller = new AbortController()
    getAsset(selectedId, controller.signal).then(result => {
      if (!controller.signal.aborted) { setDetail(result); setDetailError('') }
    }).catch(err => { if (!controller.signal.aborted) setDetailError(errorMessage(err)) })
    return () => controller.abort()
  }, [selectedId, refresh])

  const analyzing = (library?.active_jobs || 0) > 0 || library?.assets.some(asset => isActiveAnalysis(asset.status)) || (detail?.job_id === selectedId && isActiveAnalysis(detail?.status))
  useEffect(() => {
    if (!analyzing) return
    const interval = setInterval(refreshLibrary, 4000)
    return () => clearInterval(interval)
  }, [analyzing, refreshLibrary])

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (!selectedId && (event.metaKey || event.ctrlKey) && event.key === 'k') { event.preventDefault(); searchInput.current?.focus() }
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  }, [selectedId])

  async function importFiles(files: FileList | File[]) {
    const incoming = Array.from(files)
    if (!incoming.length || uploadLock.current) return
    uploadLock.current = true; setUploadNotice(''); setUploadError('')
    let imported = 0
    const errors: string[] = []
    try {
      for (let index = 0; index < incoming.length; index++) {
        const file = incoming[index]
        setUpload({ filename: file.name, current: index + 1, total: incoming.length, percent: 0 })
        try {
          const result = await uploadVideo(file, percent => setUpload(previous => previous ? { ...previous, percent } : previous))
          imported++; refreshLibrary()
          setShotProjectId(result.job_id)
          if (incoming.length === 1) openVideo({ id: result.job_id })
        } catch (err) { errors.push(`${file.name}: ${errorMessage(err)}`) }
      }
      if (imported) setUploadNotice(`${imported} ${imported === 1 ? 'project' : 'projects'} imported.`)
      if (errors.length) setUploadError(errors.join('\n'))
    } finally { setUpload(null); uploadLock.current = false; if (fileInput.current) fileInput.current.value = '' }
  }

  function toggleProject(asset: LibraryAsset) {
    setCheckedProjects(previous => previous.some(item => item.id === asset.job_id)
      ? previous.filter(item => item.id !== asset.job_id)
      : [...previous, { id: asset.job_id, title: asset.metadata.title || asset.filename }])
  }
  function projectsDeleted(ids: string[], notes: string[]) {
    if (!ids.length) return
    setCheckedProjects(previous => previous.filter(item => !ids.includes(item.id)))
    setLibrary(previous => previous ? { ...previous, assets: previous.assets.filter(item => !ids.includes(item.job_id)) } : previous)
    setUploadNotice(`${ids.length} ${ids.length === 1 ? 'project' : 'projects'} deleted.${notes.length ? ` ${notes.join(' ')}` : ''}`)
    refreshLibrary()
  }

  function clearFilters() { setQuery(''); setProject(''); setReview(''); setTag(''); setRights(''); setCollection('') }
  const assets = library?.assets || []
  const selectableProjects = assets.filter(asset => !isActiveAnalysis(asset.status) && asset.status !== 'deleting')
  const allPageSelected = selectableProjects.length > 0 && selectableProjects.every(asset => checkedProjects.some(item => item.id === asset.job_id))
  const shots = shotResultProject === shotProjectId ? shotResults : []
  const hasFilters = !!(query || project || review || tag || rights || collection)
  const count = searchMode === 'shots' ? (shotResultProject === shotProjectId ? shotTotal : 0) : library?.total || 0
  const projectLoading = searchMode === 'shots' && !projects && !projectsError
  const shotScopeLoading = searchMode === 'shots' && !!shotProjectId && shotResultProject !== shotProjectId
  const projectError = searchMode === 'shots' ? projectsError : ''
  const displayError = error || (!projects ? projectError : '')
  const hasActiveFilters = !!(project || review || tag || rights || collection)
  return <div ref={workspace} className={`lw-workspace ${selection ? 'has-video-workspace' : ''}`}>
    <main ref={libraryMain} hidden={!!selection} className="lw-main" onDragEnter={event => { event.preventDefault(); if (event.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true) } }} onDragOver={event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault() }} onDragLeave={event => { event.preventDefault(); dragDepth.current--; if (dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false) } }} onDrop={event => { event.preventDefault(); dragDepth.current = 0; setDragging(false); importFiles(event.dataTransfer.files) }}>
      <h1 className="lw-sr-only">Footage library</h1>
      <div className="lw-library-content">
        <header className="lw-toolbar">
          <div className="lw-search"><Icon name="search" size={19} /><input ref={searchInput} value={query} onChange={event => setQuery(event.target.value)} placeholder={searchMode === 'shots' ? 'Search shots' : 'Search projects'} aria-label="Search footage" />{query && <button className="lw-icon-button" aria-label="Clear search" onClick={() => setQuery('')}><Icon name="close" size={15} /></button>}</div>
          <div className="lw-toolbar-actions">
            <div className="lw-segmented" aria-label="Search result type"><button className={searchMode === 'assets' ? 'is-active' : ''} aria-pressed={searchMode === 'assets'} onClick={() => setSearchMode('assets')}>Projects</button><button className={searchMode === 'shots' ? 'is-active' : ''} aria-pressed={searchMode === 'shots'} onClick={() => setSearchMode('shots')}>Shots</button></div>
            <button className={`lw-button lw-filter-toggle ${hasActiveFilters ? 'has-filters' : ''}`} aria-expanded={filtersOpen} aria-controls="library-filters" onClick={() => setFiltersOpen(open => !open)}>Filters{hasActiveFilters && <span className="lw-filter-dot" aria-label="Filters active" />}</button>
            <button className="lw-button lw-button-primary" onClick={() => fileInput.current?.click()} disabled={!!upload}><Icon name="plus" size={17} />{upload ? 'Importing…' : 'Import'}</button>
            <button className="lw-icon-button" aria-label="Settings" title="Settings" onClick={() => setSettings(true)}><Icon name="settings" size={18} /></button>
          </div>
        </header>
        {searchMode === 'assets' && (assets.length > 0 || checkedProjects.length > 0) && <div className="lw-project-selection" role="group" aria-label="Project selection">
          <label><input type="checkbox" aria-label="Select projects on this page" checked={allPageSelected} disabled={loading || !selectableProjects.length} onChange={event => setCheckedProjects(previous => event.target.checked ? [...previous, ...selectableProjects.filter(asset => !previous.some(item => item.id === asset.job_id)).map(asset => ({ id: asset.job_id, title: asset.metadata.title || asset.filename }))] : previous.filter(item => !selectableProjects.some(asset => asset.job_id === item.id)))} />Select page</label>
          {!!checkedProjects.length && <><span>{checkedProjects.length} selected</span><button className="lw-text-button" onClick={() => setCheckedProjects([])}>Clear selection</button><button className="lw-button lw-button-danger" onClick={() => setDeletingProjects(checkedProjects)}><Icon name="trash" size={15} />Delete selected</button></>}
        </div>}
        {searchMode === 'shots' && <div className="lw-project-scope">
          <label><span>Project</span><select aria-label="Shot project" required value={shotProjectId} disabled={!projects?.length} onChange={event => setShotProjectId(event.target.value)}>
            {!projects?.length && <option value="">{projectLoading ? 'Loading projects…' : 'No projects yet'}</option>}
            {projects?.map(item => <option value={item.id} key={item.id}>{item.title || item.filename}</option>)}
          </select></label>
          {shotProjectId && <button className="lw-text-button" onClick={() => openVideo({ id: shotProjectId })}>Open project<Icon name="arrow" size={14} /></button>}
          {projectError && projects && <div className="lw-inline-error" role="alert">{projectError}<button className="lw-text-button" onClick={refreshLibrary}>Try again</button></div>}
        </div>}
        <input ref={fileInput} className="lw-file-input" type="file" multiple accept="video/*,.mxf,.mkv,.avi,.mov,.mp4,.webm,.m4v,.mpg,.mpeg,.mts,.m2ts" aria-label="Select videos to import" onChange={event => { if (event.target.files) importFiles(event.target.files) }} />
        {filtersOpen && <div id="library-filters" className="lw-filter-panel">
          <div className="lw-filters">
            <label><span>Project label</span><select aria-label="Filter by project label" value={project} onChange={event => setProject(event.target.value)}><option value="">Any label</option>{library?.facets.projects.map(name => <option key={name}>{name}</option>)}</select></label>
            <label><span>Review</span><select aria-label="Filter by review status" value={review} onChange={event => setReview(event.target.value)}><option value="">Any status</option><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="needs_changes">Needs changes</option></select></label>
            <label><span>Tag</span><select aria-label="Filter by tag" value={tag} onChange={event => setTag(event.target.value)}><option value="">All tags</option>{library?.facets.tags.map(name => <option key={name}>{name}</option>)}</select></label>
            <label><span>Rights</span><select aria-label="Filter by usage rights" value={rights} onChange={event => setRights(event.target.value)}><option value="">All rights</option><option value="cleared">Cleared</option><option value="restricted">Restricted</option><option value="unknown">Unverified</option></select></label>
            <label><span>Collection</span><select aria-label="Filter by collection" value={collection} onChange={event => setCollection(event.target.value)}><option value="">All collections</option>{library?.facets.collections?.map(name => <option key={name}>{name}</option>)}</select></label>
            {searchMode === 'assets' && <label><span>Sort</span><select aria-label="Sort footage" value={sort === 'newest' && !query ? 'default' : sort} onChange={event => setSort(event.target.value)}><option value="default">{query ? 'Relevance' : 'Newest first'}</option><option value="name">Name A–Z</option>{query && <option value="newest">Newest first</option>}</select></label>}
          </div>
          <div className="lw-filter-panel-actions">{hasFilters && <button className="lw-text-button" onClick={clearFilters}>Clear filters</button>}<div className="lw-view-toggle"><button className={layout === 'grid' ? 'is-active' : ''} aria-label="Grid view" aria-pressed={layout === 'grid'} onClick={() => setLayout('grid')}><Icon name="grid" size={16} /></button><button className={layout === 'list' ? 'is-active' : ''} aria-label="List view" aria-pressed={layout === 'list'} onClick={() => setLayout('list')}><Icon name="list" size={17} /></button></div></div>
        </div>}
        {upload && <div className="lw-upload-progress" role="status"><Icon name="upload" size={17} /><div><strong>{upload.filename} · {upload.current}/{upload.total}</strong><span>{upload.percent === 100 ? 'Preparing video…' : `${upload.percent}% uploaded`}</span><progress max={100} value={upload.percent} /></div></div>}
        {uploadNotice && <div className="lw-notice" role="status"><Icon name="check" size={17} /><span>{uploadNotice}</span><button className="lw-icon-button" aria-label="Dismiss notification" onClick={() => setUploadNotice('')}><Icon name="close" size={15} /></button></div>}
        {uploadError && <div className="lw-inline-error" role="alert">{uploadError}<button className="lw-text-button" onClick={() => setUploadError('')}>Dismiss</button></div>}
        {(loading || projectLoading || shotScopeLoading) && library && <span className="lw-loading-status" role="status">Loading…</span>}
        {displayError ? <div className="lw-empty-state lw-error-state"><h2>Couldn’t load {projectError && !projects ? 'projects' : 'footage'}</h2><p>{displayError}</p><button className="lw-button" onClick={refreshLibrary}>Try again</button></div> : (loading && !library) || projectLoading || shotScopeLoading ? <div className="lw-asset-grid" aria-label="Loading library" aria-busy="true">{Array.from({ length: 6 }, (_, index) => <div className="lw-skeleton" key={index}><div /><span /><span /></div>)}</div> : !count ? <EmptyState hasLibrary={!!library?.stats.assets} filtered={hasFilters} shots={searchMode === 'shots'} onImport={() => fileInput.current?.click()} importing={!!upload} onClear={clearFilters} onVideos={() => setSearchMode('assets')} /> : <div className={`${layout === 'grid' ? 'lw-asset-grid' : 'lw-asset-list'} ${loading ? 'is-updating' : ''}`}>{searchMode === 'assets' ? assets.map(asset => <AssetCard key={asset.job_id} asset={asset} selected={selection?.id === asset.job_id} onClick={() => openVideo({ id: asset.job_id })} checked={checkedProjects.some(item => item.id === asset.job_id)} onToggle={() => toggleProject(asset)} onDelete={() => setDeletingProjects([{ id: asset.job_id, title: asset.metadata.title || asset.filename }])} />) : shots.map(shot => <ShotCard key={`${shot.job_id}-${shot.shot_number}`} shot={shot} selected={selection?.id === shot.job_id && selection?.shot === shot.shot_number} onClick={() => openVideo({ id: shot.job_id, seconds: shot.start_seconds, shot: shot.shot_number })} />)}</div>}
        {!displayError && count > pageSize && <div className="lw-pagination"><span>{(page * pageSize + 1).toLocaleString()}–{Math.min((page + 1) * pageSize, count).toLocaleString()} of {count.toLocaleString()}</span><div><button className="lw-button" disabled={page === 0 || loading} onClick={() => setPagination({ context: pageContext, page: page - 1 })}>Previous</button><button className="lw-button" disabled={(page + 1) * pageSize >= count || loading} onClick={() => setPagination({ context: pageContext, page: page + 1 })}>Next</button></div></div>}
      </div>
      {dragging && <div className="lw-drop-overlay"><Icon name="upload" size={32} /><h2>Drop videos here</h2></div>}
    </main>
    {selection && (detail?.job_id === selection.id ? <AssetInspector key={`${selection.id}-${selection.shot || 'asset'}`} asset={detail} initialSeconds={selection.seconds} initialShot={selection.shot} capabilities={capabilities} onClose={() => setSelection(current => current?.id === selection.id ? null : current)} onRefresh={refreshLibrary} onConfigure={() => setSettings(true)} onRemoved={note => setUploadNotice(`Video removed.${note ? ` ${note}` : ''}`)} /> : <main className="lw-inspector lw-video-workspace lw-video-workspace-loading" aria-label="Video workspace"><button className="lw-button lw-back-to-library" data-video-back onClick={() => setSelection(null)}><Icon name="back" />Back to library</button>{detailError ? <><p role="alert">{detailError}</p><button className="lw-button" onClick={refreshLibrary}>Try again</button></> : <p role="status">Opening video…</p>}</main>)}
    {deletingProjects && <DeleteProjectsDialog projects={deletingProjects} onClose={() => setDeletingProjects(null)} onDeleted={projectsDeleted} />}
    {settings && <SettingsDialog capabilities={capabilities} onClose={() => setSettings(false)} onOpenAnalyzer={onOpenAnalyzer} />}
  </div>
}

function AssetCard({ asset, selected, onClick, checked, onToggle, onDelete }: { asset: LibraryAsset; selected: boolean; onClick: () => void; checked: boolean; onToggle: () => void; onDelete: () => void }) {
  const isAnalyzing = ['queued', 'analyzing', 'processing'].includes(asset.status)
  const busy = isActiveAnalysis(asset.status) || asset.status === 'deleting'
  const title = asset.metadata.title || asset.filename
  return <div className={`lw-project-card ${checked ? 'is-checked' : ''}`}>
    <div className="lw-project-card-actions"><input type="checkbox" aria-label={`Select project ${title}`} checked={checked} disabled={busy} title={busy ? 'Finish or cancel analysis before deleting this project.' : 'Select project'} onChange={onToggle} /><button className="lw-icon-button" aria-label={`Delete project ${title}`} title={busy ? 'Finish or cancel analysis before deleting this project.' : 'Delete project'} disabled={busy} onClick={onDelete}><Icon name="trash" size={16} /></button></div>
    <button className={`lw-asset-card ${selected ? 'is-selected' : ''}`} onClick={onClick} aria-pressed={selected}>
    <div className="lw-card-image">{asset.thumbnail_url ? <img src={asset.thumbnail_url} alt="" loading="lazy" /> : <div className="lw-card-placeholder"><Icon name="film" size={32} /><span>{asset.mime_type?.split('/')[1]?.toUpperCase() || 'VIDEO'}</span></div>}<span className="lw-card-play"><Icon name="play" size={17} /></span><span className="lw-card-duration">{duration(asset.technical.duration_seconds)}</span>{isAnalyzing ? <span className="lw-card-status analyzing"><Icon name="spark" size={11} />{asset.status === 'queued' ? 'Queued' : 'Analyzing'}</span> : asset.status === 'error' ? <span className="lw-card-status error">Analysis failed</span> : null}</div>
    <div className="lw-card-content"><div className="lw-card-title"><h3 title={asset.metadata.title || asset.filename}>{asset.metadata.title || asset.filename}</h3><span className={`lw-review-dot ${asset.metadata.review_status}`} title={reviewLabel(asset.metadata.review_status)} /></div><div className="lw-card-subtitle">{asset.metadata.project && <span>{asset.metadata.project}</span>}</div>{asset.match_context && asset.match_context !== 'Matched asset metadata' && <p className="lw-match-context">{asset.match_context}</p>}{asset.metadata.tags.length > 0 && <div className="lw-card-tags">{asset.metadata.tags.slice(0, 3).map(tag => <span key={tag}>{tag}</span>)}{asset.metadata.tags.length > 3 && <span>+{asset.metadata.tags.length - 3}</span>}</div>}</div>
    <div className="lw-list-review"><span className={`lw-review-dot ${asset.metadata.review_status}`} />{reviewLabel(asset.metadata.review_status)}</div><Icon name="chevron" className="lw-list-chevron" size={16} />
  </button></div>
}
function ShotCard({ shot, selected, onClick }: { shot: LibraryShot; selected: boolean; onClick: () => void }) {
  return <button className={`lw-asset-card lw-shot-card ${selected ? 'is-selected' : ''}`} onClick={onClick} aria-pressed={selected}><div className="lw-card-image">{shot.thumbnail_url ? <img src={shot.thumbnail_url} alt="" loading="lazy" /> : <div className="lw-card-placeholder"><Icon name="film" size={32} /><span>SHOT {String(shot.shot_number).padStart(2, '0')}</span></div>}<span className="lw-card-play"><Icon name="play" size={17} /></span><span className="lw-card-duration">{shot.start_time} – {shot.end_time}</span><span className="lw-card-status">SHOT {String(shot.shot_number).padStart(2, '0')}</span></div><div className="lw-card-content"><div className="lw-card-title"><h3>{shot.scene_title || `Shot ${shot.shot_number}`}</h3><span className={`lw-review-dot ${shot.review_status}`} /></div><div className="lw-card-subtitle"><span title={shot.filename}>{shot.filename}</span></div><p className="lw-shot-description">{shot.visual_description}</p>{shot.match_context && shot.match_context !== shot.visual_description && <p className="lw-match-context">{shot.match_context}</p>}<div className="lw-card-tags">{[shot.shot_type, shot.mood, ...shot.tags].filter(Boolean).slice(0, 3).map((tag, index) => <span key={`${tag}-${index}`}>{tag}</span>)}</div></div><Icon name="chevron" className="lw-list-chevron" size={16} /></button>
}
function EmptyState({ hasLibrary, filtered, shots, importing, onImport, onClear, onVideos }: { hasLibrary: boolean; filtered: boolean; shots: boolean; importing: boolean; onImport: () => void; onClear: () => void; onVideos: () => void }) {
  if (hasLibrary || filtered) return <div className="lw-empty-state"><h2>{shots && !filtered ? 'No analyzed shots yet' : 'No matches'}</h2>{shots && !filtered ? <><p>Open a project to analyze its shots.</p><button className="lw-button" onClick={onVideos}>Browse projects</button></> : filtered && <button className="lw-button" onClick={onClear}>Clear filters</button>}</div>
  return <button className="lw-empty-import" onClick={onImport} disabled={importing}><Icon name="upload" size={24} /><span>Drop videos here</span><small>or choose files</small></button>
}
function SettingsDialog({ capabilities, onClose, onOpenAnalyzer }: { capabilities: Capabilities | null; onClose: () => void; onOpenAnalyzer: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const apiKey = useSettingsStore(state => state.apiKey)
  const setApiKey = useSettingsStore(state => state.setApiKey)
  const [key, setKey] = useState(apiKey)
  const desktopSession = Boolean(getDesktopToken())
  const [desktopConfigured, setDesktopConfigured] = useState(Boolean(capabilities?.google_configured))
  const [desktopReady, setDesktopReady] = useState(false)
  const [checkingDesktop, setCheckingDesktop] = useState(desktopSession)
  const [saving, setSaving] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [copying, setCopying] = useState(false)
  const configured = desktopSession ? desktopConfigured : Boolean(capabilities?.google_configured || apiKey)
  useEffect(() => { dialog.current?.showModal() }, [])
  useEffect(() => {
    if (!desktopSession) return
    const controller = new AbortController()
    getDesktopHealth(controller.signal).then(result => {
      if (controller.signal.aborted) return
      setDesktopConfigured(result.google_configured)
      setDesktopReady(true)
      setSettingsError('')
    }).catch(error => {
      if (!controller.signal.aborted) setSettingsError(error instanceof Error ? error.message : 'The companion app is unavailable.')
    }).finally(() => { if (!controller.signal.aborted) setCheckingDesktop(false) })
    return () => controller.abort()
  }, [desktopSession])

  async function saveKey() {
    if (saving) return
    const trimmed = key.trim()
    if (!desktopSession || !trimmed) {
      // An empty desktop form keeps the companion key and removes any browser override.
      setApiKey(desktopSession ? '' : trimmed)
      onClose()
      return
    }
    setSaving(true); setSettingsError(''); setFeedback('')
    try {
      const response = await fetch('/api/validate-key', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: trimmed }),
      })
      const validation = await response.json().catch(() => { throw new Error('Could not read API key validation. Try again.') })
      if (!response.ok || validation.valid !== true) throw new Error('Google could not validate this API key. Check it and try again.')
      const saved = await saveDesktopGoogleApiKey(trimmed)
      setDesktopConfigured(saved.google_configured)
      setApiKey(''); setKey('')
      onClose()
    } catch (error) {
      setSettingsError(error instanceof Error && !/fetch|json/i.test(error.message) ? error.message : 'Could not validate or save the key. Check the companion app and try again.')
    } finally { setSaving(false) }
  }

  async function clearKey() {
    if (saving) return
    setSaving(true); setSettingsError(''); setFeedback('')
    try {
      const saved = await saveDesktopGoogleApiKey('')
      setDesktopConfigured(saved.google_configured)
      setApiKey(''); setKey(''); setFeedback('Gemini key cleared.')
    } catch (error) { setSettingsError(error instanceof Error ? error.message : 'Could not clear the key.') }
    finally { setSaving(false) }
  }

  async function copyPairingCode() {
    setCopying(true); setFeedback(''); setSettingsError('')
    try { await copyEditorPairingCode(); setFeedback('Copied. Paste it into Premiere or Resolve.') }
    catch (error) { setSettingsError(error instanceof Error ? error.message : 'Could not copy the pairing code.') }
    finally { setCopying(false) }
  }

  return <dialog ref={dialog} className="lw-settings-dialog" aria-labelledby="settings-title" onCancel={event => { if (saving) event.preventDefault(); else onClose() }} onClose={onClose}>
    <div className="lw-dialog-heading"><h2 id="settings-title">Settings</h2><button className="lw-icon-button" aria-label="Close settings" onClick={onClose} disabled={saving}><Icon name="close" /></button></div>
    <div className={`lw-provider-status ${configured ? 'is-connected' : ''}`}><i /><span>{checkingDesktop ? 'Checking companion app…' : configured ? 'Gemini key configured' : 'Add a Gemini key to analyze video.'}</span></div>
    <form onSubmit={event => { event.preventDefault(); void saveKey() }}>
      <label className="lw-field">Gemini API key<input type="password" autoComplete="off" disabled={saving} value={key} onChange={event => { setKey(event.target.value); setSettingsError(''); setFeedback('') }} placeholder={configured ? 'Enter a new key to replace it' : 'Paste API key'} /><span className="lw-field-hint">{desktopSession ? 'Saved locally by the companion app and used in Premiere and Resolve. Leave blank to keep the configured key.' : 'Saved in this browser. Leave blank to use the server key.'}</span></label>
      {settingsError && <p className="lw-inline-error" role="alert">{settingsError}</p>}
      {feedback && <p className="lw-field-hint" role="status">{feedback}</p>}
      <details className="lw-details"><summary>Models</summary><dl><div><dt>Indexing</dt><dd>{capabilities?.models.analysis || 'Server default'}</dd></div><div><dt>Deep analysis</dt><dd>{capabilities?.models.deep || 'Server default'}</dd></div></dl></details>
      {desktopReady && <button type="button" className="lw-button" onClick={() => void copyPairingCode()} disabled={saving || copying}>{copying ? 'Copying…' : 'Copy editor pairing code'}</button>}
      <div className="lw-dialog-actions">{desktopReady && desktopConfigured && <button type="button" className="lw-button" onClick={() => void clearKey()} disabled={saving}>Clear key</button>}<button type="button" className="lw-button" onClick={onClose} disabled={saving}>Cancel</button><button type="submit" className="lw-button lw-button-primary" disabled={saving || checkingDesktop}>{saving ? 'Saving…' : 'Save'}</button></div>
    </form>
    <button className="lw-text-button lw-analyzer-link" disabled={saving} onClick={() => { onClose(); onOpenAnalyzer() }}>Open analyzer<Icon name="arrow" size={14} /></button>
  </dialog>
}
