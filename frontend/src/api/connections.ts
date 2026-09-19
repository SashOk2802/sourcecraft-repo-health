import { getJson, postJson, request } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import { connectMockSourceCraft, disconnectMockSourceCraft, mockConnection } from "./mocks/connections";

/*
 * Подключение SourceCraft по личному токену (docs/frontend-review-response.md, раздел 4):
 *   GET    /api/v1/connections/sourcecraft — состояние подключения
 *   POST   /api/v1/connections/sourcecraft — отправить токен один раз
 *   DELETE /api/v1/connections/sourcecraft — отключить
 *
 * Яндекс ID подтверждает личность, но доступа к репозиториям SourceCraft не даёт.
 * Токен уходит на backend один раз, хранится зашифрованно и в браузер не возвращается.
 */

export interface SourceCraftConnection {
  connected: boolean;
  /** Логин в SourceCraft, под которым проверен токен. */
  login: string | null;
  connectedAt: string | null;
}

export async function fetchSourceCraftConnection(): Promise<SourceCraftConnection> {
  if (mocksEnabled) {
    return withMockDelay(mockConnection(), 120);
  }
  return getJson<SourceCraftConnection>("/api/v1/connections/sourcecraft");
}

export async function connectSourceCraft(token: string): Promise<SourceCraftConnection> {
  if (mocksEnabled) {
    return withMockDelay(connectMockSourceCraft(token), 400);
  }
  return postJson<SourceCraftConnection>("/api/v1/connections/sourcecraft", { token });
}

export async function disconnectSourceCraft(): Promise<void> {
  if (mocksEnabled) {
    disconnectMockSourceCraft();
    return withMockDelay(undefined, 200);
  }
  await request<void>("/api/v1/connections/sourcecraft", { method: "DELETE" });
}
