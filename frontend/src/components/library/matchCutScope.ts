import type { LibraryProject } from '../../api/library'

export interface MatchCutScope {
  mode: 'all' | 'current' | 'selected'
  mediaIds: string[]
  folderIds: string[]
}

export function searchableMedia(projects: LibraryProject[]) {
  return projects.filter(item => item.shot_count > 0 && item.has_local_media !== false &&
    !['queued', 'analyzing', 'processing', 'deleting'].includes(item.status))
}

export function resolveMatchCutScope(scope: MatchCutScope, projects: LibraryProject[], sourceId: string): string[] {
  if (scope.mode === 'current') return [sourceId]
  const media = new Set(scope.mediaIds), folders = new Set(scope.folderIds)
  return [...new Set(searchableMedia(projects).filter(item => scope.mode === 'all' ||
    media.has(item.id) || folders.has(item.folder_id || 'unfiled')).map(item => item.id))].sort()
}

export function validMatchCutScope(value: unknown): value is MatchCutScope {
  if (!value || typeof value !== 'object') return false
  const scope = value as MatchCutScope
  return ['all', 'current', 'selected'].includes(scope.mode) &&
    Array.isArray(scope.mediaIds) && scope.mediaIds.every(id => typeof id === 'string') &&
    Array.isArray(scope.folderIds) && scope.folderIds.every(id => typeof id === 'string')
}
