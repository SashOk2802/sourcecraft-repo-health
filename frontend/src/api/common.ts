/**
 * Общие типы ответов backend. Формат и правила описаны в docs/api-contract.md:
 * JSON в camelCase, даты — строки ISO 8601, доли — числа от 0 до 1.
 */

/** Доступность данных категории — categories[].status в контракте. */
export type CategoryStatus = "measured" | "unavailable" | "not_applicable" | "insufficient_sample" | "error";

/** Приоритет рекомендации: p0 — самый срочный. */
export type RecommendationPriority = "p0" | "p1" | "p2" | "p3";

/**
 * Статус запуска анализа — docs/api-contract.md, GET /api/v1/analyses/{id}.
 * Backend присылает queued, running, completed, partial или failed;
 * collecting и calculating — предложенные этапы сбора, cancelled — отмена.
 */
export type AnalysisStatus =
  | "queued"
  | "running"
  | "collecting"
  | "calculating"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

/** Оценка категории без подробностей — для рейтинга и списков. */
export interface CategoryBrief {
  code: string;
  label: string;
  status: CategoryStatus;
  /** 0–100 только при status = "measured", иначе null. */
  score: number | null;
}

/** Подтверждающий факт: файл, прогон CI, issue, уязвимость и т. п. */
export interface Evidence {
  source: string;
  reference: string;
  summary: string;
  url: string | null;
}
