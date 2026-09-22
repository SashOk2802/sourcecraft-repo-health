import { Text } from "@gravity-ui/uikit";
import type { CSSProperties } from "react";

import type { ReportCategory } from "../../api/report";
import { usePointerTilt } from "../../hooks/usePointerTilt";
import { describeCategory } from "../../lib/categoryMeaning";
import { formatPoints, formatScore } from "../../lib/format";
import { buildRadar, toPoints } from "../../lib/radar";
import "./CategoryRadar.css";

/*
 * Одна фигура вместо таблицы: длина луча — вес категории, вершина на луче — оценка.
 * У категории без данных вершины нет, и контур в этом месте разомкнут пунктиром:
 * так видно, что её не приравняли к нулю.
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
              className={axis.vertex ? "category-radar__arm" : "category-radar__arm category-radar__arm_empty"}
              x1={radar.center.x}
              y1={radar.center.y}
              x2={axis.end.x}
              y2={axis.end.y}
              style={order(index)}
            />
          ))}

          {radar.vertices.length >= 3 && (
            <polygon className="category-radar__shape" points={toPoints(radar.vertices)} />
          )}

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
                className="category-radar__dot"
                cx={axis.vertex.x}
                cy={axis.vertex.y}
                r={5.5}
                style={order(index)}
              />
            ) : (
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
                {`вес ${formatPoints(axis.weight)}% · ${axis.score === null ? "нет данных" : `оценка ${formatScore(axis.score)}`}`}
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
      </ul>
    </div>
  );
}
