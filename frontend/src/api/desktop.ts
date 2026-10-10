const TOKEN_STORAGE_KEY = 'video-analyzer-desktop-token'
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/

export interface DesktopHealth {
  desktop_mode: boolean
  google_configured: boolean
  local_media: boolean
}

function isLoopback(hostname: string): boolean {
  return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '[::1]' || hostname === '::1'
}

/** Consume the launcher's fragment before rendering; never persist pairing in localStorage. */
export function captureDesktopSession(browser: Pick<Window, 'location' | 'history' | 'sessionStorage'> = window): void {
  const fragment = new URLSearchParams(browser.location.hash.slice(1))
  if (!fragment.has('desktop-token')) return
  const token = fragment.get('desktop-token') || ''
  // Strip even invalid/untrusted tokens so copying the page URL cannot expose one.
  browser.history.replaceState(null, '', browser.location.pathname + browser.location.search)
  if (!isLoopback(browser.location.hostname)) return
  try {
    if (TOKEN_PATTERN.test(token)) browser.sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
    else browser.sessionStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // Without sessionStorage, the desktop session remains unpaired; no persistent fallback.
  }
}

export function getDesktopToken(): string | null {
  if (!isLoopback(window.location.hostname)) return null
  try {
    const token = window.sessionStorage.getItem(TOKEN_STORAGE_KEY)
    return token && TOKEN_PATTERN.test(token) ? token : null
  } catch {
    return null
  }
}

async function desktopRequest(path: 'health' | 'settings', init: RequestInit = {}): Promise<DesktopHealth> {
  const token = getDesktopToken()
  if (!token) throw new Error('Open Video Analyzer from the companion app to configure your editor connection.')
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try {
    response = await fetch(`/api/editor/premiere/${path}`, { ...init, headers, cache: 'no-store', credentials: 'omit', redirect: 'error' })
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new Error('The companion app is unavailable. Open it and try again.')
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new Error('This desktop session is no longer paired. Reopen Video Analyzer from the companion app.')
    }
    // Error bodies can contain provider details; never reflect them into the key dialog.
    throw new Error(path === 'settings' ? 'The companion app could not save the key. Try again.' : 'Could not read the companion app settings.')
  }
  let data: Record<string, unknown>
  try { data = await response.json() as Record<string, unknown> }
  catch { throw new Error('The companion app returned an unreadable response. Try again.') }
  if (!data || data.desktop_mode !== true) throw new Error('API key storage is available only in the installed companion app.')
  return {
    desktop_mode: true,
    google_configured: data.google_configured === true || data.google_api_key_configured === true,
    local_media: data.local_media === true,
  }
}

export function getDesktopHealth(signal?: AbortSignal): Promise<DesktopHealth> {
  return desktopRequest('health', { signal })
}

export async function saveDesktopGoogleApiKey(key: string): Promise<DesktopHealth> {
  const result = await desktopRequest('settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ google_api_key: key }),
  })
  if (result.google_configured !== Boolean(key)) throw new Error('The companion app did not confirm the requested key change. Try again.')
  return result
}

/** Called only by the explicit Copy pairing code button. */
export async function copyEditorPairingCode(): Promise<void> {
  const token = getDesktopToken()
  if (!token) throw new Error('Reopen Video Analyzer from the companion app to get a pairing code.')
  if (!navigator.clipboard?.writeText) throw new Error('Clipboard access is unavailable. Use Pair with editor in the companion app menu.')
  try { await navigator.clipboard.writeText(token) }
  catch { throw new Error('Could not copy the pairing code. Use Pair with editor in the companion app menu.') }
}

export const copyPremierePairingCode = copyEditorPairingCode
