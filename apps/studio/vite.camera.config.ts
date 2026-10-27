import { defineConfig } from 'vite';

// The camera page, built apart from the studio: the camera bridge serves it at /camera/ (iphone_bridge_server.py
// --page-dir), so it works with nothing else of MicroscopeMind running. Its code is the studio's src/camera/.
export default defineConfig({
  base: '/camera/',
  build: {
    outDir: 'dist-camera',
    emptyOutDir: true,
    rollupOptions: { input: { camera: 'camera.html' } },
  },
});
