import { ApiError } from "./http";

/*
 * Откуда интерфейс берёт данные.
 *
 * auto — настоящий API, а для раздела, которого у backend ещё нет, — демо-данные
 *        с пометкой на странице. Как только backend отдаст раздел, демо пропадёт само,
 *        без пересборки. Так работает стенд.
 * api  — только настоящий API: ошибки показываются как есть.
 * demo — только демо-данные, backend не нужен.
 *
 * Режим задаёт VITE_DATA_SOURCE при сборке; по умолчанию его выбирает vite.config.ts.
 * Старое VITE_USE_MOCKS=true|false тоже понимается.
 */
export type DataMode = "auto" | "api" | "demo";

export function parseDataMode(source: string | undefined, legacyMocks: string | undefined): DataMode {
  if (source === "auto" || source === "api" || source === "demo") return source;
  if (legacyMocks === "true") return "demo";
  if (legacyMocks === "false") return "api";
  return "auto";
}

export const dataMode: DataMode = parseDataMode(import.meta.env.VITE_DATA_SOURCE, import.meta.env.VITE_USE_MOCKS);

/** Разделы API, которых у backend может ещё не быть. */
export type Section = "leaderboard" | "methodology" | "session";

export type Source = "live" | "demo";

/** Идентификаторы демо-данных начинаются с demo-: такую ссылку не спутать с настоящим анализом. */
export function isDemoId(id: string): boolean {
  return id.startsWith("demo-");
}

/**
 * Демо включается, только если раздела у backend нет или сам backend недоступен.
 * Остальные ошибки (403, 404 конкретного анализа, 422) демо не маскирует.
 */
export function isBackendMissing(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return error.routeMissing || error.status === 0 || error.status === 502 || error.status === 503 || error.status === 504;
}

// Раздел, которого нет, не спрашиваем на каждом переходе: пять минут, а если backend
// просто недоступен — полминуты, потом пробуем снова.
const MISSING_ROUTE_TTL_MS = 5 * 60_000;
const UNREACHABLE_TTL_MS = 30_000;

export interface SourceRouter {
  /** Живой запрос с запасными демо-данными — по правилам режима. */
  liveOrDemo<T>(section: Section, live: () => Promise<T>, demo: () => T | Promise<T>): Promise<T>;
  /** Откуда пришли последние данные раздела; null — ещё не загружали. */
  currentSource(section: Section): Source | null;
  subscribe(listener: () => void): () => void;
}

export function createSourceRouter(
  mode: DataMode,
  { now = Date.now, delayMs = 300 }: { now?: () => number; delayMs?: number } = {},
): SourceRouter {
  const missingUntil = new Map<Section, number>();
  const sources = new Map<Section, Source>();
  const listeners = new Set<() => void>();

  function report(section: Section, source: Source): void {
    if (sources.get(section) === source) return;
    sources.set(section, source);
    listeners.forEach((listener) => listener());
  }

  return {
    async liveOrDemo(section, live, demo) {
      if (mode === "demo" || (mode === "auto" && (missingUntil.get(section) ?? 0) > now())) {
        report(section, "demo");
        return withDemoDelay(await demo(), delayMs);
      }

      try {
        const value = await live();
        missingUntil.delete(section);
        report(section, "live");
        return value;
      } catch (error) {
        if (mode !== "auto" || !isBackendMissing(error)) {
          throw error;
        }
        const ttl = error instanceof ApiError && error.routeMissing ? MISSING_ROUTE_TTL_MS : UNREACHABLE_TTL_MS;
        missingUntil.set(section, now() + ttl);
        report(section, "demo");
        return demo();
      }
    },

    currentSource(section) {
      return mode === "demo" ? "demo" : (sources.get(section) ?? null);
    },

    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export const sourceRouter = createSourceRouter(dataMode);

/** Небольшая задержка, чтобы у демо-данных были видны состояния загрузки. */
export function withDemoDelay<T>(value: T, delayMs = 300): Promise<T> {
  if (delayMs <= 0) return Promise.resolve(value);
  return new Promise((resolve) => setTimeout(() => resolve(value), delayMs));
}

/** Демо-данные для этого раздела или идентификатора — без обращения к backend. */
export function usesDemo(id?: string): boolean {
  return dataMode === "demo" || (dataMode === "auto" && id !== undefined && isDemoId(id));
}

/** Сессия в демо: вход, кабинет и подключение SourceCraft работают на примере. */
export function isDemoSession(): boolean {
  return sourceRouter.currentSource("session") === "demo";
}
