import { getJson } from "./http";

/*
 * GET /api/v1/public/repositories/{org}/{repo}/history — docs/public-api.md.
 * Точки — уже сохранённые снимки анализов публичного репозитория, старые первыми, не больше 20.
 * Публичность backend проверяет по каталогу SourceCraft при каждом запросе: для закрытого,
 * внутреннего и неизвестного репозитория ответ — 404.
 */

export interface ScorePoint {
  analyzedAt: string;
  /** null — ни одну категорию не удалось измерить. */
  score: number | null;
  /** Доля веса категорий с данными, 0–1. */
  coverage: number | null;
  /** partial — оценка предварительная. */
  status: "completed" | "partial";
  methodologyVersion: string;
}

export async function fetchPublicHistory(organizationSlug: string, repositorySlug: string): Promise<ScorePoint[]> {
  const path = `/api/v1/public/repositories/${encodeURIComponent(organizationSlug)}/${encodeURIComponent(repositorySlug)}/history`;
  return toScorePoints(await getJson<unknown>(path));
}

/** Разбирает ответ; точку в неожиданном формате пропускаем, а не рисуем выдуманной. */
export function toScorePoints(payload: unknown): ScorePoint[] {
  const points = isObject(payload) && Array.isArray(payload.points) ? payload.points : [];
  return points.flatMap((point): ScorePoint[] => {
    if (!isObject(point)) return [];
    const { analyzedAt, score, coverage, status, methodologyVersion } = point;
    if (typeof analyzedAt !== "string" || Number.isNaN(Date.parse(analyzedAt))) return [];
    if (typeof methodologyVersion !== "string" || methodologyVersion === "") return [];
    if (status !== "completed" && status !== "partial") return [];
    if (score !== null && typeof score !== "number") return [];
    if (coverage !== null && typeof coverage !== "number") return [];
    return [{ analyzedAt, score, coverage, status, methodologyVersion }];
  });
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
