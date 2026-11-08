import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        configure(proxy) {
          proxy.on('proxyReq', (proxyRequest, request) => {
            proxyRequest.setHeader('X-Real-IP', request.socket.remoteAddress || '127.0.0.1');
          });
        },
      },
      '/health': 'http://localhost:8000',
    },
  },
});
