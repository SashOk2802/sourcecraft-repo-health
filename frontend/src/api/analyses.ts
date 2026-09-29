import type { AnalysisStatus } from "./common";
import { usesDemo, withDemoDelay } from "./dataSource";
import { ApiError, describeError, describeSourceCraftError, getJson, postJson } from "./http";
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

/**
 * Почему не запустился анализ — по кодам POST /api/v1/repositories/{id}/analyses
 * (docs/api-contract.md). Закрытый или внутренний репозиторий backend проверяет по личному
 * подключению SourceCraft (PR #101): без него — 409, без прав у токена — 403.
 */
export function describeStartError(error: Error): string {
  // 409 и недоступное подключение — по тексту backend, как и остальные ошибки SourceCraft.
  const sourceCraft = describeSourceCraftError(error);
  if (sourceCraft) return sourceCraft;
  if (error instanceof ApiError) {
    if (error.status === 401) return "Сессия закончилась — войдите через Яндекс ID ещё раз.";
    // Защита от подделки запросов (docs/csrf-protection.md): POST пришёл не с адреса сервиса.
    if (error.status === 403 && /cross-site/i.test(error.message)) {
      return "Сервер отклонил запрос с этого адреса. Откройте сервис по его основному адресу и попробуйте снова.";
    }
    if (error.status === 403) {
      return "Нет доступа к этому репозиторию. Закрытые и внутренние проверяются по подключению SourceCraft — у токена должны быть права на репозиторий.";
    }
    if (error.status === 404) return "Такого репозитория нет в каталоге, который проверяет сервис.";
    if (error.status === 422) return "Такой идентификатор репозитория не подходит — проверьте, что скопировали его целиком.";
    // 429 — SourceCraft ограничил частоту запросов, 502 — его ответ не превратить в анализ.
    if (error.status === 429) return "SourceCraft просит подождать: слишком много запросов подряд. Попробуйте через минуту.";
    if (error.status === 502) return "SourceCraft вернул данные, по которым анализ не запустить. Попробуйте позже.";
    if (error.status === 503) {
      // backend/app/main.py: каталог SourceCraft не ответил — это временно; иначе запуск не настроен.
      return /catalog/i.test(error.message)
        ? "SourceCraft сейчас не отдаёт данные этого репозитория. Попробуйте через несколько минут."
        : "Запуск анализа на сервере пока не настроен. Попробуйте позже.";
    }
  }
  return describeError(error);
}
