import type { AnalysisStatus } from "./common";
import { ApiError, getJson, postJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import { mockMyRepositories } from "./mocks/me";
import { mockSession } from "./mocks/session";

/*
 * Пользователь и его репозитории — предложение к docs/api-contract.md.
 * Вход через Яндекс ID выполняет backend: интерфейс только уводит на /api/v1/auth/yandex/start
 * и не видит токенов.
 */

export interface CurrentUser {
  id: string;
  displayName: string;
  login: string | null;
  avatarUrl: string | null;
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
    return await getJson<CurrentUser>("/api/v1/me");
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
