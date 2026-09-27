/** sessionStorage для mock-состояния: вход, подключение и запуски анализа переживают перезагрузку вкладки. */

const memory = new Map<string, string>();

function storage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    // Хранилище может быть запрещено настройками браузера.
    return null;
  }
}

export function readItem(key: string): string | null {
  return storage()?.getItem(key) ?? memory.get(key) ?? null;
}

export function writeItem(key: string, value: string): void {
  memory.set(key, value);
  try {
    storage()?.setItem(key, value);
  } catch {
    // Остаётся копия в памяти.
  }
}

export function removeItem(key: string): void {
  memory.delete(key);
  try {
    storage()?.removeItem(key);
  } catch {
    // Нечего удалять.
  }
}
