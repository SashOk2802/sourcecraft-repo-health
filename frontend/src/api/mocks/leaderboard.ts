import type { LeaderboardItem, LeaderboardQuery, LeaderboardResponse, LeaderboardSort } from "../leaderboard";
import { mockAnalysisId, mockRepositories, type MockRepository } from "./catalog";
import { scoreMockCategories } from "./scoring";
import { minutesAgo, todayAt } from "./time";

/*
 * Имитация backend по правилам docs/leaderboard-policy.md: места, фильтры, сортировка и страницы.
 * Место считается до фильтров и только по Score полных оценок; поиск — по «организация/репозиторий».
 */
export function queryMockLeaderboard(query: LeaderboardQuery, pageSize: number): LeaderboardResponse {
  // В публичный рейтинг попадают только открытые репозитории.
  const publicRepositories = mockRepositories.filter(
    (repository) =>
      repository.visibility === "public" && !repository.awaitingFirstAnalysis && !repository.lastAnalysisFailed,
  );
  const analyzed = publicRepositories.flatMap((repository) =>
    repository.categories === null ? [] : [toItem(repository)],
  );

  // Место зависит только от Score и только среди полных оценок.
  const complete = analyzed.filter((item) => !item.isPreliminary && item.score !== null);
  rankByScore(complete);

  const search = query.search.toLowerCase();
  const matchesSearch = (item: LeaderboardItem): boolean => !search || item.repository.name.toLowerCase().includes(search);

  const languageCounts = new Map<string, number>();
  for (const item of analyzed.filter(matchesSearch)) {
    const language = item.repository.language;
    if (language) languageCounts.set(language, (languageCounts.get(language) ?? 0) + 1);
  }

  const matchesFilters = (item: LeaderboardItem): boolean =>
    matchesSearch(item) && (!query.language || item.repository.language === query.language);

  const filtered = complete.filter(matchesFilters).sort(comparators[query.sort]);
  const preliminary = analyzed
    .filter((item) => item.isPreliminary || item.score === null)
    .filter(matchesFilters)
    .sort(comparators[query.sort]);

  const start = (query.page - 1) * pageSize;

  return {
    items: filtered.slice(start, start + pageSize),
    preliminary: query.includePreliminary ? preliminary : [],
    total: filtered.length,
    preliminaryTotal: preliminary.length,
    page: query.page,
    pageSize,
    languages: [...languageCounts]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    updatedAt: todayAt(6),
    pendingCount: publicRepositories.filter((repository) => repository.categories === null).length,
    methodologyVersion: "v1",
  };
}

/** Спортивные места, как в backend/app/leaderboard/policy.py: равный Score — общее место, 1, 2, 2, 4. */
export function rankByScore(items: LeaderboardItem[]): void {
  const ordered = [...items].sort(
    (a, b) => (b.score ?? 0) - (a.score ?? 0) || a.repository.id.localeCompare(b.repository.id),
  );
  let place = 0;
  let previousScore: number | null = null;
  ordered.forEach((item, index) => {
    if (item.score !== previousScore) {
      place = index + 1;
      previousScore = item.score;
    }
    item.place = place;
  });
}

function toItem(repository: MockRepository): LeaderboardItem {
  const result = scoreMockCategories(repository.categories!, repository.scoreLimit);
  return {
    place: null,
    analysisId: mockAnalysisId(repository),
    repository: {
      id: repository.id,
      organizationSlug: repository.organizationSlug,
      repositorySlug: repository.repositorySlug,
      name: `${repository.organizationSlug}/${repository.repositorySlug}`,
      url: `https://sourcecraft.dev/${repository.organizationSlug}/${repository.repositorySlug}`,
      description: repository.description,
      language: repository.language,
    },
    score: result.score,
    coverage: result.coverage,
    isPreliminary: result.isPreliminary,
    scoreLimited:
      repository.scoreLimit !== undefined &&
      result.uncappedScore !== null &&
      result.uncappedScore > repository.scoreLimit,
    likes: repository.likes,
    lastActivityAt: repository.lastActivityAt,
    analyzedAt: minutesAgo(190),
    categories: result.categories.map(({ code, label, status, score }) => ({ code, label, status, score })),
  };
}

const byPlace = (a: LeaderboardItem, b: LeaderboardItem): number =>
  (a.place ?? Number.MAX_SAFE_INTEGER) - (b.place ?? Number.MAX_SAFE_INTEGER) ||
  (b.score ?? -1) - (a.score ?? -1) ||
  a.repository.id.localeCompare(b.repository.id);

const comparators: Record<LeaderboardSort, (a: LeaderboardItem, b: LeaderboardItem) => number> = {
  score: byPlace,
  likes: (a, b) => (b.likes ?? -1) - (a.likes ?? -1) || byPlace(a, b),
  activity: (a, b) => Date.parse(b.lastActivityAt ?? "") - Date.parse(a.lastActivityAt ?? "") || byPlace(a, b),
};
