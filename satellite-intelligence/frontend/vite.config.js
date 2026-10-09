import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/recharts/') || id.includes('/node_modules/victory-vendor/')) {
            return 'charts';
          }
          if (id.includes('/node_modules/leaflet/') || id.includes('/node_modules/react-leaflet/')) {
            return 'maps';
          }
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/') || id.includes('/node_modules/scheduler/')) {
            return 'framework';
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    restoreMocks: true,
  },
});
