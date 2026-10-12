import type { VisualIndex, VisualMatch, VisualResults, VisualShape } from '../../api/visual'
import type { MatchCutScope } from './matchCutScope'
import { validMatchCutScope } from './matchCutScope.ts'

export interface MatchCutSession {
  run: string | null
  shotNumber: number
  seconds: number
  targetIds: string[]
  targetScope?: MatchCutScope
  weights: { shape: number; composition: number; color: number }
  region: number[] | null
  sourceOutline?: number[][] | null
  drawingMode?: 'outline' | 'box'
  sourceShapeId: number | null
  alignShape: boolean
  knownShapes: { key: string; forms: VisualShape[] } | null
  indexes: VisualIndex[]
  result: VisualResults | null
  resultKey: string
  searchIntent: string
  selected: VisualMatch | null
  incoming: number
  handle: number
}

const PREFIX = 'video-analyzer:match-cuts:v1:'
const memory = new Map<string, MatchCutSession>()

export function readMatchCutSession(id: string, run: string | null): MatchCutSession | null {
  try {
    const cached = memory.get(id)
    const raw = cached ? null : localStorage.getItem(PREFIX + id)
    const saved: MatchCutSession | null = cached || (raw ? JSON.parse(raw) : null)
    if (!saved || saved.run !== run || !Number.isFinite(saved.shotNumber) || !Number.isFinite(saved.seconds) ||
      !Array.isArray(saved.targetIds) || !saved.targetIds.every(id => typeof id === 'string') ||
      !saved.weights || !['shape', 'composition', 'color'].every(axis => {
        const weight = saved.weights[axis as keyof typeof saved.weights]
        return Number.isFinite(weight) && weight >= 0 && weight <= 1
      }) || !Array.isArray(saved.indexes) || typeof saved.searchIntent !== 'string' ||
      typeof saved.resultKey !== 'string' || typeof saved.alignShape !== 'boolean' ||
      !Number.isFinite(saved.incoming) || ![.5, 1, 2, 3, 5].includes(saved.handle) ||
      (saved.region !== null && (!Array.isArray(saved.region) || saved.region.length !== 4 || !saved.region.every(Number.isFinite))) ||
      (saved.sourceOutline != null && (!Array.isArray(saved.sourceOutline) || saved.sourceOutline.length < 3 || saved.sourceOutline.length > 96 || !saved.sourceOutline.every(p => Array.isArray(p) && p.length === 2 && p.every(v => Number.isFinite(v) && v >= 0 && v <= 1000)))) ||
      (saved.drawingMode !== undefined && saved.drawingMode !== 'outline' && saved.drawingMode !== 'box') ||
      (saved.sourceShapeId !== null && (!Number.isInteger(saved.sourceShapeId) || saved.sourceShapeId < 0)) ||
      (saved.knownShapes !== null && (typeof saved.knownShapes?.key !== 'string' || !Array.isArray(saved.knownShapes?.forms))) ||
      (saved.result && !Array.isArray(saved.result.matches)) ||
      (saved.targetScope !== undefined && !validMatchCutScope(saved.targetScope))) return null
    return saved
  } catch { return memory.get(id)?.run === run ? memory.get(id)! : null }
}

export function saveMatchCutSession(id: string, session: MatchCutSession) {
  memory.set(id, session)
  try { localStorage.setItem(PREFIX + id, JSON.stringify(session)) }
  catch { /* Navigation still restores from memory when browser storage is unavailable. */ }
}

export function forgetMatchCutSession(id: string) {
  memory.delete(id)
  try { localStorage.removeItem(PREFIX + id) }
  catch { /* Storage may be disabled. The in-memory session is still removed. */ }
}
