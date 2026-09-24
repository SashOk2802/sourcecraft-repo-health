/** Страницы приложения и разбор адресной строки. Без React, чтобы логику было просто тестировать. */

export type Route =
  | { page: "leaderboard" }
  | { page: "report"; organizationSlug: string; repositorySlug: string }
  | { page: "myRepositories" }
  | { page: "analysis"; analysisId: string }
  | { page: "methodology" }
  | { page: "notFound" };

export type PageName = Route["page"];

export const paths = {
  leaderboard: (): string => "/",
  report: (organizationSlug: string, repositorySlug: string): string =>
    `/repositories/${encodeURIComponent(organizationSlug)}/${encodeURIComponent(repositorySlug)}`,
  myRepositories: (): string => "/me/repositories",
  analysis: (analysisId: string): string => `/analyses/${encodeURIComponent(analysisId)}`,
  methodology: (): string => "/methodology",
};

export function matchRoute(pathname: string): Route {
  const parts = pathname.split("/").filter(Boolean).map(decodeSegment);

  if (parts.length === 0) {
    return { page: "leaderboard" };
  }

  const [first, second, third] = parts;

  if (first === "repositories" && parts.length === 3) {
    return { page: "report", organizationSlug: second, repositorySlug: third };
  }
  if (first === "me" && second === "repositories" && parts.length === 2) {
    return { page: "myRepositories" };
  }
  if (first === "analyses" && parts.length === 2) {
    return { page: "analysis", analysisId: second };
  }
  if (first === "methodology" && parts.length === 1) {
    return { page: "methodology" };
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
