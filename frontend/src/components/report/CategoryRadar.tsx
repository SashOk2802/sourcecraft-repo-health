import { Text } from "@gravity-ui/uikit";

import type { ReportCategory } from "../../api/report";
import { formatPoints, formatScore } from "../../lib/format";
import { buildRadar, toPoints } from "../../lib/radar";
import "./CategoryRadar.css";

/*
 * Одна фигура вместо таблицы: длина луча — вес категории, вершина на луче — оценка.
 * У категории без данных вершины нет, и контур в этом месте разомкнут пунктиром:
 * так видно, что её не приравняли к нулю.
 */
export function CategoryRadar({ categories }: { categories: ReportCategory[] }) {
  const radar = buildRadar(categories);
  if (!radar) {
    return null;
  }

  return (
    <div className="category-radar">
      <svg
        className="category-radar__chart"
        viewBox={`0 0 ${radar.width} ${radar.height}`}
        role="img"
        aria-label="Оценки категорий: длина луча — вес категории, точка на луче — оценка"
      >
        {radar.rings.map((ring, index) => (
          <polygon key={index} className="category-radar__ring" points={toPoints(ring)} />
        ))}

        {radar.axes.map((axis) => (
          <line
            key={`track-${axis.code}`}
            className="category-radar__track"
            x1={radar.center.x}
            y1={radar.center.y}
            x2={axis.tip.x}
            y2={axis.tip.y}
          />
        ))}

        {radar.axes.map((axis) => (
          <line
            key={`arm-${axis.code}`}
            className={axis.vertex ? "category-radar__arm" : "category-radar__arm category-radar__arm_empty"}
            x1={radar.center.x}
            y1={radar.center.y}
            x2={axis.end.x}
            y2={axis.end.y}
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
          />
        ))}

        {radar.axes.map((axis) =>
          axis.vertex ? (
            <circle key={`dot-${axis.code}`} className="category-radar__dot" cx={axis.vertex.x} cy={axis.vertex.y} r={5.5} />
          ) : (
            <circle key={`dot-${axis.code}`} className="category-radar__dot category-radar__dot_empty" cx={axis.end.x} cy={axis.end.y} r={6} />
          ),
        )}

        {radar.axes.map((axis) => (
          <g key={`label-${axis.code}`}>
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

      <ul className="category-radar__legend">
        <li>
          <span className="category-radar__key category-radar__key_shape" />
          <Text variant="body-1" color="secondary">
            оценка категории
          </Text>
        </li>
        <li>
          <span className="category-radar__key category-radar__key_ring" />
          <Text variant="body-1" color="secondary">
            кольца — половина и весь вес
          </Text>
        </li>
        <li>
          <span className="category-radar__key category-radar__key_empty" />
          <Text variant="body-1" color="secondary">
            луч без вершины — нет данных
          </Text>
        </li>
        <li>
          <Text variant="body-1" color="secondary">
            длина луча — вес категории
          </Text>
        </li>
      </ul>
    </div>
  );
}
