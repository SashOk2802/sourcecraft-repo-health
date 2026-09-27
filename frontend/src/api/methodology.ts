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
  /** Формула Security Score — docs/scoring-methodology.md, §4.2. */
  security?: {
    summary?: string;
    formula?: string;
    eligibility?: string;
    severityPenalties?: Array<{ severity: string; penaltyPerFinding: number; maximumFindings: number }>;
  };
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
  const securityRules = securityExplanation(payload.security);
  return {
    version: payload.version,
    categories: payload.categories.map((category) => {
      // Если backend прислал формулу Security Score, штрафы на странице — из неё, а не общими словами.
      const explanation =
        category.code === "security" && securityRules ? securityRules : categoryExplanations[category.code];
      return {
        code: category.code,
        label: category.label,
        weight: category.weight,
        // Незнакомую категорию не описываем за backend: остаются название и вес.
        measures: explanation?.measures ?? "",
        caveat: explanation?.caveat ?? "",
      };
    }),
    criticalScoreLimit: payload.scoreLimits?.find((limit) => limit.code === CRITICAL_LIMIT_CODE)?.maximumScore ?? null,
    schedule: { ...schedulePolicy },
  };
}

const severityNames: Record<string, string> = {
  CRITICAL: "критичная",
  HIGH: "высокая",
  MEDIUM: "средняя",
  LOW: "низкая",
};

/**
 * Security Score (docs/scoring-methodology.md, §4.2): штраф за каждую открытую находку по
 * критичности, с потолком числа учитываемых находок. null — backend формулу не прислал.
 * Условие допуска (eligibility) backend пишет терминами контракта — «движки», severity,
 * insufficient_sample, — поэтому оговорка своя, простыми словами, из methodologyTexts.
 */
function securityExplanation(security: MethodologyPayload["security"]): { measures: string; caveat: string } | null {
  const penalties = (security?.severityPenalties ?? []).filter(
    (rule) => rule.penaltyPerFinding > 0 && severityNames[rule.severity.toUpperCase()] !== undefined,
  );
  if (penalties.length === 0) {
    return null;
  }
  const rules = penalties
    .map(
      (rule) =>
        `${severityNames[rule.severity.toUpperCase()]} — ${rule.penaltyPerFinding} (учитываем до ${rule.maximumFindings})`,
    )
    .join(", ");
  return {
    measures: `Открытые находки SAST, SCA и secret scanning в SourceCraft. За каждую снимаем баллы по критичности: ${rules}.`,
    caveat: categoryExplanations.security.caveat,
  };
}

export async function fetchMethodology(): Promise<Methodology> {
  if (mocksEnabled) {
    return withMockDelay(toMethodology(mockMethodologyPayload), 150);
  }
  return toMethodology(await getJson<MethodologyPayload>("/api/v1/methodology"));
}
