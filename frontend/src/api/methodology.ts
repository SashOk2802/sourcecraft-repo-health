import { getJson } from "./http";
import { mocksEnabled, withMockDelay } from "./mockMode";
import { mockMethodologyPayload } from "./mocks/methodology";
import { categoryExplanations, schedulePolicy } from "../lib/methodologyTexts";

/*
 * GET /api/v1/methodology — docs/api-contract.md, «Методика Score». Веса, названия
 * категорий и ограничения приходят с backend, чтобы страница «Как считаем» не расходилась
 * с расчётом. Объяснения простыми словами и политика пересчёта — на стороне интерфейса.
 */

/** Ответ backend. Страница использует версию, категории и ограничения; остальное — справочно. */
export interface MethodologyPayload {
  version: string;
  categories: Array<{ code: string; label: string; weight: number }>;
  scoreLimits?: Array<{ code: string; maximumScore: number; summary?: string }>;
}

export interface Methodology {
  version: string;
  categories: MethodologyCategory[];
  /** Выше какого Score нельзя подняться при подтверждённой критической проблеме; null — правило выключено. */
  criticalScoreLimit: number | null;
  /** Политика планового пересчёта. */
  schedule: {
    regularHours: number;
    activeHours: number;
    activeWithinDays: number;
    inactiveHours: number;
    inactiveAfterDays: number;
    manualCooldownMinutes: number;
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

/** Код ограничения за подтверждённую критическую уязвимость — docs/scoring-methodology.md, §1. */
const CRITICAL_LIMIT_CODE = "security-open-critical";

export function toMethodology(payload: MethodologyPayload): Methodology {
  return {
    version: payload.version,
    categories: payload.categories.map((category) => ({
      code: category.code,
      label: category.label,
      weight: category.weight,
      // Незнакомую категорию не описываем за backend: остаются название и вес.
      measures: categoryExplanations[category.code]?.measures ?? "",
      caveat: categoryExplanations[category.code]?.caveat ?? "",
    })),
    criticalScoreLimit: payload.scoreLimits?.find((limit) => limit.code === CRITICAL_LIMIT_CODE)?.maximumScore ?? null,
    schedule: { ...schedulePolicy },
  };
}

export async function fetchMethodology(): Promise<Methodology> {
  if (mocksEnabled) {
    return withMockDelay(toMethodology(mockMethodologyPayload), 150);
  }
  return toMethodology(await getJson<MethodologyPayload>("/api/v1/methodology"));
}
