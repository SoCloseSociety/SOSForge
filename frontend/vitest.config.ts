import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

/** Config de test separee de vite.config.ts: le serveur de dev (proxy :8300,
 * port 5273) n'a rien a faire dans un run de tests. */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/__tests__/setup.ts'],
    include: ['src/__tests__/**/*.test.{ts,tsx}'],
  },
})
