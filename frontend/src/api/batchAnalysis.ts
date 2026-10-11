export type AnalysisMode = 'flash_only' | 'flash_pro'
export interface BatchAnalysisResult { id: string; accepted: boolean; error?: string }

/** Queue requests promptly; the server limits concurrent Gemini analyses. */
export async function queueBatchAnalysis(
  ids: string[], mode: AnalysisMode,
  submit: (id: string, mode: AnalysisMode) => Promise<unknown>,
  onResult: (result: BatchAnalysisResult) => void,
) {
  const results: BatchAnalysisResult[] = []
  for (const id of new Set(ids)) {
    let result: BatchAnalysisResult
    try {
      await submit(id, mode)
      result = { id, accepted: true }
    } catch (error) {
      result = { id, accepted: false, error: error instanceof Error ? error.message : 'Could not queue analysis' }
    }
    results.push(result)
    onResult(result)
  }
  return results
}
