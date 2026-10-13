import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { analyzeAsset, getAsset, getCapabilities, getLibrary, getProjects, getShots, importLocalMedia, getFolders, moveToFolder } from '../api/library'
import { queueBatchAnalysis } from '../api/batchAnalysis'
import type { AnalysisMode, BatchAnalysisResult } from '../api/batchAnalysis'
import type { AssetDetail, Capabilities, ImportProgress, LibraryAsset, LibraryResponse, LibraryShot, FolderResponse } from '../api/library'
import { chooseNativeMedia, chooseNativeFolders, discoverNativeMedia, getDroppedFolders, isVideoMedia, nativeMediaAvailable, resolveBrowserDrop, resolveBrowserFolderDrop } from '../api/nativeMedia'
import { copyEditorPairingCode, getDesktopHealth, getDesktopToken, saveDesktopGoogleApiKey } from '../api/desktop'
import { useSettingsStore } from '../stores/settingsStore'
import { CosplayBadges } from './library/CosplayEditor'
import { AssetInspector } from './library/AssetInspector'
import { HoverScrubThumbnail } from './library/HoverScrubThumbnail'
import { Icon } from './library/Icon'
import { OriginalOfflineBadge } from './library/OriginalOfflineBadge'
import { useSourceAvailability } from './library/useSourceAvailability'
import { duration, errorMessage } from './library/format'
import { analysisFacts, analysisStageLabel, isActiveAnalysis } from './library/analysisProgress'
import { DeleteProjectsDialog } from './library/DeleteProjectsDialog'
import { MediaExportDialog } from './library/MediaExportDialog'
import type { DeletionProject } from './library/DeleteProjectsDialog'
import { LibraryFolders, FolderMovePicker, SHOT_DRAG_TYPE } from './library/LibraryFolders'
import './LibraryWorkspace.css'

const METADATA_FILTERS = [
  { key: 'character', label: 'Character', all: 'All characters' },
  { key: 'franchise', label: 'Franchise', all: 'All franchises' },
  { key: 'production', label: 'Production', all: 'All productions' },
  { key: 'scene', label: 'Scene', all: 'All scenes' },
  { key: 'take', label: 'Take', all: 'All takes' },
  { key: 'camera_roll', label: 'Camera roll', all: 'All rolls' },
  { key: 'cinematographer', label: 'Cinematographer', all: 'All cinematographers' },
] as const

