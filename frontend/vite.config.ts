import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

/*
 * По каким доменам открывают dev-сервер или vite preview. По умолчанию — любые: стенд работает
 * на dev-сервере за HTTPS-прокси со своим доменом (как в main). Чтобы пустить только свои
 * домены, перечислите их через запятую в FRONTEND_ALLOWED_HOSTS.
 */
function parseAllowedHosts(value: string | undefined): string[] | true {
  if (!value || value.trim() === "all") return true;
  const hosts = value
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  return hosts.length > 0 ? hosts : true;
}

const allowedHosts = parseAllowedHosts(process.env.FRONTEND_ALLOWED_HOSTS);
const proxy = { "/api": { target: apiProxyTarget, changeOrigin: true } };

// Порт, на котором браузер подключается к HMR, когда dev-сервер стоит за HTTPS-прокси (например, 443).
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT ? Number(process.env.VITE_HMR_CLIENT_PORT) : undefined;

/*
 * Источник данных — src/api/dataSource.ts: без флагов настоящий API. Демо-данные включают
 * явно — VITE_USE_MOCKS=true или VITE_DATA_SOURCE=demo, а API с демо там, где раздела у
 * backend нет, — VITE_DATA_SOURCE=auto.
 */
export default defineConfig({
  // Docker Compose mounts node_modules as a named volume. Older volumes can be
  // owned by root, while the container deliberately runs Vite as user `app`.
  cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite",
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts,
    ...(hmrClientPort ? { hmr: { clientPort: hmrClientPort } } : {}),
    proxy,
  },
  // `npm run preview` — собранная версия с тем же proxy: так её можно проверить перед выкладкой.
  preview: {
    host: true,
    port: 4173,
    strictPort: true,
    allowedHosts,
    proxy,
  },
  build: {
    rolldownOptions: {
      output: {
        // Библиотеки меняются реже кода приложения: отдельные файлы дольше живут в кэше браузера.
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: "gravity", test: /node_modules[\\/]@gravity-ui[\\/]/ },
            { name: "vendor", test: /node_modules[\\/]/ },
          ],
        },
      },
    },
  },
});
