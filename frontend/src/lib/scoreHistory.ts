import type { ScorePoint } from "../api/publicHistory";

/*
 * Что говорить про динамику Score честно:
 * - сравниваем только оценки одной версии методики — v1 и v2 считаются по-разному;
 * - точка без Score — разрыв, а не ноль;
 * - одна точка — ещё не динамика.
 */

export interface HistorySummary {
  /** Точки последней версии методики с числовым Score. */
  comparable: ScorePoint[];
  /** Изменение от первой до последней сравнимой точки; null — сравнивать не с чем. */
  delta: number | null;
  /** В истории есть точки другой версии методики — они не сравниваются с текущей. */
  versionChanged: boolean;
  /** Версия методики последней точки. */
  latestVersion: string | null;
}

export function summarizeHistory(points: ScorePoint[]): HistorySummary {
  const latestVersion = points.length > 0 ? points[points.length - 1].methodologyVersion : null;
  const comparable = points.filter(
    (point) => point.methodologyVersion === latestVersion && point.score !== null,
  );
  const delta =
    comparable.length >= 2
      ? (comparable[comparable.length - 1].score as number) - (comparable[0].score as number)
      : null;
  return {
    comparable,
    delta,
    versionChanged: points.some((point) => point.methodologyVersion !== latestVersion),
    latestVersion,
  };
}

export interface ChartPoint {
  x: number;
  y: number;
  point: ScorePoint;
}

/**
 * Координаты для графика: по горизонтали — порядок снимков, по вертикали — Score 0–100.
 * Точки без Score в линию не входят: линия рвётся, ноль не рисуется.
 */
export function chartSegments(points: ScorePoint[], width: number, height: number, padding: number): ChartPoint[][] {
  const step = points.length > 1 ? (width - padding * 2) / (points.length - 1) : 0;
  const segments: ChartPoint[][] = [];
  let current: ChartPoint[] = [];
  points.forEach((point, index) => {
    if (point.score === null) {
      if (current.length > 0) segments.push(current);
      current = [];
      return;
    }
    const x = points.length > 1 ? padding + step * index : width / 2;
    const y = padding + (1 - point.score / 100) * (height - padding * 2);
    current.push({ x, y, point });
  });
  if (current.length > 0) segments.push(current);
  return segments;
}
