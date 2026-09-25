import { CircleCheck, CircleQuestion, TriangleExclamation } from "@gravity-ui/icons";
import { Icon, Text } from "@gravity-ui/uikit";
import type { CSSProperties } from "react";

import type { ReportCategory } from "../../api/report";
import { cn } from "../../lib/classNames";
import { formatScore } from "../../lib/format";
import { getScoreBand } from "../../lib/scoreBands";
import { CategoryIcon } from "../CategoryIcon";
import { splitHighlights, summaryWithoutScore, type MeasuredCategory } from "./reportHelpers";
import "./ProjectHighlights.css";

/*
 * Сильные и слабые стороны — ТЗ, п. 3.4 и сценарий 8.1. Это оглавление к карточкам
 * ниже: пункт ведёт к подробной карточке части проекта с фактами.
 */
export function ProjectHighlights({ categories }: { categories: ReportCategory[] }) {
  const { strengths, weaknesses, unchecked } = splitHighlights(categories);
  if (strengths.length + weaknesses.length + unchecked.length === 0) {
    return null;
  }

  return (
    <section className="card highlights" aria-labelledby="highlights-title">
      <Text variant="subheader-2" as="h2" id="highlights-title">
        Сильные и слабые стороны
      </Text>

      <div className="highlights__columns">
        <Column
          kind="strengths"
          title="Сильные стороны"
          empty="Частей с оценкой 80 и выше пока нет."
          items={strengths}
        />
        <Column
          kind="weaknesses"
          title="Слабые стороны"
          empty="Всё, что удалось проверить, в хорошем состоянии."
          items={weaknesses}
        />
      </div>

      {unchecked.length > 0 && (
        <p className="highlights__unchecked">
          <Icon data={CircleQuestion} size={16} className="highlights__unchecked-icon" />
          <Text variant="body-1" color="secondary">
            Не удалось проверить: {unchecked.map((category) => `«${category.label}»`).join(", ")}. Это «нет
            данных», а не плохой результат — в оценку эти части не вошли.
          </Text>
        </p>
      )}
    </section>
  );
}

interface ColumnProps {
  kind: "strengths" | "weaknesses";
  title: string;
  empty: string;
  items: MeasuredCategory[];
}

function Column({ kind, title, empty, items }: ColumnProps) {
  return (
    <div className={cn("highlights__column", `highlights__column_kind_${kind}`)}>
      <Text variant="subheader-1" as="h3" className="highlights__title">
        <Icon data={kind === "strengths" ? CircleCheck : TriangleExclamation} size={16} />
        {title}
      </Text>
      {items.length === 0 ? (
        <Text variant="body-1" color="secondary">
          {empty}
        </Text>
      ) : (
        <ul className="highlights__list">
          {items.map((category, index) => (
            <Item key={category.code} category={category} order={index} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Item({ category, order }: { category: MeasuredCategory; order: number }) {
  const band = getScoreBand(category.score);
  // Оценка стоит справа, поэтому «Оценка документации: 85/100.» из summary не повторяем.
  const summary = summaryWithoutScore(category.summary);

  return (
    <li style={{ "--rh-step": order } as CSSProperties}>
      <a className="highlights__item" href={`#category-${category.code}`}>
        <CategoryIcon code={category.code} band={band} size={16} />
        <span className="highlights__text">
          <span className="highlights__label">{category.label}</span>
          {summary && <span className="highlights__summary">{summary}</span>}
        </span>
        <span className={cn("highlights__score", "num", `highlights__score_band_${band}`)}>
          {formatScore(category.score)}
        </span>
      </a>
    </li>
  );
}
