import type { AnalysisStatus } from "./common";
import { ApiError, getJson, postJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import {
  findMockRepositoryByAnalysisId,
  mockAnalysisId,
  mockRepositories,
  type MockRepository,
} from "./mocks/catalog";
import { findMockReport } from "./mocks/reports";

/**
 * GET /api/v1/me/repositories.
 *
 * В первой версии backend возвращает публичный каталог организаций из
 * SOURCECRAFT_PUBLIC_ORGANIZATIONS. Идентификатор репозитория непрозрачный:
 * frontend передаёт его в endpoint запуска как есть.
 */
export interface MyRepository {
  id: string;
  organizationSlug: string;
  repositorySlug: string;
  name: string;
  url: string | null;
  defaultBranch: string | null;
  language: string | null;
  isEmpty: boolean;
}

export interface MyRepositoriesResponse {
  repositories: MyRepository[];
  total: number;
}

export interface AnalysisJobError {
  code: string;
  summary: string;
}

/** Ответ POST /repositories/{id}/analyses и GET /analyses/{id}. */
export interface AnalysisJob {
  id: string;
  status: AnalysisStatus;
  repository: { id: string; name?: string };
  score: number | null;
  isPreliminary: boolean | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: AnalysisJobError | null;
  reportUrl: string | null;
  markdownReportUrl: string | null;
}

const terminalStatuses: AnalysisStatus[] = ["completed", "partial", "failed", "cancelled"];

export function isAnalysisTerminal(status: AnalysisStatus): boolean {
  return terminalStatuses.includes(status);
}

export async function fetchMyRepositories(): Promise<MyRepositoriesResponse> {
  if (mocksEnabled) {
    const repositories = mockRepositories
      .filter((repository) => repository.visibility === "public")
      .map(toMyRepository);
    return withMockDelay({ repositories, total: repositories.length });
  }
  return getJson<MyRepositoriesResponse>("/api/v1/me/repositories");
}

export async function startRepositoryAnalysis(repositoryId: string): Promise<AnalysisJob> {
  if (!mocksEnabled) {
    return postJson<AnalysisJob>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/analyses`);
  }

  const repository = mockRepositories.find((item) => item.id === repositoryId);
  if (!repository || repository.visibility !== "public") {
    throw new ApiError(404, "Репозиторий не найден");
  }

  const id = mockAnalysisId(repository);
  mockJobs.set(id, { repository, polls: 0 });
  return withMockDelay(makeMockJob(id, repository, "queued"));
}

export async function fetchAnalysisStatus(analysisId: string): Promise<AnalysisJob> {
  if (!mocksEnabled) {
    return getJson<AnalysisJob>(`/api/v1/analyses/${encodeURIComponent(analysisId)}`);
  }

  const existing = mockJobs.get(analysisId);
  const repository = existing?.repository ?? findMockRepositoryByAnalysisId(analysisId);
  if (!repository) {
    throw new ApiError(404, "Анализ не найден");
  }

  if (!existing) {
    return withMockDelay(mockFinishedJob(analysisId, repository));
  }

  existing.polls += 1;
  if (existing.polls === 1) {
    return withMockDelay(makeMockJob(analysisId, repository, "running"));
  }
  return withMockDelay(mockFinishedJob(analysisId, repository));
}

interface MockJobRecord {
  repository: MockRepository;
  polls: number;
}

/** В mock-режиме один запуск проходит queued → running → terminal. */
const mockJobs = new Map<string, MockJobRecord>();

function toMyRepository(repository: MockRepository): MyRepository {
  return {
    id: repository.id,
    organizationSlug: repository.organizationSlug,
    repositorySlug: repository.repositorySlug,
    name: `${repository.organizationSlug}/${repository.repositorySlug}`,
    url: `https://sourcecraft.dev/${repository.organizationSlug}/${repository.repositorySlug}`,
    defaultBranch: "main",
    language: repository.language,
    isEmpty: repository.categories === null,
  };
}

function makeMockJob(id: string, repository: MockRepository, status: AnalysisStatus): AnalysisJob {
  const now = new Date().toISOString();
  return {
    id,
    status,
    repository: { id: repository.id, name: `${repository.organizationSlug}/${repository.repositorySlug}` },
    score: null,
    isPreliminary: null,
    createdAt: now,
    startedAt: status === "queued" ? null : now,
    finishedAt: null,
    error: null,
    reportUrl: null,
    markdownReportUrl: null,
  };
}

function mockFinishedJob(id: string, repository: MockRepository): AnalysisJob {
  const report = findMockReport(id);
  const now = new Date().toISOString();

  if (!report) {
    return {
      ...makeMockJob(id, repository, "failed"),
      startedAt: now,
      finishedAt: now,
      error: {
        code: "analysis_data_unavailable",
        summary: "Для этого репозитория пока недостаточно данных, чтобы собрать отчёт.",
      },
    };
  }

  const status: AnalysisStatus = report.analysis.isPreliminary ? "partial" : "completed";
  return {
    ...makeMockJob(id, repository, status),
    score: report.score,
    isPreliminary: report.analysis.isPreliminary,
    startedAt: now,
    finishedAt: now,
    reportUrl: `/api/v1/analyses/${encodeURIComponent(id)}/report`,
    markdownReportUrl: `/api/v1/analyses/${encodeURIComponent(id)}/report.md`,
  };
}
