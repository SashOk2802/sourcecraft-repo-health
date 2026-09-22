import { Icon } from "@gravity-ui/uikit";

import { cn } from "../lib/classNames";
import { categoryIcon } from "../lib/categoryIcons";
import type { ScoreBand } from "../lib/scoreBands";
import "./CategoryIcon.css";

/*
 * Значок категории в подложке. Подложка красится по полосе оценки:
 * цвет остаётся носителем смысла, а не просто украшением.
 * Без оценки подложка нейтральная — это не «плохо», а «нет данных».
 */
export function CategoryIcon({ code, band, size = 18 }: { code: string; band: ScoreBand | null; size?: number }) {
  const data = categoryIcon(code);
  if (!data) {
    return null;
  }

  return (
    <span className={cn("category-icon", band ? `category-icon_band_${band}` : "category-icon_band_none")} aria-hidden="true">
      <Icon data={data} size={size} />
    </span>
  );
}
