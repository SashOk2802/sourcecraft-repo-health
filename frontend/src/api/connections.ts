import { isDemoSession } from "./dataSource";
import { ApiError, getJson, postJson, request } from "./http";

/*
 * Подключение SourceCraft по личному токену (docs/frontend-review-response.md, раздел 4):
 *   GET    /api/v1/connections/sourcecraft — состояние подключения
 *   POST   /api/v1/connections/sourcecraft — отправить токен один раз
 *   DELETE /api/v1/connections/sourcecraft — отключить
 *
 * Яндекс ID подтверждает личность, но доступа к репозиториям SourceCraft не даёт.
 * Токен уходит на backend один раз, хранится зашифрованно и в браузер не возвращается.
 *
 * Хранилище токенов backend включает ключ SOURCECRAFT_CONNECTION_ENCRYPTION_KEY (PR #88); без
 * него GET отвечает 404, и форму подключения кабинет не показывает. Список закрытых
 * репозиториев backend добавит следующим этапом: пока GET /api/v1/me/repositories отдаёт
 * только публичный каталог. В демо-кабинете подключения нет.
 */

export interface SourceCraftConnection {
  connected: boolean;
  /** Логин в SourceCraft, под которым проверен токен. */
  login: string | null;
  connectedAt: string | null;
}

/**
 * null — подключения на этом сервере нет: хранилище не настроено («SourceCraft connection is
 * not configured.») или у backend нет такого раздела. Оба ответа — 404.
 */
export async function fetchSourceCraftConnection(): Promise<SourceCraftConnection | null> {
  if (isDemoSession()) {
    return null;
  }
  try {
    return await getJson<SourceCraftConnection>("/api/v1/connections/sourcecraft");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function connectSourceCraft(token: string): Promise<SourceCraftConnection> {
  return postJson<SourceCraftConnection>("/api/v1/connections/sourcecraft", { token });
}

export async function disconnectSourceCraft(): Promise<void> {
  await request<void>("/api/v1/connections/sourcecraft", { method: "DELETE" });
}
