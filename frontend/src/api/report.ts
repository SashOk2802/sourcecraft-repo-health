import type { AnalysisStatus, CategoryStatus, Evidence, RecommendationPriority } from "./common";
import { usesDemo, withDemoDelay } from "./dataSource";
import { ApiError, getJson, getText } from "./http";
import { findMockReport } from "./mocks/reports";
import { renderReportMarkdown } from "../lib/reportMarkdown";

/*
 * GET /api/v1/analyses/{analysisId}/report — docs/api-contract.md, раздел «Формат JSON-отчёта».
 * Отчёт привязан к неизменяемому снимку анализа, поэтому запрашивается по его идентификатору.
 * Веса и вклад категорий приходят в процентах методики, coverage — долей от 0 до 1.
 */

export interface RepositoryReport {
  repository: ReportRepository;
  /** README-бейдж доступен только для репозитория, подтверждённого как публичный. */
  badgeAvailable: boolean;
  analysis: ReportAnalysis;
  /** 0–100 или null. Ограничение Score, если оно есть, уже применено. */
  score: number | null;
  scoreDetails: ScoreDetails;
  categories: ReportCategory[];
  /** Уже отсортированы ядром по приоритету. */
  recommendations: Recommendation[];
}

export interface ReportRepository {
  id: string;
  organizationSlug: string;
  repositorySlug: string;
  name: string;
  url: string | null;
}

export interface ReportAnalysis {
  id: string;
  status: AnalysisStatus;
  analyzedAt: string;
  commitSha: string | null;
  methodologyVersion: string;
  /** Доля измеренного веса среди применимых категорий; null — применимых категорий нет. */
  coverage: number | null;
  /** Данные есть не по всем применимым категориям. */
  isPreliminary: boolean;
  scoreLimit: ScoreLimit | null;
}

export interface ScoreDetails {
  /** Вес измеренных категорий, проценты методики. */
  measuredWeight: number;
  /** Вес применимых категорий, проценты методики. */
  applicableWeight: number;
}

export interface ScoreLimit {
  /** Выше этого значения Score не поднимется, пока причина не устранена. */
  value: number;
  /** Каким был бы Score без ограничения. */
  uncappedScore: number;
  code: string;
  summary: string;
}

export interface ReportCategory {
  code: string;
  label: string;
  status: CategoryStatus;
  score: number | null;
  /** Вес категории в методике, проценты. */
  weight: number;
  /** Вес среди измеренных категорий, проценты; null — категория не участвовала в Score. */
  effectiveWeight: number | null;
  /** Вклад категории в Score; null — не участвовала. */
  points: number | null;
  summary: string;
  /** Машинный код причины, по которой оценки нет. */
  reason: string | null;
  /** Метрики, из которых сложилась оценка категории. */
  evidence: CategoryMetric[];
}

export interface CategoryMetric {
  code: string;
  value: number | string | null;
  normalizedScore: number | null;
  summary: string;
  /** Ссылки на прогоны, задачи, файлы и находки AppSec. */
  evidence: Evidence[];
}

export interface Recommendation {
  code: string;
  priority: RecommendationPriority;
  problem: string;
  action: string;
  rationale: string;
  expectedEffect: string | null;
  /** На сколько баллов вырастет Score; null — оценить надёжно нельзя. */
  expectedScoreDelta: number | null;
  evidence: Evidence[];
}

export async function fetchReport(analysisId: string): Promise<RepositoryReport> {
  if (usesDemo(analysisId)) {
    const report = findMockReport(analysisId);
    if (!report) {
      throw new ApiError(404, "Отчёт не найден");
    }
    return withDemoDelay(report);
  }
  return getJson<RepositoryReport>(`/api/v1/analyses/${encodeURIComponent(analysisId)}/report`);
}

/** Markdown по тому же снимку анализа. */
export function markdownReportUrl(analysisId: string): string {
  return `/api/v1/analyses/${encodeURIComponent(analysisId)}/report.md`;
}

/**
 * Текст Markdown-отчёта. Настоящий отдаёт backend; демо-отчёт собирается здесь же
 * в том же формате, что и backend/app/reporting/builder.py.
 */
export async function fetchReportMarkdown(report: RepositoryReport): Promise<string> {
  if (usesDemo(report.analysis.id)) {
    return renderReportMarkdown(report, { demo: true });
  }
  return getText(markdownReportUrl(report.analysis.id));
}
