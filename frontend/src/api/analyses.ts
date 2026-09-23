import type { AnalysisStatus } from "./common";
import { usesDemo, withDemoDelay } from "./dataSource";
import { ApiError, getJson, postJson } from "./http";
import { fetchMockAnalysis, startMockAnalysis } from "./mocks/analyses";

/*
 * GET /api/v1/analyses/{id} — состояние снимка анализа (docs/api-contract.md).
 * Запуск анализа (POST /api/v1/repositories/{id}/analyses) и этапы сбора — предложение
 * к контракту: backend отдаёт их вместе с фоновым анализом.
 */

export interface AnalysisStatusResponse {
  id: string;
  status: AnalysisStatus;
  repository: {
    id: string;
    /** Пока анализ в очереди или идёт, backend присылает только id репозитория. */
    name?: string;
    organizationSlug?: string;
    repositorySlug?: string;
  };
  score: number | null;
  /** null, пока анализ не закончился. */
  isPreliminary: boolean | null;
  /** null, пока отчёт не сохранён (docs/api-contract.md). */
  reportUrl: string | null;
  markdownReportUrl: string | null;
  /** Предложение: этапы сбора, пока анализ идёт. */
  stages?: AnalysisStage[];
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  /** Только у failed: безопасный код и текст без токенов. */
  error?: AnalysisError | null;
}

export interface AnalysisError {
  /** Машинный код, например worker_interrupted. */
  code: string;
  /** Текст для человека: backend пишет его по-русски. */
  summary: string;
}

export interface AnalysisStage {
  code: string;
  label: string;
  status: "pending" | "running" | "done" | "unavailable" | "error";
  summary: string | null;
}

export interface StartedAnalysis {
  id: string;
  status: AnalysisStatus;
}

const finishedStatuses: AnalysisStatus[] = ["completed", "partial", "failed", "cancelled"];

export function isAnalysisFinished(status: AnalysisStatus): boolean {
  return finishedStatuses.includes(status);
}

/** Анализ из демо-данных открывается без backend: его идентификатор начинается с demo-. */
export async function fetchAnalysisStatus(analysisId: string): Promise<AnalysisStatusResponse> {
  if (usesDemo(analysisId)) {
    const run = fetchMockAnalysis(analysisId);
    if (!run) {
      throw new ApiError(404, "Анализ не найден");
    }
    return withDemoDelay(run, 150);
  }
  return getJson<AnalysisStatusResponse>(`/api/v1/analyses/${encodeURIComponent(analysisId)}`);
}

/**
 * Повторный запуск для того же репозитория возвращает уже идущий анализ, а не создаёт второй.
 * Демо-репозиторий запускает демо-анализ; настоящий всегда уходит в backend — даже из демо-кабинета.
 */
export async function startAnalysis(repositoryId: string): Promise<StartedAnalysis> {
  if (usesDemo(repositoryId)) {
    const started = startMockAnalysis(repositoryId);
    if (!started) {
      throw new ApiError(404, "Репозиторий не найден");
    }
    return withDemoDelay(started, 250);
  }
  return postJson<StartedAnalysis>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/analyses`);
}
