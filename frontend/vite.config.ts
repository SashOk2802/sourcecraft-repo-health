import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

/*
 * Доменные имена, по которым открывают dev-сервер или vite preview на стенде, через запятую;
 * "all" — любые. Без этого Vite отвечает «Blocked request» на всё, кроме localhost и IP.
 */
function parseAllowedHosts(value: string | undefined): string[] | true | undefined {
  if (!value) return undefined;
  if (value.trim() === "all") return true;
  const hosts = value
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  return hosts.length > 0 ? hosts : undefined;
}

const allowedHosts = parseAllowedHosts(process.env.FRONTEND_ALLOWED_HOSTS);
const proxy = { "/api": { target: apiProxyTarget, changeOrigin: true } };

export default defineConfig(({ command, mode }) => {
  /*
   * Источник данных по умолчанию (src/api/dataSource.ts). Сборка для стенда и dev-сервер
   * рядом с backend (compose задаёт API_PROXY_TARGET) ходят в настоящий API, а где раздела
   * ещё нет — показывают демо. Одиночный `npm run dev` без backend — сразу демо.
   * Явное VITE_DATA_SOURCE или VITE_USE_MOCKS в окружении и .env-файлах важнее.
   */
  const env = loadEnv(mode, process.cwd(), "VITE_");
  if (!env.VITE_DATA_SOURCE && !env.VITE_USE_MOCKS) {
    process.env.VITE_DATA_SOURCE = command === "build" || process.env.API_PROXY_TARGET ? "auto" : "demo";
  }

  return {
    // Docker Compose mounts node_modules as a named volume. Older volumes can be
    // owned by root, while the container deliberately runs Vite as user `app`.
    cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite",
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      strictPort: true,
      allowedHosts,
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
  };
});
