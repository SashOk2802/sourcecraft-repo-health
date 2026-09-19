import { getJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import { mockMethodology } from "./mocks/methodology";

/*
 * GET /api/v1/methodology — предложение к docs/api-contract.md.
 * Веса и пороги приходят с backend, чтобы страница «Как считаем» не расходилась с расчётом.
 */

export interface Methodology {
  version: string;
  categories: MethodologyCategory[];
  /** Выше какого Score нельзя подняться при подтверждённой критической проблеме; null — правило выключено. */
  criticalScoreLimit: number | null;
  /** Периодичность планового пересчёта в часах. */
  schedule: {
    regularHours: number;
    activeHours: number;
    inactiveHours: number;
  } | null;
}

export interface MethodologyCategory {
  code: string;
  label: string;
  /** Вес в процентах; сумма по категориям — 100. */
  weight: number;
  /** Что учитываем. */
  measures: string;
  /** Как не ошибиться с выводом. */
  caveat: string;
}

export async function fetchMethodology(): Promise<Methodology> {
  if (mocksEnabled) {
    return withMockDelay(mockMethodology, 150);
  }
  return getJson<Methodology>("/api/v1/methodology");
}
