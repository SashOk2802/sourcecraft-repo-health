import { Code, CodeCommits, Comment, FileText, Gear, Shield } from "@gravity-ui/icons";
import type { IconData } from "@gravity-ui/uikit";

/*
 * Значок категории. Он не украшение: по нему часть проекта узнаётся
 * на всех экранах одинаково, даже когда подпись обрезана.
 * Коды — из docs/api-contract.md; незнакомый код остаётся без значка.
 */

const icons: Record<string, IconData> = {
  security: Shield,
  cicd: Gear,
  documentation: FileText,
  activity: CodeCommits,
  issues: Comment,
  code_health: Code,
};

export function categoryIcon(code: string): IconData | null {
  return icons[code] ?? null;
}

/** Коды, для которых значок есть. */
export function categoryIconCodes(): string[] {
  return Object.keys(icons);
}
