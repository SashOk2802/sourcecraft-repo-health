/*
 * Часть экранов опирается на endpoint, которых backend ещё не поднял
 * (docs/api-contract.md: реализованы healthcheck и отчёт по снимку анализа).
 * Пока endpoint нет, интерфейс не уводит на 404, а показывает элемент выключенным.
 * Включается переменной окружения, когда backend будет готов.
 */

/** Вход через Яндекс ID: `/api/v1/auth/yandex/*`. */
export function isYandexAuthReady(value: string | undefined): boolean {
  return value === "true" || value === "1";
}

export const yandexAuthReady = isYandexAuthReady(import.meta.env.VITE_YANDEX_AUTH);

/** Что показать вместо перехода, пока вход не подключён. */
export const yandexAuthPendingHint =
  "Вход через Яндекс ID появится, когда backend поднимет /api/v1/auth/yandex";
