import { useEffect, useState } from 'react'
import { getSourceStatus } from '../../api/library'

export function useSourceAvailability(ids: string[], enabled = true) {
  const key = JSON.stringify([...new Set(ids)].sort())
  const [state, setState] = useState<{ key: string; sources: Record<string, boolean | null> }>({ key: '', sources: {} })
  useEffect(() => {
    const selected: string[] = JSON.parse(key)
    if (!enabled || !selected.length) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let checking = false
    async function poll() {
      if (checking || controller.signal.aborted) return
      clearTimeout(timer)
      checking = true
      try {
        const result = await getSourceStatus(selected, controller.signal)
        if (!controller.signal.aborted) setState({ key, sources: result.sources })
      } catch {
        // Losing the companion connection does not mean the original went offline.
      } finally {
        checking = false
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 10000)
      }
    }
    void poll()
    window.addEventListener('focus', poll)
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('focus', poll) }
  }, [key, enabled])
  return state.key === key ? state.sources : {}
}
