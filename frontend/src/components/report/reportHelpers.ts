import type { CategoryMetric, ReportCategory } from "../../api/report";
import { formatPoints } from "../../lib/format";
import { getScoreBand } from "../../lib/scoreBands";

/*
 * Здесь только подготовка к выводу того, что прислал backend: веса и вклад приходят
 * в процентах, новых оценок интерфейс не придумывает.
 */

export type MeasuredCategory = ReportCategory & {
  status: "measured";
  score: number;
  effectiveWeight: number;
  points: number;
};

export function isMeasured(category: ReportCategory): category is MeasuredCategory {
  return (
    category.status === "measured" &&
    category.score !== null &&
    category.effectiveWeight !== null &&
    category.points !== null
  );
}

export interface Loss {
  category: MeasuredCategory;
  /** Сколько баллов категория недобрала до своего максимума. */
  lost: number;
}

/** Категории, на которых теряется больше всего баллов (не меньше minLost). */
export function biggestLosses(categories: ReportCategory[], limit = 2, minLost = 3): Loss[] {
  return categories
    .filter(isMeasured)
    .map((category) => ({ category, lost: category.effectiveWeight - category.points }))
    .filter((loss) => loss.lost >= minLost)
    .sort((a, b) => b.lost - a.lost)
    .slice(0, limit);
}

/**
 * Строка расчёта из весов и оценок backend: «(58×20 + 88×20) ÷ 40 = 73,4».
 * Веса приходят в процентах методики, делитель — вес измеренных категорий.
 */
export function buildFormula(categories: ReportCategory[], measuredWeight: number, result: number): string | null {
  const measured = categories.filter(isMeasured);
  if (measured.length === 0 || measuredWeight <= 0) {
    return null;
  }
  const terms = measured.map((category) => `${formatPoints(category.score)}×${formatPoints(category.weight)}`);
  return `(${terms.join(" + ")}) ÷ ${formatPoints(measuredWeight)} = ${formatPoints(result)}`;
}

export type MetricTone = "high" | "mid" | "low" | "info";

/**
 * Цвет метрики берётся из её собственного normalizedScore по тем же полосам,
 * что и оценки категорий. null — справочная метрика, её не оценивают.
 */
export function metricTone(metric: CategoryMetric): MetricTone {
  return metric.normalizedScore === null ? "info" : getScoreBand(metric.normalizedScore);
}

/**
 * Список метрик показываем, только если backend их прислал: при unavailable,
 * insufficient_sample и пока анализатор не отдал детализацию evidence пустой.
 */
export function hasMetrics(category: ReportCategory): boolean {
  return category.evidence.length > 0;
}
