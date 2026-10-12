import { Icon } from './Icon'
import './OriginalOfflineBadge.css'

export function OriginalOfflineBadge({ offline }: { offline: boolean }) {
  if (!offline) return null
  return <span className="lw-original-offline" title="The original media is unavailable or its source drive is disconnected. An available proxy can still play."><Icon name="warning" size={12} />Original media offline</span>
}
