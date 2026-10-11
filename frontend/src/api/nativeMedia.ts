declare global {
  interface Window { pywebview?: { api?: { choose_files?: () => Promise<string[]> } } }
}

export function nativeMediaAvailable() {
  return typeof window.pywebview?.api?.choose_files === 'function'
}

export function chooseNativeMedia(): Promise<string[]> {
  const choose = window.pywebview?.api?.choose_files
  if (!choose) return Promise.reject(new Error('Open the desktop window to select original media.'))
  return choose()
}

export async function openDesktopWindow(pick = false) {
  const response = await fetch('/api/local-media/open-desktop', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pick }) })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail || 'Could not open the desktop window.')
}
