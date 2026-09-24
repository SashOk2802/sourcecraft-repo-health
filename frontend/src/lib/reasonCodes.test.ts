import { describe, expect, it } from "vitest";

import { describeReason, isKnownReason } from "./reasonCodes";

describe("describeReason", () => {
  it("переводит известные коды backend", () => {
    expect(describeReason("appsec_not_available")).toContain("не значит, что уязвимостей нет");
    expect(isKnownReason("analyzer_not_configured")).toBe(true);
  });

  it("знает причины анализаторов Security и CI/CD", () => {
    const codes = [
      "appsec_unavailable",
      "appsec_source_error",
      "security_scoring_not_configured",
      "empty_repository",
      "cicd_runs_unavailable",
      "cicd_runs_truncated",
      "cicd_no_runs",
      "cicd_no_automated_runs_in_period",
      "cicd_too_few_outcome_runs",
    ];
    for (const code of codes) {
      expect(isKnownReason(code)).toBe(true);
    }
  });

  it("не повторяет summary backend о неподключённом анализаторе", () => {
    // Backend уже пишет «Анализатор категории пока не подключён.» — здесь объясняем, что это значит.
    expect(describeReason("analyzer_not_configured")).not.toContain("Анализатор");
  });

  it("не прячет незнакомый код", () => {
    expect(describeReason("some_new_code")).toBe("Причина: some_new_code");
    expect(isKnownReason("some_new_code")).toBe(false);
  });
});
