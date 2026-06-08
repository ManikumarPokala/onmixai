import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Test config: the React plugin handles JSX + a single React instance; jsdom DOM; a setup
// file wiring jest-dom, jest-axe, and MSW; explicit imports (no test globals).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
    css: false,
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/lib/api/schema.ts'],
    },
  },
})
