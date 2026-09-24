/*
 * Тема интерфейса: как в системе, светлая или тёмная.
 * Выбор хранится в браузере посетителя — это его удобство, а не данные сервиса,
 * поэтому хранилище может отказать (приватный режим, запрет cookie),
 * и тогда интерфейс просто следует системе.
 */

export type ThemePreference = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "rh-theme";

const preferences: readonly ThemePreference[] = ["system", "light", "dark"];

/** Всё незнакомое — «как в системе». */
export function parseThemePreference(value: string | null | undefined): ThemePreference {
  return preferences.includes(value as ThemePreference) ? (value as ThemePreference) : "system";
}

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem" | "removeItem">;

export function readThemePreference(storage: ReadableStorage | null): ThemePreference {
  if (!storage) {
    return "system";
  }
  try {
    return parseThemePreference(storage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

/** «Как в системе» не храним: пусть следующая загрузка тоже следует системе. */
export function saveThemePreference(storage: WritableStorage | null, preference: ThemePreference): void {
  if (!storage) {
    return;
  }
  try {
    if (preference === "system") {
      storage.removeItem(THEME_STORAGE_KEY);
    } else {
      storage.setItem(THEME_STORAGE_KEY, preference);
    }
  } catch {
    // Хранилище недоступно — выбор проживёт до перезагрузки, этого достаточно.
  }
}

/** Доступ к localStorage, который сам может бросить исключение. */
export function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
