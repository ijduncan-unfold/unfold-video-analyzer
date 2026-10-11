import { create } from 'zustand'
import type { ProxyMode } from '../api/library'

const STORAGE_KEY = 'video-analyzer-api-key'
const PROXY_KEY = 'video-analyzer-proxy-mode-v1'
const savedProxyMode = localStorage.getItem(PROXY_KEY)

interface SettingsState {
  proxyMode: ProxyMode
  setProxyMode: (mode: ProxyMode) => void
  apiKey: string
  setApiKey: (key: string) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  proxyMode: savedProxyMode === 'always' || savedProxyMode === 'off' ? savedProxyMode : 'auto',
  setProxyMode: (proxyMode) => {
    localStorage.setItem(PROXY_KEY, proxyMode)
    set({ proxyMode })
  },
  apiKey: localStorage.getItem(STORAGE_KEY) || '',
  setApiKey: (apiKey) => {
    if (apiKey) {
      localStorage.setItem(STORAGE_KEY, apiKey)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
    set({ apiKey })
  },
}))
