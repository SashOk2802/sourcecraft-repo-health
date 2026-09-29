/** Запросы к backend. Пути только относительные: /api проксирует Vite в разработке и nginx на стенде. */

export class ApiError extends Error {
  /** HTTP-статус ответа; 0 — сервер не ответил. */
  readonly status: number;
  /**
   * У backend нет такого маршрута: FastAPI ответил 404 «Not Found» без своего текста,
   * или вместо API ответила статика. Это «раздел ещё не сделан», а не «ресурс не найден».
   */
  readonly routeMissing: boolean;

  constructor(status: number, message: string, { routeMissing = false }: { routeMissing?: boolean } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.routeMissing = routeMissing;
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
  const response = await send(path, init, "application/json");
  if (response.status === 204) {
    return undefined as T;
  }
  if (!isJson(response)) {
    // Например, index.html от статического хостинга: API за этим адресом нет.
    throw new ApiError(response.status, "Сервер ответил не данными API", { routeMissing: true });
  }
  return (await response.json()) as T;
}

/** Текстовый ответ — Markdown-отчёт. */
export async function getText(path: string): Promise<string> {
  const response = await send(path, { method: "GET" }, "text/markdown, text/plain");
  if (response.headers.get("Content-Type")?.includes("text/html")) {
    throw new ApiError(response.status, "Сервер ответил не данными API", { routeMissing: true });
  }
  return response.text();
}

async function send(path: string, init: RequestInit, accept: string): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", accept);

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Сервер не отвечает");
  }

  if (!response.ok) {
    const failure = await readFailure(response);
    throw new ApiError(response.status, failure.message, {
      routeMissing: isRouteMissing(response.status, failure),
    });
  }
  return response;
}

interface Failure {
  message: string;
  /** Поле detail из ответа FastAPI; null — тела нет или это не JSON. */
  detail: string | null;
  json: boolean;
}

async function readFailure(response: Response): Promise<Failure> {
  const fallback = response.statusText || `HTTP ${response.status}`;
  if (!isJson(response)) {
    return { message: fallback, detail: null, json: false };
  }
  try {
    // FastAPI кладёт текст ошибки в поле detail.
    const body = (await response.json()) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : null;
    return { message: detail ?? fallback, detail, json: true };
  } catch {
    return { message: fallback, detail: null, json: false };
  }
}

/**
 * Неизвестный маршрут FastAPI отвечает ровно `{"detail": "Not Found"}`, а свои 404
 * backend пишет иначе («Analysis not found.»). 405 и 501 — метод для адреса не сделан.
 */
export function isRouteMissing(status: number, failure: Pick<Failure, "detail" | "json">): boolean {
  if (status === 404) return !failure.json || failure.detail === "Not Found";
  return status === 405 || status === 501;
}

function isJson(response: Response): boolean {
  return response.headers.get("Content-Type")?.includes("json") ?? false;
}

/*
 * Ошибки личного подключения SourceCraft (backend PR #88, #101, #102) узнаём по тексту detail:
 * статус у них общий с другими причинами. 401 значит и «сессия Яндекс ID закончилась», и
 * «SourceCraft отклонил токен», а 503 — и «сервис не настроен», и «SourceCraft не ответил».
 */
const sourceCraftDetails: Array<[pattern: RegExp, text: string]> = [
  [
    /connection must be renewed/i,
    "Токен SourceCraft больше не действует. Отключите SourceCraft и подключите заново с новым токеном.",
  ],
  [/rejected the token/i, "SourceCraft не принял токен: проверьте, что он скопирован целиком и не отозван."],
  [/token has an invalid format/i, "Это не похоже на токен SourceCraft — проверьте, что скопировали его целиком."],
  [/token could not be verified/i, "SourceCraft сейчас не может проверить токен. Попробуйте через несколько минут."],
  [/connection is not configured/i, "Подключение SourceCraft на этом сервере не настроено."],
  [
    /connect SourceCraft to analyze/i,
    "Это закрытый или внутренний репозиторий: чтобы его проверить, подключите SourceCraft по личному токену.",
  ],
  [/connection is unavailable/i, "SourceCraft сейчас не отвечает по вашему подключению. Попробуйте через несколько минут."],
];

/** Объяснение ошибки личного подключения SourceCraft; null — ошибка другая. */
export function describeSourceCraftError(error: Error): string | null {
  if (!(error instanceof ApiError)) return null;
  return sourceCraftDetails.find(([pattern]) => pattern.test(error.message))?.[1] ?? null;
}

/*
 * 503 «раздел не настроен» и «SourceCraft не отдал каталог» (backend/app/main.py) — это не
 * «на сервере что-то сломалось»: так отвечает сервер без каталога открытых репозиториев.
 */
const serviceDetails: Array<[pattern: RegExp, text: string]> = [
  [/leaderboard is not configured/i, "Рейтинг на этом сервере пока не настроен: не задан каталог открытых репозиториев."],
  [/repository catalog is not configured/i, "Каталог открытых репозиториев на этом сервере не настроен."],
  [/repository catalog is unavailable/i, "SourceCraft сейчас не отдаёт список репозиториев. Попробуйте через несколько минут."],
];

/** 503 «Каталог открытых репозиториев не настроен»: в кабинете тогда помогает только личное подключение. */
export function isCatalogNotConfigured(error: Error): boolean {
  return error instanceof ApiError && error.status === 503 && /repository catalog is not configured/i.test(error.message);
}

/** 503 «Рейтинг не настроен»: у сервера нет каталога открытых репозиториев. Повтор запроса не поможет. */
export function isLeaderboardNotConfigured(error: Error): boolean {
  return error instanceof ApiError && error.status === 503 && /leaderboard is not configured/i.test(error.message);
}

/** Понятное человеку объяснение ошибки загрузки. */
export function describeError(error: Error): string {
  const sourceCraft = describeSourceCraftError(error);
  if (sourceCraft) return sourceCraft;
  const service = error instanceof ApiError ? serviceDetails.find(([pattern]) => pattern.test(error.message)) : undefined;
  if (service) return service[1];
  if (error instanceof ApiError) {
    if (error.status === 0) return "Сервер не отвечает. Проверьте подключение и попробуйте ещё раз.";
    if (error.status === 401) return "Нужно войти через Яндекс ID.";
    if (error.status === 403) return "У вашей учётной записи нет доступа к этим данным.";
    if (error.status === 404) return "Ничего не нашлось: возможно, ссылка устарела или анализ ещё не проводился.";
    if (error.status === 422) return "Сервер не принял запрос. Обновите страницу и попробуйте ещё раз.";
    if (error.status === 503) return "Сервис временно недоступен. Попробуйте ещё раз через пару минут.";
    if (error.status >= 500) return "На сервере что-то сломалось. Попробуйте ещё раз через минуту.";
  }
  return error.message || "Неизвестная ошибка.";
}
