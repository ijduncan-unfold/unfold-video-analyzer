import { getApiKey } from './apiKey'
import type { UploadResponse } from './types'

export async function uploadVideo(
  file: File,
  onProgress?: (percent: number) => void,
  maxFileSizeMb = 2000,
): Promise<UploadResponse> {
  if (file.size > maxFileSizeMb * 1024 * 1024) {
    throw new Error(`This file exceeds the ${maxFileSizeMb} MB copy limit. Use Link original to reference it without copying.`)
  }
  if (/\.(braw|r3d|ari|arx|crm)$/i.test(file.name)) {
    throw new Error('Use Link original for RAW camera media, with a full-length viewing proxy if needed.')
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const formData = new FormData()
    formData.append('file', file)

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    })

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText))
      } else {
        try {
          const err = JSON.parse(xhr.responseText)
          reject(new Error(err.detail || `Upload failed: ${xhr.status}`))
        } catch {
          reject(new Error(`Upload failed: ${xhr.status}`))
        }
      }
    })

    xhr.addEventListener('error', () => reject(new Error('Network error during upload')))
    xhr.addEventListener('abort', () => reject(new Error('Upload aborted')))

    xhr.open('POST', '/api/upload')
    const key = getApiKey()
    if (key) xhr.setRequestHeader('X-API-Key', key)
    xhr.send(formData)
  })
}
