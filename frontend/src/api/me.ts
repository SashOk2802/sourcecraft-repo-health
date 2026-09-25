import type { AnalysisStatus } from "./common";
import { ApiError, getJson, postJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import { mockMyRepositories } from "./mocks/me";
import { mockSession } from "./mocks/session";

/*
 * Пользователь — docs/api-contract.md, «Вход через Яндекс ID»: GET /api/v1/me отдаёт
 * { id, login }, без сессии — 401, если вход не настроен — 503. Вход выполняет backend:
 * интерфейс только уводит на /api/v1/auth/yandex/start, после входа backend сам возвращает
 * на /me/repositories. Токенов интерфейс не видит.
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

/** null — пользователь не вошёл (backend ответил 401). */
export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  if (mocksEnabled) {
    return withMockDelay(mockSession.user(), 120);
  }
  try {
    return toCurrentUser(await getJson<MePayload>("/api/v1/me"));
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function fetchMyRepositories(): Promise<MyRepositoriesResponse> {
  if (mocksEnabled) {
    const items = mockMyRepositories();
    if (items === null) throw new ApiError(401, "Нужно войти");
    return withMockDelay({ items });
  }
  return getJson<MyRepositoriesResponse>("/api/v1/me/repositories");
}

/** Адрес входа: backend уводит на Яндекс ID и после входа возвращает на returnTo. */
export function yandexSignInUrl(returnTo: string): string {
  return `/api/v1/auth/yandex/start?returnTo=${encodeURIComponent(returnTo)}`;
}

export async function signOut(): Promise<void> {
  if (mocksEnabled) {
    mockSession.signOut();
    return;
  }
  await postJson<void>("/api/v1/auth/logout");
}
