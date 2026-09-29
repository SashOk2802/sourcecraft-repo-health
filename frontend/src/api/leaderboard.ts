import type { CategoryBrief } from "./common";
import { sourceRouter } from "./dataSource";
import { getJson } from "./http";
import { queryMockLeaderboard } from "./mocks/leaderboard";

/*
 * GET /api/v1/leaderboard — согласованный backend-контракт публичного рейтинга.
 * Места, фильтры и сортировка считаются на backend по правилам docs/leaderboard-policy.md:
 * место — только по Score среди полных оценок одной версии методики, равные делят место.
 * Если раздел недоступен (рейтинг на сервере не настроен или backend не отвечает), в режиме
 * auto рейтинг строится из демо-данных, и страница об этом говорит (src/api/dataSource.ts).
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
  /** Сколько предварительных оценок подходит под фильтры, даже если сам список не запрошен. */
  preliminaryTotal: number;
  page: number;
  pageSize: number;
  languages: LanguageFacet[];
  /** Когда рейтинг пересчитывали в последний раз. */
  updatedAt: string | null;
  /** Открытые репозитории, которые ещё ждут первого анализа. */
  pendingCount: number;
  /** Версия методики, оценки которой сравниваются; null — backend её не указал. */
  methodologyVersion: string | null;
}

export interface LeaderboardItem {
  /** Место по Repo Health Score; null — у предварительных оценок. */
  place: number | null;
  /** Снимок анализа, по которому открывается отчёт; null — backend его не прислал. */
  analysisId: string | null;
  repository: LeaderboardRepository;
  score: number | null;
  coverage: number | null;
  isPreliminary: boolean;
  /** Score ограничен из-за подтверждённой критической проблемы. */
  scoreLimited: boolean;
  likes: number | null;
  lastActivityAt: string | null;
  analyzedAt: string | null;
  /** Необычная активность из отчёта; на место не влияет. Старые снимки — false. */
  gamingSuspected: boolean;
  /** Короткая метка вроде «аномальная активность»; null если флага нет. */
  gamingLabel: string | null;
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

/**
 * Адрес рейтинга после смены фильтра. Остальные фильтры берутся из текущего адреса, а не из
 * того, что видел обработчик при создании: отложенный поиск не должен откатывать язык или
 * сортировку, выбранные, пока он ждал. Любая смена возвращает на первую страницу.
 */
export function patchLeaderboardSearch(currentSearch: string, patch: Partial<LeaderboardQuery>): string {
  return stringifyLeaderboardQuery({ ...parseLeaderboardQuery(currentSearch), page: 1, ...patch });
}

/** Последняя страница рейтинга; у пустого списка — первая. */
export function lastLeaderboardPage(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export async function fetchLeaderboard(query: LeaderboardQuery): Promise<LeaderboardResponse> {
  return sourceRouter.liveOrDemo(
    "leaderboard",
    () => fetchLiveLeaderboard(query),
    () => queryMockLeaderboard(query, LEADERBOARD_PAGE_SIZE),
  );
}

async function fetchLiveLeaderboard(query: LeaderboardQuery): Promise<LeaderboardResponse> {
  const params = new URLSearchParams({
    sort: query.sort,
    page: String(query.page),
    pageSize: String(LEADERBOARD_PAGE_SIZE),
    includePreliminary: String(query.includePreliminary),
  });
  if (query.language) params.set("language", query.language);
  if (query.search) params.set("search", query.search);
  return toLeaderboardResponse(await getJson<LeaderboardPayload>(`/api/v1/leaderboard?${params.toString()}`), query);
}

/*
 * Backend возвращает канонические поля из docs/api-contract.md. Поддержка исторических
 * entries, rank и плоской строки остаётся только для старых fixture и безопасной
 * деградации: отсутствующие данные интерфейс показывает как неизвестные.
 */
type LeaderboardRowPayload = Partial<Omit<LeaderboardItem, "repository">> & {
  rank?: number | null;
  repository?: Partial<LeaderboardRepository>;
  repositoryId?: string;
  organizationSlug?: string;
  repositorySlug?: string;
  name?: string;
  url?: string | null;
  description?: string | null;
  language?: string | null;
};

export interface LeaderboardPayload {
  items?: LeaderboardRowPayload[];
  entries?: LeaderboardRowPayload[];
  preliminary?: LeaderboardRowPayload[];
  preliminaryEntries?: LeaderboardRowPayload[];
  total?: number;
  preliminaryTotal?: number;
  page?: number;
  pageSize?: number;
  languages?: LanguageFacet[];
  updatedAt?: string | null;
  pendingCount?: number;
  methodologyVersion?: string | null;
}

export function toLeaderboardResponse(payload: LeaderboardPayload, query: LeaderboardQuery): LeaderboardResponse {
  const items = (payload.items ?? payload.entries ?? []).map(toLeaderboardItem);
  const preliminary = (payload.preliminary ?? payload.preliminaryEntries ?? []).map(toLeaderboardItem);
  return {
    items,
    preliminary,
    total: payload.total ?? items.length,
    preliminaryTotal: payload.preliminaryTotal ?? preliminary.length,
    page: payload.page ?? query.page,
    pageSize: payload.pageSize ?? LEADERBOARD_PAGE_SIZE,
    languages: payload.languages ?? languageFacets([...items, ...preliminary]),
    updatedAt: payload.updatedAt ?? null,
    pendingCount: payload.pendingCount ?? 0,
    methodologyVersion: payload.methodologyVersion ?? null,
  };
}

function toLeaderboardItem(row: LeaderboardRowPayload): LeaderboardItem {
  const organizationSlug = row.repository?.organizationSlug ?? row.organizationSlug ?? "";
  const repositorySlug = row.repository?.repositorySlug ?? row.repositorySlug ?? "";
  const name = row.repository?.name ?? row.name ?? `${organizationSlug}/${repositorySlug}`;
  const place = row.place ?? row.rank ?? null;
  return {
    place,
    analysisId: row.analysisId ?? null,
    repository: {
      id: row.repository?.id ?? row.repositoryId ?? name,
      organizationSlug,
      repositorySlug,
      name,
      url: row.repository?.url ?? row.url ?? null,
      description: row.repository?.description ?? row.description ?? null,
      language: row.repository?.language ?? row.language ?? null,
    },
    score: row.score ?? null,
    coverage: row.coverage ?? null,
    isPreliminary: row.isPreliminary ?? place === null,
    scoreLimited: row.scoreLimited ?? false,
    likes: row.likes ?? null,
    lastActivityAt: row.lastActivityAt ?? null,
    analyzedAt: row.analyzedAt ?? null,
    gamingSuspected: row.gamingSuspected ?? false,
    gamingLabel: row.gamingLabel ?? null,
    categories: row.categories ?? [],
  };
}

/** Языки для фильтра, если backend их не прислал: по строкам ответа, самые частые сверху. */
export function languageFacets(items: LeaderboardItem[]): LanguageFacet[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    const language = item.repository.language;
    if (language) counts.set(language, (counts.get(language) ?? 0) + 1);
  }
  return [...counts]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
