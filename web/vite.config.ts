import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwind from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  // '' prefix: read CAFEOPS_API_URL from .env files and the process environment.
  const api = loadEnv(mode, '.', '').CAFEOPS_API_URL || 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwind()],
    server: {
      port: 5178,
      strictPort: false,
      // VITE_LIVE=1 dev: forward the API and menu photos to uvicorn.
      proxy: { '/api': api, '/media': api },
    },
  }
})
