declare global {
  interface Window { pywebview?: { api?: { choose_files?: () => Promise<string[]> } } }
}

export function nativeMediaAvailable() {
  return typeof window.pywebview?.api?.choose_files === 'function'
}

export async function chooseNativeMedia(): Promise<string[]> {
  const choose = window.pywebview?.api?.choose_files
  if (choose) return choose()
  const response = await fetch('/api/local-media/choose-files', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail || 'Could not open the media picker.')
  return result.paths
}

export async function resolveBrowserDrop(file: File): Promise<string | null> {
  const edgeBytes = 65536
  const head = new Uint8Array(await file.slice(0, edgeBytes).arrayBuffer())
  const tail = file.size > edgeBytes
    ? new Uint8Array(await file.slice(Math.max(edgeBytes, file.size - edgeBytes)).arrayBuffer())
    : new Uint8Array()
  const edges = new Uint8Array(head.length + tail.length)
  edges.set(head); edges.set(tail, head.length)
  const digest = await crypto.subtle.digest('SHA-256', edges)
  const hash = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
  const response = await fetch('/api/local-media/resolve-drop', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: file.name, size: file.size, last_modified: file.lastModified, edge_sha256: hash }) })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail || 'Could not locate the original media.')
  return result.media_path
}
