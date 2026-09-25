import type { CategoryBrief } from "../api/common";
import { cn } from "../lib/classNames";
import { describeCategoryScore } from "../lib/labels";
import { getScoreBand } from "../lib/scoreBands";
import "./CategoryCells.css";

/** Шесть клеток — по одной на категорию: сразу видно, где у проекта слабое место. */
export function CategoryCells({ categories }: { categories: CategoryBrief[] }) {
  const summary = categories.map((category) => `${category.label}: ${describeCategoryScore(category)}`).join("; ");

  return (
    <span className="category-cells" role="img" aria-label={summary}>
      {categories.map((category) => (
        <span
          key={category.code}
          className={cn("category-cells__cell", cellModifier(category))}
          title={`${category.label}: ${describeCategoryScore(category)}`}
        />
      ))}
    </span>
  );
}

function cellModifier(category: CategoryBrief): string {
  if (category.status === "measured" && category.score !== null) {
    return `category-cells__cell_band_${getScoreBand(category.score)}`;
  }
  return `category-cells__cell_status_${category.status}`;
}
