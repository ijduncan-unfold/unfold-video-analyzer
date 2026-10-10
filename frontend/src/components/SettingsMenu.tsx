import { useState, useRef, useEffect } from 'react'
import { useSettingsStore } from '../stores/settingsStore'
import { copyPremierePairingCode, getDesktopHealth, getDesktopToken, saveDesktopGoogleApiKey } from '../api/desktop'

export function SettingsMenu() {
  const [isOpen, setIsOpen] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const { apiKey } = useSettingsStore()
  const [desktopConfigured, setDesktopConfigured] = useState(false)
  const [desktopReady, setDesktopReady] = useState(false)
  const [desktopError, setDesktopError] = useState('')
  const [pairingMessage, setPairingMessage] = useState('')
  const desktopSession = Boolean(getDesktopToken())

  const hasKey = apiKey.length > 0 || desktopConfigured

  useEffect(() => {
    if (!desktopSession) return
    const controller = new AbortController()
    getDesktopHealth(controller.signal).then(result => {
      if (controller.signal.aborted) return
      setDesktopReady(true)
      setDesktopConfigured(result.google_configured)
      setDesktopError('')
    }).catch(error => {
      if (!controller.signal.aborted) setDesktopError(error instanceof Error ? error.message : 'The companion app is unavailable.')
    })
    return () => controller.abort()
  }, [desktopSession])

  const handleCopyPairingCode = async () => {
    setPairingMessage('')
    try {
      await copyPremierePairingCode()
      setPairingMessage('Copied. Paste it into the Premiere panel.')
    } catch (error) {
      setPairingMessage(error instanceof Error ? error.message : 'Could not copy the pairing code.')
    }
  }

  return (
    <>
      <div className="relative">
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="p-1.5 rounded hover:bg-zinc-800 transition-colors relative"
          title="Settings"
          aria-label="Settings"
          aria-expanded={isOpen}
        >
          <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          {hasKey && (
            <div className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-blue-500 rounded-full" />
          )}
        </button>

        {isOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setIsOpen(false)} />
            <div className="absolute right-0 mt-1 w-52 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl z-20 py-1">
              <button
                onClick={() => {
                  setIsOpen(false)
                  setShowModal(true)
                }}
                className="w-full px-3 py-2 text-left hover:bg-zinc-700/50 transition-colors flex items-center gap-2"
              >
                <svg className="w-3.5 h-3.5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
                <div>
                  <div className="text-xs text-zinc-200">Google API Key</div>
                  <div className="text-[10px] text-zinc-500">
                    {hasKey ? 'Configured' : 'Not set'}
                  </div>
                </div>
                {hasKey && (
                  <div className="ml-auto w-1.5 h-1.5 bg-blue-500 rounded-full" />
                )}
              </button>
              {desktopReady && (
                <button onClick={handleCopyPairingCode} className="w-full px-3 py-2 text-left text-xs text-zinc-200 hover:bg-zinc-700/50 transition-colors">
                  Copy Premiere pairing code
                </button>
              )}
              {desktopSession && (pairingMessage || desktopError) && (
                <p className="px-3 py-2 text-[10px] text-zinc-400" role="status">{pairingMessage || desktopError}</p>
              )}
            </div>
          </>
        )}
      </div>

      {showModal && <ApiKeyModal onClose={() => setShowModal(false)} desktopSession={desktopSession} desktopConfigured={desktopConfigured} onDesktopConfigured={setDesktopConfigured} />}
    </>
  )
}

