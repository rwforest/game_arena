import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
// import jQuery from 'jquery';

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  define: {
    // 'window.$': jQuery,
    // 'window.jQuery': jQuery,
  },
  optimizeDeps: {
    include: ['jquery'],
  },
  resolve: {
    alias: {
      'jquery': 'jquery/dist/jquery.js',
    },
  },
})
