/**
 * Чистый Docker-запуск остаётся на mock-данных без SourceCraft-конфигурации.
 * Настоящий API включают явно через VITE_USE_MOCKS=false после настройки backend.
 */
export const mocksEnabled: boolean = import.meta.env.VITE_USE_MOCKS !== "false";

/** Небольшая задержка, чтобы в mock-режиме были видны состояния загрузки. */
export function withMockDelay<T>(value: T, delayMs = 300): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), delayMs));
}
