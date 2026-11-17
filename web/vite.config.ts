import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development, requests to /api go to the FastAPI server (no CORS setup needed).
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 2000 },   // map libraries (MapLibre, deck.gl) are big; that's expected
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})