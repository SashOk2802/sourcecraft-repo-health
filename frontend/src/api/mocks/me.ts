import type { MyRepository } from "../me";
import { analysisRunState } from "./analyses";
import { mockAnalysisId, mockRepositories } from "./catalog";
import { activeRunFor, finishedAt, lastFinishedRunFor } from "./runs";
import { scoreMockCategories } from "./scoring";
import { mockSession } from "./session";
import { daysAgo, minutesAgo } from "./time";

/*
 * Демо-кабинет повторяет первый этап настоящего: после входа — публичные репозитории из
 * организаций, которые проверяет сервис (у backend это SOURCECRAFT_PUBLIC_ORGANIZATIONS).
 */
const catalogOrganizations = ["gorod-dev", "shkola-it"];

/** null — пользователь не вошёл. */
export function mockMyRepositories(now = Date.now()): MyRepository[] | null {
  if (!mockSession.isSignedIn()) {
    return null;
  }

  const catalog = mockRepositories.filter(
    (repository) => repository.visibility === "public" && catalogOrganizations.includes(repository.organizationSlug),
  );

  return catalog.flatMap((repository): MyRepository[] => {
    const id = repository.id;
    if (repository.categories === null) {
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
          isEmpty: false,
        },
        lastAnalysis,
        activeAnalysisId: active?.id ?? null,
      },
    ];
  });
}
