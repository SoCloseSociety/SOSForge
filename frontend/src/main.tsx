import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './ErrorBoundary'
import { registerServiceWorker } from './pwa'
import { applyStoredTheme } from './theme'
// IBM Plex, self-hosted (OFL, via @fontsource): the files are bundled under
// /assets/ and cached by the service worker like any other asset, so the CSP
// needs no font host and the fonts are there offline. Three sans weights and
// two mono: words in sans, numbers and overlines in mono. CJK glyphs fall
// back to the system font, as the design source accepts for Cyrillic.
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import './styles.css'

// Before React renders anything: a page that mounts Lunar and flips to Soft
// one frame later flashes. (The empty page before this script runs is still
// Lunar's ground for a Soft reader: an inline script could fix that, and the
// CSP -- script-src 'self' -- rightly forbids one.)
applyStoredTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)

registerServiceWorker()
