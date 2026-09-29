/*
 * Адреса публичного API Repo Health (docs/public-api.md): JSON с оценкой и SVG-бейдж.
 * Работают без ключа и только для репозиториев, которые SourceCraft подтверждает как публичные.
 */

export interface RepositorySlug {
  organization: string;
  repository: string;
}

/** «org/repo», адрес репозитория в SourceCraft или null, если это не похоже на репозиторий. */
export function parseRepositorySlug(input: string): RepositorySlug | null {
  const trimmed = input.trim().replace(/^https?:\/\/[^/]+\//i, "").replace(/\/+$/, "");
  const parts = trimmed.split("/").filter(Boolean);
  if (parts.length !== 2) return null;
  const [organization, repository] = parts;
  return { organization, repository };
}

function slugPath({ organization, repository }: RepositorySlug): string {
  return `${encodeURIComponent(organization)}/${encodeURIComponent(repository)}`;
}

export function publicHealthPath(slug: RepositorySlug): string {
  return `/api/v1/public/repositories/${slugPath(slug)}/health`;
}

export function badgePath(slug: RepositorySlug): string {
  return `/api/v1/repositories/${slugPath(slug)}/badge.svg`;
}

/** Ответ API для показа: JSON с отступами, иначе текст как есть. */
export function formatApiBody(body: string): string {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}
