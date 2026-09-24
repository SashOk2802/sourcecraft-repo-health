import { describe, expect, it } from "vitest";

import { describeCategory } from "./categoryMeaning";

describe("describeCategory", () => {
  it("объясняет все шесть категорий методики", () => {
    for (const code of ["security", "cicd", "documentation", "activity", "issues", "code_health"]) {
      expect(describeCategory(code)).toBeTruthy();
    }
  });

  it("объясняет обычными словами, без терминов из кода", () => {
    const cicd = describeCategory("cicd");
    expect(cicd).not.toContain("CI");
    expect(cicd).not.toContain("pipeline");
  });

  it("незнакомый код остаётся без объяснения", () => {
    expect(describeCategory("dependencies")).toBeNull();
    expect(describeCategory("")).toBeNull();
  });
});
