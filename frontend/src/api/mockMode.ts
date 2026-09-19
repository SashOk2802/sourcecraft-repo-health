/**
 * Пока backend не отдаёт нужные endpoint, интерфейс работает на mock-данных.
 * Чтобы ходить в настоящий API, запустите frontend с переменной VITE_USE_MOCKS=false.
 */
export const mocksEnabled: boolean = import.meta.env.VITE_USE_MOCKS !== "false";

/** Небольшая задержка, чтобы в mock-режиме были видны состояния загрузки. */
export function withMockDelay<T>(value: T, delayMs = 300): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), delayMs));
}
