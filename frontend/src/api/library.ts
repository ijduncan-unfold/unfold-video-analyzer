import { getApiKey } from './apiKey'
import type { FlashAnalysis, SceneDeepAnalysis, VideoSummary, Shot } from './types'

export type ReviewStatus = 'unreviewed' | 'reviewed' | 'needs_changes'
export type ProxyMode = 'auto' | 'always' | 'off'
export type RightsStatus = 'unknown' | 'cleared' | 'restricted'
export interface AssetMetadata {
  title: string
  client: string
  project: string
  campaign: string
  tags: string[]
  notes: string
  rights_status: RightsStatus
  review_status: ReviewStatus
  collections: string[]
}
export interface LibraryAsset {
  folder_id?: string | null
  progress?: { message?: string; pass?: number; scene?: number; total?: number } | string | null
  analysis_progress?: AnalysisProgress | null
  error?: string | null
  job_id: string
  filename: string
  status: string
  created_at: string
  size_bytes: number
  mime_type: string
  media_origin?: 'upload' | 'external'
  uses_viewing_proxy?: boolean
  youtube_url?: string | null
  metadata: AssetMetadata
  technical: {
    duration_seconds?: number | null
    width?: number | null
    height?: number | null
    frame_rate?: number | null
    codec?: string | null
    camera_manufacturer?: string | null
    camera_model?: string | null
    source_timecode?: string | null
    has_audio?: boolean | null
    playback_proxy_required?: boolean
    camera_frame_count?: number
    display_color?: string
    display_revision?: string
    viewing_proxy?: { viewing_transform?: string; width?: number; height?: number; sample_aspect_ratio?: string }
    technical_basis?: string
    camera_metadata?: { manufacturer?: string; camera_model?: string; recording_format?: string; recorder_model?: string }
  }
  shot_count: number
  summary: string
  thumbnail_url: string | null
  preview_url: string | null
  match_context?: string
}
export interface LibraryResponse {
  assets: LibraryAsset[]
  total: number
  active_jobs?: number
  facets: { projects: string[]; tags: string[]; collections?: string[]; production?: string[]; scene?: string[]; take?: string[]; camera_roll?: string[]; cinematographer?: string[] }
  stats: { assets: number; shots: number; reviewed: number; duration_seconds: number }
}
export interface ShotAnnotation {
  tags: string[]
  notes: string
  review_status: ReviewStatus
}
export interface AnalysisProgress {
  stage?: string | null
  upload_part?: number | null
  total_upload_parts?: number | null
  completed_sections?: number | null
  total_sections?: number | null
  failed_sections?: number | null
  completed_shots?: number | null
  processed_seconds?: number | null
  total_seconds?: number | null
  updated_at?: string | null
}
export interface TranscriptSegment {
  start_time?: string
  end_time?: string
  start_seconds?: number
  end_seconds?: number
  start?: string | number
  end?: string | number
  text?: string
  speaker?: string
}
export interface AssetDetail extends LibraryAsset {
  editor_metadata?: { schema_version: string; fields: EditorMetadataField[]; shot_fields: Record<string, EditorMetadataField[]>; conflicts: string[] }
  rough_cut?: RoughCutDraft | null
  flash: FlashAnalysis | null
  deep: SceneDeepAnalysis[] | null
  video_summary: VideoSummary | null
  custom_result: Record<string, unknown> | null
  shot_annotations: Record<string, ShotAnnotation>
  progress?: { message?: string; pass?: number; scene?: number; total?: number } | string | null
  analysis_progress?: AnalysisProgress | null
  error?: string | null
  warnings?: string[]
  analysis_config?: { provider?: string; analysis_model?: string; deep_model?: string; fps?: number; mode?: string; shot_mode?: 'auto' | 'single_take' | 'detect_cuts'; resolved_shot_mode?: string; started_at?: string; timestamp_accuracy?: string; grouping?: string; proxy_mode?: ProxyMode; proxy_used?: boolean; proxy_reason?: string }
  cost_estimate?: { estimated_cost_usd: number | null; pricing_status?: string; pricing_as_of?: string; warnings?: string[]; total_input_tokens?: number; total_output_tokens?: number } | null
  transcript: TranscriptSegment[]
}
export interface EditorMetadataField {
  key: string
  label: string
  value: string
  origin: string
  resolve: string | null
  premiere: string
  xmp: string
}
export interface CutMoment {
  moment_id: string
  start_seconds: number
  end_seconds: number
  window_start: number
  window_end: number
  text: string
  visual: string
  kind: 'usable' | 'false_start' | 'repeated_take' | 'off_topic'
  reason: string
  original_moment_id?: string
  part_index?: number
  part_count?: number
  text_is_context?: boolean
}
export interface RoughCutDraft {
  remove_fillers?: boolean
  filler_spans?: { start_seconds: number; end_seconds: number; start_time: string; end_time: string; text: string }[]
  filler_removal?: { removed_count: number; removed_seconds: number; checked_windows: number; skipped_count: number }
  proxy_mode?: ProxyMode
  proxy_used?: boolean
  proxy_reason?: string
  revision: string
  status: 'queued' | 'analyzing' | 'assembling' | 'ready' | 'error' | 'cancelled'
  mode: 'cleanup' | 'condense'
  target_seconds: number
  source_seconds: number
  duration_seconds?: number
  brief: string
  moments: CutMoment[]
  clips: CutMoment[]
  completed_windows: number
  total_windows: number
  progress: string
  error?: string | null
  rationale?: string
  cost_estimate?: { estimated_cost_usd: number | null }
  warnings: string[]
}
export interface CutClipEdit { moment_id: string; start_seconds: number; end_seconds: number }
export function getRoughCut(id: string, signal?: AbortSignal) {
  return request<RoughCutDraft | null>(`/api/library/${encodeURIComponent(id)}/rough-cut`, { signal })
}
export function generateRoughCut(id: string, mode: RoughCutDraft['mode'], targetSeconds: number, brief: string, proxyMode: ProxyMode = 'auto', removeFillers = false) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey
  return request<RoughCutDraft>(`/api/library/${encodeURIComponent(id)}/rough-cut`, {
    method: 'POST', headers, body: JSON.stringify({ mode, target_seconds: targetSeconds, brief, proxy_mode: proxyMode, remove_fillers: removeFillers }),
  })
}
export function saveRoughCut(id: string, revision: string, clips: CutClipEdit[]) {
  return request<RoughCutDraft>(`/api/library/${encodeURIComponent(id)}/rough-cut`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, clips }),
  })
}
export function cancelRoughCut(id: string) {
  return request<{ status: string }>(`/api/library/${encodeURIComponent(id)}/rough-cut/cancel`, { method: 'POST' })
}
export function roughCutExportUrl(id: string, revision: string, format: 'fcpxml' | 'json') {
  return `/api/library/${encodeURIComponent(id)}/rough-cut/export?${new URLSearchParams({ revision, format })}`
}
export interface LibraryProject {
  id: string
  title: string
  filename: string
  status: string
  shot_count: number
  folder_id?: string | null
  has_local_media?: boolean
}
export interface LibraryShot extends Shot {
  job_id: string
  filename: string
  scene_number: number
  scene_title: string
  shot_number: number
  start_time: string
  end_time: string
  start_seconds: number
  end_seconds: number
  visual_description: string
  shot_type: string
  camera_movement: string
  subjects: string[]
  mood: string
  tags: string[]
  thumbnail_url: string | null
  preview_url: string | null
  review_status: ReviewStatus
  section_context?: { scene_number: number; scene_title: string; scene_description: string; start_time: string; end_time: string }
  section_analysis?: SceneDeepAnalysis | null
  video_summary?: VideoSummary | null
  project_metadata?: AssetMetadata
  custom_analysis?: unknown
  transcript_segments?: Record<string, unknown>[]
  match_sources?: string[]
  match_context?: string
}
export interface Capabilities {
  google_configured: boolean
  models: { analysis: string; deep: string }
  local_media: boolean
  max_file_size_mb?: number
  linked_media_import?: boolean
}
export interface ImportProgress { stage: string; message: string; percent?: number; elapsed_seconds?: number }
export async function importLocalMedia(mediaPath: string, viewingPath: string, confirmed: boolean, onProgress?: (progress: ImportProgress) => void) {
  const importId = crypto.randomUUID()
  const controller = new AbortController()
  let finished = false
  async function poll() {
    while (!finished) {
      try {
        const progress = await request<ImportProgress>(`/api/local-media/import-progress/${importId}`, {
          headers: { 'Content-Type': 'application/json' }, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
        })
        if (!finished) onProgress?.(progress)
      } catch {
        if (!finished) onProgress?.({ stage: 'reconnecting', message: 'Reconnecting to import progress…' })
      }
      if (!finished) await new Promise(resolve => setTimeout(resolve, 800))
    }
  }
  if (onProgress) void poll()
  try {
    return await request<AssetDetail>('/api/local-media/import', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_path: mediaPath, viewing_path: viewingPath, proxy_alignment_confirmed: confirmed, import_id: importId }) })
  } finally { finished = true; controller.abort() }
}
export function setDisplayColor(jobId: string, mode: string) {
  return request<AssetDetail>(`/api/local-media/${jobId}/display-color`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) })
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status}). Please try again.`)
  }
  return response.json() as Promise<T>
}
export function getLibrary(filters: { q?: string; production?: string; scene?: string; take?: string; camera_roll?: string; cinematographer?: string; project?: string; review_status?: string; tag?: string; rights_status?: string; collection?: string; folder_id?: string; limit?: number; offset?: number; sort?: string }, signal?: AbortSignal) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, String(value)) })
  return request<LibraryResponse>(`/api/library?${params}`, { signal })
}
export function getAsset(id: string, signal?: AbortSignal) {
  return request<AssetDetail>(`/api/library/${encodeURIComponent(id)}`, { signal })
}
export interface PlaybackStatus {
  available: boolean
  original_offline?: boolean
  kind?: 'original' | 'viewing_proxy' | 'cached_proxy'
  needs_transcode?: boolean
  message: string
}
export function getPlaybackStatus(id: string, signal?: AbortSignal) {
  return request<PlaybackStatus>(`/api/library/${encodeURIComponent(id)}/playback-status`, { signal })
}
export function getSourceStatus(ids: string[], signal?: AbortSignal) {
  return request<{ sources: Record<string, boolean | null> }>('/api/library/source-status', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_ids: ids }),
  })
}
export function getProjects(signal?: AbortSignal, folderId = '') {
  return request<{ projects: LibraryProject[]; total: number }>(`/api/library/projects?${new URLSearchParams({ folder_id: folderId })}`, { signal })
}
export function getShots(filters: { q: string; job_id?: string; production?: string; scene?: string; take?: string; camera_roll?: string; cinematographer?: string; project?: string; review_status?: string; tag?: string; rights_status?: string; collection?: string; folder_id?: string; limit?: number; offset?: number }, signal?: AbortSignal) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, String(value)) })
  return request<{ shots: LibraryShot[]; total: number }>(`/api/library/search/shots?${params}`, { signal })
}
export interface LibraryFolder { id: string; name: string; shot_count: number }
export interface FolderResponse { folders: LibraryFolder[]; total_shots: number; unfiled_shots: number }
export function getFolders(signal?: AbortSignal) { return request<FolderResponse>('/api/library/folders', { signal }) }
export function saveFolder(name: string, id?: string) {
  return request<{ id: string; name: string }>(id ? `/api/library/folders/${encodeURIComponent(id)}` : '/api/library/folders', {
    method: id ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  })
}
export function deleteFolder(id: string) { return request<{ status: string; note: string }>(`/api/library/folders/${encodeURIComponent(id)}`, { method: 'DELETE' }) }
export function moveToFolder(ids: string[], folderId: string | null) {
  return request<{ moved: number; folder_id: string | null }>('/api/library/move', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_ids: ids, folder_id: folderId }),
  })
}
export function saveMetadata(id: string, metadata: Partial<AssetMetadata>) {
  return request<LibraryAsset>(`/api/library/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(metadata),
  })
}
export function saveShot(id: string, number: number, annotation: Partial<ShotAnnotation>, expectedRange: { start_time: string; end_time: string }) {
  const params = new URLSearchParams({ expected_start_time: expectedRange.start_time, expected_end_time: expectedRange.end_time })
  return request<ShotAnnotation>(`/api/library/${encodeURIComponent(id)}/shots/${number}?${params}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(annotation),
  })
}
export function analyzeAsset(id: string, mode: 'flash_only' | 'flash_pro', fps: number, customPrompt: string, proxyMode: ProxyMode = 'auto', shotMode: 'auto' | 'single_take' | 'detect_cuts' = 'auto') {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey
  return request<{ job_id: string; status: string }>(`/api/library/${encodeURIComponent(id)}/analyze`, {
    method: 'POST', headers, body: JSON.stringify({ mode, fps, custom_prompt: customPrompt, proxy_mode: proxyMode, shot_mode: shotMode }),
  })
}
export function getCapabilities() { return request<Capabilities>('/api/capabilities') }
export function cancelAnalysis(id: string) { return request<{ status: string }>(`/api/library/${encodeURIComponent(id)}/cancel`, { method: 'POST' }) }
export function deleteAsset(id: string) {
  const headers: Record<string, string> = {}
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey
  return request<{ status: 'deleted'; remote_deleted: boolean; note: string | null }>(`/api/files/${encodeURIComponent(id)}`, {
    method: 'DELETE', headers,
  })
}
export type ExportFormat = 'json' | 'csv' | 'xmp' | 'srt' | 'edl' | 'fcpxml' | 'premiere_xml'
export interface MediaExportOptions {
  available: boolean
  items: { id: string; title: string; filename: string; available: boolean; reason: string | null; analysis_version: string; analyzed: boolean; partial: boolean }[]
}
export function getMediaExportOptions(ids: string[], signal?: AbortSignal) {
  return request<MediaExportOptions>('/api/library/media-export-options', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_ids: ids }), signal,
  })
}
export function downloadMediaExport(ids: string[], title: string, layout: 'timeline' | 'bin', includeMetadata: boolean, versions: Record<string, string>, signal?: AbortSignal) {
  return fetch('/api/library/media-export', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: ids, title, layout, include_metadata: includeMetadata, analysis_versions: versions }), signal })
}
export interface ExportOptions {
  scope: 'selected_shots' | 'asset'
  shot_numbers: number[]
  formats: { format: ExportFormat; label: string; available: boolean; reason: string | null }[]
}
function exportParams(shotNumbers?: number[], analysisStartedAt?: string) {
  const params = new URLSearchParams()
  if (shotNumbers) params.set('shot_numbers', shotNumbers.join(','))
  if (analysisStartedAt !== undefined) params.set('analysis_started_at', analysisStartedAt)
  return params
}
export function getExportOptions(id: string, shotNumbers?: number[], analysisStartedAt?: string, signal?: AbortSignal) {
  return request<ExportOptions>(`/api/library/${encodeURIComponent(id)}/export-options?${exportParams(shotNumbers, analysisStartedAt)}`, { signal })
}
export function exportUrl(id: string, format: ExportFormat, shotNumbers?: number[], analysisStartedAt?: string) {
  const params = exportParams(shotNumbers, analysisStartedAt)
  params.set('format', format)
  return `/api/library/${encodeURIComponent(id)}/export?${params}`
}
