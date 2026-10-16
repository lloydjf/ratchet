import './styles/app.css'
import { initTheme } from './lib/theme'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { initApi } from './api/select'
import { configureAnalytics } from './analytics'

initTheme()

async function boot() {
  const api = await initApi()
  const { App } = await import('./App')
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  // Analytics only on the public host; meta decides. Never blocks rendering.
  api
    .getMeta()
    .then((m) => configureAnalytics({ api, publicMode: m.public_mode }))
    .catch(() => {})
}

void boot()
