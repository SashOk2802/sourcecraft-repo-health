import type { LeaderboardItem, LeaderboardQuery, LeaderboardResponse, LeaderboardSort } from "../leaderboard";
import { mockAnalysisId, mockRepositories, type MockRepository } from "./catalog";
import { scoreMockCategories } from "./scoring";
import { minutesAgo, todayAt } from "./time";

/** Имитация backend: места, фильтры, сортировка и страницы. */
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
  [...complete]
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .forEach((item, index) => {
      item.place = index + 1;
    });

  const search = query.search.toLowerCase();
  const matchesSearch = (item: LeaderboardItem): boolean =>
    !search || `${item.repository.name} ${item.repository.description ?? ""}`.toLowerCase().includes(search);

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
    page: query.page,
    pageSize,
    languages: [...languageCounts]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    updatedAt: todayAt(6),
    pendingCount: publicRepositories.filter((repository) => repository.categories === null).length,
  };
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
  (b.score ?? -1) - (a.score ?? -1);

const comparators: Record<LeaderboardSort, (a: LeaderboardItem, b: LeaderboardItem) => number> = {
  score: byPlace,
  likes: (a, b) => (b.likes ?? -1) - (a.likes ?? -1) || byPlace(a, b),
  activity: (a, b) => Date.parse(b.lastActivityAt ?? "") - Date.parse(a.lastActivityAt ?? "") || byPlace(a, b),
};
