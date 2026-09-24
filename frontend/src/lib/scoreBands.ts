/**
 * Полосы оценки — только для подсветки в интерфейсе.
 * Сами оценки и Score считает backend; границы полос стоит согласовать с ядром анализа.
 */

export type ScoreBand = "high" | "mid" | "low";

export const SCORE_BAND_LIMITS = { low: 60, high: 80 } as const;

export function getScoreBand(score: number): ScoreBand {
  if (score < SCORE_BAND_LIMITS.low) return "low";
  if (score < SCORE_BAND_LIMITS.high) return "mid";
  return "high";
}
