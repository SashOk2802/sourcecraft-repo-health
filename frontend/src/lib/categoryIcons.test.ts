import { describe, expect, it } from "vitest";

import { describeCategory } from "./categoryMeaning";
import { categoryIcon, categoryIconCodes } from "./categoryIcons";

describe("categoryIcon", () => {
  it("значок есть у каждой категории методики", () => {
    for (const code of ["security", "cicd", "documentation", "activity", "issues", "code_health"]) {
      expect(categoryIcon(code)).not.toBeNull();
    }
  });

  it("значки и объяснения описывают один и тот же набор категорий", () => {
    for (const code of categoryIconCodes()) {
      expect(describeCategory(code)).toBeTruthy();
    }
  });

  it("незнакомый код остаётся без значка", () => {
    expect(categoryIcon("dependencies")).toBeNull();
  });
});
