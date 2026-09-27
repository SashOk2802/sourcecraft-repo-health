import { isAnalysisFinished } from "./analyses";
import type { AnalysisStatus } from "./common";
import { isDemoSession, sourceRouter, withDemoDelay } from "./dataSource";
import { ApiError, getJson, postJson } from "./http";
import { mockMyRepositories } from "./mocks/me";
import { mockSession } from "./mocks/session";

/*
 * Пользователь — docs/api-contract.md, «Вход через Яндекс ID»: GET /api/v1/me отдаёт
 * { id, login }, без сессии — 401, если вход не настроен — 503. Вход выполняет backend:
 * интерфейс только уводит на /api/v1/auth/yandex/start, после входа backend возвращает
 * в /me/repositories. Токенов интерфейс не видит.
 * Пока вход у backend не настроен или маршрута нет, в режиме auto работает демо-кабинет:
 * вход, список репозиториев и анализ показаны на вымышленных репозиториях.
 *
 * GET /api/v1/me/repositories — репозитории, которые можно проверить. На первом этапе backend
 * отдаёт публичные репозитории из организаций SOURCECRAFT_PUBLIC_ORGANIZATIONS: стабильный id,
 * название, организацию, webUrl и ветку по умолчанию. Id из списка уходит в
 * POST /api/v1/repositories/{id}/analyses — сам пользователь его не видит.
 */

export interface CurrentUser {
  id: string;
  /** Как назвать пользователя в шапке: backend пока отдаёт только login. */
  displayName: string;
  login: string | null;
  avatarUrl: string | null;
}

/** Ответ GET /api/v1/me. Обязательны только id и login; остальное — на будущее. */
export interface MePayload {
  id: string;
  login?: string | null;
  displayName?: string | null;
  avatarUrl?: string | null;
}

export function toCurrentUser(payload: MePayload): CurrentUser {
  const login = payload.login?.trim() || null;
  return {
    id: payload.id,
    login,
    displayName: payload.displayName?.trim() || login || "Пользователь Яндекса",
    avatarUrl: payload.avatarUrl ?? null,
  };
}

export interface MyRepositoriesResponse {
  items: MyRepository[];
}

export interface MyRepository {
  repository: {
    id: string;
    organizationSlug: string;
    repositorySlug: string;
    name: string;
    url: string | null;
    description: string | null;
    language: string | null;
    visibility: "public" | "private";
    /** В репозитории ещё нет коммитов: анализировать нечего, запуск не предлагаем. */
    isEmpty: boolean;
  };
  /** Последний завершённый анализ; null — репозиторий ещё не проверяли. */
  lastAnalysis: {
    id: string;
    status: AnalysisStatus;
    analyzedAt: string | null;
    score: number | null;
    isPreliminary: boolean;
  } | null;
  /** Анализ, который идёт прямо сейчас. */
  activeAnalysisId: string | null;
}

export interface Session {
  /**
   * live — вход обслуживает backend; demo — демо-кабинет в режиме auto, когда вход на backend
   * недоступен (OAuth не настроен — 503, или раздела нет); offline — то же в режиме api:
   * работаем как гость.
   */
  mode: "live" | "demo" | "offline";
  /** null — пользователь не вошёл. */
  user: CurrentUser | null;
}

export async function fetchSession(): Promise<Session> {
  try {
    const user = await sourceRouter.liveOrDemo("session", fetchLiveUser, () => mockSession.user());
    return { mode: isDemoSession() ? "demo" : "live", user };
  } catch {
    // Публичные страницы от этого не ломаются: рейтинг и отчёты открываются и без входа.
    return { mode: "offline", user: null };
  }
}

