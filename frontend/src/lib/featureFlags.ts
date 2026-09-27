/*
 * Вход через Яндекс ID backend уже умеет (/api/v1/auth/yandex/*), но без OAuth-переменных
 * YANDEX_CLIENT_ID, YANDEX_CLIENT_SECRET и YANDEX_REDIRECT_URI отвечает 503 «не настроен».
 * Поэтому кнопки входа включает VITE_YANDEX_AUTH=true — только там, где OAuth настроен.
 */

/** Вход через Яндекс ID: `/api/v1/auth/yandex/*`. */
export function isYandexAuthReady(value: string | undefined): boolean {
  return value === "true" || value === "1";
}

export const yandexAuthReady = isYandexAuthReady(import.meta.env.VITE_YANDEX_AUTH);

/** Что показать вместо перехода, пока вход в окружении не настроен. */
export const yandexAuthPendingHint = "Вход через Яндекс ID на этом сервере пока не настроен";
