import { ApiError } from "../http";
import type { SourceCraftConnection } from "../connections";
import { readItem, removeItem, writeItem } from "./storage";

const CONNECTION_KEY = "repo-health:mock-sourcecraft";

// Для демо: ?mock-connected=1 сразу подключает SourceCraft в mock-режиме.
if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mock-connected") === "1") {
  writeItem(CONNECTION_KEY, new Date().toISOString());
}

export function mockConnection(): SourceCraftConnection {
  const connectedAt = readItem(CONNECTION_KEY);
  return connectedAt
    ? { connected: true, login: "a-kovaleva", connectedAt }
    : { connected: false, login: null, connectedAt: null };
}

/**
 * Backend проверяет токен запросом к SourceCraft. В mock-режиме считаем валидным
 * любой непустой токен длиной от 12 символов — чтобы можно было пройти сценарий и увидеть ошибку.
 */
export function connectMockSourceCraft(token: string): SourceCraftConnection {
  if (token.trim().length < 12) {
    throw new ApiError(400, "SourceCraft не принял токен: проверьте, что скопировали его целиком.");
  }
  writeItem(CONNECTION_KEY, new Date().toISOString());
  return mockConnection();
}

export function disconnectMockSourceCraft(): void {
  removeItem(CONNECTION_KEY);
}

export function isMockSourceCraftConnected(): boolean {
  return readItem(CONNECTION_KEY) !== null;
}
