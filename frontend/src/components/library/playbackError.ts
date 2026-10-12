import type { PlaybackStatus } from '../../api/library'

export function playbackError(status: PlaybackStatus): string {
  if (!status.available) return status.message
  if (status.kind === 'original') return 'The original is available, but this browser could not play it. Retry playback or prepare a browser-compatible viewing proxy.'
  return `The available proxy could not be played by this browser. ${status.original_offline ? 'The original media is also offline. ' : ''}Retry playback; if the error persists, reconnect the original and regenerate the viewing proxy.`
}