export function LibraryWorkspace() {
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [metadataFilters, setMetadataFilters] = useState({ character: '', franchise: '', identification: '', production: '', scene: '', take: '', camera_roll: '', cinematographer: '' })
  const [tag, setTag] = useState('')
  const [folderId, setFolderId] = useState('')
  const [folders, setFolders] = useState<FolderResponse | null>(null)
  const [folderError, setFolderError] = useState('')
  const [moving, setMoving] = useState(false)
  const moveLock = useRef(false)
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
  const [batchMode, setBatchMode] = useState<AnalysisMode>('cosplay')
  const [queueing, setQueueing] = useState(false)
  const [selectingAll, setSelectingAll] = useState(false)
  const [batchResults, setBatchResults] = useState<BatchAnalysisResult[]>([])
  const batchLock = useRef(false)
  const [deletingProjects, setDeletingProjects] = useState<DeletionProject[] | null>(null)
  const [exportingMedia, setExportingMedia] = useState<DeletionProject[] | null>(null)
  const [detail, setDetail] = useState<AssetDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [settings, setSettings] = useState(false)
  const [upload, setUpload] = useState<{ filename: string; current: number; total: number; progress?: ImportProgress } | null>(null)
  const [uploadNotice, setUploadNotice] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [dragging, setDragging] = useState(false)
  const uploadLock = useRef(false)
  const importMenu = useRef<HTMLDetailsElement>(null)
  const dragDepth = useRef(0)
  const searchInput = useRef<HTMLInputElement>(null)
  const workspace = useRef<HTMLDivElement>(null)
  const libraryMain = useRef<HTMLElement>(null)
  const libraryReturnFocus = useRef<HTMLElement | null>(null)
  const libraryScrollTop = useRef(0)
  const libraryWindowScroll = useRef({ x: 0, y: 0 })
  const wasSelected = useRef(false)
  const refreshLibrary = useCallback(() => setRefresh(value => value + 1), [])
  const pageContext = JSON.stringify([query, metadataFilters, tag, folderId, searchMode, sort, searchMode === 'shots' ? shotProjectId : ''])
  const currentLibraryContext = useRef(pageContext)
  useEffect(() => { currentLibraryContext.current = pageContext }, [pageContext])
  const page = pagination.context === pageContext ? pagination.page : 0
  const pageSize = 60
  const selectedId = selection?.id
  const detailReady = !!selectedId && detail?.job_id === selectedId

  function openVideo(next: NonNullable<typeof selection>) {
    if (!selection) {
      const activeElement = document.activeElement
      libraryReturnFocus.current = activeElement instanceof HTMLElement && libraryMain.current?.contains(activeElement) ? activeElement : searchInput.current
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
    getProjects(controller.signal, folderId).then(result => {
      if (controller.signal.aborted) return
      setProjects(result.projects); setProjectsError('')
      setShotProjectId(current => result.projects.some(item => item.id === current) ? current : '')
    }).catch(err => { if (!controller.signal.aborted) setProjectsError(errorMessage(err)) })
    return () => controller.abort()
  }, [refresh, folderId])

  useEffect(() => {
    const controller = new AbortController()
    getFolders(controller.signal).then(result => {
      if (!controller.signal.aborted) { setFolders(result); setFolderError('') }
    }).catch(err => { if (!controller.signal.aborted) setFolderError(errorMessage(err)) })
    return () => controller.abort()
  }, [refresh])

  function selectFolder(id: string) {
    setFolderId(id); setProjects(null); setProjectsError(''); setShotProjectId(''); setShotResults([])
  }

  async function moveShots(ids: string[], target: string | null) {
    if (moveLock.current) throw new Error('A move is already in progress.')
    moveLock.current = true; setMoving(true); setFolderError('')
    try {
      const result = await moveToFolder(ids, target)
      setCheckedProjects(previous => previous.filter(item => !ids.includes(item.id)))
      setUploadNotice(`${result.moved} media ${result.moved === 1 ? 'item' : 'items'} moved to ${folders?.folders.find(folder => folder.id === target)?.name || 'Unfiled'}.`)
      refreshLibrary()
    } catch (err) { setFolderError(errorMessage(err)); throw err }
    finally { moveLock.current = false; setMoving(false) }
  }

  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true); setError('')
      try {
        const [result, shots] = await Promise.all([
          getLibrary({ q: searchMode === 'assets' ? query : '', ...metadataFilters, tag: searchMode === 'assets' ? tag : '', folder_id: folderId, limit: pageSize, offset: searchMode === 'assets' ? page * pageSize : 0, sort: sort === 'default' ? (query ? 'relevance' : 'newest') : sort }, controller.signal),
          searchMode === 'shots' ? getShots({ q: query, job_id: shotProjectId || undefined, ...metadataFilters, tag, folder_id: folderId, limit: pageSize, offset: page * pageSize }, controller.signal) : Promise.resolve(null),
        ])
        if (controller.signal.aborted) return
        setLibrary(result); setShotResults(shots?.shots.filter(shot => !shotProjectId || shot.job_id === shotProjectId) || []); setShotTotal(shots?.total || 0); setShotResultProject(shotProjectId)
        const resultTotal = searchMode === 'shots' ? shots?.total || 0 : result.total
        if (page > 0 && resultTotal <= page * pageSize) setPagination({ context: pageContext, page: Math.max(0, Math.ceil(resultTotal / pageSize) - 1) })
      } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)) }
      finally { if (!controller.signal.aborted) setLoading(false) }
    }, query ? 280 : 0)
    return () => { clearTimeout(timer); controller.abort() }
  }, [query, metadataFilters, tag, folderId, searchMode, shotProjectId, sort, page, pageContext, refresh])

  useEffect(() => {
    if (!selectedId) return
    const controller = new AbortController()
    getAsset(selectedId, controller.signal).then(result => {
      if (!controller.signal.aborted) { setDetail(result); setDetailError('') }
    }).catch(err => { if (!controller.signal.aborted) setDetailError(errorMessage(err)) })
    return () => controller.abort()
  }, [selectedId, refresh])

  const analyzing = (library?.active_jobs || 0) > 0 || library?.assets.some(asset => isActiveAnalysis(asset.status)) || (detail?.job_id === selectedId && isActiveAnalysis(detail?.status))
  const importing = !!upload
  useEffect(() => {
    if (!analyzing && !importing) return
    const interval = setInterval(refreshLibrary, 4000)
    return () => clearInterval(interval)
  }, [analyzing, importing, refreshLibrary])

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (!selectedId && (event.metaKey || event.ctrlKey) && event.key === 'k') { event.preventDefault(); searchInput.current?.focus() }
      if (event.key === 'Escape' && importMenu.current?.open) { importMenu.current.open = false; importMenu.current.querySelector('summary')?.focus() }
    }
    function outside(event: PointerEvent) {
      if (importMenu.current && !importMenu.current.contains(event.target as Node)) importMenu.current.open = false
    }
    window.addEventListener('keydown', keydown)
    window.addEventListener('pointerdown', outside)
    return () => { window.removeEventListener('keydown', keydown); window.removeEventListener('pointerdown', outside) }
  }, [selectedId])

  async function importPaths(paths: string[]) {
    if (!paths.length || uploadLock.current) return
    uploadLock.current = true; setUploadNotice(''); setUploadError('')
    let imported = 0
    const errors: string[] = []
    try {
      setUpload({ filename: paths.length === 1 ? paths[0].split(/[/\\]/).pop() || paths[0] : 'Folders and media', current: 0, total: 0,
        progress: { stage: 'scanning', message: 'Finding video files in subfolders…' } })
      const discovered = await discoverNativeMedia([...new Set(paths)])
      const incoming = discovered.paths
      errors.push(...discovered.warnings)
      if (errors.length) setUploadError(errors.join('\n'))
      if (!incoming.length) setUploadNotice('No video files found in this selection.')
      for (let index = 0; index < incoming.length; index++) {
        const path = incoming[index]
        const filename = path.split(/[/\\]/).pop() || path
        setUpload({ filename, current: index + 1, total: incoming.length })
        try {
          const result = await importLocalMedia(path, '', false, progress => {
            setUpload(previous => previous ? { ...previous, progress } : previous)
          })
          if (folderId && folderId !== 'unfiled') {
            try { await moveToFolder([result.job_id], folderId); result.folder_id = folderId }
            catch (err) { errors.push(`${filename} imported into Unfiled: ${errorMessage(err)}`) }
          }
          // Publish the finished clip before starting the next proxy. Keep a
          // user-changed search/filter view intact; polling refreshes that view.
          if (searchMode === 'assets' && !hasFilters && page === 0 && currentLibraryContext.current === pageContext && (!folderId || result.folder_id === (folderId === 'unfiled' ? null : folderId))) {
            setLibrary(current => {
              if (!current) return current
              const ready = [result, ...current.assets.filter(asset => asset.job_id !== result.job_id)]
              if (sort === 'name') ready.sort((a, b) => a.metadata.title.localeCompare(b.metadata.title))
              else ready.sort((a, b) => b.created_at.localeCompare(a.created_at))
              return { ...current, assets: ready.slice(0, pageSize), total: Math.max(current.total, ready.length) }
            })
          }
          imported++; refreshLibrary()
          setUploadNotice(`${imported} of ${incoming.length} media ${incoming.length === 1 ? 'item' : 'items'} ready.`)
          setShotProjectId(result.job_id)
          if (incoming.length === 1) openVideo({ id: result.job_id })
        } catch (err) {
          errors.push(`${filename}: ${errorMessage(err)}`)
          setUploadError(errors.join('\n'))
          refreshLibrary()
        }
      }
      if (imported) setUploadNotice(`${imported} media ${imported === 1 ? 'item' : 'items'} imported.`)
      if (errors.length) setUploadError(errors.join('\n'))
    } catch (err) { setUploadError(errorMessage(err))
    } finally { setUpload(null); uploadLock.current = false; refreshLibrary() }
  }

  const receiveNativeFiles = useEffectEvent((paths: string[]) => { void importPaths(paths) })
  useEffect(() => {
    function receive(event: Event) {
      const paths: unknown = (event as CustomEvent).detail
      if (Array.isArray(paths) && paths.every(path => typeof path === 'string')) receiveNativeFiles(paths)
    }
    window.addEventListener('video-analyzer-files', receive)
    return () => window.removeEventListener('video-analyzer-files', receive)
  }, [])

  async function selectMedia(folders = false) {
    if (uploadLock.current) return
    if (importMenu.current) importMenu.current.open = false
    setUploadError('')
    try {
      await importPaths(await (folders ? chooseNativeFolders() : chooseNativeMedia()))
    } catch (err) { setUploadError(errorMessage(err)) }
  }

  async function browserDrop(files: File[], folders: FileSystemDirectoryEntry[]) {
    if (nativeMediaAvailable()) return // The native drop handler supplies the original paths.
    if (uploadLock.current) return
    uploadLock.current = true; setUploadError(''); setUploadNotice('')
    const paths: string[] = []
    try {
      let unresolvedFolders = false
      for (const folder of folders) {
        setUpload({ filename: folder.name, current: 0, total: 0, progress: { stage: 'locating', message: 'Locating original folder…' } })
        const path = await resolveBrowserFolderDrop(folder)
        if (path) paths.push(path)
        else unresolvedFolders = true
      }
      if (unresolvedFolders) {
        setUploadNotice('Choose the remaining dropped folders in the folder picker. Video subfolders will be included; originals stay in place.')
        setUpload({ filename: 'Video folders', current: 0, total: 0, progress: { stage: 'choosing', message: 'Choose original folders in the folder picker' } })
        paths.push(...await chooseNativeFolders())
      }
      for (const [index, file] of files.entries()) {
        if (!isVideoMedia(file.name)) continue
        setUpload({ filename: file.name, current: index + 1, total: files.length,
          progress: { stage: 'locating', message: 'Locating original in known camera folders…' } })
        const path = await resolveBrowserDrop(file)
        if (path) paths.push(path)
        else {
          // A normal browser hides absolute paths. Ask the OS once rather than
          // opening another app window and requiring a second drop.
          setUploadNotice('This browser hides the original path. Choose the dropped clips in the file picker; originals stay in place.')
          setUpload(previous => previous ? { ...previous, progress: { stage: 'choosing', message: 'Choose original media in the file picker' } } : previous)
          paths.push(...await chooseNativeMedia())
          break
        }
      }
    } catch (err) { setUploadError(errorMessage(err)) }
    finally { setUpload(null); uploadLock.current = false }
    if (paths.length) await importPaths(paths)
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
    setUploadNotice(`${ids.length} media ${ids.length === 1 ? 'item' : 'items'} deleted.${notes.length ? ` ${notes.join(' ')}` : ''}`)
    refreshLibrary()
  }

  async function analyzeSelected() {
    if (batchLock.current || selectingAll || !checkedProjects.length) return
    batchLock.current = true; setQueueing(true); setBatchResults([])
    const ids = checkedProjects.map(item => item.id)
    try {
      await queueBatchAnalysis(ids, batchMode, (id, mode) => analyzeAsset(id, mode, 1, '', 'auto'), result => {
        setBatchResults(previous => [...previous, result])
        if (result.accepted) {
          setCheckedProjects(previous => previous.filter(item => item.id !== result.id))
          refreshLibrary()
        }
      })
    } finally { batchLock.current = false; setQueueing(false); refreshLibrary() }
  }

  async function selectAllMatching() {
    if (selectingAll || queueing) return
    const context = pageContext
    setSelectingAll(true); setError('')
    try {
      const selected: DeletionProject[] = []
      for (let offset = 0; ; offset += 500) {
        const result = await getLibrary({ q: query, ...metadataFilters, tag, folder_id: folderId, sort: 'name', limit: 500, offset })
        if (currentLibraryContext.current !== context) return
        selected.push(...result.assets.filter(asset => !isActiveAnalysis(asset.status) && asset.status !== 'deleting').map(asset => ({ id: asset.job_id, title: asset.metadata.title || asset.filename })))
        if (offset + result.assets.length >= result.total || !result.assets.length) break
      }
      if (currentLibraryContext.current === context) setCheckedProjects([...new Map(selected.map(item => [item.id, item])).values()])
    } catch (err) { setError(errorMessage(err)) }
    finally { setSelectingAll(false) }
  }
  function clearFilters() { setQuery(''); setMetadataFilters({ character: '', franchise: '', identification: '', production: '', scene: '', take: '', camera_roll: '', cinematographer: '' }); setTag('') }
  const assets = library?.assets || []
  const selectableProjects = assets.filter(asset => !isActiveAnalysis(asset.status) && asset.status !== 'deleting')
  const allPageSelected = selectableProjects.length > 0 && selectableProjects.every(asset => checkedProjects.some(item => item.id === asset.job_id))
  const shots = shotResultProject === shotProjectId ? shotResults : []
  const sourceStatus = useSourceAvailability(searchMode === 'assets' ? assets.map(asset => asset.job_id) : shots.map(shot => shot.job_id), !selectedId)
  const hasFilters = !!(query || tag || Object.values(metadataFilters).some(Boolean))
  const count = searchMode === 'shots' ? (shotResultProject === shotProjectId ? shotTotal : 0) : library?.total || 0
  const projectLoading = searchMode === 'shots' && !projects && !projectsError
  const shotScopeLoading = searchMode === 'shots' && !!shotProjectId && shotResultProject !== shotProjectId
  const projectError = searchMode === 'shots' ? projectsError : ''
  const displayError = error || (!projects ? projectError : '')
  const hasActiveFilters = !!(tag || Object.values(metadataFilters).some(Boolean))
  return <div ref={workspace} className={`lw-workspace ${selection ? 'has-video-workspace' : ''}`}>
    <main ref={libraryMain} hidden={!!selection} className="lw-main" onDragEnter={event => { event.preventDefault(); if (event.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true) } }} onDragOver={event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault() }} onDragLeave={event => { event.preventDefault(); dragDepth.current--; if (dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false) } }} onDrop={event => {
      event.preventDefault(); dragDepth.current = 0; setDragging(false)
      const folders = getDroppedFolders(event.dataTransfer)
      const files = Array.from(event.dataTransfer.items).filter(item => !item.webkitGetAsEntry?.()?.isDirectory).map(item => item.getAsFile()).filter((file): file is File => file !== null)
      if (files.length || folders.length) void browserDrop(files, folders)
    }}>
      <h1 className="lw-sr-only">Footage library</h1>
      <div className="lw-library-layout">
      <LibraryFolders data={folders} activeId={folderId} onSelect={selectFolder} onRefresh={refreshLibrary} onMove={moveShots} error={folderError} />
      <div className="lw-library-content">
        <header className="lw-toolbar">
          <div className="lw-search"><Icon name="search" size={19} /><input ref={searchInput} value={query} onChange={event => setQuery(event.target.value)} placeholder={searchMode === 'shots' ? 'Search clips' : 'Search media'} aria-label="Search footage" />{query && <button className="lw-icon-button" aria-label="Clear search" onClick={() => setQuery('')}><Icon name="close" size={15} /></button>}</div>
          <div className="lw-toolbar-actions">
            <div className="lw-segmented" aria-label="Search result type"><button className={searchMode === 'assets' ? 'is-active' : ''} aria-pressed={searchMode === 'assets'} onClick={() => setSearchMode('assets')}>Media</button><button className={searchMode === 'shots' ? 'is-active' : ''} aria-pressed={searchMode === 'shots'} onClick={() => setSearchMode('shots')}>Clips</button></div>
            <button className={`lw-button lw-filter-toggle ${hasActiveFilters ? 'has-filters' : ''}`} aria-expanded={filtersOpen} aria-controls="library-filters" onClick={() => setFiltersOpen(open => !open)}>Filters{hasActiveFilters && <span className="lw-filter-dot" aria-label="Filters active" />}</button>
            <div className="lw-import-controls">
              <button className="lw-button lw-button-primary" onClick={() => void selectMedia()} disabled={!!upload}><Icon name="plus" size={17} />{upload ? 'Importing…' : 'Import'}</button>
              <details ref={importMenu} className="lw-import-options" onToggle={event => { if (upload) event.currentTarget.open = false }}>
                <summary aria-label="Import options" title="Import files or folders" aria-disabled={!!upload}><Icon name="down" size={15} /></summary>
                <div className="lw-import-menu"><button className="lw-button" disabled={!!upload} onClick={() => void selectMedia()}><Icon name="film" size={16} />Import files</button><button className="lw-button" disabled={!!upload} onClick={() => void selectMedia(true)}><Icon name="folder" size={16} />Import folders</button></div>
              </details>
            </div>
            <button className="lw-icon-button" aria-label="Settings" title="Settings" onClick={() => setSettings(true)}><Icon name="settings" size={18} /></button>
          </div>
        </header>
        <div className="lf-scope-heading"><h2>{folderId === 'unfiled' ? 'Unfiled' : folders?.folders.find(folder => folder.id === folderId)?.name || 'All media'}</h2><span>{searchMode === 'assets' ? `${library?.total || 0} media ${library?.total === 1 ? 'item' : 'items'}` : shotProjectId ? 'Clips from the selected media' : 'Clips across this library folder'}</span></div>
        {searchMode === 'assets' && (assets.length > 0 || checkedProjects.length > 0) && <div className="lw-project-selection" role="group" aria-label="Media selection">
          <label><input type="checkbox" aria-label="Select media on this page" checked={allPageSelected} disabled={queueing || moving || loading || !selectableProjects.length} onChange={event => setCheckedProjects(previous => event.target.checked ? [...previous, ...selectableProjects.filter(asset => !previous.some(item => item.id === asset.job_id)).map(asset => ({ id: asset.job_id, title: asset.metadata.title || asset.filename }))] : previous.filter(item => !selectableProjects.some(asset => asset.job_id === item.id)))} />Select page</label>
          {(library?.total || 0) > pageSize && <button className="lw-text-button" disabled={selectingAll || queueing || moving} onClick={() => void selectAllMatching()}>{selectingAll ? 'Selecting…' : `Select all ${library?.total} matches`}</button>}
          {(!!checkedProjects.length || queueing) && <><span>{checkedProjects.length} selected</span><button className="lw-text-button" disabled={queueing || moving} onClick={() => setCheckedProjects([])}>Clear selection</button><FolderMovePicker folders={folders?.folders || []} ids={checkedProjects.map(item => item.id)} disabled={queueing || moving} onMove={moveShots} /><div className="lw-batch-analysis"><select aria-label="Selected media analysis type" disabled={queueing} value={batchMode} onChange={event => setBatchMode(event.target.value as AnalysisMode)}><option value="flash_pro">Full analysis</option><option value="flash_only">Clips and tags</option><option value="cosplay">Cosplay</option></select><button className="lw-button lw-button-primary" disabled={selectingAll || queueing || moving || !checkedProjects.length} onClick={() => void analyzeSelected()}><Icon name="spark" size={16} />{queueing ? 'Queueing…' : 'Analyze selected'}</button></div><button className="lw-button" disabled={selectingAll || queueing || moving || checkedProjects.length > 1000} onClick={() => setExportingMedia([...checkedProjects])}><Icon name="download" size={16} />Export selected</button><button className="lw-button lw-button-danger" disabled={queueing || moving} onClick={() => setDeletingProjects(checkedProjects)}><Icon name="trash" size={15} />Delete selected</button></>}
        </div>}
        {!!batchResults.length && <div className="lw-batch-notice"><div role="status">{batchResults.filter(result => result.accepted).length} media items queued{queueing ? '…' : '.'}</div>{batchResults.filter(result => !result.accepted).map(result => <p className="lw-inline-error" role="alert" key={result.id}>{projects?.find(item => item.id === result.id)?.title || result.id}: {result.error}</p>)}{!queueing && <button className="lw-text-button" onClick={() => setBatchResults([])}>Dismiss</button>}</div>}
        {searchMode === 'shots' && <div className="lw-project-scope">
          <label><span>Source media</span><select aria-label="Source media" required value={shotProjectId} disabled={!projects?.length} onChange={event => setShotProjectId(event.target.value)}>
            <option value="">{projectLoading ? 'Loading media…' : 'All media in this folder'}</option>
            {projects?.map(item => <option value={item.id} key={item.id}>{item.title || item.filename}</option>)}
          </select></label>
          {shotProjectId && <button className="lw-text-button" onClick={() => openVideo({ id: shotProjectId })}>Open media<Icon name="arrow" size={14} /></button>}
          {projectError && projects && <div className="lw-inline-error" role="alert">{projectError}<button className="lw-text-button" onClick={refreshLibrary}>Try again</button></div>}
        </div>}
        {filtersOpen && <div id="library-filters" className="lw-filter-panel">
          <div className="lw-filters">
            {METADATA_FILTERS.map(({ key, label, all }) => <label key={key}><span>{label}</span><select aria-label={`Filter by ${label.toLowerCase()}`} value={metadataFilters[key]} disabled={!library?.facets[key]?.length && !metadataFilters[key]} onChange={event => setMetadataFilters(previous => ({ ...previous, [key]: event.target.value }))}><option value="">{library?.facets[key]?.length ? all : 'No metadata yet'}</option>{metadataFilters[key] && !library?.facets[key]?.includes(metadataFilters[key]) && <option>{metadataFilters[key]}</option>}{library?.facets[key]?.map(value => <option key={value}>{value}</option>)}</select></label>)}
            <label><span>Identification</span><select aria-label="Filter by identification" value={metadataFilters.identification} onChange={event => setMetadataFilters(previous => ({ ...previous, identification: event.target.value }))}><option value="">All identifications</option><option value="suggested">Suggested</option><option value="confirmed">Confirmed</option><option value="unknown">Unknown costumes</option></select></label>
            <label><span>Tag</span><select aria-label="Filter by tag" value={tag} onChange={event => setTag(event.target.value)}><option value="">All tags</option>{library?.facets.tags.map(name => <option key={name}>{name}</option>)}</select></label>
            {searchMode === 'assets' && <label><span>Sort</span><select aria-label="Sort footage" value={sort === 'newest' && !query ? 'default' : sort} onChange={event => setSort(event.target.value)}><option value="default">{query ? 'Relevance' : 'Newest first'}</option><option value="name">Name A–Z</option>{query && <option value="newest">Newest first</option>}</select></label>}
          </div>
          <div className="lw-filter-panel-actions">{hasFilters && <button className="lw-text-button" onClick={clearFilters}>Clear filters</button>}<div className="lw-view-toggle"><button className={layout === 'grid' ? 'is-active' : ''} aria-label="Grid view" aria-pressed={layout === 'grid'} onClick={() => setLayout('grid')}><Icon name="grid" size={16} /></button><button className={layout === 'list' ? 'is-active' : ''} aria-label="List view" aria-pressed={layout === 'list'} onClick={() => setLayout('list')}><Icon name="list" size={17} /></button></div></div>
        </div>}
        {upload && <div className="lw-upload-progress" role="status"><Icon name="film" size={17} /><div><strong>{upload.filename}{upload.total > 0 ? ` · ${upload.current}/${upload.total}` : ''}</strong><span>{upload.progress?.message || 'Checking original file…'}{upload.progress?.percent !== undefined ? ` · ${upload.progress.percent}%` : ''}{(upload.progress?.elapsed_seconds || 0) >= 5 ? ` · ${upload.progress?.elapsed_seconds}s elapsed` : ''}</span><progress className={upload.progress?.percent === undefined ? 'is-indeterminate' : ''} aria-label="Import progress" max={100} value={upload.progress?.percent} /></div></div>}
        {uploadNotice && <div className="lw-notice" role="status"><Icon name="check" size={17} /><span>{uploadNotice}</span><button className="lw-icon-button" aria-label="Dismiss notification" onClick={() => setUploadNotice('')}><Icon name="close" size={15} /></button></div>}
        {uploadError && <div className="lw-inline-error" role="alert">{uploadError}<button className="lw-text-button" onClick={() => setUploadError('')}>Dismiss</button></div>}
        {(loading || projectLoading || shotScopeLoading) && library && <span className="lw-loading-status" role="status">Loading…</span>}
        {displayError ? <div className="lw-empty-state lw-error-state"><h2>Couldn’t load {projectError && !projects ? 'media' : 'footage'}</h2><p>{displayError}</p><button className="lw-button" onClick={refreshLibrary}>Try again</button></div> : (loading && !library) || projectLoading || shotScopeLoading ? <div className="lw-asset-grid" aria-label="Loading library" aria-busy="true">{Array.from({ length: 6 }, (_, index) => <div className="lw-skeleton" key={index}><div /><span /><span /></div>)}</div> : !count ? <EmptyState hasLibrary={!!library?.stats.assets} filtered={hasFilters} shots={searchMode === 'shots'} folder={!!folderId} onImport={() => void selectMedia()} importing={!!upload} onClear={clearFilters} onVideos={() => setSearchMode('assets')} /> : <div className={`${layout === 'grid' ? 'lw-asset-grid' : 'lw-asset-list'} ${loading ? 'is-updating' : ''}`}>{searchMode === 'assets' ? assets.map(asset => <AssetCard key={asset.job_id} locked={queueing || moving} dragIds={checkedProjects.some(item => item.id === asset.job_id) ? checkedProjects.map(item => item.id) : [asset.job_id]} asset={asset} originalOffline={sourceStatus[asset.job_id] === true} selected={selection?.id === asset.job_id} onClick={() => openVideo({ id: asset.job_id })} checked={checkedProjects.some(item => item.id === asset.job_id)} onToggle={() => toggleProject(asset)} onDelete={() => setDeletingProjects([{ id: asset.job_id, title: asset.metadata.title || asset.filename }])} />) : shots.map(shot => <ShotCard key={`${shot.job_id}-${shot.shot_number}`} shot={shot} originalOffline={sourceStatus[shot.job_id] === true} selected={selection?.id === shot.job_id && selection?.shot === shot.shot_number} onClick={() => openVideo({ id: shot.job_id, seconds: shot.start_seconds, shot: shot.shot_number })} />)}</div>}
        {!displayError && count > pageSize && <div className="lw-pagination"><span>{(page * pageSize + 1).toLocaleString()}–{Math.min((page + 1) * pageSize, count).toLocaleString()} of {count.toLocaleString()}</span><div><button className="lw-button" disabled={page === 0 || loading} onClick={() => setPagination({ context: pageContext, page: page - 1 })}>Previous</button><button className="lw-button" disabled={(page + 1) * pageSize >= count || loading} onClick={() => setPagination({ context: pageContext, page: page + 1 })}>Next</button></div></div>}
      </div></div>
      {dragging && <div className="lw-drop-overlay"><Icon name="film" size={32} /><h2>Drop videos or folders here</h2></div>}
    </main>
    {selection && (detail?.job_id === selection.id ? <AssetInspector key={`${selection.id}-${selection.shot || 'asset'}`} asset={detail} initialSeconds={selection.seconds} initialShot={selection.shot} capabilities={capabilities} onClose={() => setSelection(current => current?.id === selection.id ? null : current)} onRefresh={refreshLibrary} onConfigure={() => setSettings(true)} onRemoved={note => setUploadNotice(`Media removed.${note ? ` ${note}` : ''}`)} /> : <main className="lw-inspector lw-video-workspace lw-video-workspace-loading" aria-label="Video workspace"><button className="lw-button lw-back-to-library" data-video-back onClick={() => setSelection(null)}><Icon name="back" />Back to library</button>{detailError ? <><p role="alert">{detailError}</p><button className="lw-button" onClick={refreshLibrary}>Try again</button></> : <p role="status">Opening media…</p>}</main>)}
    {deletingProjects && <DeleteProjectsDialog projects={deletingProjects} onClose={() => setDeletingProjects(null)} onDeleted={projectsDeleted} />}
    {exportingMedia && <MediaExportDialog media={exportingMedia} onClose={() => setExportingMedia(null)} />}
    {settings && <SettingsDialog capabilities={capabilities} onClose={() => setSettings(false)} />}
  </div>
}

