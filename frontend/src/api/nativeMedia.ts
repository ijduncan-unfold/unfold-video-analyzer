declare global {
  interface Window { pywebview?: { api?: { choose_files?: () => Promise<string[]>; choose_folders?: () => Promise<string[]> } } }
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

export async function chooseNativeFolders(): Promise<string[]> {
  const choose = window.pywebview?.api?.choose_folders
  if (choose) return choose()
  const response = await fetch('/api/local-media/choose-folders', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: '{}' })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail || 'Could not open the folder picker.')
  return result.paths
}

export async function discoverNativeMedia(paths: string[]): Promise<{ paths: string[]; warnings: string[] }> {
  const response = await fetch('/api/local-media/discover', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths }) })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail || 'Could not scan video folders.')
  return result
}

const videoExtension = /\.(mp4|mov|mxf|mkv|avi|webm|m4v|mpg|mpeg|mts|m2ts|braw|r3d|ari|arx|crm|flv|wmv|3gpp)$/i
export function isVideoMedia(name: string) { return videoExtension.test(name) }

// Capture entries during the drop event: browsers clear DataTransfer afterwards.
export function getDroppedFolders(transfer: DataTransfer): FileSystemDirectoryEntry[] {
  return Array.from(transfer.items).map(item => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemDirectoryEntry => !!entry?.isDirectory)
}

export async function resolveBrowserFolderDrop(folder: FileSystemDirectoryEntry): Promise<string | null> {
  let remaining = 2000
  async function firstVideo(entry: FileSystemEntry, relative: string): Promise<{ file: File; relative: string } | null> {
    if (--remaining < 0) return null
    if (entry.isFile) {
      if (!isVideoMedia(entry.name)) return null
      const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
      return { file, relative }
    }
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    while (remaining > 0) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
      if (!batch.length) break
      for (const child of batch) {
        const found = await firstVideo(child, `${relative}/${child.name}`)
        if (found) return found
      }
    }
    return null
  }
  try {
    const anchor = await firstVideo(folder, folder.name)
    if (!anchor) return null
    const path = await resolveBrowserDrop(anchor.file)
    if (!path) return null
    // The backend verified the file fingerprint. Derive a folder only when the
    // complete relative path agrees; never guess a folder from its name alone.
    const originalParts = path.split(/[/\\]/), relativeParts = anchor.relative.split('/')
    if (originalParts.length < relativeParts.length || relativeParts.some((part, index) =>
      part.toLocaleLowerCase() !== originalParts[originalParts.length - relativeParts.length + index].toLocaleLowerCase())) return null
    return originalParts.slice(0, originalParts.length - relativeParts.length + 1).join(path.includes('\\') ? '\\' : '/')
  } catch { return null } // Unknown/unreadable folders use the native picker.
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
