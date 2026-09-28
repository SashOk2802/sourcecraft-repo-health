import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT
  ? Number(process.env.VITE_HMR_CLIENT_PORT)
  : undefined;

export default defineConfig({
  // Docker Compose mounts node_modules as a named volume. Older volumes can be
  // owned by root, while the container deliberately runs Vite as user `app`.
  cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite",
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    ...(hmrClientPort ? { hmr: { clientPort: hmrClientPort } } : {}),
    proxy: {
      "/api": { target: apiProxyTarget, changeOrigin: true },
    },
  },
});