function ApiKeyModal({ onClose, desktopSession, desktopConfigured, onDesktopConfigured }: {
  onClose: () => void
  desktopSession: boolean
  desktopConfigured: boolean
  onDesktopConfigured: (configured: boolean) => void
}) {
  const { apiKey, setApiKey } = useSettingsStore()
  const [value, setValue] = useState(apiKey)
  const [validating, setValidating] = useState(false)
  const [status, setStatus] = useState<'idle' | 'valid' | 'invalid'>('idle')
  const [errorMsg, setErrorMsg] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSave = async () => {
    if (validating) return
    const trimmed = value.trim()
    if (!trimmed) {
      setApiKey('')
      onClose()
      return
    }

    setValidating(true)
    setStatus('idle')
    setErrorMsg('')

    try {
      const res = await fetch('/api/validate-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: trimmed }),
      })
      const data = await res.json().catch(() => { throw new Error('Could not read API key validation. Try again.') })
      if (res.ok && data.valid) {
        if (desktopSession) {
          const saved = await saveDesktopGoogleApiKey(trimmed)
          onDesktopConfigured(saved.google_configured)
          // The companion owns the saved key; avoid retaining another browser copy.
          setApiKey('')
          setValue('')
        } else {
          setApiKey(trimmed)
        }
        setStatus('valid')
        setTimeout(onClose, 600)
      } else {
        setStatus('invalid')
        setErrorMsg(desktopSession ? 'Google could not validate this API key. Check it and try again.' : data.error || 'Invalid API key')
      }
    } catch (error) {
      setStatus('invalid')
      setErrorMsg(desktopSession && error instanceof Error ? error.message : 'Failed to validate key')
    } finally {
      setValidating(false)
    }
  }

  const handleClear = async () => {
    if (validating) return
    setValidating(true)
    setErrorMsg('')
    try {
      if (desktopSession) {
        const saved = await saveDesktopGoogleApiKey('')
        onDesktopConfigured(saved.google_configured)
      }
      setApiKey('')
      setValue('')
      setStatus('idle')
    } catch (error) {
      setStatus('invalid')
      setErrorMsg(error instanceof Error ? error.message : 'Could not clear the key.')
    } finally {
      setValidating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-full max-w-md p-5"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-key-title"
      >
        <div className="flex items-center justify-between mb-4">
          <h2 id="api-key-title" className="text-sm font-semibold text-zinc-100">Google API Key</h2>
          <button onClick={onClose} disabled={validating} aria-label="Close API key settings" className="text-zinc-500 hover:text-zinc-300 transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <p className="text-xs text-zinc-400 mb-3">
          {desktopSession ? 'Your key is saved locally by the companion app and used for Gemini analysis in Premiere.' : "Enter your Google AI Studio API key to use Video Analyzer. Your key is stored in this browser and sent to this application's backend, which calls Google's API. A server-configured key can also be used."}
        </p>
        {desktopSession && desktopConfigured && !apiKey && (
          <p className="text-xs text-blue-400 mb-3">A key is configured in the companion app. Enter a new key to replace it.</p>
        )}

        <div className="space-y-3">
          <input
            ref={inputRef}
            type="password"
            aria-label="Google API key"
            autoComplete="off"
            disabled={validating}
            value={value}
            onChange={(e) => {
              setValue(e.target.value)
              setStatus('idle')
              setErrorMsg('')
            }}
            onKeyDown={(e) => e.key === 'Enter' && handleSave()}
            placeholder="AIzaSy..."
            className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-blue-500 transition-colors font-mono"
          />

          {status === 'invalid' && (
            <p className="text-xs text-red-400" role="alert">{errorMsg}</p>
          )}
          {status === 'valid' && (
            <p className="text-xs text-blue-400" role="status">{desktopSession ? 'Saved for the companion app' : 'Key validated successfully'}</p>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={validating}
              className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-600/50 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {validating ? 'Validating...' : 'Save'}
            </button>
            {(apiKey || desktopConfigured) && (
              <button
                onClick={handleClear}
                disabled={validating}
                className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm rounded-lg transition-colors"
              >
                Clear
              </button>
            )}
            <button
              onClick={onClose}
              disabled={validating}
              className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-sm rounded-lg transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>

        <a
          href="https://aistudio.google.com/apikey"
          target="_blank"
          rel="noopener noreferrer"
          className="block mt-3 text-[10px] text-blue-400 hover:text-blue-300 transition-colors"
        >
          Get a free API key from Google AI Studio &rarr;
        </a>
      </div>
    </div>
  )
}
