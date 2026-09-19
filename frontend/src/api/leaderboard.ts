import type { CategoryBrief } from "./common";
import { getJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import { queryMockLeaderboard } from "./mocks/leaderboard";

/*
 * GET /api/v1/leaderboard — предложение к docs/api-contract.md, backend его ещё не реализовал.
 * Места, фильтры и сортировка считаются на backend.
 *
 * Полные и предварительные оценки приходят разными списками: сравнивать их местами нельзя
 * (docs/frontend-review-response.md, раздел 5).
 */

export type LeaderboardSort = "score" | "likes" | "activity";

export interface LeaderboardQuery {
  /** null — все языки. */
  language: string | null;
  sort: LeaderboardSort;
  search: string;
  /** Показывать ли блок предварительных оценок. */
  includePreliminary: boolean;
  page: number;
}

export interface LeaderboardResponse {
  /** Полные сопоставимые оценки, у каждой есть место. */
  items: LeaderboardItem[];
  /** Предварительные оценки: место не присваивается. */
  preliminary: LeaderboardItem[];
  /** Сколько полных оценок подходит под фильтры. */
  total: number;
  page: number;
  pageSize: number;
  languages: LanguageFacet[];
  /** Когда рейтинг пересчитывали в последний раз. */
  updatedAt: string | null;
  /** Открытые репозитории, которые ещё ждут первого анализа. */
  pendingCount: number;
}

export interface LeaderboardItem {
  /** Место по Repo Health Score; null — у предварительных оценок. */
  place: number | null;
  /** Снимок анализа, по которому открывается отчёт. */
  analysisId: string;
  repository: LeaderboardRepository;
  score: number | null;
  coverage: number | null;
  isPreliminary: boolean;
  /** Score ограничен из-за подтверждённой критической проблемы. */
  scoreLimited: boolean;
  likes: number | null;
  lastActivityAt: string | null;
  analyzedAt: string | null;
  categories: CategoryBrief[];
}

export interface LeaderboardRepository {
  id: string;
  organizationSlug: string;
  repositorySlug: string;
  name: string;
  url: string | null;
  description: string | null;
  language: string | null;
}

export interface LanguageFacet {
  name: string;
  count: number;
}

export const LEADERBOARD_PAGE_SIZE = 15;

export const defaultLeaderboardQuery: LeaderboardQuery = {
  language: null,
  sort: "score",
  search: "",
  includePreliminary: false,
  page: 1,
};

const sorts: LeaderboardSort[] = ["score", "likes", "activity"];

/** Фильтры живут в адресной строке, чтобы ссылкой на рейтинг можно было поделиться. */
export function parseLeaderboardQuery(search: string): LeaderboardQuery {
  const params = new URLSearchParams(search);
  const sort = params.get("sort");
  const page = Number(params.get("page"));
  return {
    language: params.get("language") || null,
    sort: sorts.includes(sort as LeaderboardSort) ? (sort as LeaderboardSort) : "score",
    search: params.get("q")?.trim() ?? "",
    includePreliminary: params.get("preliminary") === "1",
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

/** Обратное к parseLeaderboardQuery; значения по умолчанию в адрес не пишем. */
export function stringifyLeaderboardQuery(query: LeaderboardQuery): string {
  const params = new URLSearchParams();
  if (query.language) params.set("language", query.language);
  if (query.sort !== "score") params.set("sort", query.sort);
  if (query.search) params.set("q", query.search);
  if (query.includePreliminary) params.set("preliminary", "1");
  if (query.page > 1) params.set("page", String(query.page));
  const text = params.toString();
  return text ? `?${text}` : "";
}

export async function fetchLeaderboard(query: LeaderboardQuery): Promise<LeaderboardResponse> {
  if (mocksEnabled) {
    return withMockDelay(queryMockLeaderboard(query, LEADERBOARD_PAGE_SIZE));
  }
  const params = new URLSearchParams({
    sort: query.sort,
    page: String(query.page),
    pageSize: String(LEADERBOARD_PAGE_SIZE),
    includePreliminary: String(query.includePreliminary),
  });
  if (query.language) params.set("language", query.language);
  if (query.search) params.set("search", query.search);
  return getJson<LeaderboardResponse>(`/api/v1/leaderboard?${params.toString()}`);
}
