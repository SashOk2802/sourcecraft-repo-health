/**
 * Mock-режим нужен только для изолированной вёрстки без backend.
 * Docker-окружение задаёт VITE_USE_MOCKS=false, поэтому демо использует настоящий API.
 */
export const mocksEnabled: boolean = import.meta.env.VITE_USE_MOCKS !== "false";

/** Небольшая задержка, чтобы в mock-режиме были видны состояния загрузки. */
export function withMockDelay<T>(value: T, delayMs = 300): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), delayMs));
}
