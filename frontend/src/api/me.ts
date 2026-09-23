import type { AnalysisStatus } from "./common";
import { isDemoSession, sourceRouter, withDemoDelay } from "./dataSource";
import { ApiError, getJson, postJson } from "./http";
import { mockMyRepositories } from "./mocks/me";
import { mockSession } from "./mocks/session";

/*
 * Пользователь — docs/api-contract.md, «Вход через Яндекс ID»: GET /api/v1/me отдаёт
 * { id, login }, без сессии — 401, если вход не настроен — 503. Вход выполняет backend:
 * интерфейс только уводит на /api/v1/auth/yandex/start, после входа backend сам возвращает
 * на /me/repositories. Токенов интерфейс не видит.
 * Пока вход у backend не настроен или маршрута нет, в режиме auto работает демо-кабинет:
 * вход, подключение SourceCraft и анализ показаны на вымышленных репозиториях.
 * Репозитории пользователя (/api/v1/me/repositories) — пока предложение к контракту.
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
   * live — вход обслуживает backend; demo — демо-кабинет, пока у backend нет /me;
   * offline — режим api, а backend без входа или недоступен: работаем как гость.
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
  return getJson<MyRepositoriesResponse>("/api/v1/me/repositories");
}

/** Адрес входа: backend уводит на Яндекс ID и после входа возвращает на returnTo. */
export function yandexSignInUrl(returnTo: string): string {
  return `/api/v1/auth/yandex/start?returnTo=${encodeURIComponent(returnTo)}`;
}

export async function signOut(): Promise<void> {
  if (isDemoSession()) {
    mockSession.signOut();
    return;
  }
  await postJson<void>("/api/v1/auth/logout");
}
