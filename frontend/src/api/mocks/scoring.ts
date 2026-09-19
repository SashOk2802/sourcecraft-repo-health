import type { AnalysisStatus, CategoryStatus } from "../common";
import { mockCategories, type MockCategoryCode, type MockCategoryValue } from "./catalog";

export interface MockScoredCategory {
  code: MockCategoryCode;
  label: string;
  status: CategoryStatus;
  score: number | null;
  /** Проценты методики. */
  weight: number;
  /** Проценты среди измеренных категорий; null — категория не участвовала. */
  effectiveWeight: number | null;
  points: number | null;
}

export interface MockScore {
  score: number | null;
  uncappedScore: number | null;
  /** Доля измеренного веса среди применимых категорий; null — применимых категорий нет. */
  coverage: number | null;
  isPreliminary: boolean;
  status: AnalysisStatus;
  measuredWeight: number;
  applicableWeight: number;
  categories: MockScoredCategory[];
}

/**
 * Упрощённая копия формулы ядра — только чтобы mock-данные сходились между собой.
 * Интерфейс её не вызывает: с настоящим backend все числа приходят в ответе API.
 */
export function scoreMockCategories(
  values: Record<MockCategoryCode, MockCategoryValue>,
  scoreLimit?: number,
): MockScore {
  const measuredWeight = mockCategories.reduce(
    (sum, category) => (typeof values[category.code] === "number" ? sum + category.weight : sum),
    0,
  );
  // Неприменимая категория не считается нехваткой данных и не делает оценку предварительной.
  const applicableWeight = mockCategories.reduce(
    (sum, category) => (values[category.code] === "not_applicable" ? sum : sum + category.weight),
    0,
  );

  const categories = mockCategories.map((category): MockScoredCategory => {
    const value = values[category.code];
    if (typeof value !== "number") {
      return { ...category, status: value, score: null, effectiveWeight: null, points: null };
    }
    const effectiveWeight = (category.weight / measuredWeight) * 100;
    return {
      ...category,
      status: "measured",
      score: value,
      effectiveWeight: round(effectiveWeight, 2),
      points: round((value * effectiveWeight) / 100, 2),
    };
  });

  const isPreliminary = measuredWeight < applicableWeight;
  const status: AnalysisStatus = isPreliminary ? "partial" : "completed";

  if (measuredWeight === 0) {
    return {
      score: null,
      uncappedScore: null,
      coverage: applicableWeight === 0 ? null : 0,
      isPreliminary,
      status,
      measuredWeight,
      applicableWeight,
      categories,
    };
  }

  const uncapped = round(
    categories.reduce((sum, category) => sum + (category.points ?? 0), 0),
    1,
  );
  return {
    score: scoreLimit !== undefined ? Math.min(uncapped, scoreLimit) : uncapped,
    uncappedScore: uncapped,
    coverage: applicableWeight === 0 ? null : round(measuredWeight / applicableWeight, 2),
    isPreliminary,
    status,
    measuredWeight,
    applicableWeight,
    categories,
  };
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
