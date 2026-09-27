import type { AnalysisStage, AnalysisStatusResponse, StartedAnalysis } from "../analyses";
import type { AnalysisStatus } from "../common";
import { markdownReportUrl } from "../report";
import { mockAnalysisId, mockRepositories, type MockCategoryCode, type MockRepository } from "./catalog";
import { activeRunFor, addRun, findRun, finishedAt, MOCK_ANALYSIS_DURATION_MS, type MockRun } from "./runs";
import { scoreMockCategories } from "./scoring";

interface StagePlan {
  code: string;
  label: string;
  /** Через сколько миллисекунд после запуска этап заканчивается. */
  doneAt: number;
  /** Категория, которая зависит от источника: её статус решает, удался ли этап. */
  category?: MockCategoryCode;
  okSummary: string;
}

const stagePlan: StagePlan[] = [
  { code: "queue", label: "Очередь", doneAt: 1_500, okSummary: "обработчик взял задание" },
  { code: "git", label: "Git-история и файлы", doneAt: 3_800, category: "documentation", okSummary: "история за 12 месяцев" },
  { code: "ci", label: "CI-прогоны", doneAt: 5_400, category: "cicd", okSummary: "40 последних прогонов" },
  { code: "issues", label: "Issues и merge requests", doneAt: 6_900, category: "issues", okSummary: "за 12 месяцев" },
  { code: "appsec", label: "AppSec", doneAt: 8_300, category: "security", okSummary: "результаты последнего скана" },
  { code: "score", label: "Расчёт оценки и рекомендаций", doneAt: MOCK_ANALYSIS_DURATION_MS, okSummary: "готово" },
];

export function startMockAnalysis(repositoryId: string, now = Date.now()): StartedAnalysis | null {
  const repository = mockRepositories.find((item) => item.id === repositoryId);
  if (!repository || repository.categories === null) {
    return null;
  }
  const run = activeRunFor(repositoryId, now) ?? addRun(repositoryId, now);
  return { id: run.id, status: analysisRunState(run, repository, now).status };
}

export function fetchMockAnalysis(analysisId: string, now = Date.now()): AnalysisStatusResponse | null {
  const run = findRun(analysisId);
  if (run) {
    const repository = mockRepositories.find((item) => item.id === run.repositoryId);
    return repository ? analysisRunState(run, repository, now) : null;
  }

  // Плановый анализ из каталога: он давно завершён.
  const repository = mockRepositories.find((item) => mockAnalysisId(item) === analysisId);
  if (!repository || repository.categories === null || repository.awaitingFirstAnalysis || repository.lastAnalysisFailed) {
    return null;
  }
  const result = scoreMockCategories(repository.categories, repository.scoreLimit);
  return {
    id: analysisId,
    status: result.status,
    repository: repositoryRef(repository),
    score: result.score,
    isPreliminary: result.isPreliminary,
    reportUrl: `/api/v1/analyses/${analysisId}/report`,
    markdownReportUrl: markdownReportUrl(analysisId),
    stages: stagePlan.map((plan) => ({
      code: plan.code,
      label: plan.label,
      status: stageStatus(plan, repository),
      summary: stageSummary(plan, repository),
    })),
  };
}

/** Состояние запуска в момент now — чистая функция, чтобы ход анализа было просто проверить. */
export function analysisRunState(run: MockRun, repository: MockRepository, now: number): AnalysisStatusResponse {
  const elapsed = now - run.createdAt;

  const stages = stagePlan.map((plan, index): AnalysisStage => {
    const startedAt = index === 0 ? 0 : stagePlan[index - 1].doneAt;
    if (elapsed < startedAt) {
      return { code: plan.code, label: plan.label, status: "pending", summary: null };
    }
    if (elapsed < plan.doneAt) {
      return { code: plan.code, label: plan.label, status: "running", summary: null };
    }
    return {
      code: plan.code,
      label: plan.label,
      status: stageStatus(plan, repository),
      summary: stageSummary(plan, repository),
    };
  });

  const finished = elapsed >= MOCK_ANALYSIS_DURATION_MS;
  const result = finished && repository.categories ? scoreMockCategories(repository.categories, repository.scoreLimit) : null;

  const status: AnalysisStatus = finished
    ? (result?.status ?? "completed")
    : elapsed < stagePlan[0].doneAt
      ? "queued"
      : elapsed < stagePlan[stagePlan.length - 2].doneAt
        ? "collecting"
        : "calculating";

  return {
    id: run.id,
    status,
    repository: repositoryRef(repository),
    score: result?.score ?? null,
    isPreliminary: result?.isPreliminary ?? false,
    reportUrl: `/api/v1/analyses/${run.id}/report`,
    markdownReportUrl: markdownReportUrl(run.id),
    stages,
    createdAt: new Date(run.createdAt).toISOString(),
    finishedAt: finished ? finishedAt(run) : null,
    error: null,
  };
}

function stageStatus(plan: StagePlan, repository: MockRepository): AnalysisStage["status"] {
  const value = plan.category && repository.categories ? repository.categories[plan.category] : undefined;
  if (value === "unavailable") return "unavailable";
  if (value === "error") return "error";
  return "done";
}

function stageSummary(plan: StagePlan, repository: MockRepository): string | null {
  const status = stageStatus(plan, repository);
  if (status === "unavailable") return "источник не вернул данных";
  if (status === "error") return "SourceCraft API не ответил";
  return plan.okSummary;
}

function repositoryRef(repository: MockRepository): AnalysisStatusResponse["repository"] {
  return {
    id: repository.id,
    name: `${repository.organizationSlug}/${repository.repositorySlug}`,
    organizationSlug: repository.organizationSlug,
    repositorySlug: repository.repositorySlug,
  };
}
