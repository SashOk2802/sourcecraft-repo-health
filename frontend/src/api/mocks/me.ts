import type { MyRepository } from "../me";
import { analysisRunState } from "./analyses";
import { mockAnalysisId, mockRepositories } from "./catalog";
import { isMockSourceCraftConnected } from "./connections";
import { activeRunFor, finishedAt, lastFinishedRunFor } from "./runs";
import { scoreMockCategories } from "./scoring";
import { mockSession } from "./session";
import { daysAgo, minutesAgo } from "./time";

/** К этим репозиториям у mock-пользователя есть доступ в SourceCraft. */
const accessibleRepositoryIds = ["repo-2001", "repo-1008", "repo-2002", "repo-2003"];

/** null — пользователь не вошёл; пустой список — SourceCraft не подключён. */
export function mockMyRepositories(now = Date.now()): MyRepository[] | null {
  if (!mockSession.isSignedIn()) {
    return null;
  }
  if (!isMockSourceCraftConnected()) {
    return [];
  }

  return accessibleRepositoryIds.flatMap((id): MyRepository[] => {
    const repository = mockRepositories.find((item) => item.id === id);
    if (!repository || repository.categories === null) {
      return [];
    }

    const active = activeRunFor(id, now);
    const finished = lastFinishedRunFor(id, now);
    const result = scoreMockCategories(repository.categories, repository.scoreLimit);

    let lastAnalysis: MyRepository["lastAnalysis"] = null;
    if (finished) {
      const state = analysisRunState(finished, repository, now);
      lastAnalysis = {
        id: finished.id,
        status: state.status,
        analyzedAt: finishedAt(finished),
        score: state.score,
        isPreliminary: state.isPreliminary ?? false,
      };
    } else if (repository.lastAnalysisFailed) {
      lastAnalysis = {
        id: mockAnalysisId(repository),
        status: "failed",
        analyzedAt: daysAgo(2, 6, 4),
        score: null,
        isPreliminary: false,
      };
    } else if (!repository.awaitingFirstAnalysis) {
      lastAnalysis = {
        id: mockAnalysisId(repository),
        status: result.status,
        analyzedAt: minutesAgo(190),
        score: result.score,
        isPreliminary: result.isPreliminary,
      };
    }

    return [
      {
        repository: {
          id: repository.id,
          organizationSlug: repository.organizationSlug,
          repositorySlug: repository.repositorySlug,
          name: `${repository.organizationSlug}/${repository.repositorySlug}`,
          url: `https://sourcecraft.dev/${repository.organizationSlug}/${repository.repositorySlug}`,
          description: repository.description,
          language: repository.language,
          visibility: repository.visibility,
        },
        lastAnalysis,
        activeAnalysisId: active?.id ?? null,
      },
    ];
  });
}
