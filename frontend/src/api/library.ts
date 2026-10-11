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
  job_id: string
  filename: string
  status: string
  created_at: string
  size_bytes: number
  mime_type: string
  youtube_url?: string | null
  metadata: AssetMetadata
  technical: {
    duration_seconds?: number | null
    width?: number | null
    height?: number | null
    frame_rate?: number | null
    codec?: string | null
    source_timecode?: string | null
    has_audio?: boolean | null
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
  facets: { projects: string[]; tags: string[]; collections?: string[] }
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
  analysis_config?: { provider?: string; analysis_model?: string; deep_model?: string; fps?: number; mode?: string; started_at?: string; timestamp_accuracy?: string; grouping?: string; proxy_mode?: ProxyMode; proxy_used?: boolean; proxy_reason?: string }
  cost_estimate?: { estimated_cost_usd: number | null; pricing_status?: string; pricing_as_of?: string; warnings?: string[]; total_input_tokens?: number; total_output_tokens?: number } | null
  transcript: TranscriptSegment[]
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
}
export interface RoughCutDraft {
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
export function generateRoughCut(id: string, mode: RoughCutDraft['mode'], targetSeconds: number, brief: string, proxyMode: ProxyMode = 'auto') {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey
  return request<RoughCutDraft>(`/api/library/${encodeURIComponent(id)}/rough-cut`, {
    method: 'POST', headers, body: JSON.stringify({ mode, target_seconds: targetSeconds, brief, proxy_mode: proxyMode }),
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
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status}). Please try again.`)
  }
  return response.json() as Promise<T>
}
export function getLibrary(filters: { q?: string; project?: string; review_status?: string; tag?: string; rights_status?: string; collection?: string; limit?: number; offset?: number; sort?: string }, signal?: AbortSignal) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, String(value)) })
  return request<LibraryResponse>(`/api/library?${params}`, { signal })
}
export function getAsset(id: string, signal?: AbortSignal) {
  return request<AssetDetail>(`/api/library/${encodeURIComponent(id)}`, { signal })
}
export function getProjects(signal?: AbortSignal) {
  return request<{ projects: LibraryProject[]; total: number }>('/api/library/projects', { signal })
}
export function getShots(filters: { q: string; job_id?: string; project?: string; review_status?: string; tag?: string; rights_status?: string; collection?: string; limit?: number; offset?: number }, signal?: AbortSignal) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, String(value)) })
  return request<{ shots: LibraryShot[]; total: number }>(`/api/library/search/shots?${params}`, { signal })
}
export function saveMetadata(id: string, metadata: Partial<AssetMetadata>) {
  return request<LibraryAsset>(`/api/library/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(metadata),
  })
}
export function saveShot(id: string, number: number, annotation: ShotAnnotation, expectedRange: { start_time: string; end_time: string }) {
  const params = new URLSearchParams({ expected_start_time: expectedRange.start_time, expected_end_time: expectedRange.end_time })
  return request<ShotAnnotation>(`/api/library/${encodeURIComponent(id)}/shots/${number}?${params}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(annotation),
  })
}
export function analyzeAsset(id: string, mode: 'flash_only' | 'flash_pro', fps: number, customPrompt: string, proxyMode: ProxyMode = 'auto') {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey
  return request<{ job_id: string; status: string }>(`/api/library/${encodeURIComponent(id)}/analyze`, {
    method: 'POST', headers, body: JSON.stringify({ mode, fps, custom_prompt: customPrompt, proxy_mode: proxyMode }),
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
export type ExportFormat = 'json' | 'csv' | 'xmp' | 'srt' | 'edl' | 'fcpxml'
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
