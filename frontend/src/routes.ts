/** Страницы приложения и разбор адресной строки. Без React, чтобы логику было просто тестировать. */

export type Route =
  | { page: "leaderboard" }
  | { page: "analysis"; analysisId: string }
  | { page: "myRepositories" }
  | { page: "methodology" }
  | { page: "publicApi" }
  | { page: "notFound" };

export type PageName = Route["page"];

export const paths = {
  leaderboard: (): string => "/",
  /** Отчёт привязан к снимку анализа, поэтому адрес строится по его идентификатору. */
  analysis: (analysisId: string): string => `/analyses/${encodeURIComponent(analysisId)}`,
  myRepositories: (): string => "/me/repositories",
  methodology: (): string => "/methodology",
  /** Не /api/…: всё под /api уходит в backend (Vite proxy, nginx, Traefik). */
  publicApi: (): string => "/public-api",
};

export function matchRoute(pathname: string): Route {
  const parts = pathname.split("/").filter(Boolean).map(decodeSegment);

  if (parts.length === 0) {
    return { page: "leaderboard" };
  }

  const [first, second] = parts;

  if (first === "analyses" && parts.length === 2) {
    return { page: "analysis", analysisId: second };
  }
  if (first === "me" && second === "repositories" && parts.length === 2) {
    return { page: "myRepositories" };
  }
  if (first === "methodology" && parts.length === 1) {
    return { page: "methodology" };
  }
  if (first === "public-api" && parts.length === 1) {
    return { page: "publicApi" };
  }
  return { page: "notFound" };
}

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    // Битый %-код в ручной ссылке не должен ронять приложение.
    return segment;
  }
}
