import { Text } from "@gravity-ui/uikit";
import type { CSSProperties } from "react";

import type { ReportCategory } from "../../api/report";
import { usePointerTilt } from "../../hooks/usePointerTilt";
import { describeCategory } from "../../lib/categoryMeaning";
import { cn } from "../../lib/classNames";
import { buildRadar, describeAxis, toPoints } from "../../lib/radar";
import { getScoreBand } from "../../lib/scoreBands";
import "./CategoryRadar.css";

/*
 * Одна фигура вместо таблицы: длина луча — вес категории, вершина на луче — оценка.
 * У категории без данных вершины нет: контур в этом месте разомкнут пунктиром,
 * а заливка сектор не закрывает — так видно, что её не приравняли к нулю.
 * «Не применимо» рисуется бледным лучом без пунктирного кружка: данных тут и не ждём.
 * Фигура собирается при появлении и чуть поворачивается за курсором.
 */
export function CategoryRadar({ categories }: { categories: ReportCategory[] }) {
  const tilt = usePointerTilt(5);
  const radar = buildRadar(categories);
  if (!radar) {
    return null;
  }

  /** Лучи и подписи проявляются по очереди, чтобы фигура собиралась на глазах. */
  const order = (index: number): CSSProperties => ({ "--rh-step": index }) as CSSProperties;

  return (
    <div className="category-radar">
      <div
        className="category-radar__stage"
        style={tilt.style}
        onPointerMove={tilt.onPointerMove}
        onPointerLeave={tilt.onPointerLeave}
      >
        <svg
          className="category-radar__chart"
          viewBox={`0 0 ${radar.width} ${radar.height}`}
          role="img"
          aria-label="Оценки категорий: длина луча — вес категории, точка на луче — оценка"
        >
          <defs>
            <linearGradient id="rh-radar-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--rh-accent)" stopOpacity="0.26" />
              <stop offset="100%" stopColor="var(--rh-accent)" stopOpacity="0.06" />
            </linearGradient>
          </defs>

          {radar.rings.map((ring, index) => (
            <polygon key={index} className="category-radar__ring" points={toPoints(ring)} style={order(index)} />
          ))}

          {radar.axes.map((axis, index) => (
            <line
              key={`track-${axis.code}`}
              className="category-radar__track"
              x1={radar.center.x}
              y1={radar.center.y}
              x2={axis.tip.x}
              y2={axis.tip.y}
              style={order(index)}
            />
          ))}

          {radar.axes.map((axis, index) => (
            <line
              key={`arm-${axis.code}`}
              className={cn(
                "category-radar__arm",
                !axis.vertex && (axis.status === "not_applicable" ? "category-radar__arm_na" : "category-radar__arm_empty"),
              )}
              x1={radar.center.x}
              y1={radar.center.y}
              x2={axis.end.x}
              y2={axis.end.y}
              style={order(index)}
            />
          ))}

          {/* Сектор категории без оценки заливка не закрывает: там разрыв, а не ноль. */}
          {radar.fills.map((fill, index) => (
            <polygon key={`fill-${index}`} className="category-radar__shape" points={toPoints(fill)} />
          ))}

          {radar.edges.map((edge, index) => (
            <line
              key={`edge-${index}`}
              className={edge.dashed ? "category-radar__edge category-radar__edge_gap" : "category-radar__edge"}
              x1={edge.from.x}
              y1={edge.from.y}
              x2={edge.to.x}
              y2={edge.to.y}
              style={order(index)}
            />
          ))}

          {radar.axes.map((axis, index) =>
            axis.vertex ? (
              <circle
                key={`dot-${axis.code}`}
                className={cn("category-radar__dot", `category-radar__dot_band_${getScoreBand(axis.score as number)}`)}
                cx={axis.vertex.x}
                cy={axis.vertex.y}
                r={5.5}
                style={order(index)}
              />
            ) : axis.status === "not_applicable" ? null : (
              // Пунктирный кружок — «данных нет». У неприменимой категории его нет: ей нечего ждать.
              <circle
                key={`dot-${axis.code}`}
                className="category-radar__dot category-radar__dot_empty"
                cx={axis.end.x}
                cy={axis.end.y}
                r={6}
                style={order(index)}
              />
            ),
          )}

          {radar.axes.map((axis, index) => (
            <g key={`label-${axis.code}`} className="category-radar__caption" style={order(index)}>
              {/* Подсказка при наведении: что это за часть проекта, обычными словами. */}
              {describeCategory(axis.code) && <title>{`${axis.label} — ${describeCategory(axis.code)}`}</title>}
              <text
                className={axis.vertex ? "category-radar__label" : "category-radar__label category-radar__label_empty"}
                x={axis.labelSpot.at.x}
                y={axis.labelSpot.at.y}
                textAnchor={axis.labelSpot.anchor}
              >
                {axis.label}
              </text>
              <text
                className="category-radar__sub"
                x={axis.labelSpot.subAt.x}
                y={axis.labelSpot.subAt.y}
                textAnchor={axis.labelSpot.anchor}
              >
                {describeAxis(axis)}
              </text>
            </g>
          ))}
        </svg>
      </div>

      <ul className="category-radar__legend">
        <li>
          <span className="category-radar__key category-radar__key_shape" />
          <Text variant="body-1" color="secondary">
            точка — оценка части проекта
          </Text>
        </li>
        <li>
          <span className="category-radar__key category-radar__key_ring" />
          <Text variant="body-1" color="secondary">
            чем длиннее луч, тем сильнее часть влияет на оценку
          </Text>
        </li>
        <li>
          <span className="category-radar__key category-radar__key_empty" />
          <Text variant="body-1" color="secondary">
            пунктир — данных по этой части нет
          </Text>
        </li>
        {radar.axes.some((axis) => axis.status === "not_applicable") && (
          <li>
            <span className="category-radar__key category-radar__key_na" />
            <Text variant="body-1" color="secondary">
              бледный луч — часть к этому проекту не относится
            </Text>
          </li>
        )}
      </ul>
    </div>
  );
}
