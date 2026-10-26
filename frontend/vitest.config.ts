import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

/** Test config, kept apart from vite.config.ts: the dev server (port 5380,
 * proxy to the API on :8300) has no business in a test run. */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/__tests__/setup.ts'],
    include: ['src/__tests__/**/*.test.{ts,tsx}'],
  },
})