async function fetchLiveUser(): Promise<CurrentUser | null> {
  try {
    return toCurrentUser(await getJson<MePayload>("/api/v1/me"));
  } catch (error) {
    // 401 — backend умеет вход, просто пользователь ещё не вошёл.
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function fetchMyRepositories(): Promise<MyRepositoriesResponse> {
  if (isDemoSession()) {
    const items = mockMyRepositories();
    if (items === null) throw new ApiError(401, "Нужно войти");
    return withDemoDelay({ items });
  }
  return toMyRepositories(await getJson<MyRepositoriesPayload>("/api/v1/me/repositories"));
}

/*
 * Ответ дополнительно разбирается в будущих совместимых формах: список в items, repositories
 * или сразу массивом; запись плоская или с вложенным repository; организация строкой,
 * объектом или organizationSlug; slug или repositorySlug. Запись без id пропускаем:
 * анализ запускается только по id из каталога, а не по названию.
 */
interface RepositoryPayload {
  id?: string;
  name?: string;
  slug?: string;
  repositorySlug?: string;
  organizationSlug?: string;
  organization?: string | { slug?: string };
  webUrl?: string | null;
  url?: string | null;
  description?: string | null;
  language?: string | null;
  visibility?: string;
  isEmpty?: boolean;
}

interface LastAnalysisPayload {
  id: string;
  status: AnalysisStatus;
  score?: number | null;
  isPreliminary?: boolean | null;
  analyzedAt?: string | null;
  finishedAt?: string | null;
  createdAt?: string | null;
}

type MyRepositoryPayload = RepositoryPayload & {
  repository?: RepositoryPayload;
  lastAnalysis?: LastAnalysisPayload | null;
  activeAnalysisId?: string | null;
};

export type MyRepositoriesPayload =
  | MyRepositoryPayload[]
  | { items?: MyRepositoryPayload[]; repositories?: MyRepositoryPayload[] };

export function toMyRepositories(payload: MyRepositoriesPayload): MyRepositoriesResponse {
  const rows = Array.isArray(payload) ? payload : (payload.items ?? payload.repositories ?? []);
  return { items: rows.flatMap((row) => (row.repository?.id ?? row.id ? [toMyRepository(row)] : [])) };
}

function toMyRepository(row: MyRepositoryPayload): MyRepository {
  const source = row.repository ?? row;
  const organizationSlug =
    source.organizationSlug ??
    (typeof source.organization === "string" ? source.organization : source.organization?.slug) ??
    (source.name?.includes("/") ? source.name.split("/")[0] : undefined) ??
    "";
  const repositorySlug = source.repositorySlug ?? source.slug ?? source.name?.split("/").pop() ?? "";
  const last = row.lastAnalysis ?? null;
  // Идущий анализ ещё не дал оценки: строка показывает «идёт анализ» и ведёт на его ход.
  const running = last !== null && !isAnalysisFinished(last.status);

  return {
    repository: {
      id: source.id ?? row.id ?? "",
      organizationSlug,
      repositorySlug,
      name: `${organizationSlug}/${repositorySlug}`,
      url: source.webUrl ?? source.url ?? null,
      description: source.description ?? null,
      language: source.language ?? null,
      visibility: source.visibility === "private" || source.visibility === "internal" ? "private" : "public",
      isEmpty: source.isEmpty === true,
    },
    lastAnalysis:
      last === null || running
        ? null
        : {
            id: last.id,
            status: last.status,
            analyzedAt: last.analyzedAt ?? last.finishedAt ?? last.createdAt ?? null,
            score: last.score ?? null,
            isPreliminary: last.isPreliminary ?? false,
          },
    activeAnalysisId: row.activeAnalysisId ?? (running && last !== null ? last.id : null),
  };
}

/** Адрес входа: backend уводит на Яндекс ID и после входа возвращает в кабинет. */
export function yandexSignInUrl(): string {
  return "/api/v1/auth/yandex/start";
}

export async function signOut(): Promise<void> {
  if (isDemoSession()) {
    mockSession.signOut();
    return;
  }
  await postJson<void>("/api/v1/auth/logout");
}
