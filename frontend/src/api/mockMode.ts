/**
 * Mocks нужны только для явно запрошенного демо-режима. Production-сборка без
 * флага должна получать данные из backend, а не незаметно подменять их fixtures.
 */
export function areMocksEnabled(value: string | undefined): boolean {
  return value === "true";
}

export const mocksEnabled = areMocksEnabled(import.meta.env.VITE_USE_MOCKS);

/** Небольшая задержка, чтобы в mock-режиме были видны состояния загрузки. */
export function withMockDelay<T>(value: T, delayMs = 300): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), delayMs));
}
