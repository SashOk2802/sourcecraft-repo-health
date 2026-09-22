import type { AnalysisStatus, CategoryBrief, CategoryStatus, RecommendationPriority } from "../api/common";
import { formatScore } from "./format";

/** Как называть статус категории в тексте. Отсутствие данных нигде не выглядит как оценка. */
export const categoryStatusLabels: Record<CategoryStatus, string> = {
  measured: "оценено",
  unavailable: "нет данных",
  not_applicable: "не применимо",
  insufficient_sample: "мало данных",
  error: "не удалось получить",
};

export const priorityLabels: Record<RecommendationPriority, string> = {
  p0: "Критично",
  p1: "Важно",
  p2: "Стоит сделать",
  p3: "Можно позже",
};

export const analysisStatusLabels: Record<AnalysisStatus, string> = {
  queued: "в очереди",
  running: "идёт анализ",
  collecting: "собираем данные",
  calculating: "считаем оценку",
  completed: "завершён",
  partial: "завершён частично",
  failed: "не удался",
  cancelled: "отменён",
};

/** «58 из 100» или «нет данных». */
export function describeCategoryScore(category: CategoryBrief): string {
  if (category.status === "measured" && category.score !== null) {
    return `${formatScore(category.score)} из 100`;
  }
  return categoryStatusLabels[category.status];
}