function AssetCard({ asset, selected, onClick, checked, onToggle, onDelete, locked, dragIds, originalOffline }: { originalOffline: boolean; dragIds: string[]; locked: boolean; asset: LibraryAsset; selected: boolean; onClick: () => void; checked: boolean; onToggle: () => void; onDelete: () => void }) {
  const isAnalyzing = ['queued', 'analyzing', 'processing'].includes(asset.status)
  const busy = locked || isActiveAnalysis(asset.status) || asset.status === 'deleting'
  const analyzed = asset.status === 'complete'
  const title = asset.metadata.title || asset.filename
  const facts = analysisFacts(asset.analysis_progress, asset.shot_count)
  return <div draggable={!locked && asset.status !== 'deleting'} onDragStart={event => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData(SHOT_DRAG_TYPE, JSON.stringify(dragIds)) }} className={`lw-project-card ${checked ? 'is-checked' : ''}`}>
    <div className="lw-project-card-actions"><input type="checkbox" aria-label={`Select media ${title}`} checked={checked} disabled={busy} title={busy ? 'This media is busy.' : 'Select media'} onChange={onToggle} /><button className="lw-icon-button" aria-label={`Delete media ${title}`} title={busy ? 'Finish or cancel analysis before deleting this media.' : 'Delete media'} disabled={busy} onClick={onDelete}><Icon name="trash" size={16} /></button></div>
    <button className={`lw-asset-card ${selected ? 'is-selected' : ''}`} onClick={onClick} aria-pressed={selected}>
    <HoverScrubThumbnail key={asset.preview_url} src={asset.preview_url} poster={asset.thumbnail_url} enabled={asset.status !== 'deleting' && !asset.technical.playback_proxy_required} placeholder={<div className="lw-card-placeholder"><Icon name="film" size={32} /><span>{asset.mime_type?.split('/')[1]?.toUpperCase() || 'VIDEO'}</span></div>}><span className="lw-card-play"><Icon name="play" size={17} /></span><span className="lw-card-duration">{duration(asset.technical.duration_seconds)}</span>{isAnalyzing ? <span className="lw-card-status analyzing"><Icon name="spark" size={11} />{asset.status === 'queued' ? 'Queued' : 'Analyzing'}</span> : analyzed ? null : asset.status === 'error' ? <span className="lw-card-status error">Analysis failed</span> : <span className="lw-card-status pending">{asset.status === 'deleting' ? 'Removing…' : 'Not analyzed'}</span>}</HoverScrubThumbnail>
    <div className="lw-card-content"><div className="lw-card-title"><h3 title={asset.metadata.title || asset.filename}>{asset.metadata.title || asset.filename}</h3><OriginalOfflineBadge offline={originalOffline} /></div><div className="lw-card-subtitle">{asset.metadata.project && <span>{asset.metadata.project}</span>}</div>{asset.match_context && asset.match_context !== 'Matched asset metadata' && <p className="lw-match-context">{asset.match_context}</p>}<CosplayBadges records={asset.cosplay} />{asset.metadata.tags.length > 0 && <div className="lw-card-tags">{asset.metadata.tags.slice(0, 3).map(tag => <span key={tag}>{tag}</span>)}{asset.metadata.tags.length > 3 && <span>+{asset.metadata.tags.length - 3}</span>}</div>}{(isAnalyzing || asset.status === 'complete' || asset.status === 'error') && <div className="lw-card-analysis" aria-label={`Analysis progress for ${title}`}>{isAnalyzing ? <><strong>{analysisStageLabel(asset.analysis_progress?.stage || (asset.status === 'queued' ? 'queued' : undefined))}</strong>{asset.status !== 'queued' && <span>{facts.coverage || facts.summary}</span>}</> : asset.status === 'complete' ? <span>Analyzed · {asset.shot_count} {asset.shot_count === 1 ? 'clip' : 'clips'}</span> : <span>{asset.error || 'Analysis failed. Open this media to retry.'}</span>}</div>}</div>
    <Icon name="chevron" className="lw-list-chevron" size={16} />
  </button></div>
}
function ShotCard({ shot, selected, onClick, originalOffline }: { originalOffline: boolean; shot: LibraryShot; selected: boolean; onClick: () => void }) {
  return <button className={`lw-asset-card lw-shot-card ${selected ? 'is-selected' : ''}`} onClick={onClick} aria-pressed={selected}><HoverScrubThumbnail key={shot.preview_url} src={shot.preview_url} poster={shot.thumbnail_url} start={shot.start_seconds} end={shot.end_seconds} placeholder={<div className="lw-card-placeholder"><Icon name="film" size={32} /><span>CLIP {String(shot.shot_number).padStart(2, '0')}</span></div>}><span className="lw-card-play"><Icon name="play" size={17} /></span><span className="lw-card-duration">{shot.start_time} – {shot.end_time}</span><span className="lw-card-status">CLIP {String(shot.shot_number).padStart(2, '0')}</span></HoverScrubThumbnail><div className="lw-card-content"><div className="lw-card-title"><h3>{shot.scene_title || `Clip ${shot.shot_number}`}</h3><OriginalOfflineBadge offline={originalOffline} /></div><div className="lw-card-subtitle"><span title={shot.filename}>{shot.filename}</span></div><CosplayBadges records={shot.cosplay} /><p className="lw-shot-description">{shot.visual_description}</p>{shot.match_context && shot.match_context !== shot.visual_description && <p className="lw-match-context">{shot.match_context}</p>}<div className="lw-card-tags">{[shot.shot_type, shot.mood, ...shot.tags].filter(Boolean).slice(0, 3).map((tag, index) => <span key={`${tag}-${index}`}>{tag}</span>)}</div></div><Icon name="chevron" className="lw-list-chevron" size={16} /></button>
}
function EmptyState({ hasLibrary, filtered, shots, folder, importing, onImport, onClear, onVideos }: { hasLibrary: boolean; filtered: boolean; shots: boolean; folder: boolean; importing: boolean; onImport: () => void; onClear: () => void; onVideos: () => void }) {
  if (folder && !filtered && !shots) return <div className="lw-empty-state"><Icon name="folder" size={28} /><h2>This folder is empty</h2><p>Move media here from All media, or import into this folder.</p><button className="lw-button lw-button-primary" disabled={importing} onClick={onImport}>Import media</button></div>
  if (hasLibrary || filtered) return <div className="lw-empty-state"><h2>{shots && !filtered ? 'No analyzed clips yet' : 'No matches'}</h2>{shots && !filtered ? <><p>Open a media item to analyze its clips.</p><button className="lw-button" onClick={onVideos}>Browse media</button></> : filtered && <button className="lw-button" onClick={onClear}>Clear filters</button>}</div>
  return <button className="lw-empty-import" onClick={onImport} disabled={importing}><Icon name="film" size={24} /><span>Drop videos or folders here</span><small>or choose files</small></button>
}
function SettingsDialog({ capabilities, onClose }: { capabilities: Capabilities | null; onClose: () => void }) {
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
  </dialog>
}
