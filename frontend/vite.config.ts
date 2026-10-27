import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** The API the dev server proxies to. Overridable so a second checkout (a
 * worktree running its own backend on another port) can be looked at without
 * editing this file: `SOS_API_URL=http://127.0.0.1:8330 npx vite --port 5283`. */
const api = process.env.SOS_API_URL ?? 'http://127.0.0.1:8300'

export default defineConfig({
  plugins: [react()],
  server: {
    // 5273 was also AdsForge's declared Vite port. A collision used to land
    // silently on the next free port, which is how one product's browser tab
    // ends up talking to another product's API. `strictPort` fails loudly.
    port: 5380,
    strictPort: true,
    proxy: {
      '/api': { target: api, changeOrigin: true },
      '/healthz': { target: api, changeOrigin: true },
      '/ws': { target: api.replace(/^http/, 'ws'), ws: true },
    },
  },
})
