import type { ReportCategory } from "../api/report";
import { isMeasured } from "../components/report/reportHelpers";

/*
 * Геометрия радара отчёта. Длина луча — вес категории в методике,
 * вершина на луче — её оценка. У категории без данных вершины нет:
 * контур в этом месте разомкнут, и ноль ей не приписывается.
 * Здесь только расчёт координат, рисует их CategoryRadar.
 */

export interface Point {
  x: number;
  y: number;
}

export type LabelAnchor = "start" | "middle" | "end";

export interface RadarLabel {
  at: Point;
  /** Вторая строка подписи: уходит наружу от центра. */
  subAt: Point;
  anchor: LabelAnchor;
}

export interface RadarAxis {
  code: string;
  label: string;
  /** Вес категории в процентах методики. */
  weight: number;
  /** Оценка 0–100; null — категория без данных. */
  score: number | null;
  /** Конец луча: длина пропорциональна весу. */
  end: Point;
  /** Конец дорожки, одинаковый у всех лучей: до него дотягивается подпись. */
  tip: Point;
  /** Вершина контура; null — у категории без данных. */
  vertex: Point | null;
  labelSpot: RadarLabel;
}

export interface RadarEdge {
  from: Point;
  to: Point;
  /** Между вершинами пропущена категория без данных. */
  dashed: boolean;
}

export interface RadarGeometry {
  width: number;
  height: number;
  center: Point;
  radius: number;
  axes: RadarAxis[];
  /** Кольца по долям от длины каждого луча. */
  rings: Point[][];
  /** Контур оценок: сплошной между соседними категориями, пунктирный через пропуск. */
  edges: RadarEdge[];
  /** Вершины контура — по ним рисуются точки и заливка. */
  vertices: Point[];
}

export interface RadarOptions {
  width?: number;
  height?: number;
  radius?: number;
  /** Отступ подписи от конца дорожки. */
  labelGap?: number;
  /** Сдвиг второй строки подписи. */
  subGap?: number;
  /** Доли длины луча, по которым рисуются кольца. */
  rings?: number[];
}

const DEFAULT_RINGS = [0.5, 1];

const DEFAULTS = {
  width: 560,
  height: 420,
  radius: 150,
  labelGap: 20,
  subGap: 17,
} as const;

/** Первый луч смотрит вверх, дальше по часовой стрелке. */
function direction(index: number, total: number): Point {
  const radians = ((-90 + (index * 360) / total) * Math.PI) / 180;
  return { x: Math.cos(radians), y: Math.sin(radians) };
}

function at(center: Point, dir: Point, distance: number): Point {
  return { x: center.x + dir.x * distance, y: center.y + dir.y * distance };
}

function anchorFor(dir: Point): LabelAnchor {
  if (Math.abs(dir.x) < 0.3) return "middle";
  return dir.x > 0 ? "start" : "end";
}

/**
 * Считает радар по категориям отчёта. Порядок лучей — порядок категорий.
 * Возвращает null, если рисовать нечего: меньше трёх категорий или все веса нулевые.
 */
export function buildRadar(categories: ReportCategory[], options: RadarOptions = {}): RadarGeometry | null {
  const width = options.width ?? DEFAULTS.width;
  const height = options.height ?? DEFAULTS.height;
  const radius = options.radius ?? DEFAULTS.radius;
  const labelGap = options.labelGap ?? DEFAULTS.labelGap;
  const subGap = options.subGap ?? DEFAULTS.subGap;
  const ringShares = options.rings ?? DEFAULT_RINGS;

  const maxWeight = categories.reduce((max, category) => Math.max(max, category.weight), 0);
  if (categories.length < 3 || maxWeight <= 0) {
    return null;
  }

  const center: Point = { x: width / 2, y: height / 2 - 10 };
  const directions = categories.map((_, index) => direction(index, categories.length));
  const armLengths = categories.map((category) => (radius * category.weight) / maxWeight);

  const axes: RadarAxis[] = categories.map((category, index) => {
    const dir = directions[index];
    const score = isMeasured(category) ? category.score : null;
    const labelAt = at(center, dir, radius + labelGap);

    return {
      code: category.code,
      label: category.label,
      weight: category.weight,
      score,
      end: at(center, dir, armLengths[index]),
      tip: at(center, dir, radius),
      vertex: score === null ? null : at(center, dir, (armLengths[index] * score) / 100),
      labelSpot: {
        at: labelAt,
        subAt: { x: labelAt.x, y: labelAt.y + (dir.y < 0 ? -subGap : subGap) },
        anchor: anchorFor(dir),
      },
    };
  });

  const rings = ringShares.map((share) =>
    directions.map((dir, index) => at(center, dir, armLengths[index] * share)),
  );

  const measuredIndexes = axes.map((axis, index) => (axis.vertex ? index : -1)).filter((index) => index >= 0);
  const vertices = measuredIndexes.map((index) => axes[index].vertex as Point);

  const edges: RadarEdge[] =
    vertices.length < 2
      ? []
      : measuredIndexes.map((index, position) => {
          const nextIndex = measuredIndexes[(position + 1) % measuredIndexes.length];
          return {
            from: axes[index].vertex as Point,
            to: axes[nextIndex].vertex as Point,
            dashed: (index + 1) % axes.length !== nextIndex,
          };
        });

  return { width, height, center, radius, axes, rings, edges, vertices };
}

/** Точки многоугольника в формате атрибута points. */
export function toPoints(points: Point[]): string {
  return points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
}
