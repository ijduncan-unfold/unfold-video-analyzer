import type { VisualMatch, VisualResults } from '../../api/visual'

// The engine accepts 20 sources per request. Merge their scores globally so
// selecting a whole library does not impose that limit on the user.
export async function searchAcrossMedia(ids: string[], search: (ids: string[]) => Promise<VisualResults>,
  signal?: AbortSignal, onPartial?: (result: VisualResults, searched: number) => void): Promise<VisualResults> {
  const unique = [...new Set(ids)]
  if (!unique.length) throw new Error('Choose searchable media or folders first.')
  const matches = new Map<string, VisualMatch>()
  const indexes = new Map<string, VisualResults['indexes'][number]>()
  let result: VisualResults | undefined
  for (let offset = 0; offset < unique.length; offset += 20) {
    if (signal?.aborted) throw new DOMException('Search canceled', 'AbortError')
    const part = await search(unique.slice(offset, offset + 20))
    if (signal?.aborted) throw new DOMException('Search canceled', 'AbortError')
    for (const match of part.matches) {
      const key = `${match.job_id}:${match.shot_number}`
      if (!matches.has(key) || matches.get(key)!.score < match.score) matches.set(key, match)
    }
    for (const index of part.indexes) indexes.set(index.job_id, index)
    result = { ...part, matches: [...matches.values()].sort((a, b) => b.score - a.score ||
      a.job_id.localeCompare(b.job_id) || a.shot_number - b.shot_number).slice(0, 12), indexes: [...indexes.values()] }
    onPartial?.(result, Math.min(unique.length, offset + 20))
  }
  return result!
}
