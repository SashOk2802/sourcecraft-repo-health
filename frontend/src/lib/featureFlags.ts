/*
 * Backend уже реализует Yandex OAuth. Кнопку входа включаем отдельной
 * переменной только в окружении, где заданы OAuth-переменные backend:
 * иначе пользователь получил бы заведомую ошибку конфигурации.
 */

/** Вход через Яндекс ID: `/api/v1/auth/yandex/*`. */
export function isYandexAuthReady(value: string | undefined): boolean {
  return value === "true" || value === "1";
}

export const yandexAuthReady = isYandexAuthReady(import.meta.env.VITE_YANDEX_AUTH);

/** Что показать вместо перехода, пока OAuth не настроен в окружении. */
export const yandexAuthPendingHint =
  "Вход через Яндекс ID станет доступен после настройки OAuth в окружении";
