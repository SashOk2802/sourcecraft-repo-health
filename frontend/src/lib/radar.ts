import type { CategoryStatus } from "../api/common";
import type { ReportCategory } from "../api/report";
import { isMeasured } from "../components/report/reportHelpers";
import { formatPoints, formatScore } from "./format";

/*
 * Геометрия радара отчёта. Длина луча — вес категории в методике,
 * вершина на луче — её оценка. У категории без оценки вершины нет:
 * контур в этом месте разомкнут, заливка этот сектор не закрывает,
 * и ноль ей не приписывается. Здесь только расчёт координат, рисует их CategoryRadar.
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
  /** Оценка 0–100; null — категория без оценки. */
  score: number | null;
  /**
   * Статус данных как прислал backend. «Не применимо» — не то же, что «нет данных»:
   * категория к репозиторию просто не относится, и подпись должна это сказать.
   */
  status: CategoryStatus;
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
  /** Вершины контура в порядке лучей. */
  vertices: Point[];
  /**
   * Заливка. Без пропусков — один многоугольник по всем вершинам. С пропуском —
   * «веера» от центра по сериям соседних измеренных категорий: сектор категории
   * без оценки остаётся пустым, как и обещает разрыв контура.
   */
  fills: Point[][];
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
  width: 600,
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
      status: category.status,
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

  return { width, height, center, radius, axes, rings, edges, vertices, fills: buildFills(axes, center) };
}

/**
 * Заливка без сектора, где оценки нет. Серии соседних измеренных лучей ищутся
 * по кругу: серия может переходить через последний луч к первому. Одна вершина
 * площади не даёт, такую серию заливать нечем.
 */
function buildFills(axes: RadarAxis[], center: Point): Point[][] {
  const measured = axes.map((axis) => axis.vertex !== null);
  if (measured.every(Boolean)) {
    return axes.length >= 3 ? [axes.map((axis) => axis.vertex as Point)] : [];
  }

  const total = axes.length;
  // Начинаем сразу после любого пропуска, чтобы серия не разрезалась на стыке конца и начала.
  const start = (measured.findIndex((isMeasuredAxis) => !isMeasuredAxis) + 1) % total;
  const fills: Point[][] = [];
  let run: Point[] = [];

  for (let step = 0; step < total; step += 1) {
    const axis = axes[(start + step) % total];
    if (axis.vertex) {
      run.push(axis.vertex);
      continue;
    }
    if (run.length >= 2) fills.push([center, ...run]);
    run = [];
  }
  if (run.length >= 2) fills.push([center, ...run]);

  return fills;
}

/** Короткие слова для подписи на радаре: места у края фигуры мало, а статус должен читаться точно. */
const axisStatusWords: Record<Exclude<CategoryStatus, "measured">, string> = {
  unavailable: "нет данных",
  insufficient_sample: "мало данных",
  error: "ошибка сбора",
  not_applicable: "не применимо",
};

/** Вторая строка подписи луча: вес и оценка, а без оценки — почему её нет. */
export function describeAxis(axis: RadarAxis): string {
  const weight = `вес ${formatPoints(axis.weight)}%`;
  if (axis.score !== null) {
    return `${weight} · оценка ${formatScore(axis.score)}`;
  }
  // Измеренная категория без оценки невозможна по контракту; на всякий случай — «нет данных».
  const word = axis.status === "measured" ? axisStatusWords.unavailable : axisStatusWords[axis.status];
  return `${weight} · ${word}`;
}

/** Точки многоугольника в формате атрибута points. */
export function toPoints(points: Point[]): string {
  return points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
}
