/** Запросы к backend. Пути только относительные: Vite проксирует /api в контейнер backend. */

export class ApiError extends Error {
  /** HTTP-статус ответа; 0 — сервер не ответил. */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function getJson<T>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function request<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Сервер не отвечает");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Текстовый ответ — Markdown-отчёт. */
export async function getText(path: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "text/markdown, text/plain" }, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Сервер не отвечает");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }
  return response.text();
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    // FastAPI кладёт текст ошибки в поле detail.
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Тело не JSON — используем статус.
  }
  return response.statusText || `HTTP ${response.status}`;
}

/** Понятное человеку объяснение ошибки загрузки. */
export function describeError(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Сервер не отвечает. Проверьте, что backend запущен, и попробуйте ещё раз.";
    if (error.status === 401) return "Нужно войти через Яндекс ID.";
    if (error.status === 403) return "У вашей учётной записи нет доступа к этим данным.";
    if (error.status === 404) return "Ничего не нашлось: возможно, ссылка устарела или анализ ещё не проводился.";
    if (error.status >= 500) return "На сервере что-то сломалось. Попробуйте ещё раз через минуту.";
  }
  return error.message || "Неизвестная ошибка.";
}
