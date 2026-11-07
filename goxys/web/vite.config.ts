import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const alvo = 'http://localhost:8000'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: alvo, changeOrigin: true },
      '/portal': { target: alvo, changeOrigin: true },
      '/assets-goxys': { target: alvo, changeOrigin: true },
    },
  },
})
