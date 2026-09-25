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
 * На первом этапе кабинет показывает публичные репозитории из каталога сервиса и без
 * подключения (GET /api/v1/me/repositories). Подключение понадобится для закрытых
 * репозиториев: пока backend его не поддерживает, интерфейс его не предлагает — ни в
 * живом режиме, ни в демо-кабинете.
 */

export interface SourceCraftConnection {
  connected: boolean;
  /** Логин в SourceCraft, под которым проверен токен. */
  login: string | null;
  connectedAt: string | null;
}

/** null — backend подключение SourceCraft к кабинету пока не поддерживает. */
export async function fetchSourceCraftConnection(): Promise<SourceCraftConnection | null> {
  if (isDemoSession()) {
    return null;
  }
  try {
    return await getJson<SourceCraftConnection>("/api/v1/connections/sourcecraft");
  } catch (error) {
    if (error instanceof ApiError && error.routeMissing) return null;
    throw error;
  }
}

export async function connectSourceCraft(token: string): Promise<SourceCraftConnection> {
  return postJson<SourceCraftConnection>("/api/v1/connections/sourcecraft", { token });
}

export async function disconnectSourceCraft(): Promise<void> {
  await request<void>("/api/v1/connections/sourcecraft", { method: "DELETE" });
}
