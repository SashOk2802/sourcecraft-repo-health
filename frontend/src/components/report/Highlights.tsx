import { Text } from "@gravity-ui/uikit";

import type { ReportCategory } from "../../api/report";
import { formatScore } from "../../lib/format";
import { SCORE_BAND_LIMITS } from "../../lib/scoreBands";
import { isMeasured, pickHighlights, type MeasuredCategory } from "./reportHelpers";
import "./Highlights.css";

export function Highlights({ categories }: { categories: ReportCategory[] }) {
  if (!categories.some(isMeasured)) {
    return null;
  }

  const { strengths, weaknesses } = pickHighlights(categories);

  return (
    <section className="section highlights">
      <div className="highlights__column">
        <Text variant="subheader-2" as="h2">
          На чём держится
        </Text>
        {strengths.length > 0 ? (
          <HighlightList items={strengths} sign="plus" />
        ) : (
          <Text variant="body-2" color="secondary">
            Ни одна категория пока не набрала {SCORE_BAND_LIMITS.high} баллов.
          </Text>
        )}
      </div>
      <div className="highlights__column">
        <Text variant="subheader-2" as="h2">
          Что тянет вниз
        </Text>
        {weaknesses.length > 0 ? (
          <HighlightList items={weaknesses} sign="minus" />
        ) : (
          <Text variant="body-2" color="secondary">
            Явных слабых мест нет: у всех оценённых категорий {SCORE_BAND_LIMITS.high} баллов и больше.
          </Text>
        )}
      </div>
    </section>
  );
}

function HighlightList({ items, sign }: { items: MeasuredCategory[]; sign: "plus" | "minus" }) {
  return (
    <ul className={`highlights__list highlights__list_sign_${sign}`}>
      {items.map((category) => (
        <li className="highlights__item" key={category.code}>
          <Text variant="body-2">
            <b>
              {category.label}, <span className="num">{formatScore(category.score)}</span>.
            </b>{" "}
            {category.summary}
          </Text>
        </li>
      ))}
    </ul>
  );
}
