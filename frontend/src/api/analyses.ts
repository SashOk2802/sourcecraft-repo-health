import type { AnalysisStatus } from "./common";
import { ApiError, getJson, postJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
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
    name: string;
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
  error?: string | null;
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

export async function fetchAnalysisStatus(analysisId: string): Promise<AnalysisStatusResponse> {
  if (mocksEnabled) {
    const run = fetchMockAnalysis(analysisId);
    if (!run) {
      throw new ApiError(404, "Анализ не найден");
    }
    return withMockDelay(run, 150);
  }
  return getJson<AnalysisStatusResponse>(`/api/v1/analyses/${encodeURIComponent(analysisId)}`);
}

/** Повторный запуск для того же репозитория возвращает уже идущий анализ, а не создаёт второй. */
export async function startAnalysis(repositoryId: string): Promise<StartedAnalysis> {
  if (mocksEnabled) {
    const started = startMockAnalysis(repositoryId);
    if (!started) {
      throw new ApiError(404, "Репозиторий не найден");
    }
    return withMockDelay(started, 250);
  }
  return postJson<StartedAnalysis>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/analyses`);
}
