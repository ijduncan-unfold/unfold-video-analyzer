import type { AnalysisProgress } from '../../api/library'
import { duration } from './format'

export function isActiveAnalysis(status?: string | null) {
  return !!status && ['queued', 'analyzing', 'processing'].includes(status)
}

export function analysisStageLabel(stage?: string | null, grouping?: string) {
  const labels: Record<string, string> = {
    starting: 'Starting analysis', queued: 'Queued', uploading: 'Uploading video',
    preparing_part: 'Preparing analysis proxy', preparing_proxy: 'Preparing analysis proxy', processing: 'Gemini is preparing video',
    indexing: 'Analyzing sections', enriching: grouping === 'processing_sections' ? 'Adding section details' : 'Analyzing scenes', summarizing: 'Writing summary',
    matching: 'Matching shots', custom: 'Running custom analysis', complete: 'Complete',
    error: 'Analysis stopped', cancelled: 'Cancelled', interrupted: 'Interrupted',
  }
  return (stage && labels[stage]) || 'Analyzing video'
}

function known(value?: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

export function analysisFacts(progress: AnalysisProgress | null | undefined, availableShots: number) {
  const facts: string[] = []
  if (['preparing_part', 'preparing_proxy', 'uploading', 'processing'].includes(progress?.stage || '') &&
      known(progress?.upload_part) && known(progress?.total_upload_parts)) {
    facts.push(`Part ${progress.upload_part}/${progress.total_upload_parts}`)
  }
  if (known(progress?.completed_sections)) {
    if (known(progress?.total_sections) && progress.total_sections > 0) {
      facts.push(`${progress.completed_sections}/${progress.total_sections} sections`)
    } else if (progress.completed_sections > 0) {
      facts.push(`${progress.completed_sections} sections`)
    }
  }
  const shots = known(progress?.completed_shots) ? progress.completed_shots : availableShots
  facts.push(`${shots} ${shots === 1 ? 'shot' : 'shots'}`)
  if (known(progress?.failed_sections) && progress.failed_sections > 0) facts.push(`${progress.failed_sections} failed`)
  const coverage = known(progress?.processed_seconds) && known(progress?.total_seconds) && progress.total_seconds > 0
    ? `${duration(progress.processed_seconds)} / ${duration(progress.total_seconds)} analyzed`
    : null
  return { summary: facts.join(' · '), coverage }
}
