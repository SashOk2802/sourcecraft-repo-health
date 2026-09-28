import { Label, Tooltip } from "@gravity-ui/uikit";

import type { CategoryStatus } from "../api/common";
import { formatPoints } from "../lib/format";
import { categoryStatusLabels } from "../lib/labels";

/** Неполная оценка: часть применимых категорий без данных. */
export function PreliminaryLabel({ hint, size = "m" }: { hint?: string; size?: "xs" | "s" | "m" }) {
  const label = (
    <Label theme="warning" size={size}>
      Предварительная оценка
    </Label>
  );
  return hint ? (
    <Tooltip content={hint} openDelay={100}>
      <span>{label}</span>
    </Tooltip>
  ) : (
    label
  );
}

/** Score ограничен из-за подтверждённой критической проблемы. */
export function ScoreLimitLabel({ value, size = "m" }: { value: number; size?: "xs" | "s" | "m" }) {
  return (
    <Label theme="danger" size={size}>
      Ограничено: не выше {formatPoints(value)}
    </Label>
  );
}

/** Статус категории без оценки: «нет данных», «мало данных», «не применимо», «не удалось получить». */
export function CategoryStatusLabel({ status }: { status: Exclude<CategoryStatus, "measured"> }) {
  return (
    <Label theme={status === "error" ? "danger" : "unknown"} size="s">
      {categoryStatusLabels[status]}
    </Label>
  );
}
